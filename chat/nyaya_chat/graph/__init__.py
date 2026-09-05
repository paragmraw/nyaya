"""Graph assembly: supervisor → parallel tools → synthesis, with reflection.

This module builds the compiled ``StateGraph`` and holds its module-level
lifecycle state (:func:`get_graph` / :func:`get_graph_if_ready` /
:func:`reset_graph`), replacing ``agent.py``.

**No checkpointer.** The client supplies the conversation history on every
turn (``ChatRequest.history``) and each turn is a bounded run of at most
``MAX_REFLECTION_ROUNDS`` supervisor/synthesis cycles; persisting state
across turns would buy server-side memory we have not productized while
adding an AsyncPostgresSaver dependency to every request. Revisit only if
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
from ..schemas_llm import ToolPlan
from ..tools_layer import load_tools
from .state import ChatState
from .supervisor import (
    make_supervisor_node,
    route_supervisor,  # re-exported for tests
)
from .synthesis import make_synthesis_node, route_synthesis
from .tools_node import DedupToolNode

log = logging.getLogger("nyaya_chat.graph")


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
    """Connect to tools and compile the supervisor-tools-synthesis graph.

    Returns ``(compiled_graph, tools)``. With zero tools loaded, a degraded
    single-node graph is returned (streams an answer directly, no retrieval,
    no verification) so the endpoint degrades instead of failing.
    """
    tools = await load_tools(settings)
    if not tools:
        log.warning("no tools loaded, building degraded graph")
        model = get_model(settings)
        builder: StateGraph = StateGraph(ChatState)
        builder.add_node(_DEGRADED_NODE_NAME, make_synthesis_node(settings, model, has_tools=False))
        builder.add_edge(START, _DEGRADED_NODE_NAME)
        builder.add_edge(_DEGRADED_NODE_NAME, END)
        return builder.compile(), []

    log.info("loaded %d tools", len(tools))

    # Supervisor model: try with_structured_output(ToolPlan) first; if the
    # API doesn't support it at invoke time, the supervisor node falls back
    # to the bind_tools model on its first failure (pre-built here so the
    # fallback is instant).
    supervisor_base = _make_model(
        settings,
        model_name=settings.supervisor_model,
        max_tokens=settings.supervisor_max_tokens,
        temperature=settings.supervisor_temperature,
    )

    # Disable thinking mode so the model focuses on tool calling instead of
    # reasoning.
    if hasattr(supervisor_base, "with_thinking_mode"):
        try:
            supervisor_base = supervisor_base.with_thinking_mode(enabled=False)
            log.info("supervisor: thinking mode disabled")
        except Exception:
            log.warning("could not disable thinking mode for supervisor")

    supervisor_structured = None
    supervisor_bind_tools = None
    if hasattr(supervisor_base, "with_structured_output"):
        try:
            supervisor_structured = supervisor_base.with_structured_output(ToolPlan)
            log.info("supervisor: with_structured_output(ToolPlan) available")
        except Exception:
            pass
    if hasattr(supervisor_base, "bind_tools"):
        supervisor_bind_tools = supervisor_base.bind_tools(tools)
        log.info("supervisor: bind_tools available as fallback")

    supervisor_model = supervisor_structured or supervisor_bind_tools or supervisor_base

    synthesis_model = _make_model(
        settings,
        model_name=settings.synthesis_model,
        max_tokens=settings.synthesis_max_tokens,
    )

    supervisor_node = make_supervisor_node(settings, supervisor_model, supervisor_bind_tools)
    synthesis_node = make_synthesis_node(settings, synthesis_model, has_tools=True)

    builder = StateGraph(ChatState)
    builder.add_node("supervisor", supervisor_node)
    builder.add_node("tools", DedupToolNode(tools))
    builder.add_node(_SYNTHESIS_NODE_NAME, synthesis_node)

    builder.add_edge(START, "supervisor")
    builder.add_conditional_edges(
        "supervisor", route_supervisor, ["tools", _SYNTHESIS_NODE_NAME],
    )
    builder.add_edge("tools", _SYNTHESIS_NODE_NAME)
    builder.add_conditional_edges(
        _SYNTHESIS_NODE_NAME,
        # route_synthesis speaks "end"/"supervisor"; LangGraph's end sentinel
        # is the END constant, so translate here.
        lambda state: (
            "supervisor" if route_synthesis(state, settings) == "supervisor" else END
        ),
        ["supervisor", END],
    )

    graph = builder.compile()
    log.info(
        "compiled LangGraph supervisor-tools-synthesis graph with reflection "
        "(supervisor=%s, synthesis=%s, tools=%d, max_rounds=%d, citation_verification=%s)",
        settings.supervisor_model, settings.synthesis_model, len(tools),
        settings.max_reflection_rounds, settings.citation_verification,
    )
    return graph, tools


# Node-name constants. The streamer no longer keys on them (nodes emit their
# own events); they remain for tests, logging, and the degraded-graph wiring.
_SYNTHESIS_NODE_NAME = "synthesis"
_DEGRADED_NODE_NAME = "degraded_synthesis"

# Public aliases (kept for tests that import the constants).
SYNTHESIS_NODE_NAME = _SYNTHESIS_NODE_NAME
DEGRADED_NODE_NAME = _DEGRADED_NODE_NAME


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
    """Build a fresh graph for the given settings (bypassing the cache).

    Backward-compatible entry point mirroring the old ``build_agent``.
    """
    return await build_graph(settings)
