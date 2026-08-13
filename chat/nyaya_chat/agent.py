"""LangGraph supervisor-synthesis architecture over the nyaya MCP tools.

The graph has two phases:

1. **Supervisor** (short output): receives the user question, briefly reasons
   about which MCP tools to call, and emits ALL tool calls in a single
   ``AIMessage`` for parallel execution. The supervisor does NOT answer the
   question — it only plans and delegates.

2. **Parallel tool execution**: the ``DedupToolNode`` runs all tool calls
   concurrently (LangGraph's ``ToolNode`` uses ``asyncio.gather``). Duplicate
   (name+args) calls are deduplicated — the second call gets the cached
   result of the first.

3. **Synthesis** (full output): receives all tool results as ``ToolMessage``s
   and composes the final grounded answer with citations.

No checkpointer is used. Each request rebuilds the message list from the
client-supplied history plus the new user message; the agent runs to
completion and tokens stream out over SSE (see ``streaming.py``).
"""

from __future__ import annotations

import logging
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from .config import Settings, get_settings
from .llm import SUPERVISOR_PROMPT, ainvoke_with_retry, astream_with_retry, get_model
from .tools import load_tools

log = logging.getLogger("nyaya_chat.agent")


class ChatState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]


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
    """Create a ChatNVIDIA instance for a specific phase.

    Each phase (supervisor, synthesis) gets its own model instance with the
    appropriate model name and token cap. In tests, ``get_model`` is
    monkeypatched to return a ``FakeChatModel``; this factory respects that
    override when the model name matches.
    """
    base = get_model(settings)
    cached_name = getattr(base, "model", None) or getattr(getattr(base, "_client", None), "model", None)

    # If the cached model is a fake (tests) or the name matches, reuse it
    if cached_name == model_name and max_tokens == settings.llm_max_tokens:
        return base
    if not hasattr(base, "model") and not hasattr(base, "_client"):
        # FakeChatModel in tests — always reuse
        return base

    # Create a fresh ChatNVIDIA instance for a different model/token cap
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
    """A stable hashable key identifying a (tool_name, args) pair.

    Normalises argument values to strings so that e.g. ``302`` and ``"302"``
    map to the same key.
    """
    normalised = {k: str(v) for k, v in sorted(args.items())}
    return f"{name}:{normalised}"


def _clean_tool_content(content: Any) -> str:
    """Normalise a ToolMessage's content to a clean string.

    MCP adapters return tool results as a list of content blocks like
    ``[{'type': 'text', 'text': '{"act": "IPC", ...}', 'id': 'lc_...'}]``.
    LangGraph's ``ToolNode`` wraps this in a ``ToolMessage`` with
    ``content=str(result)``, producing the Python repr
    ``"[{'type': 'text', 'text': '...', 'id': 'lc_...'}]"`` — a string that
    is unreadable by both the synthesis model and the UI.

    This function detects that pattern and extracts the ``text`` field from
    each block, producing a clean JSON string (or plain text) that the model
    can use and the UI can format.
    """
    if isinstance(content, str):
        # Check if it looks like a stringified list of content blocks
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
                pass  # not a valid Python literal — return as-is
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
    """A ToolNode wrapper that skips duplicate (name+args) tool calls.

    If a tool call with the same ``_tool_call_key`` has already been executed
    in this graph run, the duplicate gets a synthetic ToolMessage with the
    cached result — the underlying MCP tool is not called again.

    Also post-processes ToolMessage content to extract clean text from the
    MCP adapter's list-of-blocks format (see ``_clean_tool_content``).
    """

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

        # Partition tool_calls into unique vs duplicate
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
            # Clean and cache results for future dedup lookups
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

        # Run unique calls through the real ToolNode
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

        # Add synthetic ToolMessages for duplicates
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
# Agent builder
# ---------------------------------------------------------------------------

async def _build_agent(settings: Settings) -> tuple[Any, list[Any]]:
    """Connect to MCP, build tools, and compile the supervisor-synthesis graph."""
    mcp_tools = await load_tools(settings)
    if not mcp_tools:
        log.warning("no MCP tools loaded, building degraded agent")
        model = get_model(settings)
        builder: StateGraph = StateGraph(ChatState)
        builder.add_node("agent", _make_synthesis_node(model, settings))
        builder.add_edge(START, "agent")
        builder.add_edge("agent", END)
        return builder.compile(), []

    log.info("loaded %d MCP tools", len(mcp_tools))

    # Supervisor model: short output for planning
    supervisor_model = _make_model(
        settings,
        model_name=settings.supervisor_model,
        max_tokens=settings.supervisor_max_tokens,
    )
    if hasattr(supervisor_model, "bind_tools"):
        supervisor_model = supervisor_model.bind_tools(mcp_tools)

    # Synthesis model: full output for answer composition
    synthesis_model = _make_model(
        settings,
        model_name=settings.synthesis_model,
        max_tokens=settings.synthesis_max_tokens,
    )

    async def call_supervisor(state: ChatState) -> dict[str, Any]:
        response = await ainvoke_with_retry(
            supervisor_model, state["messages"], max_retries=settings.llm_max_retries,
        )
        return {"messages": [response]}

    def route_supervisor(state: ChatState) -> str:
        last = state["messages"][-1]
        if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
            return "tools"
        return "synthesis"

    synthesis_fn = _make_synthesis_node(synthesis_model, settings)

    builder = StateGraph(ChatState)
    builder.add_node("supervisor", call_supervisor)
    builder.add_node("tools", DedupToolNode(mcp_tools))
    builder.add_node("synthesis", synthesis_fn)

    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges("supervisor", route_supervisor, ["tools", "synthesis"])
    builder.add_edge("tools", "synthesis")
    builder.add_edge("synthesis", END)

    graph = builder.compile()
    log.info(
        "compiled LangGraph supervisor-synthesis agent "
        "(supervisor=%s, synthesis=%s, mcp_tools=%d)",
        settings.supervisor_model, settings.synthesis_model, len(mcp_tools),
    )
    return graph, mcp_tools


def _make_synthesis_node(model: Any, settings: Settings):
    """Build the synthesis node function.

    The synthesis node receives the full message history (including tool
    results as ToolMessages) and produces the final grounded answer.
    """
    from .llm import SYSTEM_PROMPT

    async def _synthesis(state: ChatState) -> dict[str, Any]:
        messages = state["messages"]
        # Replace the supervisor system prompt with the synthesis system prompt
        out_msgs: list[BaseMessage] = [SystemMessage(content=SYSTEM_PROMPT)]
        for m in messages:
            if isinstance(m, SystemMessage):
                continue  # drop the supervisor's system prompt
            out_msgs.append(m)
        # Stream the synthesis model so LangGraph's stream_mode="messages"
        # intercepts each token via on_llm_new_token and yields it to the SSE
        # stream. Collect chunks for the final state update.
        chunks: list[Any] = []
        async for chunk in astream_with_retry(
            model, out_msgs, max_retries=settings.llm_max_retries,
        ):
            chunks.append(chunk)
        if chunks:
            final = chunks[0]
            for chunk in chunks[1:]:
                final = final + chunk
            return {"messages": [final]}
        return {"messages": []}

    return _synthesis


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
