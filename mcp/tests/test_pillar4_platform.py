"""Pillar 4 platform & workflow tests (TDD):
1. Point-in-time statutory reconstruction (`storage/point_in_time.py`)
2. Cause-list local store & official feed parser (`storage/cause_list_store.py`)
3. BYO live docket adapter (`storage/live_docket.py`)
4. Batch provision retrieval tool (`tools/get_provisions_batch.py`)
5. Batch judgment retrieval tool (`tools/get_judgments_batch.py`)
6. Cause-list MCP tool (`tools/get_cause_list.py`)
7. CLI pack synchronization (`cli.sync_packs` & `nyaya-mcp sync`)
8. MCP Registry `server.json` & `.github/workflows/release-pypi.yml`
"""

from __future__ import annotations

import hashlib
import io
import json
import sqlite3
import tarfile
import zipfile
from datetime import date as Date
from pathlib import Path

import httpx
import pytest
import yaml
import zstandard as zstd
from fastmcp import FastMCP

from nyaya import cli, storage
from nyaya.config import Settings
from nyaya.exceptions import InvalidFilter, StorageUnavailable
from nyaya.ingest.lockfile import _BANNED_URI_SUBSTRINGS
from nyaya.models import (
    AmendmentRecord,
    InvalidatingJudgment,
    JudgmentMeta,
    ProvisionRecord,
    ProvisionRef,
    SourceProvenance,
    StatutoryLink,
    StatutoryValidity,
)
from nyaya.storage.memory_backend import InMemoryCorpusBackend


def _prov_safe() -> SourceProvenance:
    return SourceProvenance(
        source_id="indiacode_central",
        source_name="India Code Central Acts",
        source_url="https://www.indiacode.nic.in/handle/123456789/1",
        license_spdx="IN-S52-1-q",
        commercial_safe=True,
        terms_status="cleared",
        as_of=Date(2026, 11, 22),
    )


def _prov_nc() -> SourceProvenance:
    return SourceProvenance(
        source_id="ildc_research",
        source_name="ILDC Research Corpus",
        source_url="https://example.invalid/ildc",
        license_spdx="CC-BY-NC-4.0",
        commercial_safe=False,
        terms_status="non_commercial_only",
        as_of=Date(2026, 11, 22),
    )


# ---------------------------------------------------------------------------
# 1. Point-in-time reconstruction
# ---------------------------------------------------------------------------


def test_point_in_time_commencement_repeal_and_invalidation():
    from nyaya.storage.point_in_time import PointInTimeResult, reconstruct_provision_as_of

    prov_bns = ProvisionRecord(
        doc_id="bns-2023:103",
        act_id="bns-2023",
        act="BNS",
        act_full_name="Bharatiya Nyaya Sanhita, 2023",
        ref="103",
        ref_norm="103",
        title="Punishment for murder",
        text="Whoever commits murder shall be punished with death or imprisonment for life.",
        validity=StatutoryValidity(
            status="in_force",
            commencement_date=Date(2024, 7, 1),
        ),
        provenance=_prov_safe(),
    )

    before = reconstruct_provision_as_of(prov_bns, [], [], as_of="2024-01-15")
    assert isinstance(before, PointInTimeResult)
    assert before.effective_status == "not_yet_in_force"
    assert before.as_of == Date(2024, 1, 15)

    on_commencement = reconstruct_provision_as_of(prov_bns, [], [], as_of=Date(2024, 7, 1))
    assert on_commencement.effective_status == "in_force"

    prov_ipc = ProvisionRecord(
        doc_id="ipc-1860:302",
        act_id="ipc-1860",
        act="IPC",
        act_full_name="Indian Penal Code, 1860",
        ref="302",
        ref_norm="302",
        title="Punishment for murder",
        text="Whoever commits murder shall be punished with death...",
        validity=StatutoryValidity(
            status="repealed",
            commencement_date=Date(1862, 1, 1),
            repeal_date=Date(2024, 7, 1),
        ),
        provenance=_prov_safe(),
    )
    ipc_2023 = reconstruct_provision_as_of(prov_ipc, [], [], as_of="2023-06-01")
    assert ipc_2023.effective_status == "in_force"

    ipc_2024 = reconstruct_provision_as_of(prov_ipc, [], [], as_of="2024-07-01")
    assert ipc_2024.effective_status == "repealed"

    inval = InvalidatingJudgment(
        citation="(2015) 5 SCC 1",
        neutral_citation="2015 INSC 248",
        case_id="sc:2015:shreya_singhal",
        case_name="Shreya Singhal v. Union of India",
        decision_date=Date(2015, 3, 24),
        coram_size=2,
        effect="struck_down",
        holding_summary="Struck down in entirety.",
    )
    prov_66a = ProvisionRecord(
        doc_id="it-2000:66A",
        act_id="it-2000",
        act="ITAct",
        act_full_name="Information Technology Act, 2000",
        ref="66A",
        ref_norm="66A",
        title="Offensive messages",
        text="Any person who sends...",
        validity=StatutoryValidity(
            status="struck_down",
            commencement_date=Date(2009, 10, 27),
            invalidating_judgment=inval,
        ),
        provenance=_prov_safe(),
    )

    pre_shreya = reconstruct_provision_as_of(prov_66a, [], [inval], as_of="2014-01-01")
    assert pre_shreya.effective_status == "in_force"
    assert pre_shreya.active_invalidation is None

    post_shreya = reconstruct_provision_as_of(prov_66a, [], [inval], as_of="2015-03-24")
    assert post_shreya.effective_status == "struck_down"
    assert post_shreya.active_invalidation is not None

    read_down_inval = InvalidatingJudgment(
        citation="(2018) 10 SCC 1",
        case_id="sc:2018:navtej",
        case_name="Navtej Singh Johar v. Union of India",
        decision_date=Date(2018, 9, 6),
        coram_size=5,
        effect="read_down",
        holding_summary="Read down as applied to consensual adults.",
    )
    post_navtej = reconstruct_provision_as_of(prov_ipc, [], [read_down_inval], as_of="2019-01-01")
    assert post_navtej.effective_status == "read_down"
    abeyance_inval = InvalidatingJudgment(
        citation="(2022) 7 SCC 1",
        case_id="sc:2022:sg_vombatkere",
        case_name="S.G. Vombatkere v. Union of India",
        decision_date=Date(2022, 5, 11),
        coram_size=3,
        effect="kept_in_abeyance",
        holding_summary="Kept in abeyance pending re-examination.",
    )
    post_abeyance = reconstruct_provision_as_of(prov_ipc, [], [abeyance_inval], as_of="2023-01-01")
    assert post_abeyance.effective_status == "kept_in_abeyance"


def test_point_in_time_chronological_amendments_and_versions():
    from nyaya.storage.point_in_time import reconstruct_provision_as_of

    prov = ProvisionRecord(
        doc_id="const:21",
        act_id="const",
        act="Constitution",
        act_full_name="The Constitution of India",
        ref="21",
        ref_norm="21",
        title="Protection of life and personal liberty",
        text="Current text v3",
        validity=StatutoryValidity(
            status="in_force",
            commencement_date=Date(1950, 1, 26),
        ),
        provenance=_prov_safe(),
        metadata={
            "initial_text": "Original text 1950",
            "provision_versions": [
                {"effective_date": "1950-01-26", "text": "Original text 1950"},
                {"effective_date": "1978-06-20", "text": "Amended text 1978"},
                {"effective_date": "2010-04-01", "text": "Current text v3"},
            ],
        },
    )

    res_1970 = reconstruct_provision_as_of(prov, [], [], as_of="1970-01-01")
    assert res_1970.text == "Original text 1950"
    assert res_1970.reconstruction_basis

    res_1990 = reconstruct_provision_as_of(prov, [], [], as_of="1990-01-01")
    assert res_1990.text == "Amended text 1978"

    # Test amendment records list without provision_versions
    prov_am = ProvisionRecord(
        doc_id="act:10",
        act_id="act",
        act="TestAct",
        act_full_name="Test Act",
        ref="10",
        ref_norm="10",
        title="Section 10",
        text="Base text",
        validity=StatutoryValidity(status="in_force", commencement_date=Date(2000, 1, 1)),
        provenance=_prov_safe(),
    )
    amends = [
        {
            "amendment_id": "am-2",
            "title": "Amendment 2020",
            "effective_date": "2020-01-01",
            "action": "substituted",
            "summary": "Substituted in 2020",
            "text": "Text 2020",
        },
        {
            "amendment_id": "am-1",
            "title": "Amendment 2010",
            "effective_date": "2010-01-01",
            "action": "amended",
            "summary": "Amended in 2010",
            "text": "Text 2010",
        },
    ]
    r_2005 = reconstruct_provision_as_of(prov_am, amends, [], as_of="2005-01-01")
    assert r_2005.text == "Base text"
    assert r_2005.applied_amendments == []

    r_2015 = reconstruct_provision_as_of(prov_am, amends, [], as_of="2015-01-01")
    assert r_2015.text == "Text 2010"
    assert len(r_2015.applied_amendments) == 1

    r_2022 = reconstruct_provision_as_of(prov_am, amends, [], as_of="2022-01-01")
    assert r_2022.text == "Text 2020"
    assert len(r_2022.applied_amendments) == 2


# ---------------------------------------------------------------------------
# 2. CauseListStore
# ---------------------------------------------------------------------------


def test_cause_list_store_sqlite_jsonl_and_html_parser(tmp_path: Path):
    from nyaya.storage.cause_list_store import CauseListStore, parse_cause_list_feed

    # SQLite cause_lists table
    db_path = tmp_path / "cause_lists.sqlite"
    con = sqlite3.connect(db_path)
    con.execute(
        """
        CREATE TABLE cause_lists (
            court TEXT NOT NULL,
            cause_list_date TEXT NOT NULL,
            bench TEXT,
            judge TEXT,
            item_number TEXT,
            case_number TEXT,
            cnr TEXT,
            parties TEXT,
            advocates TEXT,
            purpose TEXT,
            source_url TEXT
        )
        """
    )
    con.execute(
        "INSERT INTO cause_lists VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "SC",
            "2026-11-26",
            "Court No. 1",
            "CJI Sanjiv Khanna; Justice Sanjay Kumar",
            "1",
            "W.P.(C) 101/2026",
            "SCIN010001012026",
            "State of Maharashtra v. Union of India",
            json.dumps(["Adv. A. Singhvi", "SG Tushar Mehta"]),
            "Final Hearing",
            "https://www.sci.gov.in/daily-cause-list/2026-11-26",
        ),
    )
    con.commit()
    con.close()

    # JSONL feed
    jsonl_path = tmp_path / "hc_delhi.jsonl"
    jsonl_path.write_text(
        json.dumps(
            {
                "court": "DELHI_HC",
                "cause_list_date": "2026-11-26",
                "bench": "DB-II",
                "judge": "Justice Vibhu Bakhru",
                "item_number": "14",
                "case_number": "FAO(OS) 42/2026",
                "cnr": "DLHC010000422026",
                "parties": "Acme Tech Pvt Ltd v. Union of India",
                "advocates": ["Adv. R. Sharma"],
                "purpose": "Admission",
                "source_url": "https://delhihighcourt.nic.in/causelist/2026-11-26",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    store = CauseListStore(db_path=db_path, jsonl_paths=[jsonl_path])
    sc_hits = store.search_cause_lists(court="Supreme Court", date="2026-11-26", advocate="Singhvi")
    assert len(sc_hits) == 1
    hit = sc_hits[0]
    assert hit.court == "SC"
    assert hit.cause_list_date == "2026-11-26"
    assert hit.bench == "Court No. 1"
    assert "Sanjiv Khanna" in (hit.judge or "")
    assert hit.item_number == "1"
    assert hit.case_number == "W.P.(C) 101/2026"
    assert hit.cnr == "SCIN010001012026"
    assert "Maharashtra" in (hit.parties or "")
    assert "Adv. A. Singhvi" in hit.advocates
    assert hit.purpose == "Final Hearing"
    assert hit.source_url.startswith("https://www.sci.gov.in")

    cnr_hits = store.search_cause_lists(cnr="DLHC010000422026")
    assert len(cnr_hits) == 1
    assert cnr_hits[0].case_number == "FAO(OS) 42/2026"

    # Official sci.gov.in / HC HTML cause list table parser
    sample_html = """
    <html><body>
      <table>
        <tr>
          <th>Item No</th><th>Case Number</th><th>Parties</th><th>Advocates</th><th>Purpose</th>
        </tr>
        <tr>
          <td>5</td>
          <td>SLP(C) 555/2026</td>
          <td>Alpha Corp v. SEBI</td>
          <td>Adv. M. Datar; Adv. P. Rao</td>
          <td>Notice / Stay</td>
        </tr>
      </table>
    </body></html>
    """
    parsed = parse_cause_list_feed(
        sample_html,
        court="SC",
        cause_list_date="2026-11-26",
        bench="Court No. 3",
        judge="Justice B.R. Gavai",
        source_url="https://www.sci.gov.in/causelist/2026-11-26",
    )
    assert len(parsed) == 1
    assert parsed[0].item_number == "5"
    assert parsed[0].case_number == "SLP(C) 555/2026"
    assert parsed[0].parties == "Alpha Corp v. SEBI"
    assert parsed[0].advocates == ["Adv. M. Datar", "Adv. P. Rao"]
    assert parsed[0].purpose == "Notice / Stay"


# ---------------------------------------------------------------------------
# 3. LiveDocketAdapter & ByoLiveDocketAdapter
# ---------------------------------------------------------------------------


def test_byo_live_docket_adapter_hygiene_unconfigured_and_httpx():
    from nyaya.storage.live_docket import ByoLiveDocketAdapter, LiveDocketAdapter

    for banned in _BANNED_URI_SUBSTRINGS:
        with pytest.raises(ValueError):
            ByoLiveDocketAdapter(api_url=f"https://{banned}.example/api", api_key="secret")

    unconfigured = ByoLiveDocketAdapter(api_url=None, api_key=None)
    assert isinstance(unconfigured, LiveDocketAdapter)
    with pytest.raises(StorageUnavailable, match="Live docket adapter is not configured"):
        unconfigured.search_cause_lists(court="SC")

    captured: dict[str, object] = {}

    def _handler(request: httpx.Request) -> httpx.Response:
        captured["auth"] = request.headers.get("authorization")
        captured["params"] = dict(request.url.params)
        return httpx.Response(
            200,
            json={
                "entries": [
                    {
                        "court": "SC",
                        "cause_list_date": "2026-11-26",
                        "bench": "Court No. 2",
                        "judge": "Justice Surya Kant",
                        "item_number": "3",
                        "case_number": "C.A. 88/2026",
                        "cnr": "SCIN010000882026",
                        "parties": "Ramesh v. State",
                        "advocates": ["Adv. K. Viswanathan"],
                        "purpose": "Directions",
                        "source_url": "https://api.official-docket.gov.in/v1/causelist",
                    }
                ]
            },
        )

    adapter = ByoLiveDocketAdapter(
        api_url="https://api.official-docket.gov.in/v1/causelist",
        api_key="live-token-123",
        transport=httpx.MockTransport(_handler),
    )
    entries = adapter.search_cause_lists(court="SC", date="2026-11-26")
    assert captured["auth"] == "Bearer live-token-123"
    assert len(entries) == 1
    assert entries[0].case_number == "C.A. 88/2026"


# ---------------------------------------------------------------------------
# 4. get_provisions_batch
# ---------------------------------------------------------------------------


def test_get_provisions_batch_caps_partial_success_and_framing():
    from nyaya.tools.get_provisions_batch import get_provisions_batch, register

    backend = InMemoryCorpusBackend.from_sample_data()
    # Add a non-commercial provision and an amendment
    backend.aliases["ncact"] = "NCAct"
    backend.provisions["nc-act:1"] = ProvisionRecord(
        doc_id="nc-act:1",
        act_id="nc-act",
        act="NCAct",
        act_full_name="Non-Commercial Act",
        ref="1",
        ref_norm="1",
        title="NC Section 1",
        text="Non-commercial restricted text.",
        validity=StatutoryValidity(status="in_force"),
        provenance=_prov_nc(),
    )
    backend.amendments.append(
        AmendmentRecord(
            amendment_id="ipc-am-1",
            number=1,
            title="Criminal Law Amendment 2013",
            act="IPC",
            year=2013,
            enactment_date=Date(2013, 2, 3),
            effective_date=Date(2013, 4, 2),
            action="amended",
            provisions_affected=["302"],
            summary="Amended IPC 302 note.",
            provenance=_prov_safe(),
        )
    )
    # Attach a link on IPC 302 to test link context_note framing
    backend.provisions["ipc-1860:302"].top_links = [
        StatutoryLink(
            from_ref=ProvisionRef(act="IPC", ref="302"),
            to_ref=ProvisionRef(act="BNS", ref="103"),
            relation_kind="replaced_by",
            context_note="Corresponding BNS provision.",
            provenance=_prov_safe(),
        )
    ]
    storage.set_backend(backend)

    # Empty & >50 checks
    with pytest.raises(InvalidFilter):
        get_provisions_batch(citations=[])
    with pytest.raises(InvalidFilter):
        get_provisions_batch(citations=[f"IPC s.{i}" for i in range(51)])

    resp = get_provisions_batch(
        citations=["IPC s.302", "IPC s.9999", "NCAct s.1"],
        requests=[{"act": "BNS", "ref": "103"}],
        as_of="2023-01-01",
        commercial_safe_only=True,
    )
    assert resp.total_requested == 4
    assert resp.returned == 2
    assert "IPC s.9999" in resp.not_found
    assert len(resp.errors) == 1
    assert resp.errors[0]["code"] == "commercial_license_blocked"

    for item in resp.results:
        assert item["text"].startswith("<corpus_text ")
        assert item["text"].rstrip().endswith("</corpus_text>")
        for link in item.get("top_links", []):
            if link.get("context_note"):
                assert link["context_note"].startswith("<corpus_text ")

    # Tool registration check
    mcp = FastMCP("test-p4")
    register(mcp)
    import asyncio

    tools = asyncio.run(mcp.list_tools())
    t = next(x for x in tools if x.name == "get_provisions_batch")
    assert t.annotations is not None
    assert t.annotations.readOnlyHint is True


# ---------------------------------------------------------------------------
# 5. get_judgments_batch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_judgments_batch_caps_ban_ambiguity_and_framing():
    from nyaya.tools.get_judgments_batch import get_judgments_batch, register

    backend = InMemoryCorpusBackend.from_sample_data()
    backend.judgments["sc:2017:puttaswamy"] = JudgmentMeta(
        case_id="sc:2017:puttaswamy",
        court_tier="supreme_court",
        court_code="SC",
        court_name="Supreme Court of India",
        neutral_citation="2017 INSC 801",
        title="Justice K.S. Puttaswamy v. Union of India",
        decision_date=Date(2017, 8, 24),
        year=2017,
        provenance=_prov_safe(),
        snippet="Right to privacy is protected as an intrinsic part of Article 21.",
    )
    # Add an ambiguous pair with matching title substring
    for idx in (1, 2):
        backend.judgments[f"sc:2020:dup{idx}"] = JudgmentMeta(
            case_id=f"sc:2020:dup{idx}",
            court_tier="supreme_court",
            court_code="SC",
            court_name="Supreme Court of India",
            neutral_citation=f"2020 INSC {100 + idx}",
            title=f"Common Party Name v. State ({idx})",
            decision_date=Date(2020, 5, idx),
            year=2020,
            provenance=_prov_safe(),
            snippet=f"Opening paragraph {idx}.",
        )
    # Add a publication-banned judgment
    backend.judgments["sc:2021:banned"] = JudgmentMeta(
        case_id="sc:2021:banned",
        court_tier="supreme_court",
        court_code="SC",
        court_name="Supreme Court of India",
        neutral_citation="2021 INSC 999",
        title="Sealed Matter v. Union",
        decision_date=Date(2021, 6, 1),
        year=2021,
        provenance=_prov_safe(),
        snippet="This matter is ordered to be kept in a sealed cover.",
        docket_extra={"sealed": True},
    )
    storage.set_backend(backend)

    with pytest.raises(InvalidFilter):
        await get_judgments_batch(identifiers=[])
    with pytest.raises(InvalidFilter):
        await get_judgments_batch(identifiers=[f"2020 INSC {i}" for i in range(21)])

    sample_nc = "2017 INSC 801"

    resp = await get_judgments_batch(
        identifiers=[sample_nc, "1999 INSC 404", "Common Party Name", "2021 INSC 999"],
        view="overview",
    )
    assert resp.total_requested == 4
    assert resp.returned == 1
    assert "1999 INSC 404" in resp.not_found
    assert len(resp.ambiguous) == 1
    assert resp.ambiguous[0]["identifier"] == "Common Party Name"
    assert len(resp.errors) == 1
    assert resp.errors[0]["code"] == "text_unavailable_metadata_only"

    res_item = resp.results[0]
    for ex in res_item.excerpts:
        assert ex.text.startswith("<corpus_text ")
        assert ex.text.rstrip().endswith("</corpus_text>")

    mcp = FastMCP("test-p4-j")
    register(mcp)
    tools = await mcp.list_tools()
    t = next(x for x in tools if x.name == "get_judgments_batch")
    assert t.annotations is not None
    assert t.annotations.readOnlyHint is True


# ---------------------------------------------------------------------------
# 6. get_cause_list tool
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_cause_list_tool_local_live_and_structured_error(tmp_path: Path):
    from nyaya.storage.cause_list_store import CauseListStore
    from nyaya.storage.live_docket import ByoLiveDocketAdapter, set_live_docket_adapter
    from nyaya.tools.get_cause_list import get_cause_list, register, set_cause_list_store

    jsonl_path = tmp_path / "cl.jsonl"
    jsonl_path.write_text(
        json.dumps(
            {
                "court": "SC",
                "cause_list_date": "2026-11-26",
                "bench": "Court No. 4",
                "judge": "Justice A.S. Oka",
                "item_number": "7",
                "case_number": "W.P.(Crl.) 12/2026",
                "cnr": "SCIN010000122026",
                "parties": "Citizen Forum v. State",
                "advocates": ["Adv. Vrinda Grover"],
                "purpose": "Arguments",
                "source_url": "https://www.sci.gov.in/causelist",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    set_cause_list_store(CauseListStore(jsonl_paths=[jsonl_path]))
    set_live_docket_adapter(ByoLiveDocketAdapter(api_url=None, api_key=None))

    try:
        local_resp = get_cause_list(court="SC", date="2026-11-26")
        assert local_resp.returned == 1
        assert local_resp.source == "local"
        entry = local_resp.entries[0]
        assert (entry.parties or "").startswith("<corpus_text ")
        assert (entry.purpose or "").startswith("<corpus_text ")
        assert entry.advocates[0].startswith("<corpus_text ")

        mcp = FastMCP("test-cl")
        register(mcp)
        tools = await mcp.list_tools()
        t = next(x for x in tools if x.name == "get_cause_list")
        assert t.annotations is not None
        assert t.annotations.readOnlyHint is True

        # Unconfigured live=True via MCP tool returns structured error via @structured_errors
        err_result = await mcp.call_tool("get_cause_list", {"court": "SC", "live": True})
        assert err_result.is_error is True
        assert err_result.structured_content is not None
        assert err_result.structured_content["error"]["code"] == "storage_unavailable"

        # Configured live=True works and frames text
        def _handler(_req: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=[
                    {
                        "court": "SC",
                        "cause_list_date": "2026-11-26",
                        "bench": "Court No. 1",
                        "judge": "CJI",
                        "item_number": "2",
                        "case_number": "SLP(C) 99/2026",
                        "parties": "Live Party v. Respondent",
                        "advocates": ["Adv. Live"],
                        "purpose": "Mentioning",
                        "source_url": "https://api.official-docket.gov.in/cl",
                    }
                ],
            )

        set_live_docket_adapter(
            ByoLiveDocketAdapter(
                api_url="https://api.official-docket.gov.in/cl",
                api_key="tok",
                transport=httpx.MockTransport(_handler),
            )
        )
        live_resp = get_cause_list(court="SC", live=True)
        assert live_resp.source == "live"
        assert live_resp.returned == 1
        assert (live_resp.entries[0].parties or "").startswith("<corpus_text ")
    finally:
        set_cause_list_store(None)
        set_live_docket_adapter(None)


# ---------------------------------------------------------------------------
# 7. cli.sync_packs
# ---------------------------------------------------------------------------


def _make_settings(tmp_path: Path, *, manifest_url: str | None = None, commercial_safe_only: bool = False) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        bucket_endpoint=None,
        bucket_name=None,
        bucket_access_key_id=None,
        bucket_secret_access_key=None,
        bucket_region="auto",
        bucket_url_style="vhost",
        manifest_url=manifest_url,
        commercial_safe_only=commercial_safe_only,
        offline_only=False,
        duckdb_ext_dir=None,
        log_level="WARNING",
        port=8000,
        redis_url=None,
        trusted_proxy_hops=1,
    )


def test_cli_sync_packs_all_and_sha256_and_traversal_guards(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    sqlite_raw = b"SQLite format 3\x00dummy-core-bytes"
    sqlite_zst = zstd.ZstdCompressor().compress(sqlite_raw)
    sqlite_zst_sha = hashlib.sha256(sqlite_zst).hexdigest()

    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, "w") as zf:
        zf.writestr("judgments.lance/data.bin", b"lance-index-bytes")
    lance_zip = zip_buf.getvalue()
    lance_zip_sha = hashlib.sha256(lance_zip).hexdigest()

    bad_zip_buf = io.BytesIO()
    with zipfile.ZipFile(bad_zip_buf, "w") as zf:
        zf.writestr("../escape.txt", b"evil")
    bad_lance_zip = bad_zip_buf.getvalue()

    bad_tar_buf = io.BytesIO()
    with tarfile.open(fileobj=bad_tar_buf, mode="w") as tf:
        info = tarfile.TarInfo(name="/etc/passwd")
        info.size = 4
        tf.addfile(info, io.BytesIO(b"evil"))
    bad_lance_tar_zst = zstd.ZstdCompressor().compress(bad_tar_buf.getvalue())

    good_tar_buf = io.BytesIO()
    with tarfile.open(fileobj=good_tar_buf, mode="w") as tf:
        data_bytes = b"tar-zst-lance-bytes"
        info = tarfile.TarInfo(name="statutes.lance/data.bin")
        info.size = len(data_bytes)
        tf.addfile(info, io.BytesIO(data_bytes))
    good_lance_tar_zst = zstd.ZstdCompressor().compress(good_tar_buf.getvalue())

    pq_safe = b"PAR1safe"
    pq_nc = b"PAR1nc"

    parquet_manifest_obj = {
        "schema_version": "2.0",
        "generated_at": "2026-11-26T00:00:00Z",
        "datasets": {
            "supreme_court": {
                "license_spdx": "CC-BY-4.0",
                "commercial_safe": True,
                "terms_status": "cleared",
                "row_count": 10,
                "partitions": [
                    {
                        "court_code": "SC",
                        "rows": 10,
                        "s3_key": "parquet/sc/2026.parquet",
                        "public_url": "https://mirror.example.org/parquet/sc/2026.parquet",
                        "bytes": len(pq_safe),
                        "sha256": hashlib.sha256(pq_safe).hexdigest(),
                    }
                ],
            },
            "district_court": {
                "license_spdx": "CC-BY-NC-4.0",
                "commercial_safe": False,
                "terms_status": "non_commercial_only",
                "row_count": 10,
                "partitions": [
                    {
                        "state_code": "DL",
                        "rows": 10,
                        "s3_key": "parquet/dc/dl.parquet",
                        "public_url": "https://mirror.example.org/parquet/dc/dl.parquet",
                        "bytes": len(pq_nc),
                        "sha256": hashlib.sha256(pq_nc).hexdigest(),
                    }
                ],
            },
        },
    }

    payloads: dict[str, bytes] = {
        "https://mirror.example.org/release.json": json.dumps(
            {
                "core_sqlite": {
                    "url": "https://mirror.example.org/nyaya-core.sqlite.zst",
                    "sha256": sqlite_zst_sha,
                },
                "lance_fts": {
                    "url": "https://mirror.example.org/lancedb.zip",
                    "sha256": lance_zip_sha,
                },
                "parquet_manifest": {
                    "url": "https://mirror.example.org/manifest.json",
                },
            }
        ).encode("utf-8"),
        "https://mirror.example.org/nyaya-core.sqlite.zst": sqlite_zst,
        "https://mirror.example.org/lancedb.zip": lance_zip,
        "https://mirror.example.org/manifest.json": json.dumps(parquet_manifest_obj).encode("utf-8"),
        "https://mirror.example.org/parquet/sc/2026.parquet": pq_safe,
        "https://mirror.example.org/parquet/dc/dl.parquet": pq_nc,
        "https://mirror.example.org/bad-sha-manifest.json": json.dumps(
            {
                "core_sqlite": {
                    "url": "https://mirror.example.org/nyaya-core.sqlite.zst",
                    "sha256": "0" * 64,
                }
            }
        ).encode("utf-8"),
        "https://mirror.example.org/bad-zip-manifest.json": json.dumps(
            {
                "lance_fts": {
                    "url": "https://mirror.example.org/bad.zip",
                    "sha256": hashlib.sha256(bad_lance_zip).hexdigest(),
                }
            }
        ).encode("utf-8"),
        "https://mirror.example.org/bad.zip": bad_lance_zip,
        "https://mirror.example.org/bad-tar-manifest.json": json.dumps(
            {
                "lance_fts": {
                    "url": "https://mirror.example.org/bad.tar.zst",
                    "sha256": hashlib.sha256(bad_lance_tar_zst).hexdigest(),
                }
            }
        ).encode("utf-8"),
        "https://mirror.example.org/bad.tar.zst": bad_lance_tar_zst,
        "https://mirror.example.org/good-tar-manifest.json": json.dumps(
            {
                "lance_fts": {
                    "url": "https://mirror.example.org/good.tar.zst",
                    "sha256": hashlib.sha256(good_lance_tar_zst).hexdigest(),
                }
            }
        ).encode("utf-8"),
        "https://mirror.example.org/good.tar.zst": good_lance_tar_zst,
    }

    def _mock_handler(request: httpx.Request) -> httpx.Response:
        body = payloads.get(str(request.url))
        if body is None:
            return httpx.Response(404)
        return httpx.Response(200, content=body)

    transport = httpx.MockTransport(_mock_handler)
    real_client = httpx.Client

    def _patched_client(*args, **kwargs):
        kwargs["transport"] = transport
        return real_client(*args, **kwargs)

    monkeypatch.setattr(cli.httpx, "Client", _patched_client)

    s1 = _make_settings(tmp_path / "run1", manifest_url="https://mirror.example.org/release.json")
    synced = cli.sync_packs(s1, pack="all", commercial_safe_only=True)
    assert (s1.data_dir / "nyaya-core.sqlite").read_bytes() == sqlite_raw
    assert (s1.data_dir / "lancedb" / "judgments.lance" / "data.bin").read_bytes() == b"lance-index-bytes"
    assert (s1.data_dir / "parquet" / "sc" / "2026.parquet").read_bytes() == pq_safe
    assert not (s1.data_dir / "parquet" / "dc" / "dl.parquet").exists()
    assert "core" in synced and "lance" in synced

    # Bad SHA-256 must fail
    s_bad_sha = _make_settings(tmp_path / "bad_sha", manifest_url="https://mirror.example.org/bad-sha-manifest.json")
    with pytest.raises(RuntimeError, match="SHA-256"):
        cli.sync_packs(s_bad_sha, pack="core")

    # Path traversal in zip and tar.zst must fail
    s_bad_zip = _make_settings(tmp_path / "bad_zip", manifest_url="https://mirror.example.org/bad-zip-manifest.json")
    with pytest.raises(ValueError, match="Unsafe"):
        cli.sync_packs(s_bad_zip, pack="lance")

    s_bad_tar = _make_settings(tmp_path / "bad_tar", manifest_url="https://mirror.example.org/bad-tar-manifest.json")
    with pytest.raises(ValueError, match="Unsafe"):
        cli.sync_packs(s_bad_tar, pack="lance")

    # Valid .tar.zst extraction + CLI main wiring
    s_good_tar = _make_settings(tmp_path / "good_tar", manifest_url="https://mirror.example.org/good-tar-manifest.json")
    monkeypatch.setenv("NYAYA_MANIFEST_URL", "https://mirror.example.org/good-tar-manifest.json")
    rc = cli.main(["sync", "--pack", "lance", "--data-dir", str(s_good_tar.data_dir)])
    assert rc == 0
    assert (s_good_tar.data_dir / "lancedb" / "statutes.lance" / "data.bin").read_bytes() == b"tar-zst-lance-bytes"


# ---------------------------------------------------------------------------
# 8. server.json & release-pypi.yml
# ---------------------------------------------------------------------------


def test_server_json_and_release_workflow():
    repo_root = Path(__file__).resolve().parents[2]
    server_json_path = repo_root / "server.json"
    workflow_path = repo_root / ".github" / "workflows" / "release-pypi.yml"

    assert server_json_path.is_file()
    data = json.loads(server_json_path.read_text(encoding="utf-8"))
    assert data["$schema"].startswith("https://static.modelcontextprotocol.io/schemas/")
    assert data["name"] == "io.github.paragmraw/nyaya-mcp"
    assert data["version"] in {"2.0.0", "2.1.0"}
    assert len(data["description"]) <= 100
    assert any(
        p.get("registryType") == "pypi"
        and p.get("identifier") == "nyaya-mcp"
        and p.get("transport", {}).get("type") == "stdio"
        for p in data.get("packages", [])
    )
    assert any(
        r.get("type") == "streamable-http"
        and r.get("url") == "https://nyaya.parag.tech/mcp"
        for r in data.get("remotes", [])
    )

    assert workflow_path.is_file()
    wf_raw = workflow_path.read_text(encoding="utf-8")
    wf = yaml.safe_load(wf_raw)
    assert "v*" in str(wf.get("on") or wf.get(True))
    assert "pypa/gh-action-pypi-publish" in wf_raw
    assert "uv build" in wf_raw
    assert "mcp-publisher" in wf_raw
