"""Postgres data access for nyaya.

All functions are synchronous and intended to be wrapped with
``asyncio.to_thread`` by the (async) tool layer — see ``tools/_util.py``.

Design notes
------------
* Input normalization: act/section/article strings are stripped and resolved
  via an alias map; act lookups are case-insensitive at the SQL level.
* Search: ``search_all`` uses a single UNION ALL query with a global
  ORDER BY + LIMIT/OFFSET so pagination is correct across corpora. The total
  is computed via a separate ``count(*)`` query so it remains accurate when
  ``offset >= total`` (a window-function approach returns 0 for an empty page).
* Errors: DB exceptions are translated to ``DatabaseUnavailable`` /
  ``SearchError`` so the MCP client receives a structured error code instead
  of a raw psycopg traceback.
"""

from __future__ import annotations

import contextlib
import re
import threading
import time
from datetime import date
from typing import Any, Iterator

import psycopg
import psycopg_pool
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import get_settings
from .exceptions import DatabaseUnavailable, SearchError
from .models import (
    Act,
    Amendment,
    Article,
    Chapter,
    CrossRef,
    Judgment,
    Schedule,
    Section,
    SearchResult,
)

# Fallback provenance date used when an act row lacks a current ``as_of``.
CORPUS_AS_OF = date(2026, 7, 1)

# Strips Unicode bidi/format characters that could spoof act-name lookups
# (zero-width joiners, RTL/LTR overrides, isolates).
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

_pool: ConnectionPool | None = None
_pool_lock = threading.Lock()

# 5-minute TTL: ``as_of`` changes only on re-ingestion, so caching avoids an
# extra round-trip on every search call. Failures are not cached.
_as_of_cache: tuple[float, date | None] = (0.0, None)
_AS_OF_TTL = 300.0  # seconds

# ts_headline option strings, passed as SQL parameters so the option syntax
# (quotes, angle brackets) never appears in the SQL literal.
TS_HEADLINE_OPTS = (
    'MaxWords=60, MinWords=20, MaxFragments=3, '
    'FragmentDelimiter=" … ", StartSel="<<", StopSel=">>"'
)
TS_HEADLINE_OPTS_LONG = (
    'MaxWords=80, MinWords=20, MaxFragments=3, '
    'FragmentDelimiter=" … ", StartSel="<<", StopSel=">>"'
)

# Strips a leading "s."/"section"/"art."/"article" prefix from a section or
# article number, with or without a separator ("s.302", "s302", "section 302",
# "section302", "art.21", "art21", "article 21").
_REF_PREFIX_RE = re.compile(
    r"^(?:s(?:ec(?:tion)?)?\.?|art(?:icle)?\.?)\s*",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Input normalization
# ---------------------------------------------------------------------------

def normalize_act(act: str | None) -> str | None:
    """Normalize an act short-name: strip, resolve aliases (case-insensitive).

    Returns the canonical ``short_name`` (e.g. 'IPC') or ``None`` if the input
    is empty/None. Unknown values are returned stripped (original case
    preserved) — the DB lookup is case-insensitive so this is fine.

    Also strips Unicode bidi/format characters (U+200B-200F, U+202A-202E,
    U+2066-2069) that could be used to spoof lookups.
    """
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
    """Strip whitespace and a leading 's.'/'section'/'art.'/'article' prefix.

    Handles both "s. 302" (with separator) and "s302" (without separator).
    """
    if ref is None:
        return None
    r = ref.strip()
    if not r:
        return None
    r = _REF_PREFIX_RE.sub("", r).strip()
    return r if r else None


def _escape_like(s: str) -> str:
    """Escape ``%`` and ``_`` so they match literally in LIKE/ILIKE patterns."""
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

def _get_pool() -> ConnectionPool:
    global _pool
    # Lazy initialization: ``asyncio.to_thread`` can race on the first call,
    # so the lock ensures only one pool is created.
    with _pool_lock:
        if _pool is None or _pool.closed:
            settings = get_settings()

            def _configure(conn: psycopg.Connection) -> None:
                # Configure runs once per new connection. SET commands start
                # a transaction in psycopg's default autocommit=False mode, so
                # we must commit (or use autocommit) — otherwise the pool
                # sees the connection left in INTRANS and discards it.
                conn.autocommit = True
                try:
                    if settings.statement_timeout_ms > 0:
                        # String formatting is safe: statement_timeout_ms is a
                        # validated int from config. Required because Supabase's
                        # PgBouncer in transaction mode doesn't support
                        # parameterized SET.
                        with conn.cursor() as cur:
                            cur.execute(
                                f"set statement_timeout = {int(settings.statement_timeout_ms)}"
                            )
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
            "Could not acquire a database connection in time. The pool may be exhausted or the database is unreachable.",
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
# Acts / chapters
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


def list_chapters(act_short_name: str) -> list[Chapter]:
    sn = normalize_act(act_short_name)
    if sn is None:
        return []
    with _conn() as c:
        rows = c.execute(
            "select c.number, c.title, c.section_range "
            "from chapters c join acts a on a.id = c.act_id "
            "where lower(a.short_name) = lower(%s) order by c.number",
            (sn,),
        ).fetchall()
    return [Chapter(**r) for r in rows]


# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

def get_section(act_short_name: str, section_number: str) -> Section | None:
    sn = normalize_act(act_short_name)
    num = normalize_ref(section_number)
    if sn is None or num is None:
        return None
    with _conn() as c:
        row = c.execute(
            """
            select s.number, s.title, s.text, s.url,
                   c.number as chapter_number, c.title as chapter_title,
                   a.short_name as act, a.source, a.source_license, a.as_of
            from sections s
            join acts a on a.id = s.act_id
            left join chapters c on c.id = s.chapter_id
            where lower(a.short_name) = lower(%s) and s.number = %s
            """,
            (sn, num),
        ).fetchone()
    if not row:
        return None
    return Section(
        act=row["act"], section=row["number"], title=row["title"],
        text=row["text"], url=row["url"],
        chapter_number=row["chapter_number"], chapter_title=row["chapter_title"],
        source=row["source"], source_license=row["source_license"], as_of=row["as_of"],
    )


def list_sections(act_short_name: str, chapter: int | None = None,
                  limit: int = 100, offset: int = 0) -> tuple[list[Section], int]:
    """List sections of an act, optionally filtered to a chapter.

    Returns ``(sections, total)`` where ``total`` is the true match count
    before limit/offset.
    """
    sn = normalize_act(act_short_name)
    if sn is None:
        return [], 0
    params: list[Any] = [sn]
    where = "where lower(a.short_name) = lower(%s)"
    if chapter is not None:
        where += " and c.number = %s"
        params.append(chapter)
    with _conn() as c:
        total = c.execute(
            f"select count(*) as n from sections s join acts a on a.id = s.act_id "
            f"left join chapters c on c.id = s.chapter_id {where}",
            params,
        ).fetchone()["n"]
        rows = c.execute(
            f"""
            select s.number, s.title, s.text, s.url,
                   c.number as chapter_number, c.title as chapter_title,
                   a.short_name as act, a.source, a.source_license, a.as_of
            from sections s
            join acts a on a.id = s.act_id
            left join chapters c on c.id = s.chapter_id
            {where}
            order by s.number
            limit %s offset %s
            """,
            [*params, limit, offset],
        ).fetchall()
    return [Section(
        act=r["act"], section=r["number"], title=r["title"], text=r["text"],
        url=r["url"], chapter_number=r["chapter_number"], chapter_title=r["chapter_title"],
        source=r["source"], source_license=r["source_license"], as_of=r["as_of"],
    ) for r in rows], int(total)


def get_sections_by_range(act_short_name: str, start: str, end: str,
                          limit: int = 500) -> list[Section]:
    """Fetch all sections of an act between two numbers (inclusive).

    Section numbers are strings ('302', '354A'); the numeric prefix is
    compared numerically and the full string is used as a secondary sort/bound
    so '354A'..'354B' does not match '354'. Non-numeric section numbers are
    guarded with ``NULLIF`` so they don't break the cast.
    """
    sn = normalize_act(act_short_name)
    s = normalize_ref(start)
    e = normalize_ref(end)
    if sn is None or s is None or e is None:
        return []
    with _conn() as c:
        rows = c.execute(
            """
            select s.number, s.title, s.text, s.url,
                   c.number as chapter_number, c.title as chapter_title,
                   a.short_name as act, a.source, a.source_license, a.as_of
            from sections s
            join acts a on a.id = s.act_id
            left join chapters c on c.id = s.chapter_id
            where lower(a.short_name) = lower(%s)
              and coalesce(nullif(regexp_replace(s.number, '[^0-9].*$', ''), '')::int, 0)
                  between coalesce(nullif(regexp_replace(%s, '[^0-9].*$', ''), '')::int, 0)
                  and coalesce(nullif(regexp_replace(%s, '[^0-9].*$', ''), '')::int, 0)
            order by coalesce(nullif(regexp_replace(s.number, '[^0-9].*$', ''), '')::int, 0), s.number
            limit %s
            """,
            (sn, s, e, limit),
        ).fetchall()
    return [Section(
        act=r["act"], section=r["number"], title=r["title"], text=r["text"],
        url=r["url"], chapter_number=r["chapter_number"], chapter_title=r["chapter_title"],
        source=r["source"], source_license=r["source_license"], as_of=r["as_of"],
    ) for r in rows]


def search_sections(query: str, act: str | None = None,
                    limit: int = 10, offset: int = 0) -> tuple[list[SearchResult], int]:
    """FTS over sections. Returns ``(results, total)`` where ``total`` is the
    true match count (via a separate count query, correct even when
    ``offset >= total``).
    """
    where = "where s.act_id = a.id and s.search_tsv @@ q"
    params: list[Any] = [TS_HEADLINE_OPTS, query]
    if act:
        where += " and lower(a.short_name) = lower(%s)"
        params.append(act)
    base = (
        f"from sections s, acts a, plainto_tsquery('english', %s) q {where}"
    )
    with _conn() as c:
        total = c.execute(
            f"select count(*) as n {base.replace(TS_HEADLINE_OPTS + ', ', '')}",
            params[1:],
        ).fetchone()["n"]
        rows = c.execute(
            f"""
            select a.short_name as act,
                   's. ' || s.number as ref,
                   s.title,
                   ts_rank(s.search_tsv, q) as rank,
                   ts_headline('english', s.text, q, %s) as snippet,
                   a.citation,
                   'section' as kind
            {base}
            order by rank desc limit %s offset %s
            """,
            [*params, limit, offset],
        ).fetchall()
    return [SearchResult(**r) for r in rows], int(total)


# ---------------------------------------------------------------------------
# Constitution articles
# ---------------------------------------------------------------------------

def get_article(number: str) -> Article | None:
    num = normalize_ref(number)
    if num is None:
        return None
    with _conn() as c:
        row = c.execute(
            "select number, title, text, part from articles where number = %s",
            (num,),
        ).fetchone()
        prov = c.execute(
            "select source, source_license, as_of from acts where lower(short_name) = 'constitution'"
        ).fetchone()
    if not row:
        return None
    source = prov["source"] if prov else "Vikhram-S/IndianConstitution (Apache-2.0)"
    license_ = prov["source_license"] if prov else "Apache-2.0"
    as_of = prov["as_of"] if prov else CORPUS_AS_OF
    return Article(
        number=row["number"], title=row["title"], text=row["text"], part=row["part"],
        source=source, source_license=license_, as_of=as_of,
    )


def list_articles(part: str | None = None,
                   limit: int = 100, offset: int = 0) -> tuple[list[Article], int]:
    """List Constitution articles, optionally filtered by Part."""
    params: list[Any] = []
    where = ""
    if part:
        where = "where part ilike %s escape '\\'"
        params.append(f"%{_escape_like(part)}%")
    with _conn() as c:
        total = c.execute(
            f"select count(*) as n from articles {where}", params
        ).fetchone()["n"]
        rows = c.execute(
            f"select number, title, text, part from articles {where} "
            f"order by number limit %s offset %s",
            [*params, limit, offset],
        ).fetchall()
        prov = c.execute(
            "select source, source_license, as_of from acts where lower(short_name) = 'constitution'"
        ).fetchone()
    source = prov["source"] if prov else "Vikhram-S/IndianConstitution (Apache-2.0)"
    license_ = prov["source_license"] if prov else "Apache-2.0"
    as_of = prov["as_of"] if prov else CORPUS_AS_OF
    return [Article(
        number=r["number"], title=r["title"], text=r["text"], part=r["part"],
        source=source, source_license=license_, as_of=as_of,
    ) for r in rows], int(total)


def search_articles(query: str, limit: int = 10, offset: int = 0) -> tuple[list[SearchResult], int]:
    with _conn() as c:
        total = c.execute(
            "select count(*) as n from articles a, plainto_tsquery('english', %s) q where a.search_tsv @@ q",
            (query,),
        ).fetchone()["n"]
        rows = c.execute(
            """
            select 'Constitution' as act,
                   'art. ' || a.number as ref,
                   a.title,
                   ts_rank(a.search_tsv, q) as rank,
                   ts_headline('english', a.text, q, %s) as snippet,
                   null as citation,
                   'article' as kind
            from articles a, plainto_tsquery('english', %s) q
            where a.search_tsv @@ q
            order by rank desc limit %s offset %s
            """,
            (TS_HEADLINE_OPTS, query, limit, offset),
        ).fetchall()
    return [SearchResult(**r) for r in rows], int(total)


# ---------------------------------------------------------------------------
# Judgments
# ---------------------------------------------------------------------------

def get_judgment(case_slug: str) -> Judgment | None:
    """Fetch a judgment by exact citation, slugified case name, or fuzzy name.

    The match is tried in order of specificity (citation → slugified name →
    fuzzy name) and only falls through to a looser match if the tighter one
    returns nothing. This prevents short slugs like "v" from matching an
    arbitrary judgment via substring LIKE.
    """
    slug = case_slug.strip() if case_slug else ""
    if not slug:
        return None
    with _conn() as c:
        # 1. Exact citation match (uses judgments_citation_idx).
        row = c.execute(
            "select case_name, citation, court, date, summary, text "
            "from judgments where citation = %s limit 1",
            (slug,),
        ).fetchone()
        if not row:
            # 2. Slugified case-name match (e.g. "kesavananda-bharati-v-state-of-kerala").
            #    Strip periods so "v." -> "v" matches the common slug form.
            row = c.execute(
                "select case_name, citation, court, date, summary, text "
                "from judgments where lower(replace(replace(case_name, ' ', '-'), '.', '')) = lower(replace(%s, '.', '')) limit 1",
                (slug,),
            ).fetchone()
        if not row and len(slug) >= 8:
            # 3. Fuzzy substring match ONLY for slugs >= 8 chars, to avoid
            # "v" or "india" matching arbitrary cases.
            escaped = _escape_like(slug.replace("-", " "))
            row = c.execute(
                "select case_name, citation, court, date, summary, text "
                "from judgments where lower(case_name) like lower(%s) escape '\\' limit 1",
                (f"%{escaped}%",),
            ).fetchone()
    if not row:
        return None
    return Judgment(
        case_name=row["case_name"], citation=row["citation"], court=row["court"],
        date=row["date"], summary=row["summary"], text=row["text"],
        source="Curated from indiankanoon.org (public domain)",
        source_license="Public domain (government edicts)",
        as_of=CORPUS_AS_OF,
    )


def list_judgments(limit: int = 50, offset: int = 0) -> tuple[list[Judgment], int]:
    """List all judgments in the corpus."""
    with _conn() as c:
        total = c.execute("select count(*) as n from judgments").fetchone()["n"]
        rows = c.execute(
            "select case_name, citation, court, date, summary, text "
            "from judgments order by date desc nulls last limit %s offset %s",
            (limit, offset),
        ).fetchall()
    return [Judgment(
        case_name=r["case_name"], citation=r["citation"], court=r["court"],
        date=r["date"], summary=r["summary"], text=r["text"],
        source="Curated from indiankanoon.org (public domain)",
        source_license="Public domain (government edicts)",
        as_of=CORPUS_AS_OF,
    ) for r in rows], int(total)


def search_judgments(query: str, court: str | None = None,
                    date_from: str | None = None, date_to: str | None = None,
                    limit: int = 10, offset: int = 0) -> tuple[list[SearchResult], int]:
    """Full-text search across judgments with optional court/date filters."""
    where = "where j.search_tsv @@ q"
    params: list[Any] = [query]
    if court:
        where += " and j.court ilike %s escape '\\'"
        params.append(f"%{_escape_like(court)}%")
    if date_from:
        where += " and j.date >= %s::date"
        params.append(date_from)
    if date_to:
        where += " and j.date <= %s::date"
        params.append(date_to)
    base = f"from judgments j, plainto_tsquery('english', %s) q {where}"
    with _conn() as c:
        total = c.execute(f"select count(*) as n {base}", params).fetchone()["n"]
        rows = c.execute(
            f"""
            select 'judgment' as act,
                   coalesce(j.citation, j.case_name) as ref,
                   j.case_name as title,
                   ts_rank(j.search_tsv, q) as rank,
                   ts_headline('english', j.text, q, %s) as snippet,
                   j.citation,
                   'judgment' as kind
            {base}
            order by rank desc limit %s offset %s
            """,
            [TS_HEADLINE_OPTS_LONG, *params, limit, offset],
        ).fetchall()
    return [SearchResult(**r) for r in rows], int(total)


# ---------------------------------------------------------------------------
# Unified search (single UNION ALL query for correct global pagination)
# ---------------------------------------------------------------------------

def search_all(query: str, act: str | None = None,
               limit: int = 10, offset: int = 0) -> tuple[list[SearchResult], int]:
    """Search the whole corpus. Returns ``(results, total)`` where ``total``
    is the true match count across all sub-corpora (before limit/offset).

    For scoped search (``act`` set), delegates to the per-corpus search
    function. For unscoped search, runs a single UNION ALL query with a global
    ORDER BY + LIMIT/OFFSET so pagination is correct (the old fan-out approach
    applied offset per-corpus, returning wrong results on page 2+).
    """
    normalized = normalize_act(act)
    if normalized and normalized.lower() in {"constitution", "article", "articles"}:
        return search_articles(query, limit=limit, offset=offset)
    if normalized and normalized.lower() in {"judgment", "judgments", "case", "cases"}:
        return search_judgments(query, limit=limit, offset=offset)
    if normalized:
        return search_sections(query, act=normalized, limit=limit, offset=offset)

    # Unscoped: single UNION ALL over all three corpora with global pagination.
    # Ranks from different corpora are not strictly comparable, so we normalize
    # by dividing each by its sub-corpus max rank (window function) before the
    # global sort. This mitigates the cross-corpus rank-scale conflation.
    union_sql = """
        with matched as (
            select a.short_name as act,
                   's. ' || s.number as ref,
                   s.title,
                   ts_rank(s.search_tsv, q) as raw_rank,
                   ts_headline('english', s.text, q, %s) as snippet,
                   a.citation,
                   'section' as kind
            from sections s, acts a, plainto_tsquery('english', %s) q
            where s.act_id = a.id and s.search_tsv @@ q
            union all
            select 'Constitution', 'art. ' || ar.number, ar.title,
                   ts_rank(ar.search_tsv, q),
                   ts_headline('english', ar.text, q, %s), null, 'article'
            from articles ar, plainto_tsquery('english', %s) q
            where ar.search_tsv @@ q
            union all
            select 'judgment', coalesce(j.citation, j.case_name), j.case_name,
                   ts_rank(j.search_tsv, q),
                   ts_headline('english', j.text, q, %s), j.citation, 'judgment'
            from judgments j, plainto_tsquery('english', %s) q
            where j.search_tsv @@ q
        )
        select act, ref, title,
               case when max(raw_rank) over () > 0
                    then raw_rank / max(raw_rank) over ()
                    else raw_rank end as rank,
               snippet, citation, kind
        from matched
        order by rank desc
        limit %s offset %s
    """
    union_params = [
        TS_HEADLINE_OPTS, query,       # sections headline + query
        TS_HEADLINE_OPTS, query,       # articles headline + query
        TS_HEADLINE_OPTS_LONG, query,  # judgments headline + query
        limit, offset,
    ]
    count_sql = """
        select (
            (select count(*) from sections s, plainto_tsquery('english', %s) q where s.search_tsv @@ q)
            + (select count(*) from articles a, plainto_tsquery('english', %s) q where a.search_tsv @@ q)
            + (select count(*) from judgments j, plainto_tsquery('english', %s) q where j.search_tsv @@ q)
        ) as n
    """
    with _conn() as c:
        total = c.execute(count_sql, (query, query, query)).fetchone()["n"]
        rows = c.execute(union_sql, union_params).fetchall()
    return [SearchResult(**r) for r in rows], int(total)


# ---------------------------------------------------------------------------
# Schedules / amendments
# ---------------------------------------------------------------------------

def list_schedules() -> list[Schedule]:
    with _conn() as c:
        rows = c.execute(
            "select number, title, text from schedules order by number"
        ).fetchall()
    return [
        Schedule(
            number=r["number"], title=r["title"], text=r["text"],
            source="PRS (CC BY 4.0) / Government of India (public domain)",
            source_license="CC BY 4.0",
            as_of=CORPUS_AS_OF,
        )
        for r in rows
    ]


def get_schedule(number: int) -> Schedule | None:
    with _conn() as c:
        row = c.execute(
            "select number, title, text from schedules where number = %s",
            (number,),
        ).fetchone()
    if not row:
        return None
    return Schedule(
        number=row["number"], title=row["title"], text=row["text"],
        source="PRS (CC BY 4.0) / Government of India (public domain)",
        source_license="CC BY 4.0",
        as_of=CORPUS_AS_OF,
    )


def list_amendments(year_from: int | None = None,
                    year_to: int | None = None) -> list[Amendment]:
    params: list[Any] = []
    where = ""
    if year_from is not None:
        where = "where year >= %s"
        params.append(year_from)
    if year_to is not None:
        where = ("where " if not where else where + " and ") + "year <= %s"
        params.append(year_to)
    with _conn() as c:
        rows = c.execute(
            f"select number, year, title, articles_affected, date from amendments {where} "
            f"order by number",
            params,
        ).fetchall()
    return [
        Amendment(
            number=r["number"], year=r["year"], title=r["title"],
            articles_affected=r["articles_affected"], date=r["date"],
            source="PRS (CC BY 4.0)", source_license="CC BY 4.0", as_of=CORPUS_AS_OF,
        )
        for r in rows
    ]


def get_amendment(number: int) -> Amendment | None:
    with _conn() as c:
        row = c.execute(
            "select number, year, title, articles_affected, date from amendments where number = %s",
            (number,),
        ).fetchone()
    if not row:
        return None
    return Amendment(
        number=row["number"], year=row["year"], title=row["title"],
        articles_affected=row["articles_affected"], date=row["date"],
        source="PRS (CC BY 4.0)", source_license="CC BY 4.0", as_of=CORPUS_AS_OF,
    )


def get_amendments_for_article(article: str) -> list[Amendment]:
    """Reverse lookup: which amendments affected a given article number.

    Parses the comma-separated ``articles_affected`` text field since the
    schema doesn't normalize it into a join table yet.
    """
    art = normalize_ref(article)
    if art is None:
        return []
    with _conn() as c:
        rows = c.execute(
            "select number, year, title, articles_affected, date from amendments "
            "where articles_affected is not null order by number"
        ).fetchall()
    results: list[Amendment] = []
    for r in rows:
        affected = (r["articles_affected"] or "")
        # Word-boundary match so "31" doesn't match "314".
        if re.search(rf"\b{re.escape(art)}\b", affected):
            results.append(Amendment(
                number=r["number"], year=r["year"], title=r["title"],
                articles_affected=r["articles_affected"], date=r["date"],
                source="PRS (CC BY 4.0)", source_license="CC BY 4.0", as_of=CORPUS_AS_OF,
            ))
    return results


# ---------------------------------------------------------------------------
# Cross-references (bidirectional)
# ---------------------------------------------------------------------------

def get_cross_refs(act: str, section: str, direction: str = "both") -> list[CrossRef]:
    """Look up cross-references for a section (bidirectional by default).

    ``direction``:
      * ``"from"`` — only outgoing refs (act/section is the source).
      * ``"to"`` — only incoming refs (act/section is the target).
      * ``"both"`` — union of both (default).
    """
    a = normalize_act(act)
    s = normalize_ref(section)
    if a is None or s is None:
        return []
    refs: list[CrossRef] = []
    with _conn() as c:
        if direction in ("from", "both"):
            rows = c.execute(
                "select from_act, from_section, to_act, to_section, kind "
                "from cross_refs where lower(from_act) = lower(%s) and from_section = %s "
                "order by kind, to_act, to_section",
                (a, s),
            ).fetchall()
            refs.extend(CrossRef(**r) for r in rows)
        if direction in ("to", "both"):
            rows = c.execute(
                "select from_act, from_section, to_act, to_section, kind "
                "from cross_refs where lower(to_act) = lower(%s) and to_section = %s "
                "order by kind, from_act, from_section",
                (a, s),
            ).fetchall()
            refs.extend(CrossRef(**r) for r in rows)
    # Dedupe exact duplicate rows. A from->to row and its inverse to->from
    # row have different keys and are both kept (distinct directed edges).
    seen: set[tuple[str, str, str, str, str]] = set()
    unique: list[CrossRef] = []
    for r in refs:
        key = (r.from_act, r.from_section, r.to_act, r.to_section, r.kind)
        if key not in seen:
            seen.add(key)
            unique.append(r)
    return unique


# ---------------------------------------------------------------------------
# Semantic search (pgvector)
# ---------------------------------------------------------------------------

def semantic_search_sections(embedding: list[float], act: str | None = None,
                             limit: int = 5) -> list[SearchResult]:
    sql = """
        select a.short_name as act,
               's. ' || s.number as ref,
               s.title,
               1 - (e.embedding <=> %s::vector) as rank,
               left(s.text, 300) as snippet,
               a.citation,
               'section' as kind
        from section_embeddings e
        join sections s on s.id = e.section_id
        join acts a on a.id = s.act_id
    """
    params: list[Any] = [embedding, embedding]
    if act:
        sql += " where lower(a.short_name) = lower(%s)"
        params.append(act)
    sql += " order by e.embedding <=> %s::vector limit %s"
    with _conn() as c:
        rows = c.execute(sql, params).fetchall()
    return [SearchResult(**r) for r in rows]


def semantic_search_articles(embedding: list[float], limit: int = 5) -> list[SearchResult]:
    sql = """
        select 'Constitution' as act,
               'art. ' || a.number as ref,
               a.title,
               1 - (e.embedding <=> %s::vector) as rank,
               left(a.text, 300) as snippet,
               null as citation,
               'article' as kind
        from article_embeddings e
        join articles a on a.id = e.article_id
        order by e.embedding <=> %s::vector
        limit %s
    """
    with _conn() as c:
        rows = c.execute(sql, (embedding, embedding, limit)).fetchall()
    return [SearchResult(**r) for r in rows]


def semantic_search_judgments(embedding: list[float], limit: int = 5) -> list[SearchResult]:
    sql = """
        select 'judgment' as act,
               coalesce(j.citation, j.case_name) as ref,
               j.case_name as title,
               1 - (e.embedding <=> %s::vector) as rank,
               left(j.text, 300) as snippet,
               j.citation,
               'judgment' as kind
        from judgment_embeddings e
        join judgments j on j.id = e.judgment_id
        order by e.embedding <=> %s::vector
        limit %s
    """
    with _conn() as c:
        rows = c.execute(sql, (embedding, embedding, limit)).fetchall()
    return [SearchResult(**r) for r in rows]


def semantic_search_all(embedding: list[float], act: str | None = None,
                         limit: int = 5) -> list[SearchResult]:
    """Semantic search across the corpus.

    ``act`` optionally scopes to sections of one act. Articles and judgments
    are not scoped by act.
    """
    normalized = normalize_act(act)
    if normalized and normalized.lower() in {"constitution", "article", "articles"}:
        return semantic_search_articles(embedding, limit=limit)
    if normalized and normalized.lower() in {"judgment", "judgments", "case", "cases"}:
        return semantic_search_judgments(embedding, limit=limit)
    if normalized:
        return semantic_search_sections(embedding, act=normalized, limit=limit)

    sec = semantic_search_sections(embedding, limit=limit)
    art = semantic_search_articles(embedding, limit=limit)
    jud = semantic_search_judgments(embedding, limit=limit)
    merged = sec + art + jud
    merged.sort(key=lambda r: r.rank, reverse=True)
    return merged[:limit]


# ---------------------------------------------------------------------------
# Stats / health
# ---------------------------------------------------------------------------

def corpus_stats() -> dict[str, int]:
    """Counts for all corpus tables in a single round-trip (UNION ALL)."""
    with _conn() as c:
        rows = c.execute(
            """
            select 'acts' as k, count(*) as n from acts
            union all select 'sections', count(*) from sections
            union all select 'articles', count(*) from articles
            union all select 'judgments', count(*) from judgments
            union all select 'amendments', count(*) from amendments
            union all select 'schedules', count(*) from schedules
            union all select 'chapters', count(*) from chapters
            union all select 'cross_refs', count(*) from cross_refs
            """
        ).fetchall()
    return {r["k"]: int(r["n"]) for r in rows}


def corpus_as_of() -> date | None:
    """Return the latest ``as_of`` date across acts (cached for 5 minutes).

    Caching avoids an extra round-trip on every search call. Database
    failures return ``None`` without caching so the next call retries.
    """
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
        # Don't cache failures — let the next call retry immediately.
        return None
    _as_of_cache = (now, val)
    return val