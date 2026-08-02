"""search_law: full-text search across the entire corpus."""

from __future__ import annotations

from .. import db
from ..models import SearchResponse
from ._util import run_sync


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
            "use get_article. The ``total`` field gives the true match count (before "
            "limit/offset) so you can page with the ``offset`` parameter."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "Search Indian law"},
    )
    @run_sync
    def search_law(
        query: str,
        act: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> SearchResponse:
        """Search the corpus.

        Args:
            query: Free-text query, e.g. "punishment for murder" or "right to privacy".
                Must be non-empty.
            act: Optional act short-name to restrict the search. Accepts aliases
                like 'ipc', 'Indian Penal Code', 'Constitution', 'judgment'. When
                omitted, all acts + articles + judgments are searched.
            limit: Maximum number of hits to return (1–50, default 10).
            offset: Number of hits to skip for pagination (default 0). Use with
                ``limit`` to page through large result sets.
        """
        if not query or not query.strip():
            return SearchResponse(query=query or "", total=0, returned=0, offset=offset,
                                  results=[], limit=max(1, min(int(limit), 50)))
        limit = max(1, min(int(limit), 50))
        results, total = db.search_all(query, act=act, limit=limit, offset=offset)
        return SearchResponse(
            query=query, total=total, returned=len(results), offset=offset,
            results=results, as_of=db.corpus_as_of(), limit=limit,
        )