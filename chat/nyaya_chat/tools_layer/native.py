"""Native LangChain tools wrapping the nyaya db layer directly.

Instead of calling the MCP server over HTTP (a loopback self-call to
``/mcp`` in the same process), the chat agent imports these wrappers which
call ``nyaya.db`` functions directly. This eliminates HTTP serialization
overhead (~5-15ms per tool call) and removes the startup dependency on the
MCP server being reachable over HTTP.

The MCP server remains a separate consumption surface for external clients
(Claude Desktop, Cursor, opencode); these native tools are the chat-internal
fast path.

Each tool's interface (name, description, args schema) comes from
``tools_layer.spec`` — the single source of truth — so the native and MCP
paths expose the SAME interface to the model. Errors from the db layer
(``NyayaError`` subclasses) are caught and returned as JSON error strings so
the model can self-correct, matching the ``handle_tool_errors=True``
behaviour of the MCP adapter.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, Literal, cast

from langchain_core.tools import StructuredTool

from ..config import Settings, get_settings
from .spec import TOOL_SPECS

log = logging.getLogger("nyaya_chat.tools_layer.native")


def _error_json(exc: Exception) -> str:
    """Convert a NyayaError (or any exception) to a JSON error string."""
    code = getattr(exc, "code", "error")
    msg = getattr(exc, "message", str(exc))
    kind = getattr(exc, "kind", None)
    hint = getattr(exc, "hint", None)
    return json.dumps({"error": {"code": code, "message": msg, "kind": kind, "hint": hint}})


# Citation parsing regexes (mirror mcp/nyaya/tools/get_section.py).
_CITATION_RE = re.compile(
    r"(?:s(?:ec(?:tion)?)?\.?\s*(?P<num>\d+[A-Z]?)\s*(?:of\s+)?(?P<act>[A-Za-z]+)?)"
    r"|(?P<act2>[A-Za-z]+)\s+s(?:ec(?:tion)?)?\.?\s*(?P<num2>\d+[A-Z]?)",
    re.IGNORECASE,
)
_ART_CITATION_RE = re.compile(r"art(?:icle)?\.?\s*(?P<num>\d+[A-Z]?)", re.IGNORECASE)

# Sentinel values the model sometimes emits instead of omitting an
# optional argument.
_SENTINELS = frozenset({"none", "null", "n/a", ""})


def _clean_optional(value: str | None) -> str | None:
    """Map model-emitted sentinels ('none'/'null'/'n/a'/'') to None."""
    if value is None:
        return None
    return None if value.strip().lower() in _SENTINELS else value


# ---------------------------------------------------------------------------
# Tool implementations — async wrappers around nyaya.db sync functions.
# ---------------------------------------------------------------------------

async def _semantic_query(query: str, kind: str | None = None, act: str | None = None,
                          limit: int = 10, offset: int = 0, promote_definitions: bool = False) -> str:
    from nyaya import db
    from nyaya.exceptions import SearchError
    try:
        limit = max(1, min(int(limit), 50))
        offset = max(0, int(offset))
        if not query or not query.strip():
            return '{"query":"","total":0,"results":[],"returned":0}'
        if len(query) > 4096:
            raise SearchError(f"Query too long ({len(query)} chars); maximum is 4096.")
        kind = _clean_optional(kind)
        act = _clean_optional(act)
        results, total, fallback_reason = await asyncio.to_thread(
            db.rerank_search, query, kind=kind, act=act, limit=limit, offset=offset,
            promote_definitions=promote_definitions,
        )
        as_of = await asyncio.to_thread(db.corpus_as_of)
        return json.dumps({
            "query": query, "total": total, "returned": len(results), "offset": offset,
            "results": [r.model_dump() for r in results],
            "source": "nyaya", "as_of": str(as_of) if as_of else None,
            "limit": limit, "fallback_reason": fallback_reason,
        }, default=str)
    except Exception as exc:
        log.warning("semantic_query failed: %s", exc)
        return _error_json(exc)


async def _get_section(act: str = "", section: str = "") -> str:
    from nyaya import db
    from nyaya.exceptions import NotFound
    try:
        if not act or not act.strip():
            m = _CITATION_RE.search(section or "")
            if m:
                parsed_act = m.group("act") or m.group("act2")
                parsed_num = m.group("num") or m.group("num2")
                if parsed_act and parsed_num:
                    result = await asyncio.to_thread(db.get_section, parsed_act, parsed_num)
                    if result is not None:
                        return result.model_dump_json()
                    raise NotFound(f"Section {parsed_num} of {parsed_act} not in corpus.", kind="section")
        result = await asyncio.to_thread(db.get_section, act, section)
        if result is None:
            raise NotFound(f"Section {section} of {act} not in corpus.", kind="section")
        return result.model_dump_json()
    except Exception as exc:
        log.warning("get_section failed: %s", exc)
        return _error_json(exc)


async def _get_article(article: str) -> str:
    from nyaya import db
    from nyaya.exceptions import NotFound
    try:
        m = _ART_CITATION_RE.match(article or "")
        if m:
            article = m.group("num")
        result = await asyncio.to_thread(db.get_article, article)
        if result is None:
            raise NotFound(f"Article {article} not in corpus.", kind="article")
        return result.model_dump_json()
    except Exception as exc:
        log.warning("get_article failed: %s", exc)
        return _error_json(exc)


async def _get_judgment(case_slug: str) -> str:
    from nyaya import db
    from nyaya.exceptions import NotFound
    try:
        result = await asyncio.to_thread(db.get_judgment, case_slug)
        if result is None:
            raise NotFound(f"Judgment {case_slug!r} not in corpus.", kind="judgment")
        return result.model_dump_json()
    except Exception as exc:
        log.warning("get_judgment failed: %s", exc)
        return _error_json(exc)


async def _cross_reference(act: str, section: str, direction: str = "both") -> str:
    from nyaya import db
    from nyaya.exceptions import SearchError
    try:
        if direction not in ("both", "from", "to"):
            raise SearchError(f"direction must be 'both', 'from', or 'to', got {direction!r}.")
        refs = await asyncio.to_thread(
            db.get_cross_refs, act, section,
            cast(Literal["both", "from", "to"], direction),
        )
        return json.dumps({
            "from_act": act, "from_section": section,
            "references": [r.model_dump() for r in refs], "direction": direction,
        }, default=str)
    except Exception as exc:
        log.warning("cross_reference failed: %s", exc)
        return _error_json(exc)


async def _list_acts() -> str:
    from nyaya import db
    try:
        acts = await asyncio.to_thread(db.list_acts)
        return json.dumps({"acts": [a.model_dump() for a in acts]}, default=str)
    except Exception as exc:
        log.warning("list_acts failed: %s", exc)
        return _error_json(exc)


# name -> coroutine impl. Spec order defines the exposed tool order.
# Explicit value type: the impls have differing signatures, so mypy would
# otherwise collapse the dict value type to ``function`` and reject it as the
# StructuredTool ``coroutine`` argument.
_IMPLS: dict[str, Callable[..., Awaitable[Any]]] = {
    "semantic_query": _semantic_query,
    "get_section": _get_section,
    "get_article": _get_article,
    "get_judgment": _get_judgment,
    "cross_reference": _cross_reference,
    "list_acts": _list_acts,
}


async def load_native_tools(settings: Settings | None = None) -> list[StructuredTool]:
    """Build the allowlisted native tools from the spec table.

    Returns an empty list if the nyaya package is not importable (the caller
    should fall back to MCP tools or degrade gracefully).
    """
    s = settings or get_settings()
    allow = set(s.tool_allowlist)
    tools: list[StructuredTool] = []
    for spec in TOOL_SPECS:
        if spec.name not in allow or spec.name not in _IMPLS:
            continue
        tools.append(StructuredTool.from_function(
            coroutine=_IMPLS[spec.name],
            name=spec.name,
            description=spec.description,
            args_schema=spec.args_model,
        ))
    missing = sorted(allow - {t.name for t in tools})
    if missing:
        log.warning("native tools: allowlist references tools not built: %s", missing)
    log.info("loaded %d native tools: %s", len(tools), [t.name for t in tools])
    return tools
