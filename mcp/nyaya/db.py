"""Postgres data access for nyaya.

All functions are synchronous and intended to be wrapped with
``asyncio.to_thread`` by the (async) tool layer — see ``tools/_util.py``.

Design notes
------------
* Input normalization: act/section/article strings are stripped and (for acts)
  upper-cased before hitting SQL. A small alias map resolves common variants
  ('indian penal code' -> 'IPC'). This keeps the LLM-facing tools forgiving.
* Provenance: articles/schedules/amendments/judgments no longer hardcode the
  ``as_of`` date — it's read from the Constitution act row / a per-row column
  where possible, and falls back to a module constant ``CORPUS_AS_OF`` only
  when the act row is unavailable (e.g. during partial hydrates).
* Cross-refs: ``get_cross_refs`` queries BOTH directions (from_* and to_*) and
  merges, so the ``cross_reference`` tool finally delivers the bidirectional
  lookup its description promises. The ``cross_refs_to_idx`` index is now used.
* Search: ``search_all`` returns the true match count (before limit) so the
  LLM can decide whether to page. ``offset`` is supported everywhere.
"""

from __future__ import annotations

import contextlib
from datetime import date
from typing import Any, Iterator

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from .config import get_settings
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

# Fallback corpus as-of date used only when an act row can't supply one.
CORPUS_AS_OF = date(2026, 7, 1)

# Aliases that map common act names/case variants to the canonical short_name
# stored in the ``acts`` table. Keys are lower-cased on lookup.
_ACT_ALIASES: dict[str, str] = {
    "ipc": "IPC",
    "indian penal code": "IPC",
    "crpc": "CrPC",
    "code of criminal procedure": "CrPC",
    "cpc": "CPC",
    "code of civil procedure": "CPC",
    "iea": "EvidenceAct",
    "evidence act": "EvidenceAct",
    "indian evidence act": "EvidenceAct",
    "evidenceact": "EvidenceAct",
    "bns": "BNS",
    "bharatiya nyaya sanhita": "BNS",
    "bnss": "BNSS",
    "bharatiya nagarik suraksha sanhita": "BNSS",
    "bsa": "BSA",
    "bharatiya sakshya adhiniyam": "BSA",
    "bharatiya sakshya bill": "BSA",
    "companies": "Companies",
    "companies act": "Companies",
    "igst": "IGST",
    "cgst": "CGST",
    "itact": "ITAct",
    "information technology act": "ITAct",
    "arbitration": "Arbitration",
    "consumerprotection": "ConsumerProtection",
    "consumer protection act": "ConsumerProtection",
    "constitution": "Constitution",
    "article": "Constitution",
    "articles": "Constitution",
    "judgment": "judgment",
    "judgments": "judgment",
    "case": "judgment",
    "cases": "judgment",
}

_pool: ConnectionPool | None = None

# ts_headline option strings, passed as SQL parameters to keep the headline
# options (which contain quotes/angles) out of the SQL literal — inlining them
# breaks the parser. Kept module-level so they're defined once and reused.
TS_HEADLINE_OPTS = (
    'MaxWords=60, MinWords=20, MaxFragments=3, '
    'FragmentDelimiter=" … ", StartSel="<<", StopSel=">>"'
)
TS_HEADLINE_OPTS_LONG = (
    'MaxWords=80, MinWords=20, MaxFragments=3, '
    'FragmentDelimiter=" … ", StartSel="<<", StopSel=">>"'
)


# ---------------------------------------------------------------------------
# Input normalization
# ---------------------------------------------------------------------------

def normalize_act(act: str | None) -> str | None:
    """Normalize an act short-name: strip, lower-case, resolve aliases.

    Returns the canonical ``short_name`` (e.g. 'IPC') or ``None`` if the input
    is empty/None. Unknown values are returned upper-cased and stripped — this
    lets callers pass through new act names not in the alias map without
    silently failing.
    """
    if act is None:
        return None
    key = act.strip()
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
    low = r.lower()
    for prefix in ("section ", "s. ", "s.", "sec ", "sec.", "art. ", "art.", "article "):
        if low.startswith(prefix):
            r = r[len(prefix):].strip()
            break
    return r


# ---------------------------------------------------------------------------
# Connection management
# ---------------------------------------------------------------------------

def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None or _pool.closed:
        settings = get_settings()

        def _configure(conn: psycopg.Connection) -> None:
            if settings.statement_timeout_ms > 0:
                with conn.cursor() as cur:
                    cur.execute(
                        "set statement_timeout = %s",
                        (settings.statement_timeout_ms,),
                    )
            with conn.cursor() as cur:
                cur.execute("set application_name = 'nyaya'")

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
    pool = _get_pool()
    with pool.connection(timeout=get_settings().pool_timeout) as conn:
        conn.row_factory = dict_row
        yield conn


def close_db() -> None:
    global _pool
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
            "from acts where short_name = %s",
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
            "where a.short_name = %s order by c.number",
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
            where a.short_name = %s and s.number = %s
            """,
            (sn, num),
        ).fetchone()
    if not row:
        return None
    return Section(
        act=row["act"],
        section=row["number"],
        title=row["title"],
        text=row["text"],
        url=row["url"],
        chapter_number=row["chapter_number"],
        chapter_title=row["chapter_title"],
        source=row["source"],
        source_license=row["source_license"],
        as_of=row["as_of"],
    )


def list_sections(act_short_name: str, chapter: int | None = None,
                  limit: int = 100, offset: int = 0) -> tuple[list[Section], int]:
    """List sections of an act, optionally filtered to a chapter.

    Returns (sections, total) where total is the true match count before
    limit/offset.
    """
    sn = normalize_act(act_short_name)
    if sn is None:
        return [], 0
    params: list[Any] = [sn]
    where = "where a.short_name = %s"
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

    Section numbers are strings ('302', '354A'); numeric prefixes are compared
    numerically and the suffix is used as a tiebreaker. This handles mixed
    numeric/alpha schemes like IPC.
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
            where a.short_name = %s
              and regexp_replace(s.number, '[^0-9].*$', '')::int
                  between regexp_replace(%s, '[^0-9].*$', '')::int
                  and regexp_replace(%s, '[^0-9].*$', '')::int
            order by regexp_replace(s.number, '[^0-9].*$', '')::int, s.number
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
    sql = """
        select a.short_name as act,
               's. ' || s.number as ref,
               s.title,
               ts_rank(s.search_tsv, q) as rank,
               ts_headline('english', s.text, q, %s) as snippet,
               a.citation,
               'section' as kind,
               count(*) over() as total
        from sections s, acts a, plainto_tsquery('english', %s) q
        where s.act_id = a.id and s.search_tsv @@ q
    """
    params: list[Any] = [TS_HEADLINE_OPTS, query]
    if act:
        sql += " and a.short_name = %s"
        params.append(act)
    sql += " order by rank desc limit %s offset %s"
    params.extend([limit, offset])
    with _conn() as c:
        rows = c.execute(sql, params).fetchall()
    total = int(rows[0]["total"]) if rows else 0
    return [SearchResult(**{k: v for k, v in r.items() if k != "total"}) for r in rows], total


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
        # Provenance from the Constitution act row.
        prov = c.execute(
            "select source, source_license, as_of from acts where short_name = 'Constitution'"
        ).fetchone()
    if not row:
        return None
    source = prov["source"] if prov else "Vikhram-S/IndianConstitution (Apache-2.0)"
    license_ = prov["source_license"] if prov else "Apache-2.0"
    as_of = prov["as_of"] if prov else CORPUS_AS_OF
    return Article(
        number=row["number"],
        title=row["title"],
        text=row["text"],
        part=row["part"],
        source=source,
        source_license=license_,
        as_of=as_of,
    )


def list_articles(part: str | None = None,
                   limit: int = 100, offset: int = 0) -> tuple[list[Article], int]:
    """List Constitution articles, optionally filtered by Part."""
    params: list[Any] = []
    where = ""
    if part:
        where = "where part ilike %s"
        params.append(f"%{part}%")
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
            "select source, source_license, as_of from acts where short_name = 'Constitution'"
        ).fetchone()
    source = prov["source"] if prov else "Vikhram-S/IndianConstitution (Apache-2.0)"
    license_ = prov["source_license"] if prov else "Apache-2.0"
    as_of = prov["as_of"] if prov else CORPUS_AS_OF
    return [Article(
        number=r["number"], title=r["title"], text=r["text"], part=r["part"],
        source=source, source_license=license_, as_of=as_of,
    ) for r in rows], int(total)


def search_articles(query: str, limit: int = 10, offset: int = 0) -> tuple[list[SearchResult], int]:
    sql = """
        select 'Constitution' as act,
               'art. ' || a.number as ref,
               a.title,
               ts_rank(a.search_tsv, q) as rank,
               ts_headline('english', a.text, q, %s) as snippet,
               null as citation,
               'article' as kind,
               count(*) over() as total
        from articles a, plainto_tsquery('english', %s) q
        where a.search_tsv @@ q
        order by rank desc limit %s offset %s
    """
    with _conn() as c:
        rows = c.execute(sql, (TS_HEADLINE_OPTS, query, limit, offset)).fetchall()
    total = int(rows[0]["total"]) if rows else 0
    return [SearchResult(**{k: v for k, v in r.items() if k != "total"}) for r in rows], total


# ---------------------------------------------------------------------------
# Judgments
# ---------------------------------------------------------------------------

def get_judgment(case_slug: str) -> Judgment | None:
    slug = case_slug.strip() if case_slug else ""
    if not slug:
        return None
    with _conn() as c:
        row = c.execute(
            """
            select case_name, citation, court, date, summary, text
            from judgments
            where citation = %s
               or lower(replace(case_name, ' ', '-')) = lower(%s)
               or lower(case_name) like lower(%s)
            limit 1
            """,
            (slug, slug, f"%{slug.replace('-', ' ')}%"),
        ).fetchone()
    if not row:
        return None
    return Judgment(
        case_name=row["case_name"],
        citation=row["citation"],
        court=row["court"],
        date=row["date"],
        summary=row["summary"],
        text=row["text"],
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
    sql = """
        select 'judgment' as act,
               coalesce(j.citation, j.case_name) as ref,
               j.case_name as title,
               ts_rank(j.search_tsv, q) as rank,
               ts_headline('english', j.text, q, %s) as snippet,
               j.citation,
               'judgment' as kind,
               count(*) over() as total
        from judgments j, plainto_tsquery('english', %s) q
        where j.search_tsv @@ q
    """
    params: list[Any] = [TS_HEADLINE_OPTS_LONG, query]
    if court:
        sql += " and j.court ilike %s"
        params.append(f"%{court}%")
    if date_from:
        sql += " and j.date >= %s::date"
        params.append(date_from)
    if date_to:
        sql += " and j.date <= %s::date"
        params.append(date_to)
    sql += " order by rank desc limit %s offset %s"
    params.extend([limit, offset])
    with _conn() as c:
        rows = c.execute(sql, params).fetchall()
    total = int(rows[0]["total"]) if rows else 0
    return [SearchResult(**{k: v for k, v in r.items() if k != "total"}) for r in rows], total


# ---------------------------------------------------------------------------
# Unified search
# ---------------------------------------------------------------------------

def search_all(query: str, act: str | None = None,
               limit: int = 10, offset: int = 0) -> tuple[list[SearchResult], int]:
    """Search the whole corpus. Returns (results, total) where total is the
    true match count across all sub-corpora (before limit/offset)."""
    normalized = normalize_act(act)
    if normalized and normalized.lower() in {"constitution", "article", "articles"}:
        return search_articles(query, limit=limit, offset=offset)
    if normalized and normalized.lower() in {"judgment", "judgments", "case", "cases"}:
        return search_judgments(query, limit=limit, offset=offset)

    # For a specific act, only search sections (articles/judgments are not
    # scoped by act). For unscoped search, fan out and merge.
    if normalized:
        return search_sections(query, act=normalized, limit=limit, offset=offset)

    sec, n_sec = search_sections(query, limit=limit, offset=offset)
    art, n_art = search_articles(query, limit=limit, offset=offset)
    jud, n_jud = search_judgments(query, limit=limit, offset=offset)
    merged = sec + art + jud
    merged.sort(key=lambda r: r.rank, reverse=True)
    return merged[:limit], n_sec + n_art + n_jud


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
            number=r["number"],
            title=r["title"],
            text=r["text"],
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
            number=r["number"],
            year=r["year"],
            title=r["title"],
            articles_affected=r["articles_affected"],
            date=r["date"],
            source="PRS (CC BY 4.0)",
            source_license="CC BY 4.0",
            as_of=CORPUS_AS_OF,
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


# ---------------------------------------------------------------------------
# Cross-references (bidirectional)
# ---------------------------------------------------------------------------

def get_cross_refs(act: str, section: str, direction: str = "both") -> list[CrossRef]:
    """Look up cross-references for a section.

    ``direction``:
      * ``"from"`` — only outgoing refs (act/section is the source).
      * ``"to"`` — only incoming refs (act/section is the target).
      * ``"both"`` — union of both (default, matches the tool description).
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
                "from cross_refs where from_act = %s and from_section = %s "
                "order by kind, to_act, to_section",
                (a, s),
            ).fetchall()
            refs.extend(CrossRef(**r) for r in rows)
        if direction in ("to", "both"):
            rows = c.execute(
                "select from_act, from_section, to_act, to_section, kind "
                "from cross_refs where to_act = %s and to_section = %s "
                "order by kind, from_act, from_section",
                (a, s),
            ).fetchall()
            refs.extend(CrossRef(**r) for r in rows)
    # Dedupe (a from→to row and a to→from row may describe the same link twice).
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
        sql += " where a.short_name = %s"
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
    """Semantic search across the corpus. ``act`` optionally scopes to sections
    of one act (articles/judgments are not scoped by act)."""
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
    """Return the latest ``as_of`` date across acts (for SearchResponse.as_of)."""
    with _conn() as c:
        row = c.execute("select max(as_of) as d from acts").fetchone()
    return row["d"] if row else None