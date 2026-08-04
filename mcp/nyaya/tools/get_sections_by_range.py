"""get_sections_by_range: fetch all sections of an act between two numbers."""

from __future__ import annotations

import re

from .. import db
from ..exceptions import NotFound, SearchError
from ..models import SectionsList
from ._util import run_sync

_NUM_PREFIX_RE = re.compile(r"^\d+")


def register(mcp) -> None:
    @mcp.tool(
        name="get_sections_by_range",
        description=(
            "Fetch all sections of an act between two section numbers (inclusive). Useful "
            "for retrieving a whole chapter after list_chapters shows a range like "
            "'Sections 299 to 377' — instead of calling get_section 79 times. Section "
            "numbers are compared by their numeric prefix; the alpha suffix is used as a "
            "tiebreaker. Act names are normalized."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "Get sections by range"},
    )
    @run_sync
    def get_sections_by_range(act: str, start: str, end: str,
                              limit: int = 500) -> SectionsList:
        """Fetch sections in a range.

        Args:
            act: Act short name or alias, e.g. 'IPC', 'BNS'.
            start: Starting section number (inclusive), e.g. '299'. Must have a numeric prefix.
            end: Ending section number (inclusive), e.g. '377'. Must have a numeric prefix.
            limit: Max sections to return (default 500, max 1000).
        """
        limit = max(1, min(int(limit), 1000))
        # Validate start/end have a numeric prefix to avoid a SQL cast error.
        for label, val in (("start", start), ("end", end)):
            v = db.normalize_ref(val) or ""
            if not _NUM_PREFIX_RE.match(v):
                raise SearchError(
                    f"{label} must have a numeric prefix, got {val!r}.",
                    hint="Use a section number like '299' or '354A'.",
                )
        sections = db.get_sections_by_range(act, start, end, limit=limit)
        if not sections:
            if db.get_act(act) is None:
                raise NotFound(
                    f"Act {act!r} not found in the corpus.",
                    kind="act",
                    hint="Call list_acts to enumerate the corpus.",
                )
        return SectionsList(act=act, sections=sections, total=len(sections),
                            offset=0, limit=limit)
