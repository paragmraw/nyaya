"""FastAPI sub-app for the nyaya chat feature.

Mounted into the existing nyaya MCP/Starlette server at ``/chat`` (see
``mcp/nyaya/server.py``) so the SPA, REST API, MCP endpoint, and chat share
one origin, one healthcheck, and one Railway service. The sub-app owns only
the chat-specific concerns: the LangGraph agent, the NVIDIA model, and the
SSE stream encoder. Cross-cutting middleware (CORS, security headers,
request-id, top-level rate limiting, body-size cap) is provided by the host
Starlette app, so this sub-app deliberately omits them.

Routes
------
* ``POST /chat/turn`` — stream a chat turn as Server-Sent Events.
* ``GET  /chat/health`` — sub-app health (cheap; does not call NVIDIA).
* ``GET  /chat/`` — small info payload.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from . import __version__
from .agent import _build_messages, get_agent, get_agent_if_ready
from .config import Settings, get_settings
from .guardrail import Intent, classify_intent, get_canned_response
from .observability import configure_structlog
from .schemas import ChatRequest, ChatSubHealthResponse
from .streaming import _sse, stream_turn

log = logging.getLogger("nyaya_chat")

# Headers shared by every SSE StreamingResponse. The per-request
# ``X-Request-ID`` is added at response time.
_SSE_HEADERS: dict[str, str] = {
    "Cache-Control": "no-cache",
    "X-Accel-Buffering": "no",
    "Connection": "keep-alive",
}


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    # One-line wiring so structlog (structured JSON logging) is actually
    # configured in the running sub-app instead of being a dead dependency.
    configure_structlog(s.log_level)
    log.info("nyaya-chat sub-app lifespan starting: model=%s mcp_url=%s", s.llm_model, s.mcp_url)
    app.state.settings = s
    try:
        yield
    finally:
        log.info("nyaya-chat sub-app shutting down")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the chat FastAPI sub-app."""
    s = settings or get_settings()
    app = FastAPI(
        title="nyaya-chat",
        version=__version__,
        description="LangGraph + NVIDIA Nemotron chat backend (sub-app of nyaya).",
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.state.settings = s

    @app.get("/health", response_model=ChatSubHealthResponse)
    async def health() -> ChatSubHealthResponse:
        graph = getattr(app.state, "graph", None)
        tools = getattr(app.state, "tools", None) or []
        if graph is None:
            # Fast path: NEVER build the agent on a health probe. The old
            # behaviour paid the full tool-loading + graph-compilation cost on
            # a cold health check (which is also what load balancers hit).
            # Report the prewarmed graph if the host lifespan (or an earlier
            # request) built one; otherwise return the degraded payload
            # immediately — the build happens on the first /turn instead.
            # ``get_agent_if_ready`` only reads already-built module state, so
            # this is O(1).
            graph, tools = get_agent_if_ready()
            if graph is not None:
                app.state.graph = graph
                app.state.tools = tools

        # Degraded if: agent not built yet, OR graph built but zero tools
        # loaded. The model field is always set (ChatPanel.tsx reads it for
        # the model badge).
        if graph is None:
            return ChatSubHealthResponse(
                status="degraded",
                model=s.llm_model,
                tools_loaded=0,
                reason="chat agent is still initializing (built at startup or on first /turn)",
            )
        if len(tools) == 0:
            return ChatSubHealthResponse(
                status="degraded",
                model=s.llm_model,
                tools_loaded=0,
                reason="graph built but no corpus tools loaded",
            )
        return ChatSubHealthResponse(
            status="healthy", model=s.llm_model, tools_loaded=len(tools),
        )

    @app.post("/turn")
    async def turn(req: ChatRequest, request: Request) -> Any:
        # Use the host's request ID if available; generate one as fallback.
        rid = getattr(getattr(request, "state", None), "request_id", None) or uuid.uuid4().hex

        graph = getattr(app.state, "graph", None)
        tools = getattr(app.state, "tools", None) or []
        if graph is None:
            try:
                graph, tools = await get_agent()
                app.state.graph = graph
                app.state.tools = tools
            except Exception:
                log.exception("failed to build chat agent for turn")
                return _agent_unavailable(rid, "chat agent temporarily unavailable")

        if graph is None:
            return _agent_unavailable(rid, "chat agent not available")

        history = [t.model_dump() for t in req.history][-s.max_history:]
        log.info("chat turn request_id=%s msg_len=%d history=%d", rid, len(req.message), len(history))

        # Guardrail: classify intent before entering the agent pipeline.
        # Non-legal messages (greetings, capability questions, off-topic) get
        # a canned SSE response instantly -- no supervisor/tool/synthesis calls.
        intent = await classify_intent(req.message, s)
        if intent != Intent.LEGAL:
            log.info("guardrail: fast-path for intent=%s (skipping agent pipeline)", intent.value)
            canned = get_canned_response(intent)

            async def fast_path() -> AsyncIterator[bytes]:
                yield _sse("meta", {"request_id": rid})
                yield _sse("status", {"msg": "analyzing", "rid": rid})
                yield _sse("status", {"msg": "composing", "rid": rid})
                yield _sse("token", {"content": canned})
                yield _sse("done", {})

            return StreamingResponse(
                fast_path(),
                media_type="text/event-stream",
                headers={**_SSE_HEADERS, "X-Request-ID": rid},
            )

        # Normal pipeline: legal question goes through the full agent graph.
        messages = _build_messages(req.message, history)
        keepalive_interval = s.sse_keepalive_interval_s

        async def event_source() -> AsyncIterator[bytes]:
            yield _sse("meta", {"request_id": rid})
            yield _sse("status", {"msg": "analyzing", "rid": rid})
            async for chunk in stream_turn(
                graph, messages, keepalive_interval=keepalive_interval, rid=rid,
            ):
                yield chunk

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={**_SSE_HEADERS, "X-Request-ID": rid},
        )

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"service": "nyaya-chat", "version": __version__, "turn": "POST /chat/turn", "health": "GET /chat/health"}

    return app


def _agent_unavailable(rid: str, detail: str) -> JSONResponse:
    """503 JSON body in the unified error shape ``{message, detail, rid}``."""
    return JSONResponse(
        {"message": "agent_unavailable", "detail": detail, "rid": rid},
        status_code=503,
    )
