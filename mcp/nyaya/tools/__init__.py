"""MCP tools for nyaya."""

import logging

from .corpus_stats import register as _corpus_stats
from .cross_reference import register as _cross_reference
from .get_amendments_for_article import register as _get_amendments_for_article
from .get_article import register as _get_article
from .get_judgment import register as _get_judgment
from .get_section import register as _get_section
from .list_acts import register as _list_acts
from .schedules_amendments import register as _schedules_amendments
from .semantic_query import register as _semantic_query

log = logging.getLogger("nyaya.tools")

# (register_fn, tool_name) pairs in registration order.
# 9 register functions producing 16 @mcp.tool decorators
# (list_acts registers 5: list_acts/list_chapters/list_sections/list_articles/list_judgments;
#  schedules_amendments registers 4: list_schedules/get_schedule/list_amendments/get_amendment).
_REGISTRATIONS = [
    (_semantic_query, "semantic_query"),
    (_get_section, "get_section"),
    (_get_article, "get_article"),
    (_list_acts, "list_acts"),
    (_cross_reference, "cross_reference"),
    (_get_judgment, "get_judgment"),
    (_schedules_amendments, "schedules_amendments"),
    (_corpus_stats, "corpus_stats"),
    (_get_amendments_for_article, "get_amendments_for_article"),
]


def register(mcp) -> None:
    for reg_fn, name in _REGISTRATIONS:
        try:
            reg_fn(mcp)
        except Exception:
            log.exception("Failed to register tool %r; continuing with remaining tools.", name)
