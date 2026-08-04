"""get_amendments_for_article: reverse amendment lookup by article number."""

from __future__ import annotations

from .. import db
from ..models import AmendmentsList
from ._util import run_sync


def register(mcp) -> None:
    @mcp.tool(
        name="get_amendments_for_article",
        description=(
            "Find all Constitutional amendments that affected a specific article. Useful "
            "for tracing the amendment history of a provision, e.g. 'which amendments "
            "changed Article 31 (right to property)?'. Searches the articles_affected text "
            "field of each amendment."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "Amendments for an article"},
    )
    @run_sync
    def get_amendments_for_article(article: str) -> AmendmentsList:
        """Find amendments that affected a given article.

        Args:
            article: Article number, e.g. '31', '14', '368'.
        """
        ams = db.get_amendments_for_article(article)
        return AmendmentsList(amendments=ams)
