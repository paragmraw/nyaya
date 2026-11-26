"""Tests for SafeHttpFetcher exponential-backoff retries, HC S3 prefixes, and resilient bulk sync."""

from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml

from nyaya.ingest.cli import _cmd_sync
from nyaya.ingest.fetchers.http import ChecksumMismatchError, SafeHttpFetcher, SecurityError
from nyaya.ingest.lockfile import DEFAULT_LOCKFILE_PATH

_EMPTY_S3_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <IsTruncated>false</IsTruncated>
</ListBucketResult>
"""

_HC_CASE_DETAILS_S3_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Name>indian-high-court-judgments</Name>
  <IsTruncated>false</IsTruncated>
  <Contents>
    <Key>metadata/parquet_case_details/year=2024/court=7_26/case_details.parquet</Key>
    <LastModified>2026-11-22T00:00:00.000Z</LastModified>
    <ETag>"hc-case-details-2024"</ETag>
    <Size>256</Size>
  </Contents>
</ListBucketResult>
"""


def test_http_fetcher_retries_remote_protocol_error_and_503_with_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("nyaya.ingest.fetchers.http.time.sleep", sleeps.append)

    payload = b"recovered-parquet-payload"
    sha = hashlib.sha256(payload).hexdigest()
    attempts = {"count": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise httpx.RemoteProtocolError(
                "Server disconnected without sending a response.",
                request=req,
            )
        if attempts["count"] == 2:
            return httpx.Response(503, text="Service Unavailable")
        if attempts["count"] == 3:
            raise httpx.ReadTimeout("read timed out", request=req)
        return httpx.Response(200, content=payload)

    fetcher = SafeHttpFetcher(
        allowed_domains=["indian-high-court-judgments.s3.ap-south-1.amazonaws.com"],
        max_retries=4,
        retry_backoff_s=1.0,
        transport=httpx.MockTransport(handler),
    )

    got = fetcher.fetch_bytes(
        "https://indian-high-court-judgments.s3.ap-south-1.amazonaws.com/metadata/parquet/x.parquet",
        expected_sha256=sha,
    )
    assert got == payload
    assert attempts["count"] == 4
    assert sleeps == [1.0, 2.0, 4.0]


def test_http_fetcher_retries_persistent_failure_then_raises_and_never_retries_security_error(
    tmp_path: Path,
) -> None:
    # 1. Persistent 503 exhausts max_retries and raises HTTPStatusError
    fail_calls = {"count": 0}

    def always_503(_req: httpx.Request) -> httpx.Response:
        fail_calls["count"] += 1
        return httpx.Response(503)

    fetcher = SafeHttpFetcher(
        allowed_domains=["oss-data-in.vaquill.ai"],
        max_retries=2,
        retry_backoff_s=0.0,
        transport=httpx.MockTransport(always_503),
    )
    with pytest.raises(httpx.HTTPStatusError):
        fetcher.fetch_bytes("https://oss-data-in.vaquill.ai/v2026.08.1/SHA256SUMS.json")
    assert fail_calls["count"] == 3

    # 2. 404 is non-retryable -> called once
    calls_404 = {"count": 0}

    def always_404(_req: httpx.Request) -> httpx.Response:
        calls_404["count"] += 1
        return httpx.Response(404)

    fetcher_404 = SafeHttpFetcher(
        allowed_domains=["oss-data-in.vaquill.ai"],
        max_retries=4,
        retry_backoff_s=0.0,
        transport=httpx.MockTransport(always_404),
    )
    with pytest.raises(httpx.HTTPStatusError):
        fetcher_404.fetch_bytes("https://oss-data-in.vaquill.ai/missing.json")
    assert calls_404["count"] == 1

    # 3. SecurityError (cross-domain redirect & SHA mismatch & banned domain) never retries
    sec_calls = {"count": 0}

    def sec_handler(req: httpx.Request) -> httpx.Response:
        sec_calls["count"] += 1
        if req.url.path == "/redirect":
            return httpx.Response(302, headers={"Location": "https://indiankanoon.org/doc/1"})
        return httpx.Response(200, content=b"valid-bytes")

    fetcher_sec = SafeHttpFetcher(
        allowed_domains=["oss-data-in.vaquill.ai"],
        max_retries=4,
        retry_backoff_s=0.0,
        transport=httpx.MockTransport(sec_handler),
    )
    with pytest.raises(SecurityError, match="Cross-domain|Disallowed"):
        fetcher_sec.fetch_bytes("https://oss-data-in.vaquill.ai/redirect")
    assert sec_calls["count"] == 1

    dest_bad = tmp_path / "bad.parquet"
    with pytest.raises(ChecksumMismatchError, match="SHA-256"):
        fetcher_sec.download_to_file(
            "https://oss-data-in.vaquill.ai/ok",
            dest_bad,
            expected_sha256="0" * 64,
        )
    assert sec_calls["count"] == 2
    assert not dest_bad.exists()
    assert not dest_bad.with_suffix(".parquet.part").exists()


def test_http_fetcher_download_to_file_streams_and_retries_remote_protocol_error(
    tmp_path: Path,
) -> None:
    payload = b"streaming-chunk-" * 5000
    sha = hashlib.sha256(payload).hexdigest()
    attempts = {"count": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise httpx.RemoteProtocolError("Server disconnected", request=req)
        return httpx.Response(200, content=payload)

    fetcher = SafeHttpFetcher(
        allowed_domains=["oss-data-in.vaquill.ai"],
        max_retries=3,
        retry_backoff_s=0.0,
        transport=httpx.MockTransport(handler),
    )
    dest = tmp_path / "big.parquet"
    digest = fetcher.download_to_file(
        "https://oss-data-in.vaquill.ai/v2026.08.1/in_central_legislation.parquet",
        dest,
        expected_sha256=sha,
    )
    assert digest == sha
    assert attempts["count"] == 2
    assert dest.read_bytes() == payload
    assert not dest.with_suffix(".parquet.part").exists()


def test_cmd_sync_hc_uses_parquet_case_details_prefix_and_bulk_sync_warns_on_auxiliary_failures(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    lock_raw: dict[str, Any] = yaml.safe_load(DEFAULT_LOCKFILE_PATH.read_text(encoding="utf-8"))
    allowed = list(lock_raw["allowed_domains"])
    central_bytes = b"PAR1-central"
    central_sha = hashlib.sha256(central_bytes).hexdigest()
    seen_s3_prefixes: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        host = req.url.host
        if host == "oss-data-in.vaquill.ai":
            if req.url.path.endswith("SHA256SUMS.json"):
                return httpx.Response(
                    200,
                    content=json.dumps({"in_central_legislation.parquet": central_sha}).encode("utf-8"),
                )
            if req.url.path.endswith("in_central_legislation.parquet"):
                return httpx.Response(200, content=central_bytes)
            return httpx.Response(404)

        if host == "indian-supreme-court-judgments.s3.ap-south-1.amazonaws.com":
            return httpx.Response(200, text=_EMPTY_S3_XML)

        if host == "indian-high-court-judgments.s3.ap-south-1.amazonaws.com":
            if req.url.params.get("list-type") == "2":
                prefix = req.url.params.get("prefix", "")
                seen_s3_prefixes.append(prefix)
                if "metadata/parquet_case_details/" in prefix:
                    return httpx.Response(200, text=_HC_CASE_DETAILS_S3_XML)
                return httpx.Response(200, text=_EMPTY_S3_XML)
            return httpx.Response(200, content=b"HC-CASE-DETAILS-PARQUET")

        # Auxiliary portals fail with 404 or HTML/placeholder SHA mismatch
        if host == "digiscr.sci.gov.in":
            return httpx.Response(404)
        if host == "prsindia.org":
            return httpx.Response(200, text="<html><body>Not JSON</body></html>")
        return httpx.Response(404)

    http_fetcher = SafeHttpFetcher(
        allowed_domains=allowed,
        max_retries=1,
        retry_backoff_s=0.0,
        transport=httpx.MockTransport(handler),
    )

    caplog.set_level(logging.WARNING, logger="nyaya.ingest.cli")
    work_dir = tmp_path / "bulk_work"
    rc = _cmd_sync(
        lockfile=DEFAULT_LOCKFILE_PATH,
        work_dir=work_dir,
        tier="all",
        sources="all",
        hc_year_from=2024,
        hc_year_to=2024,
        http_fetcher=http_fetcher,
    )
    assert rc == 0

    # HC synced metadata/parquet/ and metadata/parquet_case_details/, never case_details-mobile/
    assert any("metadata/parquet/year=2024/" in p for p in seen_s3_prefixes)
    assert any("metadata/parquet_case_details/year=2024/" in p for p in seen_s3_prefixes)
    assert not any("case_details-mobile" in p for p in seen_s3_prefixes)
    assert (
        work_dir
        / "raw/indian-high-court-judgments/metadata/parquet_case_details/year=2024/court=7_26/case_details.parquet"
    ).read_bytes() == b"HC-CASE-DETAILS-PARQUET"
    assert (work_dir / "raw/vaquill/v2026.08.1/in_central_legislation.parquet").read_bytes() == central_bytes

    # Auxiliary failures logged warnings instead of crashing bulk sync
    assert "escr_judgments" in caplog.text
    assert "prs_2026_delta" in caplog.text

    # Explicit single-source failure must still propagate
    with pytest.raises(httpx.HTTPStatusError):
        _cmd_sync(
            lockfile=DEFAULT_LOCKFILE_PATH,
            work_dir=tmp_path / "single_escr",
            tier="all",
            sources="escr_judgments",
            http_fetcher=http_fetcher,
        )

    with pytest.raises(ChecksumMismatchError):
        _cmd_sync(
            lockfile=DEFAULT_LOCKFILE_PATH,
            work_dir=tmp_path / "single_prs",
            tier="all",
            sources="prs_2026_delta",
            http_fetcher=http_fetcher,
        )

    # SecurityError (banned domain redirect) must STILL propagate even during sources="all"
    def evil_redirect_handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "digiscr.sci.gov.in":
            return httpx.Response(302, headers={"Location": "https://indiankanoon.org/doc/99"})
        return handler(req)

    evil_fetcher = SafeHttpFetcher(
        allowed_domains=allowed,
        max_retries=1,
        retry_backoff_s=0.0,
        transport=httpx.MockTransport(evil_redirect_handler),
    )
    with pytest.raises(SecurityError, match="Cross-domain|Disallowed"):
        _cmd_sync(
            lockfile=DEFAULT_LOCKFILE_PATH,
            work_dir=tmp_path / "evil_work",
            tier="all",
            sources="all",
            hc_year_from=2024,
            hc_year_to=2024,
            http_fetcher=evil_fetcher,
        )


def test_cmd_sync_auxiliary_non_banned_security_error_skipped_in_bulk_but_raised_for_single_source(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    lock_raw: dict[str, Any] = yaml.safe_load(DEFAULT_LOCKFILE_PATH.read_text(encoding="utf-8"))
    allowed = list(lock_raw["allowed_domains"])
    central_bytes = b"PAR1-central"
    central_sha = hashlib.sha256(central_bytes).hexdigest()

    def redirect_handler(req: httpx.Request) -> httpx.Response:
        host = req.url.host
        if host == "oss-data-in.vaquill.ai":
            if req.url.path.endswith("SHA256SUMS.json"):
                return httpx.Response(
                    200,
                    content=json.dumps({"in_central_legislation.parquet": central_sha}).encode("utf-8"),
                )
            if req.url.path.endswith("in_central_legislation.parquet"):
                return httpx.Response(200, content=central_bytes)
            return httpx.Response(404)
        if host in (
            "indian-supreme-court-judgments.s3.ap-south-1.amazonaws.com",
            "indian-high-court-judgments.s3.ap-south-1.amazonaws.com",
        ):
            return httpx.Response(200, text=_EMPTY_S3_XML)
        if host == "indiacode.nic.in":
            return httpx.Response(
                301,
                headers={"Location": "https://prsindia.org/handle/123456789/1362"},
            )
        if host == "legislative.gov.in":
            return httpx.Response(
                302,
                headers={"Location": "https://cdn.example-govt.in/COI_2026.pdf"},
            )
        return httpx.Response(404)

    http_fetcher = SafeHttpFetcher(
        allowed_domains=allowed,
        max_retries=1,
        retry_backoff_s=0.0,
        transport=httpx.MockTransport(redirect_handler),
    )

    caplog.set_level(logging.WARNING, logger="nyaya.ingest.cli")
    rc = _cmd_sync(
        lockfile=DEFAULT_LOCKFILE_PATH,
        work_dir=tmp_path / "bulk_redirect_work",
        tier="all",
        sources="all",
        hc_year_from=2024,
        hc_year_to=2024,
        http_fetcher=http_fetcher,
    )
    assert rc == 0
    assert (
        "Skipping auxiliary source indiacode_central_acts during bulk sync (redirect/domain blocked)"
        in caplog.text
    )
    assert (
        "Skipping auxiliary source legislative_constitution during bulk sync (redirect/domain blocked)"
        in caplog.text
    )

    # Explicit single-source sync must STILL raise SecurityError
    with pytest.raises(SecurityError, match="Cross-domain"):
        _cmd_sync(
            lockfile=DEFAULT_LOCKFILE_PATH,
            work_dir=tmp_path / "single_indiacode",
            tier="all",
            sources="indiacode_central_acts",
            http_fetcher=http_fetcher,
        )

    # Banned host redirect on indiacode must STILL raise SecurityError even in bulk sync
    def banned_redirect_handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "indiacode.nic.in":
            return httpx.Response(302, headers={"Location": "https://indiankanoon.org/doc/42"})
        return redirect_handler(req)

    banned_fetcher = SafeHttpFetcher(
        allowed_domains=allowed,
        max_retries=1,
        retry_backoff_s=0.0,
        transport=httpx.MockTransport(banned_redirect_handler),
    )
    with pytest.raises(SecurityError, match="Disallowed"):
        _cmd_sync(
            lockfile=DEFAULT_LOCKFILE_PATH,
            work_dir=tmp_path / "bulk_banned_work",
            tier="all",
            sources="all",
            hc_year_from=2024,
            hc_year_to=2024,
            http_fetcher=banned_fetcher,
        )


def test_loader_matches_parquet_case_details_without_double_counting(tmp_path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq

    from nyaya.ingest.loader import load_staged_ingest_bundle

    work_dir = tmp_path / "loader_work"
    hc_case_details_dir = (
        work_dir
        / "raw"
        / "indian-high-court-judgments"
        / "metadata"
        / "parquet_case_details"
        / "year=2024"
        / "court=7_26"
    )
    hc_case_details_dir.mkdir(parents=True)
    pq.write_table(
        pa.Table.from_pylist(
            [
                {
                    "cnr": "DLHC010999992024",
                    "filing_date": "2024-02-01",
                    "disposal_nature": "Disposed",
                }
            ]
        ),
        hc_case_details_dir / "case_details.parquet",
    )

    bundle = load_staged_ingest_bundle(work_dir)
    assert len(bundle.hc_mobile_rows) == 1
    assert bundle.hc_mobile_rows[0]["cnr"] == "DLHC010999992024"
    assert bundle.hc_web_rows == []


def test_http_fetcher_allows_allowlisted_www_apex_redirects_and_blocks_cross_domain() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.host == "legislative.gov.in" and req.url.path == "/to-www":
            return httpx.Response(302, headers={"Location": "https://www.legislative.gov.in/final"})
        if req.url.host == "www.legislative.gov.in" and req.url.path == "/to-apex":
            return httpx.Response(301, headers={"Location": "https://legislative.gov.in/final"})
        if req.url.host == "legislative.gov.in" and req.url.path == "/to-other-www":
            return httpx.Response(302, headers={"Location": "https://www.indiacode.nic.in/doc"})
        if req.url.host == "legislative.gov.in" and req.url.path == "/to-sub":
            return httpx.Response(302, headers={"Location": "https://api.legislative.gov.in/doc"})
        return httpx.Response(200, content=b"ok-payload")

    # Both apex and www. are allowlisted -> apex <-> www. redirects succeed
    fetcher = SafeHttpFetcher(
        allowed_domains=[
            "legislative.gov.in",
            "www.legislative.gov.in",
            "www.indiacode.nic.in",
            "api.legislative.gov.in",
        ],
        max_retries=1,
        retry_backoff_s=0.0,
        transport=httpx.MockTransport(handler),
    )
    assert fetcher.fetch_bytes("https://legislative.gov.in/to-www") == b"ok-payload"
    assert fetcher.fetch_bytes("https://www.legislative.gov.in/to-apex") == b"ok-payload"

    # Cross-domain or other-subdomain redirects remain strictly blocked even if allowlisted
    with pytest.raises(SecurityError, match="Cross-domain redirect blocked"):
        fetcher.fetch_bytes("https://legislative.gov.in/to-other-www")

    with pytest.raises(SecurityError, match="Cross-domain redirect blocked"):
        fetcher.fetch_bytes("https://legislative.gov.in/to-sub")

    # Redirect to www. is still blocked if www.host is NOT in allowed_domains
    strict_fetcher = SafeHttpFetcher(
        allowed_domains=["legislative.gov.in"],
        max_retries=1,
        retry_backoff_s=0.0,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(SecurityError, match="Disallowed domain"):
        strict_fetcher.fetch_bytes("https://legislative.gov.in/to-www")
