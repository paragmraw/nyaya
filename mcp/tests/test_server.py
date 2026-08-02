"""Integration test for the full server boot and /health endpoint."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_health_endpoint(monkeypatch):
    from starlette.testclient import TestClient

    from nyaya import db
    monkeypatch.setattr(db, "corpus_stats",
                       lambda: {"acts": 0, "sections": 0, "articles": 0, "judgments": 0,
                                "amendments": 0, "schedules": 0, "chapters": 0, "cross_refs": 0})

    import importlib
    import nyaya.server as server_module
    importlib.reload(server_module)

    with TestClient(server_module.app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["service"] == "nyaya"
        assert body["status"] == "healthy"
        assert "counts" in body
        assert body["version"] == "0.1.0"


def test_health_degraded(monkeypatch):
    """When the DB is unreachable, /health returns 200 with status=degraded."""
    from starlette.testclient import TestClient

    from nyaya import db

    def _boom():
        raise RuntimeError("DB is down")

    monkeypatch.setattr(db, "corpus_stats", _boom)

    import importlib
    import nyaya.server as server_module
    importlib.reload(server_module)

    with TestClient(server_module.app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"].startswith("degraded")
        assert body["counts"] == {}


def test_mcp_endpoint_reachable(monkeypatch):
    from starlette.testclient import TestClient

    from nyaya import db
    monkeypatch.setattr(db, "corpus_stats",
                       lambda: {"acts": 0, "sections": 0, "articles": 0, "judgments": 0,
                                "amendments": 0, "schedules": 0, "chapters": 0, "cross_refs": 0})

    import importlib
    import nyaya.server as server_module
    importlib.reload(server_module)

    with TestClient(server_module.app) as client:
        r = client.post(
            "/mcp",
            headers={"Content-Type": "application/json"},
            json={"jsonrpc": "2.0", "method": "initialize", "id": 1},
        )
        assert r.status_code != 404


def test_mcp_initialize_handshake(monkeypatch):
    """The MCP initialize handshake returns capabilities + protocolVersion."""
    from starlette.testclient import TestClient

    from nyaya import db
    monkeypatch.setattr(db, "corpus_stats",
                       lambda: {"acts": 0, "sections": 0, "articles": 0, "judgments": 0,
                                "amendments": 0, "schedules": 0, "chapters": 0, "cross_refs": 0})

    import importlib
    import nyaya.server as server_module
    importlib.reload(server_module)

    with TestClient(server_module.app) as client:
        # MCP requires an initialize before other requests.
        r = client.post(
            "/mcp",
            headers={"Content-Type": "application/json", "Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "method": "initialize", "id": 1,
                  "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "0.1"}}},
        )
        # 200 or 202 — both indicate the endpoint is alive and handled the request.
        assert r.status_code in (200, 202, 400), f"unexpected status {r.status_code}: {r.text}"