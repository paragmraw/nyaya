"""Hermetic unit tests for ``nyaya.storage.lance_store`` (native Rust FTS)."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import lancedb
import pytest

from nyaya.config import Settings
from nyaya.exceptions import StorageUnavailable
from nyaya.storage import _build_lance, build_lance_storage_options
from nyaya.storage.lance_store import LanceStore
from tests.fixtures.mini_corpus.builder import build_mini_corpus


@pytest.fixture(scope="module")
def corpus_paths(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    return build_mini_corpus(tmp_path_factory.mktemp("lance_corpus"))


@pytest.fixture
def lance_store(corpus_paths: dict[str, Path]) -> LanceStore:
    return LanceStore(local_dir=corpus_paths["lancedb"], offline_only=True)


def test_lance_native_fts_search_across_3_tables(lance_store: LanceStore) -> None:
    sc_hits = lance_store.search_sc("basic structure Constitution", limit=5)
    assert len(sc_hits) >= 1
    assert sc_hits[0]["case_id"] == "sc:1973:kesavananda"

    hc_hits = lance_store.search_hc("anticipatory bail 438", court_code="7_26", limit=5)
    assert len(hc_hits) == 1
    assert hc_hits[0]["case_id"] == "hc:2024:dhc:1234"

    state_hits = lance_store.search_state_regulators("material disclosures Regulation 30", limit=5)
    assert len(state_hits) >= 1
    assert state_reg_match(state_hits, "SEBI-LODR")


def state_reg_match(hits: list[dict], short_name: str) -> bool:
    return any(h.get("short_name") == short_name for h in hits)


def test_lance_commercial_safe_only_excludes_ildc(lance_store: LanceStore) -> None:
    unfiltered = lance_store.search_sc("ILDC non-commercial", commercial_safe_only=False, limit=5)
    assert any(h["case_id"] == "ildc:2020:001" for h in unfiltered)

    filtered = lance_store.search_sc("ILDC non-commercial", commercial_safe_only=True, limit=5)
    assert not any(h["case_id"] == "ildc:2020:001" for h in filtered)


def test_lance_sql_escape_handles_quotes_safely(lance_store: LanceStore) -> None:
    # Single quote in filter must not crash LanceDB filter parser
    hits = lance_store.search_hc("bail", court_code="7' OR '1'='1", limit=5)
    assert hits == []


def test_lance_unavailable_raises_storage_unavailable(tmp_path: Path) -> None:
    missing = LanceStore(local_dir=tmp_path / "does_not_exist", offline_only=True)
    with pytest.raises(StorageUnavailable):
        missing.search_sc("privacy")


def _settings(tmp_path: Path, *, url_style: str = "vhost", offline_only: bool = False) -> Settings:
    return Settings(
        data_dir=tmp_path / "data",
        cache_dir=tmp_path / "cache",
        bucket_endpoint="https://t3.storageapi.dev",
        bucket_name="nyaya-prod-bucket",
        bucket_access_key_id="tid_abc",
        bucket_secret_access_key="tsec_xyz",
        bucket_region="auto",
        bucket_url_style="vhost" if url_style == "vhost" else "path",
        manifest_url=None,
        commercial_safe_only=False,
        offline_only=offline_only,
        duckdb_ext_dir=None,
        log_level="INFO",
        port=8000,
        redis_url=None,
        trusted_proxy_hops=1,
    )


def test_build_lance_s3_bucket_connection_and_storage_options(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings_vhost = _settings(tmp_path, url_style="vhost")
    settings_vhost.data_dir.mkdir(parents=True)  # Note: no lancedb/ subdir

    opts_vhost = build_lance_storage_options(settings_vhost)
    assert opts_vhost["aws_endpoint"] == "https://nyaya-prod-bucket.t3.storageapi.dev"
    assert opts_vhost["aws_access_key_id"] == "tid_abc"
    assert opts_vhost["aws_secret_access_key"] == "tsec_xyz"
    assert opts_vhost["aws_region"] == "auto"
    assert opts_vhost["aws_virtual_hosted_style_request"] == "true"

    opts_path = build_lance_storage_options(_settings(tmp_path, url_style="path"))
    assert opts_path["aws_endpoint"] == "https://t3.storageapi.dev"
    assert opts_path["aws_virtual_hosted_style_request"] == "false"

    store = _build_lance(settings_vhost)
    assert store.bucket_uri == "s3://nyaya-prod-bucket/lancedb"
    assert store.is_available() is True

    connect_calls: list[tuple[str, dict[str, Any] | None]] = []

    class _FakeDB:
        def list_tables(self) -> Any:
            return SimpleNamespace(tables=["sc_chunks", "hc_hot_chunks", "state_acts_regulators"])

    def _fake_connect(uri: str, **kwargs: Any) -> _FakeDB:
        connect_calls.append((uri, kwargs.get("storage_options")))
        return _FakeDB()

    monkeypatch.setattr(lancedb, "connect", _fake_connect)
    health = store.health()
    assert health["status"] == "ok"
    assert "sc_chunks" in health["tables"]
    assert connect_calls == [("s3://nyaya-prod-bucket/lancedb", opts_vhost)]


def test_lance_health_states(tmp_path: Path, lance_store: LanceStore) -> None:
    assert LanceStore(local_dir=tmp_path / "missing").health() == {"status": "unconfigured"}
    assert LanceStore(
        local_dir=tmp_path / "missing",
        bucket_uri="s3://nyaya-prod-bucket/lancedb",
        offline_only=True,
    ).health() == {"status": "unavailable"}

    h_ok = lance_store.health()
    assert h_ok["status"] == "ok"
    assert set(h_ok["tables"]) >= {"sc_chunks", "hc_hot_chunks", "state_acts_regulators"}
