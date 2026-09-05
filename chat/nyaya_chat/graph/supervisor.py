"""Supervisor node: plans tool calls, never answers.

Primary path is ``with_structured_output(ToolPlan)`` (structured, validated);
if that fails at invoke time the node permanently switches to the
``bind_tools`` fallback. When a model returns NO tool calls through either
path — the observed root cause of zero-tool-call turns — the node retries
ONCE with a corrective message before giving up and routing to synthesis
with whatever it has.

All semantic SSE events (``plan``, ``status: searching``, ``tool_start``)
are emitted here via ``graph/events``. Reflection rounds (round > 1) get
the retrieval-only prompt suffix and re-emit no plan text.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ..errors import TurnError
from ..llm import ainvoke_with_retry
from ..prompts import REFLECTION_PROMPT, SUPERVISOR_PROMPT
from ..schemas_llm import ToolPlan
from ..text_tools import parse_text_tool_calls
from ..tools_layer.spec import TOOL_NAMES
from .events import plan as plan_event
from .events import status, timed_phase, tool_start
from .state import ChatState

log = logging.getLogger("nyaya_chat.graph.supervisor")

# SSE tool_start args echo cap (the model-facing args are unaffected).
TOOL_START_ARGS_CHARS = 200


def _is_permanent_structured_failure(exc: BaseException) -> bool:
    """Heuristic: did the structured-output call fail PERMANENTLY?

    Only 400-class rejections (unsupported parameter / response_format, the
    observed NVIDIA ``guided_json`` 400) mean structured output will never
    work and the ``structured_dead`` latch should flip. A transient failure
    (429/5xx/timeout) must NOT latch: the latch lives for the process
    lifetime, and downgrading every future turn over one exhausted retry
    window would silently degrade answer quality forever. The heuristic is
    deliberately conservative — message-substring matching on the codes the
    endpoint actually returns.
    """
    text = str(exc).lower()
    return any(
        marker in text
        for marker in ("400", "404", "422", "unsupported", "not supported",
                       "invalid parameter", "response_format", "guided")
    )


def build_messages(message: str, history: list[dict[str, str]]) -> list:
    """Assemble the message list for a turn.

    The supervisor system prompt is first, then the capped history (oldest
    dropped if longer than ``Settings.max_history``), then the new user
    message. The synthesis node swaps in its own system prompt.
    """
    msgs: list = [SystemMessage(content=SUPERVISOR_PROMPT)]
    for turn in history:
        if turn["role"] == "user":
            msgs.append(HumanMessage(content=turn["content"]))
        else:
            msgs.append(AIMessage(content=turn["content"]))
    msgs.append(HumanMessage(content=message))
    return msgs


def _is_reflection_round(state: ChatState) -> bool:
    """True when the synthesis node has already produced one answer this turn.

    ``state["round"]`` counts completed synthesis rounds; the graph routes
    back to the supervisor for a reflection round when round 1 lacked
    citations or refused. There is no separate reflection flag: the round
    counter is the single source of truth, set by the synthesis node.
    """
    return state.get("round", 0) >= 1


def _state_messages(state: ChatState) -> list:
    """Rebuild the model input from state, honouring the reflection round.

    The stored system prompt is always the base supervisor prompt; reflection
    rounds get the retrieval-only suffix appended (the base prompt stays
    cached-prefix-stable for the first call of a turn).
    """
    msgs = [m for m in state["messages"] if not isinstance(m, SystemMessage)]
    system = SUPERVISOR_PROMPT + (REFLECTION_PROMPT if _is_reflection_round(state) else "")
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


def _as_tool_calls(name: str, args: dict[str, Any], index: int) -> dict[str, Any]:
    return {"id": f"tc_{index}", "name": name, "args": args}


def _plan_calls(plan: ToolPlan) -> tuple[list[dict[str, Any]], int]:
    """Extract allowlisted tool-call dicts from a structured ToolPlan.

    Returns ``(calls, dropped)`` — dropped counts specs whose name is not in
    the allowlist. Normalising here (spec → dict) is what keeps the protocol
    path's ``tc.get("name")`` safe: a pydantic ``ToolCallSpec`` would crash
    on ``.get``.
    """
    all_specs = list(plan.tool_calls or [])
    calls = [
        _as_tool_calls(tc.name, tc.args or {}, i)
        for i, tc in enumerate(all_specs)
        if tc.name in TOOL_NAMES
    ]
    return calls, len(all_specs) - len(calls)


def _as_model_message(response: Any) -> AIMessage:
    """Coerce a supervisor response into an AIMessage for the retry history.

    A ``ToolPlan`` is not a valid LangChain message — the corrective retry
    feeds the previous exchange back to the model, so a structured plan must
    be flattened to its reasoning text first.
    """
    if isinstance(response, AIMessage):
        return response
    if isinstance(response, ToolPlan):
        return AIMessage(content=response.reasoning or "")
    return AIMessage(content=response.content if isinstance(response.content, str) else str(response.content))


def _deadline_exceeded(state: ChatState) -> bool:
    """True when the turn's wall-clock budget (set by the streamer) is spent."""
    deadline = state.get("deadline")
    if deadline is None or deadline <= 0.0:
        return False
    return time.monotonic() > deadline


def make_supervisor_node(
    settings: Any,
    supervisor_model: Any,
    fallback_model: Any | None,
):
    """Build the supervisor node with the given primary/fallback models.

    ``supervisor_model`` is the structured-output model (or the bind_tools
    model when structured output is unavailable); ``fallback_model`` is the
    bind_tools model used once if the structured call fails.
    """

    # ``with_structured_output`` can be permanently unsupported by the model
    # (e.g. NVIDIA catalog rejects ``guided_json`` with a 400). Remember that
    # across turns in this closure — the graph is built once, so the flag lives
    # for the process lifetime — instead of paying a doomed structured
    # round-trip (tens of seconds) on EVERY turn.
    structured_dead = False

    async def _supervisor(state: ChatState) -> dict[str, Any]:
        t0 = time.monotonic()
        messages = _state_messages(state)
        # Observability (Langfuse) callbacks arrive via the single
        # ``config={"callbacks": ...}`` the streamer passes to
        # ``graph.astream`` — not per-model-call kwargs.
        invoke_kwargs: dict[str, Any] = {"max_retries": settings.llm_max_retries}
        nonlocal structured_dead

        if _deadline_exceeded(state):
            raise TurnError(
                "timeout",
                "the turn exceeded its wall-clock budget before retrieval started",
            )

        primary = fallback_model if structured_dead else supervisor_model
        # One attempt chain; skip the duplicate when primary IS the fallback
        # (structured output was unavailable at build time).
        chain = (primary,) if primary is fallback_model else (primary, fallback_model)
        response: Any = None
        last_exc: Exception | None = None
        used_fallback = False
        responded_by: Any = primary
        for model in chain:
            if model is None:
                break
            try:
                response = await ainvoke_with_retry(model, messages, **invoke_kwargs)
                responded_by = model
                break
            except Exception as exc:  # structured path failed → bind_tools once
                last_exc = exc
                if used_fallback or fallback_model is None:
                    raise
                used_fallback = True
                # Latch ONLY on a permanent structured-output rejection (the
                # latch outlives this turn); a transient failure retries the
                # structured path again next turn.
                if _is_permanent_structured_failure(exc):
                    structured_dead = True
                log.warning(
                    "supervisor structured output failed (%s), falling back to "
                    "bind_tools%s",
                    str(exc)[:120],
                    " permanently for this graph" if structured_dead else " for this turn",
                )
                # The doomed extra round-trip is invisible otherwise — keep
                # the client's phase indicator alive during it.
                status("analyzing", state.get("rid", ""))

        if response is None:
            raise RuntimeError(f"supervisor produced no response: {last_exc}")

        log.info("supervisor round done in %.0fms", (time.monotonic() - t0) * 1000.0)

        # ── Structured path: response is a ToolPlan ──
        if isinstance(response, ToolPlan):
            out_msgs: list = []
            reasoning = (response.reasoning or "").strip()
            if reasoning and not _is_reflection_round(state):
                # Round 2's plan text is not re-streamed (the first plan
                # already told the client what retrieval was happening).
                plan_event(reasoning)
                out_msgs.append(AIMessage(content=reasoning))
            calls, dropped = _plan_calls(response)
            if dropped:
                log.warning("supervisor: dropped %d non-allowlisted tool calls", dropped)
            if calls:
                _emit_tool_starts(state, calls)
                out_msgs.append(AIMessage(content="", tool_calls=calls))
                timed_phase(state, "supervisor_ms", t0)
                return {"messages": out_msgs}
            if not dropped:
                # A legitimately EMPTY plan — ToolPlan documents "Empty list
                # if no retrieval is needed". The plan text becomes the
                # synthesis input instead of crashing the node.
                timed_phase(state, "supervisor_ms", t0)
                log.info(
                    "supervisor: structured plan needs no retrieval; routing to synthesis"
                )
                return {"messages": out_msgs}
            # dropped > 0 with no valid calls: the model named tools outside
            # the allowlist — fall through to the corrective retry below.

        # ── Protocol path (bind_tools) or a structured plan whose calls were
        #    all dropped: recover tool calls from the response ──
        if not isinstance(response, ToolPlan):
            calls = list(getattr(response, "tool_calls", None) or [])
            invalid = list(getattr(response, "invalid_tool_calls", None) or [])
            if invalid:
                log.warning(
                    "supervisor: %d invalid tool call(s) from the model (%s)",
                    len(invalid),
                    "; ".join(getattr(c, "error", "") or "?" for c in invalid)[:200],
                )
            if not calls:
                # Free-text recovery: the model embedded calls in its content
                # instead of using the tool-calling protocol.
                content = response.content if isinstance(response.content, str) else str(response.content)
                parsed = parse_text_tool_calls(content)
                if parsed:
                    log.info("supervisor: recovered %d tool call(s) from free text", len(parsed))
                    calls = parsed

        if not calls:
            # Last resort: ONE corrective retry. A supervisor that returns
            # prose instead of calls leaves the turn with zero retrievals —
            # the dominant ungrounded-answer failure mode. The nudge spells
            # out the allowlist so a model that named off-list tools can
            # self-correct.
            log.info("supervisor: no tool calls, retrying with corrective nudge")
            retry_msgs = [*messages, _as_model_message(response), HumanMessage(
                content=(
                    "Your response contained no usable tool calls. You MUST respond "
                    "with tool calls only — no prose. Call the appropriate "
                    f"retrieval tool(s) now. Available tools: {', '.join(TOOL_NAMES)}."
                ),
            )]
            retry_response = await ainvoke_with_retry(responded_by, retry_msgs, **invoke_kwargs)
            if isinstance(retry_response, ToolPlan):
                calls, retry_dropped = _plan_calls(retry_response)
                if retry_dropped:
                    log.warning(
                        "supervisor: retry plan dropped %d non-allowlisted tool call(s)",
                        retry_dropped,
                    )
                # The retry's last word is its reasoning (a ToolPlan is not a
                # valid AIMessage to forward into synthesis).
                response = AIMessage(content=(retry_response.reasoning or ""))
            else:
                calls = list(getattr(retry_response, "tool_calls", None) or [])
                if not calls:
                    content = retry_response.content if isinstance(retry_response.content, str) else str(retry_response.content)
                    calls = parse_text_tool_calls(content)
                # The retry's response is the model's last word — forward that.
                response = retry_response

        calls = [
            tc if isinstance(tc, dict)
            else _as_tool_calls(tc.name, tc.args or {}, i)
            for i, tc in enumerate(calls)
        ]
        calls = [tc for tc in calls if tc.get("name") in TOOL_NAMES]
        _emit_tool_starts(state, calls)
        timed_phase(state, "supervisor_ms", t0)

        out_msgs = [AIMessage(content="", tool_calls=calls)] if calls else []
        if not calls:
            # Nothing retrievable: hand the raw response to synthesis so it
            # can answer (or refuse) instead of dropping the turn.
            log.info("supervisor: no tool calls after retry; routing to synthesis")
            if isinstance(response, AIMessage):
                out_msgs = [response]
        return {"messages": out_msgs}

    return _supervisor


def route_supervisor(state: ChatState) -> str:
    """After the supervisor: run tools when it produced calls, else synthesize.

    The destination node name matches ``graph.__init__``'s synthesis node
    registration (``"synthesis"``); importing it here would be circular.
    """
    last = state["messages"][-1]
    if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
        return "tools"
    return "synthesis"
