"""Tests for HC & Tribunal judgment lookup across LanceDB & DuckDB cold tiers and manifest as_of."""

from __future__ import annotations

import json
import sqlite3
import time
from datetime import date as Date
from pathlib import Path
from typing import Any

import lancedb
import pyarrow as pa
import pytest

from nyaya import storage
from nyaya.config import get_settings
from nyaya.exceptions import StorageUnavailable, TextUnavailableMetadataOnly
from nyaya.models import CorpusCounts, CorpusStatusResponse
from nyaya.retrieval.lexical_engine import LexicalRetrievalEngine
from nyaya.storage import enrich_corpus_status
from nyaya.storage.duckdb_store import DuckDBStore
from nyaya.storage.lance_store import (
    HC_HOT_CHUNKS_TABLE,
    TRIBUNAL_CHUNKS_TABLE,
    LanceStore,
)
from nyaya.storage.manifest import CorpusManifest, load_manifest
from nyaya.storage.memory_backend import InMemoryCorpusBackend
from nyaya.storage.sqlite_store import SqliteStore
from nyaya.tools.get_judgment import get_judgment, resolve_judgment_across_tiers
from nyaya.tools.get_judgments_batch import get_judgments_batch
from nyaya.tools.verify_citations import verify_citations
from tests.fixtures.mini_corpus.builder import _write_zstd_parquet, build_mini_corpus


def _build_cold_tier_corpus(root: Path) -> dict[str, Path]:
    """Build SQLite (SC-only) + LanceDB (multi-chunk HC & Tribunal) + Parquet (HC & Tribunal)."""
    sqlite_path = root / "nyaya-core.sqlite"
    con = sqlite3.connect(sqlite_path)
    from tests.fixtures.mini_corpus.builder import MINI_SQLITE_DDL

    try:
        con.executescript(MINI_SQLITE_DDL)
        con.execute(
            "INSERT INTO sources VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "dattam-sc",
                "Dattam Labs SC",
                "s3://indian-supreme-court-judgments",
                "CC-BY-4.0",
                1,
                "cleared",
                "2026-11-26",
                "2026-11-20",
                "a" * 64,
                1,
                None,
            ),
        )
        con.execute(
            "INSERT INTO judgments_meta VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "sc:2017:puttaswamy",
                "sc",
                "SCI",
                "Supreme Court of India",
                "Constitution Bench",
                None,
                "2017 INSC 801",
                None,
                "(2017) 10 SCC 1",
                None,
                "Justice K.S. Puttaswamy v. Union of India",
                "Justice K.S. Puttaswamy",
                "Union of India",
                "2017-08-24",
                2017,
                "J.S. Khehar; D.Y. Chandrachud",
                "D.Y. Chandrachud",
                9,
                "Allowed",
                "Writ Petition (Civil)",
                '["Constitution"]',
                1,
                None,
                None,
                "https://indian-supreme-court-judgments.s3.ap-south-1.amazonaws.com",
                "b" * 64,
                0,
                820,
                "dattam-sc",
                1,
            ),
        )
        con.commit()
    finally:
        con.close()

    lance_dir = root / "lancedb"
    lance_dir.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(lance_dir))

    hc_tbl = pa.table(
        {
            "chunk_id": [
                "hc:2024:7_26:DLHC010555552024:1",
                "hc:2024:7_26:DLHC010555552024:0",
                "hc:2024:7_26:BANNED1:0",
                "hc:2024:7_26:NC_ONLY:0",
            ],
            "case_id": [
                "hc:2024:7_26:DLHC010555552024",
                "hc:2024:7_26:DLHC010555552024",
                "hc:2024:7_26:BANNED1",
                "hc:2024:7_26:NC_ONLY",
            ],
            "year": pa.array([2024, 2024, 2024, 2024], type=pa.int32()),
            "decision_date": ["2024-09-12", "2024-09-12", "2024-08-01", "2024-07-01"],
            "title": [
                "Vikram Rao v. Directorate of Enforcement",
                "Vikram Rao v. Directorate of Enforcement",
                "In Re: Victim Protection Matter",
                "Non Commercial HC Case v. State",
            ],
            "neutral_citation": [
                "2024:DHC:7777",
                "2024:DHC:7777",
                "2024:DHC:8888",
                "2024:DHC:9999",
            ],
            "air_citation": ["", "", "", ""],
            "scc_citation": ["", "", "", ""],
            "author_judge": [
                "Anup Jairam Bhambhani",
                "Anup Jairam Bhambhani",
                "Judge X",
                "Judge Y",
            ],
            "bench_strength": pa.array([1, 1, 1, 1], type=pa.int32()),
            "opinion_type": ["majority", "majority", "majority", "majority"],
            "para_start": pa.array([10, 1, 1, 1], type=pa.int32()),
            "para_end": pa.array([12, 3, 2, 2], type=pa.int32()),
            "page_start": pa.array([4, 1, 1, 1], type=pa.int32()),
            "page_end": pa.array([5, 2, 1, 1], type=pa.int32()),
            "acts_cited": [["PMLA", "BNSS"], ["PMLA", "BNSS"], ["POCSO"], ["IPC"]],
            "text": [
                "Twin conditions under Section 45 PMLA cannot override prolonged incarceration.",
                "Petitioner seeks regular bail in ECIR proceedings under PMLA.",
                "Publication of the identity of the victim is prohibited under Section 228A IPC.",
                "Non-commercial research HC chunk.",
            ],
            "commercial_safe": [True, True, True, False],
            "terms_status": ["cleared", "cleared", "cleared", "non_commercial_only"],
            "license_spdx": ["CC-BY-4.0", "CC-BY-4.0", "CC-BY-4.0", "CC-BY-NC-4.0"],
            "source_id": ["dattam-hc", "dattam-hc", "dattam-hc", "ildc-hc"],
            "source_url": [
                "https://indian-high-court-judgments.s3.ap-south-1.amazonaws.com",
                "https://indian-high-court-judgments.s3.ap-south-1.amazonaws.com",
                "https://indian-high-court-judgments.s3.ap-south-1.amazonaws.com",
                "https://example.invalid/nc",
            ],
            "text_sha256": ["c" * 64, "d" * 64, "e" * 64, "f" * 64],
            "court_code": ["7_26", "7_26", "7_26", "7_26"],
            "bench": ["Principal", "Principal", "Principal", "Principal"],
            "cnr": [
                "DLHC010555552024",
                "DLHC010555552024",
                "DLHC010666662024",
                "DLHC010777772024",
            ],
            "disposal_nature": ["Allowed", "Allowed", "Disposed of", "Dismissed"],
            "is_reportable": [True, True, False, False],
        }
    )
    db.create_table(HC_HOT_CHUNKS_TABLE, data=hc_tbl, mode="overwrite").create_fts_index(
        "text", replace=True, with_position=True
    )

    trib_tbl = pa.table(
        {
            "chunk_id": ["CESTAT_2024_501:1", "CESTAT_2024_501:0"],
            "case_id": ["CESTAT_2024_501", "CESTAT_2024_501"],
            "forum_code": ["CESTAT", "CESTAT"],
            "court_code": ["CESTAT", "CESTAT"],
            "court_name": [
                "Customs, Excise and Service Tax Appellate Tribunal",
                "Customs, Excise and Service Tax Appellate Tribunal",
            ],
            "bench": ["New Delhi", "New Delhi"],
            "year": pa.array([2024, 2024], type=pa.int32()),
            "decision_date": ["2024-11-05", "2024-11-05"],
            "title": [
                "Bharat Logistics Pvt Ltd v. Commissioner of Customs",
                "Bharat Logistics Pvt Ltd v. Commissioner of Customs",
            ],
            "neutral_citation": ["2024 CESTAT 501", "2024 CESTAT 501"],
            "author_judge": ["Justice Dilip Gupta", "Justice Dilip Gupta"],
            "case_type": ["Customs Appeal", "Customs Appeal"],
            "disposal_nature": ["Allowed", "Allowed"],
            "acts_cited": [["CustomsAct"], ["CustomsAct"]],
            "para_start": pa.array([7, 1], type=pa.int32()),
            "para_end": pa.array([9, 3], type=pa.int32()),
            "text": [
                "Extended period of limitation cannot be invoked absent wilful suppression.",
                "Appeal challenges customs duty demand and penalty under Section 114A.",
            ],
            "commercial_safe": [True, True],
            "terms_status": ["cleared", "cleared"],
            "license_spdx": ["CC-BY-4.0", "CC-BY-4.0"],
            "source_id": ["vaquill_tribunals", "vaquill_tribunals"],
            "source_url": ["https://oss-data-in.vaquill.ai", "https://oss-data-in.vaquill.ai"],
            "text_sha256": ["1" * 64, "2" * 64],
        }
    )
    db.create_table(TRIBUNAL_CHUNKS_TABLE, data=trib_tbl, mode="overwrite").create_fts_index(
        "text", replace=True, with_position=True
    )

    # Parquet cold tier (records NOT in SQLite and NOT in LanceDB)
    hc_dhc_2022 = pa.table(
        {
            "case_id": ["hc:2022:7_26:DLHC010111112022"],
            "cnr": ["DLHC010111112022"],
            "court_code": ["7_26"],
            "court_name": ["7~26"],  # code-only; should be enriched to High Court of Delhi
            "bench": ["Principal"],
            "neutral_citation": ["2022:DHC:4321"],
            "title": ["Cold Tier Appellant v. Union of India"],
            "petitioner": ["Cold Tier Appellant"],
            "respondent": ["Union of India"],
            "judge": ["Justice Rekha Palli"],
            "author_judge": ["Justice Rekha Palli"],
            "bench_strength": pa.array([1], type=pa.int32()),
            "decision_date": ["2022-06-15"],
            "year": pa.array([2022], type=pa.int32()),
            "disposal_nature": ["Disposed of"],
            "case_type": ["W.P.(C)"],
            "act": ["Constitution"],
            "commercial_safe": [True],
        }
    )
    hc_bhc_2021 = pa.table(
        {
            "case_id": ["hc:2021:27_1:BHC_COLD_99"],
            "cnr": ["MHHC010222222021"],
            "court_code": ["27_1"],
            "court_name": [""],  # missing; should be enriched to High Court of Bombay
            "bench": ["Original Side"],
            "neutral_citation": ["2021:BHC-OS:99"],
            "title": ["Matter ordered to be kept in a sealed cover v. State"],
            "petitioner": ["Sealed Party"],
            "respondent": ["State"],
            "judge": ["Justice G.S. Patel"],
            "author_judge": ["Justice G.S. Patel"],
            "bench_strength": pa.array([1], type=pa.int32()),
            "decision_date": ["2021-04-20"],
            "year": pa.array([2021], type=pa.int32()),
            "disposal_nature": ["Disposed of"],
            "case_type": ["SUIT"],
            "act": ["CPC"],
            "commercial_safe": [True],
        }
    )
    trib_cestat = pa.table(
        {
            "case_id": ["CESTAT_2023_900", "CUSTOM_FALLBACK_TRIB_1"],
            "forum_code": ["CESTAT", "CESTAT"],
            "court_code": ["CESTAT", "CESTAT"],
            "court_name": [
                "Customs, Excise and Service Tax Appellate Tribunal",
                "Customs, Excise and Service Tax Appellate Tribunal",
            ],
            "bench": ["Mumbai", "Mumbai"],
            "title": ["Cold CESTAT Order v. CCE", "Fallback Forum Order v. CCE"],
            "petitioner": ["Cold CESTAT Order", "Fallback Forum Order"],
            "respondent": ["CCE", "CCE"],
            "judge": ["Member Judicial A", "Member Judicial B"],
            "decision_date": ["2023-09-10", "2023-10-11"],
            "year": pa.array([2023, 2023], type=pa.int32()),
            "disposal_nature": ["Allowed", "Dismissed"],
            "case_type": ["Excise Appeal", "Excise Appeal"],
            "act": ["CentralExciseAct", "CentralExciseAct"],
            "commercial_safe": [True, True],
        }
    )
    trib_itat = pa.table(
        {
            "case_id": ["ITAT_2023_44"],
            "forum_code": ["ITAT"],
            "court_code": ["ITAT"],
            "court_name": ["Income Tax Appellate Tribunal"],
            "bench": ["Delhi"],
            "title": ["Cold ITAT Tax Matter v. ACIT"],
            "petitioner": ["Cold ITAT Tax Matter"],
            "respondent": ["ACIT"],
            "judge": ["Accountant Member X"],
            "decision_date": ["2023-03-18"],
            "year": pa.array([2023], type=pa.int32()),
            "disposal_nature": ["Allowed"],
            "case_type": ["ITA"],
            "act": ["IncomeTax1961"],
            "commercial_safe": [True],
        }
    )

    rel_dhc = "parquet/high_court/court=7_26/decade=2020s.parquet"
    rel_bhc = "parquet/high_court/court=27_1/decade=2020s.parquet"
    rel_cestat = "parquet/tribunal/forum=CESTAT.parquet"
    rel_itat = "parquet/tribunal/forum=ITAT.parquet"
    _write_zstd_parquet(hc_dhc_2022, root / rel_dhc)
    _write_zstd_parquet(hc_bhc_2021, root / rel_bhc)
    _write_zstd_parquet(trib_cestat, root / rel_cestat)
    _write_zstd_parquet(trib_itat, root / rel_itat)

    manifest_obj = {
        "schema_version": "2.0",
        "generated_at": "2026-11-26T12:00:00Z",
        "datasets": {
            "high_court": {
                "license_spdx": "CC-BY-4.0",
                "commercial_safe": True,
                "terms_status": "cleared",
                "row_count": 2,
                "partitions": [
                    {
                        "court_code": "7_26",
                        "court_name": "High Court of Delhi",
                        "year_min": 2020,
                        "year_max": 2029,
                        "rows": 1,
                        "s3_key": rel_dhc,
                    },
                    {
                        "court_code": "27_1",
                        "court_name": "High Court of Bombay",
                        "year_min": 2020,
                        "year_max": 2029,
                        "rows": 1,
                        "s3_key": rel_bhc,
                    },
                ],
            },
            "tribunal": {
                "license_spdx": "CC-BY-4.0",
                "commercial_safe": True,
                "terms_status": "cleared",
                "row_count": 3,
                "partitions": [
                    {
                        "forum_code": "CESTAT",
                        "year_min": 2020,
                        "year_max": 2029,
                        "rows": 2,
                        "s3_key": rel_cestat,
                    },
                    {
                        "forum_code": "ITAT",
                        "year_min": 2020,
                        "year_max": 2029,
                        "rows": 1,
                        "s3_key": rel_itat,
                    },
                ],
            },
        },
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest_obj, indent=2), encoding="utf-8")
    return {
        "root": root,
        "sqlite": sqlite_path,
        "lancedb": lance_dir,
        "manifest": manifest_path,
    }


@pytest.fixture
def wired_cold_tiers(tmp_path: Path):
    paths = _build_cold_tier_corpus(tmp_path)
    sqlite_store = SqliteStore(paths["sqlite"])
    lance_store = LanceStore(local_dir=paths["lancedb"], offline_only=True)
    manifest = load_manifest(paths["manifest"])
    duck_store = DuckDBStore(
        manifest=manifest,
        base_dir=paths["root"],
        temp_dir=tmp_path / "duck_tmp",
        offline_only=True,
    )
    settings = get_settings()
    engine = LexicalRetrievalEngine(
        settings, sqlite_store, lance=lance_store, duckdb=duck_store
    )
    storage.set_backend(sqlite_store)
    storage.set_retrieval(engine)
    storage.set_analytics(duck_store)
    try:
        yield paths, sqlite_store, lance_store, duck_store
    finally:
        storage.close_all()


def test_lance_lookup_judgment_by_id_sorting_cs_and_unavailable(
    wired_cold_tiers, tmp_path: Path
) -> None:
    _, _, lance, _ = wired_cold_tiers

    # Multi-chunk HC lookup by case_id (including ~ -> _), CNR, neutral_citation
    for ident in (
        "hc:2024:7_26:DLHC010555552024",
        "hc:2024:7~26:DLHC010555552024",
        "DLHC010555552024",
        "2024:DHC:7777",
    ):
        tbl, rows = lance.lookup_judgment_by_id(ident)
        assert tbl == HC_HOT_CHUNKS_TABLE
        assert [r["para_start"] for r in rows] == [1, 10]

    # Tribunal lookup by case_id and neutral_citation, sorted by para_start
    for ident in ("CESTAT_2024_501", "2024 CESTAT 501"):
        tbl, rows = lance.lookup_judgment_by_id(ident)
        assert tbl == TRIBUNAL_CHUNKS_TABLE
        assert [r["para_start"] for r in rows] == [1, 7]

    # commercial_safe_only filters out non-commercial LanceDB chunks
    tbl_all, rows_all = lance.lookup_judgment_by_id(
        "hc:2024:7_26:NC_ONLY", commercial_safe_only=False
    )
    assert tbl_all == HC_HOT_CHUNKS_TABLE
    assert len(rows_all) == 1
    tbl_cs, rows_cs = lance.lookup_judgment_by_id(
        "hc:2024:7_26:NC_ONLY", commercial_safe_only=True
    )
    assert tbl_cs is None and rows_cs == []

    # StorageUnavailable handled gracefully
    missing = LanceStore(local_dir=tmp_path / "missing_lance", offline_only=True)
    assert missing.lookup_judgment_by_id("2024:DHC:7777") == (None, [])


def test_duckdb_lookup_judgment_by_id_pruning_enrichment_and_manifest_as_of(
    wired_cold_tiers,
) -> None:
    _, _, _, duck = wired_cold_tiers

    # HC case_id with ~ normalization & court_name enrichment ("7~26" -> "High Court of Delhi")
    ds, row = duck.lookup_judgment_by_id("hc:2022:7~26:DLHC010111112022")
    assert ds == "high_court"
    assert row is not None
    assert row["case_id"] == "hc:2022:7_26:DLHC010111112022"
    assert row["court_name"] == "High Court of Delhi"

    # 16-char CNR -> year pruning + missing court_name enrichment ("" -> "High Court of Bombay")
    ds_cnr, row_cnr = duck.lookup_judgment_by_id("MHHC010222222021")
    assert ds_cnr == "high_court"
    assert row_cnr is not None
    assert row_cnr["court_name"] == "High Court of Bombay"

    # HC neutral citation
    ds_nc, row_nc = duck.lookup_judgment_by_id("2022:DHC:4321")
    assert ds_nc == "high_court"
    assert row_nc is not None
    assert row_nc["cnr"] == "DLHC010111112022"

    # Tribunal case_id with inferred forum prefix (CESTAT) vs fallback across all tribunal partitions
    ds_t1, row_t1 = duck.lookup_judgment_by_id("CESTAT_2023_900")
    assert ds_t1 == "tribunal"
    assert row_t1 is not None and row_t1["title"] == "Cold CESTAT Order v. CCE"

    ds_t2, row_t2 = duck.lookup_judgment_by_id("CUSTOM_FALLBACK_TRIB_1")
    assert ds_t2 == "tribunal"
    assert row_t2 is not None and row_t2["title"] == "Fallback Forum Order v. CCE"

    # Verify manifest_as_of replaces hardcoded Date(2026, 11, 22) in aggregate()
    assert duck._manifest_as_of() == Date(2026, 11, 26)
    agg = duck.aggregate(
        "high_court",
        "court",
        filters={"year_from": 2020, "year_to": 2025},
        sample_cases=1,
    )
    assert agg.provenances[0].as_of == Date(2026, 11, 26)
    assert agg.buckets[0].samples[0].provenance.as_of == Date(2026, 11, 26)


def test_duckdb_simulated_25_hc_partitions_within_memory_and_timeout_budget(
    tmp_path: Path,
) -> None:
    """Verify partition pruning & bounded chunked lookup across 25 HC partitions stay < 256MB / 10s."""
    pq_root = tmp_path / "sim_scale"
    partitions: list[dict[str, Any]] = []
    # 25 High Court partitions x 500 rows each = 12,500 rows
    for idx in range(1, 26):
        c_code = f"{idx}_1"
        rows = {
            "case_id": [f"hc:2024:{c_code}:{r}" for r in range(500)],
            "cnr": [f"ST{idx:02d}{r:08d}2024" for r in range(500)],
            "court_code": [c_code] * 500,
            "court_name": [f"High Court {idx}"] * 500,
            "bench": ["Principal"] * 500,
            "neutral_citation": [f"2024:HC{idx}:{r}" for r in range(500)],
            "title": [f"Party {r} v. State" for r in range(500)],
            "decision_date": ["2024-05-01"] * 500,
            "year": pa.array([2024] * 500, type=pa.int32()),
            "commercial_safe": [True] * 500,
        }
        rel = f"parquet/high_court/court={c_code}/decade=2020s.parquet"
        _write_zstd_parquet(pa.table(rows), pq_root / rel)
        partitions.append(
            {
                "court_code": c_code,
                "court_name": f"High Court {idx}",
                "year_min": 2020,
                "year_max": 2029,
                "rows": 500,
                "s3_key": rel,
            }
        )

    manifest = CorpusManifest.model_validate(
        {
            "schema_version": "2.0",
            "generated_at": "2026-11-26T00:00:00Z",
            "datasets": {
                "high_court": {
                    "license_spdx": "CC-BY-4.0",
                    "commercial_safe": True,
                    "terms_status": "cleared",
                    "row_count": 12500,
                    "partitions": partitions,
                }
            },
        }
    )
    store = DuckDBStore(
        manifest=manifest,
        base_dir=pq_root,
        temp_dir=tmp_path / "duck_scale_tmp",
        offline_only=True,
        query_timeout_s=10.0,
    )
    try:
        t0 = time.perf_counter()
        # 25th court CNR (forces crossing >24 partitions if batched)
        ds, hit = store.lookup_judgment_by_id("ST25000004992024")
        elapsed = time.perf_counter() - t0
        assert ds == "high_court"
        assert hit is not None and hit["case_id"] == "hc:2024:25_1:499"
        assert elapsed < 5.0

        # Pruned HC case_id lookup hits single file
        t1 = time.perf_counter()
        ds2, hit2 = store.lookup_judgment_by_id("hc:2024:25_1:499")
        elapsed2 = time.perf_counter() - t1
        assert ds2 == "high_court" and hit2 is not None
        assert elapsed2 < 2.0

        # Verify DuckDB memory stayed well below 256 MB
        con = store._connect()
        mem_rows = con.execute("SELECT sum(memory_usage_bytes) FROM duckdb_memory()").fetchall()
        used_bytes = int(mem_rows[0][0] or 0)
        assert used_bytes < 256 * 1024 * 1024
    finally:
        store.close()


@pytest.mark.asyncio
async def test_get_judgment_views_lance_and_duckdb_with_bans_and_framing(
    wired_cold_tiers,
) -> None:
    _, _, _, _ = wired_cold_tiers

    # 1. LanceDB HC judgment: overview, paragraphs, search_within, docket
    ov = await get_judgment(identifier="2024:DHC:7777", view="overview")
    assert ov.card.case_id == "hc:2024:7_26:DLHC010555552024"
    assert ov.card.court_tier == "high_court"
    assert ov.card.court == "High Court of Delhi"
    assert ov.extraction_method == "indexed_text"
    assert len(ov.excerpts) == 2
    assert "regular bail" in ov.excerpts[0].text
    assert "Section 45 PMLA" in ov.excerpts[1].text
    for ex in ov.excerpts:
        assert ex.text.startswith("<corpus_text ")
        assert ex.text.rstrip().endswith("</corpus_text>")
    assert ov.card.snippet and ov.card.snippet.startswith("<corpus_text ")

    paras = await get_judgment(
        identifier="DLHC010555552024", view="paragraphs", para_start=2, para_end=2
    )
    assert len(paras.excerpts) == 1
    assert "Section 45 PMLA" in paras.excerpts[0].text

    sw = await get_judgment(
        identifier="hc:2024:7~26:DLHC010555552024",
        view="search_within",
        within_query="prolonged incarceration",
    )
    assert sw.view == "search_within"
    assert any(
        "Section 45 PMLA" in ex.text and (ex.score or 0.0) > 0 for ex in sw.excerpts
    )

    dk = await get_judgment(identifier="CESTAT_2024_501", view="docket")
    assert dk.card.court_tier == "tribunal"
    assert dk.docket is not None
    assert dk.extraction_method == "metadata_only"

    # 2. LanceDB publication ban enforcement
    with pytest.raises(TextUnavailableMetadataOnly):
        await get_judgment(identifier="2024:DHC:8888", view="overview")

    # 3. DuckDB HC & Tribunal judgments: overview + docket work, paragraphs raises TextUnavailableMetadataOnly
    cold_ov = await get_judgment(identifier="DLHC010111112022", view="overview")
    assert cold_ov.card.court == "High Court of Delhi"
    assert cold_ov.card.court_tier == "high_court"
    assert cold_ov.extraction_method == "metadata_only"
    assert cold_ov.provenance.as_of == Date(2026, 11, 26)
    assert cold_ov.excerpts[0].text.startswith("<corpus_text ")

    cold_trib_dk = await get_judgment(identifier="ITAT_2023_44", view="docket")
    assert cold_trib_dk.card.court_tier == "tribunal"
    assert cold_trib_dk.docket is not None
    assert cold_trib_dk.docket.petitioner == "Cold ITAT Tax Matter"

    with pytest.raises(TextUnavailableMetadataOnly):
        await get_judgment(identifier="ITAT_2023_44", view="paragraphs")

    # 4. DuckDB publication ban enforcement (title has in-camera / sealed cover)
    with pytest.raises(TextUnavailableMetadataOnly):
        await get_judgment(identifier="MHHC010222222021", view="overview")


@pytest.mark.asyncio
async def test_get_judgments_batch_across_sqlite_lance_and_duckdb(
    wired_cold_tiers,
) -> None:
    batch = await get_judgments_batch(
        identifiers=[
            "2017 INSC 801",  # SQLite SC
            "2024:DHC:7777",  # LanceDB HC
            "2024 CESTAT 501",  # LanceDB Tribunal
            "DLHC010111112022",  # DuckDB HC
            "ITAT_2023_44",  # DuckDB Tribunal
            "2024:DHC:999999",  # Missing
        ],
        view="overview",
    )
    assert batch.total_requested == 6
    assert batch.returned == 5
    assert batch.not_found == ["2024:DHC:999999"]
    returned_ids = {r.card.case_id for r in batch.results}
    assert returned_ids == {
        "sc:2017:puttaswamy",
        "hc:2024:7_26:DLHC010555552024",
        "CESTAT_2024_501",
        "hc:2022:7_26:DLHC010111112022",
        "ITAT_2023_44",
    }
    for item in batch.results:
        for ex in item.excerpts:
            assert ex.text.startswith("<corpus_text ")
            assert ex.text.rstrip().endswith("</corpus_text>")


@pytest.mark.asyncio
async def test_verify_citations_resolves_hc_and_tribunal_cold_tiers(
    wired_cold_tiers,
) -> None:
    res = await verify_citations(
        text="See 2024:DHC:7777, DLHC010111112022, and 2024:DHC:999999.",
        verify_quotes=[
            {
                "citation": "2024:DHC:7777",
                "quote": "Twin conditions under Section 45 PMLA cannot override prolonged incarceration.",
            },
            {
                "citation": "CESTAT_2024_501",
                "quote": "Extended period of limitation cannot be invoked absent wilful suppression.",
            },
        ],
    )
    text_findings = [f for f in res.findings if f.status in ("VERIFIED", "NOT_FOUND_IN_CORPUS")]
    by_raw = {f.raw_citation: f for f in text_findings}
    assert by_raw["2024:DHC:7777"].status == "VERIFIED"
    assert by_raw["DLHC010111112022"].status == "VERIFIED"
    assert by_raw["2024:DHC:999999"].status == "NOT_FOUND_IN_CORPUS"

    quote_findings = [f for f in res.findings if f.status == "QUOTE_VERIFIED"]
    assert len(quote_findings) == 2
    assert {f.matched_para_num for f in quote_findings} == {10, 7}
    for qf in quote_findings:
        assert qf.matched_excerpt and qf.matched_excerpt.startswith("<corpus_text ")


def test_enrich_corpus_status_updates_as_of_from_manifest(
    wired_cold_tiers, tmp_path: Path
) -> None:
    # resp.as_of older than manifest.generated_at (2026-11-26) -> updated to 2026-11-26
    resp_older = CorpusStatusResponse(
        as_of=Date(2026, 11, 20),
        counts=CorpusCounts(),
    )
    enriched = enrich_corpus_status(resp_older)
    assert enriched.as_of == Date(2026, 11, 26)
    assert enriched.tiers["lance_fts"] == "ok"
    assert enriched.tiers["duckdb_parquet"] == "ok"

    # resp.as_of is None -> set to manifest date
    resp_none = CorpusStatusResponse(as_of=None, counts=CorpusCounts())
    enriched_none = enrich_corpus_status(resp_none)
    assert enriched_none.as_of == Date(2026, 11, 26)

    # resp.as_of newer than manifest date -> preserved
    resp_newer = CorpusStatusResponse(as_of=Date(2026, 12, 1), counts=CorpusCounts())
    enriched_newer = enrich_corpus_status(resp_newer)
    assert enriched_newer.as_of == Date(2026, 12, 1)


def test_resolve_judgment_across_tiers_handles_storage_unavailable(tmp_path: Path) -> None:
    paths = build_mini_corpus(tmp_path / "mini")
    storage.set_backend(InMemoryCorpusBackend.from_sample_data())

    class _BrokenLance:
        def lookup_judgment_by_id(self, *_args: Any, **_kwargs: Any):
            raise StorageUnavailable("lance down")

    class _BrokenAnalytics:
        def lookup_judgment_by_id(self, *_args: Any, **_kwargs: Any):
            raise StorageUnavailable("duck down")

    class _BrokenRetrieval:
        lance = _BrokenLance()

    storage.set_retrieval(_BrokenRetrieval())  # type: ignore[arg-type]
    storage.set_analytics(_BrokenAnalytics())  # type: ignore[arg-type]

    assert resolve_judgment_across_tiers("2024:DHC:999999", commercial_safe_only=False) == []

    # Also ensure missing mini corpus SQLite doesn't mask NotFound when cold tiers empty
    storage.set_backend(SqliteStore(paths["sqlite"]))
    assert resolve_judgment_across_tiers("2024:DHC:999999", commercial_safe_only=False) == []
