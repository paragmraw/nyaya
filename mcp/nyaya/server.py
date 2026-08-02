"""nyaya MCP server: FastMCP app, lifespan, /health, entrypoint."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator

from fastmcp import FastMCP
from fastmcp.server.lifespan import lifespan as lifespan_decorator

from . import db
from .config import get_settings

log = logging.getLogger("nyaya")


@lifespan_decorator
async def nyaya_lifespan(server: FastMCP) -> AsyncIterator[None]:
    try:
        yield {}
    finally:
        db.close_db()


def _build_app() -> FastMCP:
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

    from .tools import register as register_tools
    from .resources import register as register_resources

    register_tools(mcp)
    register_resources(mcp)

    @mcp.custom_route("/health", methods=["GET"])
    async def health(_request):  # type: ignore[no-untyped-def]
        from starlette.responses import JSONResponse

        try:
            stats = db.corpus_stats()
            status = "healthy"
        except Exception as e:
            stats = {}
            status = f"degraded: {e}"
        return JSONResponse(
            {
                "status": status,
                "service": "nyaya",
                "version": "0.1.0",
                "counts": stats,
            }
        )

    return mcp


mcp_instance = _build_app()
app = mcp_instance.http_app(stateless_http=True, path="/mcp")

# Add permissive CORS so browser-based MCP clients can call the endpoint.
# Auth/rate-limiting are intentionally out of scope for the alpha; deploy behind
# a reverse proxy (Cloudflare, Railway's edge) for production hardening.
try:
    from starlette.middleware.cors import CORSMiddleware

    # Starlette ASGI middleware must be added to the underlying app.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
except Exception:  # pragma: no cover — middleware is optional
    log.warning("Could not add CORS middleware; continuing without it.", exc_info=True)


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
    )


if __name__ == "__main__":
    main()