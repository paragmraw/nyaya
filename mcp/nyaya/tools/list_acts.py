"""list_acts / list_chapters: enumerate the corpus."""

from __future__ import annotations

from .. import db
from ..exceptions import NotFound
from ..models import ActsList, ChaptersList


def register(mcp) -> None:
    @mcp.tool(
        name="list_acts",
        description=(
            "List all acts available in the nyaya corpus with provenance (source, license, "
            "as-of date). Use this first to discover what's searchable, then call "
            "list_chapters for a specific act's table of contents, or get_section to read a "
            "section."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "List available acts"},
    )
    def list_acts() -> ActsList:
        """List all acts in the corpus."""
        return ActsList(acts=db.list_acts())

    @mcp.tool(
        name="list_chapters",
        description=(
            "List the chapters of a specific act with their section ranges. Useful for "
            "navigating a large act (e.g. IPC has 23 chapters) before fetching individual "
            "sections."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "List chapters of an act"},
    )
    def list_chapters(act: str) -> ChaptersList:
        """List chapters of an act.

        Args:
            act: Act short name, e.g. 'IPC', 'CrPC', 'BNS'.
        """
        chapters = db.list_chapters(act)
        if not chapters:
            raise NotFound(
                f"Act {act!r} not found or has no chapter structure in the corpus. "
                "Call list_acts to see available acts."
            )
        return ChaptersList(act=act, chapters=chapters)