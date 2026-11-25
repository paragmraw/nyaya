"""Tests for Vaquill nested SHA256SUMS + categorization, S3 pagination + year filter,
staged Parquet `loader.py`, and full builder + gate integration.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import httpx
import pyarrow as pa
import pyarrow.parquet as pq

from nyaya.ingest.builders.lance_fts import build_lance_fts_tables
from nyaya.ingest.builders.parquet_compact import build_compacted_parquets
from nyaya.ingest.builders.sqlite_core import build_sqlite_core
from nyaya.ingest.fetchers.http import SafeHttpFetcher
from nyaya.ingest.fetchers.s3_dattam import DattamS3Fetcher, parse_s3_list_objects_page
from nyaya.ingest.fetchers.vaquill import (
    VaquillFetcher,
    list_files_by_category,
    parse_sha256sums_json,
)
from nyaya.ingest.gate import run_quality_gate
from nyaya.ingest.loader import load_staged_ingest_bundle
from nyaya.ingest.state import IngestState
from nyaya.storage.sqlite_store import SqliteStore


def test_vaquill_nested_sha256sums_and_category_filtering(tmp_path: Path) -> None:
    nested = {
        "version": "v2026.08.1",
        "files": {
            "in_central_legislation.parquet": {"sha256": hashlib.sha256(b"central").hexdigest(), "bytes": 7},
            "in_maharashtra_legislation.parquet": {"sha256": hashlib.sha256(b"mh").hexdigest(), "bytes": 2},
            "in_sebi_regulations.parquet": {"sha256": hashlib.sha256(b"sebi").hexdigest(), "bytes": 4},
            "in_nclt_tribunal_matters.parquet": {"sha256": hashlib.sha256(b"nclt").hexdigest(), "bytes": 4},
        },
    }
    parsed = parse_sha256sums_json(nested)
    assert len(parsed) == 4
    by_cat = list_files_by_category(parsed)
    assert by_cat == {
        "central": ["in_central_legislation.parquet"],
        "state": ["in_maharashtra_legislation.parquet"],
        "regulator": ["in_sebi_regulations.parquet"],
        "tribunal": ["in_nclt_tribunal_matters.parquet"],
    }

    # Flat format also supported
    assert parse_sha256sums_json({"a.parquet": "deadbeef"}) == {"a.parquet": "deadbeef"}

    payloads = {
        "/v2026.08.1/SHA256SUMS.json": json.dumps(nested).encode("utf-8"),
        "/v2026.08.1/in_central_legislation.parquet": b"central",
        "/v2026.08.1/in_sebi_regulations.parquet": b"sebi",
    }

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path in payloads:
            return httpx.Response(200, content=payloads[req.url.path])
        return httpx.Response(404)

    state = IngestState(tmp_path / "state.sqlite")
    try:
        fetcher = VaquillFetcher(
            state=state,
            http_fetcher=SafeHttpFetcher(
                allowed_domains=["oss-data-in.vaquill.ai"],
                transport=httpx.MockTransport(handler),
            ),
        )
        res = fetcher.sync_packs(tmp_path, categories=["central", "regulator"])
        assert res.downloaded_files == [
            "in_central_legislation.parquet",
            "in_sebi_regulations.parquet",
        ]
        assert (tmp_path / "raw/vaquill/v2026.08.1/in_central_legislation.parquet").read_bytes() == b"central"
        assert not (tmp_path / "raw/vaquill/v2026.08.1/in_maharashtra_legislation.parquet").exists()
    finally:
        state.close()


def test_s3_dattam_pagination_and_year_filtering(tmp_path: Path) -> None:
    page1_xml = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <IsTruncated>true</IsTruncated>
  <NextContinuationToken>tok+page/2=</NextContinuationToken>
  <Contents>
    <Key>metadata/parquet/year=2024/court=7_26/bench=dhc/metadata.parquet</Key>
    <ETag>"etag-1"</ETag>
    <Size>10</Size>
  </Contents>
</ListBucketResult>"""

    page2_xml = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <IsTruncated>false</IsTruncated>
  <Contents>
    <Key>metadata/parquet/year=2024/court=27_1/bench=bhc/metadata.parquet</Key>
    <ETag>"etag-2"</ETag>
    <Size>10</Size>
  </Contents>
</ListBucketResult>"""

    objs_p1, tok = parse_s3_list_objects_page(page1_xml)
    assert len(objs_p1) == 1
    assert tok == "tok+page/2="

    seen_urls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen_urls.append(str(req.url))
        if req.url.params.get("list-type") == "2":
            if req.url.params.get("continuation-token") == "tok+page/2=":
                return httpx.Response(200, text=page2_xml)
            return httpx.Response(200, text=page1_xml)
        return httpx.Response(200, content=b"PAR1")

    state = IngestState(tmp_path / "state.sqlite")
    try:
        fetcher = DattamS3Fetcher(
            state=state,
            http_fetcher=SafeHttpFetcher(
                allowed_domains=["indian-high-court-judgments.s3.ap-south-1.amazonaws.com"],
                transport=httpx.MockTransport(handler),
            ),
        )
        summary = fetcher.sync_bucket(
            "indian-high-court-judgments",
            tmp_path,
            prefixes=["metadata/parquet/"],
            year_from=2024,
            year_to=2024,
        )
        assert len(summary.downloaded_keys) == 2
        assert any("prefix=metadata/parquet/year%3D2024/" in u or "year=2024" in u for u in seen_urls)
        assert any("continuation-token=tok%2Bpage%2F2%3D" in u for u in seen_urls)
    finally:
        state.close()


def test_staged_loader_builds_and_passes_quality_gate(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    vq_dir = work_dir / "raw" / "vaquill" / "v2026.08.1"
    vq_dir.mkdir(parents=True)

    # 1. Central legislation (multi-chunk BNS 103 + dirty HTML row + headnote row + sealed ban row)
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "act_id": "IND_central_20062",
                    "chunk_id": "IND_central_20062_s103_p0",
                    "title": "The Bharatiya Nyaya Sanhita, 2023",
                    "chapter": "VI",
                    "section_number": "103",
                    "section_title": "The Bharatiya Nyaya Sanhita, 2023",
                    "text": "Act: The Bharatiya Nyaya Sanhita, 2023 | India | Central | In Force\nChapter VI: OFFENCES AFFECTING THE HUMAN BODY | Section 103: Punishment for murder\n(1) Whoever commits murder shall be punished with death or imprisonment for life.",
                    "act_status": "in_force",
                    "in_force": True,
                    "section_type": "section",
                    "year": 2023.0,
                    "source_url": "https://oss-data-in.vaquill.ai/v2026.08.1/in_central_legislation.parquet",
                },
                {
                    "act_id": "IND_central_20062",
                    "chunk_id": "IND_central_20062_s103_p1",
                    "title": "The Bharatiya Nyaya Sanhita, 2023",
                    "chapter": "VI",
                    "section_number": "103",
                    "section_title": "The Bharatiya Nyaya Sanhita, 2023",
                    "text": "Act: The Bharatiya Nyaya Sanhita, 2023 | India | Central | In Force\n(2) When a group of five or more persons commits murder on grounds of race or caste.",
                    "act_status": "in_force",
                    "in_force": True,
                    "section_type": "section",
                    "year": 2023.0,
                    "source_url": "https://oss-data-in.vaquill.ai/v2026.08.1/in_central_legislation.parquet",
                },
                {
                    "act_id": "IND_central_99999",
                    "chunk_id": "IND_central_99999_bad",
                    "title": "Spent Act",
                    "chapter": "",
                    "section_number": "1",
                    "section_title": "Bad",
                    "text": "<html><body>404 Not Found</body></html>",
                    "act_status": "spent",
                    "in_force": False,
                    "section_type": "section",
                    "year": 1955.0,
                    "source_url": "https://oss-data-in.vaquill.ai/v2026.08.1/in_central_legislation.parquet",
                },
            ]
        ),
        vq_dir / "in_central_legislation.parquet",
    )

    # 2. State legislation, Regulator, Tribunal
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "act_id": "IND_state_16719",
                    "chunk_id": "IND_state_16719_s002",
                    "title": "The Maharashtra Ownership Flats Act, 1963",
                    "section_number": "4",
                    "section_title": "Promoter agreement",
                    "text": "Promoter before accepting advance payment shall execute registered agreement for sale.",
                    "act_status": "in_force",
                    "in_force": True,
                    "section_type": "section",
                    "year": 1963.0,
                    "source_url": "https://oss-data-in.vaquill.ai/v2026.08.1/in_maharashtra_legislation.parquet",
                }
            ]
        ),
        vq_dir / "in_maharashtra_legislation.parquet",
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "act_id": "REG_SEBI_lodr",
                    "chunk_id": "REG_SEBI_lodr_r30",
                    "title": "SEBI (LODR) Regulations, 2015",
                    "section_number": "30",
                    "section_title": "Disclosure",
                    "text": "Every listed entity shall disclose material events under Regulation 30.",
                    "act_status": "in_force",
                    "in_force": True,
                    "instrument_type": "regulatory",
                    "year": 2015.0,
                    "source_url": "https://oss-data-in.vaquill.ai/v2026.08.1/in_sebi_regulations.parquet",
                }
            ]
        ),
        vq_dir / "in_sebi_regulations.parquet",
    )
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "tribunal": "nclt",
                    "case_id": "NCLT_2024_1",
                    "case_number": "CP/1/2024",
                    "title": "Creditor v. Corporate Debtor",
                    "petitioner": "Creditor",
                    "respondent": "Corporate Debtor",
                    "bench": "Mumbai",
                    "judges": ["Member A"],
                    "decision_date": "2024-02-01",
                    "year": 2024,
                    "doc_type": "order",
                }
            ]
        ),
        vq_dir / "in_nclt_tribunal_matters.parquet",
    )

    # 3. Dattam SC (year=2017 with Puttaswamy + year=2024)
    sc_2017 = work_dir / "raw/indian-supreme-court-judgments/metadata/parquet/year=2017"
    sc_2017.mkdir(parents=True)
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "title": "JUSTICE K S PUTTASWAMY (RETD.), AND ANR. versus UNION OF INDIA AND ORS.",
                    "petitioner": "JUSTICE K S PUTTASWAMY (RETD.)",
                    "respondent": "UNION OF INDIA",
                    "description": "",
                    "judge": "J.S. KHEHAR, D.Y. CHANDRACHUD",
                    "author_judge": None,
                    "citation": "[2017] 10 S.C.R. 569",
                    "case_id": "2017 INSC 801",
                    "cnr": "ESCR010000342017",
                    "decision_date": "24-08-2017",
                    "disposal_nature": "Reference answered",
                    "available_languages": "ENG,HIN",
                    "path": "2017_10_569_999",
                    "nc_display": "2017INSC801",
                    "year": 2017,
                }
            ]
        ),
        sc_2017 / "metadata.parquet",
    )

    # 4. Dattam HC (year=2024)
    hc_2024 = work_dir / "raw/indian-high-court-judgments/metadata/parquet/year=2024/court=7_26/bench=principal"
    hc_2024.mkdir(parents=True)
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "court_code": "7~26",
                    "title": "Arjun Mehta v. State NCT of Delhi",
                    "description": "Anticipatory bail granted under Section 482 BNSS.",
                    "judge": "JUSTICE VIBHU BAKHRU",
                    "pdf_link": "orders/DLHC010123452024.pdf",
                    "cnr": "DLHC010123452024",
                    "decision_date": datetime(2024, 5, 14),
                    "disposal_nature": "Allowed",
                    "court": "7_26",
                    "pdf_exists": True,
                    "bench": "principal",
                    "year": 2024,
                }
            ]
        ),
        hc_2024 / "metadata.parquet",
    )

    bundle = load_staged_ingest_bundle(work_dir)
    build_dir = tmp_path / "build"
    build_sqlite_core(build_dir / "nyaya-core.sqlite", input_data=bundle)
    counts = build_lance_fts_tables(build_dir / "lancedb", input_data=bundle)
    assert counts["state_acts_regulators"] == 2
    assert counts["hc_hot_chunks"] == 1
    build_compacted_parquets(build_dir, input_data=bundle)

    report = run_quality_gate(build_dir, write_gate_pass=True)
    assert report.passed, f"Gate failed: {report.all_errors}"

    store = SqliteStore(build_dir / "nyaya-core.sqlite")
    try:
        # Multi-chunk BNS 103 concatenated cleanly without "..."
        bns103 = store.get_provision("BNS", "103")
        assert bns103 is not None
        assert "(1) Whoever commits murder" in bns103.text
        assert "(2) When a group of five" in bns103.text

        # Puttaswamy carries real S3 PDF key and canonical SCC citation
        metas = store.resolve_judgment_id("(2017) 10 SCC 1")
        assert metas
        assert metas[0].pdf_s3_bucket == "indian-supreme-court-judgments"
        assert metas[0].pdf_s3_key == "data/pdf/year=2017/english/2017_10_569_999_EN.pdf"

        # State/Regulator parent acts registered with lance_bucket
        mh_acts = store.browse_statute(jurisdiction="MH")
        assert mh_acts.total >= 1

        # Curated provisions & metadata hygiene checks
        c73 = store.get_provision("ContractAct", "73")
        assert c73 is not None and "naturally arose in the usual course" in c73.text
        crpc438 = store.get_provision("CrPC", "438")
        assert crpc438 is not None and crpc438.validity.commencement_date.isoformat() == "1974-04-01"
        iea65b = store.get_provision("EvidenceAct", "65B")
        assert iea65b is not None and "(4)" in iea65b.text and "(5)" in iea65b.text
        bsa63 = store.get_provision("BSA", "63")
        assert bsa63 is not None and "(4)" in bsa63.text and "Schedule" in bsa63.text
        it66a = store.get_provision("ITAct", "66A")
        assert it66a is not None and it66a.validity.status == "struck_down"
        cpc_r11 = store.get_provision("CPC", "Order 7 Rule 11")
        assert cpc_r11 is not None and "(d) where the suit appears" in cpc_r11.text

        # Vaquill header/footnote stripped on idx == 0 & sub-clause inherits parent text
        assert not bns103.text.startswith("Act:")
        bns103_1 = store.get_provision("BNS", "103(1)")
        assert bns103_1 is not None and "(2) When a group of five" in bns103_1.text

        # Source record counts populated (> 0)
        status = store.corpus_status()
        counts_by_src = {s.source_id: s.record_count for s in status.sources}
        assert counts_by_src.get("vaquill_central_legislation", 0) > 0
        assert counts_by_src.get("dattam_sc_judgments", 0) > 0
    finally:
        store.close()


def test_p1_series_grouping_vaquill_cleaning_and_sc_cnr_dedup(tmp_path: Path) -> None:
    """Verify series-aware ArbitrationAct 9 / IBC 14 selection, idx==0 footnote stripping, and SC CNR deduplication."""
    work_dir = tmp_path / "work2"
    vq_dir = work_dir / "raw" / "vaquill" / "v2026.08.1"
    vq_dir.mkdir(parents=True)

    pq.write_table(
        pa.Table.from_pylist(
            [
                # Arbitration Act s.9 collision: _s009 TOC vs _s009_p0 + _s009_p1 statutory body
                {
                    "act_id": "IND_central_1978",
                    "chunk_id": "IND_central_1978_s009",
                    "title": "The Arbitration and Conciliation Act, 1996",
                    "chapter": "VI",
                    "section_number": "9",
                    "section_title": "The Arbitration and Conciliation Act, 1996",
                    "text": (
                        "Act: The Arbitration and Conciliation Act, 1996 | India | Central | In Force\n"
                        "Chapter VI: ## _Making of arbitral award_ | "
                        "Section 29: Decision making\nSection 30: Settlement\nSection 31: Form and contents"
                    ),
                    "act_status": "in_force",
                    "in_force": True,
                    "section_type": "section",
                    "year": 1996.0,
                    "source_url": "https://oss-data-in.vaquill.ai/v2026.08.1/in_central_legislation.parquet",
                },
                {
                    "act_id": "IND_central_1978",
                    "chunk_id": "IND_central_1978_s009_p0",
                    "title": "The Arbitration and Conciliation Act, 1996",
                    "chapter": "II",
                    "section_number": "9",
                    "section_title": "Interim measures, etc., by Court",
                    "text": (
                        "Act: The Arbitration and Conciliation Act, 1996 | India | Central | In Force\n"
                        "Chapter II: ## _Arbitration agreement_ | Section 9: **Interim measures, etc., by Court**\n"
                        "1[(1)] A party may, before or during arbitral proceedings apply to a Court for interim measure.\n"
                        "> 1. Subs. by Act 3 of 2016, s. 5 (w.e.f. 23-10-2015)."
                    ),
                    "act_status": "in_force",
                    "in_force": True,
                    "section_type": "section",
                    "year": 1996.0,
                    "source_url": "https://oss-data-in.vaquill.ai/v2026.08.1/in_central_legislation.parquet",
                },
                {
                    "act_id": "IND_central_1978",
                    "chunk_id": "IND_central_1978_s009_p1",
                    "title": "The Arbitration and Conciliation Act, 1996",
                    "chapter": "II",
                    "section_number": "9",
                    "section_title": "Interim measures, etc., by Court",
                    "text": (
                        "Act: The Arbitration and Conciliation Act, 1996 | India | Central | In Force\n"
                        "(2) Where the Court passes an order for any interim measure, arbitral proceedings shall commence within ninety days."
                    ),
                    "act_status": "in_force",
                    "in_force": True,
                    "section_type": "section",
                    "year": 1996.0,
                    "source_url": "https://oss-data-in.vaquill.ai/v2026.08.1/in_central_legislation.parquet",
                },
                # IBC s.14 collision: _s014 Schedule amendment vs _s014_p0 + _s014_p1 moratorium
                {
                    "act_id": "IND_central_2154",
                    "chunk_id": "IND_central_2154_s014",
                    "title": "The Insolvency and Bankruptcy Code, 2016.",
                    "chapter": "VII",
                    "section_number": "14",
                    "section_title": "The Insolvency and Bankruptcy Code, 2016.",
                    "text": (
                        "Act: The Insolvency and Bankruptcy Code, 2016. | India | Central | In Force\n"
                        "Chapter VII: FINANCE, ACCOUNTS AND AUDIT | Section 14: For section 280, the following section shall be substituted."
                    ),
                    "act_status": "in_force",
                    "in_force": True,
                    "section_type": "section",
                    "year": 2016.0,
                    "source_url": "https://oss-data-in.vaquill.ai/v2026.08.1/in_central_legislation.parquet",
                },
                {
                    "act_id": "IND_central_2154",
                    "chunk_id": "IND_central_2154_s014_p0",
                    "title": "The Insolvency and Bankruptcy Code, 2016.",
                    "chapter": "II",
                    "section_number": "14",
                    "section_title": "Moratorium",
                    "text": (
                        "Act: The Insolvency and Bankruptcy Code, 2016. | India | Central | In Force\n"
                        "Chapter II: CORPORATE INSOLVENCY RESOLUTION PROCESS | Section 14: Moratorium**\n"
                        "14. Moratorium** .—( 1 ) Subject to provisions of sub-sections (2) and (3), on the insolvency commencement date, the Adjudicating Authority shall by order declare moratorium."
                    ),
                    "act_status": "in_force",
                    "in_force": True,
                    "section_type": "section",
                    "year": 2016.0,
                    "source_url": "https://oss-data-in.vaquill.ai/v2026.08.1/in_central_legislation.parquet",
                },
            ]
        ),
        vq_dir / "in_central_legislation.parquet",
    )

    # Same SC judgment duplicated across year=1976 and year=1977 with identical CNR
    for yr in (1976, 1977):
        ydir = work_dir / f"raw/indian-supreme-court-judgments/metadata/parquet/year={yr}"
        ydir.mkdir(parents=True)
        pq.write_table(
            pa.Table.from_pylist(
                [
                    {
                        "title": "Sample Appellant versus Sample Respondent",
                        "petitioner": "Sample Appellant",
                        "respondent": "Sample Respondent",
                        "description": "Arbitration interim relief precedent." if yr == 1976 else "",
                        "judge": "Y.V. CHANDRACHUD",
                        "author_judge": "Y.V. CHANDRACHUD",
                        "citation": "AIR 1976 SC 1234",
                        "case_id": "1976 INSC 276",
                        "cnr": "ESCR010000071977",
                        "decision_date": "15-10-1976",
                        "disposal_nature": "Allowed",
                        "available_languages": "ENG",
                        "path": "1976_sample",
                        "nc_display": "1976INSC276",
                        "year": yr,
                    }
                ]
            ),
            ydir / "metadata.parquet",
        )

    bundle = load_staged_ingest_bundle(work_dir)
    matching_cnrs = [j for j in bundle.sc_judgments if j.get("cnr") == "ESCR010000071977"]
    assert len(matching_cnrs) == 1

    build_dir = tmp_path / "build2"
    build_sqlite_core(build_dir / "nyaya-core.sqlite", input_data=bundle)
    store = SqliteStore(build_dir / "nyaya-core.sqlite")
    try:
        arb9 = store.get_provision("ArbitrationAct", "9")
        assert arb9 is not None
        assert "A party may, before or during arbitral proceedings" in arb9.text
        assert "within ninety days" in arb9.text
        assert "Act:" not in arb9.text
        assert "Subs. by Act" not in arb9.text
        assert "**" not in arb9.text

        ibc14 = store.get_provision("IBC", "14")
        assert ibc14 is not None
        assert "declare moratorium" in ibc14.text
        assert "For section 280" not in ibc14.text
        assert "**" not in ibc14.text
        assert " .—" not in ibc14.text
    finally:
        store.close()
