"""define: look up the statutory definition of a legal term."""

from __future__ import annotations

from .. import db
from ..models import SearchResponse
from ._util import run_sync


def register(mcp) -> None:
    @mcp.tool(
        name="define",
        description=(
            "Look up the statutory definition of a legal term across acts. Many acts have "
            "a definitions section (e.g. IPC s.2, BNS s.2) that defines terms like 'good "
            "faith', 'dishonestly', 'fraud', 'criminal conspiracy'. This tool searches for "
            "the term in the titles and text of sections whose title suggests they are "
            "definitions. Returns matching sections with snippets. Use this when the user "
            "asks 'what does X mean under the IPC?' or 'how is fraud defined?'."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "Define a legal term"},
    )
    @run_sync
    def define(term: str, act: str | None = None,
               limit: int = 10) -> SearchResponse:
        """Look up a legal term's statutory definition.

        Args:
            term: The term to define, e.g. 'good faith', 'dishonestly', 'fraud'.
            act: Optional act short-name to scope the search.
            limit: Max hits (1–50, default 10).
        """
        if not term or not term.strip():
            return SearchResponse(query=term or "", total=0, returned=0, offset=0,
                                  results=[], limit=max(1, min(int(limit), 50)))
        limit = max(1, min(int(limit), 50))
        # Search for the term, prioritizing sections whose title contains
        # "definition" or the term itself. We do a scoped search_law and let
        # ts_rank surface definition-heavy sections naturally.
        query = f"{term} definition"
        results, total = db.search_all(query, act=act, limit=limit, offset=0)
        return SearchResponse(
            query=term, total=total, returned=len(results), offset=0,
            results=results, as_of=db.corpus_as_of(), limit=limit,
        )