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


async def build_agent(settings: Settings | None = None) -> tuple[Any, list[Any]]:
    """Compile the ReAct graph and return ``(compiled_graph, tools)``.

    The graph is compiled without a checkpointer. Tools are loaded from the
    nyaya MCP server at build time; the returned list is also returned so
    callers (the server lifespan) can report the count in /health.
    """
    s = settings or get_settings()
    tools = await load_tools(s)
    model = get_model(s).bind_tools(tools) if tools else get_model(s)

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
        # No tools — single shot, still useful for /health + graceful degrade.
        builder.add_edge(START, "agent")
        builder.add_edge("agent", END)

    graph = builder.compile()
    log.info("compiled LangGraph agent (tools=%d)", len(tools))
    return graph, tools
