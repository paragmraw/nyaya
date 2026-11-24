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
