"""LangGraph supervisor-synthesis architecture over the nyaya corpus tools.

The graph has these phases:

1. **Supervisor** (short output): receives the user question, briefly reasons
   about which tools to call, and emits ALL tool calls in a single
   ``AIMessage`` for parallel execution. The supervisor does NOT answer the
   question — it only plans and delegates.

2. **Parallel tool execution**: the ``DedupToolNode`` runs all tool calls
   concurrently. Duplicate (name+args) calls are deduplicated.

3. **Synthesis** (full output): receives all tool results as ``ToolMessage``s,
   wraps them in ``<corpus_text>`` delimiters (prompt-injection defense), and
   composes the final grounded answer with citations.

4. **Reflection check** (optional): after synthesis, if the answer appears
   ungrounded (no citations + tools were called), the graph routes back to
   the supervisor for one more retrieval round (up to ``MAX_REFLECTION_ROUNDS``
   total rounds).

5. **Citation verification** (post-synthesis): programmatic check that all
   ``[[act: X, ref: Y]]`` markers in the final answer are backed by tool
   results. Ungrounded citations are stripped. A caveat is appended if
   zero grounded citations remain.

No checkpointer is used. Each request rebuilds the message list from the
client-supplied history plus the new user message.
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from .citations import CITATION_RE
from .config import Settings, get_settings
from .llm import SUPERVISOR_PROMPT, ainvoke_with_retry, astream_with_retry, get_model
from .schemas_llm import ToolPlan
from .tool_call_parser import parse_text_tool_calls
from .tool_content import clean_tool_content, prune_list_result, strip_corpus_tags
from .tools import load_tools

log = logging.getLogger("nyaya_chat.agent")

# LangGraph node names, shared with streaming.py (which reads node updates
# from the streamed graph state by name). The degraded no-tools graph has a
# single node whose synthesis logic runs under the name "agent".
SYNTHESIS_NODE_NAME = "synthesis"
DEGRADED_NODE_NAME = "agent"


class ChatState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    round: int
    # Per-request tool-call dedup state used by ``DedupToolNode``. The compiled
    # graph (and thus the node instance) is shared across all requests, so the
    # dedup memory must live in state, not on the node.
    dedup_seen: dict[str, str]
    dedup_results: dict[str, str]


def _build_messages(message: str, history: list[dict[str, str]]) -> list[BaseMessage]:
    """Assemble the message list for a turn.

    The system prompt is first, then the capped history (oldest dropped if
    longer than ``Settings.max_history``), then the new user message.
    """
    msgs: list[BaseMessage] = [SystemMessage(content=SUPERVISOR_PROMPT)]
    for turn in history:
        if turn["role"] == "user":
            msgs.append(HumanMessage(content=turn["content"]))
        else:
            msgs.append(AIMessage(content=turn["content"]))
    msgs.append(HumanMessage(content=message))
    return msgs


# ---------------------------------------------------------------------------
# Model factory — centralised so tests can inject fakes via get_model.
# ---------------------------------------------------------------------------

def _make_model(settings: Settings, *, model_name: str, max_tokens: int, temperature: float | None = None) -> Any:
    """Create a chat model instance for a specific graph phase.

    The cached base model (``get_model``) is reused only when its
    configuration already matches the phase exactly — same model id,
    temperature, and token cap — which is what keeps a phase that shares
    the base configuration from constructing a second API client.

    Test fakes advertise themselves with the ``nyaya_fake_model`` marker
    attribute (the fake protocol documented in ``tests/conftest.py``) and
    honour the requested temperature/max_tokens via
    ``with_generation_params``; no duck-type sniffing of ``model`` /
    ``_client`` attributes.
    """
    base = get_model(settings)
    temp = temperature if temperature is not None else settings.llm_temperature

    if getattr(base, "nyaya_fake_model", False):
        return base.with_generation_params(temperature=temp, max_tokens=max_tokens)

    from langchain_nvidia_ai_endpoints import ChatNVIDIA
    if (
        isinstance(base, ChatNVIDIA)
        and base.model == model_name
        and base.temperature == temp
        and base.max_tokens == max_tokens
    ):
        return base
    return ChatNVIDIA(
        model=model_name,
        temperature=temp,
        max_completion_tokens=max_tokens,
        timeout=settings.llm_timeout_s,
        api_key=settings.nvidia_api_key.get_secret_value(),
    )


# ---------------------------------------------------------------------------
# Tool-call deduplication
# ---------------------------------------------------------------------------

def _tool_call_key(name: str, args: dict) -> str:
    """A stable hashable key identifying a (tool_name, args) pair."""
    normalised = {k: str(v) for k, v in sorted(args.items())}
    return f"{name}:{normalised}"


class DedupToolNode:
    """A ToolNode wrapper that skips duplicate (name+args) tool calls.

    The node itself is STATELESS: dedup memory lives in ``ChatState``
    (``dedup_seen`` maps a tool-call key to the id of the call that first
    issued it; ``dedup_results`` maps a key to its cleaned result) so it is
    scoped to a single request. The compiled graph is built once and shared
    across all requests, so instance-level state here would leak tool calls
    from one request (or one user's conversation) into the next.
    """

    def __init__(self, tools: list[Any]):
        self._tool_node = ToolNode(tools)

    async def __call__(self, state: ChatState) -> dict[str, Any]:
        messages = state.get("messages", [])
        last_ai: AIMessage | None = None
        for m in reversed(messages):
            if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
                last_ai = m
                break
        if last_ai is None:
            return await self._tool_node.ainvoke(state)

        # Per-request dedup state; LangGraph merges the returned dicts back
        # into state, so rounds within one request share this memory.
        seen: dict[str, str] = dict(state.get("dedup_seen", {}))
        results: dict[str, str] = dict(state.get("dedup_results", {}))

        unique_calls: list[Any] = []
        duplicate_calls: list[Any] = []
        for tc in last_ai.tool_calls:
            key = _tool_call_key(tc["name"], tc.get("args", {}))
            if key in seen:
                duplicate_calls.append(tc)
            else:
                unique_calls.append(tc)
                seen[key] = tc.get("id") or ""

        if not duplicate_calls:
            result = await self._tool_node.ainvoke(state)
            cleaned_msgs: list[BaseMessage] = []
            for m in result.get("messages", []):
                if isinstance(m, ToolMessage):
                    cleaned = clean_tool_content(m.content)
                    m = ToolMessage(
                        content=cleaned,
                        tool_call_id=m.tool_call_id,
                        name=m.name,
                    )
                    for tc in last_ai.tool_calls:
                        if tc.get("id") == m.tool_call_id:
                            key = _tool_call_key(tc["name"], tc.get("args", {}))
                            results[key] = cleaned
                            break
                cleaned_msgs.append(m)
            return {
                "messages": cleaned_msgs,
                "dedup_seen": seen,
                "dedup_results": results,
            }

        new_msgs: list[ToolMessage] = []
        if unique_calls:
            modified_ai = AIMessage(content=last_ai.content, tool_calls=unique_calls)
            modified_messages = messages[:-1] + [modified_ai]
            modified_state = {**state, "messages": modified_messages}
            result = await self._tool_node.ainvoke(modified_state)
            for m in result.get("messages", []):
                if isinstance(m, ToolMessage):
                    cleaned = clean_tool_content(m.content)
                    m = ToolMessage(
                        content=cleaned,
                        tool_call_id=m.tool_call_id,
                        name=m.name,
                    )
                    for tc in unique_calls:
                        if tc.get("id") == m.tool_call_id:
                            key = _tool_call_key(tc["name"], tc.get("args", {}))
                            results[key] = cleaned
                            break
                    new_msgs.append(m)

        dup_msgs: list[ToolMessage] = []
        for tc in duplicate_calls:
            key = _tool_call_key(tc["name"], tc.get("args", {}))
            cached = results.get(key, "")
            dup_msgs.append(ToolMessage(
                content=cached or "(duplicate call skipped)",
                tool_call_id=tc.get("id", ""),
                name=tc["name"],
            ))
            log.info("dedup: skipped duplicate tool call %s args=%s", tc["name"], tc.get("args", {}))

        return {
            "messages": new_msgs + dup_msgs,
            "dedup_seen": seen,
            "dedup_results": results,
        }


# ---------------------------------------------------------------------------
# Helpers for synthesis: wrap tool results in corpus_text delimiters
# ---------------------------------------------------------------------------

def _prune_tool_results_for_synthesis(messages: list[BaseMessage]) -> list[BaseMessage]:
    """Prune bulk, non-essential fields from LIST-type tool results.

    **Pruning rule (conservative):** before the message list goes to the
    synthesis model, only LIST-type tool results (the multi-hit array of
    ``semantic_query`` — up to 50 hits of snippet text, the ~12K-token worst
    case per round) are pruned, and only beyond the top hit:

    * Hit 0 survives verbatim (full snippet). Hits 1..N keep their
      identification fields (act, ref, title, kind, rank, citation) and a
      300-char snippet — enough to cite the provision or fetch its full text
      via ``get_section``/``get_article`` in a follow-up round.
    * Redundant envelope metadata (query echo, source, as_of, offset, limit,
      fallback_reason) is dropped.
    * Single-document results — ``get_section``/``get_article``/
      ``get_judgment``, whose full text the answer must quote and the
      citation verifier must match — and anything that does not parse as the
      expected JSON shape pass through UNCHANGED. When in doubt, don't prune.

    The details live in :func:`nyaya_chat.tool_content.prune_list_result`; this
    only applies it to ToolMessages and rebuilds those whose content changed.
    Pruning is read-time: it affects the model input, not the dedup cache or
    the unpruned ``messages`` used for citation verification.
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

    This is a prompt-injection defense: it clearly delineates corpus data
    from instructions in the synthesis prompt. The synthesis system prompt
    instructs the model to treat all text inside these tags as data only.
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


def _has_citations(text: str) -> bool:
    """Check if the answer text contains at least one [[act: X, ref: Y]] marker."""
    return bool(CITATION_RE.search(text))


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


def _had_tool_calls(messages: list[BaseMessage]) -> bool:
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


# ---------------------------------------------------------------------------
# Agent builder
# ---------------------------------------------------------------------------

async def _build_agent(settings: Settings) -> tuple[Any, list[Any]]:
    """Connect to tools, build the supervisor-synthesis graph with reflection."""
    mcp_tools = await load_tools(settings)
    if not mcp_tools:
        log.warning("no tools loaded, building degraded agent")
        model = get_model(settings)
        builder: StateGraph = StateGraph(ChatState)
        builder.add_node(DEGRADED_NODE_NAME, _make_synthesis_node(model, settings, has_tools=False))
        builder.add_edge(START, DEGRADED_NODE_NAME)
        builder.add_edge(DEGRADED_NODE_NAME, END)
        return builder.compile(), []

    log.info("loaded %d tools", len(mcp_tools))

    # Build the supervisor model. Try with_structured_output(ToolPlan) first;
    # if the API doesn't support it at invoke time, fall back to bind_tools.
    # The fallback is handled in call_supervisor (catch the exception and
    # switch to the bind_tools model on the first failure).
    supervisor_base = _make_model(
        settings,
        model_name=settings.supervisor_model,
        max_tokens=settings.supervisor_max_tokens,
        temperature=settings.supervisor_temperature,
    )

    # Disable thinking mode so the model focuses on tool calling instead of reasoning
    if hasattr(supervisor_base, "with_thinking_mode"):
        try:
            supervisor_base = supervisor_base.with_thinking_mode(enabled=False)
            log.info("supervisor: thinking mode disabled")
        except Exception:
            log.warning("could not disable thinking mode for supervisor")

    # Pre-build both models so the fallback is instant.
    supervisor_structured = None
    supervisor_bind_tools = None
    if hasattr(supervisor_base, "with_structured_output"):
        try:
            supervisor_structured = supervisor_base.with_structured_output(ToolPlan)
            log.info("supervisor: with_structured_output(ToolPlan) available")
        except Exception:
            pass
    if hasattr(supervisor_base, "bind_tools"):
        supervisor_bind_tools = supervisor_base.bind_tools(mcp_tools)
        log.info("supervisor: bind_tools available as fallback")

    # State: which model to use (switches to bind_tools if structured fails)
    supervisor_model = supervisor_structured or supervisor_bind_tools or supervisor_base
    supervisor_fallback = supervisor_bind_tools

    synthesis_model = _make_model(
        settings,
        model_name=settings.synthesis_model,
        max_tokens=settings.synthesis_max_tokens,
    )

    async def call_supervisor(state: ChatState) -> dict[str, Any]:
        from .observability import get_langfuse_callbacks
        callbacks = get_langfuse_callbacks()
        invoke_kwargs: dict[str, Any] = {"max_retries": settings.llm_max_retries}
        if callbacks:
            invoke_kwargs["config"] = {"callbacks": callbacks}

        nonlocal supervisor_model, supervisor_fallback
        try:
            response = await ainvoke_with_retry(
                supervisor_model, state["messages"], **invoke_kwargs,
            )
        except Exception as exc:
            if supervisor_fallback is not None and supervisor_model is not supervisor_fallback:
                log.warning("supervisor structured output failed (%s), falling back to bind_tools", str(exc)[:120])
                supervisor_model = supervisor_fallback
                supervisor_fallback = None  # only fall back once
                response = await ainvoke_with_retry(
                    supervisor_model, state["messages"], **invoke_kwargs,
                )
            else:
                raise

        # Convert structured ToolPlan to LangChain messages.
        # If with_structured_output was used, response is a ToolPlan object.
        # If bind_tools fallback was used, response is an AIMessage with tool_calls.
        if isinstance(response, ToolPlan):
            # Structured output path: convert ToolPlan to messages
            msgs_out: list[BaseMessage] = []
            # The reasoning becomes an AIMessage (shows as "plan" in the frontend)
            if response.reasoning and response.reasoning.strip():
                msgs_out.append(AIMessage(content=response.reasoning))
            # The tool calls become an AIMessage with tool_calls attribute
            if response.tool_calls:
                tool_calls_list = [
                    {
                        "id": f"tc_{i}",
                        "name": tc.name,
                        "args": tc.args,
                    }
                    for i, tc in enumerate(response.tool_calls)
                ]
                msgs_out.append(AIMessage(content="", tool_calls=tool_calls_list))
            return {"messages": msgs_out}
        else:
            # Fallback path (bind_tools): response is an AIMessage.
            # The model may have emitted tool calls as text (JSON in content)
            # instead of using the tool-calling protocol. Parse them.
            if isinstance(response, AIMessage) and not getattr(response, "tool_calls", None):
                parsed = parse_text_tool_calls(response.content)
                if parsed:
                    log.info("supervisor: parsed %d tool calls from text response", len(parsed))
                    return {"messages": [AIMessage(content="", tool_calls=parsed)]}
            return {"messages": [response]}

    def route_supervisor(state: ChatState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
            return "tools"
        return SYNTHESIS_NODE_NAME

    synthesis_fn = _make_synthesis_node(synthesis_model, settings, has_tools=True)

    def route_synthesis(state: ChatState) -> str:
        """After synthesis, check if a reflection round is needed.

        Routes back to supervisor if:
        - The answer has no citations AND tools were called AND we haven't
          exceeded MAX_REFLECTION_ROUNDS.
        - OR the answer contains a refusal phrase AND we haven't exceeded
          the round cap.
        Otherwise routes to END.
        """
        current_round = state.get("round", 1)
        if current_round >= settings.max_reflection_rounds:
            log.info("reflection: max rounds reached (%d), ending", current_round)
            return END

        messages = state.get("messages", [])
        last = messages[-1] if messages else None
        if not isinstance(last, AIMessage):
            return END

        answer = last.content if isinstance(last.content, str) else str(last.content)
        had_tools = _had_tool_calls(messages)

        if not had_tools:
            return END

        if not _has_citations(answer) or _has_refusal(answer):
            log.info(
                "reflection: routing back to supervisor (round %d → %d), "
                "has_citations=%s, has_refusal=%s",
                current_round, current_round + 1,
                _has_citations(answer), _has_refusal(answer),
            )
            return "supervisor"

        return END

    builder = StateGraph(ChatState)
    builder.add_node("supervisor", call_supervisor)
    builder.add_node("tools", DedupToolNode(mcp_tools))
    builder.add_node(SYNTHESIS_NODE_NAME, synthesis_fn)

    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges("supervisor", route_supervisor, ["tools", SYNTHESIS_NODE_NAME])
    builder.add_edge("tools", SYNTHESIS_NODE_NAME)
    builder.add_conditional_edges("synthesis", route_synthesis, ["supervisor", END])

    graph = builder.compile()
    log.info(
        "compiled LangGraph supervisor-synthesis agent with reflection "
        "(supervisor=%s, synthesis=%s, tools=%d, max_rounds=%d, citation_verification=%s)",
        settings.supervisor_model, settings.synthesis_model, len(mcp_tools),
        settings.max_reflection_rounds, settings.citation_verification,
    )
    return graph, mcp_tools


def _make_synthesis_node(model: Any, settings: Settings, *, has_tools: bool = True):
    """Build the synthesis node function.

    The synthesis node receives the full message history (including tool
    results as ToolMessages), wraps tool results in <corpus_text> delimiters,
    and produces the final grounded answer. The returned AIMessage is THE
    authoritative answer: citation verification (if enabled) runs here, once,
    and the disclaimer is appended here when the model omitted it, so the
    streamed-and-verified text is final.
    """
    from .llm import DISCLAIMER, SYSTEM_PROMPT

    async def _synthesis(state: ChatState) -> dict[str, Any]:
        from .observability import get_langfuse_callbacks
        callbacks = get_langfuse_callbacks()

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

        # Stream the synthesis model
        chunks: list[Any] = []
        stream_kwargs: dict[str, Any] = {"max_retries": settings.llm_max_retries}
        if callbacks:
            stream_kwargs["config"] = {"callbacks": callbacks}
        async for chunk in astream_with_retry(
            model, out_msgs, **stream_kwargs,
        ):
            chunks.append(chunk)
        if chunks:
            final = chunks[0]
            for chunk in chunks[1:]:
                final = final + chunk
            raw_text = final.content if isinstance(final.content, str) else str(final.content)
            answer_text = raw_text

            # Citation verification: the ONE authoritative pass. The verified
            # AIMessage returned here is the final answer — the SSE streamer
            # derives the citations event from it and only compares (never
            # re-verifies) when deciding whether to emit a correction.
            if settings.citation_verification and has_tools:
                tool_contents = _get_tool_content_list(messages)
                had_tools = _had_tool_calls(messages)
                answer_text = _verify_and_strip(
                    answer_text, tool_contents, had_tools=had_tools,
                )

            # The disclaimer is part of the verified message (not appended
            # post-stream), so the streamed text, the client's final state,
            # and the reflection routing all see the same answer.
            if "not legal advice" not in answer_text.lower():
                answer_text = answer_text.rstrip() + f"\n\n*{DISCLAIMER}*"

            if answer_text != raw_text:
                final = AIMessage(content=answer_text)

            # Increment round counter for reflection routing
            current_round = state.get("round", 0) + 1
            return {"messages": [final], "round": current_round}
        return {"messages": [], "round": state.get("round", 0) + 1}

    return _synthesis


def _verify_and_strip(
    answer_text: str,
    tool_contents: list[str],
    *,
    had_tools: bool = False,
) -> str:
    """Run citation verification on the synthesis output."""
    from .citations import verify_citations
    try:
        return verify_citations(
            answer_text, tool_contents, had_tool_calls=had_tools,
        )
    except Exception as exc:
        log.warning("citation verification failed (returning original): %s", exc)
        return answer_text


# ---------------------------------------------------------------------------
# Global state for lazy initialization
# ---------------------------------------------------------------------------

_agent_graph: Any = None
_agent_tools: list[Any] | None = None
_agent_build_lock: Any = None


def _get_build_lock():
    global _agent_build_lock
    if _agent_build_lock is None:
        import asyncio
        _agent_build_lock = asyncio.Lock()
    return _agent_build_lock


async def get_agent() -> tuple[Any, list[Any]]:
    """Get or build the chat agent (lazy initialization with thread safety)."""
    global _agent_graph, _agent_tools

    if _agent_graph is not None and _agent_tools is not None:
        return _agent_graph, _agent_tools

    lock = _get_build_lock()
    async with lock:
        if _agent_graph is not None and _agent_tools is not None:
            return _agent_graph, _agent_tools

        settings = get_settings()
        _agent_graph, _agent_tools = await _build_agent(settings)
        return _agent_graph, _agent_tools


def get_agent_if_ready() -> tuple[Any, list[Any]] | tuple[None, None]:
    """Return the ALREADY-BUILT agent, or ``(None, None)`` — never builds.

    The fast path for ``/chat/health``: it reports the prewarmed graph when
    the host lifespan (or an earlier request) built one, and reports the
    agent as still-initialising otherwise, WITHOUT triggering the seconds-long
    build on a health probe. Building happens only in ``get_agent`` and
    ``build_agent``.
    """
    if _agent_graph is not None and _agent_tools is not None:
        return _agent_graph, _agent_tools
    return None, None


def reset_agent() -> None:
    """Reset the agent state (for testing)."""
    global _agent_graph, _agent_tools
    _agent_graph = None
    _agent_tools = None


async def build_agent(settings: Settings | None = None) -> tuple[Any, list[Any]]:
    """Build the agent (backward compatible with tests)."""
    if settings is None:
        return await get_agent()
    return await _build_agent(settings)
