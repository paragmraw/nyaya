"""LangGraph ReAct agent over the nyaya MCP tools.

The graph is a two-stage pipeline:

1. **ReAct retrieval loop**: ``START → agent → (tool_calls? tools : synthesis)``
   with ``tools → agent`` cycling until the model produces a final answer.
   The model is wrapped with ``with_thinking_mode(True)`` so reasoning
   tokens stream as ``event: reasoning``.

2. **Structured synthesis**: ``→ synthesis → END`` — a separate call using
   ``with_structured_output(CitedAnswer)`` transforms the draft answer +
   retrieved tool results into a schema-guaranteed ``CitedAnswer`` object
   with ``citations[]``. The citations are emitted as ``event: citations``
   via ``get_stream_writer()``. If structured output fails, the graph
   gracefully degrades to the raw ReAct answer (with inline ``[[act:…]]``
   markers parsed by the frontend as fallback).

No checkpointer is used (per the product decision: we do not persist
conversations). Each request rebuilds the message list from the client-supplied
history plus the new user message; the agent runs to completion and the
tokens stream out over SSE (see ``streaming.py``).
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from .config import Settings, get_settings
from .llm import SYNTHESIS_PROMPT, SYSTEM_PROMPT, get_base_model, get_model
from .schemas import CitedAnswer, StructuredCitation
from .tools import load_tools

log = logging.getLogger("nyaya_chat.agent")


class ChatState(TypedDict, total=False):
    messages: list[BaseMessage]
    cited_answer: Any  # CitedAnswer | None


def _build_messages(message: str, history: list[dict[str, str]]) -> list[BaseMessage]:
    """Assemble the message list for a turn.

    The system prompt is first, then the capped history (oldest dropped if
    longer than ``Settings.max_history``), then the new user message.
    """
    msgs: list[BaseMessage] = [SystemMessage(content=SYSTEM_PROMPT)]
    for turn in history:
        if turn["role"] == "user":
            msgs.append(HumanMessage(content=turn["content"]))
        else:
            msgs.append(AIMessage(content=turn["content"]))
    msgs.append(HumanMessage(content=message))
    return msgs


def _collect_tool_context(messages: list[BaseMessage]) -> str:
    """Extract the text content of all ToolMessages for the synthesis prompt."""
    parts: list[str] = []
    for msg in messages:
        if isinstance(msg, ToolMessage):
            content = getattr(msg, "content", "")
            name = getattr(msg, "name", "tool")
            if isinstance(content, str):
                parts.append(f"[{name}] {content[:1200]}")
    return "\n\n".join(parts) if parts else "(no tool results)"


def _make_call_synthesis(settings: Settings) -> Any:
    """Build the synthesis node function.

    Returns an async function that:
    1. Collects the draft answer (last AIMessage) + tool results from state.
    2. Calls ``get_base_model().with_structured_output(CitedAnswer)`` to
       produce schema-guaranteed citations.
    3. Emits citations via ``get_stream_writer()`` as a custom event.
    4. Falls back gracefully if structured output fails.
    """
    async def call_synthesis(state: ChatState) -> dict[str, Any]:
        messages = state.get("messages", [])
        # Find the last AIMessage (the draft answer from the ReAct loop).
        draft_answer = ""
        for msg in reversed(messages):
            if isinstance(msg, AIMessage) and msg.content:
                draft_answer = msg.content
                break

        tool_context = _collect_tool_context(messages)

        synthesis_messages: list[BaseMessage] = [
            SystemMessage(content=SYNTHESIS_PROMPT),
            HumanMessage(content=(
                f"Draft answer:\n{draft_answer}\n\n"
                f"Retrieved provisions:\n{tool_context}\n\n"
                "Produce the final answer with structured citations."
            )),
        ]

        try:
            base_model = get_base_model(settings)
            structured_model = base_model.with_structured_output(CitedAnswer)
            result = await structured_model.ainvoke(synthesis_messages)

            if result is not None and isinstance(result, CitedAnswer):
                # Emit citations as a custom event for the SSE stream.
                try:
                    from langgraph.config import get_stream_writer
                    writer = get_stream_writer()
                    citations_data = [
                        {
                            "act": c.act,
                            "ref": c.ref,
                            **({"quote": c.quote} if c.quote else {}),
                        }
                        for c in result.citations
                    ]
                    writer({"type": "citations", "citations": citations_data})
                except Exception:
                    log.debug("get_stream_writer not available, skipping citations event", exc_info=True)

                return {"cited_answer": result}
            else:
                log.warning("structured output returned None, falling back to raw answer")
                return {"cited_answer": None}
        except Exception as exc:
            log.warning("structured synthesis failed, falling back to raw answer: %s", exc)
            return {"cited_answer": None}

    return call_synthesis


async def _build_agent_with_retry(settings: Settings, max_retries: int = 3, base_delay: float = 1.0) -> tuple[Any, list[Any]]:
    """Build agent with retry logic for MCP connection."""
    import asyncio

    last_exception: BaseException | None = None
    for attempt in range(max_retries):
        try:
            log.info("Building chat agent (attempt %d/%d)", attempt + 1, max_retries)
            tools = await load_tools(settings)
            model = get_model(settings).bind_tools(tools) if tools else get_model(settings)

            async def call_model(state: ChatState) -> dict[str, Any]:
                messages = state["messages"]
                response = await model.ainvoke(messages)
                return {"messages": [response]}

            call_synthesis = _make_call_synthesis(settings)

            builder: StateGraph = StateGraph(ChatState)
            builder.add_node("agent", call_model)
            builder.add_node("synthesis", call_synthesis)
            if tools:
                builder.add_node("tools", ToolNode(tools))
                builder.add_edge(START, "agent")

                def route(state: ChatState) -> str:
                    last = state["messages"][-1]
                    if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
                        return "tools"
                    return "synthesis"

                builder.add_conditional_edges("agent", route, ["tools", "synthesis"])
                builder.add_edge("tools", "agent")
                builder.add_edge("synthesis", END)
            else:
                builder.add_edge(START, "agent")
                builder.add_edge("agent", "synthesis")
                builder.add_edge("synthesis", END)

            graph = builder.compile()
            log.info("compiled LangGraph agent (tools=%d, synthesis=on)", len(tools))
            return graph, tools
        except Exception as e:
            last_exception = e
            log.warning("Failed to build chat agent (attempt %d/%d): %s", attempt + 1, max_retries, e)
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                log.info("Retrying in %.1f seconds...", delay)
                await asyncio.sleep(delay)
            else:
                log.error("All retries exhausted for building chat agent")
                if last_exception is not None:
                    raise last_exception
                raise RuntimeError("All retries exhausted for building chat agent")

    # Should never be reached, but mypy needs a return
    raise RuntimeError("All retries exhausted for building chat agent")


# Global state for lazy initialization
_agent_graph: Any = None
_agent_tools: list[Any] | None = None
_agent_build_lock: Any = None


def _get_build_lock():
    """Get or create asyncio lock for agent building."""
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
        # Double-check after acquiring lock
        if _agent_graph is not None and _agent_tools is not None:
            return _agent_graph, _agent_tools

        settings = get_settings()
        _agent_graph, _agent_tools = await _build_agent_with_retry(settings)
        return _agent_graph, _agent_tools


def reset_agent() -> None:
    """Reset the agent state (for testing)."""
    global _agent_graph, _agent_tools
    _agent_graph = None
    _agent_tools = None


# Backward compatibility for tests
async def build_agent(settings: Settings | None = None) -> tuple[Any, list[Any]]:
    """Build the agent (backward compatible with tests).

    This wraps the new lazy initialization for backward compatibility with tests.
    """
    return await get_agent()
