"""Unit tests for ``nyaya.retrieval`` (QueryAnalyzer, RRF, Authority Ranking, LexicalEngine)."""

from __future__ import annotations

from pathlib import Path

import pytest

from nyaya.config import Settings
from nyaya.models import JudgmentSearchQuery, StatuteSearchQuery
from nyaya.retrieval import build_retrieval_engine
from nyaya.retrieval.paragraph_bm25 import ParagraphBM25
from nyaya.storage.duckdb_store import DuckDBStore
from nyaya.storage.lance_store import LanceStore
from nyaya.storage.manifest import load_manifest
from nyaya.storage.sqlite_store import SqliteStore
from tests.fixtures.mini_corpus.builder import build_mini_corpus


def _make_settings(root: Path, cache: Path) -> Settings:
    return Settings(
        data_dir=root,
        cache_dir=cache,
        bucket_endpoint=None,
        bucket_name=None,
        bucket_access_key_id=None,
        bucket_secret_access_key=None,
        bucket_region="auto",
        bucket_url_style="vhost",
        manifest_url=None,
        commercial_safe_only=False,
        offline_only=True,
        duckdb_ext_dir=None,
        log_level="INFO",
        port=8000,
        redis_url=None,
        trusted_proxy_hops=1,
    )


@pytest.fixture(scope="module")
def corpus_paths(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    return build_mini_corpus(tmp_path_factory.mktemp("lexical_corpus"))


def test_exact_citation_top_3_floor_and_rrf_fusion(
    corpus_paths: dict[str, Path],
    tmp_path: Path,
) -> None:
    settings = _make_settings(corpus_paths["root"], tmp_path / "cache")
    sqlite = SqliteStore(corpus_paths["sqlite"])
    lance = LanceStore(local_dir=corpus_paths["lancedb"], offline_only=True)
    duck = DuckDBStore(
        manifest=load_manifest(corpus_paths["manifest"]),
        base_dir=corpus_paths["root"],
        temp_dir=tmp_path / "duck_tmp",
        offline_only=True,
    )
    engine = build_retrieval_engine(settings, sqlite, lance=lance, duckdb=duck)
    try:
        # Exact citation "IPC s.302" in a broad query stays in top 3
        resp = engine.search_statutes(
            StatuteSearchQuery(query="punishment for murder IPC s.302 and disclosures", limit=10)
        )
        assert resp.returned >= 1
        top_3_keys = [(h.act, h.ref) for h in resp.results[:3]]
        assert ("IPC", "302") in top_3_keys
        assert resp.degraded_sources == []

        # State/Regulator hit from LanceDB fused via RRF
        reg_resp = engine.search_statutes(
            StatuteSearchQuery(query="material disclosures board of directors Regulation 30", limit=10)
        )
        assert any(h.act == "SEBI-LODR" and h.ref == "30" for h in reg_resp.results)
    finally:
        sqlite.close()
        duck.close()


def test_promote_definitions_boosts_definition_provisions(
    corpus_paths: dict[str, Path],
    tmp_path: Path,
) -> None:
    settings = _make_settings(corpus_paths["root"], tmp_path / "cache")
    sqlite = SqliteStore(corpus_paths["sqlite"])
    lance = LanceStore(local_dir=corpus_paths["lancedb"], offline_only=True)
    engine = build_retrieval_engine(settings, sqlite, lance=lance)
    try:
        resp = engine.search_statutes(
            StatuteSearchQuery(
                query="Sanhita words and expressions punishment",
                act="BNS",
                promote_definitions=True,
                limit=5,
            )
        )
        assert resp.returned >= 1
        assert resp.results[0].ref == "2"
        assert "Defin" in (resp.results[0].title or "")
    finally:
        sqlite.close()


def test_pari_materia_equivalent_expansion_and_authority_ranking(
    corpus_paths: dict[str, Path],
    tmp_path: Path,
) -> None:
    settings = _make_settings(corpus_paths["root"], tmp_path / "cache")
    sqlite = SqliteStore(corpus_paths["sqlite"])
    lance = LanceStore(local_dir=corpus_paths["lancedb"], offline_only=True)
    duck = DuckDBStore(
        manifest=load_manifest(corpus_paths["manifest"]),
        base_dir=corpus_paths["root"],
        temp_dir=tmp_path / "duck_tmp",
        offline_only=True,
    )
    engine = build_retrieval_engine(settings, sqlite, lance=lance, duckdb=duck)
    try:
        # Searching for BNSS 482 with include_equivalent_provisions=True expands to CrPC 438
        j_resp = engine.search_judgments(
            JudgmentSearchQuery(
                query="anticipatory bail",
                act="BNSS",
                section="482",
                include_equivalent_provisions=True,
                binding_for_court="DHC",
                limit=10,
            )
        )
        assert any("CrPC" in ep and "438" in ep for ep in j_resp.expanded_provisions)
        dhc_hits = [r for r in j_resp.results if r.case_id == "hc:2024:dhc:1234"]
        assert len(dhc_hits) == 1
        assert dhc_hits[0].authority_level == "binding_home_hc"

        # Binding hierarchy: binding_sc > binding_home_hc > persuasive_other_hc
        all_resp = engine.search_judgments(
            JudgmentSearchQuery(
                query="Constitution liberty bail rejection",
                binding_for_court="DHC",
                sort_by="authority_relevance",
                limit=10,
            )
        )
        levels = [r.authority_level for r in all_resp.results]
        assert "binding_sc" in levels
        if "binding_home_hc" in levels and "persuasive_other_hc" in levels:
            assert levels.index("binding_sc") < levels.index("persuasive_other_hc")
            assert levels.index("binding_home_hc") < levels.index("persuasive_other_hc")
    finally:
        sqlite.close()
        duck.close()


def test_degraded_sources_when_lance_unavailable(
    corpus_paths: dict[str, Path],
    tmp_path: Path,
) -> None:
    settings = _make_settings(corpus_paths["root"], tmp_path / "cache")
    sqlite = SqliteStore(corpus_paths["sqlite"])
    missing_lance = LanceStore(local_dir=tmp_path / "missing_lancedb", offline_only=True)
    engine = build_retrieval_engine(settings, sqlite, lance=missing_lance)
    try:
        resp = engine.search_statutes(StatuteSearchQuery(query="murder punishment", limit=5))
        assert resp.returned >= 1
        assert "lance_fts" in resp.degraded_sources
    finally:
        sqlite.close()


def test_paragraph_bm25_ranking() -> None:
    paras = [
        "1. Preliminary facts of the appeal regarding municipal property assessment.",
        "2. The admissibility of electronic records under Section 65B(4) requires a mandatory certificate.",
        "3. Consequential relief and costs are declined.",
    ]
    bm25 = ParagraphBM25(paras)
    scores = bm25.score_all("Section 65B(4) mandatory certificate")
    assert scores[1] > scores[0]
    assert scores[1] > scores[2]

    ranked = bm25.rank("electronic records certificate", top_k=1, context_window=0)
    assert len(ranked) == 1
    assert ranked[0][0] == 1
