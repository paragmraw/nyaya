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

import json
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .agent import _build_messages, get_agent
from .config import Settings, get_settings
from .guardrail import Intent, classify_intent, get_canned_response
from .llm import get_model
from .schemas import ChatRequest, ChatSubHealthResponse
from .streaming import stream_turn

log = logging.getLogger("nyaya_chat")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    s = get_settings()
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
        version="0.1.0",
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
            try:
                graph, tools = await get_agent()
                app.state.graph = graph
                app.state.tools = tools
            except Exception:
                log.exception("failed to build chat agent for health check")
                graph, tools = None, []

        # Degraded if: no graph, OR graph built but zero tools loaded
        is_degraded = graph is None or len(tools) == 0
        return ChatSubHealthResponse(
            status="degraded" if is_degraded else "healthy",
            model=s.llm_model,
            tools_loaded=len(tools),
        )

    @app.post("/turn")
    async def turn(req: ChatRequest, request: Request) -> Any:
        graph = getattr(app.state, "graph", None)
        tools = getattr(app.state, "tools", None) or []
        if graph is None:
            try:
                graph, tools = await get_agent()
                app.state.graph = graph
                app.state.tools = tools
            except Exception:
                log.exception("failed to build chat agent for turn")
                return JSONResponse(
                    {"error": "agent_unavailable", "detail": "chat agent temporarily unavailable"},
                    status_code=503,
                )

        if graph is None:
            return JSONResponse(
                {"error": "agent_unavailable", "detail": "chat agent not available"},
                status_code=503,
            )

        # Use the host's request ID if available; generate one as fallback.
        rid = getattr(getattr(request, "state", None), "request_id", None) or uuid.uuid4().hex
        history = [t.model_dump() for t in req.history][-s.max_history:]
        log.info("chat turn request_id=%s msg_len=%d history=%d", rid, len(req.message), len(history))

        # Guardrail: classify intent before entering the agent pipeline.
        # Non-legal messages (greetings, capability questions, off-topic) get
        # a canned SSE response instantly -- no supervisor/tool/synthesis calls.
        intent = await classify_intent(req.message, get_model(s), s)
        if intent != Intent.LEGAL:
            log.info("guardrail: fast-path for intent=%s (skipping agent pipeline)", intent.value)
            canned = get_canned_response(intent)

            async def fast_path() -> AsyncIterator[bytes]:
                yield _sse_meta(rid)
                yield _sse_status(rid)
                yield _sse_composing()
                yield _sse_token(canned)
                yield _sse_done()

            return StreamingResponse(
                fast_path(),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "Connection": "keep-alive",
                    "X-Request-ID": rid,
                },
            )

        # Normal pipeline: legal question goes through the full agent graph.
        messages = _build_messages(req.message, history)
        keepalive_interval = s.sse_keepalive_interval_s

        async def event_source() -> AsyncIterator[bytes]:
            yield _sse_meta(rid)
            yield _sse_status(rid)
            async for chunk in stream_turn(graph, messages, keepalive_interval=keepalive_interval):
                yield chunk

        return StreamingResponse(
            event_source(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
                "X-Request-ID": rid,
            },
        )

    @app.get("/")
    async def root() -> dict[str, str]:
        return {"service": "nyaya-chat", "version": "0.1.0", "turn": "POST /chat/turn", "health": "GET /chat/health"}

    return app


def _sse_meta(rid: str) -> bytes:
    return f'event: meta\ndata: {json.dumps({"request_id": rid})}\n\n'.encode()


def _sse_status(rid: str) -> bytes:
    return f'event: status\ndata: {json.dumps({"msg": "analyzing", "rid": rid})}\n\n'.encode()


def _sse_composing() -> bytes:
    return f'event: status\ndata: {json.dumps({"msg": "composing"})}\n\n'.encode()


def _sse_token(content: str) -> bytes:
    return f'event: token\ndata: {json.dumps({"content": content}, ensure_ascii=False)}\n\n'.encode()


def _sse_done() -> bytes:
    return b'event: done\ndata: {}\n\n'
