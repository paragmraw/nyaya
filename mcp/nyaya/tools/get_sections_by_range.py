"""get_sections_by_range: fetch all sections of an act between two numbers."""

from __future__ import annotations

from .. import db
from ..exceptions import NotFound
from ..models import SectionsList
from ._util import run_sync


def register(mcp) -> None:
    @mcp.tool(
        name="get_sections_by_range",
        description=(
            "Fetch all sections of an act between two section numbers (inclusive). Useful "
            "for retrieving a whole chapter after list_chapters shows a range like "
            "'Sections 299 to 377' — instead of calling get_section 79 times. Section "
            "numbers are compared by their numeric prefix. Act names are normalized."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "Get sections by range"},
    )
    @run_sync
    def get_sections_by_range(act: str, start: str, end: str,
                              limit: int = 500) -> SectionsList:
        """Fetch sections in a range.

        Args:
            act: Act short name or alias, e.g. 'IPC', 'BNS'.
            start: Starting section number (inclusive), e.g. '299'.
            end: Ending section number (inclusive), e.g. '377'.
            limit: Max sections to return (default 500, to guard against huge ranges).
        """
        limit = max(1, min(int(limit), 1000))
        sections = db.get_sections_by_range(act, start, end, limit=limit)
        if not sections:
            if db.get_act(act) is None:
                raise NotFound(
                    f"Act {act!r} not found in the corpus.",
                    kind="act",
                    hint="Call list_acts to enumerate the corpus.",
                )
        return SectionsList(act=act, sections=sections)