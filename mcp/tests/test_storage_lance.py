"""Hermetic unit tests for ``nyaya.storage.lance_store`` (native Rust FTS)."""

from __future__ import annotations

from pathlib import Path

import pytest

from nyaya.exceptions import StorageUnavailable
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
