"""Unit tests for the ``structured_errors`` decorator (``nyaya.tools._error``).

Verifies that ``NyayaError`` subclasses raised inside a tool function are
converted to a ``ToolResult`` with ``is_error=True`` and a structured
``error`` object containing ``code``, ``message``, ``kind``, and ``hint``.
"""

from __future__ import annotations

import asyncio

import pytest

from nyaya.exceptions import NotFound, SearchError
from nyaya.tools._error import structured_errors


def test_not_found_converted_to_structured_tool_result():
    """NotFound is caught and returned as a ToolResult with is_error=True."""

    @structured_errors
    async def fake_tool():
        raise NotFound("Section 9999 not found", kind="section", hint="try semantic_query")

    result = asyncio.run(fake_tool())
    assert result.is_error is True
    assert result.structured_content is not None
    err = result.structured_content["error"]
    assert err["code"] == "not_found"
    assert err["message"] == "Section 9999 not found"
    assert err["kind"] == "section"
    assert err["hint"] == "try semantic_query"
    assert result.content[0].text == "Section 9999 not found"


def test_search_error_converted_to_structured_tool_result():
    """SearchError is caught and returned as a ToolResult with is_error=True."""

    @structured_errors
    async def fake_tool():
        raise SearchError("query too long", hint="shorten it")

    result = asyncio.run(fake_tool())
    assert result.is_error is True
    err = result.structured_content["error"]
    assert err["code"] == "search_error"
    assert err["message"] == "query too long"
    assert "kind" not in err  # SearchError has no kind
    assert err["hint"] == "shorten it"


def test_non_nyaya_error_re_raised():
    """Non-NyayaError exceptions are re-raised unchanged (FastMCP handles them)."""

    @structured_errors
    async def fake_tool():
        raise ValueError("not a NyayaError")

    with pytest.raises(ValueError, match="not a NyayaError"):
        asyncio.run(fake_tool())


def test_successful_result_passthrough():
    """When no exception is raised, the result passes through unchanged."""

    @structured_errors
    async def fake_tool():
        return {"data": "ok"}

    result = asyncio.run(fake_tool())
    # Successful results pass through as-is (not wrapped in ToolResult —
    # FastMCP's tool layer handles conversion of return values).
    assert result == {"data": "ok"}


def test_not_found_without_hint():
    """NotFound without a hint omits the hint field from structured_content."""

    @structured_errors
    async def fake_tool():
        raise NotFound("missing", kind="article")

    result = asyncio.run(fake_tool())
    err = result.structured_content["error"]
    assert err["code"] == "not_found"
    assert err["kind"] == "article"
    assert "hint" not in err


def test_structured_content_has_error_key():
    """The structured_content dict has an 'error' key wrapping the error data."""

    @structured_errors
    async def fake_tool():
        raise NotFound("not found", kind="schedule", hint="list_schedules")

    result = asyncio.run(fake_tool())
    assert "error" in result.structured_content
    assert isinstance(result.structured_content["error"], dict)
