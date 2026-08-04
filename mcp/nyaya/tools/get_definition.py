"""get_definition: look up the statutory definition of a legal term.

Targets sections whose **title** contains "definition"/"interpretation" and
promotes them to the top of the result list. Far more precise than appending
" definition" to a generic search query.
"""

from __future__ import annotations

import re

from .. import db
from ..models import SearchResponse
from ._util import run_sync


def register(mcp) -> None:
    @mcp.tool(
        name="get_definition",
        description=(
            "Look up the statutory definition of a legal term. Searches sections whose "
            "title suggests they are definitions (e.g. IPC s.2 'Definitions', BNS s.2 "
            "'Definitions') and extracts the defining sentence. Use this when the user "
            "asks 'what does good faith mean under the IPC?' or 'how is fraud defined in "
            "the BNS?'. Falls back to a general search if no definition section is found."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "Define a legal term"},
    )
    @run_sync
    def get_definition(term: str, act: str | None = None,
                       limit: int = 10) -> SearchResponse:
        """Look up a legal term's statutory definition.

        Args:
            term: The term to define, e.g. 'good faith', 'dishonestly', 'fraud'.
            act: Optional act short-name to scope the search.
            limit: Max hits (1–50, default 10).
        """
        limit = max(1, min(int(limit), 50))
        if not term or not term.strip():
            return SearchResponse(query=term or "", total=0, returned=0, offset=0,
                                  results=[], as_of=db.corpus_as_of(), limit=limit)

        # Search for the term; promote hits whose title contains
        # "definition"/"interpretation" via a custom sort key.
        results, total = db.search_all(term, act=act, limit=limit, offset=0)
        def _is_def(r):
            return bool(r.title and re.search(r"defin|interpret", r.title, re.I))
        results.sort(key=lambda r: (0 if _is_def(r) else 1, -r.rank))
        return SearchResponse(
            query=term, total=total, returned=len(results), offset=0,
            results=results, as_of=db.corpus_as_of(), limit=limit,
        )