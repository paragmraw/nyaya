"""Apache-2.0 `pypdfium2` page-tracked PDF extractor with `%PDF-` magic check,
15 MiB / 500-page bomb guard, and scanned-PDF quality detection.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import pypdfium2 as pdfium

from .ocr import OcrEngine, get_default_ocr_engine, render_pdf_page_to_png

MAX_PDF_BYTES = 15 * 1024 * 1024
MAX_PDF_PAGES = 500
MIN_CHARS_PER_PAGE = 100.0


class PdfExtractionError(ValueError):
    """Raised when a PDF fails `%PDF-` magic check, size cap, page cap, or parsing."""


@dataclass(frozen=True)
class ExtractedPage:
    """Single extracted PDF page with character offsets into the joined document text."""

    page_num: int
    text: str
    char_start: int
    char_end: int


@dataclass
class PdfExtractionResult:
    """Full page-tracked extraction result from a court or statute PDF."""

    full_text: str
    page_count: int
    chars_per_page: float
    extraction_quality: Literal["verified", "scanned_image_only"]
    text_availability: Literal["indexed_bm25", "scanned_image_only"]
    text_sha256: str
    pages: list[ExtractedPage] = field(default_factory=list)
    ocr_used: bool = False


def _assemble_pages(raw_pages: list[str]) -> tuple[str, list[ExtractedPage], int]:
    pages: list[ExtractedPage] = []
    parts: list[str] = []
    cursor = 0
    total_non_ws = 0
    for idx, raw_page_text in enumerate(raw_pages):
        norm_page = raw_page_text.replace("\r\n", "\n").replace("\r", "\n")
        if idx > 0 and parts:
            parts.append("\n\n")
            cursor += 2
        start = cursor
        parts.append(norm_page)
        cursor += len(norm_page)
        total_non_ws += len(norm_page.strip())
        pages.append(
            ExtractedPage(
                page_num=idx + 1,
                text=norm_page,
                char_start=start,
                char_end=cursor,
            )
        )
    return "".join(parts), pages, total_non_ws


def extract_pdf_bytes(
    pdf_bytes: bytes,
    *,
    max_bytes: int = MAX_PDF_BYTES,
    max_pages: int = MAX_PDF_PAGES,
    min_chars_per_page: float = MIN_CHARS_PER_PAGE,
    enable_ocr: bool = False,
    ocr_engine: OcrEngine | None = None,
) -> PdfExtractionResult:
    """Validate and extract text page-by-page from raw PDF bytes using `pypdfium2`."""
    if not pdf_bytes or not pdf_bytes.startswith(b"%PDF-"):
        raise PdfExtractionError(
            "Invalid PDF magic header: payload does not start with '%PDF-' (possible HTML placeholder)."
        )
    if len(pdf_bytes) > max_bytes:
        raise PdfExtractionError(
            f"PDF payload size ({len(pdf_bytes)} bytes) exceeds 15 MiB cap ({max_bytes} bytes)."
        )

    try:
        doc = pdfium.PdfDocument(pdf_bytes)
    except Exception as exc:
        raise PdfExtractionError(f"pypdfium2 failed to open PDF: {exc}") from exc

    try:
        page_count = len(doc)
        if page_count > max_pages:
            raise PdfExtractionError(
                f"PDF page count ({page_count}) exceeds {max_pages}-page safety cap."
            )

        raw_pages: list[str] = []
        for idx in range(page_count):
            page = doc[idx]
            textpage = page.get_textpage()
            try:
                raw_pages.append(textpage.get_text_range() or "")
            finally:
                textpage.close()
                page.close()

        full_text, pages, total_non_ws = _assemble_pages(raw_pages)
        avg_chars = (total_non_ws / page_count) if page_count > 0 else 0.0
        is_scanned = avg_chars < min_chars_per_page
        ocr_used = False

        if is_scanned and enable_ocr and page_count > 0:
            engine = ocr_engine if ocr_engine is not None else get_default_ocr_engine()
            if engine is not None and engine.is_available():
                ocr_raw_pages: list[str] = []
                for idx in range(page_count):
                    page = doc[idx]
                    try:
                        png_bytes = render_pdf_page_to_png(page)
                        ocr_raw_pages.append(engine.extract_text(png_bytes))
                    finally:
                        page.close()
                ocr_full, ocr_pages, ocr_non_ws = _assemble_pages(ocr_raw_pages)
                ocr_avg = ocr_non_ws / page_count
                if ocr_avg >= min_chars_per_page:
                    full_text = ocr_full
                    pages = ocr_pages
                    avg_chars = ocr_avg
                    is_scanned = False
                    ocr_used = True

        quality: Literal["verified", "scanned_image_only"] = (
            "scanned_image_only" if is_scanned else "verified"
        )
        availability: Literal["indexed_bm25", "scanned_image_only"] = (
            "scanned_image_only" if is_scanned else "indexed_bm25"
        )
        sha = hashlib.sha256(full_text.encode("utf-8")).hexdigest()
        return PdfExtractionResult(
            full_text=full_text,
            page_count=page_count,
            chars_per_page=round(avg_chars, 2),
            extraction_quality=quality,
            text_availability=availability,
            text_sha256=sha,
            pages=pages,
            ocr_used=ocr_used,
        )
    finally:
        doc.close()


def extract_pdf_file(
    path: Path | str,
    *,
    max_bytes: int = MAX_PDF_BYTES,
    max_pages: int = MAX_PDF_PAGES,
    min_chars_per_page: float = MIN_CHARS_PER_PAGE,
    enable_ocr: bool = False,
    ocr_engine: OcrEngine | None = None,
) -> PdfExtractionResult:
    """Read a PDF file from disk (checking size before full read) and extract text."""
    fpath = Path(path)
    size = fpath.stat().st_size
    if size > max_bytes:
        raise PdfExtractionError(
            f"PDF file {fpath} size ({size} bytes) exceeds 15 MiB cap ({max_bytes} bytes)."
        )
    return extract_pdf_bytes(
        fpath.read_bytes(),
        max_bytes=max_bytes,
        max_pages=max_pages,
        min_chars_per_page=min_chars_per_page,
        enable_ocr=enable_ocr,
        ocr_engine=ocr_engine,
    )
