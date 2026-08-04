"""search_by_kind: search filtered by document type (section/article/judgment)."""

from __future__ import annotations

from typing import Literal

from .. import db
from ..models import SearchResponse
from ._util import run_sync


def register(mcp) -> None:
    @mcp.tool(
        name="search_by_kind",
        description=(
            "Full-text search filtered to a specific document type. Use this when you want "
            "only sections, only articles, or only judgments — search_law searches all three. "
            "For example, search_by_kind(query='right to privacy', kind='article') searches "
            "only Constitution articles."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "Search by document type"},
    )
    @run_sync
    def search_by_kind(
        query: str,
        kind: Literal["section", "article", "judgment"] = "section",
        act: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> SearchResponse:
        """Search filtered by document type.

        Args:
            query: Free-text query.
            kind: 'section', 'article', or 'judgment'.
            act: Optional act short-name (only for kind='section').
            limit: Max hits (1–50, default 10).
            offset: Pagination offset (default 0, clamped to >= 0).
        """
        limit = max(1, min(int(limit), 50))
        offset = max(0, int(offset))
        if not query or not query.strip():
            return SearchResponse(query=query or "", total=0, returned=0, offset=offset,
                                  results=[], as_of=db.corpus_as_of(), limit=limit)

        if kind == "section":
            results, total = db.search_sections(query, act=act, limit=limit, offset=offset)
        elif kind == "article":
            results, total = db.search_articles(query, limit=limit, offset=offset)
        elif kind == "judgment":
            results, total = db.search_judgments(query, limit=limit, offset=offset)
        else:
            results, total = db.search_all(query, act=act, limit=limit, offset=offset)
        return SearchResponse(
            query=query, total=total, returned=len(results), offset=offset,
            results=results, as_of=db.corpus_as_of(), limit=limit,
        )
