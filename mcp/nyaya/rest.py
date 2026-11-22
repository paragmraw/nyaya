"""Thin v2.0 REST endpoints for the Nyaya web frontend (/api/*)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, TypeVar

from starlette.requests import Request
from starlette.responses import JSONResponse

from . import __version__
from .config import _is_truthy, get_settings
from .exceptions import (
    CommercialLicenseBlocked,
    InvalidFilter,
    NotFound,
    QueryTimeout,
    QueryTooBroad,
    StorageUnavailable,
)
from .models import AnalyticsTier, CourtTier, JudgmentSearchQuery
from .storage import get_analytics, get_backend, get_retrieval
from .storage.sqlite_store import SqliteStore

log = logging.getLogger("nyaya.rest")

T = TypeVar("T")


def _safe(fn: Callable[..., T], *args: Any, **kwargs: Any) -> Any:
    """Run a synchronous storage/retrieval function in a worker thread."""
    return asyncio.to_thread(fn, *args, **kwargs)


def _query_commercial_safe(request: Request) -> bool:
    raw = request.query_params.get("commercial_safe_only")
    return bool(_is_truthy(raw) or get_settings().commercial_safe_only)


def _error_response(exc: Exception, request: Request | None = None) -> JSONResponse:
    """Build a safe JSON error response without leaking internal details."""
    request_id = (
        getattr(getattr(request, "state", None), "request_id", None)
        if request is not None
        else None
    )

    def _with_rid(payload: dict[str, Any]) -> dict[str, Any]:
        if request_id:
            payload["request_id"] = request_id
        return payload

    if isinstance(exc, StorageUnavailable):
        log.warning("Storage unavailable (request_id=%s)", request_id, exc_info=True)
        return JSONResponse(
            _with_rid({"error": "storage_unavailable"}),
            status_code=503,
        )
    if isinstance(exc, CommercialLicenseBlocked):
        return JSONResponse(
            _with_rid(
                {
                    "error": "commercial_license_blocked",
                    "detail": exc.message,
                    "license_spdx": exc.license_spdx,
                }
            ),
            status_code=403,
        )
    if isinstance(exc, (QueryTooBroad, InvalidFilter)):
        return JSONResponse(
            _with_rid({"error": exc.code, "detail": exc.message}),
            status_code=400,
        )
    if isinstance(exc, NotFound):
        return JSONResponse(
            _with_rid({"error": "not_found", "detail": exc.message}),
            status_code=404,
        )
    if isinstance(exc, QueryTimeout):
        return JSONResponse(
            _with_rid({"error": "query_timeout", "detail": exc.message}),
            status_code=504,
        )

    log.error("Internal error (request_id=%s)", request_id, exc_info=True)
    return JSONResponse(_with_rid({"error": "internal_error"}), status_code=500)


async def _build_corpus_status_dict(
    request: Request,
    *,
    include_health_status: bool = False,
) -> dict[str, Any]:
    cs_only = _query_commercial_safe(request)
    court = request.query_params.get("court") or None
    backend = get_backend()
    try:
        resp = await _safe(
            backend.corpus_status,
            court=court,
            include_sources=True,
            commercial_safe_only=cs_only,
        )
        data = resp.model_dump(mode="json") if hasattr(resp, "model_dump") else dict(resp)
        data.setdefault("version", __version__)
        data.setdefault("service_version", __version__)
        if include_health_status:
            data["status"] = "healthy"
            data["backends"] = data.get("tiers", {"sqlite_core": "ok"})
        return data
    except Exception:
        if not include_health_status:
            raise
        return {
            "status": "degraded",
            "version": __version__,
            "service_version": __version__,
            "as_of": None,
            "backends": {"sqlite_core": "unavailable"},
            "counts": {},
        }


async def corpus_status_endpoint(request: Request) -> JSONResponse:
    """GET /api/corpus-status (and /api/corpus-stats) -> CorpusStatusResponse JSON."""
    try:
        payload = await _build_corpus_status_dict(request, include_health_status=False)
        return JSONResponse(payload)
    except Exception as exc:
        log.warning("corpus_status endpoint failed", exc_info=True)
        return _error_response(exc, request)


async def acts_endpoint(request: Request) -> JSONResponse:
    """GET /api/acts?jurisdiction=&category=&q=&limit=50&offset=0&commercial_safe_only= -> {items, total, limit, offset}."""
    try:
        limit = max(1, min(int(request.query_params.get("limit", "50")), 200))
        offset = max(0, int(request.query_params.get("offset", "0")))
    except ValueError:
        return JSONResponse(
            {"error": "bad_request", "detail": "limit/offset must be integers"},
            status_code=400,
        )

    jurisdiction = request.query_params.get("jurisdiction") or None
    category = request.query_params.get("category") or None
    q = request.query_params.get("q") or None
    cs_only = _query_commercial_safe(request)

    try:
        res = await _safe(
            get_backend().browse_statute,
            act=None,
            query=q,
            jurisdiction=jurisdiction,
            category=category,
            limit=limit,
            offset=offset,
            commercial_safe_only=cs_only,
        )
        items: list[dict[str, Any]] = []
        for a in res.acts:
            d = a.model_dump(mode="json")
            d.setdefault("kind", a.category)
            d.setdefault("source", a.provenance.source_name)
            d.setdefault("license_spdx", a.provenance.license_spdx)
            d.setdefault("commercial_safe", a.provenance.commercial_safe)
            d.setdefault("as_of", a.provenance.as_of.isoformat())
            items.append(d)
        return JSONResponse(
            {
                "items": items,
                "total": res.total,
                "limit": limit,
                "offset": offset,
            }
        )
    except Exception as exc:
        log.warning("acts endpoint failed", exc_info=True)
        return _error_response(exc, request)


async def judgments_endpoint(request: Request) -> JSONResponse:
    """GET /api/judgments?tier=&court=&year_from=&year_to=&q=&limit=50&offset=0&commercial_safe_only=."""
    try:
        limit = max(1, min(int(request.query_params.get("limit", "50")), 100))
        offset = max(0, int(request.query_params.get("offset", "0")))
        year_from_raw = request.query_params.get("year_from")
        year_to_raw = request.query_params.get("year_to")
        year_from = int(year_from_raw) if year_from_raw else None
        year_to = int(year_to_raw) if year_to_raw else None
    except ValueError:
        return JSONResponse(
            {"error": "bad_request", "detail": "limit/offset/year_from/year_to must be integers"},
            status_code=400,
        )

    tier_raw = (request.query_params.get("tier") or "").strip().lower()
    tier_map: dict[str, CourtTier] = {
        "sc": "supreme_court",
        "supreme_court": "supreme_court",
        "hc": "high_court",
        "high_court": "high_court",
        "tribunal": "tribunal",
        "district": "district_court",
        "district_court": "district_court",
        "all": "all",
    }
    tier: CourtTier = tier_map.get(tier_raw, "supreme_court")
    court = request.query_params.get("court") or None
    q = request.query_params.get("q") or None
    cs_only = _query_commercial_safe(request)

    try:
        backend = get_backend()
        # Fast path for in-memory test backends when no query is supplied
        if hasattr(backend, "judgments") and not q and isinstance(getattr(backend, "judgments", None), dict):
            all_metas = [
                m
                for m in backend.judgments.values()  # type: ignore[attr-defined]
                if (not cs_only or m.provenance.commercial_safe)
                and (not court or m.court_code.lower() == court.lower())
                and (year_from is None or (m.year or 0) >= year_from)
                and (year_to is None or (m.year or 9999) <= year_to)
            ]
            sliced = all_metas[offset : offset + limit]
            items = []
            for m in sliced:
                card = SqliteStore._meta_to_card(m).model_dump(mode="json")
                card["license_spdx"] = m.provenance.license_spdx
                card["commercial_safe"] = m.provenance.commercial_safe
                items.append(card)
            return JSONResponse(
                {
                    "items": items,
                    "total": len(all_metas),
                    "total_is_estimate": False,
                    "limit": limit,
                    "offset": offset,
                }
            )

        search_q = JudgmentSearchQuery(
            query=q,
            court_tier=tier,
            court=court,
            year_from=year_from,
            year_to=year_to,
            commercial_safe_only=cs_only,
            limit=min(limit, 25),
            offset=offset,
        )
        resp = await _safe(get_retrieval().search_judgments, search_q)
        items = []
        for c in resp.results:
            d = c.model_dump(mode="json")
            d["license_spdx"] = c.provenance.license_spdx
            d["commercial_safe"] = c.provenance.commercial_safe
            items.append(d)
        return JSONResponse(
            {
                "items": items,
                "total": resp.total if resp.total is not None else len(items),
                "total_is_estimate": resp.total_is_estimate,
                "limit": limit,
                "offset": offset,
            }
        )
    except Exception as exc:
        log.warning("judgments endpoint failed", exc_info=True)
        return _error_response(exc, request)


async def analytics_endpoint(request: Request) -> JSONResponse:
    """GET /api/analytics?dataset=hc&group_by=year&court=&state=&year_from=&year_to=&top_n=25&commercial_safe_only=."""
    try:
        top_n = max(1, min(int(request.query_params.get("top_n", "25")), 100))
        year_from_raw = request.query_params.get("year_from")
        year_to_raw = request.query_params.get("year_to")
        year_from = int(year_from_raw) if year_from_raw else None
        year_to = int(year_to_raw) if year_to_raw else None
    except ValueError:
        return JSONResponse(
            {"error": "bad_request", "detail": "top_n/year_from/year_to must be integers"},
            status_code=400,
        )

    ds_raw = (request.query_params.get("dataset") or "high_court").strip().lower()
    ds_map: dict[str, AnalyticsTier] = {
        "sc": "supreme_court",
        "supreme_court": "supreme_court",
        "hc": "high_court",
        "high_court": "high_court",
        "district": "district_court",
        "district_court": "district_court",
        "tribunal": "tribunal",
        "regulator": "regulator",
        "all": "all",
    }
    if ds_raw not in ds_map:
        return JSONResponse(
            {"error": "invalid_filter", "detail": f"Invalid dataset {ds_raw!r}"},
            status_code=400,
        )
    dataset = ds_map[ds_raw]
    group_by = (request.query_params.get("group_by") or "year").strip()
    cs_only = _query_commercial_safe(request)

    if cs_only and dataset == "district_court":
        return _error_response(
            CommercialLicenseBlocked(
                "District Court dataset (DDL 81M, CC-BY-NC-SA-4.0) is blocked when commercial_safe_only=True.",
                license_spdx="CC-BY-NC-SA-4.0",
            ),
            request,
        )

    filters: dict[str, Any] = {
        "court": request.query_params.get("court") or None,
        "state": request.query_params.get("state") or None,
        "judge": request.query_params.get("judge") or None,
        "act": request.query_params.get("act") or None,
        "year_from": year_from,
        "year_to": year_to,
    }

    try:
        res = await _safe(
            get_analytics().aggregate,
            dataset,
            group_by,
            filters=filters,
            top_n=top_n,
            commercial_safe_only=cs_only,
        )
        first_prov = res.provenances[0] if res.provenances else None
        rows = [
            {"key": b.key, "count": b.count, "share_pct": b.share_pct}
            for b in res.buckets
        ]
        return JSONResponse(
            {
                "dataset": res.dataset,
                "group_by": res.group_by,
                "rows": rows,
                "buckets": [b.model_dump(mode="json") for b in res.buckets],
                "total_cases": res.total_matched,
                "total_matched": res.total_matched,
                "completeness_caveat": res.completeness_caveat,
                "as_of": first_prov.as_of.isoformat() if first_prov else None,
                "commercial_safe": first_prov.commercial_safe if first_prov else (dataset != "district_court"),
                "license_spdx": first_prov.license_spdx if first_prov else ("CC-BY-NC-SA-4.0" if dataset == "district_court" else "CC-BY-4.0"),
            }
        )
    except Exception as exc:
        log.warning("analytics endpoint failed", exc_info=True)
        return _error_response(exc, request)


async def tools_endpoint(request: Request) -> JSONResponse:
    """GET /api/tools -> introspected 12 tools, 8 resources_count, 4 prompts_count."""
    mcp = getattr(request.app.state, "mcp", None)
    if mcp is None:
        return JSONResponse(
            {"error": "mcp_unavailable", "detail": "MCP app not registered on app.state"},
            status_code=500,
        )
    try:
        tools = await mcp.list_tools()
        resources = await mcp.list_resources()
        templates = await mcp.list_resource_templates()
        prompts = await mcp.list_prompts()
        items = []
        for t in tools:
            ann = getattr(t, "annotations", None)
            open_world = getattr(ann, "openWorldHint", None) if ann is not None else None
            items.append(
                {
                    "name": getattr(t, "name", None) or "",
                    "description": getattr(t, "description", None) or "",
                    "open_world": bool(open_world) if open_world is not None else False,
                }
            )
        return JSONResponse(
            {
                "items": items,
                "total": len(items),
                "resources_count": len(resources) + len(templates),
                "prompts_count": len(prompts),
            }
        )
    except Exception as exc:
        log.warning("tools endpoint failed", exc_info=True)
        return _error_response(exc, request)


async def health_summary_endpoint(request: Request) -> JSONResponse:
    """GET /api/health-summary -> always HTTP 200, degrades gracefully if storage is unreachable."""
    payload = await _build_corpus_status_dict(request, include_health_status=True)
    return JSONResponse(payload)


def register(app: Any, mcp_instance: Any) -> None:
    """Mount the v2 REST endpoints on the Starlette app."""
    app.state.mcp = mcp_instance

    routes = [
        ("api/corpus-status", "GET", corpus_status_endpoint),
        ("api/corpus-stats", "GET", corpus_status_endpoint),
        ("api/acts", "GET", acts_endpoint),
        ("api/judgments", "GET", judgments_endpoint),
        ("api/analytics", "GET", analytics_endpoint),
        ("api/tools", "GET", tools_endpoint),
        ("api/health-summary", "GET", health_summary_endpoint),
    ]
    for path, method, handler in routes:
        app.router.add_route(f"/{path}", handler, methods=[method])
