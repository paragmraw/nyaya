"""MCP tools for nyaya."""

from .corpus_stats import register as _corpus_stats
from .cross_reference import register as _cross_reference
from .get_amendments_for_article import register as _get_amendments_for_article
from .get_article import register as _get_article
from .get_chapter import register as _get_chapter
from .get_definition import register as _get_definition
from .get_judgment import register as _get_judgment
from .get_section import register as _get_section
from .get_sections_by_range import register as _get_sections_by_range
from .hybrid_search import register as _hybrid_search
from .list_acts import register as _list_acts
from .resolve_citation import register as _resolve_citation
from .schedules_amendments import register as _schedules_amendments
from .search_by_kind import register as _search_by_kind
from .search_judgments import register as _search_judgments
from .search_law import register as _search_law
from .semantic_query import register as _semantic_query


def register(mcp) -> None:
    _search_law(mcp)
    _get_section(mcp)
    _get_article(mcp)
    _list_acts(mcp)                       # list_acts, list_chapters, list_sections, list_articles, list_judgments
    _cross_reference(mcp)
    _semantic_query(mcp)
    _get_judgment(mcp)
    _search_judgments(mcp)
    _get_sections_by_range(mcp)
    _schedules_amendments(mcp)            # list_schedules, get_schedule, list_amendments, get_amendment
    _get_definition(mcp)
    _corpus_stats(mcp)
    _hybrid_search(mcp)
    _resolve_citation(mcp)
    _get_chapter(mcp)
    _search_by_kind(mcp)
    _get_amendments_for_article(mcp)