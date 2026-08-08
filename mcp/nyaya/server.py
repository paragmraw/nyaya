"""nyaya MCP server: FastMCP app, lifespan, /health, entrypoint."""

from __future__ import annotations

import logging
import os
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan as lifespan_decorator
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from . import db
from .config import get_settings
from .exceptions import DatabaseUnavailable
from .ratelimit import register_rate_limiting
from .security_headers import SecurityHeadersMiddleware

log = logging.getLogger("nyaya")

# Allowed CORS origins. Edit this list to add domains.
# In production, restrict to known origins only.
_ALLOWED_ORIGINS = ["https://nyaya.parag.tech"]


@lifespan_decorator
async def nyaya_lifespan(server: FastMCP) -> AsyncIterator[None]:
    try:
        yield {}
    finally:
        db.close_db()


def _build_mcp() -> FastMCP:
    mcp = FastMCP(
        name="nyaya",
        instructions=(
            "nyaya is an Indian law MCP server. Use list_acts first to discover the corpus, "
            "then get_section / get_article to read specific provisions, search_law for "
            "keyword search, semantic_query for meaning-based search, and cross_reference "
            "to find related provisions (especially the bidirectional IPC<->BNS mapping). "
            "Every result includes source/license provenance — cite it when answering."
        ),
        lifespan=nyaya_lifespan,
    )

    from .resources import register as register_resources
    from .tools import register as register_tools

    register_tools(mcp)
    register_resources(mcp)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request: Request) -> JSONResponse:
        try:
            stats = db.corpus_stats()
            status = "healthy"
        except DatabaseUnavailable:
            stats = {}
            status = "degraded"
        return JSONResponse(
            {
                "status": status,
                "service": "nyaya",
                "version": "0.1.0",
                "counts": stats,
            }
        )

    return mcp


mcp_instance = _build_mcp()
app = mcp_instance.http_app(stateless_http=True, path="/mcp")


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Generate a UUID per request for log correlation and expose it as X-Request-ID."""

    async def dispatch(self, request: Request, call_next: Any) -> Response:
        request.state.request_id = str(uuid.uuid4())
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        return response


# Register middleware. These are security-critical — if any fails to import
# or initialise, we want the server to fail fast rather than silently start
# without protection. No bare ``except Exception`` here.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestIdMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "Accept"],
    allow_credentials=False,
)
register_rate_limiting(app)

# REST endpoints for the web frontend (see rest.py). Mounted before the
# static catch-all so /api/* takes precedence over SPA routes.
from . import rest as _rest  # noqa: E402

_rest.register(app, mcp_instance)


# Serve the built Next.js static export (web/out/) from the same origin so
# the SPA, REST, and MCP endpoints share one domain and one healthcheck. The
# mount is optional: local nyaya dev without a built SPA still works — the
# route simply 404s. In the Railway image the Dockerfile copies web/out/ here.
_WEB_OUT = os.environ.get("NYAYA_WEB_OUT", "web/out")
if os.path.isdir(_WEB_OUT):
    from starlette.staticfiles import StaticFiles  # noqa: E402

    # html=True enables SPA-style fallback: /corpus resolves to
    # /corpus/index.html; unknown paths resolve to /404.html (or a 404).
    app.mount("/", StaticFiles(directory=_WEB_OUT, html=True), name="web")


def main() -> None:
    import uvicorn

    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    log.info("Starting nyaya: %s", settings.as_dict())
    uvicorn.run(
        "nyaya.server:app",
        host="0.0.0.0",
        port=settings.port,
        log_level=settings.log_level.lower(),
        timeout_keep_alive=30,
        limit_concurrency=64,
    )


if __name__ == "__main__":
    main()
