"""get_article: fetch a Constitution article by number or citation string."""

from __future__ import annotations

import re

from .. import db
from ..exceptions import NotFound
from ..models import Document
from ._error import structured_errors
from ._util import run_sync

# Matches "Art.21", "Article 21", "art 21", "article21".
_ART_CITATION_RE = re.compile(r"art(?:icle)?\.?\s*(?P<num>\d+[A-Z]?)", re.IGNORECASE)


def register(mcp) -> None:
    @mcp.tool(
        name="get_article",
        description=(
            "Fetch the full text of a Constitution of India article by its number, "
            "including sub-clauses. Handles bare numbers ('21'), suffixed articles "
            "such as '21A', '32', '32A', '51A', and citation strings like 'Art.21' "
            "or 'Article 21'. Article numbers are normalized (whitespace-trimmed, "
            "leading 'art.'/'article ' prefix stripped). "
            "Use semantic_query for topical queries and get_section for non-constitutional acts."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "Get a Constitution article"},
    )
    @structured_errors
    @run_sync
    def get_article(article: str) -> Document:
        """Get a Constitution article by number or citation string.

        Args:
            article: Article number (e.g. '21', '21A', '14', '32', '51A') or a
                citation string like 'Art.21' or 'Article 21'.
        """
        # If the input looks like "Art.21" / "Article 21", extract the number.
        m = _ART_CITATION_RE.match(article or "")
        if m:
            article = m.group("num")
        result = db.get_article(article)
        if result is None:
            raise NotFound(
                f"Article {article} is not in the nyaya corpus. "
                "The Constitution snapshot includes Articles 1–395 and the principal "
                "sub-articles. Try semantic_query with a topical query if you can't find "
                "a specific article.",
                kind="article",
                hint="Call semantic_query with a topical query to find related provisions.",
            )
        return result
