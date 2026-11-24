"""Starlette HTTP/ASGI factory wrapping FastMCP v2.0, REST v2 endpoints, security middleware, and optional /chat."""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan as lifespan_decorator
from starlette.datastructures import MutableHeaders
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import __version__
from . import config as _config
from . import rest as _rest
from . import storage as _storage
from .mcp_app import create_mcp
from .ratelimit import (
    BodySizeLimitMiddleware,
    MaxBodySizeMiddleware,
    RateLimitMiddleware,
    register_rate_limiting,
)
from .security_headers import SecurityHeadersMiddleware
from .storage.protocols import CorpusBackend

__all__ = [
    "BodySizeLimitMiddleware",
    "MaxBodySizeMiddleware",
    "RateLimitMiddleware",
    "RequestIdMiddleware",
    "SecurityHeadersMiddleware",
    "_CachedStaticFiles",
    "create_http_app",
]

log = logging.getLogger("nyaya.http")

_ALLOWED_ORIGINS = ["https://nyaya.parag.tech"]


class RequestIdMiddleware:
    """Generate a UUID per request for log correlation and expose it as X-Request-ID (pure ASGI)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request_id = str(uuid.uuid4())
        scope.setdefault("state", {})["request_id"] = request_id

        async def send_with_request_id(message: Message) -> None:
            if message["type"] == "http.response.start":
                MutableHeaders(scope=message)["X-Request-ID"] = request_id
            await send(message)

        await self.app(scope, receive, send_with_request_id)


class _CachedStaticFiles(StaticFiles):
    """StaticFiles with content-addressed caching semantics for Next.js static export."""

    _IMMUTABLE_PREFIX = "_next/static/"
    _IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"
    _NO_CACHE = "no-cache"

    async def get_response(self, path: str, scope: Scope) -> Any:
        response = await super().get_response(path, scope)
        normalized = path.replace("\\", "/").lstrip("/")
        if normalized.startswith(self._IMMUTABLE_PREFIX):
            response.headers["Cache-Control"] = self._IMMUTABLE_CACHE_CONTROL
        else:
            response.headers["Cache-Control"] = self._NO_CACHE
        return response


def _maybe_mount_chat(app: Any) -> None:
    if not os.environ.get("NVIDIA_API_KEY"):
        log.info("NVIDIA_API_KEY not set; chat sub-app not mounted (/chat will 404)")
        return
    try:
        from nyaya_chat.server import create_app as _create_chat_app
    except ImportError:
        log.info("nyaya_chat package not installed; chat sub-app not mounted (/chat will 404)")
        return
    chat_app = _create_chat_app()
    app.mount("/chat", chat_app, name="chat")
    log.info("chat sub-app mounted at /chat")


def create_http_app(
    backend: CorpusBackend | None = None,
    *,
    mcp: FastMCP | None = None,
) -> tuple[FastMCP, Any] | Any:
    """Build the production Starlette ASGI application (and store ``mcp`` on ``app.state.mcp``)."""

    @lifespan_decorator
    async def _http_lifespan(_server: FastMCP) -> AsyncIterator[dict[str, Any]]:
        if backend is not None:
            _storage.set_backend(backend)
        if not os.environ.get("NVIDIA_API_KEY"):
            log.info("NVIDIA_API_KEY not set; chat graph not pre-warmed")
        else:
            try:
                from nyaya_chat.graph import get_graph as _get_chat_graph

                await _get_chat_graph()
                log.info("chat graph pre-warmed at startup")
            except ImportError:
                log.info("nyaya_chat package not installed; chat graph not pre-warmed")
            except Exception:
                log.warning("chat graph pre-warm failed (will retry on first request)", exc_info=True)

        async def _warmup_storage() -> None:
            def _warm() -> None:
                settings = _config.get_settings()
                analytics = _storage.get_analytics()
                if (
                    settings.has_bucket_config
                    and not settings.offline_only
                    and hasattr(analytics, "_connect")
                ):
                    lock = getattr(analytics, "_lock", None)
                    if lock is not None:
                        with lock:
                            analytics._connect(require_httpfs=True)
                    else:
                        analytics._connect(require_httpfs=True)
                _storage.get_retrieval()

            try:
                await asyncio.to_thread(_warm)
                log.info("storage cold-tier warm-up completed")
            except asyncio.CancelledError:
                raise
            except Exception:
                log.warning("storage cold-tier warm-up failed (will retry on first request)", exc_info=True)

        warmup_task = asyncio.create_task(_warmup_storage())
        try:
            yield {}
        finally:
            if not warmup_task.done():
                warmup_task.cancel()
                try:
                    await warmup_task
                except asyncio.CancelledError:
                    pass
                except Exception:
                    pass
            elif not warmup_task.cancelled():
                warmup_task.exception()
            await _storage.aclose_all()

    mcp_instance = mcp or create_mcp(backend=backend, lifespan=_http_lifespan)

    @mcp_instance.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> JSONResponse:
        def _collect_health() -> tuple[dict[str, Any], dict[str, Any]]:
            st = _storage.get_backend().corpus_status()
            if hasattr(st, "counts"):
                enriched = _storage.enrich_corpus_status(st)
                c: dict[str, Any] = (
                    enriched.counts.model_dump(mode="json")
                    if hasattr(enriched.counts, "model_dump")
                    else dict(enriched.counts)
                )
                b: dict[str, Any] = dict(enriched.tiers)
                return c, b
            if isinstance(st, dict):
                c = dict(st.get("counts", st))
                b = dict(st.get("tiers", {"sqlite_core": "ok"}))
                b.setdefault("sqlite_core", "ok")
                for k, v in _storage.get_cold_tier_statuses().items():
                    b.setdefault(k, v)
                return c, b
            return {}, {"sqlite_core": "ok", **_storage.get_cold_tier_statuses()}

        try:
            counts, backends = await asyncio.to_thread(_collect_health)
            status = "healthy"
        except Exception:
            counts = {}
            backends = {"sqlite_core": "unavailable", **_storage.get_cold_tier_statuses()}
            status = "degraded"
        return JSONResponse(
            {
                "status": status,
                "service": "nyaya",
                "version": __version__,
                "backends": backends,
                "counts": counts,
            }
        )

    app = mcp_instance.http_app(stateless_http=True, path="/mcp")

    # Middleware order (innermost -> outermost):
    # BodySizeLimit -> RateLimit -> RequestId -> SecurityHeaders -> CORS
    register_rate_limiting(app)
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_ALLOWED_ORIGINS,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Accept"],
        allow_credentials=False,
    )

    _rest.register(app, mcp_instance)
    _maybe_mount_chat(app)

    web_out = _config.WEB_OUT
    if web_out == "web/out" and not os.path.isdir(web_out) and os.path.isdir("/app/web/out"):
        web_out = "/app/web/out"
    if os.path.isdir(web_out):
        app.mount("/", _CachedStaticFiles(directory=web_out, html=True), name="web")

    return app
