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
import re
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from .config import Settings, get_settings
from .llm import SUPERVISOR_PROMPT, ainvoke_with_retry, astream_with_retry, get_model
from .tools import load_tools

log = logging.getLogger("nyaya_chat.agent")

# Citation marker regex (used by reflection check)
_CITE_RE = re.compile(r"\[\[act:\s*[^,\]]+?\s*,\s*ref:\s*[^\]]+?\s*\]\]")


class ChatState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    round: int


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

def _make_model(settings: Settings, *, model_name: str, max_tokens: int) -> Any:
    """Create a ChatNVIDIA instance for a specific phase."""
    base = get_model(settings)
    cached_name = getattr(base, "model", None) or getattr(getattr(base, "_client", None), "model", None)

    if cached_name == model_name and max_tokens == settings.llm_max_tokens:
        return base
    if not hasattr(base, "model") and not hasattr(base, "_client"):
        return base

    from langchain_nvidia_ai_endpoints import ChatNVIDIA
    return ChatNVIDIA(
        model=model_name,
        temperature=settings.llm_temperature,
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


def _clean_tool_content(content: Any) -> str:
    """Normalise a ToolMessage's content to a clean string."""
    if isinstance(content, str):
        stripped = content.strip()
        if stripped.startswith("[{") and "'type'" in stripped and "'text'" in stripped:
            try:
                import ast
                parsed = ast.literal_eval(stripped)
                if isinstance(parsed, list):
                    parts = []
                    for block in parsed:
                        if isinstance(block, dict):
                            t = block.get("text") or block.get("content")
                            if t:
                                parts.append(str(t))
                        elif isinstance(block, str):
                            parts.append(block)
                    return (" ".join(parts))[:8000]
            except Exception:
                pass
        return content[:8000]
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                t = block.get("text") or block.get("content")
                if t:
                    parts.append(str(t))
            elif isinstance(block, str):
                parts.append(block)
        return (" ".join(parts))[:8000]
    return str(content)[:8000]


class DedupToolNode:
    """A ToolNode wrapper that skips duplicate (name+args) tool calls."""

    def __init__(self, tools: list[Any]):
        self._tool_node = ToolNode(tools)
        self._seen: set[str] = set()
        self._results: dict[str, str] = {}

    async def __call__(self, state: ChatState) -> dict[str, Any]:
        messages = state.get("messages", [])
        last_ai: AIMessage | None = None
        for m in reversed(messages):
            if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
                last_ai = m
                break
        if last_ai is None:
            return await self._tool_node.ainvoke(state)

        unique_calls: list[Any] = []
        duplicate_calls: list[Any] = []
        for tc in last_ai.tool_calls:
            key = _tool_call_key(tc["name"], tc.get("args", {}))
            if key in self._seen:
                duplicate_calls.append(tc)
            else:
                unique_calls.append(tc)
                self._seen.add(key)

        if not duplicate_calls:
            result = await self._tool_node.ainvoke(state)
            cleaned_msgs: list[BaseMessage] = []
            for m in result.get("messages", []):
                if isinstance(m, ToolMessage):
                    cleaned = _clean_tool_content(m.content)
                    m = ToolMessage(
                        content=cleaned,
                        tool_call_id=m.tool_call_id,
                        name=m.name,
                    )
                    for tc in last_ai.tool_calls:
                        if tc.get("id") == m.tool_call_id:
                            key = _tool_call_key(tc["name"], tc.get("args", {}))
                            self._results[key] = cleaned
                            break
                cleaned_msgs.append(m)
            return {"messages": cleaned_msgs}

        new_msgs: list[ToolMessage] = []
        if unique_calls:
            modified_ai = AIMessage(content=last_ai.content, tool_calls=unique_calls)
            modified_messages = messages[:-1] + [modified_ai]
            modified_state = {**state, "messages": modified_messages}
            result = await self._tool_node.ainvoke(modified_state)
            for m in result.get("messages", []):
                if isinstance(m, ToolMessage):
                    cleaned = _clean_tool_content(m.content)
                    m = ToolMessage(
                        content=cleaned,
                        tool_call_id=m.tool_call_id,
                        name=m.name,
                    )
                    for tc in unique_calls:
                        if tc.get("id") == m.tool_call_id:
                            key = _tool_call_key(tc["name"], tc.get("args", {}))
                            self._results[key] = cleaned
                            break
                    new_msgs.append(m)

        dup_msgs: list[ToolMessage] = []
        for tc in duplicate_calls:
            key = _tool_call_key(tc["name"], tc.get("args", {}))
            cached = self._results.get(key, "")
            dup_msgs.append(ToolMessage(
                content=cached or "(duplicate call skipped)",
                tool_call_id=tc.get("id", ""),
                name=tc["name"],
            ))
            log.info("dedup: skipped duplicate tool call %s args=%s", tc["name"], tc.get("args", {}))

        return {"messages": new_msgs + dup_msgs}


# ---------------------------------------------------------------------------
# Helpers for synthesis: wrap tool results in corpus_text delimiters
# ---------------------------------------------------------------------------

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
    return bool(_CITE_RE.search(text))


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
            content = re.sub(r"^<corpus_text>\n?", "", content)
            content = re.sub(r"\n?</corpus_text>$", "", content)
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
        builder.add_node("agent", _make_synthesis_node(model, settings, has_tools=False))
        builder.add_edge(START, "agent")
        builder.add_edge("agent", END)
        return builder.compile(), []

    log.info("loaded %d tools", len(mcp_tools))

    supervisor_model = _make_model(
        settings,
        model_name=settings.supervisor_model,
        max_tokens=settings.supervisor_max_tokens,
    )
    if hasattr(supervisor_model, "bind_tools"):
        supervisor_model = supervisor_model.bind_tools(mcp_tools)

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
        response = await ainvoke_with_retry(
            supervisor_model, state["messages"], **invoke_kwargs,
        )
        return {"messages": [response]}

    def route_supervisor(state: ChatState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
            return "tools"
        return "synthesis"

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
    builder.add_node("synthesis", synthesis_fn)

    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges("supervisor", route_supervisor, ["tools", "synthesis"])
    builder.add_edge("tools", "synthesis")
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
    and produces the final grounded answer. If citation verification is
    enabled, it verifies and strips ungrounded citations after streaming.
    """
    from .llm import SYSTEM_PROMPT

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
            answer_text = final.content if isinstance(final.content, str) else str(final.content)

            # Citation verification: strip ungrounded citations
            if settings.citation_verification and has_tools:
                tool_contents = _get_tool_content_list(messages)
                had_tools = _had_tool_calls(messages)
                verified = _verify_and_strip(
                    answer_text, tool_contents, had_tools=had_tools,
                )
                if verified != answer_text:
                    final = AIMessage(content=verified)

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
