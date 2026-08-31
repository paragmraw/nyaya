"""Unit tests for db-layer performance features: candidate-pool sizing,
TTL caches for effectively-static data, and text-snippet truncation
(include_text/snippet_chars).

Uses a fake connection (recording executed SQL) patched over
``nyaya.db._conn`` — no live database is needed.
"""

from __future__ import annotations

import contextlib
import re
from typing import Any

import pytest

from nyaya import db, embeddings

# ---------------------------------------------------------------------------
# Helpers — a fake connection that records queries and emulates ``left()``.
# ---------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def fetchall(self) -> list[dict[str, Any]]:
        return self._rows

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _FakeConn:
    """Stand-in for a psycopg connection: records every (sql, params) pair.

    Rows passed as ``doc_rows`` are truncated to ``left(d.text, N)`` when the
    executed SELECT uses that projection, emulating the SQL snippet expression
    so the truncation behaviour is exercised end to end.
    """

    def __init__(self, doc_rows: list[dict[str, Any]] | None = None,
                 count: int | None = None) -> None:
        self.executed: list[tuple[str, Any]] = []
        self._doc_rows = doc_rows or []
        self._count = len(self._doc_rows) if count is None else count

    def execute(self, sql: Any, params: Any = None) -> _FakeResult:
        flat = " ".join(str(sql).split())
        self.executed.append((flat, params))
        if "group by kind" in flat:
            return _FakeResult(self._stats_rows)
        if "count(*)" in flat:
            return _FakeResult([{"n": self._count}])
        m = re.search(r"left\(d\.text,\s*(\d+)\)", flat)
        if m:
            n = int(m.group(1))
            return _FakeResult([{**r, "text": r["text"][:n]} for r in self._doc_rows])
        return _FakeResult(list(self._doc_rows))

    # For ``select kind, count(*) ... group by kind`` (corpus_stats).
    _stats_rows: list[dict[str, Any]] = [{"kind": "section", "n": 2}]


@pytest.fixture(autouse=True)
def _fresh_db_caches(monkeypatch):
    """Give every test its own TTL caches (module-level caches are shared)."""
    monkeypatch.setattr(db, "_LIST_ACTS_CACHE", db._LockedTTLCache(ttl=300.0, maxsize=1))
    monkeypatch.setattr(db, "_LIST_SCHEDULES_CACHE", db._LockedTTLCache(ttl=300.0, maxsize=1))
    monkeypatch.setattr(db, "_LIST_AMENDMENTS_CACHE", db._LockedTTLCache(ttl=300.0, maxsize=64))
    monkeypatch.setattr(db, "_CORPUS_STATS_CACHE", db._LockedTTLCache(ttl=60.0, maxsize=1))


def _act_row(i: int = 0) -> dict[str, Any]:
    return {
        "short_name": "IPC", "full_name": "The Indian Penal Code, 1860",
        "year": 1860, "citation": "Act No. 45 of 1860", "kind": "criminal",
        "source": "PRS (CC BY 4.0)", "source_license": "CC BY 4.0", "as_of": None,
    }


def _doc_row(i: int = 0, kind: str = "judgment") -> dict[str, Any]:
    # Keys cover both the document SELECTs (list_*) and the ANN SELECT
    # (semantic_search): act/rank/snippet/citation are SearchResult fields.
    return {
        "id": "00000000-0000-0000-0000-000000000001",
        "kind": kind, "ref": "302", "title": f"Doc {i}",
        "text": "x" * 500, "metadata": {},
        "act_short_name": "IPC", "act": "IPC",
        "rank": 0.9, "snippet": "x" * 500, "citation": "Act No. 45 of 1860",
        "as_of": None,
    }


# ---------------------------------------------------------------------------
# Item 2 — candidate pool sizing (rerank_search)
# ---------------------------------------------------------------------------

def _run_rerank_search(monkeypatch, limit: int) -> tuple[_FakeConn, Any, Any, Any]:
    conn = _FakeConn([_doc_row(0), _doc_row(1)])
    monkeypatch.setattr(db, "_conn", lambda: contextlib.nullcontext(conn))
    monkeypatch.setattr(embeddings, "embed_query", lambda q: [0.1] * 2048)
    monkeypatch.setattr(embeddings, "rerank_query", lambda q, cands: [1.0] * len(cands))
    results, total, fallback = db.rerank_search("murder", limit=limit)
    return conn, results, total, fallback


def test_rerank_pool_small_limit(monkeypatch):
    """limit=5 sizes the ANN candidate pool to min(3*5, 50) = 15."""
    conn, _results, _total, _fallback = _run_rerank_search(monkeypatch, 5)
    ann_sql, ann_params = conn.executed[0]
    assert "limit %s offset %s" in ann_sql
    assert ann_params[-2] == 15


def test_rerank_pool_large_limit_clamped(monkeypatch):
    """limit=20 sizes the pool to min(3*20, 50) = 50 (the historic value)."""
    conn, _results, _total, _fallback = _run_rerank_search(monkeypatch, 20)
    _sql, ann_params = conn.executed[0]
    assert ann_params[-2] == 50


def test_rerank_search_results_unaffected_by_pool_change(monkeypatch):
    """Results, total, and no fallback are unaffected by the pool change."""
    _conn, results, total, fallback = _run_rerank_search(monkeypatch, 5)
    assert len(results) == 2
    assert total == 2
    assert fallback is None


# ---------------------------------------------------------------------------
# Item 3 — TTL caches for effectively-static data
# ---------------------------------------------------------------------------

def test_corpus_stats_cached_within_ttl(monkeypatch):
    """N calls within the TTL produce 1 DB round-trip (3 queries)."""
    conn = _FakeConn()
    monkeypatch.setattr(db, "_conn", lambda: contextlib.nullcontext(conn))
    first = db.corpus_stats()
    second = db.corpus_stats()
    assert first == second
    # 1 round-trip = 3 queries (documents by kind, cross_refs, acts).
    assert len(conn.executed) == 3


def test_corpus_stats_cache_expires(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(db.time, "monotonic", lambda: now[0])
    conn = _FakeConn()
    monkeypatch.setattr(db, "_conn", lambda: contextlib.nullcontext(conn))

    db.corpus_stats()
    assert len(conn.executed) == 3
    now[0] += 61.0  # past the 60s TTL
    db.corpus_stats()
    assert len(conn.executed) == 6


def test_list_acts_cached_within_ttl(monkeypatch):
    conn = _FakeConn([_act_row()])
    monkeypatch.setattr(db, "_conn", lambda: contextlib.nullcontext(conn))
    acts = db.list_acts()
    assert [a.short_name for a in acts] == ["IPC"]
    db.list_acts()
    db.list_acts()
    assert len(conn.executed) == 1  # N calls -> 1 DB round-trip


def test_list_acts_cache_expires(monkeypatch):
    now = [1000.0]
    monkeypatch.setattr(db.time, "monotonic", lambda: now[0])
    conn = _FakeConn([_act_row()])
    monkeypatch.setattr(db, "_conn", lambda: contextlib.nullcontext(conn))

    db.list_acts()
    assert len(conn.executed) == 1
    now[0] += 301.0  # past the 5-min TTL
    db.list_acts()
    assert len(conn.executed) == 2


def test_list_schedules_cached(monkeypatch):
    conn = _FakeConn([_doc_row(0, kind="schedule")])
    monkeypatch.setattr(db, "_conn", lambda: contextlib.nullcontext(conn))
    docs = db.list_schedules()
    db.list_schedules()
    assert len(docs) == 1
    assert len(conn.executed) == 1


def test_list_amendments_keyed_by_year_range(monkeypatch):
    """Distinct (year_from, year_to) keys miss; a repeated key hits."""
    conn = _FakeConn([_doc_row(0, kind="amendment")])
    monkeypatch.setattr(db, "_conn", lambda: contextlib.nullcontext(conn))

    db.list_amendments(1950, 2000)
    db.list_amendments()
    assert len(conn.executed) == 2  # distinct filter args -> separate queries
    db.list_amendments(year_from=1950, year_to=2000)
    assert len(conn.executed) == 2  # repeated key -> cache hit


def test_db_failures_are_not_cached(monkeypatch):
    """A failing round-trip is not cached; a later call retries the DB."""

    class _BoomConn:
        def execute(self, sql: Any, params: Any = None) -> _FakeResult:
            raise RuntimeError("db down")

    monkeypatch.setattr(db, "_conn", lambda: contextlib.nullcontext(_BoomConn()))
    with pytest.raises(RuntimeError):
        db.list_acts()

    conn = _FakeConn([_act_row()])
    monkeypatch.setattr(db, "_conn", lambda: contextlib.nullcontext(conn))
    acts = db.list_acts()
    # The failed call must not have poisoned the cache: the good call hit the DB.
    assert [a.short_name for a in acts] == ["IPC"]
    assert len(conn.executed) == 1


# ---------------------------------------------------------------------------
# Item 5 — include_text / snippet truncation
# ---------------------------------------------------------------------------

def test_list_judgments_default_returns_snippets(monkeypatch):
    """Default (include_text=False) selects left(d.text, 300) -> bounded text."""
    conn = _FakeConn([_doc_row(0), _doc_row(1)])
    monkeypatch.setattr(db, "_conn", lambda: contextlib.nullcontext(conn))
    docs, total = db.list_judgments(limit=2)
    docs_sql, _params = conn.executed[0]
    assert "left(d.text, 300)" in docs_sql
    assert all(len(d.text) == 300 for d in docs)
    assert total == 2


def test_list_judgments_include_text_returns_full(monkeypatch):
    conn = _FakeConn([_doc_row(0)])
    monkeypatch.setattr(db, "_conn", lambda: contextlib.nullcontext(conn))
    docs, _total = db.list_judgments(limit=1, include_text=True)
    docs_sql, _params = conn.executed[0]
    assert "left(d.text" not in docs_sql
    assert docs[0].text == "x" * 500


def test_list_sections_snippet_projection(monkeypatch):
    conn = _FakeConn([_doc_row(0, kind="section")])
    monkeypatch.setattr(db, "_conn", lambda: contextlib.nullcontext(conn))
    db.list_sections("IPC", limit=1, include_text=False, snippet_chars=120)
    docs_sql, _params = conn.executed[0]
    assert "left(d.text, 120)" in docs_sql


def test_list_articles_include_text(monkeypatch):
    conn = _FakeConn([_doc_row(0, kind="article")])
    monkeypatch.setattr(db, "_conn", lambda: contextlib.nullcontext(conn))
    db.list_articles(part="Part III", include_text=True)
    docs_sql, _params = conn.executed[0]
    assert "left(d.text" not in docs_sql


def test_snippet_expr_clamped():
    """snippet_chars is clamped server-side before SQL interpolation."""
    assert db._snippet_expr(0) == "left(d.text, 1)"
    assert db._snippet_expr(5000) == "left(d.text, 2000)"
    assert db._snippet_expr(300) == "left(d.text, 300)"
