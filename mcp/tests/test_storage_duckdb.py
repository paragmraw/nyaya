"""Hermetic unit tests for ``nyaya.storage.duckdb_store`` and ``manifest.py``."""

from __future__ import annotations

from pathlib import Path

import pytest

from nyaya.config import Settings
from nyaya.exceptions import (
    CommercialLicenseBlocked,
    InvalidFilter,
    QueryTimeout,
    QueryTooBroad,
)
from nyaya.storage.duckdb_store import (
    DuckDBStore,
    build_s3_secret_sql,
    normalize_duckdb_endpoint,
)
from nyaya.storage.manifest import load_manifest, resolve_partitions
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
