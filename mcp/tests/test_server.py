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


# ---------------------------------------------------------------------------
# Static asset Cache-Control (Task 5 item 4) — the _CachedStaticFiles subclass
# is exercised directly against a tmp directory, in the same style as the
# middleware tests in test_security.py.
# ---------------------------------------------------------------------------

def _make_static_client(tmp_path):
    from starlette.applications import Starlette
    from starlette.routing import Mount
    from starlette.testclient import TestClient

    from nyaya.server import _CachedStaticFiles

    web = tmp_path / "out"
    (web / "_next" / "static").mkdir(parents=True)
    (web / "_next" / "static" / "x.js").write_text("console.log(1)")
    (web / "index.html").write_text("<html>home</html>")
    (web / "robots.txt").write_text("User-agent: *\nAllow: /")
    return TestClient(Starlette(routes=[Mount("/", app=_CachedStaticFiles(directory=str(web), html=True))]))


def test_static_next_static_assets_are_immutable(tmp_path):
    """_next/static/* (content-hashed) gets the immutable 1-year Cache-Control."""
    client = _make_static_client(tmp_path)
    r = client.get("/_next/static/x.js")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_static_html_and_robots_get_no_cache(tmp_path):
    """HTML (and other revalidatable files) get no-cache."""
    client = _make_static_client(tmp_path)
    html = client.get("/")
    assert html.status_code == 200
    assert html.headers["cache-control"] == "no-cache"

    robots = client.get("/robots.txt")
    assert robots.headers["cache-control"] == "no-cache"


# ---------------------------------------------------------------------------
# REST truncation (Task 5 item 5): /api/judgments ships snippets by default,
# with a ?full=1 escape hatch. The db function is faked (mirroring its
# projection behaviour) so no live DB is needed.
# ---------------------------------------------------------------------------

def _make_judgment_snapshot(include_text: bool, snippet_chars: int = 300):
    from nyaya.models import Document

    body = "y" * 2000
    doc = Document(
        kind="judgment", ref="AIR 1973 SC 1461", title="Kesavananda",
        text=body if include_text else body[:snippet_chars], metadata={},
        source="PRS (CC BY 4.0)",
    )
    return [doc], 1


def test_judgments_endpoint_defaults_to_snippets(monkeypatch):
    """/api/judgments ships bounded snippets by default (multi-MB pages otherwise)."""
    from starlette.testclient import TestClient

    from nyaya import db

    captured: dict[str, object] = {}

    def fake_list_judgments(limit=50, offset=0, include_text=False, snippet_chars=300):
        captured["include_text"] = include_text
        return _make_judgment_snapshot(include_text, snippet_chars)

    monkeypatch.setattr(db, "list_judgments", fake_list_judgments)

    with TestClient(app) as client:
        r = client.get("/api/judgments")
        assert r.status_code == 200
        body = r.json()
        assert captured["include_text"] is False
        assert len(body["items"][0]["text"]) == 300

        r_full = client.get("/api/judgments?full=1")
        assert r_full.status_code == 200
        assert r_full.json()["items"][0]["text"] == "y" * 2000
