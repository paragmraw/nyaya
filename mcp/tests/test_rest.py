"""Contract tests for the REST endpoints in ``nyaya.rest``.

These endpoints serve the web SPA with plain JSON GETs. The db layer is
faked (no live database), and the real ``nyaya.server.app`` is booted via
``TestClient`` so the middleware stack (request ids, security headers, CORS,
rate limiting) is exercised exactly as in production.

Covers:
- happy-path response shapes (/api/corpus-stats, /api/acts, /api/judgments,
  /api/health-summary, /api/tools)
- input validation (non-integer limit/offset -> 400)
- the degraded fallback of /api/health-summary (DB down -> 200 + partial data)
- the safe error contract (DB down on other endpoints -> 503 database_unavailable
  with a request_id; unexpected errors -> 500 internal_error, no internals leaked)
- the collapsed stats/health-summary helper keeps both endpoint shapes
"""

from __future__ import annotations

from typing import Any

import pytest
from starlette.testclient import TestClient

pytestmark = pytest.mark.integration

from nyaya.server import app  # noqa: E402

# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

_ACT = {
    "short_name": "IPC", "full_name": "The Indian Penal Code, 1860",
    "year": 1860, "citation": "Act No. 45 of 1860", "kind": "criminal",
    "source": "PRS (CC BY 4.0)", "source_license": "CC BY 4.0", "as_of": None,
}


def _stats() -> dict[str, int]:
    return {"acts": 1, "section": 2, "article": 3, "judgment": 4,
            "amendment": 5, "schedule": 6, "cross_refs": 7}


def _fake_db(monkeypatch, *, stats=None, as_of=None, fail_stats=False):
    from datetime import date

    from nyaya import db
    from nyaya.exceptions import DatabaseUnavailable

    stats = stats if stats is not None else _stats()
    as_of = as_of if as_of is not None else date(2026, 7, 1)

    def corpus_stats():
        if fail_stats:
            raise DatabaseUnavailable("DB is down")
        return dict(stats)

    def corpus_as_of():
        if fail_stats:
            raise DatabaseUnavailable("DB is down")
        return as_of

    monkeypatch.setattr(db, "corpus_stats", corpus_stats)
    monkeypatch.setattr(db, "corpus_as_of", corpus_as_of)
    return db


# ---------------------------------------------------------------------------
# Happy paths
# ---------------------------------------------------------------------------

def test_corpus_stats_shape(monkeypatch):
    """GET /api/corpus-stats -> {counts, as_of} and no status key."""
    db = _fake_db(monkeypatch)

    with TestClient(app) as client:
        r = client.get("/api/corpus-stats")
    assert r.status_code == 200
    body = r.json()
    assert body["counts"] == _stats()
    assert body["as_of"] == "2026-07-01"
    assert "status" not in body
    assert db  # imported for the monkeypatch side effects


def test_acts_endpoint_serializes_models(monkeypatch):
    """GET /api/acts returns a JSON list of the Act models."""
    from nyaya import db
    from nyaya.models import Act

    monkeypatch.setattr(db, "list_acts", lambda: [Act(**_ACT)])


    with TestClient(app) as client:
        r = client.get("/api/acts")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list) and len(body) == 1
    assert body[0]["short_name"] == "IPC"
    assert body[0]["kind"] == "criminal"


def test_health_summary_degrades_when_db_down(monkeypatch):
    """DB failure -> 200 with status=degraded and empty counts (SPA renders
    partial numbers instead of an error)."""
    _fake_db(monkeypatch, fail_stats=True)

    with TestClient(app) as client:
        r = client.get("/api/health-summary")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "degraded"
    assert body["counts"] == {}
    assert body["as_of"] is None


def test_health_summary_healthy_shape(monkeypatch):
    """Healthy DB -> {status, counts, as_of}."""
    _fake_db(monkeypatch)

    with TestClient(app) as client:
        r = client.get("/api/health-summary")
    assert r.status_code == 200
    body = r.json()
    assert body == {
        "status": "healthy",
        "counts": _stats(),
        "as_of": "2026-07-01",
    }


def test_corpus_stats_db_down_is_503_with_request_id(monkeypatch):
    """The safe error contract: DB down -> 503 {error: database_unavailable,
    request_id} and an X-Request-ID header from the middleware."""
    _fake_db(monkeypatch, fail_stats=True)

    with TestClient(app) as client:
        r = client.get("/api/corpus-stats")
    assert r.status_code == 503
    body = r.json()
    assert body["error"] == "database_unavailable"
    assert body.get("request_id")
    assert r.headers.get("x-request-id") == body["request_id"]


def test_internal_error_is_500_without_leaked_details(monkeypatch):
    """An unexpected exception -> 500 {error: internal_error} — the exception
    text must NOT reach the client."""
    from nyaya import db

    def _boom():
        raise RuntimeError("SECRET postgres DSN leaked in traceback")

    monkeypatch.setattr(db, "corpus_stats", _boom)

    with TestClient(app) as client:
        r = client.get("/api/corpus-stats")
    assert r.status_code == 500
    body = r.json()
    assert body["error"] == "internal_error"
    assert "SECRET" not in r.text


def test_judgments_rejects_non_integer_limit(monkeypatch):
    """Bad pagination input -> 400 bad_request (not a 500)."""
    _fake_db(monkeypatch)

    with TestClient(app) as client:
        r = client.get("/api/judgments?limit=abc")
        assert r.status_code == 400
        assert r.json()["error"] == "bad_request"

        r = client.get("/api/judgments?offset=1.5")
        assert r.status_code == 400


def test_judgments_clamps_limit_and_offset(monkeypatch):
    """/api/judgments clamps limit to [1, 200] and offset to >= 0, and passes
    them through to db.list_judgments."""
    from nyaya import db
    from nyaya.models import Document

    captured: dict[str, Any] = {}

    def fake_list_judgments(limit=50, offset=0, include_text=False, snippet_chars=300):
        # Mirror db.list_judgments' SQL-level projection: full text only when
        # include_text is set, else a snippet_chars-bounded text.
        captured.update(limit=limit, offset=offset, include_text=include_text)
        body_text = "t" * 400 if include_text else "t" * snippet_chars
        doc = Document(kind="judgment", ref="AIR 1973 SC 1461", title="Kesavananda",
                       text=body_text, metadata={}, source="PRS (CC BY 4.0)")
        return [doc], 1

    monkeypatch.setattr(db, "list_judgments", fake_list_judgments)

    with TestClient(app) as client:
        r = client.get("/api/judgments?limit=9999&offset=-5")
    assert r.status_code == 200
    body = r.json()
    assert captured["limit"] == 200
    assert captured["offset"] == 0
    assert captured["include_text"] is False
    assert body["total"] == 1
    assert body["limit"] == 200
    assert body["offset"] == 0
    assert len(body["items"][0]["text"]) == 300  # snippet by default

    # ?full=1 opts into the full text projection.
    with TestClient(app) as client:
        r_full = client.get("/api/judgments?full=1")
        assert r_full.status_code == 200
        assert r_full.json()["items"][0]["text"] == "t" * 400


def test_tools_endpoint_lists_registered_tools():
    """GET /api/tools introspects the MCP tool surface (16 tools, named)."""

    with TestClient(app) as client:
        r = client.get("/api/tools")
    assert r.status_code == 200
    body = r.json()
    assert body["total"] == 16
    names = {item["name"] for item in body["items"]}
    assert {"semantic_query", "get_section", "get_article", "get_judgment",
            "list_acts", "cross_reference", "corpus_stats"} <= names
    assert all(item["description"] for item in body["items"])
