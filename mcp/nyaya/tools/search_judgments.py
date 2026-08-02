"""search_judgments: full-text search across landmark judgments."""

from __future__ import annotations

from .. import db
from ..models import SearchResponse
from ._util import run_sync


def register(mcp) -> None:
    @mcp.tool(
        name="search_judgments",
        description=(
            "Full-text search across landmark Supreme Court judgments, with optional "
            "court and date filters. Returns matching passages with citations and "
            "relevance-ranked snippets. Use this instead of search_law when you specifically "
            "want case law (search_law searches everything including judgments). The "
            "``total`` field gives the true match count for pagination via ``offset``."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "Search judgments"},
    )
    @run_sync
    def search_judgments(
        query: str,
        court: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> SearchResponse:
        """Search landmark judgments.

        Args:
            query: Free-text query, e.g. 'basic structure' or 'right to privacy'.
            court: Optional court name substring, e.g. 'Supreme Court'.
            date_from: Optional ISO date (YYYY-MM-DD); only judgments on or after.
            date_to: Optional ISO date (YYYY-MM-DD); only judgments on or before.
            limit: Max hits (1–50, default 10).
            offset: Pagination offset (default 0).
        """
        if not query or not query.strip():
            return SearchResponse(query=query or "", total=0, returned=0, offset=offset,
                                  results=[], limit=max(1, min(int(limit), 50)))
        limit = max(1, min(int(limit), 50))
        results, total = db.search_judgments(
            query, court=court, date_from=date_from, date_to=date_to,
            limit=limit, offset=offset,
        )
        return SearchResponse(
            query=query, total=total, returned=len(results), offset=offset,
            results=results, as_of=db.corpus_as_of(), limit=limit,
        )