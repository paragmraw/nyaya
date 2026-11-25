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
                query="Constitution liberty rejection",
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


def test_lance_state_and_regulator_exact_match_ranks_first(
    corpus_paths: dict[str, Path],
    tmp_path: Path,
) -> None:
    settings = _make_settings(corpus_paths["root"], tmp_path / "cache")
    sqlite = SqliteStore(corpus_paths["sqlite"])
    lance = LanceStore(local_dir=corpus_paths["lancedb"], offline_only=True)
    engine = build_retrieval_engine(settings, sqlite, lance=lance)
    try:
        mofa_resp = engine.search_statutes(
            StatuteSearchQuery(
                query="What does Maharashtra Ownership Flats Act (MH-MOFA) section 4 require a promoter to execute before accepting advance payment?",
                limit=10,
            )
        )
        assert mofa_resp.returned >= 1
        assert (mofa_resp.results[0].act, mofa_resp.results[0].ref) == ("MH-MOFA", "4")

        sebi_resp = engine.search_statutes(
            StatuteSearchQuery(
                query="What disclosure of material events is required by a listed entity under SEBI LODR Reg. 30?",
                limit=10,
            )
        )
        assert sebi_resp.returned >= 1
        assert (sebi_resp.results[0].act, sebi_resp.results[0].ref) == ("SEBI-LODR", "30")

        empty_resp = engine.search_statutes(
            StatuteSearchQuery(query="dishonour of cheque insufficient funds", limit=10)
        )
        assert empty_resp.total == 0
    finally:
        sqlite.close()


def test_lance_multitoken_guard_rejects_single_token_noise_keeps_real_queries(
    corpus_paths: dict[str, Path],
    tmp_path: Path,
) -> None:
    """Out-of-corpus queries that OR-match only 1 token in LanceDB are filtered out, while real queries remain."""
    settings = _make_settings(corpus_paths["root"], tmp_path / "cache")
    sqlite = SqliteStore(corpus_paths["sqlite"])

    class _NoisyLanceStore:
        def search_state_regulators(self, query: str, **_kwargs):
            # Simulate LanceDB OR-matching a single token ("treaty") for an out-of-corpus query
            if "martian" in query.lower():
                return [
                    {
                        "doc_id": "REG_IRDAI_treaty_noise",
                        "short_name": "IRDAI-Reinsurance",
                        "jurisdiction": "Central",
                        "kind": "regulation",
                        "ref": "5",
                        "title": "Treaty reinsurance placements",
                        "status": "in_force",
                        "text": "Every insurer shall file its treaty reinsurance programme annually.",
                        "_score": 12.5,
                    }
                ]
            return []

    noisy_engine = build_retrieval_engine(settings, sqlite, lance=_NoisyLanceStore())  # type: ignore[arg-type]
    real_lance = LanceStore(local_dir=corpus_paths["lancedb"], offline_only=True)
    real_engine = build_retrieval_engine(settings, sqlite, lance=real_lance)
    try:
        noise_resp = noisy_engine.search_statutes(
            StatuteSearchQuery(query="martian space treaty quantum", limit=10)
        )
        assert noise_resp.total == 0
        assert noise_resp.results == []

        sebi_resp = real_engine.search_statutes(
            StatuteSearchQuery(query="SEBI LODR Regulation 30", limit=10)
        )
        assert any(h.act == "SEBI-LODR" and h.ref == "30" for h in sebi_resp.results)

        mofa_resp = real_engine.search_statutes(
            StatuteSearchQuery(query="Maharashtra Ownership Flats promoter agreement for sale", limit=10)
        )
        assert any(h.act == "MH-MOFA" for h in mofa_resp.results)
    finally:
        sqlite.close()


def test_p0_shorthand_precision_dedup_and_landmark_ranking(
    corpus_paths: dict[str, Path],
    tmp_path: Path,
) -> None:
    from datetime import date as Date

    from nyaya.models import JudgmentCard, SourceProvenance
    from nyaya.retrieval.lexical_engine import _lance_row_to_judgment_card
    from nyaya.retrieval.query_analyzer import analyze_query
    from nyaya.retrieval.ranking import rank_judgment_cards

    # 1. Practitioner shorthand expansion in analyze_query
    q_ab = analyze_query("anticipatory bail standards")
    assert ("BNSS", "482") in q_ab.equivalent_statute_refs
    assert ("CrPC", "438") in q_ab.equivalent_statute_refs
    assert analyze_query("anticipatory bail", act="IPC").equivalent_statute_refs == []

    q_cb = analyze_query("cheque bounce limitation")
    assert ("NIAct", "138") in q_cb.equivalent_statute_refs
    q_cd = analyze_query("cheque dishonour notice")
    assert ("NIAct", "138") in q_cd.equivalent_statute_refs
    q_rp = analyze_query("application for rejection of plaint")
    assert ("CPC", "Order 7 Rule 11") in q_rp.equivalent_statute_refs

    # 2. _lance_row_to_judgment_card text_availability for Dattam metadata-only vs full text
    short_dattam = _lance_row_to_judgment_card(
        {
            "case_id": "sc:2024:short",
            "title": "State v. Respondent",
            "text": "State v. Respondent decided on 2024-01-01",
            "source_id": "dattam_sc_judgments",
        },
        tier="supreme_court",
    )
    assert short_dattam.text_availability == "on_demand_s3_pdf"
    long_dattam = _lance_row_to_judgment_card(
        {
            "case_id": "sc:2024:long",
            "title": "State v. Respondent",
            "text": "Detailed constitutional judgment text " + ("x" * 120),
            "source_id": "dattam_sc_judgments",
        },
        tier="supreme_court",
    )
    assert long_dattam.text_availability == "indexed_bm25"

    # 3. Landmark prominence & bench strength ranking
    prov = SourceProvenance(
        source_id="dattam-sc",
        source_name="Dattam",
        source_url="https://example.invalid",
        license_spdx="CC-BY-4.0",
        commercial_safe=True,
        terms_status="cleared",
        as_of=Date(2026, 11, 22),
    )
    minor_sc = JudgmentCard(
        case_id="sc:minor",
        title="Minor SC Case",
        court="Supreme Court of India",
        court_code="SCI",
        court_tier="supreme_court",
        bench_strength=2,
        cited_by_count=0,
        provenance=prov,
    )
    landmark_sc = JudgmentCard(
        case_id="sc:landmark",
        title="Landmark Constitution Bench",
        court="Supreme Court of India",
        court_code="SCI",
        court_tier="supreme_court",
        bench_strength=13,
        cited_by_count=1450,
        provenance=prov,
    )
    ranked = rank_judgment_cards(
        [minor_sc, landmark_sc],
        {"sc:minor": 0.030, "sc:landmark": 0.025},
        sort_by="authority_relevance",
    )
    assert ranked[0].case_id == "sc:landmark"

    # 4. Precision traps on search_statutes & search_judgments + CNR deduplication
    settings = _make_settings(corpus_paths["root"], tmp_path / "cache")
    sqlite = SqliteStore(corpus_paths["sqlite"])
    lance = LanceStore(local_dir=corpus_paths["lancedb"], offline_only=True)
    duck = DuckDBStore(
        manifest=load_manifest(corpus_paths["manifest"]),
        base_dir=corpus_paths["root"],
        temp_dir=tmp_path / "duck_tmp_p0",
        offline_only=True,
    )
    engine = build_retrieval_engine(settings, sqlite, lance=lance, duckdb=duck)
    try:
        for trap in (
            "Delaware LLC fiduciary duty",
            "EU GDPR Article 17 right to erasure",
            "Martian space mining treaty",
        ):
            assert engine.search_statutes(StatuteSearchQuery(query=trap, limit=10)).total == 0
            assert engine.search_judgments(JudgmentSearchQuery(query=trap, limit=10)).total == 0

        # CNR deduplication & metadata enrichment across SQLite, LanceDB, and DuckDB
        j_resp = engine.search_judgments(
            JudgmentSearchQuery(
                query="Arjun Mehta anticipatory bail Section 438 CrPC",
                court_tier="high_court",
                court="DHC",
                limit=10,
            )
        )
        cnrs = [r.cnr for r in j_resp.results if r.cnr]
        assert cnrs.count("DLHC010123452024") == 1
        arjun = next(r for r in j_resp.results if r.cnr == "DLHC010123452024")
        assert arjun.cited_by_count == 12
        assert arjun.bench_strength == 2
    finally:
        sqlite.close()
        duck.close()


def test_hc_display_name_strict_anticipatory_bail_and_court_interleaving(
    corpus_paths: dict[str, Path],
    tmp_path: Path,
) -> None:
    from datetime import date as Date

    from nyaya.models import JudgmentCard, SourceProvenance
    from nyaya.retrieval.lexical_engine import (
        _interleave_by_court,
        _lance_row_to_judgment_card,
    )

    # 1. _lance_row_to_judgment_card resolves raw Dattam court codes to display names
    manipur_card = _lance_row_to_judgment_card(
        {
            "case_id": "hc:man:10",
            "court_code": "14_25",
            "court_name": "14_25",
            "title": "X v. State",
            "text": "Anticipatory bail under AB/11/2023",
        },
        tier="high_court",
    )
    assert manipur_card.court == "High Court of Manipur"

    tripura_card = _lance_row_to_judgment_card(
        {
            "case_id": "hc:tri:10",
            "court_code": "16_20",
            "court_name": "",
            "title": "Y v. State",
            "text": "Anticipatory bail under s.438",
        },
        tier="high_court",
    )
    assert tripura_card.court == "High Court of Tripura"

    # 2. _interleave_by_court produces 5 distinct High Courts in top 5
    prov = SourceProvenance(
        source_id="dattam-hc",
        source_name="Dattam",
        source_url="https://example.invalid",
        license_spdx="CC-BY-4.0",
        commercial_safe=True,
        terms_status="cleared",
        as_of=Date(2026, 11, 22),
    )
    sample_cards = [
        JudgmentCard(case_id="m1", title="M1", court="High Court of Manipur", court_code="14_25", court_tier="high_court", provenance=prov),
        JudgmentCard(case_id="m2", title="M2", court="High Court of Manipur", court_code="14_25", court_tier="high_court", provenance=prov),
        JudgmentCard(case_id="m3", title="M3", court="High Court of Manipur", court_code="14_25", court_tier="high_court", provenance=prov),
        JudgmentCard(case_id="t1", title="T1", court="High Court of Tripura", court_code="16_20", court_tier="high_court", provenance=prov),
        JudgmentCard(case_id="d1", title="D1", court="High Court of Delhi", court_code="7_26", court_tier="high_court", provenance=prov),
        JudgmentCard(case_id="b1", title="B1", court="High Court of Bombay", court_code="27_1", court_tier="high_court", provenance=prov),
        JudgmentCard(case_id="c1", title="C1", court="High Court at Calcutta", court_code="19_16", court_tier="high_court", provenance=prov),
    ]
    interleaved = _interleave_by_court(sample_cards)
    assert len({c.court_code for c in interleaved[:5]}) == 5
    assert [c.case_id for c in interleaved] == ["m1", "t1", "d1", "b1", "c1", "m2", "m3"]

    # 3. End-to-end LexicalRetrievalEngine test: limit=150, year filters on equiv, CRM(A), interleaving
    class _StubLance:
        def __init__(self) -> None:
            self.hc_calls: list[tuple[str, int, int | None, int | None]] = []

        def search_hc(self, query: str, **kwargs):
            limit = int(kwargs.get("limit", 10))
            self.hc_calls.append((query, limit, kwargs.get("year_from"), kwargs.get("year_to")))
            if "BNSS" in query or "CrPC" in query:
                # Equiv OR-expansion returns an unrelated regular BAIL APPLN. + a genuine s.438 CrPC hit
                return [
                    {
                        "case_id": "hc:equiv:plain_bail",
                        "court_code": "7_26",
                        "court_name": "7_26",
                        "title": "Regular Bail v. State",
                        "case_type": "BAIL APPLN. 99/2024",
                        "acts_cited": ["CrPC"],
                        "text": "Regular bail granted under Section 439 CrPC in BAIL APPLN. 99/2024.",
                    },
                    {
                        "case_id": "hc:equiv:valid_438",
                        "court_code": "19_16",
                        "court_name": "19_16",
                        "title": "Anticipatory Bail v. State",
                        "acts_cited": ["CrPC"],
                        "text": "Anticipatory bail application under Section 438 CrPC allowed.",
                    },
                ]
            return [
                {
                    "case_id": "hc:man:ab",
                    "court_code": "14_25",
                    "court_name": "14_25",
                    "title": "AB/11/2023 v. State",
                    "text": "Petitioner seeks relief in AB/11/2023 before High Court.",
                },
                {
                    "case_id": "hc:man:aba",
                    "court_code": "14_25",
                    "court_name": "14_25",
                    "title": "ABA 20/2024 v. State",
                    "text": "ABA 20/2024 granted by bench.",
                },
                {
                    "case_id": "hc:man:plain",
                    "court_code": "14_25",
                    "court_name": "14_25",
                    "title": "Plain Bail Order",
                    "case_type": "BAIL APPLN. 55/2024",
                    "text": "BAIL APPLN. 55/2024 regular custody bail disposed.",
                },
                {
                    "case_id": "hc:tri:s438",
                    "court_code": "16_20",
                    "court_name": "",
                    "title": "S438 v. State",
                    "text": "Application under s.438 allowed.",
                },
                {
                    "case_id": "hc:dhc:abail",
                    "court_code": "7_26",
                    "court_name": "7_26",
                    "title": "ABAIL v. State",
                    "text": "Anticipatory bail under Section 482 BNSS.",
                },
                {
                    "case_id": "hc:bhc:prearrest",
                    "court_code": "27_1",
                    "court_name": "27_1",
                    "title": "Pre-arrest v. State",
                    "text": "Pre-arrest bail protection granted.",
                },
            ]

        def health(self):
            return {"status": "ok"}

    import shutil
    import sqlite3

    settings = _make_settings(corpus_paths["root"], tmp_path / "cache")
    local_sqlite = tmp_path / "nyaya_test.sqlite"
    shutil.copy2(corpus_paths["sqlite"], local_sqlite)
    # Insert a plain regular bail row in SQLite FTS that OR-matches "bail" in "anticipatory bail"
    with sqlite3.connect(local_sqlite) as con:
        con.execute(
            """
            INSERT OR REPLACE INTO judgments_meta (
                case_id, tier, court_code, court_name, title, petitioner, respondent,
                decision_date, year, disposal_nature, case_type, acts_cited_json,
                is_reportable, source_url, text_sha256, has_lance_text, cited_by_count,
                source_id, commercial_safe
            ) VALUES (
                'hc:sqlite:plain_bail', 'hc', '7_26', 'High Court of Delhi',
                'Regular Bail Application State', 'Accused', 'State',
                '2024-06-01', 2024, 'Dismissed', 'BAIL APPLN. 88/2024', '["CrPC"]',
                0, 'https://example.invalid', 'sha', 0, 0, 'dattam-hc', 1
            )
            """
        )
        con.execute(
            "INSERT INTO judgments_meta_fts(judgments_meta_fts) VALUES('rebuild')"
        )
        con.commit()
    sqlite = SqliteStore(local_sqlite)

    stub_lance = _StubLance()
    engine = build_retrieval_engine(settings, sqlite, lance=stub_lance)  # type: ignore[arg-type]
    try:
        resp = engine.search_judgments(
            JudgmentSearchQuery(
                query="anticipatory bail",
                court_tier="high_court",
                year_from=2023,
                year_to=2025,
                sort_by="relevance",
                limit=5,
            )
        )
        # First search_hc call uses limit=150 when tier=="high_court" and court is None,
        # and equiv search_hc propagates year_from/year_to cleanly.
        assert stub_lance.hc_calls[0][1] == 150
        assert len(stub_lance.hc_calls) >= 2
        assert stub_lance.hc_calls[1][2:] == (2023, 2025)
        from nyaya.retrieval.lexical_engine import _has_anticipatory_bail_signal

        assert _has_anticipatory_bail_signal(
            JudgmentCard(
                case_id="hc:cal:crma",
                title="CRM (A)/3455/2026 of SK SAHARUK Vs STATE",
                court="High Court at Calcutta",
                court_code="19_16",
                court_tier="high_court",
                provenance=prov,
            )
        )
        assert _has_anticipatory_bail_signal(
            JudgmentCard(
                case_id="hc:bhc:aba",
                title="A.B.A. No. 412 of 2024",
                court="High Court of Bombay",
                court_code="27_1",
                court_tier="high_court",
                provenance=prov,
            )
        )
        ids = [r.case_id for r in resp.results]
        assert "hc:man:plain" not in ids
        assert "hc:equiv:plain_bail" not in ids
        assert "hc:sqlite:plain_bail" not in ids
        assert len({r.court_code for r in resp.results[:5]}) == 5
        assert {r.court for r in resp.results[:5]} == {
            "High Court of Manipur",
            "High Court of Tripura",
            "High Court of Delhi",
            "High Court of Bombay",
            "High Court at Calcutta",
        }

        # When exact citation is present, exact card stays pinned first while rest are interleaved
        exact_resp = engine.search_judgments(
            JudgmentSearchQuery(
                query="2024:DHC:1234 anticipatory bail",
                court_tier="high_court",
                sort_by="relevance",
                limit=5,
            )
        )
        assert exact_resp.results[0].case_id == "hc:2024:dhc:1234"
    finally:
        sqlite.close()
