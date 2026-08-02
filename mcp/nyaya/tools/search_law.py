"""search_law: full-text search across the entire corpus."""

from __future__ import annotations

from .. import db
from ..models import SearchResponse


def register(mcp) -> None:
    @mcp.tool(
        name="search_law",
        description=(
            "Full-text search across Indian law: Constitution articles, IPC/CrPC/CPC/"
            "Evidence Act sections, BNS/BNSS/BSA 2023 sections, major commercial statutes, "
            "and landmark Supreme Court judgments. Returns matching passages with citations "
            "and relevance-ranked snippets. Use this when the user asks about a legal topic "
            "in general terms (e.g. 'right to privacy', 'bail for non-bailable offence'). "
            "For a specific section by number use get_section; for a Constitution article "
            "use get_article."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "Search Indian law"},
    )
    def search_law(
        query: str,
        act: str | None = None,
        limit: int = 10,
    ) -> SearchResponse:
        """Search the corpus.

        Args:
            query: Free-text query, e.g. "punishment for murder" or "right to privacy".
            act: Optional act short-name to restrict the search (e.g. 'IPC', 'BNS',
                'Constitution', 'judgment'). When omitted, all acts are searched.
            limit: Maximum number of hits to return (1–50, default 10).
        """
        limit = max(1, min(int(limit), 50))
        results = db.search_all(query, act=act, limit=limit)
        return SearchResponse(query=query, total=len(results), results=results)