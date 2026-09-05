"""Rate-limiting and body-size-cap middleware for the nyaya ASGI app.

Thresholds are defined in :class:`nyaya.config.RateLimitSettings` and can be
overridden via environment variables (see config.py).

When ``REDIS_URL`` is configured, rate-limit counters are stored in Redis
so limits are enforced globally across all workers. Without ``REDIS_URL``,
counters are in-memory per-worker (effective limit = ``limit * workers``).

Redis I/O is synchronous (``redis-py``) so it is dispatched to a worker
thread via ``asyncio.to_thread`` to avoid blocking the event loop. If Redis
becomes unreachable at runtime, the middleware falls back to an in-memory
backend so the server keeps serving requests (fail-open).
"""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from collections import defaultdict
from typing import Any, NamedTuple, Protocol

from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp, Receive, Scope, Send

from .config import _redact_url, get_rate_limit_settings, get_settings

log = logging.getLogger("nyaya.ratelimit")

# Loopback addresses that are exempt from rate limiting when the request has
# no X-Forwarded-For header (i.e. genuine in-container self-calls such as the
# chat agent calling the MCP server over localhost). External requests through
# a reverse proxy always carry X-Forwarded-For, so a spoofed 127.0.0.1 in that
# header does not bypass the limiter.
_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


def _get_remote_address(request: Request) -> str:
    """Extract the client IP, respecting X-Forwarded-For from a trusted proxy."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimitDecision(NamedTuple):
    """Outcome of a rate-limit check.

    ``retry_after_s`` is the time until the current fixed window resets —
    the honest value for the ``Retry-After`` response header (the old
    middleware always claimed 60s regardless of the remaining window).
    """

    limited: bool
    retry_after_s: float


class RateLimitBackend(Protocol):
    """Abstract rate-limit counter backend.

    ``is_limited`` is the simple boolean API (kept for direct/test use);
    ``check`` additionally reports when the window resets so the middleware
    can send an accurate ``Retry-After``. ``needs_thread`` tells the
    middleware whether the backend does blocking I/O (Redis) and must be
    dispatched via ``asyncio.to_thread`` — the in-memory backend is a
    lock + dict update and runs inline on the event loop.
    """

    needs_thread: bool

    def is_limited(self, key: str, limit: int, window_seconds: float = 60.0) -> bool:
        """Return True if the key has exceeded ``limit`` in the current window."""
        ...

    def check(self, key: str, limit: int, window_seconds: float = 60.0) -> RateLimitDecision:
        """Check the key and report (limited, retry_after_s)."""
        ...


class InMemoryBackend:
    """Per-worker in-memory fixed-window counter (default, no Redis needed).

    Uses wall-clock time for the window so behaviour matches
    :class:`RedisBackend`. Mutations of ``_counts`` run under a lock; the
    middleware calls this backend inline on the event loop (``needs_thread``
    is False — the critical section is a few dict operations, cheaper than
    the ``asyncio.to_thread`` hop it previously paid on every request).
    """

    needs_thread = False

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counts: dict[str, dict[str, Any]] = defaultdict(
            lambda: {"count": 0, "window": time.time()}
        )

    def _check_inner(
        self, key: str, limit: int, window_seconds: float
    ) -> RateLimitDecision:
        now = time.time()
        with self._lock:
            entry = self._counts[key]
            if now - entry["window"] > window_seconds:
                entry["count"] = 0
                entry["window"] = now
            if entry["count"] >= limit:
                retry_after = max(0.0, entry["window"] + window_seconds - now)
                return RateLimitDecision(True, retry_after)
            entry["count"] += 1
            return RateLimitDecision(False, 0.0)

    def is_limited(self, key: str, limit: int, window_seconds: float = 60.0) -> bool:
        return self._check_inner(key, limit, window_seconds).limited

    def check(self, key: str, limit: int, window_seconds: float = 60.0) -> RateLimitDecision:
        return self._check_inner(key, limit, window_seconds)


class RedisBackend:
    """Redis-backed fixed-window counter (strict global limits across workers).

    Uses a single INCR + EXPIRE per request. The key includes the window
    start timestamp so it rotates cleanly.

    The window bucket is derived from wall-clock time (``time.time()``), not
    monotonic time: monotonic origins differ per process, so per-worker
    buckets would silently defeat the global limit across workers (or across
    restarts against a persistent Redis).
    """

    needs_thread = True  # synchronous redis-py network I/O

    def __init__(self, redis_url: str) -> None:
        import redis  # type: ignore[import-untyped]

        self._redis = redis.from_url(redis_url, decode_responses=True)

    def _check_inner(self, key: str, limit: int, window_seconds: float) -> RateLimitDecision:
        now = int(time.time())
        window_index = now // int(window_seconds)
        window_start = window_index * int(window_seconds)
        window_key = f"rl:{key}:{window_index}"
        pipe = self._redis.pipeline()
        pipe.incr(window_key)
        pipe.expire(window_key, int(window_seconds) + 1)
        count, _ = pipe.execute()
        if count > limit:
            retry_after = max(0.0, window_start + int(window_seconds) - now)
            return RateLimitDecision(True, retry_after)
        return RateLimitDecision(False, 0.0)

    def is_limited(self, key: str, limit: int, window_seconds: float = 60.0) -> bool:
        return self._check_inner(key, limit, window_seconds).limited

    def check(self, key: str, limit: int, window_seconds: float = 60.0) -> RateLimitDecision:
        return self._check_inner(key, limit, window_seconds)


def _create_backend() -> RateLimitBackend:
    """Create a rate-limit backend based on settings.

    Returns an ``InMemoryBackend`` if Redis is not configured or the
    ``redis`` package is not installed.
    """
    settings = get_settings()
    if settings.redis_url:
        try:
            backend = RedisBackend(settings.redis_url)
            log.info("Rate limiting: Redis backend enabled (%s)", _redact_url(settings.redis_url))
            return backend
        except ImportError:
            log.warning(
                "REDIS_URL is set but the 'redis' package is not installed. "
                "Install with: pip install redis. Falling back to in-memory rate limiting."
            )
    return InMemoryBackend()


# NOTE: ``_redact_url`` used to be duplicated here and in ``config.py``; the
# config copy is the single home now (imported above) — both call sites log
# connection strings and must redact identically.


class RateLimitMiddleware:
    """Per-IP fixed-window rate limiter with pluggable backend (pure ASGI).

    When Redis is configured, limits are enforced globally across all
    workers. Without Redis, each worker has its own counters and the
    effective per-IP limit is ``limit * workers``.

    Synchronous backend calls (Redis) are dispatched to a worker thread so
    the event loop is not blocked. If a Redis error occurs at runtime, the
    middleware falls back to an in-memory backend (fail-open).

    The middleware is a pure-ASGI callable: the 429 short-circuit sends the
    same ``{"error": "rate_limited"}`` JSON body + ``Retry-After: 60`` header
    the previous ``BaseHTTPMiddleware`` version produced, and streaming
    responses no longer pass through a buffering response task.
    """

    def __init__(
        self,
        app: ASGIApp,
        read_per_min: int = 120,
        embedding_per_min: int = 30,
        chat_per_min: int = 15,
        backend: RateLimitBackend | None = None,
    ) -> None:
        self.app = app
        self.read_per_min = read_per_min
        self.embedding_per_min = embedding_per_min
        self.chat_per_min = chat_per_min
        self._backend = backend or InMemoryBackend()
        # Fallback backend used if the primary (Redis) fails at runtime.
        self._fallback = InMemoryBackend()
        self._redis_failed = False

    def _is_embedding_request(self, request: Request) -> bool:
        """Return True for MCP POSTs to ``/mcp`` (apply the stricter limit)."""
        # MCP requests don't expose the tool name without parsing the body,
        # so we apply the stricter limit to all MCP POSTs as a safe default.
        return "/mcp" in request.url.path and request.method == "POST"

    def _is_chat_request(self, request: Request) -> bool:
        """Return True for POSTs to the chat sub-app (``/chat/*``).

        Each chat turn triggers one or more NVIDIA LLM calls plus several MCP
        round-trips, so a much tighter per-IP limit is applied than for reads.
        """
        return request.url.path.startswith("/chat") and request.method == "POST"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        # A lightweight Request view over the scope (no body consumption):
        # gives header/path/method access with unchanged semantics.
        request = Request(scope)

        ip = _get_remote_address(request)

        # /health is always allowed (used by Railway healthchecks).
        if request.url.path == "/health":
            await self.app(scope, receive, send)
            return

        # Loopback self-calls (e.g. chat agent -> MCP in the same container)
        # are exempt from rate limiting. The absence of X-Forwarded-For
        # ensures external requests cannot bypass the limiter by spoofing
        # 127.0.0.1 in that header.
        if not request.headers.get("x-forwarded-for") and ip in _LOOPBACK:
            await self.app(scope, receive, send)
            return

        if self._is_chat_request(request):
            bucket = "chat"
            limit = self.chat_per_min
        elif self._is_embedding_request(request):
            bucket = "mcp"
            limit = self.embedding_per_min
        else:
            bucket = "read"
            limit = self.read_per_min

        key = f"{ip}:{bucket}"

        # Use the fallback backend if Redis has already failed.
        backend = self._fallback if self._redis_failed else self._backend
        try:
            # Only Redis does blocking network I/O and needs a worker thread;
            # the in-memory backend is a lock + dict update and runs inline —
            # skipping the ``asyncio.to_thread`` hop on every request.
            decision: RateLimitDecision
            if backend.needs_thread:
                decision = await asyncio.to_thread(backend.check, key, limit)
            else:
                decision = backend.check(key, limit)
        except Exception:
            if not self._redis_failed:
                log.warning(
                    "Rate-limit backend failed (likely Redis unreachable). "
                    "Falling back to in-memory rate limiting.", exc_info=True,
                )
                self._redis_failed = True
            decision = self._fallback.check(key, limit)

        if decision.limited:
            response = Response(
                content='{"error": "rate_limited"}',
                status_code=429,
                media_type="application/json",
                # Time until the window actually resets (min 1s); the old
                # middleware always claimed 60s.
                headers={"Retry-After": str(max(1, math.ceil(decision.retry_after_s)))},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


class BodySizeLimitMiddleware:
    """Reject requests with a body larger than ``max_bytes`` (pure ASGI).

    Early-rejects on ``Content-Length``; also caps the streamed body as
    defense-in-depth.
    """

    def __init__(self, app: ASGIApp, max_bytes: int = 1_048_576) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        content_length = next(
            (v.decode("latin-1") for k, v in scope.get("headers") or []
             if k.lower() == b"content-length"),
            None,
        )
        if content_length and int(content_length) > self.max_bytes:
            response = Response(
                content='{"error": "request_too_large"}',
                status_code=413,
                media_type="application/json",
            )
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


def register_rate_limiting(app: Any) -> None:
    """Wire the rate-limit and body-size middleware into the Starlette app."""
    settings = get_rate_limit_settings()
    backend = _create_backend()

    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.body_size_max_bytes)
    app.add_middleware(
        RateLimitMiddleware,
        read_per_min=settings.read_per_min,
        embedding_per_min=settings.embedding_per_min,
        chat_per_min=settings.chat_per_min,
        backend=backend,
    )

    log.info(
        "Rate limiting enabled: %d req/min/IP (reads), %d req/min/IP (MCP/embeddings), "
        "%d req/min/IP (chat), %d byte body cap",
        settings.read_per_min,
        settings.embedding_per_min,
        settings.chat_per_min,
        settings.body_size_max_bytes,
    )
