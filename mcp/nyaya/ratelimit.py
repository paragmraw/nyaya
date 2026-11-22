"""Rate-limiting and streaming body-size-cap middleware for the Nyaya v2.0 ASGI app.

Hardened against:
1. Spoofed left-most ``X-Forwarded-For`` headers (parses right-to-left using
   ``trusted_proxy_hops``, defaulting to 1 for Railway edge proxy).
2. Unbounded in-memory IP counter growth (``InMemoryBackend`` uses an LRU
   ``OrderedDict`` capped at ``max_keys`` with expired-window eviction).
3. Malformed/negative ``Content-Length`` headers (returns ``400`` instead of ``500``).
4. Chunked-transfer body-size bypasses (``BodySizeLimitMiddleware`` counts
   streamed ``http.request`` bytes even when ``Content-Length`` is omitted).
"""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from collections import OrderedDict
from typing import Any, NamedTuple, Protocol

from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .config import _redact_url, get_rate_limit_settings, get_settings

log = logging.getLogger("nyaya.ratelimit")

_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})
DEFAULT_MAX_KEYS = 50_000


def _get_remote_address(
    request: Request,
    *,
    trusted_proxy_hops: int | None = None,
) -> str:
    """Extract the client IP using right-most trusted proxy hop from X-Forwarded-For."""
    hops = (
        get_rate_limit_settings().trusted_proxy_hops
        if trusted_proxy_hops is None
        else max(0, int(trusted_proxy_hops))
    )
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded and hops > 0:
        parts = [p.strip() for p in forwarded.split(",") if p.strip()]
        if parts:
            idx = max(0, len(parts) - hops)
            return parts[idx]
    return request.client.host if request.client else "unknown"


class RateLimitDecision(NamedTuple):
    """Outcome of a rate-limit check."""

    limited: bool
    retry_after_s: float


class RateLimitBackend(Protocol):
    """Abstract rate-limit counter backend."""

    needs_thread: bool

    def is_limited(self, key: str, limit: int, window_seconds: float = 60.0) -> bool: ...

    def check(self, key: str, limit: int, window_seconds: float = 60.0) -> RateLimitDecision: ...


class InMemoryBackend:
    """Per-worker in-memory fixed-window counter with bounded LRU eviction."""

    needs_thread = False

    def __init__(self, max_keys: int = DEFAULT_MAX_KEYS) -> None:
        self.max_keys = max(1, int(max_keys))
        self._lock = threading.Lock()
        self._counts: OrderedDict[str, dict[str, Any]] = OrderedDict()

    def _evict_if_needed(self, now: float, window_seconds: float) -> None:
        if len(self._counts) < self.max_keys:
            return
        # First pass: drop expired windows from oldest side
        expired_keys: list[str] = []
        for k, v in self._counts.items():
            if now - float(v["window"]) > window_seconds:
                expired_keys.append(k)
            if len(self._counts) - len(expired_keys) < self.max_keys:
                break
        for k in expired_keys:
            self._counts.pop(k, None)
        # Second pass: strict LRU eviction of oldest keys
        while len(self._counts) >= self.max_keys:
            self._counts.popitem(last=False)

    def _check_inner(
        self,
        key: str,
        limit: int,
        window_seconds: float,
    ) -> RateLimitDecision:
        now = time.time()
        with self._lock:
            entry = self._counts.get(key)
            if entry is None:
                self._evict_if_needed(now, window_seconds)
                entry = {"count": 0, "window": now}
                self._counts[key] = entry
            else:
                self._counts.move_to_end(key)
                if now - float(entry["window"]) > window_seconds:
                    entry["count"] = 0
                    entry["window"] = now

            if int(entry["count"]) >= limit:
                retry_after = max(0.0, float(entry["window"]) + window_seconds - now)
                return RateLimitDecision(True, retry_after)
            entry["count"] = int(entry["count"]) + 1
            return RateLimitDecision(False, 0.0)

    def is_limited(self, key: str, limit: int, window_seconds: float = 60.0) -> bool:
        return self._check_inner(key, limit, window_seconds).limited

    def check(self, key: str, limit: int, window_seconds: float = 60.0) -> RateLimitDecision:
        return self._check_inner(key, limit, window_seconds)


class RedisBackend:
    """Redis-backed fixed-window counter (strict global limits across workers)."""

    needs_thread = True

    def __init__(self, redis_url: str) -> None:
        import redis  # type: ignore[import-untyped]

        self._redis = redis.from_url(redis_url, decode_responses=True)

    def _check_inner(self, key: str, limit: int, window_seconds: float) -> RateLimitDecision:
        now = int(time.time())
        win_s = max(1, int(window_seconds))
        window_index = now // win_s
        window_start = window_index * win_s
        window_key = f"rl:{key}:{window_index}"
        pipe = self._redis.pipeline()
        pipe.incr(window_key)
        pipe.expire(window_key, win_s + 1)
        count, _ = pipe.execute()
        if int(count) > limit:
            retry_after = max(0.0, window_start + win_s - now)
            return RateLimitDecision(True, retry_after)
        return RateLimitDecision(False, 0.0)

    def is_limited(self, key: str, limit: int, window_seconds: float = 60.0) -> bool:
        return self._check_inner(key, limit, window_seconds).limited

    def check(self, key: str, limit: int, window_seconds: float = 60.0) -> RateLimitDecision:
        return self._check_inner(key, limit, window_seconds)


def _create_backend() -> RateLimitBackend:
    """Create a rate-limit backend based on settings."""
    settings = get_settings()
    if settings.redis_url:
        try:
            backend = RedisBackend(settings.redis_url)
            log.info("Rate limiting: Redis backend enabled (%s)", _redact_url(settings.redis_url))
            return backend
        except ImportError:
            log.warning(
                "REDIS_URL is set but the 'redis' package is not installed. "
                "Falling back to in-memory rate limiting."
            )
    return InMemoryBackend()


class RateLimitMiddleware:
    """Per-IP fixed-window rate limiter with pluggable backend (pure ASGI)."""

    def __init__(
        self,
        app: ASGIApp,
        read_per_min: int = 120,
        mcp_per_min: int | None = None,
        embedding_per_min: int = 30,
        analytics_per_min: int = 20,
        chat_per_min: int = 15,
        trusted_proxy_hops: int | None = None,
        backend: RateLimitBackend | None = None,
    ) -> None:
        self.app = app
        self.read_per_min = read_per_min
        self.mcp_per_min = mcp_per_min if mcp_per_min is not None else embedding_per_min
        self.embedding_per_min = self.mcp_per_min
        self.analytics_per_min = analytics_per_min
        self.chat_per_min = chat_per_min
        self.trusted_proxy_hops = trusted_proxy_hops
        self._backend = backend or InMemoryBackend()
        self._fallback = InMemoryBackend()
        self._redis_failed = False

    def _is_mcp_request(self, request: Request) -> bool:
        return "/mcp" in request.url.path and request.method == "POST"

    def _is_analytics_request(self, request: Request) -> bool:
        return request.url.path.startswith("/api/analytics")

    def _is_chat_request(self, request: Request) -> bool:
        return request.url.path.startswith("/chat") and request.method == "POST"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        request = Request(scope)
        ip = _get_remote_address(request, trusted_proxy_hops=self.trusted_proxy_hops)

        if request.url.path == "/health":
            await self.app(scope, receive, send)
            return

        if not request.headers.get("x-forwarded-for") and ip in _LOOPBACK:
            await self.app(scope, receive, send)
            return

        if self._is_chat_request(request):
            bucket = "chat"
            limit = self.chat_per_min
        elif self._is_analytics_request(request):
            bucket = "analytics"
            limit = self.analytics_per_min
        elif self._is_mcp_request(request):
            bucket = "mcp"
            limit = self.mcp_per_min
        else:
            bucket = "read"
            limit = self.read_per_min

        key = f"{ip}:{bucket}"
        backend = self._fallback if self._redis_failed else self._backend
        try:
            decision: RateLimitDecision
            if backend.needs_thread:
                decision = await asyncio.to_thread(backend.check, key, limit)
            else:
                decision = backend.check(key, limit)
        except Exception:
            if not self._redis_failed:
                log.warning(
                    "Rate-limit backend failed (likely Redis unreachable). "
                    "Falling back to in-memory rate limiting.",
                    exc_info=True,
                )
                self._redis_failed = True
            decision = self._fallback.check(key, limit)

        if decision.limited:
            response = Response(
                content=(
                    '{"message": "rate_limited", '
                    '"detail": "too many requests; retry after the interval in Retry-After"}'
                ),
                status_code=429,
                media_type="application/json",
                headers={"Retry-After": str(max(1, math.ceil(decision.retry_after_s)))},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


class _BodyTooLargeError(Exception):
    """Internal sentinel raised when streamed request body exceeds max_bytes."""


class BodySizeLimitMiddleware:
    """Reject requests with a body larger than ``max_bytes`` (pure ASGI).

    1. Validates ``Content-Length`` if present (non-numeric or negative -> 400;
       above ``max_bytes`` -> 413).
    2. Wraps ``receive`` to count streamed ``http.request`` bytes so chunked
       requests without ``Content-Length`` cannot bypass the cap.
    """

    def __init__(self, app: ASGIApp, max_bytes: int = 1_048_576) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        content_length = next(
            (
                v.decode("latin-1")
                for k, v in (scope.get("headers") or [])
                if k.lower() == b"content-length"
            ),
            None,
        )
        if content_length is not None:
            try:
                parsed_len = int(content_length.strip())
            except ValueError:
                bad_resp = Response(
                    content=(
                        '{"message": "invalid_content_length", '
                        '"detail": "Content-Length must be a non-negative integer"}'
                    ),
                    status_code=400,
                    media_type="application/json",
                )
                await bad_resp(scope, receive, send)
                return
            if parsed_len < 0:
                bad_resp = Response(
                    content=(
                        '{"message": "invalid_content_length", '
                        '"detail": "Content-Length must be a non-negative integer"}'
                    ),
                    status_code=400,
                    media_type="application/json",
                )
                await bad_resp(scope, receive, send)
                return
            if parsed_len > self.max_bytes:
                too_large = Response(
                    content=(
                        '{"message": "request_too_large", '
                        '"detail": "request body exceeds the size limit"}'
                    ),
                    status_code=413,
                    media_type="application/json",
                )
                await too_large(scope, receive, send)
                return

        seen_bytes = 0
        response_started = False

        async def counting_receive() -> Message:
            nonlocal seen_bytes
            message = await receive()
            if message["type"] == "http.request":
                chunk = message.get("body", b"") or b""
                seen_bytes += len(chunk)
                if seen_bytes > self.max_bytes:
                    raise _BodyTooLargeError()
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, counting_receive, tracking_send)
        except _BodyTooLargeError:
            if not response_started:
                too_large = Response(
                    content=(
                        '{"message": "request_too_large", '
                        '"detail": "request body exceeds the size limit"}'
                    ),
                    status_code=413,
                    media_type="application/json",
                )
                await too_large(scope, receive, send)


MaxBodySizeMiddleware = BodySizeLimitMiddleware


def register_rate_limiting(app: Any) -> None:
    """Wire the rate-limit and body-size middleware into the Starlette app."""
    settings = get_rate_limit_settings()
    backend = _create_backend()

    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.body_size_max_bytes)
    app.add_middleware(
        RateLimitMiddleware,
        read_per_min=settings.read_per_min,
        mcp_per_min=settings.mcp_per_min,
        analytics_per_min=settings.analytics_per_min,
        chat_per_min=settings.chat_per_min,
        trusted_proxy_hops=settings.trusted_proxy_hops,
        backend=backend,
    )

    log.info(
        "Rate limiting enabled: %d req/min/IP (reads), %d req/min/IP (MCP), "
        "%d req/min/IP (analytics), %d req/min/IP (chat), %d byte body cap",
        settings.read_per_min,
        settings.mcp_per_min,
        settings.analytics_per_min,
        settings.chat_per_min,
        settings.body_size_max_bytes,
    )
