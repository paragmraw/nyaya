"""Per-tool contract tests + tool-registration fail-fast tests for Nyaya MCP v2.0.

Covers:
1. Exact 12 canonical v2 tools registered with descriptions and read-only annotations.
2. Exact openWorldHint split: 5 local SQLite tools (openWorldHint=False) vs 7 Bucket/S3 tools (openWorldHint=True).
3. Fail-fast registration contract in tools/__init__.py (duplicates, raising modules, missing tools).
4. Behavioural contracts for all 12 tools against InMemoryCorpusBackend / mini_corpus fixture:
   - OWASP LLM01 <corpus_text ...> wrapping on every primary text field
   - commercial_safe_only=True blocking non-commercial records (CommercialLicenseBlocked)
   - trace_precedent mandatory precedent_status="unverified_automated"
   - get_judgment views (overview, paragraphs, search_within, docket) & publication ban enforcement
   - verify_citations deterministic audit statuses (VERIFIED, VERIFIED_REPEALED, VERIFIED_STRUCK_DOWN_OR_READ_DOWN, NOT_FOUND_IN_CORPUS, QUOTE_VERIFIED, QUOTE_MISMATCH)
"""

from __future__ import annotations

from datetime import date as Date
from typing import Any

import pytest
from fastmcp import FastMCP

from nyaya import storage
from nyaya import tools as tools_pkg
from nyaya.exceptions import PdfFetchError
from nyaya.models import (
    ActInfo,
    AmendmentRecord,
    CitationEdgeRecord,
    ConcordanceEntry,
    InvalidatingJudgment,
    JudgmentContent,
    JudgmentExcerpt,
    JudgmentMeta,
    ProvisionRecord,
    ProvisionRef,
    SourceProvenance,
    StatutoryValidity,
)
from nyaya.storage.memory_backend import InMemoryCorpusBackend

EXPECTED_15_TOOLS = {
    "search_statutes",
    "get_provision",
    "get_provisions_batch",
    "browse_statute",
    "compare_provisions",
    "get_provision_history",
    "cross_reference",
    "search_judgments",
    "get_judgment",
    "get_judgments_batch",
    "trace_precedent",
    "verify_citations",
    "judgment_analytics",
    "get_cause_list",
    "corpus_status",
}

LOCAL_SQLITE_TOOLS = {
    "get_provisions_batch",
    "compare_provisions",
    "cross_reference",
    "get_provision_history",
    "trace_precedent",
    "corpus_status",
}

OPEN_WORLD_TOOLS = EXPECTED_15_TOOLS - LOCAL_SQLITE_TOOLS


def _fresh_mcp() -> FastMCP:
    mcp = FastMCP(name="test-nyaya-v2")
    tools_pkg.register(mcp)
    return mcp


async def _tool_map(mcp: FastMCP) -> dict[str, Any]:
    return {t.name: t for t in await mcp.list_tools()}


def _build_rich_memory_backend() -> InMemoryCorpusBackend:
    backend = InMemoryCorpusBackend.from_sample_data()
    prov_safe = next(iter(backend.acts.values())).provenance
    prov_nc = SourceProvenance(
        source_id="ildc_research",
        source_name="ILDC Research Corpus",
        source_url="https://example.invalid/ildc",
        license_spdx="CC-BY-NC-4.0",
        commercial_safe=False,
        terms_status="non_commercial_only",
        as_of=Date(2026, 11, 22),
    )

    # Add struck-down IT Act s.66A
    p_66a = ProvisionRecord(
        doc_id="it-2000:66A",
        act_id="it-2000",
        act="ITAct",
        act_full_name="The Information Technology Act, 2000",
        ref="66A",
        ref_norm="66A",
        title="Punishment for sending offensive messages",
        text="Any person who sends, by means of a computer resource, any information that is grossly offensive...",
        validity=StatutoryValidity(
            status="struck_down",
            invalidating_judgment=InvalidatingJudgment(
                citation="(2015) 5 SCC 1",
                neutral_citation="2015 INSC 248",
                case_id="sc:2015:shreya_singhal",
                case_name="Shreya Singhal v. Union of India",
                decision_date=Date(2015, 3, 24),
                coram_size=2,
                effect="struck_down",
                holding_summary="Section 66A struck down in its entirety as violative of Article 19(1)(a).",
            ),
        ),
        provenance=prov_safe,
    )
    backend.provisions[p_66a.doc_id] = p_66a

    # Add concordance entry IPC 302 -> BNS 103
    backend.concordance.append(
        ConcordanceEntry(
            old_doc_id="ipc-1860:302",
            old_ref=ProvisionRef(act="IPC", ref="302", title="Punishment for murder"),
            new_doc_id="bns-2023:103",
            new_ref=ProvisionRef(act="BNS", ref="103", title="Punishment for murder"),
            mapping_type="one_to_one",
            substantive_delta="Restructured into sub-section (1) for murder and sub-section (2) for mob lynching.",
            punishment_delta="Added mob lynching clause in 103(2).",
            procedural_delta="Cognizable, non-bailable, triable by Court of Session.",
            savings_note="BNSS s.531: offences prior to 2024-07-01 continue under IPC.",
            verified_by="PRS Legislative Research",
            provenance=prov_safe,
        )
    )

    # Add Constitutional Amendment 42
    backend.amendments.append(
        AmendmentRecord(
            amendment_id="const-amend-42",
            number=42,
            title="The Constitution (Forty-second Amendment) Act, 1976",
            act="Constitution",
            year=1976,
            enactment_date=Date(1976, 12, 18),
            effective_date=Date(1977, 1, 3),
            action="amended",
            provisions_affected=["Preamble", "31C", "368"],
            summary="Inserted Socialist, Secular, and Integrity into the Preamble and amended Article 368.",
            provenance=prov_safe,
        )
    )

    # Add cross-reference edge
    backend.edges.append(
        CitationEdgeRecord(
            from_id="ipc-1860:302",
            from_kind="provision",
            to_id="bns-2023:103",
            to_kind="provision",
            relation="replaced_by",
            quoting_excerpt="IPC s.302 is replaced by BNS s.103(1).",
            from_ref=ProvisionRef(act="IPC", ref="302", title="Punishment for murder"),
            to_ref=ProvisionRef(act="BNS", ref="103", title="Punishment for murder"),
            provenance=prov_safe,
        )
    )

    # Add non-commercial ILDC judgment
    jm_nc = JudgmentMeta(
        case_id="ildc:2021:sample",
        court_tier="supreme_court",
        court_code="SCI",
        court_name="Supreme Court of India",
        neutral_citation="2021 INSC 999",
        reporter_citations=["2021 INSC 999"],
        title="Sample NC Research Case v. Union of India",
        decision_date=Date(2021, 5, 10),
        year=2021,
        bench_strength=3,
        snippet="Research-only judgment excerpt under CC-BY-NC-4.0.",
        provenance=prov_nc,
    )
    backend.judgments[jm_nc.case_id] = jm_nc
    return backend


# ---------------------------------------------------------------------------
# 1. Registration & Annotations Contract
# ---------------------------------------------------------------------------


async def test_all_15_v2_tools_register_with_expected_names():
    mcp = _fresh_mcp()
    assert {t.name for t in await mcp.list_tools()} == EXPECTED_15_TOOLS


async def test_every_tool_has_description_and_exact_annotations_split():
    tools = await _tool_map(_fresh_mcp())
    assert set(tools) == EXPECTED_15_TOOLS
    for name, t in tools.items():
        assert t.description, f"tool {name} has no description"
        ann = t.annotations
        assert ann is not None, f"tool {name} missing annotations"
        assert ann.readOnlyHint is True, f"tool {name} must set readOnlyHint=True"
        assert ann.destructiveHint is False, f"tool {name} must set destructiveHint=False"
        assert ann.idempotentHint is True, f"tool {name} must set idempotentHint=True"
        if name in LOCAL_SQLITE_TOOLS:
            assert ann.openWorldHint is False, f"local tool {name} must set openWorldHint=False"
        else:
            assert name in OPEN_WORLD_TOOLS
            assert ann.openWorldHint is True, f"bucket/S3 tool {name} must set openWorldHint=True"


# ---------------------------------------------------------------------------
# 2. Registration Fail-Fast Contract
# ---------------------------------------------------------------------------


def test_duplicate_tool_registration_raises():
    mcp = FastMCP(name="test")

    @mcp.tool(name="search_statutes")
    def _colliding() -> str:
        return "x"

    with pytest.raises(RuntimeError, match="would overwrite"):
        tools_pkg.register(mcp)


def test_duplicate_module_registration_raises_for_whole_loop():
    mcp = _fresh_mcp()
    with pytest.raises(RuntimeError, match="would overwrite"):
        tools_pkg.register(mcp)


def test_raising_register_module_raises_runtime_error():
    def _boom(_mcp: Any) -> None:
        raise ValueError("broken tool module")

    saved = tools_pkg._REGISTRATIONS[:]
    tools_pkg._REGISTRATIONS[:] = [(_boom, "broken", frozenset({"broken"}))]
    try:
        with pytest.raises(RuntimeError, match="broken tool module"):
            tools_pkg.register(FastMCP(name="t"))
    finally:
        tools_pkg._REGISTRATIONS[:] = saved


def test_malformed_registration_missing_tools_raises():
    def _noop(_mcp: Any) -> None:
        return None

    saved = tools_pkg._REGISTRATIONS[:]
    tools_pkg._REGISTRATIONS[:] = [(_noop, "search_statutes", frozenset({"search_statutes"}))]
    try:
        with pytest.raises(RuntimeError, match="did not land"):
            tools_pkg.register(FastMCP(name="t"))
    finally:
        tools_pkg._REGISTRATIONS[:] = saved


# ---------------------------------------------------------------------------
# 3. Behavioural Contracts Across the 12 Tools
# ---------------------------------------------------------------------------


async def test_search_statutes_frames_snippets_and_returns_hits():
    storage.set_backend(_build_rich_memory_backend())
    tools = await _tool_map(_fresh_mcp())
    res = await tools["search_statutes"].fn(query="murder")
    assert res.total >= 1
    assert res.results[0].act in {"IPC", "BNS"}
    assert res.results[0].snippet.startswith("<corpus_text ")
    assert res.results[0].snippet.endswith("</corpus_text>")


async def test_get_provision_parses_citation_and_frames_text():
    storage.set_backend(_build_rich_memory_backend())
    tools = await _tool_map(_fresh_mcp())
    res = await tools["get_provision"].fn(citation="IPC s.302")
    assert res.act == "IPC"
    assert res.ref == "302"
    assert res.validity.status == "repealed"
    assert res.validity.replaced_by[0].act == "BNS"
    assert res.text.startswith("<corpus_text ")
    assert "Whoever commits murder" in res.text


async def test_get_provision_not_found_returns_structured_error():
    storage.set_backend(_build_rich_memory_backend())
    tools = await _tool_map(_fresh_mcp())
    res = await tools["get_provision"].fn(act="IPC", ref="99999")
    assert res.is_error is True
    assert res.structured_content["error"]["code"] == "not_found"


async def test_browse_statute_catalog_toc_and_provisions():
    storage.set_backend(_build_rich_memory_backend())
    tools = await _tool_map(_fresh_mcp())
    catalog = await tools["browse_statute"].fn()
    assert catalog.mode == "catalog"
    assert catalog.total >= 2

    toc = await tools["browse_statute"].fn(act="IPC")
    assert toc.mode == "toc"
    assert toc.act_info is not None
    assert toc.act_info.short_name == "IPC"

    provs = await tools["browse_statute"].fn(act="IPC", kind="section", include_snippet=True)
    assert provs.mode == "provisions"
    assert provs.provisions[0].snippet is not None
    assert provs.provisions[0].snippet.startswith("<corpus_text ")


async def test_compare_provisions_resolves_concordance_and_manual_target():
    storage.set_backend(_build_rich_memory_backend())
    tools = await _tool_map(_fresh_mcp())
    res = await tools["compare_provisions"].fn(source="IPC s.302")
    assert res.mapping_type == "one_to_one"
    assert res.source_provision.act == "IPC"
    assert res.target_provisions[0].act == "BNS"
    assert res.target_provisions[0].ref == "103"
    assert res.source_provision.text.startswith("<corpus_text ")

    manual = await tools["compare_provisions"].fn(source="IPC s.302", target="BNS s.103")
    assert manual.target_provisions[0].ref == "103"


async def test_cross_reference_returns_framed_links():
    storage.set_backend(_build_rich_memory_backend())
    tools = await _tool_map(_fresh_mcp())
    res = await tools["cross_reference"].fn(act="IPC", ref="302", direction="from")
    assert res.source.act == "IPC"
    assert res.total >= 1
    assert res.links[0].to_ref.act == "BNS"
    assert res.links[0].context_note is not None
    assert res.links[0].context_note.startswith("<corpus_text ")


async def test_get_provision_history_returns_framed_amendment_summary():
    storage.set_backend(_build_rich_memory_backend())
    tools = await _tool_map(_fresh_mcp())
    res = await tools["get_provision_history"].fn(act="Constitution", amendment_number=42)
    assert res.total == 1
    assert res.amendments[0].year == 1976
    assert res.amendments[0].summary.startswith("<corpus_text ")


async def test_trace_precedent_enforces_unverified_automated_status():
    storage.set_backend(_build_rich_memory_backend())
    tools = await _tool_map(_fresh_mcp())
    res = await tools["trace_precedent"].fn(identifier="AIR 1973 SC 1461")
    assert res.precedent_status == "unverified_automated"
    assert "does NOT perform automated Shepardizing" in res.verification_notice or "unverified" in res.verification_notice.lower()
    assert res.target.case_id == "sc:1973:kesavananda"


async def test_search_judgments_returns_cards_and_frames_snippets():
    storage.set_backend(_build_rich_memory_backend())
    tools = await _tool_map(_fresh_mcp())

    res = await tools["search_judgments"].fn(query="2021 INSC 999")
    assert res.total >= 1
    assert res.returned >= 1
    card = res.results[0]
    assert card.case_id == "ildc:2021:sample"
    assert card.neutral_citation == "2021 INSC 999"
    assert card.snippet is not None
    assert card.snippet.startswith("<corpus_text ")
    assert card.snippet.endswith("</corpus_text>")
    assert "Research-only judgment excerpt" in card.snippet

    res_sc = await tools["search_judgments"].fn(query="AIR 1973 SC 1461")
    assert res_sc.total >= 1
    assert res_sc.results[0].case_id == "sc:1973:kesavananda"


async def test_get_judgment_modes_and_commercial_safe_block():
    backend = _build_rich_memory_backend()
    backend.judgments["sc:1973:kesavananda"].pdf_s3_bucket = "indian-supreme-court-judgments"
    backend.judgments["sc:1973:kesavananda"].pdf_s3_key = "data/pdf/year=1973/english/kesavananda.pdf"
    storage.set_backend(backend)

    class _FakePdfFetcher:
        async def fetch_and_read(self, meta: JudgmentMeta, **kwargs: Any) -> JudgmentContent:
            return JudgmentContent(
                case_id=meta.case_id,
                mode=kwargs.get("mode", "overview"),
                extraction_quality="verified",
                extraction_method="pypdfium2_s3_pdf",
                excerpts=[
                    JudgmentExcerpt(
                        para_num=1,
                        text="The basic structure of the Constitution cannot be abrogated under Article 368.",
                    )
                ],
                total_paragraphs=10,
                total_pages=5,
                chars_per_page=1200.0,
            )

        async def aclose(self) -> None:
            return None

    storage.set_pdf_fetcher(_FakePdfFetcher())
    tools = await _tool_map(_fresh_mcp())

    # Overview mode + mode="query_excerpt" alias support
    res = await tools["get_judgment"].fn(identifier="AIR 1973 SC 1461", view="overview")
    assert res.card.case_id == "sc:1973:kesavananda"
    assert res.excerpts[0].text.startswith("<corpus_text ")

    res_alias = await tools["get_judgment"].fn(
        identifier="AIR 1973 SC 1461",
        mode="query_excerpt",
        excerpt_query="basic structure",
    )
    assert res_alias.view == "search_within"
    assert res_alias.excerpts[0].text.startswith("<corpus_text ")

    # Commercial license block on ILDC NC record when commercial_safe_only=True
    blocked = await tools["get_judgment"].fn(
        identifier="2021 INSC 999",
        commercial_safe_only=True,
    )
    assert blocked.is_error is True
    assert blocked.structured_content["error"]["code"] == "commercial_license_blocked"


async def test_verify_citations_detects_repealed_struck_down_and_hallucinated():
    storage.set_backend(_build_rich_memory_backend())
    tools = await _tool_map(_fresh_mcp())
    draft = (
        "The accused is charged under IPC s. 302 and IT Act s. 66A. "
        "Reliance is placed on AIR 1973 SC 1461 and AIR 1973 SC 9999."
    )
    res = await tools["verify_citations"].fn(
        text=draft,
        verify_quotes=[{"citation": "IPC s. 302", "quote": "Whoever commits murder"}],
    )
    assert res.precedent_status == "unverified_automated"
    statuses = {f.raw_citation: f.status for f in res.findings}
    assert any(f.status == "VERIFIED_REPEALED" for f in res.findings)
    assert any(f.status == "VERIFIED_STRUCK_DOWN_OR_READ_DOWN" for f in res.findings)
    assert statuses.get("AIR 1973 SC 1461") == "VERIFIED"
    assert statuses.get("AIR 1973 SC 9999") == "NOT_FOUND_IN_CORPUS"
    assert any(f.status == "QUOTE_VERIFIED" for f in res.findings)


async def test_judgment_analytics_blocks_district_court_when_commercial_safe():
    storage.set_backend(_build_rich_memory_backend())
    tools = await _tool_map(_fresh_mcp())
    res = await tools["judgment_analytics"].fn(
        group_by="year",
        court_tier="district_court",
        state="MH",
        commercial_safe_only=True,
    )
    assert res.is_error is True
    assert res.structured_content["error"]["code"] == "commercial_license_blocked"


async def test_corpus_status_returns_v2_counts():
    storage.set_backend(_build_rich_memory_backend())
    tools = await _tool_map(_fresh_mcp())
    res = await tools["corpus_status"].fn()
    assert res.service_version == "2.0.0"
    assert res.retrieval_mode == "lexical_metadata_graph"
    assert res.counts.central_acts >= 2


async def test_get_judgment_overview_s3_fallback_and_non_overview_hint():
    backend = _build_rich_memory_backend()
    jm = backend.judgments["sc:1973:kesavananda"]
    jm.pdf_s3_bucket = "indian-supreme-court-judgments"
    jm.pdf_s3_key = "data/pdf/year=1973/english/kesavananda.pdf"
    storage.set_backend(backend)

    class _FailingPdfFetcher:
        async def fetch_and_read(self, meta: JudgmentMeta, **kwargs: Any) -> JudgmentContent:
            raise PdfFetchError("S3 returned HTTP 404", reason="http_error")

        async def aclose(self) -> None:
            return None

    storage.set_pdf_fetcher(_FailingPdfFetcher())
    tools = await _tool_map(_fresh_mcp())

    # 1. view="overview" degrades gracefully with snippet + docket + warning
    overview = await tools["get_judgment"].fn(identifier="AIR 1973 SC 1461", view="overview")
    assert overview.extraction_method == "metadata_only"
    assert overview.docket is not None
    assert overview.warning is not None
    assert "Upstream S3 PDF unavailable" in overview.warning
    assert len(overview.excerpts) >= 1
    assert overview.excerpts[0].text.startswith("<corpus_text ")

    # 2. view="overview" when snippet is empty falls back to structured metadata summary
    jm.snippet = None
    overview_no_snip = await tools["get_judgment"].fn(identifier="AIR 1973 SC 1461", view="overview")
    assert overview_no_snip.extraction_method == "metadata_only"
    assert overview_no_snip.docket is not None
    assert len(overview_no_snip.excerpts) == 1
    assert "Kesavananda" in overview_no_snip.excerpts[0].text

    # 3. Non-overview views re-raise PdfFetchError with actionable hint
    for bad_view in ("paragraphs", "pages", "search_within"):
        kwargs: dict[str, Any] = {"identifier": "AIR 1973 SC 1461", "view": bad_view}
        if bad_view == "search_within":
            kwargs["within_query"] = "basic structure"
        err = await tools["get_judgment"].fn(**kwargs)
        assert err.is_error is True
        assert err.structured_content["error"]["code"] == "pdf_fetch_error"
        assert "view='docket' or view='overview'" in err.structured_content["error"]["hint"]


async def test_verify_citations_compact_omits_parsed_and_caps_excerpt():
    backend = _build_rich_memory_backend()
    long_text = "Whoever commits murder shall be punished with death. " + ("X" * 350)
    backend.provisions["ipc-1860:302"] = backend.provisions["ipc-1860:302"].model_copy(
        update={"text": long_text}
    )
    storage.set_backend(backend)
    tools = await _tool_map(_fresh_mcp())

    res = await tools["verify_citations"].fn(
        text="Reliance on IPC s. 302 and AIR 1973 SC 1461.",
        verify_quotes=[{"citation": "IPC s. 302", "quote": "Whoever commits murder"}],
        compact=True,
    )
    assert len(res.findings) >= 2
    for finding in res.findings:
        assert finding.parsed is None
        if finding.matched_excerpt:
            inner = finding.matched_excerpt.split(">", 1)[1].rsplit("</corpus_text>", 1)[0]
            assert len(inner.strip()) <= 180


async def test_cross_reference_merges_concordance_without_duplicate_edges():
    """cross_reference surfaces IPC 302 <-> BNS 103 from concordance even when citation_edges is empty, and dedups."""
    backend = _build_rich_memory_backend()
    # Remove the citation_edges entry so only backend.concordance holds IPC 302 -> BNS 103
    backend.edges.clear()
    storage.set_backend(backend)
    tools = await _tool_map(_fresh_mcp())

    res_from = await tools["cross_reference"].fn(act="IPC", ref="302", direction="from")
    assert res_from.total == 1
    assert res_from.returned == 1
    assert res_from.links[0].from_ref.act == "IPC"
    assert res_from.links[0].from_ref.ref == "302"
    assert res_from.links[0].to_ref.act == "BNS"
    assert res_from.links[0].to_ref.ref == "103"
    assert res_from.links[0].relation_kind == "replaced_by"
    assert res_from.links[0].context_note is not None
    assert res_from.links[0].context_note.startswith("<corpus_text ")
    assert "Restructured into sub-section (1)" in res_from.links[0].context_note
    assert "BNSS s.531" in res_from.links[0].context_note

    res_to = await tools["cross_reference"].fn(act="BNS", ref="103", direction="to")
    assert res_to.total == 1
    assert res_to.links[0].from_ref.act == "IPC"
    assert res_to.links[0].to_ref.act == "BNS"
    assert res_to.links[0].relation_kind == "replaced_by"

    res_corr = await tools["cross_reference"].fn(
        act="IPC", ref="302", direction="both", relation_kind="corresponds_to"
    )
    assert res_corr.total == 1
    assert res_corr.links[0].relation_kind == "corresponds_to"

    # Re-add identical edge and verify deduplication keeps total == 1
    backend_with_edge = _build_rich_memory_backend()
    storage.set_backend(backend_with_edge)
    res_dedup = await tools["cross_reference"].fn(act="IPC", ref="302", direction="both")
    assert res_dedup.total == 1
    assert res_dedup.returned == 1


async def test_verify_citations_norm_quote_lance_and_pdf_fallback(monkeypatch: Any) -> None:
    backend = _build_rich_memory_backend()
    # Add India Code style superscript and markdown/curly punctuation to IPC 302
    backend.provisions["ipc-1860:302"] = backend.provisions["ipc-1860:302"].model_copy(
        update={
            "text": "1[Whoever commits **murder** shall be punished with \u201cdeath\u201d \u2014 or imprisonment.]"
        }
    )
    jm = backend.judgments["sc:1973:kesavananda"]
    jm.pdf_s3_bucket = "indian-supreme-court-judgments"
    jm.pdf_s3_key = "data/pdf/year=1973/english/kesavananda.pdf"
    storage.set_backend(backend)

    class _FakeLance:
        def get_judgment_chunks(
            self, case_id: str, *, commercial_safe_only: bool | None = None
        ) -> list[dict[str, Any]]:
            if case_id == "sc:1973:kesavananda":
                return [
                    {
                        "para_start": 42,
                        "text": "Article 368 does not enable Parliament to alter the basic structure.",
                    }
                ]
            return []

    class _FakeRetrieval:
        lance = _FakeLance()

    class _FakePdfFetcher:
        async def fetch_and_read(self, meta: JudgmentMeta, **kwargs: Any) -> JudgmentContent:
            return JudgmentContent(
                case_id=meta.case_id,
                mode=kwargs.get("mode", "search_within"),
                extraction_quality="verified",
                extraction_method="pypdfium2_s3_pdf",
                excerpts=[
                    JudgmentExcerpt(
                        para_num=107,
                        text="Fundamental rights and directive principles form the conscience of the Constitution.",
                    )
                ],
                total_paragraphs=200,
                total_pages=50,
                chars_per_page=1500.0,
            )

        async def aclose(self) -> None:
            return None

    storage.set_retrieval(_FakeRetrieval())
    storage.set_pdf_fetcher(_FakePdfFetcher())
    monkeypatch.delenv("NYAYA_OFFLINE_ONLY", raising=False)
    from nyaya.config import get_settings

    get_settings.cache_clear()

    try:
        tools = await _tool_map(_fresh_mcp())
        res = await tools["verify_citations"].fn(
            text="Reliance on 2024 INSC 999999.",
            verify_quotes=[
                {
                    "citation": "IPC s. 302",
                    "quote": '[Whoever commits murder shall be punished with "death" - or imprisonment.]',
                },
                {
                    "citation": "AIR 1973 SC 1461",
                    "quote": "Article 368 does not enable Parliament to alter the basic structure.",
                },
                {
                    "citation": "AIR 1973 SC 1461",
                    "quote": "conscience of the Constitution",
                },
            ],
        )
        assert res.findings[0].raw_citation == "2024 INSC 999999"
        assert res.findings[0].status == "NOT_FOUND_IN_CORPUS"

        # Quote 1: India Code superscript + curly quote + em-dash + markdown normalized
        assert res.findings[1].status == "QUOTE_VERIFIED"
        assert res.findings[1].matched_para_num == 1

        # Quote 2: Verified via LanceDB judgment chunk with exact para_start=42
        assert res.findings[2].status == "QUOTE_VERIFIED"
        assert res.findings[2].matched_para_num == 42

        # Quote 3: Verified via PDF search_within fallback with para_num=107
        assert res.findings[3].status == "QUOTE_VERIFIED"
        assert res.findings[3].matched_para_num == 107
        assert res.summary.get("QUOTE_VERIFIED") == 3
    finally:
        storage.set_retrieval(None)
        storage.set_pdf_fetcher(None)


async def test_verify_citations_cpc_order_rule_missing_returns_not_found_not_invalid() -> None:
    backend = _build_rich_memory_backend()
    prov_safe = next(iter(backend.acts.values())).provenance
    backend.acts["CPC"] = ActInfo(
        act_id="cpc-1908",
        short_name="CPC",
        full_name="The Code of Civil Procedure, 1908",
        year=1908,
        status="in_force",
        provision_count=1,
        provenance=prov_safe,
    )
    storage.set_backend(backend)
    tools = await _tool_map(_fresh_mcp())

    res = await tools["verify_citations"].fn(
        citations=["CPC Order XXXIX Rule 1", "IPC s. 99999"],
    )
    by_raw = {f.raw_citation: f for f in res.findings}
    assert by_raw["CPC Order XXXIX Rule 1"].status == "NOT_FOUND_IN_CORPUS"
    assert "partial CPC Order/Rule coverage" in (by_raw["CPC Order XXXIX Rule 1"].coverage_note or "")
    assert by_raw["IPC s. 99999"].status == "INVALID_STATUTORY_REF"
