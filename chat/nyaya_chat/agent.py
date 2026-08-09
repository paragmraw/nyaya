"""LangGraph ReAct agent over the nyaya MCP tools.

The graph is a simple two-node ReAct loop:
``START → agent → (tool_calls? tools : END) → agent → …``

No checkpointer is used (per the product decision: we do not persist
conversations). Each request rebuilds the message list from the client-supplied
history plus the new user message; the agent runs to completion and the
tokens stream out over SSE (see ``streaming.py``).
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode

from .config import Settings, get_settings
from .llm import SYSTEM_PROMPT, get_model
from .tools import load_tools

log = logging.getLogger("nyaya_chat.agent")


class ChatState(TypedDict, total=False):
    messages: list[BaseMessage]


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

            builder: StateGraph = StateGraph(ChatState)
            builder.add_node("agent", call_model)
            if tools:
                builder.add_node("tools", ToolNode(tools))
                builder.add_edge(START, "agent")

                def route(state: ChatState) -> str:
                    last = state["messages"][-1]
                    if isinstance(last, AIMessage) and getattr(last, "tool_calls", None):
                        return "tools"
                    return END

                builder.add_conditional_edges("agent", route, ["tools", END])
                builder.add_edge("tools", "agent")
            else:
                builder.add_edge(START, "agent")
                builder.add_edge("agent", END)

            graph = builder.compile()
            log.info("compiled LangGraph agent (tools=%d)", len(tools))
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
