"""MCP tools for nyaya."""

from .search_law import register as _search_law
from .get_section import register as _get_section
from .get_article import register as _get_article
from .list_acts import register as _list_acts
from .cross_reference import register as _cross_reference
from .semantic_query import register as _semantic_query


def register(mcp) -> None:
    _search_law(mcp)
    _get_section(mcp)
    _get_article(mcp)
    _list_acts(mcp)
    _cross_reference(mcp)
    _semantic_query(mcp)