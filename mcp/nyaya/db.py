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

from .config import get_settings
from .exceptions import DatabaseUnavailable
from .models import Act, CrossRef, Document, SearchResult

log = logging.getLogger("nyaya.db")

# Fallback provenance date used when an act row lacks a current ``as_of``.
CORPUS_AS_OF = date(2026, 7, 1)

# Strips Unicode bidi/format characters that could spoof act-name lookups.
_BIDI_RE = re.compile(r"[\u200B-\u200F\u202A-\u202E\u2066-\u2069]")

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

_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()

_as_of_cache: tuple[float, date | None] = (0.0, None)
_AS_OF_TTL = 300.0


# ---------------------------------------------------------------------------
# Input normalization
# ---------------------------------------------------------------------------

def normalize_act(act: str | None) -> str | None:
    """Normalize an act short-name: strip, resolve aliases (case-insensitive)."""
    if act is None:
        return None
    key = _BIDI_RE.sub("", act).strip()
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

def _get_pool() -> ConnectionPool:
    global _pool
    with _pool_lock:
        if _pool is None or _pool.closed:
            settings = get_settings()

            def _configure(conn: psycopg.Connection) -> None:
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
def _conn() -> Iterator[psycopg.Connection]:
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


_DOC_SELECT = """
    select d.id, d.kind, d.ref, d.title, d.text, d.metadata,
           a.short_name as act_short_name, a.as_of
    from documents d left join acts a on a.id = d.act_id
"""


# ---------------------------------------------------------------------------
# Acts
# ---------------------------------------------------------------------------

def list_acts() -> list[Act]:
    with _conn() as c:
        rows = c.execute(
            "select short_name, full_name, year, citation, kind, source, source_license, as_of "
            "from acts order by kind, year nulls last, short_name"
        ).fetchall()
    return [Act(**r) for r in rows]


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
                _DOC_SELECT + " where d.kind = 'section' and lower(a.short_name) = lower(%s) and d.ref = %s",
                (sn, num),
            ).fetchone()
        elif kind == "article":
            row = c.execute(
                _DOC_SELECT + " where d.kind = 'article' and d.ref = %s",
                (num,),
            ).fetchone()
        elif kind == "judgment":
            row = c.execute(
                _DOC_SELECT + " where d.kind = 'judgment' and (d.ref = %s or lower(d.title) = lower(%s))",
                (num, num),
            ).fetchone()
            if row is None and len(num) >= 8:
                row = c.execute(
                    _DOC_SELECT + " where d.kind = 'judgment' and lower(d.title) like '%%' || lower(%s) || '%%'",
                    (num,),
                ).fetchone()
        elif kind == "schedule":
            row = c.execute(
                _DOC_SELECT + " where d.kind = 'schedule' and lower(d.ref) = lower(%s)",
                (f"schedule {num}" if not num.lower().startswith("schedule ") else num,),
            ).fetchone()
        elif kind == "amendment":
            row = c.execute(
                _DOC_SELECT + " where d.kind = 'amendment' and lower(d.ref) = lower(%s)",
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
) -> tuple[list[Document], int, str | None]:
    """List sections of an act, optionally filtered by chapter or numeric range.

    Returns (sections, total, chapter_title). ``chapter_title`` is the title of
    the chapter when ``chapter`` is set, otherwise None.
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
            clauses.append("coalesce(nullif(regexp_replace(d.ref, '[^0-9].*$', ''), '')::int, 0) >= %s")
            params.append(int(s))
    if end is not None:
        e = normalize_ref(end)
        if e and e.isdigit():
            clauses.append("coalesce(nullif(regexp_replace(d.ref, '[^0-9].*$', ''), '')::int, 0) <= %s")
            params.append(int(e))
    where = " where " + " and ".join(clauses)
    with _conn() as c:
        rows = c.execute(
            _DOC_SELECT + where
            + " order by coalesce(nullif(regexp_replace(d.ref, '[^0-9].*$', ''), '')::int, 0), d.ref"
            + " limit %s offset %s",
            params + [limit, offset],
        ).fetchall()
        total_row = c.execute(
            "select count(*) as n from documents d join acts a on a.id = d.act_id" + where,
            params,
        ).fetchone()
        # Fetch chapter title if filtered by chapter.
        if chapter is not None:
            title_row = c.execute(
                """
                select distinct d.metadata->>'chapter_title' as title
                from documents d join acts a on a.id = d.act_id
                where d.kind = 'section' and lower(a.short_name) = lower(%s)
                  and (d.metadata->>'chapter_num')::int = %s limit 1
                """,
                (sn, chapter),
            ).fetchone()
            chapter_title = title_row["title"] if title_row else None
    total = int(total_row["n"]) if total_row else 0
    return [_row_to_document(r) for r in rows], total, chapter_title


def list_articles(part: str | None = None, limit: int = 100, offset: int = 0) -> tuple[list[Document], int]:
    with _conn() as c:
        if part:
            rows = c.execute(
                _DOC_SELECT + " where d.kind = 'article' and d.metadata->>'part' ilike %s order by d.ref limit %s offset %s",
                (f"%{part}%", limit, offset),
            ).fetchall()
            total_row = c.execute(
                "select count(*) as n from documents where kind = 'article' and metadata->>'part' ilike %s",
                (f"%{part}%",),
            ).fetchone()
        else:
            rows = c.execute(
                _DOC_SELECT + " where d.kind = 'article' order by d.ref limit %s offset %s",
                (limit, offset),
            ).fetchall()
            total_row = c.execute(
                "select count(*) as n from documents where kind = 'article'"
            ).fetchone()
    total = int(total_row["n"]) if total_row else 0
    return [_row_to_document(r) for r in rows], total


def list_judgments(limit: int = 50, offset: int = 0) -> tuple[list[Document], int]:
    with _conn() as c:
        rows = c.execute(
            _DOC_SELECT + " where d.kind = 'judgment' order by d.metadata->>'date' desc nulls last limit %s offset %s",
            (limit, offset),
        ).fetchall()
        total_row = c.execute("select count(*) as n from documents where kind = 'judgment'").fetchone()
    total = int(total_row["n"]) if total_row else 0
    return [_row_to_document(r) for r in rows], total


def list_schedules() -> list[Document]:
    with _conn() as c:
        rows = c.execute(
            _DOC_SELECT + " where d.kind = 'schedule' order by d.metadata->>'number'"
        ).fetchall()
    return [_row_to_document(r) for r in rows]


def list_amendments(year_from: int | None = None, year_to: int | None = None) -> list[Document]:
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
            _DOC_SELECT + where + " order by d.metadata->>'number'", params
        ).fetchall()
    return [_row_to_document(r) for r in rows]


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
            _DOC_SELECT + " where d.kind = 'amendment'"
            " and (d.metadata->>'articles_affected') ~ %s order by d.metadata->>'number'",
            (rf"[[:<:]]{re.escape(num)}[[:>:]]",),
        ).fetchall()
    return [_row_to_document(r) for r in rows]


# ---------------------------------------------------------------------------
# Cross-references
# ---------------------------------------------------------------------------

def get_cross_refs(act: str, section: str, direction: str = "both") -> list[CrossRef]:
    """Look up cross-references for a section (bidirectional by default)."""
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
                   left(d.text, 300) as snippet,
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
    """Retrieve + rerank: embed query → ANN top-50 → rerank → top-N.

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

    # Stage 1: ANN retrieval (top 50 candidates)
    candidates, total = semantic_search(query_emb, kind=kind, act=act, limit=50, offset=0)
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
        _DEF_RE = re.compile(r"defin|interpret", re.IGNORECASE)
        reranked.sort(key=lambda r: (0 if (r.title and _DEF_RE.search(r.title)) else 1, -r.rank))

    # Apply offset + limit after rerank (and after definition promotion)
    results = reranked[offset : offset + limit]
    return results, total, fallback_reason


# ---------------------------------------------------------------------------
# Stats / health
# ---------------------------------------------------------------------------

def corpus_stats() -> dict[str, int]:
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
    cached_time, cached_val = _as_of_cache
    if now - cached_time < _AS_OF_TTL:
        return cached_val
    try:
        with _conn() as c:
            row = c.execute("select max(as_of) as d from acts").fetchone()
        val = row["d"] if row else None
    except DatabaseUnavailable:
        return None
    _as_of_cache = (now, val)
    return val
