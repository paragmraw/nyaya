"""MCP tools for nyaya."""

import logging
from typing import Any

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

# (register_fn, tool_name, {tool names the module must land}) triples in
# registration order. 9 register functions producing 16 @mcp.tool decorators
# (list_acts registers 5: list_acts/list_chapters/list_sections/list_articles/
# list_judgments; schedules_amendments registers 4: list_schedules/get_schedule/
# list_amendments/get_amendment).
#
# The expected-name sets back the fail-fast contract in :func:`register`: if a
# module registers a name that already exists (duplicate — FastMCP only logs a
# warning and silently keeps the old tool) or fails to land the names it
# claims (malformed — e.g. an exception swallowed inside FastMCP), startup
# raises instead of serving a silently crippled tool surface.
_REGISTRATIONS: list[tuple[Any, str, frozenset[str]]] = [
    (
        _semantic_query,
        "semantic_query",
        frozenset({"semantic_query"}),
    ),
    (
        _get_section,
        "get_section",
        frozenset({"get_section"}),
    ),
    (
        _get_article,
        "get_article",
        frozenset({"get_article"}),
    ),
    (
        _list_acts,
        "list_acts",
        frozenset({
            "list_acts", "list_chapters", "list_sections",
            "list_articles", "list_judgments",
        }),
    ),
    (
        _cross_reference,
        "cross_reference",
        frozenset({"cross_reference"}),
    ),
    (
        _get_judgment,
        "get_judgment",
        frozenset({"get_judgment"}),
    ),
    (
        _schedules_amendments,
        "schedules_amendments",
        frozenset({"list_schedules", "get_schedule", "list_amendments", "get_amendment"}),
    ),
    (
        _corpus_stats,
        "corpus_stats",
        frozenset({"corpus_stats"}),
    ),
    (
        _get_amendments_for_article,
        "get_amendments_for_article",
        frozenset({"get_amendments_for_article"}),
    ),
]


def _registered_tool_names(mcp: Any) -> set[str] | None:
    """Return the tool names currently registered on ``mcp``, or None if the
    running FastMCP version exposes no readable registry.

    Reads FastMCP's local provider component store (sync, no event loop needed
    — :func:`register` runs at import time). Keys look like ``tool:<name>@``.
    """
    provider = getattr(mcp, "_local_provider", None)
    components = getattr(provider, "_components", None)
    if not isinstance(components, dict):
        return None
    names: set[str] = set()
    for key in components:
        if isinstance(key, str) and key.startswith("tool:"):
            names.add(key[len("tool:"):].split("@", 1)[0])
    return names


def register(mcp: Any) -> None:
    """Register every tool, failing loudly on any registration problem.

    Security-critical surface: a silently skipped or silently shadowed tool
    is worse than a failed boot (matching the middleware fail-fast stance in
    ``server.py``). Raises instead of logging-and-continuing when:
    - a register function raises;
    - a module would register a name that already exists (FastMCP only warns
      and keeps the FIRST registration, so a duplicate is invisible otherwise);
    - a module fails to land the tools it is expected to provide.
    """
    for reg_fn, name, expected in _REGISTRATIONS:
        before = _registered_tool_names(mcp)
        try:
            reg_fn(mcp)
        except Exception as exc:
            raise RuntimeError(
                f"MCP tool registration failed for {name!r}: {exc!r}. "
                "Refusing to start with a partial tool surface."
            ) from exc
        after = _registered_tool_names(mcp)
        if before is None or after is None:
            continue  # registry not introspectable; the raise above is our guard
        duplicates = sorted(expected & before)
        if duplicates:
            raise RuntimeError(
                f"MCP tool registration for {name!r} would overwrite already-registered "
                f"tool(s) {duplicates}. Refusing to start with a partial tool surface."
            )
        missing = sorted(expected - after)
        if missing:
            raise RuntimeError(
                f"MCP tool registration for {name!r} completed but did not land "
                f"tool(s) {missing}. Refusing to start with a partial tool surface."
            )
