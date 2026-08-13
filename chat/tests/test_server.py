"""Tests for nyaya_chat.server — the chat FastAPI sub-app.

The sub-app is mounted by the host nyaya server at ``/chat``, so these tests
hit the sub-app directly (routes are ``/health``, ``/turn``, ``/`` without the
``/chat`` prefix). Cross-cutting middleware (CORS, security headers,
request-id, top-level rate limiting, body-size cap) is owned by the host and
not tested here.
"""

from __future__ import annotations

from fastapi.testclient import TestClient


def _make_test_app(monkeypatch, graph=None, tools=None):
    """Build the chat sub-app with the lifespan replaced so startup sets a
    scripted graph/tools without touching NVIDIA or MCP."""
    from nyaya_chat import agent as agent_mod
    from nyaya_chat import server as srv

    async def _fake_get_agent():
        return graph, tools or []

    monkeypatch.setattr(agent_mod, "get_agent", _fake_get_agent)
    monkeypatch.setattr(srv, "get_agent", _fake_get_agent, raising=False)

    return srv.create_app()


class _Chunk:
    def __init__(self, content):
        self.content = content


class _Graph:
    async def astream(self, *a, **k):
        return
        yield  # async gen marker


def test_health_ok(monkeypatch):
    app = _make_test_app(monkeypatch, graph=_Graph(), tools=["t1", "t2"])
    with TestClient(app) as c:
        r = c.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "healthy"
        assert body["tools_loaded"] == 2
        assert "nemotron" in body["model"]


def test_health_degraded_when_no_graph(monkeypatch):
    app = _make_test_app(monkeypatch, graph=None, tools=[])
    with TestClient(app) as c:
        r = c.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "degraded"
        assert r.json()["tools_loaded"] == 0


def test_root_info(monkeypatch):
    app = _make_test_app(monkeypatch, graph=_Graph())
    with TestClient(app) as c:
        r = c.get("/")
        assert r.status_code == 200
        assert r.json()["service"] == "nyaya-chat"
        assert "turn" in r.json()["turn"]


def test_turn_empty_message_422(monkeypatch):
    app = _make_test_app(monkeypatch, graph=_Graph())
    with TestClient(app) as c:
        r = c.post("/turn", json={"message": ""})
        assert r.status_code == 422


def test_turn_blank_message_422(monkeypatch):
    app = _make_test_app(monkeypatch, graph=_Graph())
    with TestClient(app) as c:
        r = c.post("/turn", json={"message": "   "})
        assert r.status_code == 422


def test_turn_missing_message_422(monkeypatch):
    app = _make_test_app(monkeypatch, graph=_Graph())
    with TestClient(app) as c:
        r = c.post("/turn", json={})
        assert r.status_code == 422


def test_turn_no_graph_503(monkeypatch):
    app = _make_test_app(monkeypatch, graph=None, tools=[])
    with TestClient(app) as c:
        r = c.post("/turn", json={"message": "hi"})
        assert r.status_code == 503
        assert r.json()["error"] == "agent_unavailable"


def test_turn_streams_sse(monkeypatch):
    """A scripted graph yields tokens + done; the endpoint returns text/event-stream."""
    class _G:
        async def astream(self, _input, stream_mode=None, version=None):
            yield {"type": "messages", "data": (_Chunk("Hello "), {})}
            yield {"type": "messages", "data": (_Chunk("world"), {})}

    app = _make_test_app(monkeypatch, graph=_G(), tools=["t"])
    with TestClient(app) as c:
        with c.stream("POST", "/turn", json={"message": "hi"}) as r:
            assert r.status_code == 200
            assert "text/event-stream" in r.headers["content-type"]
            body = b"".join(r.iter_bytes())
            assert b"event: status" in body  # immediate status
            assert b"analyzing" in body  # new initial phase label
            assert b'event: token\ndata: {"content": "Hello "}' in body
            assert b'event: token\ndata: {"content": "world"}' in body
            assert body.endswith(b"event: done\ndata: {}\n\n")
            assert r.headers.get("Cache-Control") == "no-cache"


def test_turn_history_capped(monkeypatch):
    """History longer than max_history is accepted (server caps), doesn't error."""
    class _G:
        async def astream(self, _input, stream_mode=None, version=None):
            yield {"type": "messages", "data": (_Chunk("ok"), {})}

    app = _make_test_app(monkeypatch, graph=_G(), tools=["t"])
    with TestClient(app) as c:
        history = [{"role": "user", "content": f"q{i}"} for i in range(20)]
        with c.stream("POST", "/turn", json={"message": "now", "history": history}) as r:
            assert r.status_code == 200
            body = b"".join(r.iter_bytes())
            assert b'event: token\ndata: {"content": "ok"}' in body


def test_turn_sse_headers(monkeypatch):
    """SSE response includes no-cache + X-Accel-Buffering headers."""
    class _G:
        async def astream(self, *a, **k):
            return
            yield

    app = _make_test_app(monkeypatch, graph=_G(), tools=["t"])
    with TestClient(app) as c:
        with c.stream("POST", "/turn", json={"message": "hi"}) as r:
            assert r.status_code == 200
            assert r.headers.get("Cache-Control") == "no-cache"
            assert r.headers.get("X-Accel-Buffering") == "no"
            assert r.headers.get("Connection") == "keep-alive"


def test_message_too_long_422(monkeypatch):
    app = _make_test_app(monkeypatch, graph=_Graph())
    with TestClient(app) as c:
        r = c.post("/turn", json={"message": "x" * 4001})
        assert r.status_code == 422
