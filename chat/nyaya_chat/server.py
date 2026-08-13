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

from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse

from .agent import _build_messages, get_agent
from .config import Settings, get_settings
from .schemas import ChatRequest, ChatSubHealthResponse
from .streaming import stream_turn

log = logging.getLogger("nyaya_chat")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Sub-app lifespan: no agent building here (lazy init on first request).
    # Host lifespan handles agent building and injection.
    s = get_settings()
    log.info("nyaya-chat sub-app lifespan starting: %s", s.as_log_dict())
    app.state.settings = s
    try:
        yield
    finally:
        log.info("nyaya-chat sub-app shutting down")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the chat FastAPI sub-app.

    ``settings`` is optional; when ``None`` the process-wide ``get_settings()``
    is used.
    """
    s = settings or get_settings()
    app = FastAPI(
        title="nyaya-chat",
        version="0.1.0",
        description="LangGraph + NVIDIA Nemotron chat backend (sub-app of nyaya).",
        lifespan=lifespan,
        # Serve docs at /chat/docs so they don't clash with the host's /docs.
        docs_url="/docs",
        openapi_url="/openapi.json",
    )
    app.state.settings = s

    @app.get("/health", response_model=ChatSubHealthResponse)
    async def health() -> ChatSubHealthResponse:
        graph = getattr(app.state, "graph", None)
        tools = getattr(app.state, "tools", None) or []
        # If graph not yet built, check if we can build it (lazy init)
        if graph is None:
            try:
                graph, tools = await get_agent()
                app.state.graph = graph
                app.state.tools = tools
            except Exception:
                log.exception("failed to build chat agent for health check")
                graph, tools = None, []

        return ChatSubHealthResponse(
            status="healthy" if graph is not None else "degraded",
            model=s.llm_model,
            tools_loaded=len(tools),
        )

    @app.post("/turn")
    async def turn(req: ChatRequest) -> Any:
        graph = getattr(app.state, "graph", None)
        tools = getattr(app.state, "tools", None) or []
        if graph is None:
            try:
                graph, tools = await get_agent()
                app.state.graph = graph
                app.state.tools = tools
            except Exception as e:
                log.exception("failed to build chat agent for turn")
                return JSONResponse(
                    {"error": "agent_unavailable", "detail": str(e)},
                    status_code=503,
                )

        # After attempting to build, check if agent is available
        if graph is None:
            return JSONResponse(
                {"error": "agent_unavailable", "detail": "chat agent not available"},
                status_code=503,
            )

        # Cap history server-side.
        history = [t.model_dump() for t in req.history][-s.max_history:]
        messages = _build_messages(req.message, history)
        rid = uuid.uuid4().hex
        log.info("chat turn request_id=%s msg_len=%d history=%d", rid, len(req.message), len(history))

        async def event_source() -> AsyncIterator[bytes]:
            # Immediate status so the client knows the stream is live.
            yield _sse_status(rid)
            async for chunk in stream_turn(graph, messages):
                yield chunk

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",  # nginx: don't buffer SSE
                "Connection": "keep-alive",
            },
        )

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"service": "nyaya-chat", "version": "0.1.0", "turn": "POST /chat/turn", "health": "GET /chat/health"}

    return app


def _sse_status(rid: str) -> bytes:
    import json
    return f'event: status\ndata: {json.dumps({"msg": "analyzing", "rid": rid})}\n\n'.encode()
