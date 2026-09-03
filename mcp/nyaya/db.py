"""Postgres data access for nyaya — v0.2 unified documents schema.

All functions are synchronous and intended to be wrapped with
``asyncio.to_thread`` by the (async) tool layer — see ``tools/_util.py``.

Design notes
------------
* The v0.2 schema collapses per-kind tables into a single ``documents`` table
  with a ``kind`` discriminator and ``metadata jsonb`` for per-kind fields.
* Retrieval is embedding-based + reranker (NVIDIA API). FTS is removed.
* Input normalization: act/ref strings are stripped and alias-resolved;
  act lookups are case-insensitive at the SQL level.
"""

from __future__ import annotations

import contextlib
import logging
import re
import threading
import time
from collections.abc import Iterator
from datetime import date
from typing import Any

import psycopg
import psycopg_pool
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import SNIPPET_CHARS, get_settings
from .exceptions import DatabaseUnavailable
from .models import Act, CrossRef, CrossRefDirection, Document, SearchResult
from .sanitize import BIDI_RE

# Fallback provenance date used when an act row lacks a current ``as_of``.
CORPUS_AS_OF = date(2026, 7, 1)

# Maps common act names / case variants to the canonical short_name stored
# in the ``acts`` table. Lookup is case-insensitive.
_ACT_ALIASES: dict[str, str] = {
    "ipc": "IPC",
    "indian penal code": "IPC",
    "penal code": "IPC",
    "crpc": "CrPC",
    "code of criminal procedure": "CrPC",
    "criminal procedure code": "CrPC",
    "cpc": "CPC",
    "code of civil procedure": "CPC",
    "civil procedure code": "CPC",
    "iea": "EvidenceAct",
    "evidence act": "EvidenceAct",
    "indian evidence act": "EvidenceAct",
    "evidenceact": "EvidenceAct",
    "bns": "BNS",
    "bharatiya nyaya sanhita": "BNS",
    "nyaya sanhita": "BNS",
    "bnss": "BNSS",
    "bharatiya nagarik suraksha sanhita": "BNSS",
    "nagarik suraksha sanhita": "BNSS",
    "bsa": "BSA",
    "bharatiya sakshya adhiniyam": "BSA",
    "bharatiya sakshya bill": "BSA",
    "sakshya adhiniyam": "BSA",
    "companies": "Companies",
    "companies act": "Companies",
    "igst": "IGST",
    "integrated goods and services tax": "IGST",
    "cgst": "CGST",
    "central goods and services tax": "CGST",
    "gst": "CGST",
    "itact": "ITAct",
    "information technology act": "ITAct",
    "it act": "ITAct",
    "arbitration": "Arbitration",
    "arbitration and conciliation act": "Arbitration",
    "consumerprotection": "ConsumerProtection",
    "consumer protection act": "ConsumerProtection",
    "constitution": "Constitution",
    "the constitution": "Constitution",
    "article": "Constitution",
    "articles": "Constitution",
    "judgment": "judgment",
    "judgments": "judgment",
    "case": "judgment",
    "cases": "judgment",
}

_REF_PREFIX_RE = re.compile(
    r"^(?:s(?:ec(?:tion)?)?\.?|art(?:icle)?\.?)\s*",
    re.IGNORECASE,
)

# Definition-promotion regex (single source of truth — do not recompile
# elsewhere; used by ``rerank_search`` for ``promote_definitions`` and matches
# titles like IPC s.2 'Definitions' / 'Interpretation' clauses).
_DEF_RE = re.compile(r"defin|interpret", re.IGNORECASE)

# The pool is parameterised with the dict-row connection type so every pooled
# connection yields ``dict`` rows (psycopg's default is tuple rows).
_pool: ConnectionPool[psycopg.Connection[dict[str, Any]]] | None = None
_pool_lock = threading.Lock()

_as_of_cache: tuple[float, date | None] = (0.0, None)
_AS_OF_TTL = 300.0
# Guards _as_of_cache, which is read/written from asyncio.to_thread workers
# alongside the sync DB code (see _pool_lock for the established pattern).
_as_of_lock = threading.Lock()


class _LockedTTLCache:
    """A minimal thread-safe TTL cache keyed on hashable tuples.

    Reads and writes both run under the lock (the ``_as_of_lock`` pattern from
    Task 2); the DB round-trip itself stays outside the lock, so a slow
    refresh never blocks concurrent cache hits.

    ``get`` returns ``_MISS`` for absent/expired keys. Eviction is insertion
    order (oldest entry dropped) once ``maxsize`` is reached; values are
    treated as immutable by callers.
    """

    def __init__(self, ttl: float, maxsize: int = 128) -> None:
        self._lock = threading.Lock()
        self._ttl = ttl
        self._maxsize = maxsize
        self._store: dict[Any, tuple[float, Any]] = {}

    def get(self, key: Any) -> Any:
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return _MISS
            ts, value = entry
            if now - ts >= self._ttl:
                del self._store[key]
                return _MISS
            return value

    def set(self, key: Any, value: Any) -> None:
        now = time.monotonic()
        with self._lock:
            if len(self._store) >= self._maxsize:
                # Drop the oldest entry (dicts preserve insertion order).
                oldest = next(iter(self._store))
                del self._store[oldest]
            self._store[key] = (now, value)


_MISS = object()

# Cache keys for the argument-free list functions (single slot each).
_LIST_ACTS_KEY = "list_acts"
_LIST_SCHEDULES_KEY = "list_schedules"

# Effectively-static data (frozen corpus snapshot) cached with TTL-only
# invalidation — no manual invalidation API, matching the brief.
_LIST_TTL_S = 300.0
_LIST_ACTS_CACHE: _LockedTTLCache = _LockedTTLCache(ttl=_LIST_TTL_S, maxsize=1)
_LIST_SCHEDULES_CACHE: _LockedTTLCache = _LockedTTLCache(ttl=_LIST_TTL_S, maxsize=1)
# Keyed by (year_from, year_to): the filter space is small and enumerable
# (integer years), so caching full returns per key is safe and bounded.
_LIST_AMENDMENTS_CACHE: _LockedTTLCache = _LockedTTLCache(ttl=_LIST_TTL_S, maxsize=64)

# corpus_stats is hit by the Railway healthcheck every 30s; a 60s TTL keeps
# it to one DB round-trip per minute.
_CORPUS_STATS_TTL_S = 60.0
_CORPUS_STATS_CACHE: _LockedTTLCache = _LockedTTLCache(ttl=_CORPUS_STATS_TTL_S, maxsize=1)
_CORPUS_STATS_KEY = "corpus_stats"


log = logging.getLogger("nyaya.db")


# ---------------------------------------------------------------------------
# Input normalization
# ---------------------------------------------------------------------------

def normalize_act(act: str | None) -> str | None:
    """Normalize an act short-name: strip, resolve aliases (case-insensitive)."""
    if act is None:
        return None
    key = BIDI_RE.sub("", act).strip()
    if not key:
        return None
    low = key.lower()
    if low in _ACT_ALIASES:
        return _ACT_ALIASES[low]
    return key


def normalize_ref(ref: str | None) -> str | None:
    """Strip whitespace and a leading 's.'/'section'/'art.'/'article' prefix."""
    if ref is None:
        return None
    r = ref.strip()
    if not r:
        return None
    r = _REF_PREFIX_RE.sub("", r).strip()
    return r if r else None


def _escape_like(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

def _get_pool() -> ConnectionPool[psycopg.Connection[dict[str, Any]]]:
    global _pool
    with _pool_lock:
        if _pool is None or _pool.closed:
            settings = get_settings()

            def _configure(conn: psycopg.Connection[dict[str, Any]]) -> None:
                conn.autocommit = True
                try:
                    if settings.statement_timeout_ms > 0:
                        with conn.cursor() as cur:
                            cur.execute(f"set statement_timeout = {int(settings.statement_timeout_ms)}")
                    with conn.cursor() as cur:
                        cur.execute("set application_name = 'nyaya'")
                finally:
                    conn.autocommit = False

            _pool = ConnectionPool(
                conninfo=settings.database_url,
                min_size=settings.pool_min,
                max_size=settings.pool_max,
                timeout=settings.pool_timeout,
                open=True,
                configure=_configure,
                check=ConnectionPool.check_connection,
            )
    return _pool


@contextlib.contextmanager
def _conn() -> Iterator[psycopg.Connection[dict[str, Any]]]:
    """Yield a pooled connection whose rows come back as dicts.

    The ``Connection[dict[str, Any]]`` parameterisation is what makes
    ``cursor.execute(...).fetchone()`` type as ``dict[str, Any] | None`` for
    mypy — ``conn.row_factory = dict_row`` alone does not propagate through
    the pool's generic, which is why every row access below used to need a
    module-wide suppression.
    """
    try:
        pool = _get_pool()
        with pool.connection(timeout=get_settings().pool_timeout) as conn:
            conn.row_factory = dict_row
            yield conn
    except psycopg_pool.PoolTimeout as e:
        raise DatabaseUnavailable(
            "Could not acquire a database connection in time.",
            hint="Check DATABASE_URL, pool size, and Supabase status.",
        ) from e
    except psycopg.OperationalError as e:
        raise DatabaseUnavailable(
            f"Database connection failed: {e}",
            hint="Check DATABASE_URL and Supabase availability.",
        ) from e


def close_db() -> None:
    global _pool
    with _pool_lock:
        if _pool is not None and not _pool.closed:
            _pool.close()
        _pool = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _row_to_document(r: dict[str, Any]) -> Document:
    """Map a joined documents+acts row to a Document model."""
    meta = r.get("metadata") or {}
    return Document(
        kind=r["kind"],
        ref=r["ref"],
        act=r.get("act_short_name") or (r.get("act") if "act" in r else None),
        title=r.get("title"),
        text=r.get("text", ""),
        metadata=meta,
        source=meta.get("source", "nyaya"),
        source_license=meta.get("source_license"),
        as_of=r.get("as_of"),
    )


_DOC_SELECT_TEMPLATE = """
    select d.id, d.kind, d.ref, d.title, {text_expr}, d.metadata,
           a.short_name as act_short_name, a.as_of
    from documents d left join acts a on a.id = d.act_id
"""


def _doc_select(text_expr: str = "d.text") -> str:
    """Build the document SELECT with an explicit text-column projection.

    ``text_expr`` is either ``d.text`` (full text) or ``left(d.text, N)`` for
    snippet mode. The snippet length is interpolated as an internally
    validated integer (never user input), so the f-string is injection-safe.
    """
    return _DOC_SELECT_TEMPLATE.format(text_expr=text_expr)


def _snippet_expr(snippet_chars: int) -> str:
    """SQL expression bounding the text column to ``snippet_chars`` chars.

    The value is clamped to [1, 2000] — it is a server-side default/param,
    interpolated into SQL as an integer only.
    """
    n = min(max(int(snippet_chars), 1), 2000)
    return f"left(d.text, {n})"


# ---------------------------------------------------------------------------
# Acts
# ---------------------------------------------------------------------------

def list_acts() -> list[Act]:
    """List all acts (5-minute TTL cache).

    The corpus is a frozen snapshot, so TTL-only invalidation (no manual
    invalidation API) is correct here. ``list_acts`` takes no arguments, so a
    single cache slot holds the full return value.
    """
    hit = _LIST_ACTS_CACHE.get(_LIST_ACTS_KEY)
    if hit is not _MISS:
        return list(hit)
    with _conn() as c:
        rows = c.execute(
            "select short_name, full_name, year, citation, kind, source, source_license, as_of "
            "from acts order by kind, year nulls last, short_name"
        ).fetchall()
    acts = [Act(**r) for r in rows]
    _LIST_ACTS_CACHE.set(_LIST_ACTS_KEY, acts)
    return list(acts)


def get_act(short_name: str) -> Act | None:
    sn = normalize_act(short_name)
    if sn is None:
        return None
    with _conn() as c:
        row = c.execute(
            "select short_name, full_name, year, citation, kind, source, source_license, as_of "
            "from acts where lower(short_name) = lower(%s)",
            (sn,),
        ).fetchone()
    return Act(**row) if row else None


# ---------------------------------------------------------------------------
# Documents (unified: sections, articles, judgments, schedules, amendments)
# ---------------------------------------------------------------------------

def get_document(kind: str, act: str | None, ref: str) -> Document | None:
    """Fetch a single document by (kind, act, ref). Returns None if not found."""
    sn = normalize_act(act)
    num = normalize_ref(ref)
    if num is None:
        return None
    with _conn() as c:
        if sn and kind == "section":
            row = c.execute(
                _doc_select() + " where d.kind = 'section' and lower(a.short_name) = lower(%s) and d.ref = %s",
                (sn, num),
            ).fetchone()
        elif kind == "article":
            row = c.execute(
                _doc_select() + " where d.kind = 'article' and d.ref = %s",
                (num,),
            ).fetchone()
        elif kind == "judgment":
            row = c.execute(
                _doc_select() + " where d.kind = 'judgment' and (d.ref = %s or lower(d.title) = lower(%s))",
                (num, num),
            ).fetchone()
            if row is None and len(num) >= 8:
                row = c.execute(
                    _doc_select() + " where d.kind = 'judgment' and lower(d.title) like '%%' || lower(%s) || '%%'",
                    (num,),
                ).fetchone()
        elif kind == "schedule":
            row = c.execute(
                _doc_select() + " where d.kind = 'schedule' and lower(d.ref) = lower(%s)",
                (f"schedule {num}" if not num.lower().startswith("schedule ") else num,),
            ).fetchone()
        elif kind == "amendment":
            row = c.execute(
                _doc_select() + " where d.kind = 'amendment' and lower(d.ref) = lower(%s)",
                (f"amendment {num}" if not num.lower().startswith("amendment ") else num,),
            ).fetchone()
        else:
            row = None
    return _row_to_document(row) if row else None


def get_section(act_short_name: str, section_number: str) -> Document | None:
    return get_document("section", act_short_name, section_number)


def get_article(article_number: str) -> Document | None:
    return get_document("article", None, article_number)


def get_judgment(case_slug: str) -> Document | None:
    return get_document("judgment", None, case_slug)


def get_schedule(number: int) -> Document | None:
    return get_document("schedule", None, str(number))


def get_amendment(number: int) -> Document | None:
    return get_document("amendment", None, str(number))


def list_sections(
    act_short_name: str,
    chapter: int | None = None,
    start: str | None = None,
    end: str | None = None,
    limit: int = 100,
    offset: int = 0,
    include_text: bool = False,
    snippet_chars: int = SNIPPET_CHARS,
) -> tuple[list[Document], int, str | None]:
    """List sections of an act, optionally filtered by chapter or numeric range.

    Returns (sections, total, chapter_title). ``chapter_title`` is the title of
    the chapter when ``chapter`` is set, otherwise None.

    By default only a text snippet (first ``snippet_chars`` chars) is
    returned; pass ``include_text=True`` for full document text.

    Ordering and numeric range filtering go through ``documents.ref_num`` — an
    int GENERATED ALWAYS AS ... STORED column (see ``mcp/schema.sql``) defined
    as exactly the historic ``coalesce(nullif(regexp_replace(d.ref,
    '[^0-9].*$', ''), '')::int, 0)`` expression. Using the stored column makes
    both the ORDER BY and the range filters sargable via the
    ``documents_act_ref_num_idx`` index instead of recomputing the expression
    per row on every page.
    """
    sn = normalize_act(act_short_name)
    if sn is None:
        return [], 0, None
    clauses = ["d.kind = 'section'", "lower(a.short_name) = lower(%s)"]
    params: list[Any] = [sn]
    chapter_title: str | None = None
    if chapter is not None:
        clauses.append("(d.metadata->>'chapter_num')::int = %s")
        params.append(chapter)
    if start is not None:
        s = normalize_ref(start)
        if s and s.isdigit():
            clauses.append("d.ref_num >= %s")
            params.append(int(s))
    if end is not None:
        e = normalize_ref(end)
        if e and e.isdigit():
            clauses.append("d.ref_num <= %s")
            params.append(int(e))
    where = " where " + " and ".join(clauses)
    text_expr = "d.text" if include_text else _snippet_expr(snippet_chars)
    with _conn() as c:
        rows = c.execute(
            _doc_select(text_expr) + where
            + " order by d.ref_num, d.ref"
            + " limit %s offset %s",
            params + [limit, offset],
        ).fetchall()
        total_row = c.execute(
            "select count(*) as n from documents d left join acts a on a.id = d.act_id" + where,
            params,
        ).fetchone()
        # Fetch chapter title if filtered by chapter.
        if chapter is not None:
            title_row = c.execute(
                """
                select distinct d.metadata->>'chapter_title' as title
                from documents d left join acts a on a.id = d.act_id
                where d.kind = 'section' and lower(a.short_name) = lower(%s)
                  and (d.metadata->>'chapter_num')::int = %s limit 1
                """,
                (sn, chapter),
            ).fetchone()
            chapter_title = title_row["title"] if title_row else None
    total = int(total_row["n"]) if total_row else 0
    return [_row_to_document(r) for r in rows], total, chapter_title


def list_articles(
    part: str | None = None,
    limit: int = 100,
    offset: int = 0,
    include_text: bool = False,
    snippet_chars: int = SNIPPET_CHARS,
) -> tuple[list[Document], int]:
    """List Constitution articles (full text only with ``include_text=True``)."""
    text_expr = "d.text" if include_text else _snippet_expr(snippet_chars)
    with _conn() as c:
        if part:
            rows = c.execute(
                _doc_select(text_expr) + " where d.kind = 'article' and d.metadata->>'part' ilike %s order by d.ref limit %s offset %s",
                (f"%{part}%", limit, offset),
            ).fetchall()
            total_row = c.execute(
                "select count(*) as n from documents where kind = 'article' and metadata->>'part' ilike %s",
                (f"%{part}%",),
            ).fetchone()
        else:
            rows = c.execute(
                _doc_select(text_expr) + " where d.kind = 'article' order by d.ref limit %s offset %s",
                (limit, offset),
            ).fetchall()
            total_row = c.execute(
                "select count(*) as n from documents where kind = 'article'"
            ).fetchone()
    total = int(total_row["n"]) if total_row else 0
    return [_row_to_document(r) for r in rows], total


def list_judgments(
    limit: int = 50,
    offset: int = 0,
    include_text: bool = False,
    snippet_chars: int = SNIPPET_CHARS,
) -> tuple[list[Document], int]:
    """List landmark judgments (full text only with ``include_text=True``)."""
    text_expr = "d.text" if include_text else _snippet_expr(snippet_chars)
    with _conn() as c:
        rows = c.execute(
            _doc_select(text_expr) + " where d.kind = 'judgment' order by d.metadata->>'date' desc nulls last limit %s offset %s",
            (limit, offset),
        ).fetchall()
        total_row = c.execute("select count(*) as n from documents where kind = 'judgment'").fetchone()
    total = int(total_row["n"]) if total_row else 0
    return [_row_to_document(r) for r in rows], total


def list_schedules() -> list[Document]:
    """List all schedules (5-minute TTL cache).

    Takes no arguments, so one cache slot holds the full return value; the
    corpus is a frozen snapshot, so TTL-only invalidation is correct.
    """
    hit = _LIST_SCHEDULES_CACHE.get(_LIST_SCHEDULES_KEY)
    if hit is not _MISS:
        return list(hit)
    with _conn() as c:
        rows = c.execute(
            _doc_select() + " where d.kind = 'schedule' order by d.metadata->>'number'"
        ).fetchall()
    docs = [_row_to_document(r) for r in rows]
    _LIST_SCHEDULES_CACHE.set(_LIST_SCHEDULES_KEY, docs)
    return list(docs)


def list_amendments(year_from: int | None = None, year_to: int | None = None) -> list[Document]:
    """List amendments, optionally filtered by year range (5-minute TTL cache).

    Cache key = (year_from, year_to): the filter space is small and enumerable
    (integer years), so caching the full return per key is simple and bounded.
    The corpus is a frozen snapshot, so TTL-only invalidation is correct.
    """
    key = (year_from, year_to)
    hit = _LIST_AMENDMENTS_CACHE.get(key)
    if hit is not _MISS:
        return list(hit)
    clauses = ["d.kind = 'amendment'"]
    params: list[Any] = []
    if year_from is not None:
        clauses.append("(d.metadata->>'year')::int >= %s")
        params.append(year_from)
    if year_to is not None:
        clauses.append("(d.metadata->>'year')::int <= %s")
        params.append(year_to)
    where = " where " + " and ".join(clauses)
    with _conn() as c:
        rows = c.execute(
            _doc_select() + where + " order by d.metadata->>'number'", params
        ).fetchall()
    docs = [_row_to_document(r) for r in rows]
    _LIST_AMENDMENTS_CACHE.set(key, docs)
    return list(docs)


def list_chapters(act_short_name: str) -> list[dict[str, Any]]:
    """List chapters for an act (derived from section metadata)."""
    sn = normalize_act(act_short_name)
    if sn is None:
        return []
    with _conn() as c:
        rows = c.execute(
            """
            select distinct (d.metadata->>'chapter_num')::int as number,
                   d.metadata->>'chapter_title' as title
            from documents d join acts a on a.id = d.act_id
            where d.kind = 'section' and lower(a.short_name) = lower(%s)
              and d.metadata->>'chapter_num' is not null
            order by number
            """,
            (sn,),
        ).fetchall()
    return [dict(number=r["number"], title=r["title"]) for r in rows]


def get_amendments_for_article(article_number: str) -> list[Document]:
    num = normalize_ref(article_number)
    if num is None:
        return []
    with _conn() as c:
        rows = c.execute(
            _doc_select() + " where d.kind = 'amendment'"
            " and (d.metadata->>'articles_affected') ~ %s order by d.metadata->>'number'",
            (rf"[[:<:]]{re.escape(num)}[[:>:]]",),
        ).fetchall()
    return [_row_to_document(r) for r in rows]


# ---------------------------------------------------------------------------
# Cross-references
# ---------------------------------------------------------------------------

def get_cross_refs(
    act: str, section: str, direction: CrossRefDirection = "both"
) -> list[CrossRef]:
    """Look up cross-references for a section (bidirectional by default).

    ``direction`` is a closed Literal ('from' = outgoing, 'to' = incoming,
    'both' = default) — invalid values are rejected at the tool boundary by
    FastMCP's schema validation before this layer runs.
    """
    sn = normalize_act(act)
    s = normalize_ref(section)
    if sn is None or s is None:
        return []
    refs: list[CrossRef] = []
    with _conn() as c:
        # Find the source document id.
        doc_row = c.execute(
            "select d.id::text from documents d join acts a on a.id = d.act_id "
            "where d.kind = 'section' and lower(a.short_name) = lower(%s) and d.ref = %s",
            (sn, s),
        ).fetchone()
        if not doc_row:
            return []
        doc_id = doc_row["id"]
        if direction in ("from", "both"):
            rows = c.execute(
                """
                select da.short_name as from_act, d_from.ref as from_section,
                       db.short_name as to_act, d_to.ref as to_section, cr.kind
                from cross_refs cr
                join documents d_from on d_from.id = cr.from_doc
                join acts da on da.id = d_from.act_id
                join documents d_to on d_to.id = cr.to_doc
                left join acts db on db.id = d_to.act_id
                where cr.from_doc = %s::uuid
                order by cr.kind, db.short_name, d_to.ref
                """,
                (doc_id,),
            ).fetchall()
            refs.extend(CrossRef(**r) for r in rows)
        if direction in ("to", "both"):
            rows = c.execute(
                """
                select da.short_name as from_act, d_from.ref as from_section,
                       db.short_name as to_act, d_to.ref as to_section, cr.kind
                from cross_refs cr
                join documents d_from on d_from.id = cr.from_doc
                left join acts da on da.id = d_from.act_id
                join documents d_to on d_to.id = cr.to_doc
                join acts db on db.id = d_to.act_id
                where cr.to_doc = %s::uuid
                order by cr.kind, da.short_name, d_from.ref
                """,
                (doc_id,),
            ).fetchall()
            refs.extend(CrossRef(**r) for r in rows)
    # Dedupe exact duplicate rows.
    seen: set[tuple[str, str, str, str, str]] = set()
    unique: list[CrossRef] = []
    for r in refs:
        key = (r.from_act, r.from_section, r.to_act, r.to_section, r.kind)
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


# ---------------------------------------------------------------------------
# Semantic search (pgvector) + reranking
# ---------------------------------------------------------------------------

def semantic_search(
    query_embedding: list[float],
    kind: str | None = None,
    act: str | None = None,
    limit: int = 10,
    offset: int = 0,
) -> tuple[list[SearchResult], int]:
    """Single pgvector ANN query over documents.embedding."""
    clauses = ["d.embedding is not null"]
    filter_params: list[Any] = []
    if kind:
        clauses.append("d.kind = %s")
        filter_params.append(kind)
    sn = normalize_act(act) if act else None
    if sn:
        clauses.append("lower(a.short_name) = lower(%s)")
        filter_params.append(sn)
    where = " where " + " and ".join(clauses)
    with _conn() as c:
        # SELECT params: embedding (for rank) + filter_params + embedding (for order) + limit + offset
        rows = c.execute(
            f"""
            select coalesce(a.short_name, 'judgment') as act,
                   d.ref, d.title, d.kind,
                   1 - (d.embedding <=> %s::vector) as rank,
                   left(d.text, {int(SNIPPET_CHARS)}) as snippet,
                   d.metadata->>'citation' as citation
            from documents d left join acts a on a.id = d.act_id
            {where}
            order by d.embedding <=> %s::vector
            limit %s offset %s
            """,
            [query_embedding] + filter_params + [query_embedding, limit, offset],
        ).fetchall()
        total_row = c.execute(
            f"select count(*) as n from documents d left join acts a on a.id = d.act_id {where}",
            filter_params,
        ).fetchone()
    total = int(total_row["n"]) if total_row else 0
    results = [
        SearchResult(
            act=r["act"], ref=r["ref"], title=r.get("title"),
            snippet=r.get("snippet", ""), rank=float(r["rank"]),
            citation=r.get("citation"), kind=r.get("kind"),
        )
        for r in rows
    ]
    return results, total


def rerank_search(
    query: str,
    kind: str | None = None,
    act: str | None = None,
    limit: int = 10,
    offset: int = 0,
    promote_definitions: bool = False,
) -> tuple[list[SearchResult], int, str | None]:
    """Retrieve + rerank: embed query → ANN top-N → rerank → top-``limit``.

    Returns (results, total, fallback_reason). If the reranker fails, falls
    back to raw ANN scores with fallback_reason='reranker_unavailable'.

    When ``promote_definitions`` is True, re-sorts the reranked results to
    promote those whose title contains 'defin' or 'interpret' (matching IPC s.2
    'Definitions', BNS s.2 'Definitions', etc.) — useful for statutory term
    lookups.
    """
    from .embeddings import EmbeddingUnavailable, embed_query, rerank_query

    try:
        query_emb = embed_query(query)
    except EmbeddingUnavailable:
        return [], 0, "embedding_unavailable"

    # Stage 1: ANN candidate pool. Sized relative to the requested limit
    # (3x, clamped to [10, 50]) so small pages don't pay for 50 ANN rows
    # and a 50-row rerank call they will never return.
    pool = min(max(3 * limit, 10), 50)
    candidates, total = semantic_search(query_emb, kind=kind, act=act, limit=pool, offset=0)
    if not candidates:
        return [], 0, None

    # Stage 2: rerank
    fallback_reason: str | None = None
    try:
        cand_texts = [
            f"{r.act} {r.ref} {r.title or ''} {r.snippet}" for r in candidates
        ]
        scores = rerank_query(query, cand_texts)
        paired = list(zip(candidates, scores))
        paired.sort(key=lambda x: -x[1])
        reranked = []
        for r, score in paired:
            reranked.append(r.model_copy(update={"rank": float(score)}))
    except EmbeddingUnavailable:
        reranked = candidates
        fallback_reason = "reranker_unavailable"
    except Exception as exc:
        log.warning("reranker failed, falling back to raw ANN: %s", exc)
        reranked = candidates
        fallback_reason = "reranker_unavailable"

    # Stage 3 (optional): promote definition-titled results to the top.
    if promote_definitions:
        reranked.sort(key=lambda r: (0 if (r.title and _DEF_RE.search(r.title)) else 1, -r.rank))

    # Apply offset + limit after rerank (and after definition promotion)
    results = reranked[offset : offset + limit]
    return results, total, fallback_reason


# ---------------------------------------------------------------------------
# Stats / health
# ---------------------------------------------------------------------------

def corpus_stats() -> dict[str, int]:
    """Corpus counts (60s TTL cache).

    Hit by the Railway healthcheck every 30s; the 60s TTL collapses
    3 queries per call into one round-trip per minute. Failures are never
    cached (the exception propagates and the stale entry, if any, is left
    in place only by absence of mutation — the next call retries the DB).
    This function takes no arguments, so a single cache slot is used.
    """
    hit = _CORPUS_STATS_CACHE.get(_CORPUS_STATS_KEY)
    if hit is not _MISS:
        return dict(hit)
    stats = _corpus_stats_uncached()
    _CORPUS_STATS_CACHE.set(_CORPUS_STATS_KEY, stats)
    return dict(stats)


def _corpus_stats_uncached() -> dict[str, int]:
    with _conn() as c:
        rows = c.execute(
            """
            select kind, count(*) as n from documents group by kind
            """
        ).fetchall()
        stats = {r["kind"]: int(r["n"]) for r in rows}
        # Ensure all kinds are present
        for k in ("section", "article", "judgment", "schedule", "amendment"):
            stats.setdefault(k, 0)
        cr_row = c.execute("select count(*) as n from cross_refs").fetchone()
        stats["cross_refs"] = int(cr_row["n"]) if cr_row else 0
        act_row = c.execute("select count(*) as n from acts").fetchone()
        stats["acts"] = int(act_row["n"]) if act_row else 0
    return stats


def corpus_as_of() -> date | None:
    global _as_of_cache
    now = time.monotonic()
    # The cache tuple is shared across worker threads; take the lock only for
    # the tuple read/write — the DB round-trip below stays outside it.
    with _as_of_lock:
        cached_time, cached_val = _as_of_cache
    if now - cached_time < _AS_OF_TTL:
        return cached_val
    try:
        with _conn() as c:
            row = c.execute("select max(as_of) as d from acts").fetchone()
        val = row["d"] if row else None
    except DatabaseUnavailable:
        return None
    with _as_of_lock:
        _as_of_cache = (now, val)
    return val
