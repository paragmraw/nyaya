"""Postgres data access for nyaya."""

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

_pool: ConnectionPool | None = None


def _get_pool() -> ConnectionPool:
    global _pool
    if _pool is None or _pool.closed:
        settings = get_settings()
        _pool = ConnectionPool(
            conninfo=settings.database_url,
            min_size=1,
            max_size=8,
            open=True,
        )
    return _pool


@contextlib.contextmanager
def _conn() -> Iterator[psycopg.Connection]:
    pool = _get_pool()
    with pool.connection(timeout=3.0) as conn:
        conn.row_factory = dict_row
        yield conn


def close_db() -> None:
    global _pool
    if _pool is not None and not _pool.closed:
        _pool.close()
    _pool = None


def list_acts() -> list[Act]:
    with _conn() as c:
        rows = c.execute(
            "select short_name, full_name, year, citation, kind, source, source_license, as_of "
            "from acts order by kind, year nulls last, short_name"
        ).fetchall()
    return [Act(**r) for r in rows]


def get_act(short_name: str) -> Act | None:
    with _conn() as c:
        row = c.execute(
            "select short_name, full_name, year, citation, kind, source, source_license, as_of "
            "from acts where short_name = %s",
            (short_name,),
        ).fetchone()
    return Act(**row) if row else None


def list_chapters(act_short_name: str) -> list[Chapter]:
    with _conn() as c:
        rows = c.execute(
            "select c.number, c.title, c.section_range "
            "from chapters c join acts a on a.id = c.act_id "
            "where a.short_name = %s order by c.number",
            (act_short_name,),
        ).fetchall()
    return [Chapter(**r) for r in rows]


def get_section(act_short_name: str, section_number: str) -> Section | None:
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
            (act_short_name, section_number),
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


def search_sections(query: str, act: str | None = None, limit: int = 10) -> list[SearchResult]:
    sql = """
        select a.short_name as act,
               's. ' || s.number as ref,
               s.title,
               ts_rank(s.search_tsv, q) as rank,
               ts_headline('english', s.text, q,
                   'MaxWords=60, MinWords=20, MaxFragments=3,
                    FragmentDelimiter=" … ", StartSel="<<", StopSel=">>") as snippet,
                a.citation
        from sections s, acts a, plainto_tsquery('english', %s) q
        where s.act_id = a.id and s.search_tsv @@ q
    """
    params: list[Any] = [query]
    if act:
        sql += " and a.short_name = %s"
        params.append(act)
    sql += " order by rank desc limit %s"
    params.append(limit)
    with _conn() as c:
        rows = c.execute(sql, params).fetchall()
    return [SearchResult(**r) for r in rows]


def get_article(number: str) -> Article | None:
    with _conn() as c:
        row = c.execute(
            "select number, title, text, part from articles where number = %s",
            (number,),
        ).fetchone()
    if not row:
        return None
    return Article(
        number=row["number"],
        title=row["title"],
        text=row["text"],
        part=row["part"],
        source="Vikhram-S/IndianConstitution (Apache-2.0) + PRS schedules (CC BY 4.0)",
        source_license="Apache-2.0",
        as_of=date(2026, 7, 1),
    )


def search_articles(query: str, limit: int = 10) -> list[SearchResult]:
    sql = """
        select 'Constitution' as act,
               'art. ' || a.number as ref,
               a.title,
               ts_rank(a.search_tsv, q) as rank,
               ts_headline('english', a.text, q,
                   'MaxWords=60, MinWords=20, MaxFragments=3,
                    FragmentDelimiter=" … ", StartSel="<<", StopSel=">>") as snippet,
               null as citation
        from articles a, plainto_tsquery('english', %s) q
        where a.search_tsv @@ q
        order by rank desc limit %s
    """
    with _conn() as c:
        rows = c.execute(sql, (query, limit)).fetchall()
    return [SearchResult(**r) for r in rows]


def get_judgment(case_slug: str) -> Judgment | None:
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
            (case_slug, case_slug, f"%{case_slug.replace('-', ' ')}%"),
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
        as_of=date(2026, 7, 1),
    )


def search_judgments(query: str, limit: int = 10) -> list[SearchResult]:
    sql = """
        select 'judgment' as act,
               coalesce(j.citation, j.case_name) as ref,
               j.case_name as title,
               ts_rank(j.search_tsv, q) as rank,
               ts_headline('english', j.text, q,
                   'MaxWords=80, MinWords=20, MaxFragments=3,
                    FragmentDelimiter=" … ", StartSel="<<", StopSel=">>") as snippet,
               j.citation
        from judgments j, plainto_tsquery('english', %s) q
        where j.search_tsv @@ q
        order by rank desc limit %s
    """
    with _conn() as c:
        rows = c.execute(sql, (query, limit)).fetchall()
    return [SearchResult(**r) for r in rows]


def search_all(query: str, act: str | None = None, limit: int = 10) -> list[SearchResult]:
    if act and act.lower() in {"constitution", "article", "articles"}:
        return search_articles(query, limit=limit)
    if act and act.lower() in {"judgment", "judgments", "case", "cases"}:
        return search_judgments(query, limit=limit)

    sec = search_sections(query, act=act, limit=limit)
    art = search_articles(query, limit=limit) if not act else []
    jud = search_judgments(query, limit=limit) if not act else []
    merged = sec + art + jud
    merged.sort(key=lambda r: r.rank, reverse=True)
    return merged[:limit]


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
            as_of=date(2026, 7, 1),
        )
        for r in rows
    ]


def list_amendments() -> list[Amendment]:
    with _conn() as c:
        rows = c.execute(
            "select number, year, title, articles_affected, date from amendments order by number"
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
            as_of=date(2026, 7, 1),
        )
        for r in rows
    ]


def get_cross_refs(act: str, section: str) -> list[CrossRef]:
    with _conn() as c:
        rows = c.execute(
            "select from_act, from_section, to_act, to_section, kind "
            "from cross_refs where from_act = %s and from_section = %s "
            "order by kind, to_act, to_section",
            (act, section),
        ).fetchall()
    return [CrossRef(**r) for r in rows]


def semantic_search_sections(embedding: list[float], limit: int = 5) -> list[SearchResult]:
    sql = """
        select a.short_name as act,
               's. ' || s.number as ref,
               s.title,
               1 - (e.embedding <=> %s::vector) as rank,
               left(s.text, 300) as snippet,
               a.citation
        from section_embeddings e
        join sections s on s.id = e.section_id
        join acts a on a.id = s.act_id
        order by e.embedding <=> %s::vector
        limit %s
    """
    with _conn() as c:
        rows = c.execute(sql, (embedding, embedding, limit)).fetchall()
    return [SearchResult(**r) for r in rows]


def semantic_search_articles(embedding: list[float], limit: int = 5) -> list[SearchResult]:
    sql = """
        select 'Constitution' as act,
               'art. ' || a.number as ref,
               a.title,
               1 - (e.embedding <=> %s::vector) as rank,
               left(a.text, 300) as snippet,
               null as citation
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
               j.citation
        from judgment_embeddings e
        join judgments j on j.id = e.judgment_id
        order by e.embedding <=> %s::vector
        limit %s
    """
    with _conn() as c:
        rows = c.execute(sql, (embedding, embedding, limit)).fetchall()
    return [SearchResult(**r) for r in rows]


def semantic_search_all(embedding: list[float], limit: int = 5) -> list[SearchResult]:
    sec = semantic_search_sections(embedding, limit=limit)
    art = semantic_search_articles(embedding, limit=limit)
    jud = semantic_search_judgments(embedding, limit=limit)
    merged = sec + art + jud
    merged.sort(key=lambda r: r.rank, reverse=True)
    return merged[:limit]


def corpus_stats() -> dict[str, int]:
    with _conn() as c:
        n_acts = c.execute("select count(*) as n from acts").fetchone()["n"]
        n_sections = c.execute("select count(*) as n from sections").fetchone()["n"]
        n_articles = c.execute("select count(*) as n from articles").fetchone()["n"]
        n_judgments = c.execute("select count(*) as n from judgments").fetchone()["n"]
        n_amendments = c.execute("select count(*) as n from amendments").fetchone()["n"]
        n_schedules = c.execute("select count(*) as n from schedules").fetchone()["n"]
    return {
        "acts": n_acts,
        "sections": n_sections,
        "articles": n_articles,
        "judgments": n_judgments,
        "amendments": n_amendments,
        "schedules": n_schedules,
    }