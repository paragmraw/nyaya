"""list_acts / list_chapters / list_sections / list_articles: enumerate the corpus."""

from __future__ import annotations

from .. import db
from ..exceptions import NotFound
from ..models import ActsList, ArticlesList, ChaptersList, SectionsList
from ._util import run_sync


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
    @run_sync
    def list_acts() -> ActsList:
        """List all acts in the corpus."""
        return ActsList(acts=db.list_acts())

    @mcp.tool(
        name="list_chapters",
        description=(
            "List the chapters of a specific act with their section ranges. Useful for "
            "navigating a large act (e.g. IPC has 23 chapters) before fetching individual "
            "sections. Act names are normalized (case-insensitive, aliases accepted)."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "List chapters of an act"},
    )
    @run_sync
    def list_chapters(act: str) -> ChaptersList:
        """List chapters of an act.

        Args:
            act: Act short name or alias, e.g. 'IPC', 'ipc', 'BNS'.
        """
        chapters = db.list_chapters(act)
        if not chapters:
            # Distinguish "act doesn't exist" from "act exists but has no chapters".
            if db.get_act(act) is None:
                raise NotFound(
                    f"Act {act!r} not found in the corpus. Call list_acts to see available acts.",
                    kind="act",
                    hint="Call list_acts to enumerate the corpus.",
                )
            # Act exists but has no chapter structure — return empty list, not an error.
            from ..models import Chapter
            return ChaptersList(act=act, chapters=[])
        return ChaptersList(act=act, chapters=chapters)

    @mcp.tool(
        name="list_sections",
        description=(
            "List sections of an act, optionally filtered to a chapter, with pagination. "
            "Use this when an act has no chapter structure (list_chapters returns empty) or "
            "to enumerate all sections of a specific chapter. Act names are normalized."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "List sections of an act"},
    )
    @run_sync
    def list_sections(act: str, chapter: int | None = None,
                      limit: int = 100, offset: int = 0) -> SectionsList:
        """List sections of an act.

        Args:
            act: Act short name or alias, e.g. 'IPC', 'BNS'.
            chapter: Optional chapter number to filter to.
            limit: Max sections to return (default 100).
            offset: Pagination offset (default 0).
        """
        limit = max(1, min(int(limit), 500))
        sections, _total = db.list_sections(act, chapter=chapter, limit=limit, offset=offset)
        return SectionsList(act=act, sections=sections)

    @mcp.tool(
        name="list_articles",
        description=(
            "List Constitution articles, optionally filtered by Part (e.g. 'Part III' for "
            "Fundamental Rights). Useful for enumerating all articles in a Part before "
            "fetching individual ones with get_article."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "List Constitution articles"},
    )
    @run_sync
    def list_articles(part: str | None = None,
                      limit: int = 100, offset: int = 0) -> ArticlesList:
        """List Constitution articles.

        Args:
            part: Optional Part name substring, e.g. 'Part III', 'Fundamental Rights'.
            limit: Max articles to return (default 100).
            offset: Pagination offset (default 0).
        """
        limit = max(1, min(int(limit), 500))
        articles, _total = db.list_articles(part=part, limit=limit, offset=offset)
        return ArticlesList(articles=articles)