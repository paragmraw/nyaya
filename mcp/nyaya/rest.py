"""Thin REST endpoints for the Nyaya web frontend.

These are intentionally separate from the MCP tool surface: the SPA does
client-side ``fetch()`` against plain JSON GETs, never the MCP JSON-RPC
transport. Each endpoint calls the same synchronous ``db.*`` layer the MCP
tools use, wrapped in ``asyncio.to_thread`` so the event loop stays
responsive during Postgres round-trips.

All endpoints are read-only and inherit the permissive CORS middleware added
in ``server.py``. Error responses never leak internal exception details:
the full exception is logged server-side with a ``request_id``; the client
receives only ``{"error": "...", "request_id": "..."}``.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any, TypeVar

from starlette.requests import Request
from starlette.responses import JSONResponse

from . import db
from .exceptions import DatabaseUnavailable

log = logging.getLogger("nyaya.rest")

T = TypeVar("T")


def _safe(fn: Callable[..., T], *args: Any) -> Any:
    """Run a synchronous db function in a worker thread, returning its result."""
    return asyncio.to_thread(fn, *args)


def _error_response(exc: Exception, request: Request | None = None) -> JSONResponse:
    """Build a safe error response without leaking internal details.

    The full exception is logged server-side with the request_id; the client
    only sees a generic error code and the request_id for correlation.
    """
    request_id = getattr(getattr(request, "state", None), "request_id", None) if request else None

    if isinstance(exc, DatabaseUnavailable) or "DatabaseUnavailable" in type(exc).__name__:
        log.warning("Database unavailable (request_id=%s)", request_id, exc_info=True)
        body: dict[str, Any] = {"error": "database_unavailable"}
        if request_id:
            body["request_id"] = request_id
        return JSONResponse(body, status_code=503)

    log.error("Internal error (request_id=%s)", request_id, exc_info=True)
    body = {"error": "internal_error"}
    if request_id:
        body["request_id"] = request_id
    return JSONResponse(body, status_code=500)


async def corpus_stats_endpoint(_request: Request) -> JSONResponse:
    """GET /api/corpus-stats -> {counts: {...}, as_of: "YYYY-MM-DD"|null}"""
    try:
        stats = await _safe(db.corpus_stats)
        as_of = await _safe(db.corpus_as_of)
        return JSONResponse(
            {
                "counts": stats,
                "as_of": as_of.isoformat() if as_of else None,
            }
        )
    except Exception as exc:
        log.warning("corpus_stats endpoint failed", exc_info=True)
        return _error_response(exc, _request)


async def acts_endpoint(_request: Request) -> JSONResponse:
    """GET /api/acts -> [{short_name, full_name, year, kind, source, as_of, ...}]"""
    try:
        acts = await _safe(db.list_acts)
        return JSONResponse([a.model_dump(mode="json") for a in acts])
    except Exception as exc:
        log.warning("acts endpoint failed", exc_info=True)
        return _error_response(exc, _request)


async def judgments_endpoint(request: Request) -> JSONResponse:
    """GET /api/judgments?limit=50&offset=0 -> {items: [...], total: int}"""
    try:
        limit = max(1, min(int(request.query_params.get("limit", "50")), 200))
        offset = max(0, int(request.query_params.get("offset", "0")))
    except ValueError:
        return JSONResponse({"error": "bad_request", "detail": "limit/offset must be integers"}, status_code=400)
    try:
        judgments, total = await _safe(db.list_judgments, limit, offset)
        return JSONResponse(
            {
                "items": [j.model_dump(mode="json") for j in judgments],
                "total": total,
                "limit": limit,
                "offset": offset,
            }
        )
    except Exception as exc:
        log.warning("judgments endpoint failed", exc_info=True)
        return _error_response(exc, request)


async def tools_endpoint(request: Request) -> JSONResponse:
    """GET /api/tools -> introspected [{name, description}] list."""
    mcp = getattr(request.app.state, "mcp", None)
    if mcp is None:
        return JSONResponse({"error": "mcp_unavailable", "detail": "MCP app not registered on app.state"}, status_code=500)
    try:
        tools = await mcp.list_tools()
        items = []
        for t in tools:
            items.append(
                {
                    "name": getattr(t, "name", None) or "",
                    "description": getattr(t, "description", None) or "",
                }
            )
        return JSONResponse({"items": items, "total": len(items)})
    except Exception as exc:
        log.warning("tools endpoint failed", exc_info=True)
        return _error_response(exc, request)


async def health_summary_endpoint(_request: Request) -> JSONResponse:
    """GET /api/health-summary -> richer summary for the home stat band.

    Falls back to ``degraded`` if the DB is down so the SPA renders partial
    numbers instead of a blank.
    """
    try:
        stats = await _safe(db.corpus_stats)
        as_of = await _safe(db.corpus_as_of)
        status = "healthy"
    except Exception:
        stats = {}
        as_of = None
        status = "degraded"
    return JSONResponse(
        {
            "status": status,
            "counts": stats,
            "as_of": as_of.isoformat() if as_of else None,
        }
    )


def register(app: Any, mcp_instance: Any) -> None:
    """Mount the REST endpoints on the Starlette app.

    Called from ``server.py`` after ``mcp_instance.http_app()`` returns the
    ASGI app and before the static file mount.
    """
    app.state.mcp = mcp_instance

    routes = [
        ("api/corpus-stats", "GET", corpus_stats_endpoint),
        ("api/acts", "GET", acts_endpoint),
        ("api/judgments", "GET", judgments_endpoint),
        ("api/tools", "GET", tools_endpoint),
        ("api/health-summary", "GET", health_summary_endpoint),
    ]
    for path, method, handler in routes:
        app.router.add_route(f"/{path}", handler, methods=[method])
