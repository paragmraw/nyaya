"""get_definition: look up the statutory definition of a legal term.

Uses semantic search + reranking, then promotes hits whose title contains
"definition"/"interpretation" to the top.
"""

from __future__ import annotations

import re

from .. import db
from ..models import Document
from ._util import run_sync, validate_query_length


def register(mcp) -> None:
    @mcp.tool(
        name="get_definition",
        description=(
            "Look up the statutory definition of a legal term. Searches for sections whose "
            "title suggests they are definitions (e.g. IPC s.2 'Definitions', BNS s.2 "
            "'Definitions') and promotes them to the top of the results. Use this when the "
            "user asks 'what does good faith mean under the IPC?' or 'how is fraud defined "
            "in the BNS?'. Falls back to a general semantic search if no definition section."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "Define a legal term"},
    )
    @run_sync
    def get_definition(term: str, act: str | None = None, limit: int = 10) -> list[Document]:
        """Look up a legal term's statutory definition.

        Args:
            term: The term to define, e.g. 'good faith', 'dishonestly', 'fraud'.
            act: Optional act short-name to scope the search.
            limit: Max hits (1–50, default 10).
        """
        limit = max(1, min(int(limit), 50))
        if not term or not term.strip():
            return []
        validate_query_length(term)

        # Use semantic search to find candidate documents, then promote definition-titled ones.
        results = db.get_definition(term, act=act, limit=limit)
        def _is_def(r):
            return bool(r.title and re.search(r"defin|interpret", r.title, re.I))
        results.sort(key=lambda r: (0 if _is_def(r) else 1, r.ref))
        return results[:limit]
