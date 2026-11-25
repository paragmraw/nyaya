"""Hermetic unit tests for ``nyaya.storage.pdf_fetcher`` (pypdfium2 + zstd LRU)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import httpx
import pytest

from nyaya.exceptions import PdfFetchError
from nyaya.models import JudgmentMeta, SourceProvenance
from nyaya.storage.pdf_fetcher import (
    MAX_PDF_BYTES,
    DirectS3PdfFetcher,
    _clean_pdf_text,
    _split_paragraphs,
    validate_s3_pdf_target,
)
from tests.fixtures.mini_corpus.builder import make_minimal_pdf


def _meta(
    bucket: str = "indian-supreme-court-judgments",
    key: str = "data/pdf/year=1973/english/kesavananda.pdf",
) -> JudgmentMeta:
    return JudgmentMeta(
        case_id="sc:1973:kesavananda",
        court_tier="supreme_court",
        court_code="SCI",
        court_name="Supreme Court of India",
        title="Kesavananda Bharati v. State of Kerala",
        decision_date=date(1973, 4, 24),
        year=1973,
        pdf_s3_bucket=bucket,
        pdf_s3_key=key,
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


def test_ssrf_bucket_and_key_allowlist() -> None:
    url = validate_s3_pdf_target(
        "indian-supreme-court-judgments",
        "data/pdf/year=2024/english/judgment_1.pdf",
    )
    assert url == (
        "https://indian-supreme-court-judgments.s3.ap-south-1.amazonaws.com/"
        "data/pdf/year=2024/english/judgment_1.pdf"
    )

    for bad_bucket, bad_key in [
        ("169.254.169.254", "data/pdf/year=2024/english/a.pdf"),
        ("evil-bucket", "data/pdf/year=2024/english/a.pdf"),
        ("indian-supreme-court-judgments", "data/pdf/year=2024/english/../../etc/passwd.pdf"),
        ("indian-supreme-court-judgments", "data/pdf/year=2024/english/a.pdf?X-Amz=1"),
        ("indian-supreme-court-judgments", "data/pdf/year=1800/english/a.pdf"),
    ]:
        with pytest.raises(PdfFetchError) as exc_info:
            validate_s3_pdf_target(bad_bucket, bad_key)
        assert exc_info.value.reason == "disallowed_key"


async def test_pdf_fetcher_extracts_valid_pdf_and_uses_zstd_lru_cache(tmp_path: Path) -> None:
    pdf_bytes = make_minimal_pdf(
        [
            "1. First paragraph of Kesavananda Bharati regarding basic structure doctrine.\n"
            "2. Second paragraph discussing Article 368 and constitutional amendments.\n"
            "3. Third paragraph analyzing fundamental rights in Part III of the Constitution.",
        ]
    )
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, content=pdf_bytes)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    fetcher = DirectS3PdfFetcher(cache_dir=tmp_path / "cache", client=client)
    try:
        res1 = await fetcher.fetch_and_read(_meta(), mode="paragraphs", para_start=1, para_end=3)
        assert res1.extraction_quality == "verified"
        assert res1.extraction_method == "pypdfium2_s3_pdf"
        assert len(res1.excerpts) == 3
        assert "basic structure" in res1.excerpts[0].text
        assert calls == 1

        # Second read hits disk zstd cache (no extra HTTP call)
        res2 = await fetcher.fetch_and_read(_meta(), mode="search_within", query="Article 368")
        assert calls == 1
        assert len(res2.excerpts) >= 1
        assert any("Article 368" in ex.text for ex in res2.excerpts)
    finally:
        await fetcher.aclose()


async def test_pdf_fetcher_rejects_html_404_and_oversized_and_scanned(tmp_path: Path) -> None:
    # 1. HTML placeholder masquerading as .pdf -> rejected by %PDF- magic check
    html_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=b"<html>404 Not Found</html>")),
        follow_redirects=False,
    )
    fetcher_html = DirectS3PdfFetcher(cache_dir=tmp_path / "c1", client=html_client)
    try:
        with pytest.raises(PdfFetchError) as exc_info:
            await fetcher_html.fetch_and_read(_meta(), mode="overview")
        assert exc_info.value.reason == "corrupt_pdf"
    finally:
        await fetcher_html.aclose()

    # 2. >15 MiB stream cap rejected
    big_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda req: httpx.Response(
                200,
                headers={"Content-Length": str(MAX_PDF_BYTES + 10)},
                content=b"%PDF-1.4\n",
            )
        ),
        follow_redirects=False,
    )
    fetcher_big = DirectS3PdfFetcher(cache_dir=tmp_path / "c2", client=big_client)
    try:
        with pytest.raises(PdfFetchError) as exc_info2:
            await fetcher_big.fetch_and_read(_meta(), mode="overview")
        assert exc_info2.value.reason == "size_limit"
    finally:
        await fetcher_big.aclose()

    # 3. Scanned blank PDF (< 50 chars/page) returns scanned_image_only cleanly
    blank_pdf = make_minimal_pdf([""])
    scan_client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=blank_pdf)),
        follow_redirects=False,
    )
    fetcher_scan = DirectS3PdfFetcher(cache_dir=tmp_path / "c3", client=scan_client)
    try:
        res_scan = await fetcher_scan.fetch_and_read(_meta(), mode="overview")
        assert res_scan.extraction_quality == "scanned_image_only"
        assert res_scan.excerpts == []
        assert res_scan.warning is not None
    finally:
        await fetcher_scan.aclose()


async def test_pdf_fetcher_lru_evicts_oldest_over_limit(tmp_path: Path) -> None:
    pdf_a = make_minimal_pdf(["1. Alpha judgment text with sufficient characters for extraction quality check over fifty chars."])
    pdf_b = make_minimal_pdf(["1. Beta judgment text with sufficient characters for extraction quality check over fifty chars."])

    def handler(request: httpx.Request) -> httpx.Response:
        if "alpha" in str(request.url):
            return httpx.Response(200, content=pdf_a)
        return httpx.Response(200, content=pdf_b)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    # Very small cache budget so second insert evicts the first
    fetcher = DirectS3PdfFetcher(cache_dir=tmp_path / "lru_small", client=client, max_cache_bytes=200)
    try:
        await fetcher.fetch_and_read(
            _meta(key="data/pdf/year=2024/english/alpha.pdf"),
            mode="overview",
        )
        await fetcher.fetch_and_read(
            _meta(key="data/pdf/year=2024/english/beta.pdf"),
            mode="overview",
        )
        cached_files = list((tmp_path / "lru_small" / "pdf_cache").glob("*.json.zst"))
        assert len(cached_files) <= 1
    finally:
        await fetcher.aclose()


async def test_pdf_fetcher_default_client_instantiation_and_offline_error(tmp_path: Path) -> None:
    fetcher = DirectS3PdfFetcher(cache_dir=tmp_path / "default_client", offline_only=True)
    try:
        assert fetcher._owns_client is True
        assert fetcher._client.timeout == httpx.Timeout(connect=3.0, read=8.0, write=8.0, pool=3.0)
        with pytest.raises(PdfFetchError) as exc_info:
            await fetcher.fetch_and_read(_meta(), mode="overview")
        assert exc_info.value.reason == "http_error"
    finally:
        await fetcher.aclose()


def test_clean_pdf_text_strips_scr_headers_and_repairs_ligatures() -> None:
    raw = (
        "123 SUPREME COURT REPORTS [2024] 1 S.C.R.\r\n"
        "[2024] 1 S.C.R. 123\r\n"
        "[45]\r\n"
        "The Offi cer found Insuffi ciently suffici ent and diff erent evidence. "
        "It refl ected eff ective affi rmation of the specifi c confi rmation. "
        "Signifi cant notifi cation was classifi ed, qualifi ed, identifi ed, and fulfi lled "
        "despite diffi culty over the off ence and profi t defi nition. "
        "The certifi cate was veri fied and modifi ed after FI NDING was fi led as fi nal "
        "without confl ict. 456 SUPREME COURT REPORTS [2023] 2 S.C.R. Next sentence."
    )
    cleaned = _clean_pdf_text(raw)
    assert "SUPREME COURT REPORTS" not in cleaned
    assert "[2024] 1 S.C.R." not in cleaned
    assert "\n[45]\n" not in cleaned
    assert "Officer" in cleaned
    assert "Insufficiently" in cleaned
    assert "sufficient" in cleaned
    assert "different" in cleaned
    assert "reflected" in cleaned
    assert "effective" in cleaned
    assert "affirmation" in cleaned
    assert "specific" in cleaned
    assert "confirmation" in cleaned
    assert "Significant" in cleaned
    assert "notification" in cleaned
    assert "classified" in cleaned
    assert "qualified" in cleaned
    assert "identified" in cleaned
    assert "fulfilled" in cleaned
    assert "difficulty" in cleaned
    assert "offence" in cleaned
    assert "profit" in cleaned
    assert "definition" in cleaned
    assert "certificate" in cleaned
    assert "verified" in cleaned
    assert "modified" in cleaned
    assert "FINDING" in cleaned
    assert "filed" in cleaned
    assert "final" in cleaned
    assert "conflict" in cleaned
    assert "Next sentence." in cleaned


def test_split_paragraphs_splits_mega_blob_under_1400() -> None:
    mega_p1 = "\n\n".join(
        [f"HEADING {i}\n" + ("This is a long legal sentence in the preamble. " * 20) for i in range(15)]
    )
    assert len(mega_p1) > 10_000
    paras = _split_paragraphs([mega_p1, "1. First numbered paragraph."])
    assert len(paras) > 5
    assert all(len(p) <= 1400 for p in paras)
    assert paras[-1].startswith("1. First numbered paragraph.")


async def test_overview_skips_leading_header_noise_when_numbered_paras_follow(tmp_path: Path) -> None:
    pdf_bytes = make_minimal_pdf(
        [
            "IN THE SUPREME COURT OF INDIA\nCIVIL APPELLATE JURISDICTION\n\n"
            "FOR THE APPELLANT: Mr. Senior Counsel, Adv.\nFOR THE RESPONDENT: Ms. Counsel, Adv.\n\n"
            "1. Leave granted. This appeal arises out of a dispute under Section 138 of the NI Act.\n"
            "2. The High Court dismissed the petition without appreciating the settled legal position.\n"
            "3. Accordingly, the appeal is allowed and the impugned judgment is set aside.",
        ]
    )
    client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda req: httpx.Response(200, content=pdf_bytes)),
        follow_redirects=False,
    )
    fetcher = DirectS3PdfFetcher(cache_dir=tmp_path / "overview_skip", client=client)
    try:
        res = await fetcher.fetch_and_read(_meta(), mode="overview")
        assert res.excerpts
        assert res.excerpts[0].text.startswith("1. Leave granted.")
        assert res.excerpts[0].para_num == 2
    finally:
        await fetcher.aclose()
