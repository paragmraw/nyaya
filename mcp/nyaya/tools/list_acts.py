"""list_acts / list_chapters / list_sections / list_articles / list_judgments: enumerate the corpus."""

from __future__ import annotations

from .. import db
from ..exceptions import NotFound
from ..models import (
    ActsList, AmendmentsList, ArticlesList, ChaptersList, JudgmentsList, SectionsList,
)
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
            if db.get_act(act) is None:
                raise NotFound(
                    f"Act {act!r} not found in the corpus. Call list_acts to see available acts.",
                    kind="act",
                    hint="Call list_acts to enumerate the corpus.",
                )
            return ChaptersList(act=act, chapters=[])
        return ChaptersList(act=act, chapters=chapters)

    @mcp.tool(
        name="list_sections",
        description=(
            "List sections of an act, optionally filtered to a chapter, with pagination. "
            "Use this when an act has no chapter structure (list_chapters returns empty) or "
            "to enumerate all sections of a specific chapter. Act names are normalized. "
            "The ``total`` field gives the true section count for pagination."
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
            limit: Max sections to return (default 100, max 500).
            offset: Pagination offset (default 0, clamped to >= 0).
        """
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        sections, total = db.list_sections(act, chapter=chapter, limit=limit, offset=offset)
        return SectionsList(act=act, sections=sections, total=total, offset=offset, limit=limit)

    @mcp.tool(
        name="list_articles",
        description=(
            "List Constitution articles, optionally filtered by Part (e.g. 'Part III' for "
            "Fundamental Rights). Useful for enumerating all articles in a Part before "
            "fetching individual ones with get_article. The ``total`` field gives the true "
            "article count for pagination."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "List Constitution articles"},
    )
    @run_sync
    def list_articles(part: str | None = None,
                      limit: int = 100, offset: int = 0) -> ArticlesList:
        """List Constitution articles.

        Args:
            part: Optional Part name substring, e.g. 'Part III', 'Fundamental Rights'.
            limit: Max articles to return (default 100, max 500).
            offset: Pagination offset (default 0, clamped to >= 0).
        """
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
        articles, total = db.list_articles(part=part, limit=limit, offset=offset)
        return ArticlesList(articles=articles, total=total, offset=offset, limit=limit)

    @mcp.tool(
        name="list_judgments",
        description=(
            "List landmark judgments in the corpus, with pagination. Use this to browse "
            "available cases before fetching full text with get_judgment or searching with "
            "search_judgments."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "List landmark judgments"},
    )
    @run_sync
    def list_judgments(limit: int = 50, offset: int = 0) -> JudgmentsList:
        """List all landmark judgments.

        Args:
            limit: Max judgments to return (default 50, max 1000).
            offset: Pagination offset (default 0, clamped to >= 0).
        """
        limit = max(1, min(int(limit), 1000))
        offset = max(0, int(offset))
        judgments, total = db.list_judgments(limit=limit, offset=offset)
        return JudgmentsList(judgments=judgments, total=total, offset=offset, limit=limit)