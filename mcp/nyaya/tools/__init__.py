"""MCP tools for nyaya."""

import logging

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

log = logging.getLogger("nyaya.tools")

# (register_fn, tool_name) pairs in registration order.
_REGISTRATIONS = [
    (_search_law, "search_law"),
    (_get_section, "get_section"),
    (_get_article, "get_article"),
    (_list_acts, "list_acts"),
    (_cross_reference, "cross_reference"),
    (_semantic_query, "semantic_query"),
    (_get_judgment, "get_judgment"),
    (_search_judgments, "search_judgments"),
    (_get_sections_by_range, "get_sections_by_range"),
    (_schedules_amendments, "schedules_amendments"),
    (_get_definition, "get_definition"),
    (_corpus_stats, "corpus_stats"),
    (_hybrid_search, "hybrid_search"),
    (_resolve_citation, "resolve_citation"),
    (_get_chapter, "get_chapter"),
    (_search_by_kind, "search_by_kind"),
    (_get_amendments_for_article, "get_amendments_for_article"),
]


def register(mcp) -> None:
    for reg_fn, name in _REGISTRATIONS:
        try:
            reg_fn(mcp)
        except Exception:
            log.exception("Failed to register tool %r; continuing with remaining tools.", name)
