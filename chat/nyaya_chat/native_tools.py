"""Native LangChain tools wrapping the nyaya MCP db layer directly.

Instead of calling the MCP server over HTTP (a loopback self-call to
``/mcp`` in the same process), the chat agent imports these wrappers which
call ``nyaya.db`` functions directly. This eliminates HTTP serialization
overhead (~5-15ms per tool call) and removes the startup dependency on the
MCP server being reachable over HTTP.

The MCP server remains a separate consumption surface for external clients
(Claude Desktop, Cursor, opencode); these native tools are the chat-internal
fast path.

Each tool mirrors the schema of the corresponding MCP tool so the LLM sees
the same interface. Errors from the db layer (``NyayaError`` subclasses) are
caught and returned as JSON error strings so the model can self-correct,
matching the ``handle_tool_errors=True`` behavior of the MCP adapter.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from .config import Settings, get_settings

log = logging.getLogger("nyaya_chat.native_tools")


# ---------------------------------------------------------------------------
# Error handling — convert NyayaError to a JSON string for the model.
# ---------------------------------------------------------------------------

def _error_json(exc: Exception) -> str:
    """Convert a NyayaError (or any exception) to a JSON error string."""
    import json
    code = getattr(exc, "code", "error")
    msg = getattr(exc, "message", str(exc))
    kind = getattr(exc, "kind", None)
    hint = getattr(exc, "hint", None)
    return json.dumps({"error": {"code": code, "message": msg, "kind": kind, "hint": hint}})


# ---------------------------------------------------------------------------
# Input schemas for each tool (Pydantic models for LangChain StructuredTool).
# ---------------------------------------------------------------------------

class SemanticQueryInput(BaseModel):
    query: str = Field(description="Free-text or natural-language query.")
    kind: str | None = Field(default=None, description="Filter: 'section', 'article', 'judgment', 'schedule', or 'amendment'.")
    act: str | None = Field(default=None, description="Act short-name to scope (e.g. 'IPC', 'BNS').")
    limit: int = Field(default=10, description="Max hits (1-50).")
    offset: int = Field(default=0, description="Pagination offset.")
    promote_definitions: bool = Field(default=False, description="Boost results whose title contains 'definition' or 'interpretation'.")


class GetSectionInput(BaseModel):
    act: str = Field(default="", description="Act short name or alias, e.g. 'IPC', 'BNS'. If empty, section is parsed as a citation string.")
    section: str = Field(default="", description="Section number, e.g. '302', '354A'. A leading 's.' prefix is stripped.")


class GetArticleInput(BaseModel):
    article: str = Field(description="Article number (e.g. '21', '21A') or citation string like 'Art.21'.")


class GetJudgmentInput(BaseModel):
    case_slug: str = Field(description="Citation (e.g. 'AIR 1973 SC 1461'), case name, or slugified name.")


class CrossReferenceInput(BaseModel):
    act: str = Field(description="Act short name or alias, e.g. 'IPC', 'Constitution'.")
    section: str = Field(description="Section or article number, e.g. '302', '21'.")
    direction: str = Field(default="both", description="'both' (default), 'from' (outgoing only), or 'to' (incoming only).")


class ListActsInput(BaseModel):
    pass


# Citation parsing regex (mirrors mcp/nyaya/tools/get_section.py)
_CITATION_RE = re.compile(
    r"(?:s(?:ec(?:tion)?)?\.?\s*(?P<num>\d+[A-Z]?)\s*(?:of\s+)?(?P<act>[A-Za-z]+)?)"
    r"|(?P<act2>[A-Za-z]+)\s+s(?:ec(?:tion)?)?\.?\s*(?P<num2>\d+[A-Z]?)",
    re.IGNORECASE,
)

_ART_CITATION_RE = re.compile(r"art(?:icle)?\.?\s*(?P<num>\d+[A-Z]?)", re.IGNORECASE)


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
        results, total, fallback_reason = await asyncio.to_thread(
            db.rerank_search, query, kind=kind, act=act, limit=limit, offset=offset,
            promote_definitions=promote_definitions,
        )
        import json
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
        refs = await asyncio.to_thread(db.get_cross_refs, act, section, direction)
        import json
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
        import json
        return json.dumps({"acts": [a.model_dump() for a in acts]}, default=str)
    except Exception as exc:
        log.warning("list_acts failed: %s", exc)
        return _error_json(exc)


# ---------------------------------------------------------------------------
# Tool descriptions (kept in sync with the MCP tool descriptions).
# ---------------------------------------------------------------------------

_SEMANTIC_QUERY_DESC = (
    "Semantic search over the Indian law corpus using embedding retrieval + "
    "cross-encoder reranking. Returns the most relevant sections, articles, "
    "and judgments for a natural-language query. Better than keyword search "
    "for paraphrased queries and cross-act comparisons (e.g. 'punishment "
    "for murder' finds both IPC s.302 and BNS s.103). "
    "Optional 'kind' filters to 'section', 'article', or 'judgment'. "
    "Optional 'act' scopes to one act short-name (e.g. 'IPC', 'BNS'). "
    "Set promote_definitions=true to boost sections whose title contains "
    "'definition' or 'interpretation'."
)

_GET_SECTION_DESC = (
    "Fetch the full text of a specific section of an Indian act by its number. "
    "Supports IPC, CrPC, CPC, Evidence Act, BNS, BNSS, BSA, etc. Act names are "
    "normalized (case-insensitive). Also accepts combined citation strings like "
    "'IPC s.302' or 's.302 of IPC'. Use get_article for Constitution articles."
)

_GET_ARTICLE_DESC = (
    "Fetch the full text of a Constitution of India article by its number. "
    "Handles bare numbers ('21') and citation strings like 'Art.21' or 'Article 21'."
)

_GET_JUDGMENT_DESC = (
    "Fetch the full text of a landmark Supreme Court judgment by citation or "
    "case-name slug. Matches exact citation ('AIR 1973 SC 1461'), slugified "
    "case name, or fuzzy case-name substring (>= 8 chars)."
)

_CROSS_REF_DESC = (
    "Given a section or article, return other provisions it references AND that "
    "reference it (bidirectional). Covers IPC-BNS correspondence, cross-act "
    "references, and repealed-by relationships."
)

_LIST_ACTS_DESC = (
    "List all acts available in the nyaya corpus with provenance. Use this first "
    "to discover what's searchable."
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def load_native_tools(settings: Settings | None = None) -> list[Any]:
    """Build and return the curated set of native LangChain tools.

    These call nyaya.db functions directly (via asyncio.to_thread) instead of
    going through the MCP HTTP transport. Returns an empty list if the nyaya
    package is not importable (the caller should fall back to MCP tools or
    degrade gracefully).
    """
    s = settings or get_settings()
    allow = set(s.tool_allowlist)

    tools_map: dict[str, Any] = {}

    if "semantic_query" in allow:
        tools_map["semantic_query"] = StructuredTool.from_function(
            coroutine=_semantic_query,
            name="semantic_query",
            description=_SEMANTIC_QUERY_DESC,
            args_schema=SemanticQueryInput,
        )

    if "get_section" in allow:
        tools_map["get_section"] = StructuredTool.from_function(
            coroutine=_get_section,
            name="get_section",
            description=_GET_SECTION_DESC,
            args_schema=GetSectionInput,
        )

    if "get_article" in allow:
        tools_map["get_article"] = StructuredTool.from_function(
            coroutine=_get_article,
            name="get_article",
            description=_GET_ARTICLE_DESC,
            args_schema=GetArticleInput,
        )

    if "get_judgment" in allow:
        tools_map["get_judgment"] = StructuredTool.from_function(
            coroutine=_get_judgment,
            name="get_judgment",
            description=_GET_JUDGMENT_DESC,
            args_schema=GetJudgmentInput,
        )

    if "cross_reference" in allow:
        tools_map["cross_reference"] = StructuredTool.from_function(
            coroutine=_cross_reference,
            name="cross_reference",
            description=_CROSS_REF_DESC,
            args_schema=CrossReferenceInput,
        )

    if "list_acts" in allow:
        tools_map["list_acts"] = StructuredTool.from_function(
            coroutine=_list_acts,
            name="list_acts",
            description=_LIST_ACTS_DESC,
            args_schema=ListActsInput,
        )

    tools = [t for t in tools_map.values()]
    missing = sorted(allow - set(tools_map.keys()))
    if missing:
        log.warning("native tools: allowlist references tools not built: %s", missing)

    log.info("loaded %d native tools: %s", len(tools), list(tools_map.keys()))
    return tools
