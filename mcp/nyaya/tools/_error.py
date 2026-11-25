"""Structured error decorator for Nyaya v2.0 MCP tools.

FastMCP catches all exceptions raised inside tool functions and converts them
to ``ToolError(message)``: a bare string with no structured data. This module
provides ``@structured_errors`` which catches :class:`~nyaya.exceptions.NyayaError`
subclasses and converts them into a :class:`fastmcp.tools.base.ToolResult`
with ``is_error=True`` and ``structured_content={"error": exc.to_dict()}``.
"""

from __future__ import annotations

import functools
import inspect
import logging
from typing import Any

from fastmcp.tools.base import ToolResult
from mcp.types import TextContent

from ..exceptions import NyayaError

log = logging.getLogger("nyaya.tools.errors")


def structured_errors(func: Any) -> Any:
    """Catch :class:`NyayaError` and return a structured ``ToolResult``."""

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            res = func(*args, **kwargs)
            if inspect.isawaitable(res):
                return await res
            return res
        except NyayaError as exc:
            error_data = exc.to_dict()
            log.info("Structured error: %s", error_data)
            return ToolResult(
                content=[TextContent(type="text", text=exc.message)],
                structured_content={"error": error_data},
                is_error=True,
            )

    return wrapper
