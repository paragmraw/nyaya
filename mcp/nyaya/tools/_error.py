"""Structured error decorator for MCP tools.

FastMCP catches all exceptions raised inside tool functions and converts them
to ``ToolError(message)`` — a bare string with no structured data. This loses
the ``code``, ``kind``, and ``hint`` fields that ``NotFound`` carries, so LLM
clients cannot programmatically branch on error type.

This module provides ``@structured_errors`` — a decorator that wraps a tool
function and catches :class:`~nyaya.exceptions.NyayaError` subclasses,
converting them to a :class:`fastmcp.tools.base.ToolResult` with
``is_error=True`` and ``structured_content`` containing the machine-readable
error fields. The human-readable message is kept in ``content`` as
``TextContent``.

Usage::

    @mcp.tool(...)
    @run_sync
    @structured_errors
    def get_section(act: str, section: str) -> Document:
        ...
        raise NotFound("Section 9999 not found", kind="section", hint="try semantic_query")

The decorator must be applied **inside** ``@run_sync`` (closer to the
function) so it can catch exceptions raised synchronously, and **outside**
``@mcp.tool`` wraps the already-decorated async wrapper.
"""

from __future__ import annotations

import functools
import logging
from typing import Any, ParamSpec, TypeVar

from fastmcp.tools.base import ToolResult
from mcp.types import TextContent

from ..exceptions import NyayaError

log = logging.getLogger("nyaya.tools.errors")

P = ParamSpec("P")
T = TypeVar("T")


def structured_errors(func: Any) -> Any:
    """Catch :class:`NyayaError` and return a structured ``ToolResult``.

    Catches ``NyayaError`` (and subclasses like ``NotFound``) raised by the
    decorated function and converts them to a ``ToolResult`` with:
    - ``is_error=True``
    - ``content=[TextContent(text=message)]`` (human-readable)
    - ``structured_content={"error": {"code": ..., "message": ..., "kind": ..., "hint": ...}}``

    Non-:class:`NyayaError` exceptions are re-raised unchanged so FastMCP's
    default error handling applies.
    """

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await func(*args, **kwargs)
        except NyayaError as exc:
            error_data: dict[str, Any] = {
                "code": exc.code,
                "message": exc.message,
            }
            hint = getattr(exc, "hint", None)
            if hint:
                error_data["hint"] = hint
            kind = getattr(exc, "kind", None)
            if kind:
                error_data["kind"] = kind
            log.info("Structured error: %s", error_data)
            return ToolResult(
                content=[TextContent(type="text", text=exc.message)],
                structured_content={"error": error_data},
                is_error=True,
            )

    return wrapper
