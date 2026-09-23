"""Graph assembly: agent → parallel tools → agent, with gated reflection.

Phase 1 pipeline collapse: ONE streaming, tool-bound agent node replaces the
old supervisor (structured ToolPlan planner + corrective retry) and synthesis
nodes — ``START → agent → (tools → agent | END)``. One LLM round trip on the
answer path replaces up to three.

This module builds the compiled ``StateGraph`` and holds its module-level
lifecycle state (:func:`get_graph` / :func:`get_graph_if_ready` /
:func:`reset_graph`).

**No checkpointer.** The client supplies the conversation history on every
turn (``ChatRequest.history``) and each turn is a bounded run of at most
``MAX_REFLECTION_ROUNDS + 1`` answer legs; persisting state across turns
would buy server-side memory we have not productized while adding an
AsyncPostgresSaver dependency to every request. Revisit only if
resume-from-checkpoint or server-side memory becomes a requirement.

**Event flow:** nodes emit every semantic SSE event (tokens included) via
``graph/events`` on LangGraph's custom stream mode; ``streaming.py``
consumes ``stream_mode=["custom"]`` and projects the dicts onto the wire.
Nothing here inspects node names at runtime — the node-name string coupling
the old streamer relied on is gone by construction.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from langgraph.graph import END, START, StateGraph

from ..config import Settings, get_settings
from ..llm import get_model
from ..tools_layer import load_tools
from .agent import build_messages, make_agent_node, route_agent
from .state import ChatState
from .tools_node import DedupToolNode

log = logging.getLogger("nyaya_chat.graph")

# Re-exported for tests and the server (the agent owns message assembly now).
__all__ = [
    "build_graph",
    "build_graph_for",
    "build_messages",
    "get_graph",
    "get_graph_if_ready",
    "reset_graph",
]

_DEGRADED_NODE_NAME = "degraded_synthesis"


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


async def build_graph(settings: Settings) -> tuple[Any, list[Any]]:
    """Connect to tools and compile the agent-tools graph.

    Returns ``(compiled_graph, tools)``. With zero tools loaded, a degraded
    single-node graph is returned (streams an answer directly, no retrieval,
    no verification) so the endpoint degrades instead of failing.
    """
    tools = await load_tools(settings)
    if not tools:
        log.warning("no tools loaded, building degraded graph")
        model = get_model(settings)
        builder: StateGraph = StateGraph(ChatState)
        builder.add_node(_DEGRADED_NODE_NAME, make_agent_node(settings, model, has_tools=False))
        builder.add_edge(START, _DEGRADED_NODE_NAME)
        builder.add_edge(_DEGRADED_NODE_NAME, END)
        return builder.compile(), []

    log.info("loaded %d tools", len(tools))

    # The agent model: thinking off (reasoning tokens share the completion
    # budget and delayed the first answer word by 3k+ tokens — see
    # SYNTHESIS_THINKING in config), tool-bound so it can call the retrieval
    # tools directly.
    agent_base = _make_model(
        settings,
        model_name=settings.synthesis_model,
        max_tokens=settings.synthesis_max_tokens,
    )
    if not settings.synthesis_thinking and hasattr(agent_base, "with_thinking_mode"):
        try:
            agent_base = agent_base.with_thinking_mode(enabled=False)
            log.info("agent: thinking mode disabled")
        except Exception:
            log.warning("could not disable thinking mode for the agent model")

    if hasattr(agent_base, "bind_tools"):
        agent_model = agent_base.bind_tools(tools)
        log.info("agent: bind_tools available")
    else:
        # A model without bind_tools cannot call tools; the graph degrades
        # to direct answers (route_agent's has-tool checks keep it honest).
        log.warning("agent model does not support bind_tools; tool calls unavailable")
        agent_model = agent_base

    agent_node = make_agent_node(settings, agent_model, has_tools=True)

    def _route(state: ChatState) -> Any:
        dest = route_agent(state, settings)
        return END if dest == "end" else dest

    builder = StateGraph(ChatState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", DedupToolNode(tools))

    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", _route, ["tools", "agent", END])
    builder.add_edge("tools", "agent")

    graph = builder.compile()
    log.info(
        "compiled LangGraph agent pipeline (agent=%s, thinking=%s, tools=%d, "
        "max_reflection_rounds=%d, citation_verification=%s)",
        settings.synthesis_model, settings.synthesis_thinking, len(tools),
        settings.max_reflection_rounds, settings.citation_verification,
    )
    return graph, tools


# ---------------------------------------------------------------------------
# Lifecycle: lazy build with a single-flight lock
# ---------------------------------------------------------------------------

_graph: Any = None
_tools: list[Any] | None = None
_build_lock: Any = None


def _get_build_lock() -> Any:
    global _build_lock
    if _build_lock is None:
        _build_lock = asyncio.Lock()
    return _build_lock


async def get_graph() -> tuple[Any, list[Any]]:
    """Get or build the chat graph (lazy initialisation, single-flight)."""
    global _graph, _tools

    if _graph is not None and _tools is not None:
        return _graph, _tools

    lock = _get_build_lock()
    async with lock:
        if _graph is not None and _tools is not None:
            return _graph, _tools

        settings = get_settings()
        _graph, _tools = await build_graph(settings)
        return _graph, _tools


def get_graph_if_ready() -> tuple[Any, list[Any]] | tuple[None, None]:
    """Return the ALREADY-BUILT graph, or ``(None, None)`` — never builds.

    The fast path for ``/chat/health``: it reports the prewarmed graph when
    the host lifespan (or an earlier request) built one, and reports the
    graph as still-initialising otherwise, WITHOUT triggering the
    seconds-long build on a health probe. Building happens only in
    :func:`get_graph` and :func:`build_graph`.
    """
    if _graph is not None and _tools is not None:
        return _graph, _tools
    return None, None


def reset_graph() -> None:
    """Reset the graph state (for testing)."""
    global _graph, _tools
    _graph = None
    _tools = None


async def build_graph_for(settings: Settings) -> tuple[Any, list[Any]]:
    """Build a fresh graph for the given settings (bypassing the cache)."""
    return await build_graph(settings)
