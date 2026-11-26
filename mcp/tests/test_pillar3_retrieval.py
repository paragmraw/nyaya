"""TDD test suite for Pillar 3: OCR, official PDF URLs, citation graph, hybrid search,
HC/tribunal citations, LanceDB tribunal/CAD tables, and query equivalents.
"""

from __future__ import annotations

import inspect
import json
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from nyaya.citations import canonicalize_court, parse_single_citation
from nyaya.exceptions import PdfFetchError
from nyaya.ingest.builders.citation_graph import build_citation_edges_for_corpus
from nyaya.ingest.builders.lance_fts import _iter_hc_records, build_lance_fts_tables
from nyaya.ingest.builders.sqlite_core import IngestInputBundle, create_mini_ingest_input
from nyaya.ingest.extract.ocr import (
    OcrEngine,
    RapidOcrEngine,
    TesseractOcrEngine,
    get_default_ocr_engine,
)
from nyaya.ingest.extract.pdf import MAX_PDF_BYTES, MAX_PDF_PAGES, extract_pdf_bytes
from nyaya.models import JudgmentMeta, SourceProvenance
from nyaya.retrieval.hybrid import (
    EmbeddingProvider,
    HashedNgramEmbeddingProvider,
    OpenAICompatibleEmbeddingProvider,
    cosine_similarity,
    fuse_lexical_and_vector_rrf,
)
from nyaya.retrieval.query_analyzer import analyze_query, expand_equivalent_provisions
from nyaya.storage.lance_store import (
    CAD_DEBATES_TABLE,
    TRIBUNAL_CHUNKS_TABLE,
    LanceStore,
)
from nyaya.storage.pdf_fetcher import ALLOWED_PDF_HTTPS_HOSTS, DirectS3PdfFetcher
from tests.fixtures.mini_corpus.builder import make_minimal_pdf


class _FakeOcrEngine:
    name = "fake_ocr"

    def __init__(self, text_per_page: str, *, available: bool = True) -> None:
        self._text = text_per_page
        self._available = available
        self.calls = 0

    def is_available(self) -> bool:
        return self._available

    def extract_text(self, image_bytes: bytes, *, lang: str = "eng") -> str:
        assert image_bytes[:8] == b"\x89PNG\r\n\x1a\n"
        self.calls += 1
        return self._text


def _meta() -> JudgmentMeta:
    return JudgmentMeta(
        case_id="sc:2024:scan-test",
        court_tier="supreme_court",
        court_code="SCI",
        court_name="Supreme Court of India",
        title="Scanned Order Test v. Union of India",
        decision_date=date(2024, 5, 1),
        year=2024,
        pdf_s3_bucket="indian-supreme-court-judgments",
        pdf_s3_key="data/pdf/year=2024/english/scan_test.pdf",
        provenance=SourceProvenance(
            source_id="dattam-sc",
            source_name="Dattam SC",
            source_url="https://indian-supreme-court-judgments.s3.ap-south-1.amazonaws.com",
            license_spdx="CC-BY-4.0",
            commercial_safe=True,
            terms_status="cleared",
            as_of=date(2026, 11, 22),
        ),
    )


# ---------------------------------------------------------------------------
# 1. OCR adapter + pdf.py + DirectS3PdfFetcher.fetch_official_pdf_url
# ---------------------------------------------------------------------------


def test_tesseract_and_rapid_ocr_engines_and_default_selector(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # TesseractOcrEngine uses shutil.which + subprocess.run
    monkeypatch.setattr("nyaya.ingest.extract.ocr.shutil.which", lambda cmd: None)
    tess = TesseractOcrEngine()
    assert isinstance(tess, OcrEngine)
    assert tess.is_available() is False

    monkeypatch.setattr("nyaya.ingest.extract.ocr.shutil.which", lambda cmd: "/usr/bin/tesseract")
    assert tess.is_available() is True

    captured_cmds: list[list[str]] = []

    def _fake_run(cmd: list[str], **kwargs: Any) -> SimpleNamespace:
        captured_cmds.append(cmd)
        return SimpleNamespace(returncode=0, stdout=b"1. Held that privacy is fundamental.", stderr=b"")

    monkeypatch.setattr("nyaya.ingest.extract.ocr.subprocess.run", _fake_run)
    out = tess.extract_text(b"\x89PNG\r\n\x1a\nfake")
    assert "privacy is fundamental" in out
    assert captured_cmds and captured_cmds[0][0] == "/usr/bin/tesseract"

    # RapidOcrEngine lazily imports rapidocr_onnxruntime
    assert "rapidocr_onnxruntime" not in sys.modules
    rapid = RapidOcrEngine()
    assert isinstance(rapid, OcrEngine)

    class _FakeRapidOCR:
        def __call__(self, img: bytes) -> tuple[list[list[Any]], list[float]]:
            return ([[[0, 0], "1. RapidOCR extracted paragraph.", 0.99]], [0.01])

    fake_mod = SimpleNamespace(RapidOCR=_FakeRapidOCR)
    monkeypatch.setitem(sys.modules, "rapidocr_onnxruntime", fake_mod)
    assert rapid.is_available() is True
    assert rapid.extract_text(b"\x89PNG\r\n\x1a\nfake") == "1. RapidOCR extracted paragraph."

    default_eng = get_default_ocr_engine()
    assert default_eng is not None
    assert default_eng.is_available() is True


def test_extract_pdf_bytes_opt_in_ocr_behavior(monkeypatch: pytest.MonkeyPatch) -> None:
    blank_pdf = make_minimal_pdf(["", ""])

    # Default (enable_ocr=False) preserves scanned_image_only
    res_no_ocr = extract_pdf_bytes(blank_pdf)
    assert res_no_ocr.extraction_quality == "scanned_image_only"
    assert res_no_ocr.text_availability == "scanned_image_only"

    # enable_ocr=True but no engine available -> still scanned_image_only
    monkeypatch.setattr("nyaya.ingest.extract.pdf.get_default_ocr_engine", lambda: None)
    res_unavail = extract_pdf_bytes(blank_pdf, enable_ocr=True)
    assert res_unavail.extraction_quality == "scanned_image_only"

    # enable_ocr=True with working OcrEngine -> verified text
    fake_ocr = _FakeOcrEngine(
        "1. In this appeal the High Court considered Section 138 of the Negotiable Instruments Act, 1881 "
        "and held that notice was duly served upon the drawer."
    )
    res_ocr = extract_pdf_bytes(blank_pdf, enable_ocr=True, ocr_engine=fake_ocr)
    assert fake_ocr.calls == 2
    assert res_ocr.extraction_quality == "verified"
    assert res_ocr.text_availability == "indexed_bm25"
    assert res_ocr.chars_per_page >= 100.0
    assert "Negotiable Instruments Act" in res_ocr.full_text


async def test_pdf_fetcher_ocr_and_official_pdf_url_security(tmp_path: Path) -> None:
    assert {
        "ncdrc.nic.in",
        "nclat.nic.in",
        "sci.gov.in",
        "digiscr.sci.gov.in",
        "egazette.gov.in",
        "*.s3.ap-south-1.amazonaws.com",
    }.issubset(set(ALLOWED_PDF_HTTPS_HOSTS))

    blank_pdf = make_minimal_pdf([""])
    valid_pdf = make_minimal_pdf(
        [
            "1. The National Consumer Disputes Redressal Commission allowed the consumer complaint "
            "under Section 21 of the Consumer Protection Act, 1986."
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "redirect" in url:
            return httpx.Response(302, headers={"Location": "https://evil.example.com/x.pdf"})
        if "html" in url:
            return httpx.Response(200, content=b"<html>404 Not Found</html>")
        if "oversize" in url:
            return httpx.Response(
                200,
                headers={"Content-Length": str(MAX_PDF_BYTES + 1)},
                content=b"%PDF-1.4\n",
            )
        if "toomany" in url:
            return httpx.Response(200, content=make_minimal_pdf([""] * (MAX_PDF_PAGES + 1)))
        if "scan" in url:
            return httpx.Response(200, content=blank_pdf)
        return httpx.Response(200, content=valid_pdf)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    fake_ocr = _FakeOcrEngine(
        "1. Scanned Supreme Court order text recovered cleanly via opt-in permissive OCR adapter."
    )
    fetcher_no_ocr = DirectS3PdfFetcher(cache_dir=tmp_path / "c1", client=client, enable_ocr=False)
    fetcher_ocr = DirectS3PdfFetcher(
        cache_dir=tmp_path / "c2",
        client=client,
        enable_ocr=True,
        ocr_engine=fake_ocr,
    )
    try:
        res_plain = await fetcher_no_ocr.fetch_and_read(_meta(), mode="overview")
        assert res_plain.extraction_quality == "scanned_image_only"

        res_ocr = await fetcher_ocr.fetch_and_read(_meta(), mode="overview")
        assert res_ocr.extraction_quality == "verified"
        assert any("permissive OCR adapter" in ex.text for ex in res_ocr.excerpts)

        # Official HTTPS hosts allowed
        for ok_url in (
            "https://ncdrc.nic.in/orders/2024/order1.pdf",
            "https://nclat.nic.in/judgments/2024/j1.pdf",
            "https://sci.gov.in/pdf/2024/s1.pdf",
            "https://digiscr.sci.gov.in/pdf/2024/d1.pdf",
            "https://egazette.gov.in/WriteReadData/2024/g1.pdf",
            "https://my-legal-bucket.s3.ap-south-1.amazonaws.com/orders/o1.pdf",
        ):
            res_url = await fetcher_no_ocr.fetch_official_pdf_url(ok_url, mode="overview")
            assert res_url.extraction_quality == "verified"
            assert any("National Consumer Disputes" in ex.text for ex in res_url.excerpts)

        # Disallowed hosts / schemes rejected with disallowed_key
        for bad_url in (
            "http://sci.gov.in/pdf/2024/s1.pdf",
            "https://indiankanoon.org/doc/123.pdf",
            "https://evil.example.com/order.pdf",
            "https://169.254.169.254/latest/meta-data",
            "https://sci.gov.in.evil.com/x.pdf",
        ):
            with pytest.raises(PdfFetchError) as exc:
                await fetcher_no_ocr.fetch_official_pdf_url(bad_url)
            assert exc.value.reason == "disallowed_key"

        # Redirects not followed
        with pytest.raises(PdfFetchError) as exc_redir:
            await fetcher_no_ocr.fetch_official_pdf_url("https://sci.gov.in/redirect.pdf")
        assert exc_redir.value.reason == "http_error"

        # Non-PDF magic check
        with pytest.raises(PdfFetchError) as exc_html:
            await fetcher_no_ocr.fetch_official_pdf_url("https://sci.gov.in/html.pdf")
        assert exc_html.value.reason == "corrupt_pdf"

        # 15 MiB size cap and 500-page cap
        with pytest.raises(PdfFetchError) as exc_big:
            await fetcher_no_ocr.fetch_official_pdf_url("https://sci.gov.in/oversize.pdf")
        assert exc_big.value.reason == "size_limit"

        with pytest.raises(PdfFetchError) as exc_pages:
            await fetcher_no_ocr.fetch_official_pdf_url("https://sci.gov.in/toomany.pdf")
        assert exc_pages.value.reason == "size_limit"
    finally:
        await fetcher_no_ocr.aclose()
        await fetcher_ocr.aclose()


# ---------------------------------------------------------------------------
# 2. Citation Graph Builder
# ---------------------------------------------------------------------------


def test_build_citation_edges_for_corpus_resolves_dedups_and_caps() -> None:
    sc_judgments = [
        {
            "case_id": "sc:1973:kesavananda",
            "neutral_citation": None,
            "air_citation": "AIR 1973 SC 1461",
            "scc_citation": "(1973) 4 SCC 225",
            "scr_citation": "[1973] Supp SCR 1",
            "year": 1973,
            "bench_strength": 13,
            "text": "1. Basic structure doctrine under Article 368.",
        },
        {
            "case_id": "sc:2017:puttaswamy",
            "neutral_citation": "2017 INSC 801",
            "scc_citation": "(2017) 10 SCC 1",
            "year": 2017,
            "bench_strength": 9,
            "text": (
                "14. Following AIR 1973 SC 1461 and [1973] Supp SCR 1, "
                "and reiterating 2017 INSC 801 (self) and Article 21."
            ),
        },
    ]
    hc_judgments = [
        {
            "case_id": "hc:2024:dhc:1234",
            "neutral_citation": "2024:DHC:1234",
            "year": 2024,
            "bench_strength": 2,
            "text": "5. Applying 2017 INSC 801 and (2017) 10 SCC 1.",
        },
        {
            "case_id": "hc:2024:mhc:500",
            "neutral_citation": "2024:MHC:500",
            "year": 2024,
            "bench_strength": 1,
            "text": (
                "9. Relied on 2024:DHC:1234, 2017 INSC 801, AIR 1973 SC 1461, and Section 302 IPC."
            ),
        },
    ]
    provisions = [
        {"doc_id": "constitution:art:21", "act_short": "Constitution", "ref": "21"},
        {"doc_id": "ipc-1860:302", "act_short": "IPC", "ref": "302"},
    ]

    edges = build_citation_edges_for_corpus(
        sc_judgments,
        hc_judgments,
        provisions=provisions,
        max_edges_per_doc=100,
    )

    # Self-citation to 2017 INSC 801 excluded; duplicate kesavananda refs deduped
    putta_edges = [e for e in edges if e["from_id"] == "sc:2017:puttaswamy"]
    assert not any(e["to_id"] == "sc:2017:puttaswamy" for e in putta_edges)
    kesav_edges = [e for e in putta_edges if e["to_id"] == "sc:1973:kesavananda"]
    assert len(kesav_edges) == 1
    assert kesav_edges[0]["from_year"] == 2017
    assert kesav_edges[0]["from_bench_str"] == 9
    assert kesav_edges[0]["para_ref"] == "para 14"
    assert "AIR 1973 SC 1461" in kesav_edges[0]["quoting_excerpt"]
    assert kesav_edges[0]["precedent_status"] == "unverified_automated"

    # Provision & HC neutral resolution
    mhc_edges = [e for e in edges if e["from_id"] == "hc:2024:mhc:500"]
    mhc_targets = {e["to_id"] for e in mhc_edges}
    assert {"hc:2024:dhc:1234", "sc:2017:puttaswamy", "sc:1973:kesavananda", "ipc-1860:302"} <= mhc_targets

    # max_edges_per_doc respected
    capped = build_citation_edges_for_corpus(
        sc_judgments, hc_judgments, provisions=provisions, max_edges_per_doc=2
    )
    assert len([e for e in capped if e["from_id"] == "hc:2024:mhc:500"]) <= 2


# ---------------------------------------------------------------------------
# 3. Hybrid Search & Embeddings
# ---------------------------------------------------------------------------


def test_hashed_ngram_embedding_and_openai_provider_and_rrf() -> None:
    emb = HashedNgramEmbeddingProvider(dim=256)
    assert isinstance(emb, EmbeddingProvider)
    assert emb.dim == 256

    vecs = emb.embed(
        [
            "anticipatory bail application apprehending arrest",
            "pre-arrest bail under Section 438 CrPC",
            "customs tariff duty exemption notification",
            "",
        ]
    )
    assert len(vecs) == 4
    assert all(len(v) == 256 for v in vecs)
    assert vecs[3] == [0.0] * 256
    assert cosine_similarity(vecs[0], vecs[1]) > cosine_similarity(vecs[0], vecs[2])

    # OpenAICompatibleEmbeddingProvider via MockTransport
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content.decode("utf-8"))
        return httpx.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ]
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    oai = OpenAICompatibleEmbeddingProvider(
        endpoint="https://api.example.test/v1",
        model="text-embedding-3-small",
        api_key="secret-test-key",
        client=client,
    )
    out = oai.embed(["hello", "world"])
    assert captured["url"] == "https://api.example.test/v1/embeddings"
    assert captured["auth"] == "Bearer secret-test-key"
    assert out == [[1.0, 0.0], [0.0, 1.0]]
    assert oai.dim == 2

    # RRF fusion
    lex = [{"chunk_id": "a", "text": "A"}, {"chunk_id": "b", "text": "B"}]
    vec = [{"chunk_id": "b", "text": "B"}, {"chunk_id": "c", "text": "C"}]
    fused = fuse_lexical_and_vector_rrf(lex, vec, id_key="chunk_id", k=60, alpha=0.5)
    assert fused[0]["chunk_id"] == "b"
    assert "_rrf_score" in fused[0]


# ---------------------------------------------------------------------------
# 4. LanceDB HC sorting, 500k cap, tribunal_chunks, cad_debates, hybrid_search
# ---------------------------------------------------------------------------


def test_hc_priority_sort_and_default_hot_hc_limit() -> None:
    sig = inspect.signature(build_lance_fts_tables)
    assert sig.parameters["hot_hc_limit"].default == 500_000

    bundle = IngestInputBundle(
        hc_web_rows=[
            {
                "case_id": "hc:nonrep:bigbench",
                "cnr": "DLHC010000012024",
                "court_code": "7_26",
                "is_reportable": False,
                "bench_strength": 5,
                "year": 2024,
                "text": "Non-reportable large bench judgment text.",
            },
            {
                "case_id": "hc:rep:smallbench:old",
                "cnr": "DLHC010000022020",
                "court_code": "7_26",
                "is_reportable": True,
                "bench_strength": 1,
                "year": 2020,
                "text": "Reportable single judge older judgment.",
            },
            {
                "case_id": "hc:rep:bigbench:notext",
                "cnr": "DLHC010000032024",
                "court_code": "7_26",
                "is_reportable": True,
                "bench_strength": 3,
                "year": 2024,
                "text": "",
                "title": "Reportable Division Bench Without Text",
            },
            {
                "case_id": "hc:rep:bigbench:withtext",
                "cnr": "DLHC010000042023",
                "court_code": "7_26",
                "is_reportable": True,
                "bench_strength": 3,
                "year": 2023,
                "text": "Reportable Division Bench With Text.",
            },
        ]
    )
    recs = list(
        _iter_hc_records(bundle, commercial_safe_only=False, hot_hc_limit=3, state=None)
    )
    ids = [r["case_id"] for r in recs]
    assert ids == [
        "hc:rep:bigbench:withtext",
        "hc:rep:bigbench:notext",
        "hc:rep:smallbench:old",
    ]


def test_lance_tribunals_cad_debates_and_hybrid_search(tmp_path: Path) -> None:
    bundle = create_mini_ingest_input()
    bundle.tribunal_rows.append(
        {
            "case_id": "trib:ncdrc:2024:99",
            "forum_code": "NCDRC",
            "court_code": "NCDRC",
            "court_name": "National Consumer Disputes Redressal Commission",
            "title": "Homebuyer v. Builder Co.",
            "judge": "Justice A.P. Sahi",
            "decision_date": "2024-04-12",
            "year": 2024,
            "disposal_nature": "Allowed",
            "case_type": "Consumer Complaint",
            "act": "ConsumerProtection",
            "text": "Delay in handing over possession amounts to deficiency in service under Consumer Protection Act.",
            "commercial_safe": True,
        }
    )
    cad_debates = [
        {
            "debate_id": "cad:vol7:1948-12-06:art21",
            "volume": "Vol. VII",
            "sitting_date": "1948-12-06",
            "year": 1948,
            "speaker": "Dr. B.R. Ambedkar",
            "article_ref": "Article 21",
            "title": "Draft Article 15 - Due Process vs Procedure Established by Law",
            "text": "The Constituent Assembly debated due process of law versus procedure established by law in Draft Article 15.",
            "commercial_safe": True,
        }
    ]

    # 1. Pure FTS build (no vector column) -> hybrid_search degrades cleanly to FTS
    fts_dir = tmp_path / "lance_fts_only"
    counts = build_lance_fts_tables(
        fts_dir, input_data=bundle, cad_debates=cad_debates, commercial_safe_only=False
    )
    assert counts[TRIBUNAL_CHUNKS_TABLE] >= 2
    assert counts[CAD_DEBATES_TABLE] == 1

    store_fts = LanceStore(local_dir=fts_dir, offline_only=True)
    trib_hits = store_fts.search_tribunals("deficiency in service", forum_code="NCDRC", limit=5)
    assert len(trib_hits) == 1
    assert trib_hits[0]["case_id"] == "trib:ncdrc:2024:99"

    cad_hits = store_fts.search_cad_debates("due process procedure established", article_ref="Article 21")
    assert len(cad_hits) == 1
    assert "Ambedkar" in cad_hits[0]["speaker"]

    degraded = store_fts.hybrid_search(
        TRIBUNAL_CHUNKS_TABLE,
        "deficiency in service",
        embedding_provider=HashedNgramEmbeddingProvider(dim=256),
        limit=5,
    )
    assert len(degraded) == 1
    assert degraded[0]["case_id"] == "trib:ncdrc:2024:99"

    # 2. Vector + FTS build -> hybrid_search uses RRF with vector similarity
    emb = HashedNgramEmbeddingProvider(dim=256)
    vec_dir = tmp_path / "lance_hybrid"
    build_lance_fts_tables(
        vec_dir,
        input_data=bundle,
        cad_debates=cad_debates,
        embedding_provider=emb,
    )
    store_hybrid = LanceStore(local_dir=vec_dir, offline_only=True, embedding_provider=emb)
    # Synonym-driven semantic query ("pre-arrest bail" matches "anticipatory bail")
    hyb_hits = store_hybrid.hybrid_search("hc_hot_chunks", "pre-arrest bail", limit=5)
    assert any(h["case_id"] == "hc:2024:dhc:1234" for h in hyb_hits)


# ---------------------------------------------------------------------------
# 5. All 25 HC neutral citations, Consumer Commissions, Tribunals, Equivalents
# ---------------------------------------------------------------------------


def test_all_25_hc_neutral_citations_and_tribunal_aliases() -> None:
    expected_hc_neutrals = [
        ("2024:DHC:101", "DHC"),
        ("2024:BHC-OS:102", "BHC"),
        ("2024:BHC-AS:103", "BHC"),
        ("2024:MHC:104", "MAD"),
        ("2024:KHC:105", "KAR"),
        ("2024:KHC-K:106", "KAR"),
        ("2024:AHC:107", "AHC"),
        ("2024:AHC-LKO:108", "AHC"),
        ("2024:APHC:109", "APHC"),
        ("2024:CGHC:110", "CGHC"),
        ("2024:GAU:111", "GAU"),
        ("2024:GUJ:112", "GUJ"),
        ("2024:HPHC:113", "HPHC"),
        ("2024:JKL:114", "JKHC"),
        ("2024:JHHC:115", "JHC"),
        ("2024:MPHC:116", "MPHC"),
        ("2024:MPHC-IND:117", "MPHC"),
        ("2024:MPHC-GWL:118", "MPHC"),
        ("2024:OHC:119", "ORI"),
        ("2024:PHHC:120", "PHC"),
        ("2024:RJ-JD:121", "RAJ"),
        ("2024:RJ-JP:122", "RAJ"),
        ("2024:SHC:123", "SIK"),
        ("2024:THC:124", "TSHC"),
        ("2024:UHC:125", "UKHC"),
        ("2024:CAL:126", "CAL"),
        ("2024:PAT:127", "PAT"),
        ("2024:MEG:128", "MEG"),
        ("2024:MAN:129", "MAN"),
        ("2024:TRI:130", "TRI"),
        ("2024:KER:131", "KER"),
    ]
    for raw, exp_court in expected_hc_neutrals:
        parsed = parse_single_citation(raw)
        assert parsed is not None, raw
        assert parsed.kind == "hc_neutral", raw
        assert parsed.act_or_court == exp_court, (raw, parsed.act_or_court)

    assert canonicalize_court("NCDRC") == "NCDRC"
    assert canonicalize_court("National Consumer Disputes Redressal Commission") == "NCDRC"
    assert canonicalize_court("SCDRC") == "SCDRC"
    assert canonicalize_court("DCDRC") == "DCDRC"
    assert canonicalize_court("Armed Forces Tribunal") == "AFT"
    assert canonicalize_court("Debts Recovery Appellate Tribunal") == "DRAT"


def test_expanded_static_equivalents_criminal_and_income_tax() -> None:
    assert ("BNS", "3(5)") in expand_equivalent_provisions("IPC", "34")
    assert ("IPC", "34") in expand_equivalent_provisions("BNS", "3(5)")
    assert ("BNS", "152") in expand_equivalent_provisions("IPC", "124A")
    assert ("BNS", "61(2)") in expand_equivalent_provisions("IPC", "120B")
    assert ("BNSS", "183") in expand_equivalent_provisions("CrPC", "164")
    assert ("BNSS", "483") in expand_equivalent_provisions("CrPC", "439")
    assert ("BSA", "39") in expand_equivalent_provisions("EvidenceAct", "45")
    assert ("BSA", "120") in expand_equivalent_provisions("EvidenceAct", "114A")

    # Income-tax Act 1961 <-> 2025 pairs
    assert ("IncomeTax2025", "263") in expand_equivalent_provisions("IncomeTax1961", "139")
    assert ("IncomeTax1961", "139") in expand_equivalent_provisions("IncomeTax2025", "263")
    assert ("IncomeTax2025", "270") in expand_equivalent_provisions("IncomeTax1961", "143")
    assert ("IncomeTax2025", "279") in expand_equivalent_provisions("IncomeTax1961", "147")
    assert ("IncomeTax2025", "280") in expand_equivalent_provisions("IncomeTax1961", "148")
    assert ("IncomeTax2025", "123") in expand_equivalent_provisions("IncomeTax1961", "80C")

    aq = analyze_query("Scrutiny assessment under Income Tax Act, 1961, Section 143")
    assert ("IncomeTax1961", "143") in aq.exact_statute_refs
    assert ("IncomeTax2025", "270") in aq.equivalent_statute_refs
