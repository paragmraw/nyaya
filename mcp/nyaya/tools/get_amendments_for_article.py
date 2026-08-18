"""get_amendments_for_article: reverse amendment lookup by article number."""

from __future__ import annotations

from .. import db
from ..exceptions import NotFound
from ..models import Document
from ._error import structured_errors
from ._util import run_sync


def register(mcp) -> None:
    @mcp.tool(
        name="get_amendments_for_article",
        description=(
            "Find all Constitutional amendments that affected a specific article. Useful "
            "for tracing the amendment history of a provision, e.g. 'which amendments "
            "changed Article 31 (right to property)?'. Searches the articles_affected "
            "metadata field of each amendment."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "Amendments for an article"},
    )
    @structured_errors
    @run_sync
    def get_amendments_for_article(article: str) -> list[Document]:
        """Find amendments that affected a given article.

        Args:
            article: Article number, e.g. '31', '14', '368', '31A'.
        """
        if not article or not str(article).strip():
            raise NotFound(
                "Article number is required.",
                kind="article",
                hint="Provide an article number like '31' or '21A'.",
            )
        result = db.get_amendments_for_article(article)
        if not result:
            raise NotFound(
                f"No amendments found for Article {article}. "
                "Not all articles have been amended; the amendment may not be in the corpus yet.",
                kind="article",
                hint="Call list_amendments to see all amendments, or semantic_query for topical search.",
            )
        return result
