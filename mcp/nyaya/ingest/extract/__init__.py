"""PDF extraction, lossless legal chunking, citation graph edge extraction, and normalization."""

from __future__ import annotations

from .chunker import (
    CHUNK_OVERLAP,
    CHUNK_SIZE,
    MIN_CHUNK_SIZE,
    LegalChunk,
    chunk_legal_text,
    reconstruct_from_chunks,
)
from .citations import extract_citation_edges
from .normalize import NormalizedRecord, normalize_legal_record
from .pdf import (
    MAX_PDF_BYTES,
    MAX_PDF_PAGES,
    MIN_CHARS_PER_PAGE,
    ExtractedPage,
    PdfExtractionError,
    PdfExtractionResult,
    extract_pdf_bytes,
    extract_pdf_file,
)

__all__ = [
    "CHUNK_OVERLAP",
    "CHUNK_SIZE",
    "ExtractedPage",
    "LegalChunk",
    "MAX_PDF_BYTES",
    "MAX_PDF_PAGES",
    "MIN_CHARS_PER_PAGE",
    "MIN_CHUNK_SIZE",
    "NormalizedRecord",
    "PdfExtractionError",
    "PdfExtractionResult",
    "chunk_legal_text",
    "extract_citation_edges",
    "extract_pdf_bytes",
    "extract_pdf_file",
    "normalize_legal_record",
    "reconstruct_from_chunks",
]
