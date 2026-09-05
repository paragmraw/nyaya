"""Synthesis node: composes the final grounded answer and owns verification.

Receives all tool results as ``ToolMessage``s, prunes LIST-type bulk, wraps
results in ``<corpus_text>`` delimiters (prompt-injection defense), streams
the synthesis model, and produces the final grounded answer.

The returned AIMessage is THE authoritative answer: citation verification
(if enabled) runs here, once, and the disclaimer is appended here when the
model omitted it — so the streamed-and-verified text is final. The node
itself emits the ``citations`` and (when the verified text differs from the
streamed tokens) ``correction`` events, so the streamer never re-verifies or
re-derives anything.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, SystemMessage, ToolMessage

from ..citations import CITATION_RE, parse_citations, verify_citations
from ..errors import TurnError
from ..llm import DISCLAIMER, astream_with_retry
from ..prompts import SYSTEM_PROMPT
from ..tools_layer.cleaning import prune_list_result, strip_corpus_tags
from .events import citations as citations_event
from .events import correction as correction_event
from .events import reasoning as reasoning_event
from .events import status, timed_phase, usage
from .events import token as token_event
from .state import ChatState

log = logging.getLogger("nyaya_chat.graph.synthesis")


def _is_db_error_json(text: str) -> bool:
    """True when a tool result is the native layer's *unavailability* error JSON.

    The native tools report a dead corpus/DB as
    ``{"error": {"code": "database_unavailable", ...}}`` (``native.py``'s
    ``_error_json`` shape); a failed embed is ``embedding_unavailable``.
    Feeding those into synthesis produces an answer built on nothing;
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
    # legitimate result the synthesis model must turn into a refusal —
    # short-circuiting on it would hard-fail valid refusal turns.
    return err.get("code") in {"database_unavailable", "embedding_unavailable"}


def _prune_tool_results_for_synthesis(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Prune bulk, non-essential fields from LIST-type tool results.

    **Pruning rule (conservative):** before the message list goes to the
    synthesis model, only LIST-type tool results (the multi-hit array of
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
    instructions in the synthesis prompt; the system prompt instructs the
    model to treat all text inside these tags as data only.
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


def _has_refusal(text: str) -> bool:
    """Check if the answer text indicates the model could not find a basis."""
    lower = text.lower()
    indicators = [
        "could not find a basis",
        "could not find",
        "not in the corpus",
        "no result",
        "no tool result",
        "i could not find",
        "not available in the corpus",
    ]
    return any(ind in lower for ind in indicators)


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


def make_synthesis_node(settings: Any, synthesis_model: Any, *, has_tools: bool = True):
    """Build the synthesis node for the given model.

    ``has_tools=False`` builds the degraded no-tools variant (a single-node
    graph that streams an answer directly, with no verification).
    """

    async def _synthesis(state: ChatState) -> dict[str, Any]:
        t0 = time.monotonic()
        status("composing", state.get("rid", ""))

        messages = state["messages"]
        # Replace the supervisor system prompt with the synthesis system prompt
        out_msgs: list[BaseMessage] = [SystemMessage(content=SYSTEM_PROMPT)]
        for m in messages:
            if isinstance(m, SystemMessage):
                continue
            out_msgs.append(m)

        # Wrap tool results in <corpus_text> delimiters (prompt-injection defense)
        if has_tools:
            out_msgs = _prune_tool_results_for_synthesis(out_msgs)
            out_msgs = _wrap_tool_results_in_corpus_tags(out_msgs)

        # Stream the synthesis model, emitting each delta as it arrives.
        # Observability callbacks arrive via the streamer's graph-level
        # config, not per-model-call kwargs.
        stream_kwargs: dict[str, Any] = {"max_retries": settings.llm_max_retries}

        if has_tools:
            # Fast-fail when EVERY retrieval errored structurally: tool
            # results are all native error JSON → the corpus is unreachable,
            # and any "answer" synthesized from it is fabrication with a
            # disclaimer stapled on.
            tool_texts = _get_tool_content_list(messages)
            if tool_texts and all(_is_db_error_json(t) for t in tool_texts):
                timed_phase(state, "synthesis_ms", t0)
                log.error("synthesis: all %d tool result(s) are DB-error JSON", len(tool_texts))
                raise TurnError(
                    "retrieval_unavailable",
                    "every retrieval tool reported a database error; nothing to synthesize from",
                )

        chunks: list[Any] = []
        raw_parts: list[str] = []
        async for chunk in astream_with_retry(synthesis_model, out_msgs, **stream_kwargs):
            chunks.append(chunk)
            ak = getattr(chunk, "additional_kwargs", None) or {}
            reasoning = ak.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning:
                reasoning_event(reasoning)
            text = _chunk_text(getattr(chunk, "content", None))
            if text:
                token_event(text)
                raw_parts.append(text)
            um = getattr(chunk, "usage_metadata", None)
            if isinstance(um, dict) and any(isinstance(v, int) for v in um.values()):
                usage(um)

        if not chunks:
            # An empty stream is NOT a silent success — the client would see
            # "composing" forever followed by a message with no body. Fail
            # the turn with a code the frontend humanizes into real copy.
            timed_phase(state, "synthesis_ms", t0)
            raise TurnError(
                "empty_response",
                "the synthesis model returned an empty stream; nothing was composed",
            )

        final = chunks[0]
        for chunk in chunks[1:]:
            final = final + chunk
        raw_text = _chunk_text(final.content)
        answer_text = raw_text

        # Citation verification: the ONE authoritative pass. The verified
        # AIMessage returned here is the final answer.
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

        if answer_text != raw_text:
            final = AIMessage(content=answer_text)

        # Post-stream semantic events, emitted from the node that owns the
        # verified answer (the streamer never re-derives them).
        try:
            cites = parse_citations(answer_text)
            if cites:
                citations_event([{"act": c.act, "ref": c.ref} for c in cites])
        except Exception as exc:
            log.warning("citations event emission failed: %s", exc)

        raw_streamed = "".join(raw_parts)
        if raw_streamed != answer_text:
            correction_event(answer_text)

        synthesis_ms = timed_phase(state, "synthesis_ms", t0)
        log.info(
            "synthesis done in %.0fms (raw=%d chars, verified=%d chars)",
            synthesis_ms, len(raw_text), len(answer_text),
        )

        # Increment round counter for reflection routing
        return {"messages": [final], "round": state.get("round", 0) + 1}

    return _synthesis


def route_synthesis(state: ChatState, settings: Any) -> str:
    """After synthesis, check if a reflection round is needed.

    Routes back to the supervisor if:
    - The answer has no citations AND tools were called AND we haven't
      exceeded MAX_REFLECTION_ROUNDS, OR
    - the answer contains a refusal phrase AND we haven't exceeded the cap.
    Otherwise routes to END.
    """
    current_round = state.get("round", 1)
    if current_round >= settings.max_reflection_rounds:
        log.info("reflection: max rounds reached (%d), ending", current_round)
        return "end"

    messages = state.get("messages", [])
    last = messages[-1] if messages else None
    if not isinstance(last, AIMessage):
        return "end"

    answer = last.content if isinstance(last.content, str) else str(last.content)
    had_tools = _has_tool_calls(messages)

    if not had_tools:
        return "end"

    if not CITATION_RE.search(answer) or _has_refusal(answer):
        log.info(
            "reflection: routing back to supervisor (round %d → %d), "
            "has_citations=%s, has_refusal=%s",
            current_round, current_round + 1,
            bool(CITATION_RE.search(answer)), _has_refusal(answer),
        )
        return "supervisor"

    return "end"


