"""Hermetic unit tests for ``nyaya.storage.sqlite_store``."""

from __future__ import annotations

from pathlib import Path

import pytest

from nyaya.exceptions import CommercialLicenseBlocked
from nyaya.storage import close_all, get_backend, set_backend
from nyaya.storage.memory_backend import InMemoryCorpusBackend
from nyaya.storage.sqlite_store import SqliteStore
from tests.fixtures.mini_corpus.builder import build_mini_corpus


@pytest.fixture(scope="module")
def corpus_paths(tmp_path_factory: pytest.TempPathFactory) -> dict[str, Path]:
    return build_mini_corpus(tmp_path_factory.mktemp("sqlite_corpus"))


@pytest.fixture
def sqlite_store(corpus_paths: dict[str, Path]):
    store = SqliteStore(corpus_paths["sqlite"])
    try:
        yield store
    finally:
        store.close()


def test_sqlite_opens_readonly_immutable(sqlite_store: SqliteStore) -> None:
    assert sqlite_store.is_available() is True
    # Writing to immutable ro connection must fail
    with pytest.raises(Exception):
        sqlite_store._conn().execute("DELETE FROM acts")


def test_get_provision_and_validity_envelope(sqlite_store: SqliteStore) -> None:
    ipc_302 = sqlite_store.get_provision("IPC", "302")
    assert ipc_302 is not None
    assert ipc_302.act == "IPC"
    assert ipc_302.validity.status == "repealed"
    assert [r.ref for r in ipc_302.validity.replaced_by] == ["103"]
    assert ipc_302.provenance.commercial_safe is True

    it_66a = sqlite_store.get_provision("IT Act", "s.66A")
    assert it_66a is not None
    assert it_66a.validity.status == "struck_down"
    assert it_66a.validity.invalidating_judgment is not None
    assert "Shreya Singhal" in it_66a.validity.invalidating_judgment.case_name


def test_fts5_search_statutes_preserves_legal_tokens(sqlite_store: SqliteStore) -> None:
    hits = sqlite_store.search_statutes_fts("65B(4) electronic record", act="EvidenceAct")
    assert len(hits) >= 1
    assert hits[0].ref == "65B"


def test_concordance_and_history_and_cross_refs(sqlite_store: SqliteStore) -> None:
    conc = sqlite_store.compare_provisions("IPC", "302")
    assert len(conc) == 1
    assert conc[0].new_ref is not None and conc[0].new_ref.ref == "103"
    assert conc[0].punishment_delta is not None

    hist = sqlite_store.get_provision_history("Constitution", amendment_number=42)
    assert len(hist.amendments) == 1
    assert hist.amendments[0].year == 1976

    edges, total = sqlite_store.get_cross_refs("it-act-2000:66A", direction="to")
    assert total >= 1
    assert edges[0].relation == "invalidates"


def test_commercial_safe_only_blocks_ildc_and_filters_search(sqlite_store: SqliteStore) -> None:
    # Unfiltered finds ILDC
    ildc = sqlite_store.resolve_judgment_id("2020 INSC 99", commercial_safe_only=False)
    assert len(ildc) == 1
    assert ildc[0].provenance.commercial_safe is False

    # Commercial-safe exact lookup raises CommercialLicenseBlocked
    with pytest.raises(CommercialLicenseBlocked) as exc_info:
        sqlite_store.resolve_judgment_id("2020 INSC 99", commercial_safe_only=True)
    assert exc_info.value.license_spdx == "CC-BY-NC-4.0"

    # Commercial-safe search excludes ILDC
    sc_hits = sqlite_store.search_judgments_fts("ILDC criminal appeal", commercial_safe_only=True)
    assert all(h.provenance.commercial_safe for h in sc_hits)


def test_corpus_status_counts_and_trace_precedent(sqlite_store: SqliteStore) -> None:
    status = sqlite_store.corpus_status()
    assert status.counts.central_acts >= 1
    assert status.counts.sc_judgments >= 2
    assert status.counts.concordance_mappings >= 5

    trace = sqlite_store.trace_precedent("AIR 1973 SC 1461", direction="cited_by")
    assert trace.precedent_status == "unverified_automated"
    assert trace.cited_by_total >= 1
    assert any(e.related_case and "Puttaswamy" in e.related_case.title for e in trace.edges)

    cat = sqlite_store.browse_statute(act=None)
    assert cat.mode == "catalog" and cat.total >= 5
    toc = sqlite_store.browse_statute(act="IPC")
    assert toc.mode == "toc" and toc.returned >= 1
    provs = sqlite_store.browse_statute(act="IPC", chapter="XVI", include_text=True)
    assert provs.mode == "provisions" and provs.returned >= 1


def test_in_memory_backend_and_registry() -> None:
    mem = InMemoryCorpusBackend.from_sample_data()
    set_backend(mem)
    try:
        assert get_backend() is mem
        p = mem.get_provision("IPC", "302")
        assert p is not None and p.ref == "302"
        cat = mem.browse_statute()
        assert cat.total == 2
        toc = mem.browse_statute("IPC")
        assert toc.mode == "toc"
        provs = mem.browse_statute("IPC", kind="section")
        assert provs.returned == 1
        j = mem.resolve_judgment_id("AIR 1973 SC 1461")
        assert len(j) == 1
        status = mem.corpus_status()
        assert status.counts.central_acts == 2
    finally:
        close_all()
