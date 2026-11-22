"""Integration tests for Nyaya v2.0 HTTP server boot, /health, /mcp handshake, and static asset caching."""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from nyaya import storage
from nyaya.exceptions import StorageUnavailable
from nyaya.http_app import _CachedStaticFiles, create_http_app
from tests.test_tools_contract import _build_rich_memory_backend

pytestmark = pytest.mark.integration


def test_health_endpoint():
    backend = _build_rich_memory_backend()
    app = create_http_app(backend=backend)
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["service"] == "nyaya"
        assert body["status"] == "healthy"
        assert body["version"] == "2.0.0"
        assert "counts" in body
        assert "backends" in body


def test_health_degraded(monkeypatch):
    backend = _build_rich_memory_backend()
    app = create_http_app(backend=backend)

    def _boom(**_kwargs):
        raise StorageUnavailable("sqlite core missing")

    monkeypatch.setattr(backend, "corpus_status", _boom)

    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "degraded"
        assert body["counts"] == {}


def test_mcp_endpoint_reachable_and_initialize_handshake():
    backend = _build_rich_memory_backend()
    app = create_http_app(backend=backend)
    with TestClient(app) as client:
        r = client.post(
            "/mcp",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "method": "initialize",
                "id": 1,
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "2.0.0"},
                },
            },
        )
        assert r.status_code in (200, 202, 400), f"unexpected status {r.status_code}: {r.text}"


def test_cors_headers():
    backend = _build_rich_memory_backend()
    app = create_http_app(backend=backend)
    with TestClient(app) as client:
        r = client.get("/health", headers={"Origin": "https://nyaya.parag.tech"})
        assert r.status_code == 200
        assert "access-control-allow-origin" in {k.lower() for k in r.headers}


def test_lifespan_aclose_all_called(monkeypatch):
    """On shutdown, storage.aclose_all() is called by the lifespan handler."""
    backend = _build_rich_memory_backend()
    app = create_http_app(backend=backend)

    closed = {"called": False}
    orig_aclose = storage.aclose_all

    async def _spy_aclose():
        closed["called"] = True
        await orig_aclose()

    monkeypatch.setattr(storage, "aclose_all", _spy_aclose)

    with TestClient(app) as client:
        client.get("/health")
    assert closed["called"], "storage.aclose_all was not called on shutdown"


def _make_static_client(tmp_path):
    web = tmp_path / "out"
    (web / "_next" / "static").mkdir(parents=True)
    (web / "_next" / "static" / "x.js").write_text("console.log(1)")
    (web / "index.html").write_text("<html>home</html>")
    (web / "robots.txt").write_text("User-agent: *\nAllow: /")
    return TestClient(
        Starlette(routes=[Mount("/", app=_CachedStaticFiles(directory=str(web), html=True))])
    )


def test_static_next_static_assets_are_immutable(tmp_path):
    client = _make_static_client(tmp_path)
    r = client.get("/_next/static/x.js")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_static_html_and_robots_get_no_cache(tmp_path):
    client = _make_static_client(tmp_path)
    html = client.get("/")
    assert html.status_code == 200
    assert html.headers["cache-control"] == "no-cache"

    robots = client.get("/robots.txt")
    assert robots.headers["cache-control"] == "no-cache"
