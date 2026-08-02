"""MCP tools for nyaya."""

from .cross_reference import register as _cross_reference
from .define import register as _define
from .get_article import register as _get_article
from .get_judgment import register as _get_judgment
from .get_section import register as _get_section
from .get_sections_by_range import register as _get_sections_by_range
from .list_acts import register as _list_acts
from .schedules_amendments import register as _schedules_amendments
from .search_judgments import register as _search_judgments
from .search_law import register as _search_law
from .semantic_query import register as _semantic_query


def register(mcp) -> None:
    _search_law(mcp)
    _get_section(mcp)
    _get_article(mcp)
    _list_acts(mcp)              # registers list_acts, list_chapters, list_sections, list_articles
    _cross_reference(mcp)
    _semantic_query(mcp)
    _get_judgment(mcp)
    _search_judgments(mcp)
    _get_sections_by_range(mcp)
    _schedules_amendments(mcp)   # registers list_schedules, get_schedule, list_amendments, get_amendment
    _define(mcp)