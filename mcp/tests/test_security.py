"""Unit tests for security: rate limiting, body-size caps, security headers,
and input sanitization helpers.

Uses ``starlette.testclient.TestClient`` to exercise the middleware without a
live server. ``monkeypatch`` handles time mocking for the rate-limit window.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

import nyaya.ratelimit as rl
from nyaya.ratelimit import (
    BodySizeLimitMiddleware,
    InMemoryBackend,
    RateLimitMiddleware,
    RedisBackend,
    _get_remote_address,
)
from nyaya.sanitize import (
    MAX_TEXT_BYTES,
    cap_length,
    sanitize_text,
    strip_control_chars,
)

# ---------------------------------------------------------------------------
# Helpers — build a minimal Starlette app with the middleware under test.
# ---------------------------------------------------------------------------

def _make_app(middleware_cls, **mw_kwargs):
    """Return a Starlette app with the routes the rate-limit tests need."""
    app = Starlette()

    async def health(request):
        return JSONResponse({"ok": True})

    async def echo(request):
        body = await request.body()
        return JSONResponse({"len": len(body)})

    async def chat_turn(request):
        return JSONResponse({"ok": True})

    async def chat_health(request):
        return JSONResponse({"ok": True})

    app.router.add_route("/health", health, methods=["GET"])
    app.router.add_route("/echo", echo, methods=["POST"])
    app.router.add_route("/chat/turn", chat_turn, methods=["POST"])
    app.router.add_route("/chat/health", chat_health, methods=["GET"])
    app.add_middleware(middleware_cls, **mw_kwargs)
    return app


# ---------------------------------------------------------------------------
# strip_control_chars — shared with test_sanitize.py but re-checked here in
# the security context (defense-in-depth surface).
# ---------------------------------------------------------------------------

def test_strip_control_chars_removes_c0_c1():
    """C0 and C1 control characters are removed."""
    s = "a\x00\x01\x7f\x80\x9fb"
    assert strip_control_chars(s) == "ab"


def test_strip_control_chars_preserves_nrt():
    r"""\n, \r, \t are preserved (legitimate in legal text)."""
    s = "a\nb\rc\td"
    assert strip_control_chars(s) == "a\nb\rc\td"


def test_strip_control_chars_removes_bidi():
    """Bidi/format characters (U+200B-200F, U+202A-202E, U+2066-2069) removed."""
    s = "\u200b\u202e\u2066text\u2069"
    assert strip_control_chars(s) == "text"


# ---------------------------------------------------------------------------
# cap_length — security boundary
# ---------------------------------------------------------------------------

def test_cap_length_raises_over_max():
    """Text exceeding MAX_TEXT_BYTES raises ValueError."""
    with pytest.raises(ValueError):
        cap_length("a" * (MAX_TEXT_BYTES + 1))


def test_cap_length_under_max_ok():
    """Text at or under the limit passes."""
    assert cap_length("a" * MAX_TEXT_BYTES) == "a" * MAX_TEXT_BYTES


# ---------------------------------------------------------------------------
# sanitize_text
# ---------------------------------------------------------------------------

def test_sanitize_text_none():
    """None returns empty string (no crash)."""
    assert sanitize_text(None) == ""


def test_sanitize_text_strips_then_caps():
    """Strip runs before cap, so control chars don't count toward the limit."""
    s = "a" * 50 + "\x00" * 200
    assert sanitize_text(s, max_bytes=100) == "a" * 50


# ---------------------------------------------------------------------------
# InMemoryBackend
# ---------------------------------------------------------------------------

def test_inmemory_backend_under_limit_not_limited():
    """Requests under the limit return False (allowed)."""
    backend = InMemoryBackend()
    for _ in range(5):
        assert backend.is_limited("ip1", limit=5) is False


def test_inmemory_backend_over_limit_is_limited():
    """The (limit+1)-th request returns True (blocked)."""
    backend = InMemoryBackend()
    for _ in range(3):
        backend.is_limited("ip1", limit=3)
    assert backend.is_limited("ip1", limit=3) is True


def test_inmemory_backend_keys_independent():
    """Different keys have independent counters."""
    backend = InMemoryBackend()
    for _ in range(3):
        backend.is_limited("ip1", limit=3)
    assert backend.is_limited("ip1", limit=3) is True
    # ip2 is still under the limit.
    assert backend.is_limited("ip2", limit=3) is False


def test_inmemory_backend_window_resets(monkeypatch):
    """After the window elapses, the counter resets and requests are allowed."""
    backend = InMemoryBackend()
    # Exhaust the limit.
    for _ in range(3):
        backend.is_limited("ip1", limit=3)
    assert backend.is_limited("ip1", limit=3) is True

    # Advance wall-clock time past the 60-second window by directly
    # manipulating the stored entry (the backend reads time.time() at
    # call time, so we patch the entry's window start).
    entry = backend._counts["ip1"]
    entry["window"] -= 61.0
    assert backend.is_limited("ip1", limit=3) is False


def test_inmemory_backend_window_reset_via_time_mock(monkeypatch):
    """The window reset is driven by time.time(); mocking time confirms it."""
    t = [1_800_000_000.0]
    monkeypatch.setattr(rl.time, "time", lambda: t[0])
    backend = InMemoryBackend()
    for _ in range(2):
        backend.is_limited("ip1", limit=2)
    assert backend.is_limited("ip1", limit=2) is True
    t[0] += 61  # past the 60s window
    assert backend.is_limited("ip1", limit=2) is False


def test_inmemory_backend_concurrent_increments_exact():
    """N concurrent is_limited calls for one key record exactly N hits.

    The middleware dispatches backend calls to worker threads via
    asyncio.to_thread, so the counter mutation must be lock-guarded; without
    the lock concurrent read-modify-writes lose increments and the final
    count lands below N.
    """
    backend = InMemoryBackend()
    total = 400
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(backend.is_limited, "ip1", limit=total) for _ in range(total)]
        results = [f.result() for f in futures]
    # Every call is under the limit, and every hit was recorded.
    assert results == [False] * total
    assert backend._counts["ip1"]["count"] == total


# ---------------------------------------------------------------------------
# RedisBackend
# ---------------------------------------------------------------------------

class _FakeRedis:
    """Minimal redis-py stand-in that records pipeline INCR/EXPIRE commands."""

    def __init__(self) -> None:
        self.store: dict[str, int] = {}
        self.expires: list[tuple[str, int]] = []
        self._last_key: str | None = None

    def pipeline(self) -> _FakeRedis:
        return self

    def incr(self, key: str) -> int:
        self.store[key] = self.store.get(key, 0) + 1
        self._last_key = key
        return self.store[key]

    def expire(self, key: str, ttl: int) -> bool:
        self.expires.append((key, ttl))
        return True

    def execute(self) -> list:
        assert self._last_key is not None
        return [self.store[self._last_key], True]


def test_redis_backend_window_key_uses_wall_clock(monkeypatch):
    """The Redis window key is derived from wall-clock time.time(), not time.monotonic().

    Monotonic time has a different origin in every process, so a
    monotonic-derived bucket key would give each worker its own counters and
    silently defeat the global limit. A wildly different monotonic origin
    must leave the keys (and limiting behaviour) unaffected.
    """
    import redis as redis_mod

    fake = _FakeRedis()
    monkeypatch.setattr(redis_mod, "from_url", lambda url, **kwargs: fake)
    backend = RedisBackend("redis://localhost:6379/0")

    now = 1_800_000_000  # fixed wall-clock instant
    monkeypatch.setattr(rl.time, "time", lambda: float(now))
    monkeypatch.setattr(rl.time, "monotonic", lambda: 987_654.321)  # wild per-process origin

    expected_key = f"rl:ip1:read:{now // 60}"
    assert backend.is_limited("ip1:read", limit=5) is False
    assert list(fake.store) == [expected_key]

    # Repeated calls accumulate in the same bucket (no per-process drift).
    for _ in range(2):
        assert backend.is_limited("ip1:read", limit=5) is False
    assert fake.store == {expected_key: 3}

    # The limit is enforced on the shared counter: at count 3, a limit-3
    # window increments to 4 and blocks.
    assert backend.is_limited("ip1:read", limit=3) is True
    assert fake.store == {expected_key: 4}
    assert fake.expires[-1] == (expected_key, 61)


# ---------------------------------------------------------------------------
# RateLimitMiddleware — /health exemption
# ---------------------------------------------------------------------------

def test_health_endpoint_not_rate_limited():
    """/health bypasses rate limiting entirely (Railway healthcheck friendly)."""
    backend = InMemoryBackend()
    # Exhaust the limit for any key so a normal request would 429.
    # The middleware keys counters as "{ip}:{bucket}", so use the read bucket
    # (the default for non-chat, non-mcp requests).
    for _ in range(120):
        backend.is_limited("testclient:read", limit=120)
    assert backend.is_limited("testclient:read", limit=120) is True

    app = _make_app(RateLimitMiddleware, read_per_min=120, backend=backend)
    with TestClient(app) as client:
        # Multiple /health hits still succeed despite the exhausted counter.
        for _ in range(5):
            r = client.get("/health")
            assert r.status_code == 200


def test_rate_limit_blocks_after_threshold():
    """Once the per-IP limit is hit, subsequent requests return 429."""
    backend = InMemoryBackend()
    app = _make_app(RateLimitMiddleware, read_per_min=3, embedding_per_min=3, backend=backend)
    with TestClient(app) as client:
        # First 3 POSTs to /echo are allowed.
        for _ in range(3):
            r = client.post("/echo", json={"x": 1})
            assert r.status_code == 200
        # 4th request is blocked.
        r = client.post("/echo", json={"x": 1})
        assert r.status_code == 429
        assert r.headers.get("retry-after") == "60"


def test_rate_limit_returns_json_error():
    """The 429 response body is JSON with an 'error' field."""
    backend = InMemoryBackend()
    app = _make_app(RateLimitMiddleware, read_per_min=1, embedding_per_min=1, backend=backend)
    with TestClient(app) as client:
        client.post("/echo", json={})
        r = client.post("/echo", json={})
        assert r.status_code == 429
        body = r.json()
        assert "error" in body


def test_chat_rate_limit_tighter_than_reads():
    """POST /chat/* uses the chat_per_min limit (default 15, tighter than reads)."""
    backend = InMemoryBackend()
    app = _make_app(RateLimitMiddleware, read_per_min=100, chat_per_min=2, backend=backend)
    with TestClient(app) as client:
        # 2 chat turns allowed, 3rd blocked.
        for _ in range(2):
            r = client.post("/chat/turn", json={"message": "hi"})
            assert r.status_code == 200
        r = client.post("/chat/turn", json={"message": "hi"})
        assert r.status_code == 429


def test_chat_rate_limit_only_applies_to_post():
    """GET /chat/health is not subject to the chat (POST) limit."""
    backend = InMemoryBackend()
    app = _make_app(RateLimitMiddleware, read_per_min=100, chat_per_min=1, backend=backend)
    with TestClient(app) as client:
        # Exhaust the chat POST limit.
        client.post("/chat/turn", json={"message": "hi"})
        r = client.post("/chat/turn", json={"message": "hi"})
        assert r.status_code == 429
        # A GET to the chat sub-app still passes (read limit applies, not chat).
        r = client.get("/chat/health")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# RateLimitMiddleware — loopback exemption
# ---------------------------------------------------------------------------

def test_loopback_exempt_without_xff(monkeypatch):
    """Loopback requests without X-Forwarded-For bypass rate limiting.

    This models genuine in-container self-calls (e.g. the chat agent calling
    the MCP server over localhost). The TestClient's default client host is
    "testclient" (not loopback), so we monkeypatch _get_remote_address to
    return 127.0.0.1, simulating a real loopback connection.
    """
    import nyaya.ratelimit as rl
    monkeypatch.setattr(rl, "_get_remote_address", lambda request: "127.0.0.1")

    backend = InMemoryBackend()
    app = _make_app(RateLimitMiddleware, read_per_min=2, backend=backend)
    with TestClient(app) as client:
        # Far more than the 2 req/min read limit — all should pass.
        for _ in range(10):
            r = client.post("/echo", json={"x": 1})
            assert r.status_code == 200


def test_loopback_not_exempt_with_xff():
    """Requests with X-Forwarded-For: 127.0.0.1 are NOT exempt (spoof-proof).

    External requests through a reverse proxy always carry X-Forwarded-For.
    Even if that header is spoofed to a loopback address, the limiter must
    still enforce the limit.
    """
    backend = InMemoryBackend()
    app = _make_app(RateLimitMiddleware, read_per_min=2, backend=backend)
    with TestClient(app) as client:
        # X-Forwarded-For is set to 127.0.0.1 — should NOT be exempt.
        for _ in range(2):
            r = client.post(
                "/echo", json={"x": 1},
                headers={"X-Forwarded-For": "127.0.0.1"},
            )
            assert r.status_code == 200
        # 3rd request should be blocked despite the loopback IP in XFF.
        r = client.post(
            "/echo", json={"x": 1},
            headers={"X-Forwarded-For": "127.0.0.1"},
        )
        assert r.status_code == 429


# ---------------------------------------------------------------------------
# RateLimitMiddleware — per-bucket counter isolation
# ---------------------------------------------------------------------------

def test_buckets_independent_mcp_vs_chat():
    """Exhausting the MCP bucket does not block chat requests from the same IP."""
    backend = InMemoryBackend()
    app = _make_app(
        RateLimitMiddleware,
        read_per_min=100,
        embedding_per_min=3,
        chat_per_min=3,
        backend=backend,
    )
    with TestClient(app) as client:
        # Add an /mcp route so the embedding bucket is exercised.
        async def mcp_echo(request):
            return JSONResponse({"ok": True})
        app.router.add_route("/mcp", mcp_echo, methods=["POST"])

        # Exhaust the MCP (embedding) bucket.
        for _ in range(3):
            r = client.post("/mcp", json={"x": 1})
            assert r.status_code == 200
        r = client.post("/mcp", json={"x": 1})
        assert r.status_code == 429

        # Chat requests from the same IP should still be allowed (separate bucket).
        r = client.post("/chat/turn", json={"message": "hi"})
        assert r.status_code == 200


def test_buckets_independent_chat_vs_read():
    """Exhausting the chat bucket does not block read requests from the same IP."""
    backend = InMemoryBackend()
    app = _make_app(
        RateLimitMiddleware,
        read_per_min=3,
        chat_per_min=2,
        backend=backend,
    )
    with TestClient(app) as client:
        # Exhaust the chat bucket.
        for _ in range(2):
            r = client.post("/chat/turn", json={"message": "hi"})
            assert r.status_code == 200
        r = client.post("/chat/turn", json={"message": "hi"})
        assert r.status_code == 429

        # Read (POST /echo) requests from the same IP should still be allowed.
        r = client.post("/echo", json={"x": 1})
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# BodySizeLimitMiddleware
# ---------------------------------------------------------------------------

def test_body_size_rejects_oversize_content_length():
    """A Content-Length over the cap returns 413."""
    app = _make_app(BodySizeLimitMiddleware, max_bytes=100)
    with TestClient(app) as client:
        r = client.post("/echo", content="x" * 200, headers={"Content-Length": "200"})
        assert r.status_code == 413


def test_body_size_allows_under_cap():
    """A body under the cap is accepted."""
    app = _make_app(BodySizeLimitMiddleware, max_bytes=100)
    with TestClient(app) as client:
        r = client.post("/echo", content="x" * 50)
        assert r.status_code == 200


def test_body_size_429_is_413():
    """The 413 response body is JSON."""
    app = _make_app(BodySizeLimitMiddleware, max_bytes=10)
    with TestClient(app) as client:
        r = client.post("/echo", content="x" * 100)
        assert r.status_code == 413
        assert "error" in r.json()


def test_body_size_boundary_exact():
    """A body exactly at the cap is accepted (boundary inclusive)."""
    app = _make_app(BodySizeLimitMiddleware, max_bytes=100)
    with TestClient(app) as client:
        r = client.post("/echo", content="x" * 100)
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# _get_remote_address
# ---------------------------------------------------------------------------

def test_get_remote_address_x_forwarded_for():
    """The first IP in X-Forwarded-For is returned (trusted proxy)."""
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"x-forwarded-for", b"203.0.113.5, 198.51.100.1")],
        "client": ("127.0.0.1", 5000),
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
    }
    req = Request(scope)
    assert _get_remote_address(req) == "203.0.113.5"


def test_get_remote_address_no_forwarded():
    """Without X-Forwarded-For, the client host is used."""
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "client": ("127.0.0.1", 5000),
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
    }
    req = Request(scope)
    assert _get_remote_address(req) == "127.0.0.1"


def test_get_remote_address_no_client():
    """If client is None and no X-Forwarded-For, returns 'unknown'."""
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
        "client": None,
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
    }
    req = Request(scope)
    assert _get_remote_address(req) == "unknown"


def test_get_remote_address_strips_whitespace():
    """Whitespace around the first X-Forwarded-For entry is stripped."""
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"x-forwarded-for", b"  203.0.113.5  , 198.51.100.1")],
        "client": ("127.0.0.1", 5000),
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
    }
    req = Request(scope)
    assert _get_remote_address(req) == "203.0.113.5"
