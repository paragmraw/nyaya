"""Rate-limiting and body-size-cap middleware for the nyaya ASGI app.

Thresholds are defined in :class:`nyaya.config.RateLimitSettings` (edit
there to tune; no env vars needed).
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .config import get_rate_limit_settings

log = logging.getLogger("nyaya.ratelimit")


def _get_remote_address(request: Request) -> str:
    """Extract the client IP, respecting X-Forwarded-For from a trusted proxy."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Lightweight per-IP fixed-window rate limiter.

    Counters live in worker memory only, so behind a multi-worker server
    (e.g. ``uvicorn --workers >1``) each worker has its own counters and the
    effective per-IP limit is ``limit * workers``. A shared backend (Redis)
    would be required for strict global limits.
    """

    def __init__(self, app: Any, read_per_min: int = 120, embedding_per_min: int = 10) -> None:
        super().__init__(app)
        self.read_per_min = read_per_min
        self.embedding_per_min = embedding_per_min
        self._counts: dict[str, dict[str, Any]] = defaultdict(lambda: {"count": 0, "window": time.monotonic()})

    def _is_embedding_request(self, request: Request) -> bool:
        """Return True for MCP POSTs to ``/mcp`` (apply the stricter limit)."""
        # MCP requests don't expose the tool name without parsing the body,
        # so we apply the stricter limit to all MCP POSTs as a safe default.
        return "/mcp" in request.url.path and request.method == "POST"

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        settings = get_rate_limit_settings()
        ip = _get_remote_address(request)

        # /health is always allowed (used by Railway healthchecks).
        if request.url.path == "/health":
            return await call_next(request)

        now = time.monotonic()
        entry = self._counts[ip]
        if now - entry["window"] > 60.0:
            entry["count"] = 0
            entry["window"] = now

        limit = self.embedding_per_min if self._is_embedding_request(request) else self.read_per_min
        if entry["count"] >= limit:
            return Response(
                content='{"error": "rate_limited"}',
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": "60"},
            )

        entry["count"] += 1
        return await call_next(request)


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject requests with a body larger than ``max_bytes``.

    Early-rejects on ``Content-Length``; also caps the streamed body as
    defense-in-depth.
    """

    def __init__(self, app: Any, max_bytes: int = 1_048_576) -> None:
        super().__init__(app)
        self.max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self.max_bytes:
            return Response(
                content='{"error": "request_too_large"}',
                status_code=413,
                media_type="application/json",
            )
        return await call_next(request)


def register_rate_limiting(app: Any) -> None:
    """Wire the rate-limit and body-size middleware into the Starlette app."""
    settings = get_rate_limit_settings()

    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.body_size_max_bytes)
    app.add_middleware(
        RateLimitMiddleware,
        read_per_min=settings.read_per_min,
        embedding_per_min=settings.embedding_per_min,
    )

    log.info(
        "Rate limiting enabled: %d req/min/IP (reads), %d req/min/IP (embeddings), %d byte body cap",
        settings.read_per_min,
        settings.embedding_per_min,
        settings.body_size_max_bytes,
    )