"""get_judgment: fetch a landmark judgment by citation or slug."""

from __future__ import annotations

from .. import db
from ..exceptions import NotFound
from ..models import Judgment
from ._util import run_sync


def register(mcp) -> None:
    @mcp.tool(
        name="get_judgment",
        description=(
            "Fetch the full text of a landmark Supreme Court judgment by citation (e.g. "
            "'AIR 1973 SC 1461') or slugified case name (e.g. "
            "'kesavananda-bharati-v-state-of-kerala'). Returns the full judgment text with "
            "provenance. Use search_judgments or search_law to find judgments by topic first "
            "if you don't know the exact citation."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "Get a judgment by citation"},
    )
    @run_sync
    def get_judgment(case_slug: str) -> Judgment:
        """Get a judgment by citation or slugified case name.

        Args:
            case_slug: Citation (e.g. 'AIR 1973 SC 1461') or dash-separated case
                name (e.g. 'kesavananda-bharati-v-state-of-kerala').
        """
        result = db.get_judgment(case_slug)
        if result is None:
            raise NotFound(
                f"Judgment {case_slug!r} not found in the corpus. "
                "The corpus contains a curated set of landmark SC judgments.",
                kind="judgment",
                hint="Call search_judgments with a topical query to find relevant cases.",
            )
        return result
