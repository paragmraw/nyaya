"""Native LangChain tools wrapping the Nyaya v2 tool functions directly.

Instead of calling the MCP server over HTTP (a loopback self-call to ``/mcp``
in the same process), the chat agent imports these wrappers which call
``nyaya.tools`` directly in-process. This eliminates HTTP serialization
overhead and removes any startup dependency on the HTTP server being reachable.

Each tool's interface (name, description, args schema) comes from
``tools_layer.spec``, the single source of truth, so the native and MCP
paths expose the SAME interface to the model. Errors from the storage/retrieval
layer (``NyayaError`` subclasses) are caught and returned as JSON error strings
so the model can self-correct.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any, cast

from langchain_core.tools import StructuredTool

from ..config import Settings, get_settings
from .spec import TOOL_SPECS

log = logging.getLogger("nyaya_chat.tools_layer.native")

_SENTINELS = frozenset({"none", "null", "n/a", ""})


def _clean_optional(value: str | None) -> str | None:
    """Map model-emitted sentinels ('none'/'null'/'n/a'/'') to None."""
    if value is None:
        return None
    return None if value.strip().lower() in _SENTINELS else value.strip()


def _effective_cs(commercial_safe_only: bool = False) -> bool:
    try:
        return bool(commercial_safe_only or get_settings().commercial_safe_only)
    except Exception:
        return bool(commercial_safe_only)


def _error_json(exc: Exception) -> str:
    """Convert a NyayaError (or any exception) to a JSON error string."""
    if hasattr(exc, "to_dict") and callable(exc.to_dict):
        err_dict = dict(exc.to_dict())
    else:
        err_dict = {
            "code": getattr(exc, "code", "error"),
            "message": getattr(exc, "message", str(exc)),
        }
    err_dict.setdefault("code", getattr(exc, "code", "error"))
    err_dict.setdefault("message", getattr(exc, "message", str(exc)))
    if getattr(exc, "kind", None) is not None and "kind" not in err_dict:
        err_dict["kind"] = getattr(exc, "kind")
    if getattr(exc, "hint", None) is not None and "hint" not in err_dict:
        err_dict["hint"] = getattr(exc, "hint")
    return json.dumps({"error": err_dict}, default=str)


# ---------------------------------------------------------------------------
# Tool implementations: async wrappers around nyaya.tools.*
# ---------------------------------------------------------------------------


async def _search_statutes(
    query: str,
    act: str | None = None,
    jurisdiction: str | None = None,
    kind: str | None = None,
    status: str = "all",
    query_mode: str = "bm25",
    promote_definitions: bool = False,
    commercial_safe_only: bool = False,
    limit: int = 10,
    cursor: str | None = None,
) -> str:
    from nyaya.tools import search_statutes

    try:
        resp = await asyncio.to_thread(
            search_statutes,
            query=query,
            act=_clean_optional(act),
            jurisdiction=_clean_optional(jurisdiction),
            kind=cast(Any, _clean_optional(kind)),
            status=cast(Any, status if status in ("in_force", "repealed", "all") else "all"),
            query_mode=cast(Any, query_mode if query_mode in ("bm25", "exact_phrase") else "bm25"),
            promote_definitions=bool(promote_definitions),
            commercial_safe_only=_effective_cs(commercial_safe_only),
            limit=limit,
            cursor=_clean_optional(cursor),
        )
        return str(resp.model_dump_json())
    except Exception as exc:
        log.warning("search_statutes failed: %s", exc)
        return _error_json(exc)


async def _get_provision(
    act: str = "",
    ref: str = "",
    citation: str | None = None,
    jurisdiction: str | None = None,
    include_links: bool = True,
    commercial_safe_only: bool = False,
) -> str:
    from nyaya.tools import get_provision

    try:
        resp = await asyncio.to_thread(
            get_provision,
            act=_clean_optional(act) or "",
            ref=_clean_optional(ref) or "",
            citation=_clean_optional(citation),
            jurisdiction=_clean_optional(jurisdiction),
            include_links=bool(include_links),
            commercial_safe_only=_effective_cs(commercial_safe_only),
        )
        return str(resp.model_dump_json())
    except Exception as exc:
        log.warning("get_provision failed: %s", exc)
        return _error_json(exc)


async def _compare_provisions(
    source: str,
    target: str | None = None,
    include_text: bool = True,
    commercial_safe_only: bool = False,
) -> str:
    from nyaya.tools import compare_provisions

    try:
        resp = await asyncio.to_thread(
            compare_provisions,
            source=source,
            target=_clean_optional(target),
            include_text=bool(include_text),
            commercial_safe_only=_effective_cs(commercial_safe_only),
        )
        return str(resp.model_dump_json())
    except Exception as exc:
        log.warning("compare_provisions failed: %s", exc)
        return _error_json(exc)


async def _browse_statute(
    act: str | None = None,
    query: str | None = None,
    jurisdiction: str | None = None,
    category: str | None = None,
    status: str = "all",
    chapter: int | str | None = None,
    part: str | None = None,
    kind: str | None = None,
    ref_from: str | None = None,
    ref_to: str | None = None,
    include_snippet: bool = False,
    commercial_safe_only: bool = False,
    limit: int = 50,
    cursor: str | None = None,
) -> str:
    from nyaya.tools import browse_statute

    try:
        resp = await asyncio.to_thread(
            browse_statute,
            act=_clean_optional(act),
            query=_clean_optional(query),
            jurisdiction=_clean_optional(jurisdiction),
            category=_clean_optional(category),
            status=cast(Any, status if status in ("in_force", "repealed", "all") else "all"),
            chapter=chapter,
            part=_clean_optional(part),
            kind=cast(Any, _clean_optional(kind)),
            ref_from=_clean_optional(ref_from),
            ref_to=_clean_optional(ref_to),
            include_snippet=bool(include_snippet),
            commercial_safe_only=_effective_cs(commercial_safe_only),
            limit=limit,
            cursor=_clean_optional(cursor),
        )
        return str(resp.model_dump_json())
    except Exception as exc:
        log.warning("browse_statute failed: %s", exc)
        return _error_json(exc)


async def _get_provision_history(
    act: str = "Constitution",
    ref: str | None = None,
    amendment_number: int | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    commercial_safe_only: bool = False,
    limit: int = 50,
    cursor: str | None = None,
) -> str:
    from nyaya.tools import get_provision_history

    try:
        resp = await asyncio.to_thread(
            get_provision_history,
            act=_clean_optional(act) or "Constitution",
            ref=_clean_optional(ref),
            amendment_number=amendment_number,
            year_from=year_from,
            year_to=year_to,
            commercial_safe_only=_effective_cs(commercial_safe_only),
            limit=limit,
            cursor=_clean_optional(cursor),
        )
        return str(resp.model_dump_json())
    except Exception as exc:
        log.warning("get_provision_history failed: %s", exc)
        return _error_json(exc)


async def _search_judgments(
    query: str | None = None,
    query_mode: str = "auto",
    court_tier: str | None = None,
    court: str | list[str] | None = None,
    bench: str | None = None,
    binding_for_court: str | None = None,
    min_bench_strength: int | None = None,
    judge: str | None = None,
    author_judge: str | None = None,
    party: str | None = None,
    act: str | None = None,
    section: str | None = None,
    include_equivalent_provisions: bool = True,
    disposal_nature: str | None = None,
    case_type: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    sort_by: str = "authority_relevance",
    commercial_safe_only: bool = False,
    limit: int = 10,
    cursor: str | None = None,
) -> str:
    from nyaya.tools import search_judgments

    try:
        clean_court: str | list[str] | None = (
            [c.strip() for c in court if c and c.strip()]
            if isinstance(court, list)
            else _clean_optional(court)
        )
        resp = await asyncio.to_thread(
            search_judgments,
            query=_clean_optional(query),
            query_mode=cast(Any, query_mode if query_mode in ("auto", "bm25", "metadata") else "auto"),
            court_tier=cast(Any, _clean_optional(court_tier)),
            court=clean_court,
            bench=_clean_optional(bench),
            binding_for_court=_clean_optional(binding_for_court),
            min_bench_strength=min_bench_strength,
            judge=_clean_optional(judge),
            author_judge=_clean_optional(author_judge),
            party=_clean_optional(party),
            act=_clean_optional(act),
            section=_clean_optional(section),
            include_equivalent_provisions=bool(include_equivalent_provisions),
            disposal_nature=_clean_optional(disposal_nature),
            case_type=_clean_optional(case_type),
            date_from=_clean_optional(date_from),
            date_to=_clean_optional(date_to),
            sort_by=cast(Any, sort_by),
            commercial_safe_only=_effective_cs(commercial_safe_only),
            limit=limit,
            cursor=_clean_optional(cursor),
        )
        return str(resp.model_dump_json())
    except Exception as exc:
        log.warning("search_judgments failed: %s", exc)
        return _error_json(exc)


async def _get_judgment(
    identifier: str,
    view: str = "overview",
    para_start: int | None = None,
    para_end: int | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
    within_query: str | None = None,
    commercial_safe_only: bool = False,
) -> str:
    from nyaya.tools import get_judgment

    try:
        resp = await get_judgment(
            identifier=identifier,
            view=cast(Any, view if view in ("overview", "paragraphs", "pages", "search_within", "docket") else "overview"),
            para_start=para_start,
            para_end=para_end,
            page_start=page_start,
            page_end=page_end,
            within_query=_clean_optional(within_query),
            commercial_safe_only=_effective_cs(commercial_safe_only),
        )
        return str(resp.model_dump_json())
    except Exception as exc:
        log.warning("get_judgment failed: %s", exc)
        return _error_json(exc)


async def _trace_precedent(
    identifier: str,
    direction: str = "cited_by",
    court: str | None = None,
    min_bench_strength: int | None = None,
    larger_bench_only: bool = False,
    date_from: str | None = None,
    commercial_safe_only: bool = False,
    limit: int = 15,
    cursor: str | None = None,
) -> str:
    from nyaya.tools import trace_precedent

    try:
        resp = await asyncio.to_thread(
            trace_precedent,
            identifier=identifier,
            direction=cast(Any, direction if direction in ("cited_by", "cites", "both") else "cited_by"),
            court=_clean_optional(court),
            min_bench_strength=min_bench_strength,
            larger_bench_only=bool(larger_bench_only),
            date_from=_clean_optional(date_from),
            commercial_safe_only=_effective_cs(commercial_safe_only),
            limit=limit,
            cursor=_clean_optional(cursor),
        )
        return str(resp.model_dump_json())
    except Exception as exc:
        log.warning("trace_precedent failed: %s", exc)
        return _error_json(exc)


async def _cross_reference(
    act: str,
    ref: str,
    direction: str = "both",
    relation_kind: str | None = None,
    commercial_safe_only: bool = False,
    limit: int = 25,
    cursor: str | None = None,
) -> str:
    from nyaya.tools import cross_reference

    try:
        resp = await asyncio.to_thread(
            cross_reference,
            act=act,
            ref=ref,
            direction=cast(Any, direction if direction in ("from", "to", "both") else "both"),
            relation_kind=cast(Any, _clean_optional(relation_kind)),
            commercial_safe_only=_effective_cs(commercial_safe_only),
            limit=limit,
            cursor=_clean_optional(cursor),
        )
        return str(resp.model_dump_json())
    except Exception as exc:
        log.warning("cross_reference failed: %s", exc)
        return _error_json(exc)


async def _verify_citations(
    text: str | None = None,
    citations: list[str] | None = None,
    check_date: str | None = None,
    commercial_safe_only: bool = False,
) -> str:
    from nyaya.tools import verify_citations

    try:
        resp = await verify_citations(
            text=_clean_optional(text),
            citations=citations,
            check_date=_clean_optional(check_date),
            commercial_safe_only=_effective_cs(commercial_safe_only),
        )
        return str(resp.model_dump_json())
    except Exception as exc:
        log.warning("verify_citations failed: %s", exc)
        return _error_json(exc)


async def _judgment_analytics(
    group_by: str,
    court_tier: str = "high_court",
    court: str | list[str] | None = None,
    bench: str | None = None,
    state: str | None = None,
    district: str | None = None,
    judge: str | None = None,
    act: str | None = None,
    section: str | None = None,
    disposal_nature: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    top_k: int = 25,
    sample_cases: int = 0,
    commercial_safe_only: bool = False,
) -> str:
    from nyaya.tools import judgment_analytics

    try:
        resp = await asyncio.to_thread(
            judgment_analytics,
            group_by=cast(Any, group_by),
            court_tier=cast(Any, court_tier),
            court=court,
            bench=_clean_optional(bench),
            state=_clean_optional(state),
            district=_clean_optional(district),
            judge=_clean_optional(judge),
            act=_clean_optional(act),
            section=_clean_optional(section),
            disposal_nature=_clean_optional(disposal_nature),
            date_from=_clean_optional(date_from),
            date_to=_clean_optional(date_to),
            top_k=top_k,
            sample_cases=sample_cases,
            commercial_safe_only=_effective_cs(commercial_safe_only),
        )
        return str(resp.model_dump_json())
    except Exception as exc:
        log.warning("judgment_analytics failed: %s", exc)
        return _error_json(exc)


async def _corpus_status(
    court: str | None = None,
    include_sources: bool = True,
    commercial_safe_only: bool = False,
) -> str:
    from nyaya.tools import corpus_status

    try:
        resp = await asyncio.to_thread(
            corpus_status,
            court=_clean_optional(court),
            include_sources=bool(include_sources),
            commercial_safe_only=_effective_cs(commercial_safe_only),
        )
        return str(resp.model_dump_json())
    except Exception as exc:
        log.warning("corpus_status failed: %s", exc)
        return _error_json(exc)


_IMPLS: dict[str, Callable[..., Awaitable[Any]]] = {
    "search_statutes": _search_statutes,
    "get_provision": _get_provision,
    "compare_provisions": _compare_provisions,
    "browse_statute": _browse_statute,
    "get_provision_history": _get_provision_history,
    "search_judgments": _search_judgments,
    "get_judgment": _get_judgment,
    "trace_precedent": _trace_precedent,
    "cross_reference": _cross_reference,
    "verify_citations": _verify_citations,
    "judgment_analytics": _judgment_analytics,
    "corpus_status": _corpus_status,
}


async def load_native_tools(settings: Settings | None = None) -> list[StructuredTool]:
    """Build the allowlisted native tools from the spec table.

    Returns an empty list if the nyaya package is not importable (the caller
    should fall back to MCP tools or degrade gracefully).
    """
    import importlib.util

    if importlib.util.find_spec("nyaya") is None:
        return []

    s = settings or get_settings()
    allow = set(s.tool_allowlist)
    tools: list[StructuredTool] = []
    for spec in TOOL_SPECS:
        if spec.name not in allow or spec.name not in _IMPLS:
            continue
        tools.append(
            StructuredTool.from_function(
                coroutine=_IMPLS[spec.name],
                name=spec.name,
                description=spec.description,
                args_schema=spec.args_model,
            )
        )
    missing = sorted(allow - {t.name for t in tools})
    if missing:
        log.warning("native tools: allowlist references tools not built: %s", missing)
    log.info("loaded %d native tools: %s", len(tools), [t.name for t in tools])
    return tools
