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

from .agent import _build_messages, build_agent
from .config import Settings, get_settings
from .schemas import ChatRequest, ChatSubHealthResponse
from .streaming import stream_turn

log = logging.getLogger("nyaya_chat")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
    log.info("nyaya-chat sub-app lifespan: %s", s.as_log_dict())
    # If the host already built the agent (app.state.graph set before mount),
    # skip the async build — mounted sub-app lifespans don't run under
    # Starlette, so the host must inject state.
    if getattr(app.state, "graph", None) is not None or getattr(app.state, "tools", None):
        log.info("chat agent already built by host; skipping lifespan build")
        try:
            yield
        finally:
            log.info("nyaya-chat sub-app shutting down")
        return
    # Otherwise (standalone run), build here.
    try:
        graph, tools = await build_agent(s)
    except Exception:  # noqa: BLE001 — degrade instead of crashing on startup.
        log.exception("failed to build chat agent at startup; /chat/health will report degraded")
        graph, tools = None, []
    app.state.graph = graph
    app.state.tools = tools
    app.state.settings = s
    try:
        yield
    finally:
        log.info("nyaya-chat sub-app shutting down")


def create_app(settings: Settings | None = None, *, graph: Any = None, tools: list | None = None) -> FastAPI:
    """Build the chat FastAPI sub-app.

    ``settings`` is optional; when ``None`` the process-wide ``get_settings()``
    is used. ``graph``/``tools`` allow the host to inject a pre-built agent
    (built in the host's lifespan, since mounted sub-app lifespans don't run
    under Starlette). When ``None``, the sub-app builds the agent in its own
    lifespan (used by standalone runs and the chat package's own tests).
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
    # If the host pre-builds the agent, stash it so the lifespan skips the build.
    if graph is not None or tools is not None:
        app.state.graph = graph
        app.state.tools = tools or []

    @app.get("/health", response_model=ChatSubHealthResponse)
    async def health() -> ChatSubHealthResponse:
        tools = getattr(app.state, "tools", []) or []
        graph = getattr(app.state, "graph", None)
        return ChatSubHealthResponse(
            status="healthy" if graph is not None else "degraded",
            model=s.llm_model,
            tools_loaded=len(tools),
        )

    @app.post("/turn")
    async def turn(req: ChatRequest) -> Any:
        graph = getattr(app.state, "graph", None)
        if graph is None:
            return JSONResponse(
                {"error": "agent_unavailable", "detail": "chat agent not built at startup"},
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
    return f"event: status\ndata: {json.dumps({'msg': 'thinking', 'rid': rid})}\n\n".encode()
