"""Shared DB helper for ingestion scripts.

Separate from ``nyaya.db`` (the server's read layer) because ingestion does
bulk writes, upserts, and DDL.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from ..config import get_settings

# Resolve schema.sql relative to this file, not the CWD.
_SCHEMA_PATH = Path(__file__).resolve().parent.parent.parent / "scripts" / "schema.sql"


def _split_sql(sql: str) -> list[str]:
    """Split a SQL string into individual statements on ``;``.

    Respects dollar-quoted strings (``$$ ... $$`` or ``$tag$ ... $tag$``) so
    that function/trigger bodies containing ``;`` are not split mid-body.
    Statements that are empty or whitespace-only after stripping are dropped.
    """
    statements: list[str] = []
    buf: list[str] = []
    i = 0
    n = len(sql)
    dollar_tag: str | None = None
    while i < n:
        ch = sql[i]
        if dollar_tag is not None:
            if ch == "$":
                # Try to match the closing tag (e.g. $$ or $tag$).
                j = i + 1
                while j < n and (sql[j].isalnum() or sql[j] == "_"):
                    j += 1
                if j < n and sql[j] == "$":
                    candidate = sql[i : j + 1]
                    if candidate == dollar_tag:
                        buf.append(candidate)
                        dollar_tag = None
                        i = j + 1
                        continue
            buf.append(ch)
            i += 1
            continue
        # Not inside a dollar quote.
        if ch == "$":
            # Look ahead for an opening dollar tag: $ ... $
            j = i + 1
            while j < n and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            if j < n and sql[j] == "$":
                dollar_tag = sql[i : j + 1]
                buf.append(dollar_tag)
                i = j + 1
                continue
            buf.append(ch)
            i += 1
            continue
        if ch == ";":
            statements.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    tail = "".join(buf)
    if tail.strip():
        statements.append(tail)
    return statements


class IngestDB:
    """Synchronous connection wrapper for ingestion scripts."""

    def __init__(self, database_url: str | None = None) -> None:
        self._database_url = database_url or get_settings().database_url
        self._conn: psycopg.Connection | None = None

    def __enter__(self) -> IngestDB:
        self.connect()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def connect(self) -> None:
        if self._conn is None or self._conn.closed:
            self._conn = psycopg.connect(self._database_url, row_factory=dict_row)
            self._conn.autocommit = False

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()
        self._conn = None

    @property
    def conn(self) -> psycopg.Connection:
        if self._conn is None:
            self.connect()
        assert self._conn is not None
        return self._conn

    def apply_schema(self, schema_sql_path: str | Path | None = None) -> None:
        """Apply the schema SQL file. Defaults to the package-bundled schema.sql.

        The path is resolved against the package root (not the CWD) to prevent
        path-injection via the working directory.
        """
        path = Path(schema_sql_path) if schema_sql_path else _SCHEMA_PATH
        with open(path, encoding="utf-8") as f:
            sql = f.read()
        for stmt in _split_sql(sql):
            if stmt.strip():
                with self.conn.cursor() as cur:
                    cur.execute(stmt)
        self.conn.commit()

    def upsert_act(
        self,
        *,
        short_name: str,
        full_name: str,
        kind: str,
        source: str,
        year: int | None = None,
        citation: str | None = None,
        source_license: str | None = None,
        as_of: date | None = None,
    ) -> str:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into acts (short_name, full_name, year, citation, kind, source, source_license, as_of)
                values (%s, %s, %s, %s, %s, %s, %s, %s)
                on conflict (short_name) do update set
                    full_name = excluded.full_name,
                    year = excluded.year,
                    citation = excluded.citation,
                    kind = excluded.kind,
                    source = excluded.source,
                    source_license = excluded.source_license,
                    as_of = excluded.as_of
                returning id
                """,
                (short_name, full_name, year, citation, kind, source, source_license, as_of),
            )
            return str(cur.fetchone()["id"])

    def upsert_chapter(self, *, act_id: str, number: int, title: str, section_range: str | None = None) -> str:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into chapters (act_id, number, title, section_range)
                values (%s, %s, %s, %s)
                on conflict (act_id, number) do update set
                    title = excluded.title,
                    section_range = excluded.section_range
                returning id
                """,
                (act_id, number, title, section_range),
            )
            return str(cur.fetchone()["id"])

    def upsert_section(
        self,
        *,
        act_id: str,
        number: str,
        text: str,
        title: str | None = None,
        chapter_id: str | None = None,
        url: str | None = None,
    ) -> str:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into sections (act_id, chapter_id, number, title, text, url)
                values (%s, %s, %s, %s, %s, %s)
                on conflict (act_id, number) do update set
                    chapter_id = excluded.chapter_id,
                    title = excluded.title,
                    text = excluded.text,
                    url = excluded.url
                returning id
                """,
                (act_id, chapter_id, number, title, text, url),
            )
            return str(cur.fetchone()["id"])

    def upsert_article(
        self,
        *,
        number: str,
        title: str,
        text: str,
        part: str | None = None,
    ) -> str:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into articles (number, title, text, part)
                values (%s, %s, %s, %s)
                on conflict (number) do update set
                    title = excluded.title,
                    text = excluded.text,
                    part = excluded.part
                returning id
                """,
                (number, title, text, part),
            )
            return str(cur.fetchone()["id"])

    def upsert_schedule(self, *, number: int, title: str, text: str) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into schedules (number, title, text)
                values (%s, %s, %s)
                on conflict (number) do update set
                    title = excluded.title,
                    text = excluded.text
                """,
                (number, title, text),
            )

    def upsert_amendment(
        self,
        *,
        number: int,
        year: int,
        title: str,
        articles_affected: str | None = None,
        date: date | None = None,
    ) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into amendments (number, year, title, articles_affected, date)
                values (%s, %s, %s, %s, %s)
                on conflict (number) do update set
                    year = excluded.year,
                    title = excluded.title,
                    articles_affected = excluded.articles_affected,
                    date = excluded.date
                """,
                (number, year, title, articles_affected, date),
            )

    def upsert_judgment(
        self,
        *,
        case_name: str,
        text: str,
        citation: str | None = None,
        court: str = "Supreme Court of India",
        date: date | None = None,
        summary: str | None = None,
    ) -> str:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into judgments (case_name, citation, court, date, summary, text)
                values (%s, %s, %s, %s, %s, %s)
                on conflict (case_name) do update set
                    citation = excluded.citation,
                    court = excluded.court,
                    date = excluded.date,
                    summary = excluded.summary,
                    text = excluded.text
                returning id
                """,
                (case_name, citation, court, date, summary, text),
            )
            return str(cur.fetchone()["id"])

    def add_cross_ref(
        self,
        *,
        from_act: str,
        from_section: str,
        to_act: str,
        to_section: str,
        kind: str,
    ) -> None:
        with self.conn.cursor() as cur:
            cur.execute(
                """
                insert into cross_refs (from_act, from_section, to_act, to_section, kind)
                values (%s, %s, %s, %s, %s)
                on conflict do nothing
                """,
                (from_act, from_section, to_act, to_section, kind),
            )

    def upsert_embedding(self, *, table: str, owner_id: str, embedding: list[float]) -> None:
        if table not in {"section", "article", "judgment"}:
            raise ValueError(
                f"Invalid embedding table {table!r}; expected one of: section, article, judgment."
            )
        col = f"{table}_id"
        with self.conn.cursor() as cur:
            cur.execute(
                f"""
                insert into {table}_embeddings ({col}, embedding)
                values (%s, %s::vector)
                on conflict ({col}) do update set embedding = excluded.embedding
                """,
                (owner_id, embedding),
            )

    def fetch_all(self, sql: str, params: Iterable[Any] | None = None) -> list[dict[str, Any]]:
        with self.conn.cursor() as cur:
            cur.execute(sql, list(params) if params else [])
            return cur.fetchall()

    def commit(self) -> None:
        self.conn.commit()

    def counts(self) -> dict[str, int]:
        rows = self.fetch_all(
            """
            select 'acts' as k, count(*) as n from acts
            union all select 'sections', count(*) from sections
            union all select 'articles', count(*) from articles
            union all select 'judgments', count(*) from judgments
            union all select 'amendments', count(*) from amendments
            union all select 'schedules', count(*) from schedules
            union all select 'chapters', count(*) from chapters
            union all select 'cross_refs', count(*) from cross_refs
            union all select 'section_embeddings', count(*) from section_embeddings
            union all select 'article_embeddings', count(*) from article_embeddings
            union all select 'judgment_embeddings', count(*) from judgment_embeddings
            """
        )
        return {r["k"]: int(r["n"]) for r in rows}

    def print_counts(self) -> None:
        print(json.dumps(self.counts(), indent=2))
