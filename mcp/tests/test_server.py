"""Integration test for the full server boot and /health endpoint."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration


def test_health_endpoint(monkeypatch):
    from starlette.testclient import TestClient

    from nyaya import db
    monkeypatch.setattr(db, "corpus_stats", lambda: {"acts": 0, "sections": 0, "articles": 0, "judgments": 0, "amendments": 0, "schedules": 0})

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


def test_mcp_endpoint_reachable(monkeypatch):
    from starlette.testclient import TestClient

    from nyaya import db
    monkeypatch.setattr(db, "corpus_stats", lambda: {"acts": 0, "sections": 0, "articles": 0, "judgments": 0, "amendments": 0, "schedules": 0})

    import importlib
    import nyaya.server as server_module
    importlib.reload(server_module)

    with TestClient(server_module.app) as client:
        r = client.post("/mcp", headers={"Content-Type": "application/json"}, json={"jsonrpc": "2.0", "method": "initialize", "id": 1})
        assert r.status_code != 404