"""Tests for nyaya_chat.tools_layer.native: direct-import LangChain v2 tools."""

from __future__ import annotations

import asyncio
import json

import pytest


@pytest.fixture
def sample_backend():
    """Seed InMemoryCorpusBackend into nyaya.storage for hermetic native tool tests."""
    from nyaya.config import get_settings as get_mcp_settings
    from nyaya.retrieval import build_retrieval_engine
    from nyaya.storage import close_all, set_backend, set_retrieval
    from nyaya.storage.memory_backend import InMemoryCorpusBackend

    backend = InMemoryCorpusBackend.from_sample_data()
    set_backend(backend)
    set_retrieval(build_retrieval_engine(get_mcp_settings(), backend))
    yield backend
    close_all()


def test_load_native_tools_returns_v2_tools(monkeypatch):
    """load_native_tools should return the 8 allowlisted v2 StructuredTool instances."""
    from nyaya_chat import config

    config.reset_settings_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")

    from nyaya_chat.tools_layer.native import load_native_tools

    tools = asyncio.run(load_native_tools())
    assert len(tools) == 8
    names = {t.name for t in tools}
    assert names == {
        "search_statutes",
        "get_provision",
        "compare_provisions",
        "browse_statute",
        "get_provision_history",
        "search_judgments",
        "get_judgment",
        "trace_precedent",
    }


def test_load_native_tools_respects_allowlist(monkeypatch):
    """Tools not in the allowlist should not be loaded."""
    from nyaya_chat import config

    config.reset_settings_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")
    monkeypatch.setattr("nyaya_chat.tools_layer.spec.DEFAULT_TOOLS", ("search_statutes",))

    from nyaya_chat.tools_layer.native import load_native_tools

    tools = asyncio.run(load_native_tools())
    assert len(tools) == 1
    assert tools[0].name == "search_statutes"


def test_native_tools_use_spec_descriptions():
    """Every allowlisted spec is backed by a native implementation."""
    from nyaya_chat.tools_layer import native
    from nyaya_chat.tools_layer.spec import tool_specs_by_name

    specs = tool_specs_by_name()
    for name in specs:
        assert name in native._IMPLS


def test_error_json_handles_all_9_nyaya_error_codes():
    """All 9 canonical NyayaError subclasses serialize cleanly via _error_json."""
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

    from nyaya_chat.tools_layer.native import _error_json

    errors = [
        NotFound("Section 999 not found", kind="section", did_you_mean=["302"]),
        AmbiguousReference("Multiple matches", kind="judgment", candidates=[{"case_id": "1"}]),
        TextUnavailableMetadataOnly("Scanned PDF", kind="judgment", metadata={"chars_per_page": 12.0}),
        PdfFetchError("S3 timeout", reason="timeout"),
        InvalidFilter("Bad tier", kind="court_tier", allowed_values=["supreme_court"]),
        QueryTooBroad("Add filter", kind="query", hint="Pass court='DHC'"),
        QueryTimeout("Exceeded 5s", kind="query"),
        StorageUnavailable("SQLite missing", degraded_capabilities=["sqlite_core"]),
        CommercialLicenseBlocked("NC dataset", license_spdx="CC-BY-NC-SA-4.0"),
    ]
    expected_codes = {
        "not_found",
        "ambiguous_reference",
        "text_unavailable_metadata_only",
        "pdf_fetch_error",
        "invalid_filter",
        "query_too_broad",
        "query_timeout",
        "storage_unavailable",
        "commercial_license_blocked",
    }
    seen_codes: set[str] = set()
    for err in errors:
        payload = json.loads(_error_json(err))
        assert "error" in payload
        code = payload["error"]["code"]
        seen_codes.add(code)
    assert seen_codes == expected_codes


@pytest.mark.asyncio
async def test_native_get_provision_and_search_statutes(sample_backend):
    from nyaya_chat.tools_layer.native import (
        _browse_statute,
        _compare_provisions,
        _get_judgment,
        _get_provision,
        _get_provision_history,
        _search_judgments,
        _search_statutes,
        _trace_precedent,
    )

    # 1. get_provision by act + ref
    raw = await _get_provision(act="IPC", ref="302")
    data = json.loads(raw)
    assert data["act"] == "IPC"
    assert data["ref"] == "302"
    assert "<corpus_text" in data["text"]
    assert data["validity"]["status"] == "repealed"

    # 2. get_provision by citation string
    raw_cite = await _get_provision(citation="IPC s.302")
    data_cite = json.loads(raw_cite)
    assert data_cite["act"] == "IPC"
    assert data_cite["ref"] == "302"

    # 3. search_statutes
    raw_search = await _search_statutes(query="murder punishment")
    data_search = json.loads(raw_search)
    assert data_search["total"] >= 1
    assert any(r["act"] in ("IPC", "BNS") for r in data_search["results"])
    assert "<corpus_text" in data_search["results"][0]["snippet"]

    # 4. compare_provisions
    raw_cmp = await _compare_provisions(source="IPC s.302")
    data_cmp = json.loads(raw_cmp)
    assert data_cmp["source_provision"]["act"] == "IPC"
    assert data_cmp["target_provisions"][0]["act"] == "BNS"
    assert data_cmp["target_provisions"][0]["ref"] == "103"

    # 5. browse_statute (catalog & toc)
    raw_cat = await _browse_statute()
    data_cat = json.loads(raw_cat)
    assert data_cat["mode"] == "catalog"
    assert len(data_cat["acts"]) >= 2

    raw_toc = await _browse_statute(act="IPC")
    data_toc = json.loads(raw_toc)
    assert data_toc["mode"] == "toc"

    # 6. get_provision_history
    raw_hist = await _get_provision_history(act="Constitution")
    data_hist = json.loads(raw_hist)
    assert data_hist["act"] == "Constitution"

    # 7. search_judgments
    raw_jsearch = await _search_judgments(query="AIR 1973 SC 1461")
    data_jsearch = json.loads(raw_jsearch)
    assert data_jsearch["returned"] >= 1
    assert data_jsearch["results"][0]["case_id"] == "sc:1973:kesavananda"

    # 8. get_judgment
    raw_j = await _get_judgment(identifier="AIR 1973 SC 1461")
    data_j = json.loads(raw_j)
    assert data_j["card"]["case_id"] == "sc:1973:kesavananda"

    # 9. trace_precedent
    raw_tp = await _trace_precedent(identifier="AIR 1973 SC 1461")
    data_tp = json.loads(raw_tp)
    assert data_tp["precedent_status"] == "unverified_automated"
    assert data_tp["target"]["case_id"] == "sc:1973:kesavananda"


@pytest.mark.asyncio
async def test_native_remaining_tools_and_error_paths(sample_backend):
    from nyaya_chat.tools_layer.native import (
        _compare_provisions,
        _corpus_status,
        _cross_reference,
        _get_judgment,
        _judgment_analytics,
        _search_statutes,
        _trace_precedent,
        _verify_citations,
    )

    # 1. cross_reference
    raw_xref = await _cross_reference(act="IPC", ref="302")
    data_xref = json.loads(raw_xref)
    assert data_xref["source"]["act"] == "IPC"
    assert data_xref["source"]["ref"] == "302"

    # 2. verify_citations
    raw_vc = await _verify_citations(
        text="Under IPC s. 302 and AIR 1973 SC 1461, contrast AIR 1973 SC 9999."
    )
    data_vc = json.loads(raw_vc)
    assert data_vc["precedent_status"] == "unverified_automated"
    statuses = {f["raw_citation"]: f["status"] for f in data_vc["findings"]}
    assert statuses["AIR 1973 SC 1461"] == "VERIFIED"
    assert statuses["AIR 1973 SC 9999"] == "NOT_FOUND_IN_CORPUS"

    # 3. corpus_status
    raw_cs = await _corpus_status()
    data_cs = json.loads(raw_cs)
    assert data_cs["service_version"] == "2.1.0"

    # 4. judgment_analytics (error or valid response)
    raw_ja = await _judgment_analytics(
        group_by="year",
        court_tier="district_court",
        commercial_safe_only=True,
    )
    data_ja = json.loads(raw_ja)
    assert data_ja["error"]["code"] == "commercial_license_blocked"

    # 5. Error paths for wrappers
    err_search = json.loads(await _search_statutes(query="   "))
    assert err_search["error"]["code"] == "invalid_filter"

    err_cmp = json.loads(await _compare_provisions(source="IPC s.99999"))
    assert err_cmp["error"]["code"] == "not_found"

    err_j = json.loads(await _get_judgment(identifier="AIR 1973 SC 9999"))
    assert err_j["error"]["code"] == "not_found"

    err_tp = json.loads(await _trace_precedent(identifier="AIR 1973 SC 9999"))
    assert err_tp["error"]["code"] == "not_found"

    err_xref = json.loads(await _cross_reference(act="IPC", ref="99999"))
    assert err_xref["error"]["code"] == "not_found"


@pytest.mark.asyncio
async def test_native_get_provision_not_found_returns_error_json(sample_backend):
    from nyaya_chat.tools_layer.native import _get_provision

    raw = await _get_provision(act="IPC", ref="99999")
    data = json.loads(raw)
    assert data["error"]["code"] == "not_found"
