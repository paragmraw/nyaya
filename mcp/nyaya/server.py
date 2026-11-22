"""Compatibility shim for ``nyaya.server`` delegating to ``mcp_app``, ``http_app``, and ``cli``.

Importing this module does not call ``get_settings()`` at import time; ``app``
and ``mcp_instance`` are constructed lazily on first attribute access so
``from nyaya.server import app`` continues to work in tests and ASGI servers.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .cli import main as _cli_main
from .http_app import (
    RequestIdMiddleware,
    _CachedStaticFiles,
    create_http_app,
)
from .mcp_app import create_mcp

if TYPE_CHECKING:
    app: Any
    mcp: Any
    mcp_instance: Any

__all__ = [
    "RequestIdMiddleware",
    "_CachedStaticFiles",
    "app",
    "create_app",
    "create_http_app",
    "create_mcp",
    "main",
    "mcp",
    "mcp_instance",
]

create_app = create_http_app

_cached_mcp: Any = None
_cached_app: Any = None


def _get_or_build() -> tuple[Any, Any]:
    global _cached_mcp, _cached_app
    if _cached_app is None:
        _cached_mcp = create_mcp()
        _cached_app = create_http_app(mcp=_cached_mcp)
    return _cached_mcp, _cached_app


def __getattr__(name: str) -> Any:
    if name in ("mcp", "mcp_instance"):
        mcp_inst, _ = _get_or_build()
        return mcp_inst
    if name == "app":
        _, http_app = _get_or_build()
        return http_app
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def main() -> None:
    raise SystemExit(_cli_main(["--transport", "http"]))


if __name__ == "__main__":
    main()
