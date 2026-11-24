"""Direct anonymous AWS S3 HTTPS PDF fetcher with pypdfium2 and 256 MB zstd disk LRU."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Literal

import httpx
import pypdfium2 as pdfium
import zstandard as zstd

from ..exceptions import PdfFetchError
from ..models import JudgmentContent, JudgmentExcerpt, JudgmentMeta
from ..retrieval.paragraph_bm25 import ParagraphBM25
from ..sanitize import strip_control_chars

ALLOWED_PDF_BUCKETS = frozenset(
    {
        "indian-supreme-court-judgments",
        "indian-high-court-judgments",
    }
)

PDF_KEY_RE = re.compile(
    r"^data/pdf/year=(19[5-9]\d|20[0-3]\d)/"
    r"(?:english|regional|court=[A-Za-z0-9_]+/bench=[A-Za-z0-9_.-]+)/"
    r"[A-Za-z0-9_.-]+\.pdf$"
)

MAX_PDF_BYTES = 15 * 1024 * 1024  # 15 MiB
MAX_PDF_PAGES = 500
DEFAULT_LRU_MAX_BYTES = 256 * 1024 * 1024  # 256 MiB
MIN_CHARS_PER_PAGE = 50.0
MIN_PRINTABLE_RATIO = 0.70

_PARA_SPLIT_RE = re.compile(
    r"(?m)(?=^\s*(?:\d{1,4}\.|\[\d{1,4}\]|\(\d{1,4}\))\s+)"
)


def validate_s3_pdf_target(bucket: str | None, key: str | None) -> str:
    """Strictly validate S3 bucket + key against SSRF/path-traversal and build HTTPS URL."""
    if not bucket or not key:
        raise PdfFetchError(
            "Missing S3 bucket or key for judgment PDF.",
            reason="disallowed_key",
        )
    if bucket not in ALLOWED_PDF_BUCKETS:
        raise PdfFetchError(
            f"Disallowed PDF S3 bucket: {bucket!r}",
            reason="disallowed_key",
        )
    if any(bad in key for bad in ("..", "%", "?", "#", "\\", "//")):
        raise PdfFetchError(
            f"Unsafe characters in PDF S3 key: {key!r}",
            reason="disallowed_key",
        )
    if not PDF_KEY_RE.match(key):
        raise PdfFetchError(
            f"PDF key does not match allowlisted prefix/schema: {key!r}",
            reason="disallowed_key",
        )
    return f"https://{bucket}.s3.ap-south-1.amazonaws.com/{key}"


def _split_paragraphs(pages: list[str]) -> list[str]:
    joined = "\n\n".join(p.strip() for p in pages if p.strip())
    if not joined:
        return []
    parts = [p.strip() for p in _PARA_SPLIT_RE.split(joined) if p.strip()]
    if len(parts) <= 1:
        parts = [p.strip() for p in re.split(r"\n\s*\n", joined) if p.strip()]
    return parts


def _extract_pdf_sync(
    pdf_bytes: bytes,
    page_start: int | None = None,
    page_end: int | None = None,
) -> dict[str, Any]:
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        total_pages = len(doc)
        truncated_pages = total_pages > MAX_PDF_PAGES
        if page_start is not None or page_end is not None:
            start_idx = max(0, (page_start or 1) - 1)
            end_idx = min(total_pages, page_end or min(total_pages, start_idx + 10))
            indices = list(range(start_idx, max(start_idx, end_idx)))
        elif truncated_pages:
            indices = list(range(0, 50)) + list(range(max(50, total_pages - 20), total_pages))
        else:
            indices = list(range(total_pages))

        pages_text: list[str] = []
        for idx in indices:
            page = doc[idx]
            tp = None
            try:
                tp = page.get_textpage()
                raw = tp.get_text_bounded() or ""
                pages_text.append(strip_control_chars(raw))
            finally:
                if tp is not None:
                    tp.close()
                page.close()

        extracted_count = max(1, len(pages_text))
        all_text = "\n".join(pages_text)
        non_ws_chars = sum(1 for c in all_text if not c.isspace())
        chars_per_page = non_ws_chars / extracted_count
        printable_chars = sum(1 for c in all_text if c.isprintable() or c in "\n\r\t")
        printable_ratio = (printable_chars / len(all_text)) if all_text else 0.0

        if chars_per_page < MIN_CHARS_PER_PAGE or printable_ratio < MIN_PRINTABLE_RATIO:
            quality = "scanned_image_only"
            paragraphs: list[str] = []
        else:
            quality = "truncated" if truncated_pages else "verified"
            paragraphs = _split_paragraphs(pages_text)

        return {
            "total_pages": total_pages,
            "pages": pages_text,
            "paragraphs": paragraphs,
            "chars_per_page": round(chars_per_page, 2),
            "extraction_quality": quality,
            "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
        }
    finally:
        doc.close()


class _DiskZstdLru:
    """256 MiB disk-backed zstd LRU cache tracked by ``lru.sqlite``."""

    def __init__(self, root: Path, max_bytes: int = DEFAULT_LRU_MAX_BYTES) -> None:
        self.root = root
        self.max_bytes = max(64, int(max_bytes))
        self.target_bytes = int(self.max_bytes * 0.85)
        self.db_path = self.root / "lru.sqlite"
        self._lock = threading.Lock()
        self._initialized = False

    def _ensure_init(self) -> None:
        if self._initialized:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        con = sqlite3.connect(self.db_path, timeout=2.0)
        try:
            con.execute("PRAGMA journal_mode=WAL")
            con.execute("PRAGMA busy_timeout=2000")
            con.execute(
                "CREATE TABLE IF NOT EXISTS entries ("
                "cache_key TEXT PRIMARY KEY, "
                "byte_size INTEGER NOT NULL, "
                "last_accessed REAL NOT NULL)"
            )
            con.commit()
        finally:
            con.close()
        self._initialized = True

    def get(self, cache_key: str) -> dict[str, Any] | None:
        with self._lock:
            self._ensure_init()
            fpath = self.root / f"{cache_key}.json.zst"
            if not fpath.is_file():
                return None
            try:
                compressed = fpath.read_bytes()
                raw = zstd.ZstdDecompressor().decompress(compressed)
                data: dict[str, Any] = json.loads(raw.decode("utf-8"))
                con = sqlite3.connect(self.db_path, timeout=2.0)
                try:
                    con.execute(
                        "UPDATE entries SET last_accessed = ? WHERE cache_key = ?",
                        (time.time(), cache_key),
                    )
                    con.commit()
                finally:
                    con.close()
                return data
            except Exception:
                return None

    def put(self, cache_key: str, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload).encode("utf-8")
        compressed = zstd.ZstdCompressor(level=3).compress(raw)
        size = len(compressed)
        with self._lock:
            self._ensure_init()
            fpath = self.root / f"{cache_key}.json.zst"
            fpath.write_bytes(compressed)
            now = time.time()
            con = sqlite3.connect(self.db_path, timeout=2.0)
            try:
                con.execute(
                    "INSERT OR REPLACE INTO entries (cache_key, byte_size, last_accessed) VALUES (?, ?, ?)",
                    (cache_key, size, now),
                )
                total = int(con.execute("SELECT coalesce(sum(byte_size), 0) FROM entries").fetchone()[0])
                if total > self.max_bytes:
                    rows = con.execute(
                        "SELECT cache_key, byte_size FROM entries ORDER BY last_accessed ASC"
                    ).fetchall()
                    for evict_key, evict_size in rows:
                        if total <= self.target_bytes or evict_key == cache_key:
                            if evict_key == cache_key and len(rows) > 1:
                                continue
                            if total <= self.target_bytes:
                                break
                        con.execute("DELETE FROM entries WHERE cache_key = ?", (evict_key,))
                        evict_path = self.root / f"{evict_key}.json.zst"
                        if evict_path.is_file():
                            evict_path.unlink(missing_ok=True)
                        total -= int(evict_size)
                con.commit()
            finally:
                con.close()


class DirectS3PdfFetcher:
    """Async direct S3 HTTPS PDF fetcher using Apache-2.0 ``pypdfium2``."""

    def __init__(
        self,
        *,
        cache_dir: Path,
        client: httpx.AsyncClient | None = None,
        offline_only: bool = False,
        max_cache_bytes: int = DEFAULT_LRU_MAX_BYTES,
    ) -> None:
        self.cache = _DiskZstdLru(cache_dir / "pdf_cache", max_bytes=max_cache_bytes)
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(connect=3.0, read=8.0, write=8.0, pool=3.0),
        )
        self.offline_only = offline_only
        self._sem = asyncio.Semaphore(4)

    async def _download_pdf_bytes(self, url: str) -> bytes:
        if self.offline_only:
            raise PdfFetchError(
                "PDF network fetch disabled in offline_only mode.",
                reason="http_error",
            )
        try:
            async with self._client.stream("GET", url) as resp:
                if resp.status_code != 200:
                    raise PdfFetchError(
                        f"S3 returned HTTP {resp.status_code} for {url}",
                        reason="http_error",
                    )
                cl_raw = resp.headers.get("Content-Length")
                if cl_raw is not None:
                    try:
                        if int(cl_raw) > MAX_PDF_BYTES:
                            raise PdfFetchError(
                                f"PDF Content-Length {cl_raw} exceeds 15 MiB cap.",
                                reason="size_limit",
                            )
                    except ValueError:
                        pass
                buf = bytearray()
                checked_magic = False
                async for chunk in resp.aiter_bytes(65_536):
                    buf.extend(chunk)
                    if not checked_magic and len(buf) >= 5:
                        if bytes(buf[:5]) != b"%PDF-":
                            raise PdfFetchError(
                                "Response does not begin with %PDF- magic header.",
                                reason="corrupt_pdf",
                            )
                        checked_magic = True
                    if len(buf) > MAX_PDF_BYTES:
                        raise PdfFetchError(
                            "Streamed PDF exceeds 15 MiB size limit.",
                            reason="size_limit",
                        )
                if len(buf) < 5 or bytes(buf[:5]) != b"%PDF-":
                    raise PdfFetchError(
                        "Truncated or invalid PDF payload (%PDF- missing).",
                        reason="corrupt_pdf",
                    )
                return bytes(buf)
        except PdfFetchError:
            raise
        except httpx.TimeoutException as exc:
            raise PdfFetchError(f"S3 PDF fetch timed out: {exc}", reason="timeout") from exc
        except httpx.HTTPError as exc:
            raise PdfFetchError(f"S3 PDF HTTP error: {exc}", reason="http_error") from exc

    async def fetch_and_read(
        self,
        meta: JudgmentMeta,
        *,
        mode: Literal["overview", "paragraphs", "pages", "search_within", "docket"] = "overview",
        para_start: int | None = None,
        para_end: int | None = None,
        page_start: int | None = None,
        page_end: int | None = None,
        query: str | None = None,
        max_chars: int = 24_000,
    ) -> JudgmentContent:
        url = validate_s3_pdf_target(meta.pdf_s3_bucket, meta.pdf_s3_key)
        cache_key = hashlib.sha256(f"{meta.pdf_s3_bucket}/{meta.pdf_s3_key}".encode()).hexdigest()

        extracted = self.cache.get(cache_key)
        if extracted is None:
            pdf_bytes = await self._download_pdf_bytes(url)
            async with self._sem:
                extracted = await asyncio.to_thread(_extract_pdf_sync, pdf_bytes)
            if extracted["extraction_quality"] != "scanned_image_only":
                self.cache.put(cache_key, extracted)

        quality = str(extracted["extraction_quality"])
        chars_per_page = float(extracted["chars_per_page"])
        total_pages = int(extracted["total_pages"])
        paragraphs: list[str] = list(extracted["paragraphs"])
        pages: list[str] = list(extracted["pages"])
        total_paras = len(paragraphs)

        if quality == "scanned_image_only":
            return JudgmentContent(
                case_id=meta.case_id,
                mode=mode,
                extraction_quality="scanned_image_only",
                extraction_method="pypdfium2_s3_pdf",
                excerpts=[],
                total_paragraphs=0,
                total_pages=total_pages,
                chars_per_page=chars_per_page,
                pdf_url=url,
                warning="Scanned image PDF without embedded text layer; no OCR text available.",
                content_sha256=extracted.get("sha256"),
            )

        excerpts: list[JudgmentExcerpt] = []
        has_more = False
        next_para: int | None = None
        next_page: int | None = None
        budget = max(1000, min(int(max_chars), 30_000))

        if mode == "overview":
            head = [(i + 1, paragraphs[i]) for i in range(min(8, total_paras))]
            tail_start = max(len(head), total_paras - 8)
            tail = [(i + 1, paragraphs[i]) for i in range(tail_start, total_paras)]
            used = 0
            for pnum, txt in head + tail:
                if used + len(txt) > budget and excerpts:
                    has_more = True
                    break
                excerpts.append(JudgmentExcerpt(para_num=pnum, text=txt))
                used += len(txt)

        elif mode == "paragraphs":
            start = max(1, para_start or 1)
            end = min(total_paras, para_end or min(total_paras, start + 29))
            end = min(end, start + 29)  # max 30 paragraphs per window
            used = 0
            for pnum in range(start, end + 1):
                txt = paragraphs[pnum - 1]
                if used + len(txt) > budget and excerpts:
                    has_more = True
                    next_para = pnum
                    break
                excerpts.append(JudgmentExcerpt(para_num=pnum, text=txt))
                used += len(txt)
            if not has_more and end < total_paras:
                has_more = True
                next_para = end + 1

        elif mode == "pages":
            start = max(1, page_start or 1)
            end = min(len(pages), page_end or min(len(pages), start + 9))
            end = min(end, start + 9)  # max 10 pages per window
            used = 0
            for pg in range(start, end + 1):
                txt = pages[pg - 1]
                if used + len(txt) > budget and excerpts:
                    has_more = True
                    next_page = pg
                    break
                excerpts.append(JudgmentExcerpt(page_num=pg, text=txt))
                used += len(txt)
            if not has_more and end < len(pages):
                has_more = True
                next_page = end + 1

        elif mode == "search_within":
            bm25 = ParagraphBM25(paragraphs)
            hits = bm25.rank(query or "", top_k=5, context_window=1)
            used = 0
            for idx, sc in hits:
                txt = paragraphs[idx]
                if used + len(txt) > budget and excerpts:
                    break
                excerpts.append(
                    JudgmentExcerpt(
                        para_num=idx + 1,
                        text=txt,
                        score=round(sc, 4),
                    )
                )
                used += len(txt)

        return JudgmentContent(
            case_id=meta.case_id,
            mode=mode,
            extraction_quality=quality,  # type: ignore[arg-type]
            extraction_method="pypdfium2_s3_pdf",
            excerpts=excerpts,
            total_paragraphs=total_paras,
            total_pages=total_pages,
            chars_per_page=chars_per_page,
            has_more=has_more,
            next_para=next_para,
            next_page=next_page,
            pdf_url=url,
            content_sha256=extracted.get("sha256"),
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()
