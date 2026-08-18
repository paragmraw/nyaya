"""Integration test for the full server boot and /health endpoint."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

# server.py builds ``app`` at module level (middleware is imported unconditionally),
# so a plain import gives us the ready app — no importlib.reload needed.
from nyaya.server import app  # noqa: E402

_EMPTY_STATS = {
    "acts": 0, "section": 0, "article": 0, "judgment": 0,
    "amendment": 0, "schedule": 0, "cross_refs": 0,
}


def test_health_endpoint(monkeypatch):
    from starlette.testclient import TestClient

    from nyaya import db
    monkeypatch.setattr(db, "corpus_stats", lambda: dict(_EMPTY_STATS))

    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["service"] == "nyaya"
        assert body["status"] == "healthy"
        assert "counts" in body
        assert body["version"] == "0.2.0"


def test_health_degraded(monkeypatch):
    """When the DB is unreachable, /health returns 200 with status=degraded."""
    from starlette.testclient import TestClient

    from nyaya import db
    from nyaya.exceptions import DatabaseUnavailable

    def _boom():
        raise DatabaseUnavailable("DB is down")

    monkeypatch.setattr(db, "corpus_stats", _boom)

    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"].startswith("degraded")
        assert body["counts"] == {}


def test_mcp_endpoint_reachable(monkeypatch):
    from starlette.testclient import TestClient

    from nyaya import db
    monkeypatch.setattr(db, "corpus_stats", lambda: dict(_EMPTY_STATS))

    with TestClient(app) as client:
        r = client.post(
            "/mcp",
            headers={"Content-Type": "application/json"},
            json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
        )
        assert r.status_code != 404


def test_mcp_initialize_handshake(monkeypatch):
    """The MCP initialize handshake returns a non-error status."""
    from starlette.testclient import TestClient

    from nyaya import db
    monkeypatch.setattr(db, "corpus_stats", lambda: dict(_EMPTY_STATS))

    with TestClient(app) as client:
        r = client.post(
            "/mcp",
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "method": "initialize", "id": 1,
                  "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "0.1"}}},
        )
        assert r.status_code in (200, 202, 400), f"unexpected status {r.status_code}: {r.text}"


def test_cors_headers(monkeypatch):
    """CORS middleware adds Access-Control-Allow-Origin for allowed origins."""
    from starlette.testclient import TestClient

    from nyaya import db
    monkeypatch.setattr(db, "corpus_stats", lambda: dict(_EMPTY_STATS))

    with TestClient(app) as client:
        r = client.get("/health", headers={"Origin": "https://nyaya.parag.tech"})
        assert r.status_code == 200
        assert "access-control-allow-origin" in {k.lower() for k in r.headers}


def test_lifespan_close_db_called(monkeypatch):
    """On shutdown, db.close_db() is called by the lifespan handler."""
    from starlette.testclient import TestClient

    from nyaya import db
    monkeypatch.setattr(db, "corpus_stats", lambda: dict(_EMPTY_STATS))

    closed = {"called": False}
    original_close = db.close_db

    def _spy_close():
        closed["called"] = True
        original_close()

    monkeypatch.setattr(db, "close_db", _spy_close)

    with TestClient(app) as client:
        client.get("/health")
    assert closed["called"], "db.close_db was not called on shutdown"
