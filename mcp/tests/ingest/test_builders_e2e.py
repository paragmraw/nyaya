"""End-to-end hermetic builder tests for sqlite_core, lance_fts, and parquet_compact."""

from __future__ import annotations

from pathlib import Path

import duckdb

from nyaya.ingest.builders.lance_fts import build_lance_fts_tables
from nyaya.ingest.builders.parquet_compact import build_compacted_parquets
from nyaya.ingest.builders.sqlite_core import build_sqlite_core, create_mini_ingest_input
from nyaya.storage.lance_store import LanceStore
from nyaya.storage.manifest import load_manifest, verify_partition_sha256
from nyaya.storage.sqlite_store import SqliteStore


def test_builders_end_to_end_sqlite_lance_and_parquet(tmp_path: Path) -> None:
    input_data = create_mini_ingest_input()

    # 1. Build SQLite Core
    sqlite_out = tmp_path / "nyaya-core.sqlite"
    sha_path = build_sqlite_core(sqlite_out, input_data=input_data)
    assert sqlite_out.is_file()
    assert sha_path.is_file()

    store = SqliteStore(sqlite_out)
    try:
        # FTS5 statute search works
        hits = store.search_statutes_fts("basic structure equality", limit=5)
        assert len(hits) >= 1
        assert any(h.act == "Constitution" for h in hits)

        # All 106 amendments loaded from amendments_1_106.yaml
        hist = store.get_provision_history("Constitution")
        assert hist.total >= 106

        # Verified criminal concordance loaded from criminal_concordance.yaml
        conc = store.compare_provisions("IPC", "420")
        assert conc
        assert any(c.new_ref and c.new_ref.ref == "318(4)" for c in conc)

        # Citation graph edges and provenance present
        status = store.corpus_status()
        assert status.counts.amendments >= 106
        assert status.counts.concordance_mappings >= 30
        assert status.counts.judicial_invalidations >= 7
        assert status.counts.citation_edges >= 4
    finally:
        store.close()

    # 2. Build LanceDB Native Rust FTS (3 tables: sc_chunks, hc_hot_chunks, state_acts_regulators)
    lance_dir = tmp_path / "lancedb"
    build_lance_fts_tables(lance_dir, input_data=input_data, commercial_safe_only=False)

    lstore = LanceStore(local_dir=lance_dir)
    try:
        sc_all = lstore.search_sc("sentencing appeal murder", commercial_safe_only=False)
        assert any(r["source_id"] == "ildc" for r in sc_all)

        # commercial_safe_only=True filters out ILDC non-commercial rows
        sc_cs = lstore.search_sc("sentencing appeal murder", commercial_safe_only=True)
        assert all(r["commercial_safe"] is True for r in sc_cs)
        assert all(r["source_id"] != "ildc" for r in sc_cs)

        hc_hits = lstore.search_hc("anticipatory bail liberty")
        assert len(hc_hits) >= 1

        sr_hits = lstore.search_state_regulators("disclosure listed entity")
        assert len(sr_hits) >= 1
    finally:
        lstore.close()

    # Also verify building with commercial_safe_only=True omits NC rows at build time
    lance_cs_dir = tmp_path / "lancedb_cs"
    build_lance_fts_tables(lance_cs_dir, input_data=input_data, commercial_safe_only=True)
    lstore_cs = LanceStore(local_dir=lance_cs_dir)
    try:
        assert all(
            r["commercial_safe"] is True
            for r in lstore_cs.search_sc("sentencing appeal murder", commercial_safe_only=False)
        )
    finally:
        lstore_cs.close()

    # 3. Build Compacted ZSTD Parquets + manifest.json
    manifest_path = build_compacted_parquets(tmp_path, input_data=input_data)
    assert manifest_path.is_file()
    manifest = load_manifest(manifest_path)

    assert set(manifest.datasets.keys()) >= {
        "high_court",
        "district_court",
        "tribunal",
        "supreme_court",
        "regulator",
    }
    # Verify District Court DDL metadata is tagged commercial_safe=False and ML columns stripped
    ddl_ds = manifest.datasets["district_court"]
    assert ddl_ds.commercial_safe is False
    assert ddl_ds.license_spdx == "CC-BY-NC-SA-4.0"
    assert ddl_ds.terms_status == "non_commercial_only"

    for ds_name, ds in manifest.datasets.items():
        for part in ds.partitions:
            part_file = tmp_path / part.s3_key
            assert part_file.is_file(), f"Missing partition {part_file} for {ds_name}"
            assert verify_partition_sha256(part_file, part.sha256)

    ddl_part_path = tmp_path / ddl_ds.partitions[0].s3_key
    con = duckdb.connect(":memory:")
    try:
        cols = [
            r[0]
            for r in con.execute(
                f"DESCRIBE SELECT * FROM read_parquet('{str(ddl_part_path).replace(chr(39), chr(39)*2)}')"
            ).fetchall()
        ]
        assert "predicted_religion" not in cols
        assert "predicted_caste" not in cols
        assert "judge_predicted_religion" not in cols
    finally:
        con.close()
