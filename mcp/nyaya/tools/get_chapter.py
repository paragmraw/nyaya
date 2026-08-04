"""get_chapter: fetch all sections of a chapter by act + chapter number."""

from __future__ import annotations

from .. import db
from ..exceptions import NotFound
from ..models import ChapterWithSections
from ._util import run_sync


def register(mcp) -> None:
    @mcp.tool(
        name="get_chapter",
        description=(
            "Fetch a specific chapter of an act along with all its sections. Use this "
            "after list_chapters to retrieve the full text of every section in a chapter "
            "in one call (instead of calling get_section N times). Act names are normalized."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "Get a chapter with sections"},
    )
    @run_sync
    def get_chapter(act: str, chapter: int) -> ChapterWithSections:
        """Get a chapter and all its sections.

        Args:
            act: Act short name or alias, e.g. 'IPC', 'BNS'.
            chapter: Chapter number, e.g. 16.
        """
        chapters = db.list_chapters(act)
        ch = next((c for c in chapters if c.number == chapter), None)
        if ch is None:
            if db.get_act(act) is None:
                raise NotFound(
                    f"Act {act!r} not found in the corpus.",
                    kind="act",
                    hint="Call list_acts to enumerate the corpus.",
                )
            raise NotFound(
                f"Chapter {chapter} not found in act {act!r}.",
                kind="chapter",
                hint="Call list_chapters to see available chapters.",
            )
        sections, _ = db.list_sections(act, chapter=chapter, limit=500)
        return ChapterWithSections(
            act=act, number=ch.number, title=ch.title,
            section_range=ch.section_range, sections=sections,
        )
