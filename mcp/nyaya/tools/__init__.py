"""MCP v2.0 12-tool surface and fail-fast registration guard for Nyaya."""

from __future__ import annotations

import logging
from typing import Any

from .browse_statute import browse_statute
from .browse_statute import register as _browse_statute
from .compare_provisions import compare_provisions
from .compare_provisions import register as _compare_provisions
from .corpus_status import corpus_status
from .corpus_status import register as _corpus_status
from .cross_reference import cross_reference
from .cross_reference import register as _cross_reference
from .get_judgment import get_judgment
from .get_judgment import register as _get_judgment
from .get_provision import get_provision
from .get_provision import register as _get_provision
from .get_provision_history import get_provision_history
from .get_provision_history import register as _get_provision_history
from .judgment_analytics import judgment_analytics
from .judgment_analytics import register as _judgment_analytics
from .search_judgments import register as _search_judgments
from .search_judgments import search_judgments
from .search_statutes import register as _search_statutes
from .search_statutes import search_statutes
from .trace_precedent import register as _trace_precedent
from .trace_precedent import trace_precedent
from .verify_citations import register as _verify_citations
from .verify_citations import verify_citations

log = logging.getLogger("nyaya.tools")

__all__ = [
    "browse_statute",
    "compare_provisions",
    "corpus_status",
    "cross_reference",
    "get_judgment",
    "get_provision",
    "get_provision_history",
    "judgment_analytics",
    "register",
    "search_judgments",
    "search_statutes",
    "trace_precedent",
    "verify_citations",
]

# Exact 12 canonical v2 tools in registration order.
_REGISTRATIONS: list[tuple[Any, str, frozenset[str]]] = [
    (_search_statutes, "search_statutes", frozenset({"search_statutes"})),
    (_get_provision, "get_provision", frozenset({"get_provision"})),
    (_browse_statute, "browse_statute", frozenset({"browse_statute"})),
    (_compare_provisions, "compare_provisions", frozenset({"compare_provisions"})),
    (_cross_reference, "cross_reference", frozenset({"cross_reference"})),
    (_get_provision_history, "get_provision_history", frozenset({"get_provision_history"})),
    (_search_judgments, "search_judgments", frozenset({"search_judgments"})),
    (_get_judgment, "get_judgment", frozenset({"get_judgment"})),
    (_trace_precedent, "trace_precedent", frozenset({"trace_precedent"})),
    (_judgment_analytics, "judgment_analytics", frozenset({"judgment_analytics"})),
    (_verify_citations, "verify_citations", frozenset({"verify_citations"})),
    (_corpus_status, "corpus_status", frozenset({"corpus_status"})),
]


def _registered_tool_names(mcp: Any) -> set[str] | None:
    """Return the tool names currently registered on ``mcp``, or None if not introspectable."""
    provider = getattr(mcp, "_local_provider", None)
    components = getattr(provider, "_components", None)
    if not isinstance(components, dict):
        return None
    names: set[str] = set()
    for key in components:
        if isinstance(key, str) and key.startswith("tool:"):
            names.add(key[len("tool:") :].split("@", 1)[0])
    return names


def register(mcp: Any) -> None:
    """Register all 12 v2 tools, failing loudly on any registration problem."""
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
            continue
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
