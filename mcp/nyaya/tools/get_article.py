"""get_article: fetch a Constitution article by number."""

from __future__ import annotations

from .. import db
from ..exceptions import NotFound
from ..models import Document
from ._util import run_sync


def register(mcp) -> None:
    @mcp.tool(
        name="get_article",
        description=(
            "Fetch the full text of a Constitution of India article by its number, "
            "including sub-clauses. Handles bare numbers ('21'), and suffixed articles "
            "such as '21A', '32', '32A', '51A'. Article numbers are normalized "
            "(whitespace-trimmed, leading 'art.'/'article ' prefix stripped). "
            "Use semantic_query for topical queries and get_section for non-constitutional acts."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "Get a Constitution article"},
    )
    @run_sync
    def get_article(article: str) -> Document:
        """Get a Constitution article by number.

        Args:
            article: Article number, e.g. '21', '21A', '14', '32', '51A'.
        """
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
