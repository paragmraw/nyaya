"""Agent node: one streaming, tool-bound model that plans retrieval AND
composes the grounded answer.

Phase 1 pipeline collapse: the separate supervisor (non-streaming
``with_structured_output(ToolPlan)`` planner + corrective retry) and the
synthesis node are merged into this single node in a 2-node graph
(``START → agent → (tools → agent | END)``). One LLM round trip on the
answer path replaces up to three, and thinking is off so the completion
budget belongs entirely to the answer.

**Leg buffering.** The first leg of a turn (its last state message is not a
``ToolMessage``) is buffered: a streamed tool-call decision must not leak
into the answer body. When the buffered stream ends with tool calls, the
buffered text is replayed as a ``plan`` event and ``tool_start`` events are
emitted; when it ends without calls, the buffered text is replayed as
``token`` events after a ``composing`` status (a direct answer). Post-tools
legs (last message is a ``ToolMessage``) stream tokens live — that is the
leg whose TTFT the user feels. The degraded ``has_tools=False`` graph also
streams live (nothing to buffer for).

Reflection: ``round`` counts completed ANSWER legs (the node advances it on
answer legs only); ``route_agent`` sends the agent back in for one more
retrieval round when the answer is uncited, tools were called, and enough
of the turn budget remains.

The returned AIMessage is THE authoritative answer: citation verification
(if enabled) runs here, once, and the disclaimer is appended here when the
model omitted it — so the streamed-and-verified text is final. The node
itself emits the ``citations`` and (when the verified text differs from the
streamed tokens) ``correction`` events, so the streamer never re-verifies
or re-derives anything.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)

from ..citations import CITATION_RE, parse_citations, verify_citations
from ..errors import TurnError
from ..llm import AGENT_PROMPT, DISCLAIMER, astream_with_retry
from ..prompts import REFLECTION_PROMPT, SYSTEM_PROMPT
from ..tools_layer.cleaning import prune_list_result, strip_corpus_tags
from ..tools_layer.spec import TOOL_NAMES
from .events import citations as citations_event
from .events import correction as correction_event
from .events import plan as plan_event
from .events import reasoning as reasoning_event
from .events import status, timed_phase, tool_start, usage
from .events import token as token_event
from .state import ChatState

log = logging.getLogger("nyaya_chat.graph.agent")

# SSE tool_start args echo cap (the model-facing args are unaffected).
TOOL_START_ARGS_CHARS = 200

# A single content delta at or above this size whose opening matches the
# already-streamed reasoning is treated as a reasoning duplicate (see
# ``_is_reasoning_leak``). Real answer deltas are word-sized.
REASONING_LEAK_MIN_CHARS = 200


def _is_reasoning_leak(text: str, reasoning_buffer: str) -> bool:
    """Detect a content delta that duplicates already-streamed reasoning.

    The NVIDIA API has been observed to re-send the entire accumulated
    ``reasoning_content`` as ONE giant content delta (a server-side flush of
    an unclosed think block — seen live as an 8.6K-char chunk byte-identical
    to the reasoning buffer, right as the stream was truncated). Streaming
    that to the user leaks the model's deliberation into the answer body.
    Such a chunk is routed to the reasoning trace instead.

    A legitimate answer delta that verbatim-repeats 200+ consecutive chars
    of the model's reasoning would be a degenerate echo — hiding it is
    correct either way, so the heuristic errs toward suppression.
    """
    if not reasoning_buffer or len(text) < REASONING_LEAK_MIN_CHARS:
        return False
    return text[:REASONING_LEAK_MIN_CHARS] in reasoning_buffer


def _is_db_error_json(text: str) -> bool:
    """True when a tool result is the native layer's *unavailability* error JSON.

    The native tools report a dead corpus/DB as
    ``{"error": {"code": "database_unavailable", ...}}`` (``native.py``'s
    ``_error_json`` shape); a failed embed is ``embedding_unavailable``.
    Feeding those into the agent produces an answer built on nothing;
    detecting them here lets the turn fail fast with a specific,
    human-explainable error instead. Other error codes (``not_found``,
    ``search_error``) are legitimate results and must NOT match.
    """
    if not text.strip().startswith("{"):
        return False
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return False
    err = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(err, dict):
        return False
    # Only *unavailability* codes mean "nothing was retrieved". A
    # ``not_found`` error (e.g. get_section on a nonexistent section) is a
    # legitimate result the agent model must turn into a refusal —
    # short-circuiting on it would hard-fail valid refusal turns.
    return err.get("code") in {"database_unavailable", "embedding_unavailable"}


def _prune_tool_results_for_model(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Prune bulk, non-essential fields from LIST-type tool results.

    **Pruning rule (conservative):** before the message list goes to the
    agent model, only LIST-type tool results (the multi-hit array of
    ``semantic_query`` — up to 50 hits of snippet text, the ~12K-token worst
    case per round) are pruned, and only beyond the top hit; single-document
    results pass through UNCHANGED (see ``tools_layer.cleaning``).
    Read-time only: it affects the model input, not the dedup cache or the
    unpruned ``messages`` used for citation verification.
    """
    pruned_messages: list[BaseMessage] = []
    for m in messages:
        if isinstance(m, ToolMessage):
            pruned_content = prune_list_result(m.content, m.name)
            if pruned_content is not m.content:
                m = ToolMessage(
                    content=pruned_content,
                    tool_call_id=m.tool_call_id,
                    name=m.name,
                )
        pruned_messages.append(m)
    return pruned_messages


def _wrap_tool_results_in_corpus_tags(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Wrap ToolMessage content in <corpus_text>...</corpus_text> tags.

    Prompt-injection defense: it clearly delineates corpus data from
    instructions in the agent prompt; the system prompt instructs the model
    to treat all text inside these tags as data only.
    """
    result: list[BaseMessage] = []
    for m in messages:
        if isinstance(m, ToolMessage):
            content = m.content if isinstance(m.content, str) else str(m.content)
            wrapped = ToolMessage(
                content=f"<corpus_text>\n{content}\n</corpus_text>",
                tool_call_id=m.tool_call_id,
                name=m.name,
            )
            result.append(wrapped)
        else:
            result.append(m)
    return result


def _has_tool_calls(messages: list[BaseMessage]) -> bool:
    """Check if any AIMessage in the conversation had tool_calls."""
    for m in messages:
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
            return True
    return False


def _get_tool_content_list(messages: list[BaseMessage]) -> list[str]:
    """Extract content strings from all ToolMessages in the conversation."""
    result: list[str] = []
    for m in messages:
        if isinstance(m, ToolMessage):
            content = m.content if isinstance(m.content, str) else str(m.content)
            # Strip the corpus_text wrapper if present
            content = strip_corpus_tags(content)
            result.append(content)
    return result


def _chunk_text(content: Any) -> str:
    """Flatten a message chunk's content to text (string or block list)."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                t = block.get("text") or block.get("content")
                if isinstance(t, str):
                    parts.append(t)
        return "".join(parts)
    return str(content) if content else ""


def build_messages(message: str, history: list[dict[str, str]]) -> list:
    """Assemble the message list for a turn.

    The agent system prompt is first, then the capped history (oldest
    dropped if longer than ``Settings.max_history``), then the new user
    message.
    """
    msgs: list = [SystemMessage(content=AGENT_PROMPT)]
    for turn in history:
        if turn["role"] == "user":
            msgs.append(HumanMessage(content=turn["content"]))
        else:
            msgs.append(AIMessage(content=turn["content"]))
    msgs.append(HumanMessage(content=message))
    return msgs


def _is_reflection_round(state: ChatState) -> bool:
    """True when an answer leg has already completed this turn.

    ``state["round"]`` counts completed answer legs (the agent node advances
    it on answer legs only); route_agent routes back to the agent for a
    reflection round when an answer was uncited. The round counter is the
    single source of truth.
    """
    return state.get("round", 0) >= 1


def _state_messages(state: ChatState, base_prompt: str = AGENT_PROMPT) -> list:
    """Rebuild the model input from state, honouring the reflection round.

    The stored system prompt is always the base agent prompt; reflection
    rounds get the retrieval-only suffix appended (the base prompt stays
    cached-prefix-stable for the first call of a turn).
    """
    msgs = [m for m in state["messages"] if not isinstance(m, SystemMessage)]
    system = base_prompt + (REFLECTION_PROMPT if _is_reflection_round(state) else "")
    return [SystemMessage(content=system), *msgs]


def _cap_args(args: dict[str, Any]) -> dict[str, Any]:
    """Cap the args echo sent to the UI (model-facing args are untouched)."""
    capped: dict[str, Any] = {}
    for k, v in args.items():
        s = v if isinstance(v, str) else json.dumps(v, default=str)
        capped[k] = s if len(s) <= TOOL_START_ARGS_CHARS else s[:TOOL_START_ARGS_CHARS] + "…"
    return capped


def _emit_tool_starts(state: ChatState, calls: list[dict[str, Any]]) -> None:
    rid = state.get("rid", "")
    for tc in calls:
        tool_start(
            tc.get("id") or tc.get("name", ""),
            tc.get("name", ""),
            _cap_args(tc.get("args", {})),
        )
    if calls:
        status("searching", rid)


def _deadline_exceeded(state: ChatState) -> bool:
    """True when the turn's wall-clock budget (set by the streamer) is spent."""
    deadline = state.get("deadline")
    if deadline is None or deadline <= 0.0:
        return False
    return time.monotonic() > deadline


def make_agent_node(settings: Any, agent_model: Any, *, has_tools: bool = True):
    """Build the agent node for the given tool-bound model.

    ``has_tools=False`` builds the degraded no-tools variant (a single-node
    graph that streams an answer directly, with no verification) — the
    SYSTEM_PROMPT applies there because there is nothing to retrieve with.
    """

    async def _agent(state: ChatState) -> dict[str, Any]:
        t0 = time.monotonic()

        if _deadline_exceeded(state):
            raise TurnError(
                "timeout",
                "the turn exceeded its wall-clock budget",
            )

        messages = state["messages"]
        last = messages[-1] if messages else None
        # Buffered leg: the first leg of a turn (or a reflection re-entry)
        # streams a tool-call decision — its text must never leak into the
        # answer body. Post-tools and degraded legs stream live.
        post_tools = isinstance(last, ToolMessage)
        emit_tokens = (not has_tools) or post_tools
        status("composing" if emit_tokens else "analyzing", state.get("rid", ""))

        if has_tools:
            msgs = _state_messages(state)
        else:
            msgs = _state_messages(state, base_prompt=SYSTEM_PROMPT)
        # Prune bulk LIST-type results and wrap tool results in <corpus_text>
        # delimiters (prompt-injection defense).
        if has_tools:
            msgs = _prune_tool_results_for_model(msgs)
            msgs = _wrap_tool_results_in_corpus_tags(msgs)

        # Fast-fail when EVERY retrieval errored structurally: tool results
        # are all native error JSON → the corpus is unreachable, and any
        # "answer" generated from it is fabrication with a disclaimer
        # stapled on.
        if has_tools:
            tool_texts = _get_tool_content_list(messages)
            if tool_texts and all(_is_db_error_json(t) for t in tool_texts):
                timed_phase(state, "agent_ms", t0)
                log.error("agent: all %d tool result(s) are DB-error JSON", len(tool_texts))
                raise TurnError(
                    "retrieval_unavailable",
                    "every retrieval tool reported a database error; nothing to answer from",
                )

        # Stream the agent model. Observability callbacks arrive via the
        # streamer's graph-level config, not per-model-call kwargs.
        stream_kwargs: dict[str, Any] = {"max_retries": settings.llm_max_retries}

        raw_parts: list[str] = []  # accepted content deltas (leak-suppressed)
        leaked_parts: list[str] = []
        reasoning_buf = ""
        first_token_ms: float | None = None
        tool_calls: list | None = None
        chunks = 0
        async for chunk in astream_with_retry(agent_model, msgs, **stream_kwargs):
            chunks += 1
            ak = getattr(chunk, "additional_kwargs", None) or {}
            reasoning = ak.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning and not (
                # The API sometimes re-sends the accumulated reasoning buffer
                # as one giant delta (observed live: 4,021- and 7,920-char
                # deltas byte-identical to the buffer, the last right before
                # truncation). Emitting it again would duplicate the trace.
                len(reasoning) >= REASONING_LEAK_MIN_CHARS
                and reasoning_buf.endswith(reasoning)
            ):
                reasoning_buf += reasoning
                reasoning_event(reasoning)
            elif isinstance(reasoning, str) and reasoning:
                log.warning(
                    "agent: suppressed duplicate reasoning flush (%d chars)",
                    len(reasoning),
                )
            text = _chunk_text(getattr(chunk, "content", None))
            if text and _is_reasoning_leak(text, reasoning_buf):
                # The API re-sent its buffered thinking as content: show it
                # in the reasoning trace, never in the answer body.
                log.warning(
                    "agent: suppressed reasoning-leak content chunk "
                    "(%d chars) — routed to the reasoning trace", len(text),
                )
                leaked_parts.append(text)
                reasoning_event(text)
            elif text:
                if emit_tokens:
                    token_event(text)
                raw_parts.append(text)
                if first_token_ms is None and emit_tokens:
                    # The overhaul's headline metric — time from node entry
                    # to the first token streamed to the client — logged for
                    # every answer leg (mirrors the agent_ms phase log).
                    first_token_ms = (time.monotonic() - t0) * 1000
                    log.info("agent: first token in %.0fms", first_token_ms)
            tc = getattr(chunk, "tool_calls", None)
            if tc:
                tool_calls = list(tc)
            invalid = list(getattr(chunk, "invalid_tool_calls", None) or [])
            if invalid:
                log.warning(
                    "agent: %d invalid tool call(s) from the model (%s)",
                    len(invalid),
                    "; ".join(getattr(c, "error", "") or "?" for c in invalid)[:200],
                )
            um = getattr(chunk, "usage_metadata", None)
            if isinstance(um, dict) and any(isinstance(v, int) for v in um.values()):
                usage(um)

        if chunks == 0:
            # An empty stream is NOT a silent success — the client would see
            # "composing"/"analyzing" forever followed by a message with no
            # body. Fail the turn with a code the frontend humanizes into
            # real copy.
            timed_phase(state, "agent_ms", t0)
            raise TurnError(
                "empty_response",
                "the agent model returned an empty stream; nothing was composed",
            )

        raw_text = "".join(raw_parts)

        # ── Tool-call leg: buffered text becomes a plan event ──
        if tool_calls:
            calls = [
                tc if isinstance(tc, dict)
                else {"id": f"tc_{i}", "name": tc.name, "args": tc.args or {}}
                for i, tc in enumerate(tool_calls)
            ]
            dropped = [tc for tc in calls if tc.get("name") not in TOOL_NAMES]
            if dropped:
                log.warning(
                    "agent: dropped %d non-allowlisted tool call(s)", len(dropped),
                )
                calls = [tc for tc in calls if tc.get("name") in TOOL_NAMES]
            if not post_tools and raw_text:
                # The buffered preamble tells the client what retrieval is
                # starting — plan text, never answer tokens.
                plan_event(raw_text)
            _emit_tool_starts(state, calls)
            if calls:
                timed_phase(state, "agent_ms", t0)
                return {
                    "messages": [AIMessage(content=raw_text, tool_calls=calls)],
                }
            # Every call was dropped: fall through to the answer path with
            # the buffered/live text we have.

        # ── Answer leg ──
        if not emit_tokens:
            # The buffered stream becomes the answer: move the client's
            # phase to composing, then replay the buffered text as tokens.
            status("composing", state.get("rid", ""))
            replay_start = time.monotonic()
            first_replayed = True
            for part in raw_parts:
                if first_replayed:
                    log.info("agent: first token in %.0fms", (replay_start - t0) * 1000)
                    first_replayed = False
                token_event(part)

        # Citation verification: the ONE authoritative pass. The verified
        # AIMessage returned here is the final answer.
        answer_text = raw_text
        if settings.citation_verification and has_tools:
            tool_contents = _get_tool_content_list(messages)
            had_tools = _has_tool_calls(messages)
            try:
                answer_text = verify_citations(
                    answer_text, tool_contents, had_tool_calls=had_tools,
                )
            except Exception as exc:
                log.warning("citation verification failed (returning original): %s", exc)

        # The disclaimer is part of the verified message (not appended
        # post-stream), so the streamed text, the client's final state,
        # and the reflection routing all see the same answer.
        if "not legal advice" not in answer_text.lower():
            answer_text = answer_text.rstrip() + f"\n\n*{DISCLAIMER}*"

        # Keep-better across reflection rounds. A reflection leg re-runs on
        # the SAME tool results and its answer can degenerate (observed
        # live: a 63-char tool-call echo). Such an answer must not replace a
        # previous round's answer that already cites the corpus — keep the
        # previous one and let a ``correction`` event restore the client's
        # view.
        prev_answer = state.get("last_answer", "")
        prev_cited = state.get("last_answer_cited", False)
        new_cited = bool(CITATION_RE.search(answer_text))
        if prev_answer and prev_cited and not new_cited:
            log.info(
                "agent: keeping previous cited answer over uncited "
                "re-answer (round %d)", state.get("round", 0) + 1,
            )
            # This round's (worse) tokens already streamed to the client;
            # the correction replaces them with the kept answer.
            correction_event(prev_answer)
            timed_phase(state, "agent_ms", t0)
            return {
                "messages": [],
                "round": state.get("round", 0) + 1,
                "last_answer": prev_answer,
                "last_answer_cited": True,
            }

        # Post-stream semantic events, emitted from the node that owns the
        # verified answer (the streamer never re-derives them).
        try:
            cites = parse_citations(answer_text)
            if cites:
                citations_event([{"act": c.act, "ref": c.ref} for c in cites])
        except Exception as exc:
            log.warning("citations event emission failed: %s", exc)

        if raw_text != answer_text:
            correction_event(answer_text)

        agent_ms = timed_phase(state, "agent_ms", t0)
        log.info(
            "agent leg done in %.0fms (raw=%d chars, verified=%d chars)",
            agent_ms, len(raw_text), len(answer_text),
        )

        # Advance the round counter (it counts answer legs) and record this
        # answer so a following reflection leg can keep it if it is better.
        return {
            "messages": [AIMessage(content=answer_text)],
            "round": state.get("round", 0) + 1,
            "last_answer": answer_text,
            "last_answer_cited": new_cited,
        }

    return _agent


def route_agent(state: ChatState, settings: Any) -> str:
    """After the agent: tools → agent → END, with a gated reflection.

    Routes to ``tools`` when the agent produced tool calls. After an answer
    leg, routes back to ``agent`` for ONE more retrieval round only when the
    answer has no citations, tools were called, the round budget allows
    (``round <= max_reflection_rounds``; total answer legs = max + 1), and
    enough of the turn budget remains. Otherwise routes to END.
    """
    messages = state.get("messages", [])
    last = messages[-1] if messages else None
    if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
        return "tools"

    current_round = state.get("round", 0)
    if current_round > settings.max_reflection_rounds:
        log.info("reflection: max rounds reached (%d), ending", current_round)
        return "end"

    if not isinstance(last, AIMessage):
        return "end"

    if not _has_tool_calls(messages):
        return "end"

    answer = last.content if isinstance(last.content, str) else str(last.content)
    if CITATION_RE.search(answer):
        return "end"

    deadline = state.get("deadline")
    if (
        deadline is not None and deadline > 0.0
        and time.monotonic() >= deadline - settings.reflection_deadline_s
    ):
        log.info(
            "reflection: less than %.0fs of budget left, ending uncited",
            settings.reflection_deadline_s,
        )
        return "end"

    log.info("reflection: routing back to agent (round %d)", current_round)
    return "agent"
