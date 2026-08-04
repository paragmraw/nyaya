"""corpus_stats: expose corpus counts as a tool for LLM awareness."""

from __future__ import annotations

from .. import db
from ..models import CorpusStats
from ._util import run_sync


def register(mcp) -> None:
    @mcp.tool(
        name="corpus_stats",
        description=(
            "Return corpus counts (acts, sections, articles, judgments, amendments, "
            "schedules, chapters, cross_refs) and the as-of date. Use this to check "
            "corpus coverage or verify the database is reachable before a complex query."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "Corpus statistics"},
    )
    @run_sync
    def corpus_stats() -> CorpusStats:
        """Return corpus counts and the as-of date."""
        stats = db.corpus_stats()
        return CorpusStats(
            acts=stats.get("acts", 0),
            sections=stats.get("sections", 0),
            articles=stats.get("articles", 0),
            judgments=stats.get("judgments", 0),
            amendments=stats.get("amendments", 0),
            schedules=stats.get("schedules", 0),
            chapters=stats.get("chapters", 0),
            cross_refs=stats.get("cross_refs", 0),
            as_of=db.corpus_as_of(),
        )
