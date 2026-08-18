"""get_judgment: fetch a landmark judgment by citation or case name slug."""

from __future__ import annotations

from .. import db
from ..exceptions import NotFound
from ..models import Document
from ._util import run_sync


def register(mcp) -> None:
    @mcp.tool(
        name="get_judgment",
        description=(
            "Fetch the full text of a landmark Supreme Court judgment by its citation "
            "or case-name slug. Matches exact citation ('AIR 1973 SC 1461'), slugified "
            "case name ('kesavananda-bharati-v-state-of-kerala'), or a fuzzy case-name "
            "substring (only for slugs ≥ 8 chars to avoid false matches). "
            "Use semantic_query for topical judgment search."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "Get a judgment"},
    )
    @run_sync
    def get_judgment(case_slug: str) -> Document:
        """Get a judgment by citation or case slug.

        Args:
            case_slug: Citation (e.g. 'AIR 1973 SC 1461'), case name, or slugified name.
        """
        result = db.get_judgment(case_slug)
        if result is None:
            raise NotFound(
                f"Judgment {case_slug!r} not found. The corpus contains a curated set of "
                "landmark Supreme Court judgments. Use semantic_query for topical search.",
                kind="judgment",
                hint="Call semantic_query with a topic to find relevant judgments.",
            )
        return result
