"""Unit tests for the ``structured_errors`` decorator (``nyaya.tools._error``).

Verifies that all 9 canonical ``NyayaError`` subclasses raised inside a tool
function (sync or async) are converted to a ``ToolResult`` with
``is_error=True`` and ``structured_content={"error": exc.to_dict()}``.
"""

from __future__ import annotations

import asyncio

import pytest

from nyaya.exceptions import (
    AmbiguousReference,
    CommercialLicenseBlocked,
    InvalidFilter,
    NotFound,
    PdfFetchError,
    QueryTimeout,
    QueryTooBroad,
    StorageUnavailable,
    TextUnavailableMetadataOnly,
)
from nyaya.tools._error import structured_errors


def test_not_found_converted_to_structured_tool_result():
    """NotFound is caught and returned as a ToolResult with is_error=True."""

    @structured_errors
    async def fake_tool():
        raise NotFound(
            "Section 9999 not found",
            kind="section",
            did_you_mean=["302", "304"],
            hint="try search_statutes",
        )

    result = asyncio.run(fake_tool())
    assert result.is_error is True
    assert result.structured_content is not None
    err = result.structured_content["error"]
    assert err["code"] == "not_found"
    assert err["message"] == "Section 9999 not found"
    assert err["kind"] == "section"
    assert err["did_you_mean"] == ["302", "304"]
    assert err["hint"] == "try search_statutes"
    assert result.content[0].text == "Section 9999 not found"


def test_all_9_canonical_nyaya_errors_serialize_cleanly():
    """Every v2 NyayaError subclass maps onto its exact canonical error code."""
    cases = [
        (NotFound("missing", kind="act"), "not_found"),
        (
            AmbiguousReference("multiple matches", kind="judgment", candidates=[{"case_id": "1"}]),
            "ambiguous_reference",
        ),
        (
            TextUnavailableMetadataOnly("scanned pdf", kind="judgment", metadata={"chars_per_page": 12.0}),
            "text_unavailable_metadata_only",
        ),
        (PdfFetchError("s3 timeout", reason="timeout"), "pdf_fetch_error"),
        (InvalidFilter("bad group_by", kind="group_by", allowed_values=["year"]), "invalid_filter"),
        (QueryTooBroad("unfiltered hc scan", hint="pass court='DHC'"), "query_too_broad"),
        (QueryTimeout("exceeded 5s"), "query_timeout"),
        (StorageUnavailable("lance down", degraded_capabilities=["lance_fts"]), "storage_unavailable"),
        (
            CommercialLicenseBlocked("DDL is CC-BY-NC-SA-4.0", license_spdx="CC-BY-NC-SA-4.0"),
            "commercial_license_blocked",
        ),
    ]
    for exc, expected_code in cases:

        @structured_errors
        def sync_tool(e=exc):
            raise e

        res = asyncio.run(sync_tool())
        assert res.is_error is True
        err = res.structured_content["error"]
        assert err["code"] == expected_code


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
        raise NotFound("not found", kind="schedule", hint="browse_statute")

    result = asyncio.run(fake_tool())
    assert "error" in result.structured_content
    assert isinstance(result.structured_content["error"], dict)
