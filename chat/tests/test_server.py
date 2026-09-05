"""Tests for nyaya_chat.server — the chat FastAPI sub-app.

The sub-app is mounted by the host nyaya server at ``/chat``, so these tests
hit the sub-app directly (routes are ``/health``, ``/turn``, ``/`` without the
``/chat`` prefix). Cross-cutting middleware (CORS, security headers,
request-id, top-level rate limiting, body-size cap) is owned by the host and
not tested here.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient


def _parse_sse(raw: bytes) -> list[tuple[str, dict]]:
    """Parse SSE bytes into a list of (event, payload) tuples."""
    events: list[tuple[str, dict]] = []
    for block in raw.decode("utf-8").split("\n\n"):
        if not block.strip():
            continue
        event = "message"
        data = ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = line[len("data:"):]
        events.append((event, json.loads(data)))
    return events


def _make_test_app(monkeypatch, graph=None, tools=None, intent=None):
    """Build the chat sub-app with the lifespan replaced so startup sets a
    scripted graph/tools without touching NVIDIA or MCP. The guardrail returns
    ``intent`` (LEGAL by default) so tests can script either the agent
    pipeline or the canned fast path.

    Health no longer builds the graph, so a scripted graph is seeded directly
    on ``app.state`` (as a prior request or the host pre-warm would)."""
    from nyaya_chat import graph as graph_mod
    from nyaya_chat import guardrail as guard_mod
    from nyaya_chat import server as srv

    async def _fake_get_graph():
        return graph, tools or []

    async def _fake_classify(message, settings):
        from nyaya_chat.guardrail import Intent
        return intent or Intent.LEGAL

    monkeypatch.setattr(graph_mod, "get_graph", _fake_get_graph)
    monkeypatch.setattr(srv, "get_graph", _fake_get_graph, raising=False)
    monkeypatch.setattr(guard_mod, "classify_intent", _fake_classify)
    monkeypatch.setattr(srv, "classify_intent", _fake_classify, raising=False)

    app = srv.create_app()
    if graph is not None:
        app.state.graph = graph
        app.state.tools = tools or []
    return app


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


def test_health_degraded_without_building_agent(monkeypatch):
    """With no agent built, /health returns the degraded payload IMMEDIATELY —
    the builder is never called (a cold health probe must not pay the
    seconds-long agent build). The model field is still present for the
    frontend badge."""
    from nyaya_chat import graph as graph_mod
    from nyaya_chat import server as srv

    async def _must_not_build():
        raise AssertionError("health must not trigger graph construction")

    monkeypatch.setattr(graph_mod, "get_graph", _must_not_build)
    monkeypatch.setattr(srv, "get_graph", _must_not_build, raising=False)

    app = srv.create_app()
    with TestClient(app) as c:
        r = c.get("/health")
    body = r.json()
    assert r.status_code == 200
    assert body["status"] == "degraded"
    assert body["tools_loaded"] == 0
    assert body["model"]  # degraded still reports the known model
    assert "initializ" in (body.get("reason") or "")


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
    """The 503 JSON body uses the unified error shape {message, detail, rid}."""
    app = _make_test_app(monkeypatch, graph=None, tools=[])
    with TestClient(app) as c:
        r = c.post("/turn", json={"message": "hi"})
        assert r.status_code == 503
        body = r.json()
        assert body["message"] == "agent_unavailable"
        assert body["detail"] == "chat agent not available"
        assert body["rid"]  # non-blank request id
        assert "error" not in body  # the old `error` key is gone


def test_turn_guardrail_fast_path_unified_shapes(monkeypatch):
    """The canned fast path emits the unified shapes: meta, status with rid,
    token, done — and no citations/correction/error events."""
    from nyaya_chat.guardrail import Intent, get_canned_response

    app = _make_test_app(monkeypatch, graph=_Graph(), tools=["t"], intent=Intent.GREETING)
    with TestClient(app) as c:
        with c.stream("POST", "/turn", json={"message": "hello"}) as r:
            assert r.status_code == 200
            body = b"".join(r.iter_bytes())

    events = _parse_sse(body)
    assert [e for e, _ in events] == ["meta", "status", "status", "token", "done"]
    rid = events[0][1]["request_id"]
    assert rid
    assert r.headers.get("X-Request-ID") == rid
    statuses = [p for e, p in events if e == "status"]
    assert [p["msg"] for p in statuses] == ["analyzing", "composing"]
    assert all(p["rid"] == rid for p in statuses)
    tokens = [p for e, p in events if e == "token"]
    assert tokens == [{"content": get_canned_response(Intent.GREETING)}]
    # The fast path is canned: no verification-derived events, no errors.
    assert not any(e in ("citations", "correction", "error") for e, _ in events)
    assert events[-1] == ("done", {})


def test_turn_streams_sse(monkeypatch):
    """A scripted graph yields custom-stream event payloads; the endpoint
    returns text/event-stream."""
    class _G:
        async def astream(self, _input, **kw):
            yield {"type": "token", "content": "Hello "}
            yield {"type": "token", "content": "world"}

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
        async def astream(self, _input, **kw):
            yield {"type": "token", "content": "ok"}

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
