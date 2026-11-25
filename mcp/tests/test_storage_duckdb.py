"""Hermetic unit tests for ``nyaya.storage.duckdb_store`` and ``manifest.py``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import duckdb
import pytest

from nyaya import storage as storage_pkg
from nyaya.config import Settings
from nyaya.exceptions import (
    CommercialLicenseBlocked,
    InvalidFilter,
    QueryTimeout,
    QueryTooBroad,
)
from nyaya.storage.duckdb_store import (
    DuckDBStore,
    _select_diverse_partitions,
    build_s3_secret_sql,
    normalize_duckdb_endpoint,
)
from nyaya.storage.manifest import CorpusManifest, load_manifest, resolve_partitions
from tests.fixtures.mini_corpus.builder import build_mini_corpus


@pytest.fixture(scope="module")
def corpus_paths(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    return build_mini_corpus(tmp_path_factory.mktemp("duck_corpus"))


@pytest.fixture
def duckdb_store(corpus_paths: dict[str, Path], tmp_path: Path):
    manifest = load_manifest(corpus_paths["manifest"])
    store = DuckDBStore(
        manifest=manifest,
        base_dir=corpus_paths["root"],
        temp_dir=tmp_path / "duck_tmp",
        offline_only=True,
    )
    try:
        yield store
    finally:
        store.close()


def test_normalize_endpoint_and_tigris_vhost_scoped_secret(tmp_path: Path) -> None:
    host, use_ssl = normalize_duckdb_endpoint("https://t3.storageapi.dev")
    assert host == "t3.storageapi.dev"
    assert use_ssl is True

    settings = Settings(
        data_dir=tmp_path,
        cache_dir=tmp_path,
        bucket_endpoint="https://t3.storageapi.dev",
        bucket_name="nyaya-prod-bucket",
        bucket_access_key_id="tid_abc",
        bucket_secret_access_key="tsec_xyz",
        bucket_region="auto",
        bucket_url_style="vhost",
        manifest_url=None,
        commercial_safe_only=False,
        offline_only=False,
        duckdb_ext_dir=None,
        log_level="INFO",
        port=8000,
        redis_url=None,
        trusted_proxy_hops=1,
    )
    sql = build_s3_secret_sql(settings)
    assert sql is not None
    assert "https://" not in sql
    assert "ENDPOINT 't3.storageapi.dev'" in sql
    assert "URL_STYLE 'vhost'" in sql
    assert "SCOPE 's3://nyaya-prod-bucket'" in sql


def test_duckdb_autoinstall_disabled_on_init(duckdb_store: DuckDBStore) -> None:
    con = duckdb_store._connect()
    autoinstall = con.execute(
        "SELECT value FROM duckdb_settings() WHERE name = 'autoinstall_known_extensions'"
    ).fetchone()
    autoload = con.execute(
        "SELECT value FROM duckdb_settings() WHERE name = 'autoload_known_extensions'"
    ).fetchone()
    assert autoinstall is not None and str(autoinstall[0]).lower() == "false"
    assert autoload is not None and str(autoload[0]).lower() == "false"


def test_manifest_partition_pruning_and_duckdb_search(
    duckdb_store: DuckDBStore,
    corpus_paths: dict[str, Path],
) -> None:
    manifest = load_manifest(corpus_paths["manifest"])
    parts = resolve_partitions(
        manifest,
        "high_court",
        court="7_26",
        year_from=2022,
        year_to=2025,
        base_dir=corpus_paths["root"],
    )
    assert len(parts) == 1
    assert "court=7_26" in parts[0]

    rows, total = duckdb_store.search_judgments(
        tier="high_court",
        court="7_26",
        year_from=2023,
        year_to=2025,
    )
    assert total == 2
    assert len(rows) == 2


def test_duckdb_query_too_broad_and_commercial_blocked(duckdb_store: DuckDBStore) -> None:
    with pytest.raises(QueryTooBroad):
        duckdb_store.search_judgments(tier="high_court")

    with pytest.raises(CommercialLicenseBlocked) as exc_info:
        duckdb_store.aggregate(
            "district_court",
            "year",
            filters={"state": "MH"},
            commercial_safe_only=True,
        )
    assert exc_info.value.license_spdx == "CC-BY-NC-SA-4.0"

    # Unfiltered district_court when commercial_safe_only=False still requires state or year window
    with pytest.raises(QueryTooBroad):
        duckdb_store.search_judgments(tier="district_court", commercial_safe_only=False)


def test_duckdb_analytics_closed_schema_and_timeout(
    corpus_paths: dict[str, Path],
    tmp_path: Path,
) -> None:
    manifest = load_manifest(corpus_paths["manifest"])
    store = DuckDBStore(
        manifest=manifest,
        base_dir=corpus_paths["root"],
        temp_dir=tmp_path / "duck_tmp",
        offline_only=True,
        query_timeout_s=0.02,
    )
    try:
        res = store.aggregate("high_court", "court", filters={"year_from": 2020, "year_to": 2025})
        assert res.total_matched == 3
        assert len(res.buckets) == 2

        with pytest.raises(InvalidFilter):
            store.aggregate("high_court", "drop_table_hack", filters={"court": "7_26"})

        # Trigger con.interrupt() timeout using a deliberately slow cross-product query
        with pytest.raises(QueryTimeout):
            store._execute_with_timeout(
                "SELECT count(*) FROM range(5000000) a, range(5000000) b"
            )
    finally:
        store.close()


def _bucket_settings(
    tmp_path: Path,
    *,
    offline_only: bool = False,
    manifest_url: str | None = None,
    duckdb_ext_dir: Path | None = None,
) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        bucket_endpoint="https://t3.storageapi.dev",
        bucket_name="nyaya-prod-bucket",
        bucket_access_key_id="tid_abc",
        bucket_secret_access_key="tsec_xyz",
        bucket_region="auto",
        bucket_url_style="vhost",
        manifest_url=manifest_url,
        commercial_safe_only=False,
        offline_only=offline_only,
        duckdb_ext_dir=duckdb_ext_dir,
        log_level="INFO",
        port=8000,
        redis_url=None,
        trusted_proxy_hops=1,
    )


def test_fetch_bucket_manifest_caches_and_loads_ext_dir(
    tmp_path: Path,
    corpus_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ext_dir = tmp_path / "duckdb_ext"
    ext_dir.mkdir()
    settings = _bucket_settings(tmp_path, duckdb_ext_dir=ext_dir)
    settings.data_dir.mkdir(parents=True)
    manifest_raw = corpus_paths["manifest"].read_text(encoding="utf-8")

    executed_sql: list[tuple[str, Any]] = []

    class _FakeCursor:
        def __init__(self, row: tuple[str] | None) -> None:
            self._row = row

        def fetchone(self) -> tuple[str] | None:
            return self._row

    class _FakeDuckConn:
        def execute(self, sql: str, params: Any = None) -> _FakeCursor:
            executed_sql.append((sql, params))
            if "read_text" in sql:
                assert params == ["s3://nyaya-prod-bucket/current/manifest.json"]
                return _FakeCursor((manifest_raw,))
            return _FakeCursor(None)

        def close(self) -> None:
            return None

    monkeypatch.setattr(duckdb, "connect", lambda *_args, **_kwargs: _FakeDuckConn())

    manifest = storage_pkg._load_optional_manifest(settings)
    assert manifest is not None
    assert manifest.datasets["high_court"].row_count >= 1
    cached = settings.cache_dir / "manifest.json"
    assert cached.is_file()
    assert CorpusManifest.model_validate_json(cached.read_text(encoding="utf-8")).datasets

    sql_joined = "\n".join(s for s, _ in executed_sql)
    assert "SET extension_directory=" in sql_joined
    assert "LOAD httpfs" in sql_joined
    assert "CREATE OR REPLACE SECRET nyaya_bucket" in sql_joined


def test_load_optional_manifest_precedence_and_fallback(
    tmp_path: Path,
    corpus_paths: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_raw = corpus_paths["manifest"].read_text(encoding="utf-8")
    settings = _bucket_settings(tmp_path, manifest_url="https://example.invalid/manifest.json")
    settings.data_dir.mkdir(parents=True)

    # 1. Local data_dir/manifest.json overrides remote bucket
    (settings.data_dir / "manifest.json").write_text(manifest_raw, encoding="utf-8")
    called = {"bucket": 0}

    def _boom_bucket(_s: Settings) -> CorpusManifest:
        called["bucket"] += 1
        raise RuntimeError("bucket down")

    monkeypatch.setattr(storage_pkg, "fetch_bucket_manifest", _boom_bucket)
    local_loaded = storage_pkg._load_optional_manifest(settings)
    assert local_loaded is not None
    assert called["bucket"] == 0

    # 2. Removing local manifest tries bucket, then falls back cleanly to manifest_url
    (settings.data_dir / "manifest.json").unlink()
    fallback_manifest = load_manifest(corpus_paths["manifest"])
    monkeypatch.setattr(
        storage_pkg,
        "load_manifest",
        lambda src, **_kw: fallback_manifest if str(src).startswith("https://") else load_manifest(src),
    )
    remote_loaded = storage_pkg._load_optional_manifest(settings)
    assert remote_loaded is fallback_manifest
    assert called["bucket"] == 1

    # 3. If both bucket and manifest_url fail, returns None without crashing
    def _boom_load(*_args: Any, **_kwargs: Any) -> CorpusManifest:
        raise RuntimeError("manifest_url down")

    monkeypatch.setattr(storage_pkg, "load_manifest", _boom_load)
    assert storage_pkg._load_optional_manifest(settings) is None


def test_duckdb_store_health_and_manifest_counts(
    corpus_paths: dict[str, Path],
    tmp_path: Path,
) -> None:
    unconf = DuckDBStore(manifest=None, offline_only=True)
    assert unconf.health() == {"status": "unconfigured"}
    assert unconf.manifest_counts() == {}

    remote_unavail = DuckDBStore(manifest=None, settings=_bucket_settings(tmp_path), offline_only=False)
    assert remote_unavail.health() == {"status": "unavailable"}

    manifest = load_manifest(corpus_paths["manifest"])
    store = DuckDBStore(manifest=manifest, base_dir=corpus_paths["root"], offline_only=True)
    try:
        h = store.health()
        assert h["status"] == "ok"
        assert "high_court" in h["datasets"]

        counts_all = store.manifest_counts(commercial_safe_only=False)
        assert counts_all["hc_judgments"] >= 1
        assert counts_all["sc_judgments"] >= 1
        assert counts_all["tribunal_orders"] >= 1
        assert counts_all["district_court_records"] >= 1

        counts_cs = store.manifest_counts(commercial_safe_only=True)
        assert "district_court_records" not in counts_cs
        assert counts_cs["hc_judgments"] == counts_all["hc_judgments"]
    finally:
        store.close()


def test_select_diverse_partitions_round_robin_and_manifest_jurisdiction() -> None:
    # 30 courts x 3 years in alphabetical court order (like S3 HC listing)
    uris = [
        f"s3://nyaya/parquet/hc/court={court_idx:02d}_1/year={yr}/part.parquet"
        for court_idx in range(1, 31)
        for yr in (2022, 2023, 2024)
    ]
    selected = _select_diverse_partitions(uris, max_partitions=24)
    assert len(selected) == 24
    # First 24 picks must be 24 distinct courts, each at newest year 2024
    assert all("year=2024" in u for u in selected)
    courts_seen = {u.split("court=")[1].split("/")[0] for u in selected}
    assert len(courts_seen) == 24


def test_duckdb_word_boundary_query_and_state_normalization(
    corpus_paths: dict[str, Path],
    tmp_path: Path,
) -> None:
    import json

    import pyarrow as pa

    from tests.fixtures.mini_corpus.builder import _write_zstd_parquet

    manifest = load_manifest(corpus_paths["manifest"])
    mh_parts = resolve_partitions(
        manifest,
        "district_court",
        state="Maharashtra",
        year_from=2015,
        year_to=2016,
        commercial_safe_only=False,
        base_dir=corpus_paths["root"],
    )
    assert len(mh_parts) == 1
    assert "state=MH" in mh_parts[0]

    # Build a small HC parquet partition testing ABA/ABAIL vs BAILI
    pq_dir = tmp_path / "hc_p0"
    hc_tbl = pa.table(
        {
            "case_id": ["hc:aba:1", "hc:abail:2", "hc:baili:3", "hc:bail:4"],
            "cnr": ["DLHC01", "DLHC02", "DLHC03", "DLHC04"],
            "court_code": ["7_26", "7_26", "7_26", "7_26"],
            "court_name": ["High Court of Delhi"] * 4,
            "bench": ["Principal"] * 4,
            "neutral_citation": ["2024:DHC:1", "2024:DHC:2", "2024:DHC:3", "2024:DHC:4"],
            "title": ["X v. State", "Y v. State", "Baili Ram v. State", "Z v. State"],
            "petitioner": ["X", "Y", "Baili Ram", "Z"],
            "respondent": ["State"] * 4,
            "judge": ["Judge A"] * 4,
            "author_judge": ["Judge A"] * 4,
            "bench_strength": pa.array([1, 1, 1, 1], type=pa.int32()),
            "decision_date": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-04"],
            "year": pa.array([2024, 2024, 2024, 2024], type=pa.int32()),
            "disposal_nature": ["Allowed"] * 4,
            "case_type": ["ABA 123/2024", "ABAIL 456/2024", "RSA 99/2024", "Bail Application 7/2024"],
            "act": ["CrPC", "BNSS", "CPC", "CrPC"],
            "commercial_safe": [True, True, True, True],
        }
    )
    rel = "parquet/high_court/court=7_26/year=2024.parquet"
    _write_zstd_parquet(hc_tbl, pq_dir / rel)
    m_path = pq_dir / "manifest.json"
    m_path.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "generated_at": "2026-11-22T00:00:00Z",
                "datasets": {
                    "high_court": {
                        "license_spdx": "CC-BY-4.0",
                        "commercial_safe": True,
                        "terms_status": "cleared",
                        "row_count": 4,
                        "partitions": [
                            {
                                "court_code": "7_26",
                                "court_name": "High Court of Delhi",
                                "year_min": 2024,
                                "year_max": 2024,
                                "rows": 4,
                                "s3_key": rel,
                            }
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    store = DuckDBStore(
        manifest=load_manifest(m_path),
        base_dir=pq_dir,
        temp_dir=tmp_path / "duck_tmp2",
        offline_only=True,
    )
    try:
        aba_rows, aba_total = store.search_judgments(
            tier="high_court", court="7_26", query="anticipatory bail"
        )
        assert aba_total == 2
        assert {r["case_id"] for r in aba_rows} == {"hc:aba:1", "hc:abail:2"}

        bail_rows, bail_total = store.search_judgments(
            tier="high_court", court="7_26", query="bail"
        )
        assert bail_total == 1
        assert [r["case_id"] for r in bail_rows] == ["hc:bail:4"]
    finally:
        store.close()


def test_court_display_name_all_hc_and_dattam_codes() -> None:
    from nyaya.citations import court_display_name

    assert court_display_name("SC") == "Supreme Court of India"
    assert court_display_name("sci") == "Supreme Court of India"
    assert court_display_name("14_25") == "High Court of Manipur"
    assert court_display_name("14~25") == "High Court of Manipur"
    assert court_display_name("man") == "High Court of Manipur"
    assert court_display_name("16_20") == "High Court of Tripura"
    assert court_display_name("16~20") == "High Court of Tripura"
    assert court_display_name("tri") == "High Court of Tripura"
    assert court_display_name(" 7~26 ") == "High Court of Delhi"
    assert court_display_name("dhc") == "High Court of Delhi"
    assert court_display_name(None) is None
    assert court_display_name("   ") is None
    assert court_display_name("99_99") is None

    all_hc_pairs = [
        ("AHC", "9_13", "9~13", "High Court of Judicature at Allahabad"),
        ("APHC", "28_2", "28~2", "High Court of Andhra Pradesh"),
        ("BHC", "27_1", "27~1", "High Court of Bombay"),
        ("CAL", "19_16", "19~16", "High Court at Calcutta"),
        ("CGHC", "22_18", "22~18", "High Court of Chhattisgarh"),
        ("DHC", "7_26", "7~26", "High Court of Delhi"),
        ("GAU", "18_6", "18~6", "Gauhati High Court"),
        ("GUJ", "24_17", "24~17", "High Court of Gujarat"),
        ("HPHC", "2_5", "2~5", "High Court of Himachal Pradesh"),
        ("JKHC", "1_12", "1~12", "High Court of Jammu & Kashmir and Ladakh"),
        ("JHC", "20_7", "20~7", "High Court of Jharkhand"),
        ("KAR", "29_3", "29~3", "High Court of Karnataka"),
        ("KER", "32_4", "32~4", "High Court of Kerala"),
        ("MPHC", "23_23", "23~23", "High Court of Madhya Pradesh"),
        ("MAD", "33_10", "33~10", "High Court of Judicature at Madras"),
        ("MAN", "14_25", "14~25", "High Court of Manipur"),
        ("MEG", "17_21", "17~21", "High Court of Meghalaya"),
        ("ORI", "21_11", "21~11", "High Court of Orissa"),
        ("PAT", "10_8", "10~8", "High Court of Judicature at Patna"),
        ("PHC", "3_22", "3~22", "High Court of Punjab and Haryana"),
        ("RAJ", "8_9", "8~9", "High Court of Judicature for Rajasthan"),
        ("SIK", "11_24", "11~24", "High Court of Sikkim"),
        ("TSHC", "36_29", "36~29", "High Court for the State of Telangana"),
        ("TRI", "16_20", "16~20", "High Court of Tripura"),
        ("UKHC", "5_15", "5~15", "High Court of Uttarakhand"),
    ]
    for canon, us_code, tilde_code, expected in all_hc_pairs:
        assert court_display_name(canon) == expected
        assert court_display_name(us_code) == expected
        assert court_display_name(tilde_code) == expected


def test_duckdb_cross_hc_diversity_and_strict_anticipatory_bail(tmp_path: Path) -> None:
    import json

    import pyarrow as pa

    from tests.fixtures.mini_corpus.builder import _write_zstd_parquet

    pq_dir = tmp_path / "hc_diversity"
    rows = [
        # 4 newer Manipur (14_25) rows that would sweep top 4 under raw year/date sort
        ("hc:man:1", "MN01", "14_25", "14_25", "A v. State", "AB/11/2023", "CrPC", "2025-04-01", 2025),
        ("hc:man:2", "MN02", "14_25", "", "B v. State", "AB No. 12", "CrPC", "2025-03-01", 2025),
        ("hc:man:3", "MN03", "14_25", "14_25", "C v. State", "ABA 99/2025", "BNSS", "2025-02-01", 2025),
        # Plain BAIL APPLN. without anticipatory/438/482/AB signal must be rejected
        ("hc:man:plain", "MN04", "14_25", "14_25", "Plain v. State", "BAIL APPLN. 77/2025", "CrPC", "2025-05-01", 2025),
        # Other High Courts (older dates)
        ("hc:tri:1", "TR01", "16_20", "16_20", "D v. State", "AB 15 of 2024", "CrPC", "2024-11-01", 2024),
        ("hc:dhc:1", "DL01", "7_26", "", "E v. State", "BAIL APPLN. 1/2024", "s.438 CrPC", "2024-10-01", 2024),
        ("hc:bhc:1", "MH01", "27_1", "High Court of Bombay", "Pre-arrest bail plea", "ABA 10/2024", "BNSS 482", "2024-09-01", 2024),
        ("hc:cal:1", "WB01", "19_16", "19_16", "Apprehending arrest CRM(A)", "CRM(A) 5/2024", "Section 438", "2024-08-01", 2024),
    ]
    tbl = pa.table(
        {
            "case_id": [r[0] for r in rows],
            "cnr": [r[1] for r in rows],
            "court_code": [r[2] for r in rows],
            "court_name": [r[3] for r in rows],
            "bench": ["Principal"] * len(rows),
            "neutral_citation": [f"NC:{i}" for i in range(len(rows))],
            "title": [r[4] for r in rows],
            "petitioner": ["P"] * len(rows),
            "respondent": ["State"] * len(rows),
            "judge": ["J"] * len(rows),
            "author_judge": ["J"] * len(rows),
            "bench_strength": pa.array([1] * len(rows), type=pa.int32()),
            "decision_date": [r[7] for r in rows],
            "year": pa.array([r[8] for r in rows], type=pa.int32()),
            "disposal_nature": ["Allowed"] * len(rows),
            "case_type": [r[5] for r in rows],
            "act": [r[6] for r in rows],
            "commercial_safe": [True] * len(rows),
        }
    )
    rel = "parquet/high_court/multi.parquet"
    _write_zstd_parquet(tbl, pq_dir / rel)
    m_path = pq_dir / "manifest.json"
    m_path.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "generated_at": "2026-11-22T00:00:00Z",
                "datasets": {
                    "high_court": {
                        "license_spdx": "CC-BY-4.0",
                        "commercial_safe": True,
                        "terms_status": "cleared",
                        "row_count": len(rows),
                        "partitions": [
                            {
                                "court_code": "14_25",
                                "court_name": "High Court of Manipur",
                                "year_min": 2024,
                                "year_max": 2025,
                                "rows": len(rows),
                                "s3_key": rel,
                            }
                        ],
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    store = DuckDBStore(
        manifest=load_manifest(m_path),
        base_dir=pq_dir,
        temp_dir=tmp_path / "duck_tmp_div",
        offline_only=True,
    )
    try:
        res_rows, total = store.search_judgments(
            tier="high_court",
            query="anticipatory bail",
            year_from=2024,
            year_to=2025,
            limit=5,
        )
        assert total == 7
        assert len(res_rows) == 5
        # Plain BAIL APPLN. rejected
        assert all(r["case_id"] != "hc:man:plain" for r in res_rows)
        # Window-function diversity puts 5 distinct High Courts in top 5
        assert len({r["court_code"] for r in res_rows}) == 5
        # Court display names enriched
        by_code = {r["court_code"]: r["court_name"] for r in res_rows}
        assert by_code["14_25"] == "High Court of Manipur"
        assert by_code["16_20"] == "High Court of Tripura"
        assert by_code["7_26"] == "High Court of Delhi"
        assert by_code["19_16"] == "High Court at Calcutta"
    finally:
        store.close()
