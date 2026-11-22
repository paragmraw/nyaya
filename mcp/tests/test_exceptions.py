"""Unit tests for the canonical 9 v2 ``NyayaError`` hierarchy."""

from __future__ import annotations

import pytest

from nyaya.exceptions import (
    AmbiguousReference,
    CommercialLicenseBlocked,
    ConfigurationError,
    InvalidFilter,
    NotFound,
    NyayaError,
    PdfFetchError,
    QueryTimeout,
    QueryTooBroad,
    StorageUnavailable,
    TextUnavailableMetadataOnly,
)

EXPECTED_CODES = {
    "not_found": NotFound,
    "ambiguous_reference": AmbiguousReference,
    "text_unavailable_metadata_only": TextUnavailableMetadataOnly,
    "pdf_fetch_error": PdfFetchError,
    "invalid_filter": InvalidFilter,
    "query_too_broad": QueryTooBroad,
    "query_timeout": QueryTimeout,
    "storage_unavailable": StorageUnavailable,
    "commercial_license_blocked": CommercialLicenseBlocked,
}


def test_exact_9_nyaya_error_subclasses() -> None:
    """Exactly 9 canonical NyayaError subclasses exist."""
    subclasses = set(NyayaError.__subclasses__())
    assert subclasses == set(EXPECTED_CODES.values())
    assert {cls.code for cls in subclasses} == set(EXPECTED_CODES.keys())


def test_nyaya_error_base_attributes() -> None:
    assert NyayaError.code == "nyaya_error"
    assert NyayaError.hint is None
    err = NyayaError("boom", hint="retry")
    assert err.message == "boom"
    assert err.hint == "retry"
    assert str(err) == "boom"


def test_not_found_payload() -> None:
    err = NotFound(
        "Section 999 not found",
        kind="section",
        did_you_mean=["302", "304"],
        hint="Try search_statutes",
    )
    assert err.code == "not_found"
    assert err.kind == "section"
    assert err.did_you_mean == ["302", "304"]
    d = err.to_dict()
    assert d == {
        "code": "not_found",
        "message": "Section 999 not found",
        "kind": "section",
        "did_you_mean": ["302", "304"],
        "hint": "Try search_statutes",
    }


def test_ambiguous_reference_payload() -> None:
    candidates = [{"case_id": "sc:1", "title": "A v B"}, {"case_id": "sc:2", "title": "A v C"}]
    err = AmbiguousReference("Multiple matches", kind="judgment", candidates=candidates)
    assert err.code == "ambiguous_reference"
    assert err.candidates == candidates
    assert err.to_dict()["candidates"] == candidates


def test_text_unavailable_metadata_only_payload() -> None:
    err = TextUnavailableMetadataOnly(
        "Scanned PDF without text layer",
        kind="judgment",
        metadata={"case_id": "hc:1", "chars_per_page": 0.0},
        hint="Download PDF directly",
    )
    assert err.code == "text_unavailable_metadata_only"
    assert err.to_dict()["metadata"]["case_id"] == "hc:1"


def test_pdf_fetch_error_payload() -> None:
    err = PdfFetchError("Key rejected", reason="disallowed_key")
    assert err.code == "pdf_fetch_error"
    assert err.reason == "disallowed_key"
    assert err.to_dict()["reason"] == "disallowed_key"


def test_invalid_filter_payload() -> None:
    err = InvalidFilter("Bad tier", kind="court_tier", allowed_values=["supreme_court", "high_court"])
    assert err.code == "invalid_filter"
    assert err.allowed_values == ["supreme_court", "high_court"]
    assert err.to_dict()["allowed_values"] == ["supreme_court", "high_court"]


def test_query_too_broad_and_timeout() -> None:
    broad = QueryTooBroad("Unfiltered 81M scan", hint="Supply state or year")
    timeout = QueryTimeout("DuckDB 5s timeout exceeded")
    assert broad.code == "query_too_broad"
    assert timeout.code == "query_timeout"
    assert broad.to_dict()["hint"] == "Supply state or year"


def test_storage_unavailable_and_commercial_blocked() -> None:
    storage = StorageUnavailable("Bucket unreachable", degraded_capabilities=["lance_fts"])
    blocked = CommercialLicenseBlocked("DDL blocked", license_spdx="CC-BY-NC-SA-4.0")
    assert storage.code == "storage_unavailable"
    assert storage.to_dict()["degraded_capabilities"] == ["lance_fts"]
    assert blocked.code == "commercial_license_blocked"
    assert blocked.to_dict()["license_spdx"] == "CC-BY-NC-SA-4.0"


def test_configuration_error_is_value_error_not_nyaya_error() -> None:
    err = ConfigurationError("Invalid PORT")
    assert isinstance(err, ValueError)
    assert not isinstance(err, NyayaError)


@pytest.mark.parametrize("code,exc_cls", list(EXPECTED_CODES.items()))
def test_all_9_errors_catchable_as_nyaya_error(code: str, exc_cls: type[NyayaError]) -> None:
    with pytest.raises(NyayaError) as exc_info:
        raise exc_cls("boom")
    assert exc_info.value.code == code
