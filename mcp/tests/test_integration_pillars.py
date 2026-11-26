"""End-to-end integration tests wiring Pillars 1-4 into the 15-tool Nyaya MCP v2.1 system."""

from __future__ import annotations

import sqlite3
from datetime import date as Date
from pathlib import Path

import pytest

from nyaya import storage
from nyaya.config import Settings
from nyaya.ingest.builders.lance_fts import build_lance_fts_tables
from nyaya.ingest.builders.sqlite_core import build_sqlite_core, create_mini_ingest_input
from nyaya.mcp_app import create_mcp
from nyaya.models import (
    JudgmentSearchQuery,
    OffenceClassification,
    PointInTimeMeta,
    StatuteSearchQuery,
)
from nyaya.retrieval.hybrid import HashedNgramEmbeddingProvider
from nyaya.retrieval.lexical_engine import build_retrieval_engine
from nyaya.storage.lance_store import LanceStore
from nyaya.storage.sqlite_store import SqliteStore
from nyaya.tools.compare_provisions import compare_provisions
from nyaya.tools.get_cause_list import get_cause_list
from nyaya.tools.get_judgments_batch import get_judgments_batch
from nyaya.tools.get_provision import get_provision
from nyaya.tools.get_provisions_batch import get_provisions_batch
from tests.fixtures.mini_corpus.builder import build_mini_corpus
from tests.test_tools_contract import EXPECTED_15_TOOLS, _build_rich_memory_backend


@pytest.fixture
def _built_core_and_lance(tmp_path: Path):
    bundle = create_mini_ingest_input()
    sqlite_path = tmp_path / "nyaya-core.sqlite"
    build_sqlite_core(sqlite_path, input_data=bundle)
    lance_dir = tmp_path / "lancedb"
    embedder = HashedNgramEmbeddingProvider()
    build_lance_fts_tables(lance_dir, input_data=bundle, embedding_provider=embedder)

    backend = SqliteStore(sqlite_path)
    lance = LanceStore(local_dir=lance_dir, embedding_provider=embedder)
    storage.set_backend(backend)
    try:
        yield sqlite_path, lance_dir, backend, lance, embedder
    finally:
        lance.close()
        storage.set_backend(None)
        backend.close()


def test_sqlite_core_loads_cpc_tax_concordance_citation_edges_and_cause_lists(
    _built_core_and_lance,
) -> None:
    sqlite_path, _, backend, _, _ = _built_core_and_lance

    # 1. CPC First Schedule Orders & Rules loaded from cpc_first_schedule.yaml
    o1r1 = backend.get_provision("CPC", "Order 1 Rule 1")
    assert o1r1 is not None
    assert o1r1.kind == "order_rule"
    assert o1r1.ref_norm == "O1-R1"

    # 2. Criminal concordance surfaces offence_classification
    ipc_conc = backend.compare_provisions("IPC", "302")
    assert ipc_conc
    assert any(c.offence_classification is not None for c in ipc_conc)

    # 3. Tax concordance ITAct1961 <-> ITAct2025 loaded cleanly
    tax_conc = backend.compare_provisions("ITAct1961", "139")
    assert tax_conc
    assert any(c.new_ref and c.new_ref.act == "ITAct2025" for c in tax_conc)

    # 4. Cause lists table populated & searchable
    cl_rows = backend.search_cause_lists(court="Supreme Court of India")
    assert len(cl_rows) >= 1
    assert any("W.P.(C)" in (r.case_number or "") for r in cl_rows)

    # 5. Citation edges scaled across corpus
    con = sqlite3.connect(sqlite_path)
    try:
        edge_count = int(con.execute("SELECT count(*) FROM citation_edges").fetchone()[0])
    finally:
        con.close()
    assert edge_count >= 5


def test_sqlite_store_search_cause_lists_missing_table_graceful(tmp_path: Path) -> None:
    db_path = tmp_path / "legacy.sqlite"
    con = sqlite3.connect(db_path)
    con.execute("CREATE TABLE sources (source_id TEXT PRIMARY KEY)")
    con.commit()
    con.close()

    store = SqliteStore(db_path)
    try:
        assert store.search_cause_lists(court="SCI") == []
    finally:
        store.close()


def test_get_provision_point_in_time_as_of(_built_core_and_lance) -> None:
    # Pre-repeal vs post-repeal IPC s.302
    pre_ipc = get_provision(citation="IPC s.302", as_of="2023-06-01")
    assert pre_ipc.validity.status == "in_force"
    assert pre_ipc.text.startswith("<corpus_text")
    assert isinstance(pre_ipc.as_of_meta, PointInTimeMeta)
    assert pre_ipc.as_of_meta.effective_status == "in_force"
    assert pre_ipc.as_of_meta.as_of == Date(2023, 6, 1)

    post_ipc = get_provision(citation="IPC s.302", as_of="2025-01-15")
    assert post_ipc.validity.status == "repealed"
    assert isinstance(post_ipc.as_of_meta, PointInTimeMeta)
    assert post_ipc.as_of_meta.effective_status == "repealed"

    # Pre-Shreya Singhal vs post-Shreya Singhal ITAct s.66A
    pre_66a = get_provision(citation="ITAct s.66A", as_of="2014-01-01")
    assert pre_66a.validity.status == "in_force"
    assert pre_66a.validity.invalidating_judgment is None
    assert pre_66a.as_of_meta is not None
    assert pre_66a.as_of_meta.active_invalidation is None

    post_66a = get_provision(citation="ITAct s.66A", as_of="2016-01-01")
    assert post_66a.validity.status == "struck_down"
    assert post_66a.validity.invalidating_judgment is not None
    assert post_66a.as_of_meta is not None
    assert isinstance(post_66a.as_of_meta.active_invalidation, dict)
    assert "<corpus_text" in post_66a.as_of_meta.active_invalidation["holding_summary"]

    # Constitution Article 21A amendment metadata framed
    art21a = get_provision(citation="Article 21A", as_of="2015-01-01")
    assert art21a.as_of_meta is not None
    assert len(art21a.as_of_meta.applied_amendments) >= 1
    assert "<corpus_text" in art21a.as_of_meta.applied_amendments[0]["summary"]


def test_compare_provisions_offence_classification_and_tax_crosswalk(
    _built_core_and_lance,
) -> None:
    crim = compare_provisions(source="IPC s.302")
    assert crim.offence_classification is not None
    oc = (
        crim.offence_classification
        if isinstance(crim.offence_classification, OffenceClassification)
        else OffenceClassification.model_validate(crim.offence_classification)
    )
    assert oc.cognizable is True
    assert oc.bailable is False
    assert crim.transitional_rule and "531" in crim.transitional_rule

    tax = compare_provisions(source="ITAct1961 s.139")
    assert tax.source_provision.act == "ITAct1961"
    assert any(t.act == "ITAct2025" for t in tax.target_provisions)
    assert tax.offence_classification is None
    assert tax.transitional_rule and "536" in tax.transitional_rule

    tax_alias = compare_provisions(source="Income Tax Act 1961 s.143")
    assert tax_alias.source_provision.act == "ITAct1961"
    assert any(t.act == "ITAct2025" for t in tax_alias.target_provisions)


def test_retrieval_engine_hybrid_cad_and_tribunals(_built_core_and_lance, tmp_path: Path) -> None:
    _, lance_dir, backend, lance, embedder = _built_core_and_lance

    def _mk_settings(*, hybrid: bool) -> Settings:
        return Settings(
            data_dir=tmp_path,
            cache_dir=tmp_path / "cache",
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
            hybrid_search=hybrid,
        )

    lex_engine = build_retrieval_engine(_mk_settings(hybrid=False), backend, lance=lance)
    assert lex_engine.health()["mode"] == "lexical_bm25"
    cad_lex = lex_engine.search_statutes(StatuteSearchQuery(query="Draft Article 15 procedure established by law"))
    assert cad_lex.degraded_sources == []
    assert any(h.kind == "cad_debate" for h in cad_lex.results)

    trib_lex = lex_engine.search_judgments(
        JudgmentSearchQuery(query="Resolution plan Insolvency", court_tier="tribunal")
    )
    assert trib_lex.degraded_sources == []
    assert any(c.court_tier == "tribunal" for c in trib_lex.results)

    hyb_engine = build_retrieval_engine(
        _mk_settings(hybrid=True), backend, lance=lance, embedder=embedder
    )
    assert hyb_engine.health()["mode"] == "hybrid_rrf"
    sc_hyb = hyb_engine.search_judgments(JudgmentSearchQuery(query="basic structure Constitution", court_tier="all"))
    assert sc_hyb.degraded_sources == []
    assert len(sc_hyb.results) >= 1


async def test_fastmcp_lists_all_15_tools_and_mini_corpus_end_to_end(tmp_path: Path) -> None:
    paths = build_mini_corpus(tmp_path)
    store = SqliteStore(paths["sqlite"])
    storage.set_backend(store)
    try:
        mcp = create_mcp(backend=store)
        tools = await mcp.list_tools()
        assert {t.name for t in tools} == EXPECTED_15_TOOLS

        # Batch provisions with point-in-time reconstruction
        p_batch = get_provisions_batch(citations=["IPC s.302", "ITAct1961 s.139"], as_of="2023-01-01")
        assert p_batch.returned == 2

        # Batch judgments
        j_batch = await get_judgments_batch(["2017 INSC 801", "sc:1973:kesavananda"])
        assert j_batch.returned == 2

        # Local cause lists
        cl_resp = get_cause_list(court="Supreme Court of India")
        assert cl_resp.source == "local"
        assert cl_resp.returned >= 1
        assert cl_resp.entries[0].parties and "<corpus_text" in cl_resp.entries[0].parties
    finally:
        storage.set_backend(None)
        store.close()


async def test_in_memory_backend_supports_as_of_and_15_tools() -> None:
    backend = _build_rich_memory_backend()
    storage.set_backend(backend)
    try:
        resp = get_provision(citation="ITAct s.66A", as_of="2014-01-01")
        assert resp.validity.status == "in_force"
        assert resp.as_of_meta is not None
        assert resp.as_of_meta.active_invalidation is None

        resp_post = get_provision(citation="ITAct s.66A", as_of="2016-01-01")
        assert resp_post.validity.status == "struck_down"
        assert resp_post.as_of_meta is not None
        assert resp_post.as_of_meta.active_invalidation is not None
    finally:
        storage.set_backend(None)
