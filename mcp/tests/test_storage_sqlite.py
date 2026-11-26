"""Hermetic unit tests for ``nyaya.storage.sqlite_store``."""

from __future__ import annotations

import hashlib
from datetime import date as Date
from pathlib import Path

import pytest

from nyaya.exceptions import CommercialLicenseBlocked
from nyaya.models import JudgmentMeta, SourceProvenance
from nyaya.storage import close_all, get_backend, set_backend
from nyaya.storage.memory_backend import InMemoryCorpusBackend
from nyaya.storage.sqlite_store import SqliteStore, _fts_query
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


def test_fts_query_stopwords_and_operators() -> None:
    # Stopwords are stripped when content tokens remain
    assert _fts_query("what is the punishment for murder") == '"punishment" OR "murder"'
    assert _fts_query("punishment for murder", operator="AND") == '"punishment" AND "murder"'
    # Falls back to all tokens when every token is a stopword
    assert _fts_query("what is the") == '"what" OR "is" OR "the"'
    # Exact phrase keeps stopwords intact
    assert _fts_query("right of speech", exact_phrase=True) == '"right of speech"'


def test_search_statutes_fts_precision_no_false_positives(sqlite_store: SqliteStore) -> None:
    # Unmatched multi-word query with "of" must not match Article 19 or other noise
    assert sqlite_store.search_statutes_fts("dishonour of cheque insufficient funds") == []
    # 2-content-token query with 0 AND hits must not fall back to single-token noise
    assert sqlite_store.search_statutes_fts("cheque dishonour") == []
    # 2-content-token query with AND hits succeeds
    hits = sqlite_store.search_statutes_fts("punishment for murder")
    assert len(hits) >= 1
    assert any(h.act in {"IPC", "BNS"} and h.ref in {"302", "103"} for h in hits)


def test_meta_to_card_authority_levels_and_amendment_sha256(sqlite_store: SqliteStore) -> None:
    sc_meta = sqlite_store.resolve_judgment_id("AIR 1973 SC 1461")[0]
    assert SqliteStore._meta_to_card(sc_meta).authority_level == "binding_sc"

    hc_meta = sqlite_store.resolve_judgment_id("2024:DHC:1234")[0]
    assert SqliteStore._meta_to_card(hc_meta).authority_level == "persuasive_other_hc"

    prov = SourceProvenance(
        source_id="test",
        source_name="Test",
        source_url="https://example.invalid",
        license_spdx="CC-BY-4.0",
        commercial_safe=True,
        terms_status="cleared",
        as_of=Date(2026, 11, 22),
    )
    trib_meta = JudgmentMeta(
        case_id="trib:1",
        court_tier="tribunal",
        court_code="NCLAT",
        court_name="NCLAT",
        title="Tribunal Order",
        provenance=prov,
    )
    dist_meta = JudgmentMeta(
        case_id="dist:1",
        court_tier="district_court",
        court_code="MH_DIST_01",
        court_name="Pune District Court",
        title="District Order",
        provenance=prov,
    )
    assert SqliteStore._meta_to_card(trib_meta).authority_level == "tribunal"
    assert SqliteStore._meta_to_card(dist_meta).authority_level == "district"

    hist = sqlite_store.get_provision_history("Constitution", amendment_number=42)
    assert len(hist.amendments) == 1
    am = hist.amendments[0]
    expected_sha = hashlib.sha256(am.summary.encode("utf-8")).hexdigest()
    assert am.provenance.content_sha256 == expected_sha

    # text_availability logic: indexed_bm25 ONLY when has_lance_text=True
    s3_meta = JudgmentMeta(
        case_id="hc:s3only",
        court_tier="high_court",
        court_code="7_26",
        court_name="High Court of Delhi",
        title="S3 Only Judgment",
        has_lance_text=False,
        pdf_s3_bucket="nyaya-bucket",
        pdf_s3_key="hc/2024/test.pdf",
        provenance=prov,
    )
    meta_only = JudgmentMeta(
        case_id="hc:metaonly",
        court_tier="high_court",
        court_code="7_26",
        court_name="High Court of Delhi",
        title="Meta Only Judgment",
        has_lance_text=False,
        provenance=prov,
    )
    assert SqliteStore._meta_to_card(sc_meta).text_availability == "indexed_bm25"
    assert SqliteStore._meta_to_card(s3_meta).text_availability == "on_demand_s3_pdf"
    assert SqliteStore._meta_to_card(meta_only).text_availability == "metadata_only"


def test_alias_map_includes_acts_table_and_jurisdiction_normalization(
    sqlite_store: SqliteStore,
    tmp_path: Path,
) -> None:
    import sqlite3

    from tests.fixtures.mini_corpus.builder import MINI_SQLITE_DDL

    alias_map = sqlite_store.get_alias_map()
    assert alias_map["ipc"] == "IPC"
    assert alias_map["the indian penal code, 1860"] == "IPC"
    assert alias_map["indian penal code, 1860"] == "IPC"
    assert alias_map["maharashtra ownership flats act, 1963"] == "MH-MOFA"

    # Full state name "Maharashtra" resolves to "MH" in get_provision, browse_statute, search_statutes_fts
    assert sqlite_store.browse_statute(jurisdiction="Maharashtra").total >= 1

    db_path = tmp_path / "p0_sqlite.sqlite"
    con = sqlite3.connect(db_path)
    try:
        con.executescript(MINI_SQLITE_DDL)
        con.execute(
            "INSERT INTO sources VALUES ('s1','Src','https://example.invalid','CC-BY-4.0',1,'cleared','v1','2026-01-01',?,'10',NULL)",
            ("a" * 64,),
        )
        con.executemany(
            "INSERT INTO acts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("mh-mofa", "MH-MOFA", "The Maharashtra Ownership Flats Act, 1963", 1963, None, "MH", "state_act", "in_force", None, None, None, "sqlite_core", "s1", 1, "2026-01-01"),
                ("spices-board", "SpicesBoard", "The Spices Board Act, 1986", 1986, None, "Central", "central_act", "in_force", None, None, None, "sqlite_core", "s1", 1, "2026-01-01"),
                ("sebi-lodr", "SEBI-LODR", "SEBI LODR Regulations, 2015", 2015, None, "SEBI", "regulator", "in_force", None, None, None, "sqlite_core", "s1", 1, "2026-01-01"),
                ("ipc-old", "IPC", "The Indian Penal Code, 1860", 1860, None, "Central", "central_act", "repealed", None, None, None, "sqlite_core", "s1", 1, "2026-01-01"),
                ("bns-new", "BNS", "The Bharatiya Nyaya Sanhita, 2023", 2023, None, "Central", "central_act", "in_force", None, None, None, "sqlite_core", "s1", 1, "2026-01-01"),
            ],
        )
        con.executemany(
            "INSERT INTO provisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            [
                ("mh-mofa:4", "mh-mofa", "MH-MOFA", "section", "4", "4", 4, None, None, None, "Agreement for sale", "Promoter shall execute agreement for sale in Maharashtra.", "in_force", None, None, "{}", "s1", "https://example.invalid", "h1", 1),
                ("spices:7", "spices-board", "SpicesBoard", "section", "7", "7", 7, None, None, None, "Board disclosure", "Spices board disclosure duties for exporters.", "in_force", None, None, "{}", "s1", "https://example.invalid", "h2", 1),
                ("sebi:30", "sebi-lodr", "SEBI-LODR", "regulation", "30", "30", 30, None, None, None, "Material disclosure", "Listed entity disclosure of material events.", "in_force", None, None, "{}", "s1", "https://example.invalid", "h3", 1),
                ("noise:1", "spices-board", "SpicesBoard", "section", "17", "17", 17, None, None, None, "Article right", "Article 17 ndlzesl'llc spaceship mineral fiduciary right.", "in_force", None, None, "{}", "s1", "https://example.invalid", "h4", 1),
                ("ipc:302", "ipc-old", "IPC", "section", "302", "302", 302, None, None, None, "Murder penalty", "Whoever commits murder shall be punished.", "repealed", None, None, "{}", "s1", "https://example.invalid", "h5", 1),
                ("bns:103", "bns-new", "BNS", "section", "103", "103", 103, None, None, None, "Murder penalty", "Whoever commits murder shall be punished.", "in_force", None, None, "{}", "s1", "https://example.invalid", "h6", 1),
            ],
        )
        con.execute("INSERT INTO provisions_fts(provisions_fts) VALUES('rebuild')")
        con.commit()
    finally:
        con.close()

    store = SqliteStore(db_path)
    try:
        prov = store.get_provision("MH-MOFA", "4", jurisdiction="Maharashtra")
        assert prov is not None and prov.jurisdiction == "MH"
        fts_mh = store.search_statutes_fts("promoter agreement", jurisdiction="Maharashtra")
        assert len(fts_mh) == 1 and fts_mh[0].act == "MH-MOFA"

        # Category filter prevents central_act from leaking into regulator searches
        reg_hits = store.search_statutes_fts("disclosure", category="regulator")
        assert [h.act for h in reg_hits] == ["SEBI-LODR"]

        # Status='all' gives gentle +0.5 tie-breaker to in_force over repealed
        murder_hits = store.search_statutes_fts("murder penalty", status="all")
        assert len(murder_hits) == 2
        assert murder_hits[0].act == "BNS"
        assert murder_hits[0].bm25_score > murder_hits[1].bm25_score

        # Precision traps: word-boundary & length-scaled min_required reject out-of-corpus queries
        assert store.search_statutes_fts("Delaware LLC fiduciary duty") == []
        assert store.search_statutes_fts("EU GDPR Article 17 right to erasure") == []
        assert store.search_statutes_fts("Martian space mining treaty") == []
    finally:
        store.close()


def test_amendment_count_matches_provision_history_and_amendment_44(
    sqlite_store: SqliteStore,
    tmp_path: Path,
) -> None:
    import sqlite3

    import yaml

    from tests.fixtures.mini_corpus.builder import MINI_SQLITE_DDL

    # 1. Parity across mini-corpus Constitution articles
    for ref in ("14", "19", "21", "21A", "32"):
        prov = sqlite_store.get_provision("Constitution", ref)
        assert prov is not None
        hist = sqlite_store.get_provision_history("Constitution", ref)
        assert prov.validity.amendment_count == hist.total

    hist_art21 = sqlite_store.get_provision_history("Constitution", "Art. 21")
    hist_21 = sqlite_store.get_provision_history("Constitution", "21")
    assert hist_art21.total == hist_21.total

    # 2. Verify Amendment #44 in amendments_1_106.yaml includes Articles 20 and 21
    yaml_path = (
        Path(__file__).resolve().parents[1]
        / "nyaya"
        / "ingest"
        / "data"
        / "amendments_1_106.yaml"
    )
    data = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    amend_44 = next(a for a in data["amendments"] if a["number"] == 44)
    assert "20" in amend_44["articles_affected"]
    assert "21" in amend_44["articles_affected"]

    db_path = tmp_path / "amend44_parity.sqlite"
    con = sqlite3.connect(db_path)
    try:
        con.executescript(MINI_SQLITE_DDL)
        con.execute(
            "INSERT INTO sources VALUES ('s1','Src','https://example.invalid','CC-BY-4.0',1,'cleared','v1','2026-01-01',?,'10',NULL)",
            ("a" * 64,),
        )
        con.execute(
            "INSERT INTO acts VALUES ('constitution','Constitution','The Constitution of India',1950,NULL,'Central','constitution','in_force','1950-01-26',NULL,NULL,'sqlite_core','s1',1,'2026-01-01')"
        )
        for art in ("14", "20", "21"):
            con.execute(
                "INSERT INTO provisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    f"constitution:art:{art}",
                    "constitution",
                    "Constitution",
                    "article",
                    art,
                    art,
                    int(art),
                    None,
                    None,
                    "Part III",
                    f"Article {art}",
                    f"Text of Article {art}.",
                    "in_force",
                    "1950-01-26",
                    None,
                    "{}",
                    "s1",
                    "https://example.invalid",
                    f"h{art}",
                    1,
                ),
            )
        arts_csv = ",".join(str(a) for a in amend_44["articles_affected"])
        con.execute(
            "INSERT INTO provision_amendments VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                "const-amend-44",
                "constitution",
                None,
                str(amend_44["title"]),
                44,
                str(amend_44["enacted_date"]),
                str(amend_44["effective_date"]),
                str(amend_44["action"]),
                str(amend_44["summary"]),
                arts_csv,
                "s1",
            ),
        )
        con.commit()
    finally:
        con.close()

    store = SqliteStore(db_path)
    try:
        for art in ("14", "20", "21"):
            p = store.get_provision("Constitution", art)
            assert p is not None
            h_plain = store.get_provision_history("Constitution", art)
            h_prefixed = store.get_provision_history("Constitution", f"Art. {art}")
            assert p.validity.amendment_count == h_plain.total == h_prefixed.total
        assert store.get_provision("Constitution", "14").validity.amendment_count == 0
        assert store.get_provision("Constitution", "20").validity.amendment_count == 1
        assert store.get_provision("Constitution", "21").validity.amendment_count == 1
    finally:
        store.close()
