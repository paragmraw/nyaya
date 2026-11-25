"""Unit tests for Nyaya v2.0 security: rate limiting, right-most XFF, bounded LRU,
streaming body-size caps, security headers, and input sanitization helpers.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

import nyaya.ratelimit as rl
from nyaya.ratelimit import (
    BodySizeLimitMiddleware,
    InMemoryBackend,
    MaxBodySizeMiddleware,
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
from nyaya.security_headers import SecurityHeadersMiddleware


def _make_app(middleware_cls, **mw_kwargs):
    """Return a Starlette app with the routes the rate-limit and body-cap tests need."""
    app = Starlette()

    async def health(_request):
        return JSONResponse({"ok": True})

    async def echo(request):
        body = await request.body()
        return JSONResponse({"len": len(body)})

    async def analytics(_request):
        return JSONResponse({"ok": True})

    async def chat_turn(_request):
        return JSONResponse({"ok": True})

    async def chat_health(_request):
        return JSONResponse({"ok": True})

    app.router.add_route("/health", health, methods=["GET"])
    app.router.add_route("/echo", echo, methods=["POST"])
    app.router.add_route("/api/analytics", analytics, methods=["GET"])
    app.router.add_route("/chat/turn", chat_turn, methods=["POST"])
    app.router.add_route("/chat/health", chat_health, methods=["GET"])
    app.add_middleware(middleware_cls, **mw_kwargs)
    return app


# ---------------------------------------------------------------------------
# strip_control_chars / cap_length / sanitize_text
# ---------------------------------------------------------------------------


def test_strip_control_chars_removes_c0_c1():
    s = "a\x00\x01\x7f\x80\x9fb"
    assert strip_control_chars(s) == "ab"


def test_strip_control_chars_preserves_nrt():
    s = "a\nb\rc\td"
    assert strip_control_chars(s) == "a\nb\rc\td"


def test_strip_control_chars_removes_bidi():
    s = "\u200b\u202e\u2066text\u2069"
    assert strip_control_chars(s) == "text"


def test_cap_length_raises_over_max():
    with pytest.raises(ValueError):
        cap_length("a" * (MAX_TEXT_BYTES + 1))


def test_cap_length_under_max_ok():
    assert cap_length("a" * MAX_TEXT_BYTES) == "a" * MAX_TEXT_BYTES


def test_sanitize_text_none():
    assert sanitize_text(None) == ""


def test_sanitize_text_strips_then_caps():
    s = "a" * 50 + "\x00" * 200
    assert sanitize_text(s, max_bytes=100) == "a" * 50


# ---------------------------------------------------------------------------
# InMemoryBackend (bounded LRU + fixed window)
# ---------------------------------------------------------------------------


def test_inmemory_backend_under_limit_not_limited():
    backend = InMemoryBackend()
    for _ in range(5):
        assert backend.is_limited("ip1", limit=5) is False


def test_inmemory_backend_over_limit_is_limited():
    backend = InMemoryBackend()
    for _ in range(3):
        backend.is_limited("ip1", limit=3)
    assert backend.is_limited("ip1", limit=3) is True


def test_inmemory_backend_keys_independent():
    backend = InMemoryBackend()
    for _ in range(3):
        backend.is_limited("ip1", limit=3)
    assert backend.is_limited("ip1", limit=3) is True
    assert backend.is_limited("ip2", limit=3) is False


def test_inmemory_backend_window_resets():
    backend = InMemoryBackend()
    for _ in range(3):
        backend.is_limited("ip1", limit=3)
    assert backend.is_limited("ip1", limit=3) is True

    entry = backend._counts["ip1"]
    entry["window"] -= 61.0
    assert backend.is_limited("ip1", limit=3) is False


def test_inmemory_backend_window_reset_via_time_mock(monkeypatch):
    t = [1_800_000_000.0]
    monkeypatch.setattr(rl.time, "time", lambda: t[0])
    backend = InMemoryBackend()
    for _ in range(2):
        backend.is_limited("ip1", limit=2)
    assert backend.is_limited("ip1", limit=2) is True
    t[0] += 61
    assert backend.is_limited("ip1", limit=2) is False


def test_inmemory_backend_lru_evicts_oldest_when_max_keys_exceeded():
    """Spoofed-IP flood cannot grow _counts past max_keys."""
    backend = InMemoryBackend(max_keys=50)
    for i in range(120):
        backend.is_limited(f"192.0.2.{i}:read", limit=10)
    assert len(backend._counts) <= 50
    # Most recent key is retained; earliest key was evicted
    assert "192.0.2.119:read" in backend._counts
    assert "192.0.2.0:read" not in backend._counts


def test_inmemory_backend_concurrent_increments_exact():
    backend = InMemoryBackend()
    total = 400
    with ThreadPoolExecutor(max_workers=16) as pool:
        futures = [pool.submit(backend.is_limited, "ip1", limit=total) for _ in range(total)]
        results = [f.result() for f in futures]
    assert results == [False] * total
    assert backend._counts["ip1"]["count"] == total


def test_inmemory_check_reports_window_remaining(monkeypatch):
    t = [1_800_000_000.0]
    monkeypatch.setattr(rl.time, "time", lambda: t[0])
    backend = InMemoryBackend()
    for _ in range(3):
        backend.is_limited("ip1", limit=3)
    decision = backend.check("ip1", limit=3)
    assert decision.limited is True
    assert decision.retry_after_s == 60.0

    t[0] += 25.0
    decision = backend.check("ip1", limit=3)
    assert decision.limited is True
    assert decision.retry_after_s == 35.0

    t[0] += 40.0
    decision = backend.check("ip1", limit=3)
    assert decision.limited is False
    assert decision.retry_after_s == 0.0


def test_backend_thread_dispatch_flags():
    assert InMemoryBackend.needs_thread is False
    assert RedisBackend.needs_thread is True


# ---------------------------------------------------------------------------
# RedisBackend
# ---------------------------------------------------------------------------


class _FakeRedis:
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
    import redis as redis_mod

    fake = _FakeRedis()
    monkeypatch.setattr(redis_mod, "from_url", lambda url, **kwargs: fake)
    backend = RedisBackend("redis://localhost:6379/0")

    now = 1_800_000_000
    monkeypatch.setattr(rl.time, "time", lambda: float(now))
    monkeypatch.setattr(rl.time, "monotonic", lambda: 987_654.321)

    expected_key = f"rl:ip1:read:{now // 60}"
    assert backend.is_limited("ip1:read", limit=5) is False
    assert list(fake.store) == [expected_key]

    for _ in range(2):
        assert backend.is_limited("ip1:read", limit=5) is False
    assert fake.store == {expected_key: 3}

    assert backend.is_limited("ip1:read", limit=3) is True
    assert fake.store == {expected_key: 4}
    assert fake.expires[-1] == (expected_key, 61)


# ---------------------------------------------------------------------------
# RateLimitMiddleware: /health, buckets, right-most XFF
# ---------------------------------------------------------------------------


def test_health_endpoint_not_rate_limited():
    backend = InMemoryBackend()
    for _ in range(120):
        backend.is_limited("testclient:read", limit=120)
    assert backend.is_limited("testclient:read", limit=120) is True

    app = _make_app(RateLimitMiddleware, read_per_min=120, backend=backend)
    with TestClient(app) as client:
        for _ in range(5):
            r = client.get("/health")
            assert r.status_code == 200


def test_rate_limit_blocks_after_threshold():
    backend = InMemoryBackend()
    app = _make_app(RateLimitMiddleware, read_per_min=3, embedding_per_min=3, backend=backend)
    with TestClient(app) as client:
        for _ in range(3):
            r = client.post("/echo", json={"x": 1})
            assert r.status_code == 200
        r = client.post("/echo", json={"x": 1})
        assert r.status_code == 429
        retry_after = int(r.headers["retry-after"])
        assert 1 <= retry_after <= 60


def test_rate_limit_returns_json_error():
    backend = InMemoryBackend()
    app = _make_app(RateLimitMiddleware, read_per_min=1, embedding_per_min=1, backend=backend)
    with TestClient(app) as client:
        client.post("/echo", json={})
        r = client.post("/echo", json={})
        assert r.status_code == 429
        body = r.json()
        assert body["message"] == "rate_limited"
        assert body["detail"]


def test_chat_rate_limit_tighter_than_reads():
    backend = InMemoryBackend()
    app = _make_app(RateLimitMiddleware, read_per_min=100, chat_per_min=2, backend=backend)
    with TestClient(app) as client:
        for _ in range(2):
            r = client.post("/chat/turn", json={"message": "hi"})
            assert r.status_code == 200
        r = client.post("/chat/turn", json={"message": "hi"})
        assert r.status_code == 429


def test_analytics_rate_limit_bucket():
    """GET /api/analytics uses analytics_per_min bucket independently of reads."""
    backend = InMemoryBackend()
    app = _make_app(RateLimitMiddleware, read_per_min=100, analytics_per_min=2, backend=backend)
    with TestClient(app) as client:
        for _ in range(2):
            assert client.get("/api/analytics").status_code == 200
        assert client.get("/api/analytics").status_code == 429
        # Regular read route still open
        assert client.post("/echo", json={}).status_code == 200


def test_chat_rate_limit_only_applies_to_post():
    backend = InMemoryBackend()
    app = _make_app(RateLimitMiddleware, read_per_min=100, chat_per_min=1, backend=backend)
    with TestClient(app) as client:
        client.post("/chat/turn", json={"message": "hi"})
        r = client.post("/chat/turn", json={"message": "hi"})
        assert r.status_code == 429
        r = client.get("/chat/health")
        assert r.status_code == 200


def test_loopback_exempt_without_xff(monkeypatch):
    monkeypatch.setattr(rl, "_get_remote_address", lambda request, **kw: "127.0.0.1")
    backend = InMemoryBackend()
    app = _make_app(RateLimitMiddleware, read_per_min=2, backend=backend)
    with TestClient(app) as client:
        for _ in range(10):
            r = client.post("/echo", json={"x": 1})
            assert r.status_code == 200


def test_loopback_not_exempt_with_xff():
    backend = InMemoryBackend()
    app = _make_app(RateLimitMiddleware, read_per_min=2, backend=backend)
    with TestClient(app) as client:
        for _ in range(2):
            r = client.post("/echo", json={"x": 1}, headers={"X-Forwarded-For": "127.0.0.1"})
            assert r.status_code == 200
        r = client.post("/echo", json={"x": 1}, headers={"X-Forwarded-For": "127.0.0.1"})
        assert r.status_code == 429


def test_spoofed_leftmost_xff_cannot_bypass_rate_limit():
    """Attacker rotating left-most X-Forwarded-For behind 1 trusted proxy still hits 429."""
    backend = InMemoryBackend()
    app = _make_app(
        RateLimitMiddleware,
        read_per_min=2,
        trusted_proxy_hops=1,
        backend=backend,
    )
    with TestClient(app) as client:
        r1 = client.post("/echo", json={}, headers={"X-Forwarded-For": "1.1.1.1, 203.0.113.9"})
        r2 = client.post("/echo", json={}, headers={"X-Forwarded-For": "2.2.2.2, 203.0.113.9"})
        r3 = client.post("/echo", json={}, headers={"X-Forwarded-For": "3.3.3.3, 203.0.113.9"})
        assert r1.status_code == 200
        assert r2.status_code == 200
        assert r3.status_code == 429


def test_buckets_independent_mcp_vs_chat():
    backend = InMemoryBackend()
    app = _make_app(
        RateLimitMiddleware,
        read_per_min=100,
        embedding_per_min=3,
        chat_per_min=3,
        backend=backend,
    )
    with TestClient(app) as client:
        async def mcp_echo(_request):
            return JSONResponse({"ok": True})

        app.router.add_route("/mcp", mcp_echo, methods=["POST"])

        for _ in range(3):
            r = client.post("/mcp", json={"x": 1})
            assert r.status_code == 200
        r = client.post("/mcp", json={"x": 1})
        assert r.status_code == 429

        r = client.post("/chat/turn", json={"message": "hi"})
        assert r.status_code == 200


def test_buckets_independent_chat_vs_read():
    backend = InMemoryBackend()
    app = _make_app(
        RateLimitMiddleware,
        read_per_min=3,
        chat_per_min=2,
        backend=backend,
    )
    with TestClient(app) as client:
        for _ in range(2):
            r = client.post("/chat/turn", json={"message": "hi"})
            assert r.status_code == 200
        r = client.post("/chat/turn", json={"message": "hi"})
        assert r.status_code == 429

        r = client.post("/echo", json={"x": 1})
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# BodySizeLimitMiddleware / MaxBodySizeMiddleware (Content-Length + streaming)
# ---------------------------------------------------------------------------


def test_body_size_rejects_oversize_content_length():
    app = _make_app(BodySizeLimitMiddleware, max_bytes=100)
    with TestClient(app) as client:
        r = client.post("/echo", content="x" * 200, headers={"Content-Length": "200"})
        assert r.status_code == 413


def test_body_size_rejects_invalid_or_negative_content_length():
    """Malformed or negative Content-Length returns 400 (never unhandled 500)."""
    app = _make_app(MaxBodySizeMiddleware, max_bytes=100)
    with TestClient(app) as client:
        r_bad = client.post("/echo", content="x", headers={"Content-Length": "not-an-int"})
        assert r_bad.status_code == 400
        assert r_bad.json()["message"] == "invalid_content_length"

        r_neg = client.post("/echo", content="x", headers={"Content-Length": "-10"})
        assert r_neg.status_code == 400
        assert r_neg.json()["message"] == "invalid_content_length"


@pytest.mark.asyncio
async def test_body_size_rejects_chunked_stream_without_content_length():
    """Streaming chunked body without Content-Length is rejected with 413 once it exceeds max_bytes."""
    app = _make_app(BodySizeLimitMiddleware, max_bytes=64)
    chunks = [
        {"type": "http.request", "body": b"a" * 40, "more_body": True},
        {"type": "http.request", "body": b"b" * 40, "more_body": False},
    ]
    sent: list[dict] = []

    async def receive():
        return chunks.pop(0)

    async def send(msg):
        sent.append(msg)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/echo",
        "headers": [],  # No Content-Length header!
        "query_string": b"",
        "client": ("203.0.113.5", 1234),
        "scheme": "http",
    }
    await app(scope, receive, send)
    start_msg = next(m for m in sent if m["type"] == "http.response.start")
    assert start_msg["status"] == 413


def test_body_size_allows_under_cap():
    app = _make_app(BodySizeLimitMiddleware, max_bytes=100)
    with TestClient(app) as client:
        r = client.post("/echo", content="x" * 50)
        assert r.status_code == 200


def test_body_size_429_is_413():
    app = _make_app(BodySizeLimitMiddleware, max_bytes=10)
    with TestClient(app) as client:
        r = client.post("/echo", content="x" * 100)
        assert r.status_code == 413
        assert r.json()["message"] == "request_too_large"


def test_body_size_boundary_exact():
    app = _make_app(BodySizeLimitMiddleware, max_bytes=100)
    with TestClient(app) as client:
        r = client.post("/echo", content="x" * 100)
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# _get_remote_address (right-most trusted proxy hop)
# ---------------------------------------------------------------------------


def test_get_remote_address_x_forwarded_for_right_most_hop():
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
    assert _get_remote_address(req, trusted_proxy_hops=1) == "198.51.100.1"
    assert _get_remote_address(req, trusted_proxy_hops=2) == "203.0.113.5"
    assert _get_remote_address(req, trusted_proxy_hops=0) == "127.0.0.1"


def test_get_remote_address_no_forwarded():
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
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [(b"x-forwarded-for", b"  203.0.113.5  ,  198.51.100.1  ")],
        "client": ("127.0.0.1", 5000),
        "query_string": b"",
        "root_path": "",
        "scheme": "http",
    }
    req = Request(scope)
    assert _get_remote_address(req, trusted_proxy_hops=1) == "198.51.100.1"


# ---------------------------------------------------------------------------
# SecurityHeadersMiddleware & Middleware Ordering
# ---------------------------------------------------------------------------


def test_security_headers_coop_corp_and_x_forwarded_proto_hsts():
    app = _make_app(SecurityHeadersMiddleware, trusted_proxy_hops=1)
    with TestClient(app) as client:
        r_http = client.get("/health")
        assert r_http.headers.get("cross-origin-opener-policy") == "same-origin"
        assert r_http.headers.get("cross-origin-resource-policy") == "same-origin"
        assert "strict-transport-security" not in r_http.headers

        r_https = client.get("/health", headers={"X-Forwarded-Proto": "https"})
        assert "max-age=31536000" in r_https.headers.get("strict-transport-security", "")


def test_429_carries_security_and_cors_headers():
    from starlette.middleware.cors import CORSMiddleware

    backend = InMemoryBackend()
    app = Starlette()

    async def echo(_request):
        return JSONResponse({"ok": True})

    app.router.add_route("/echo", echo, methods=["POST"])
    app.add_middleware(RateLimitMiddleware, read_per_min=1, backend=backend)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["https://nyaya.parag.tech"],
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Accept"],
        allow_credentials=False,
    )
    with TestClient(app) as client:
        client.post("/echo", json={})
        r = client.post(
            "/echo",
            json={},
            headers={"Origin": "https://nyaya.parag.tech"},
        )
        assert r.status_code == 429
        assert 1 <= int(r.headers.get("retry-after", "0")) <= 60
        assert r.headers.get("content-security-policy", "").startswith("default-src")
        assert r.headers.get("access-control-allow-origin") == "https://nyaya.parag.tech"


def test_production_app_middleware_stack_order():
    """The production HTTP app middleware stack must be CORS -> SecurityHeaders -> RequestId -> RateLimit -> BodySizeLimit."""
    from nyaya.server import app

    order = [m.cls.__name__ for m in app.user_middleware]
    security_stack = [
        name
        for name in order
        if name
        in {
            "CORSMiddleware",
            "SecurityHeadersMiddleware",
            "RequestIdMiddleware",
            "RateLimitMiddleware",
            "BodySizeLimitMiddleware",
        }
    ]
    assert security_stack == [
        "CORSMiddleware",
        "SecurityHeadersMiddleware",
        "RequestIdMiddleware",
        "RateLimitMiddleware",
        "BodySizeLimitMiddleware",
    ]
