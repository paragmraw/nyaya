"""TDD tests for Pillar 1 Ingestion:
- OfficialStatutesFetcher (5 official sources: indiacode_central_acts, prs_2026_delta,
  legislative_constitution, cad_debates, escr_judgments) with SafeHttpFetcher + local staged loaders
- ResearchPacksFetcher (ddl_81m, ildc, justicehub_khoj) with demographic stripping & provenance
- Staged loader wiring for all Pillar 1 sources + hc_mobile_rows + graceful empty defaults
- CLI `sync` wiring for all sources.lock.yaml entries (--tier, --sources, --commercial-safe-only,
  --dry-run, --fixture-mode)
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import httpx
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from nyaya.ingest.builders.lance_fts import build_lance_fts_tables
from nyaya.ingest.builders.parquet_compact import build_compacted_parquets
from nyaya.ingest.builders.sqlite_core import build_sqlite_core
from nyaya.ingest.cli import main as ingest_cli_main
from nyaya.ingest.fetchers.http import SafeHttpFetcher, SecurityError
from nyaya.ingest.fetchers.official_statutes import (
    OFFICIAL_SOURCE_IDS,
    OfficialStatutesFetcher,
)
from nyaya.ingest.fetchers.research_packs import (
    DDL_BANNED_DEMOGRAPHIC_COLUMNS,
    ResearchPacksFetcher,
)
from nyaya.ingest.gate import run_quality_gate
from nyaya.ingest.loader import load_staged_ingest_bundle
from nyaya.ingest.lockfile import load_and_validate_lockfile
from nyaya.ingest.state import IngestState
from nyaya.storage.lance_store import LanceStore
from nyaya.storage.sqlite_store import SqliteStore
from tests.fixtures.mini_corpus.builder import make_minimal_pdf


def test_official_statutes_fetcher_remote_allowlist_and_sha256(tmp_path: Path) -> None:
    lock = load_and_validate_lockfile()
    payloads_by_host_path: dict[tuple[str, str], bytes] = {
        ("indiacode.nic.in", "/handle/123456789/1362"): json.dumps(
            [{"act_id": "rti-2005", "short_name": "RTIAct", "full_name": "Right to Information Act, 2005", "ref": "6", "title": "Request for obtaining information", "text": "A person who desires to obtain any information under this Act shall make a request in writing."}]
        ).encode("utf-8"),
        ("prsindia.org", "/files/bills_acts/acts_parliament/2026/prs_2026_delta.json"): json.dumps(
            [{"act_id": "bns-2023", "short_name": "BNS", "full_name": "Bharatiya Nyaya Sanhita, 2023", "ref": "106", "title": "Causing death by negligence", "text": "Whoever causes death of any person by doing any rash or negligent act not amounting to culpable homicide."}]
        ).encode("utf-8"),
        ("legislative.gov.in", "/sites/default/files/COI_2026.pdf"): make_minimal_pdf(
            ["Article 300A. Persons not to be deprived of property save by authority of law. No person shall be deprived of his property save by authority of law."]
        ),
        ("eparlib.sansad.in", "/bitstream/123456789/cad_debates_v1_12.json"): json.dumps(
            [{"volume": "VII", "date": "1948-11-04", "speaker": "Dr. B.R. Ambedkar", "title": "Draft Constitution Introduction", "text": "The Draft Constitution is a formidable document."}]
        ).encode("utf-8"),
        ("digiscr.sci.gov.in", "/api/v1/judgments"): json.dumps(
            [{"case_id": "sc:2024:sample", "neutral_citation": "2024 INSC 101", "scr_citation": "[2024] 1 S.C.R. 50", "title": "Sample Petitioner v. Union of India", "headnote": "Official e-SCR headnote explaining constitutional validity."}]
        ).encode("utf-8"),
    }

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/redirect-evil":
            return httpx.Response(302, headers={"Location": "https://indiankanoon.org/doc/1"})
        key = (req.url.host, req.url.path)
        if key in payloads_by_host_path:
            return httpx.Response(200, content=payloads_by_host_path[key])
        return httpx.Response(404)

    http_fetcher = SafeHttpFetcher(
        allowed_domains=lock.allowed_domains,
        transport=httpx.MockTransport(handler),
    )
    state = IngestState(tmp_path / "state.sqlite")
    try:
        fetcher = OfficialStatutesFetcher(http_fetcher=http_fetcher, lockfile=lock, state=state)
        assert set(OFFICIAL_SOURCE_IDS) == {
            "indiacode_central_acts",
            "prs_2026_delta",
            "legislative_constitution",
            "cad_debates",
            "escr_judgments",
        }

        for sid in OFFICIAL_SOURCE_IDS:
            entry = lock.get_source(sid)
            from urllib.parse import urlsplit

            parsed = urlsplit(entry.uri)
            raw_bytes = payloads_by_host_path[(parsed.hostname or "", parsed.path)]
            sha = hashlib.sha256(raw_bytes).hexdigest()

            res = fetcher.fetch_source(sid, work_dir=tmp_path, expected_sha256=sha)
            assert res.source_id == sid
            assert res.sha256 == sha
            assert res.dest_path is not None and res.dest_path.is_file()
            assert res.dest_path.read_bytes() == raw_bytes

        # SHA-256 mismatch is rejected via SafeHttpFetcher
        with pytest.raises(SecurityError, match="SHA-256"):
            fetcher.fetch_source(
                "prs_2026_delta",
                work_dir=tmp_path,
                expected_sha256="0" * 64,
            )

        # Cross-domain redirect / non-allowlisted host is rejected
        with pytest.raises(SecurityError, match="Disallowed|Cross-domain"):
            fetcher.fetch_source(
                "prs_2026_delta",
                work_dir=tmp_path,
                url="https://prsindia.org/redirect-evil",
                verify_sha256=False,
            )
    finally:
        state.close()


def test_official_statutes_fetcher_staged_json_pdf_parquet_loaders(tmp_path: Path) -> None:
    fetcher = OfficialStatutesFetcher()

    # 1. indiacode_central_acts (JSON + Parquet)
    ic_dir = tmp_path / "indiacode_central_acts"
    ic_dir.mkdir()
    (ic_dir / "acts.json").write_text(
        json.dumps(
            {
                "acts": [
                    {
                        "act_id": "rti-2005",
                        "short_name": "RTIAct",
                        "full_name": "The Right to Information Act, 2005",
                        "year": 2005,
                        "act_number": "Act No. 22 of 2005",
                        "provisions": [
                            {
                                "ref": "6",
                                "title": "Request for obtaining information",
                                "text": "A person, who desires to obtain any information under this Act, shall make a request in writing.",
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "act_id": "rti-2005",
                    "act_short": "RTIAct",
                    "act_full_name": "The Right to Information Act, 2005",
                    "year": 2005,
                    "ref": "8",
                    "title": "Exemption from disclosure of information",
                    "text": "Notwithstanding anything contained in this Act, there shall be no obligation to give any citizen certain exempted information.",
                }
            ]
        ),
        ic_dir / "rti_extra.parquet",
    )
    ic_rows = fetcher.load_indiacode_central_acts(ic_dir)
    assert len(ic_rows) == 2
    assert {r["ref"] for r in ic_rows} == {"6", "8"}
    assert all(r["source_id"] == "indiacode_central_acts" for r in ic_rows)

    # 2. prs_2026_delta (JSON)
    prs_dir = tmp_path / "prs_2026_delta"
    prs_dir.mkdir()
    (prs_dir / "prs_2026_delta.json").write_text(
        json.dumps(
            [
                {
                    "act_id": "bns-2023",
                    "act_short": "BNS",
                    "act_full_name": "The Bharatiya Nyaya Sanhita, 2023",
                    "year": 2023,
                    "ref": "106",
                    "title": "Causing death by negligence",
                    "text": "Whoever causes death of any person by doing any rash or negligent act not amounting to culpable homicide shall be punished.",
                }
            ]
        ),
        encoding="utf-8",
    )
    prs_rows = fetcher.load_prs_2026_delta(prs_dir)
    assert len(prs_rows) == 1
    assert prs_rows[0]["source_id"] == "prs_2026_delta"
    assert prs_rows[0]["ref"] == "106"

    # 3. legislative_constitution (JSON + PDF, always tagging articles kind="article")
    const_dir = tmp_path / "legislative_constitution"
    const_dir.mkdir()
    (const_dir / "articles.json").write_text(
        json.dumps(
            [
                {
                    "ref": "300A",
                    "kind": "section",  # deliberately wrong raw kind -> must normalize to "article"
                    "part": "Part XII",
                    "title": "Persons not to be deprived of property save by authority of law",
                    "text": "No person shall be deprived of his property save by authority of law.",
                }
            ]
        ),
        encoding="utf-8",
    )
    pdf_bytes = make_minimal_pdf(
        [
            "Article 356. Provisions in case of failure of constitutional machinery in States.\n"
            "If the President, on receipt of a report from the Governor of a State, is satisfied that a situation has arisen in which the government of the State cannot be carried on."
        ]
    )
    (const_dir / "COI_2026.pdf").write_bytes(pdf_bytes)
    const_rows = fetcher.load_legislative_constitution(const_dir)
    assert len(const_rows) >= 2
    assert all(r["kind"] == "article" for r in const_rows)
    assert all(r["act_id"] == "constitution" for r in const_rows)
    assert all(r["source_id"] == "legislative_constitution" for r in const_rows)
    assert any(r["ref"] == "300A" for r in const_rows)
    assert any(r["ref"] == "356" for r in const_rows)

    # 4. cad_debates (JSON)
    cad_dir = tmp_path / "cad_debates"
    cad_dir.mkdir()
    (cad_dir / "cad_debates_v1_12.json").write_text(
        json.dumps(
            [
                {
                    "volume": "VII",
                    "ref": "1948-11-04-01",
                    "date": "1948-11-04",
                    "speaker": "Dr. B.R. Ambedkar",
                    "title": "Motion on Draft Constitution",
                    "text": "Draft Constitution introduces fundamental rights enforceable by writ jurisdiction.",
                }
            ]
        ),
        encoding="utf-8",
    )
    cad_rows = fetcher.load_cad_debates(cad_dir)
    assert len(cad_rows) == 1
    assert cad_rows[0]["category"] == "cad_debate"
    assert cad_rows[0]["jurisdiction"] == "Central"
    assert cad_rows[0]["source_id"] == "cad_debates"
    assert "Ambedkar" in cad_rows[0]["text"]

    # 5. escr_judgments (JSON)
    escr_dir = tmp_path / "escr_judgments"
    escr_dir.mkdir()
    (escr_dir / "escr.json").write_text(
        json.dumps(
            [
                {
                    "cnr": "ESCR010003021973",
                    "neutral_citation": "1973 INSC 91",
                    "scr_citation": "[1973] Supp SCR 1",
                    "title": "Kesavananda Bharati v. State of Kerala",
                    "headnote": "e-SCR Official Headnote: Article 368 does not empower Parliament to destroy the basic structure.",
                }
            ]
        ),
        encoding="utf-8",
    )
    escr_rows = fetcher.load_escr_judgments(escr_dir)
    assert len(escr_rows) == 1
    assert escr_rows[0]["neutral_citation"] == "1973 INSC 91"
    assert "e-SCR Official Headnote" in escr_rows[0]["text"]
    assert escr_rows[0]["source_id"] == "escr_judgments"


def test_research_packs_fetcher_sanitizes_ddl_tags_ildc_and_normalizes_khoj(tmp_path: Path) -> None:
    fetcher = ResearchPacksFetcher()

    # 1. DDL 81M: strip all 8 banned demographic columns & force NC-SA provenance
    ddl_dir = tmp_path / "ddl_81m"
    ddl_dir.mkdir()
    ddl_raw = {
        "case_id": "ddl:up:2017:99",
        "cnr": "UPAD010000992017",
        "state_code": "UP",
        "court_code": "UP_DIST_01",
        "court_name": "District Court, Lucknow",
        "district": "Lucknow",
        "title": "State of UP v. Accused X",
        "decision_date": "2017-06-15",
        "year": 2017,
        "disposal_nature": "Convicted",
        "case_type": "Criminal",
        "act": "IPC",
    }
    for col in DDL_BANNED_DEMOGRAPHIC_COLUMNS:
        ddl_raw[col] = "SENSITIVE_ML_INFERENCE"
    pq.write_table(pa.Table.from_pylist([ddl_raw]), ddl_dir / "ddl_up.parquet")

    ddl_rows = fetcher.load_ddl_district_rows(ddl_dir)
    assert len(ddl_rows) == 1
    for col in DDL_BANNED_DEMOGRAPHIC_COLUMNS:
        assert col not in ddl_rows[0]
    assert ddl_rows[0]["commercial_safe"] is False
    assert ddl_rows[0]["license_spdx"] == "CC-BY-NC-SA-4.0"
    assert ddl_rows[0]["terms_status"] == "non_commercial_only"
    assert ddl_rows[0]["source_id"] == "ddl_81m"

    # 2. ILDC: force commercial_safe=False & CC-BY-NC-4.0
    ildc_dir = tmp_path / "ildc"
    ildc_dir.mkdir()
    (ildc_dir / "ildc.json").write_text(
        json.dumps(
            [
                {
                    "case_id": "ildc:2019:42",
                    "title": "Ramesh Kumar v. State",
                    "year": 2019,
                    "decision_date": "2019-05-01",
                    "text": "1. Appeal against conviction under Section 302 IPC where circumstantial evidence was examined.",
                    "commercial_safe": True,  # must be forced False
                }
            ]
        ),
        encoding="utf-8",
    )
    ildc_rows = fetcher.load_ildc_rows(ildc_dir)
    assert len(ildc_rows) == 1
    assert ildc_rows[0]["commercial_safe"] is False
    assert ildc_rows[0]["license_spdx"] == "CC-BY-NC-4.0"
    assert ildc_rows[0]["terms_status"] == "non_commercial_only"
    assert ildc_rows[0]["source_id"] == "ildc"

    # 3. JusticeHub KHOJ: CC-BY-4.0, commercial_safe=True, normalized HC judge metadata
    khoj_dir = tmp_path / "justicehub_khoj"
    khoj_dir.mkdir()
    (khoj_dir / "khoj_hc_judges.json").write_text(
        json.dumps(
            [
                {
                    "name": "Hon'ble Justice Vibhu Bakhru",
                    "court_code": "7_26",
                    "court_name": "High Court of Delhi",
                    "designation": "Judge",
                    "date_of_appointment": "2013-04-17",
                    "date_of_retirement": "2028-11-01",
                    "gender": "Male",
                    "source_of_recruitment": "Bar",
                }
            ]
        ),
        encoding="utf-8",
    )
    khoj_rows = fetcher.load_justicehub_khoj(khoj_dir)
    assert len(khoj_rows) == 1
    assert khoj_rows[0]["judge_name"] == "Vibhu Bakhru"
    assert khoj_rows[0]["commercial_safe"] is True
    assert khoj_rows[0]["license_spdx"] == "CC-BY-4.0"
    assert khoj_rows[0]["source_id"] == "justicehub_khoj"
    assert khoj_rows[0]["appointment_date"] == "2013-04-17"


def test_loader_wires_all_pillar1_sources_and_passes_gate(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    raw_dir = work_dir / "raw"

    # A. Dattam HC web + case_details-mobile parquet
    hc_web_dir = raw_dir / "indian-high-court-judgments/metadata/parquet/year=2024/court=7_26/bench=principal"
    hc_web_dir.mkdir(parents=True)
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "court_code": "7~26",
                    "title": "Arjun Mehta v. State NCT of Delhi",
                    "description": "Anticipatory bail granted under Section 482 BNSS.",
                    "judge": "HON'BLE JUSTICE VIBHU BAKHRU",
                    "pdf_link": "orders/DLHC010123452024.pdf",
                    "cnr": "DLHC010123452024",
                    "decision_date": datetime(2024, 5, 14),
                    "disposal_nature": "",
                    "court": "7_26",
                    "pdf_exists": True,
                    "bench": "principal",
                    "year": 2024,
                }
            ]
        ),
        hc_web_dir / "metadata.parquet",
    )

    hc_mobile_dir = raw_dir / "indian-high-court-judgments/case_details-mobile/year=2024"
    hc_mobile_dir.mkdir(parents=True)
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "cnr": "DLHC010123452024",
                    "filing_date": "2024-01-10",
                    "registration_date": "2024-01-12",
                    "disposal_nature": "Allowed",
                    "petitioner_advocates": ["Adv. R.K. Sharma"],
                    "respondent_advocates": ["APP for State"],
                    "acts_sections": ["BNSS s.482"],
                    "hearing_count": 5,
                }
            ]
        ),
        hc_mobile_dir / "mobile.parquet",
    )

    # B. JusticeHub KHOJ enrichment
    khoj_dir = raw_dir / "justicehub_khoj"
    khoj_dir.mkdir(parents=True)
    (khoj_dir / "khoj.json").write_text(
        json.dumps(
            [
                {
                    "judge_name": "Hon'ble Justice Vibhu Bakhru",
                    "court_code": "7_26",
                    "designation": "Judge",
                    "appointment_date": "2013-04-17",
                    "retirement_date": "2028-11-01",
                    "gender": "Male",
                }
            ]
        ),
        encoding="utf-8",
    )

    # C. e-SCR enrichment over SC judgments
    escr_dir = raw_dir / "escr_judgments"
    escr_dir.mkdir(parents=True)
    (escr_dir / "escr.json").write_text(
        json.dumps(
            [
                {
                    "cnr": "ESCR010000342017",
                    "neutral_citation": "2017 INSC 801",
                    "scr_citation": "[2017] 10 S.C.R. 569",
                    "title": "Justice K.S. Puttaswamy (Retd.) v. Union of India",
                    "headnote": "e-SCR Official Headnote: Informational privacy is a facet of Article 21.",
                }
            ]
        ),
        encoding="utf-8",
    )

    # D. IndiaCode Central Acts + PRS 2026 Delta + Legislative Constitution + CAD Debates
    (raw_dir / "indiacode_central_acts").mkdir(parents=True)
    (raw_dir / "indiacode_central_acts/acts.json").write_text(
        json.dumps(
            [
                {
                    "act_id": "rti-2005",
                    "act_short": "RTIAct",
                    "act_full_name": "The Right to Information Act, 2005",
                    "year": 2005,
                    "ref": "6",
                    "title": "Request for obtaining information",
                    "text": "A person who desires to obtain any information under this Act shall make a request in writing.",
                }
            ]
        ),
        encoding="utf-8",
    )
    (raw_dir / "prs_2026_delta").mkdir(parents=True)
    (raw_dir / "prs_2026_delta/delta.json").write_text(
        json.dumps(
            [
                {
                    "act_id": "dpdp-2023",
                    "act_short": "DPDPAct",
                    "act_full_name": "The Digital Personal Data Protection Act, 2023",
                    "year": 2023,
                    "ref": "4",
                    "title": "Grounds for processing personal data",
                    "text": "A person may process the personal data of a Data Principal only in accordance with the provisions of this Act.",
                }
            ]
        ),
        encoding="utf-8",
    )
    (raw_dir / "legislative_constitution").mkdir(parents=True)
    (raw_dir / "legislative_constitution/const.json").write_text(
        json.dumps(
            [
                {
                    "ref": "300A",
                    "kind": "section",  # loader must tag Constitution articles kind="article"
                    "part": "Part XII",
                    "title": "Persons not to be deprived of property save by authority of law",
                    "text": "No person shall be deprived of his property save by authority of law.",
                }
            ]
        ),
        encoding="utf-8",
    )
    (raw_dir / "cad_debates").mkdir(parents=True)
    (raw_dir / "cad_debates/cad.json").write_text(
        json.dumps(
            [
                {
                    "volume": "VII",
                    "ref": "1948-11-04-01",
                    "date": "1948-11-04",
                    "speaker": "Dr. B.R. Ambedkar",
                    "title": "Introduction of Draft Constitution",
                    "text": "Constitutional morality is not a natural sentiment; it has to be cultivated.",
                }
            ]
        ),
        encoding="utf-8",
    )

    # E. DDL 81M + ILDC
    (raw_dir / "ddl_81m").mkdir(parents=True)
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "case_id": "ddl:mh:2015:10",
                    "cnr": "MHAU020000102015",
                    "state_code": "MH",
                    "court_code": "MH_DIST_01",
                    "court_name": "District Court, Pune",
                    "district": "Pune",
                    "title": "State of Maharashtra v. Accused Z",
                    "decision_date": "2015-08-12",
                    "year": 2015,
                    "disposal_nature": "Acquitted",
                    "case_type": "Criminal",
                    "act": "IPC",
                    "predicted_religion": "MUST_BE_STRIPPED",
                    "predicted_caste": "MUST_BE_STRIPPED",
                }
            ]
        ),
        raw_dir / "ddl_81m/ddl.parquet",
    )
    (raw_dir / "ildc").mkdir(parents=True)
    (raw_dir / "ildc/ildc.json").write_text(
        json.dumps(
            [
                {
                    "case_id": "ildc:2020:001",
                    "title": "State of Maharashtra v. Sample ILDC Appellant",
                    "year": 2020,
                    "decision_date": "2020-02-01",
                    "text": "1. ILDC non-commercial academic research annotation on criminal sentencing.",
                }
            ]
        ),
        encoding="utf-8",
    )

    # 1. Load with commercial_safe_only=False -> all sources populated
    bundle = load_staged_ingest_bundle(work_dir, commercial_safe_only=False)
    assert len(bundle.hc_mobile_rows) == 1
    assert len(bundle.ddl_district_rows) == 1
    assert "predicted_religion" not in bundle.ddl_district_rows[0]
    assert bundle.ddl_district_rows[0]["commercial_safe"] is False
    assert len(bundle.ildc_rows) == 1
    assert bundle.ildc_rows[0]["commercial_safe"] is False

    # HC judge normalized & enriched via JusticeHub KHOJ
    assert bundle.hc_web_rows[0]["judge"] == "Vibhu Bakhru"
    assert bundle.hc_web_rows[0]["khoj_judge_metadata"]["appointment_date"] == "2013-04-17"

    # e-SCR headnote enriched onto Puttaswamy
    puttaswamy = next(j for j in bundle.sc_judgments if j["case_id"] == "sc:2017:puttaswamy")
    assert "Informational privacy is a facet of Article 21" in puttaswamy["text"]

    # Constitution article tagged kind="article"
    art300a = next(
        p for p in bundle.provisions if p["act_id"] == "constitution" and p["ref_norm"] == "300A"
    )
    assert art300a["kind"] == "article"

    # CAD debates normalized into state_regulator_provisions
    cad_items = [p for p in bundle.state_regulator_provisions if p["category"] == "cad_debate"]
    assert len(cad_items) == 1
    assert cad_items[0]["jurisdiction"] == "Central"
    assert cad_items[0]["source_id"] == "cad_debates"

    # Build SQLite + Lance + Parquet + Run Legal Integrity Gate
    build_dir = tmp_path / "build"
    build_sqlite_core(build_dir / "nyaya-core.sqlite", input_data=bundle)
    build_lance_fts_tables(build_dir / "lancedb", input_data=bundle)
    build_compacted_parquets(build_dir, input_data=bundle)
    report = run_quality_gate(build_dir, write_gate_pass=True)
    assert report.passed, f"Gate failed: {report.all_errors}"

    # Verify SQLite & LanceDB queries
    store = SqliteStore(build_dir / "nyaya-core.sqlite")
    try:
        assert store.get_provision("RTIAct", "6") is not None
        assert store.get_provision("DPDPAct", "4") is not None
        p300a = store.get_provision("Constitution", "300A")
        assert p300a is not None and p300a.kind == "article"
        # hc_mobile_rows merged into SQLite judgments_meta (disposal_nature + KHOJ normalized judge)
        hc_meta = store.resolve_judgment_id("DLHC010123452024")
        assert hc_meta and hc_meta[0].disposal_nature == "Allowed"
        assert hc_meta[0].author_judge == "Vibhu Bakhru"
        assert bundle.hc_mobile_rows[0]["hearing_count"] == 5
    finally:
        store.close()

    lstore = LanceStore(local_dir=build_dir / "lancedb")
    try:
        cad_hits = lstore.search_state_regulators("constitutional morality", category="cad_debate")
        assert len(cad_hits) == 1
        assert cad_hits[0]["source_id"] == "cad_debates"
    finally:
        lstore.close()

    # 2. Load with commercial_safe_only=True -> ddl_81m and ildc omitted
    cs_bundle = load_staged_ingest_bundle(work_dir, commercial_safe_only=True)
    assert cs_bundle.ddl_district_rows == []
    assert cs_bundle.ildc_rows == []

    # 3. Empty work_dir -> graceful empty defaults
    empty_bundle = load_staged_ingest_bundle(tmp_path / "empty_work")
    assert empty_bundle.hc_mobile_rows == []
    assert empty_bundle.ddl_district_rows == []
    assert empty_bundle.ildc_rows == []


def test_cli_sync_supports_all_lockfile_sources_tiers_and_commercial_safe_only(
    tmp_path: Path,
) -> None:
    work_dir = tmp_path / "cli_work"
    rc = ingest_cli_main(
        [
            "sync",
            "--fixture-mode",
            "--commercial-safe-only",
            "--work-dir",
            str(work_dir),
        ]
    )
    assert rc == 0
    # Commercial-safe sources synced in fixture mode; NC sources skipped
    assert (work_dir / "raw" / "prs_2026_delta").is_dir()
    assert (work_dir / "raw" / "legislative_constitution").is_dir()
    assert (work_dir / "raw" / "indiacode_central_acts").is_dir()
    assert (work_dir / "raw" / "escr_judgments").is_dir()
    assert (work_dir / "raw" / "cad_debates").is_dir()
    assert (work_dir / "raw" / "justicehub_khoj").is_dir()
    assert not (work_dir / "raw" / "ddl_81m").exists()
    assert not (work_dir / "raw" / "ildc").exists()

    # Syncing specifically NC sources with --commercial-safe-only still skips them
    nc_work = tmp_path / "nc_work"
    rc_nc_skip = ingest_cli_main(
        [
            "sync",
            "--fixture-mode",
            "--sources",
            "ddl_81m,ildc",
            "--commercial-safe-only",
            "--work-dir",
            str(nc_work),
        ]
    )
    assert rc_nc_skip == 0
    assert not (nc_work / "raw" / "ddl_81m").exists()
    assert not (nc_work / "raw" / "ildc").exists()

    # Without --commercial-safe-only, explicit NC sources sync in fixture mode
    rc_nc_ok = ingest_cli_main(
        [
            "sync",
            "--fixture-mode",
            "--sources",
            "ddl_81m,ildc",
            "--work-dir",
            str(nc_work),
        ]
    )
    assert rc_nc_ok == 0
    assert (nc_work / "raw" / "ddl_81m").is_dir()
    assert (nc_work / "raw" / "ildc").is_dir()

    # --tier daily skips static-only sources
    daily_work = tmp_path / "daily_work"
    rc_daily = ingest_cli_main(
        [
            "sync",
            "--fixture-mode",
            "--tier",
            "daily",
            "--work-dir",
            str(daily_work),
        ]
    )
    assert rc_daily == 0
    assert (daily_work / "raw" / "prs_2026_delta").is_dir()
    assert (daily_work / "raw" / "escr_judgments").is_dir()
    assert not (daily_work / "raw" / "cad_debates").exists()
    assert not (daily_work / "raw" / "ddl_81m").exists()

    # --dry-run does not create raw outputs for official/research packs
    dry_work = tmp_path / "dry_work"
    rc_dry = ingest_cli_main(
        [
            "sync",
            "--fixture-mode",
            "--dry-run",
            "--work-dir",
            str(dry_work),
        ]
    )
    assert rc_dry == 0
    assert not (dry_work / "raw" / "prs_2026_delta").exists()
