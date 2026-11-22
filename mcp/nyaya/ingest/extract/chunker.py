"""Lossless structure-aware legal chunker for Indian judgments and statutes."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass

from ...sanitize import MAX_CHUNK_BYTES
from .pdf import ExtractedPage

CHUNK_SIZE = 4096
CHUNK_OVERLAP = 512
MIN_CHUNK_SIZE = 300

_NUMBERED_PARA_RE = re.compile(r"(?:^|\n)(\d{1,4})\.\s")


@dataclass
class LegalChunk:
    """Single lossless legal text chunk with character, page, and paragraph offsets."""

    doc_id: str
    chunk_index: int
    total_chunks: int
    char_start: int
    char_end: int
    page_start: int
    page_end: int
    para_start: int
    para_end: int
    section_type: str
    text: str
    chunk_sha256: str


def _find_best_break(text: str, start: int, target_end: int, min_size: int) -> int:
    """Find a legal-boundary split point near ``target_end`` while keeping UTF-8 <= 16 KiB."""
    n = len(text)
    if target_end >= n:
        end = n
    else:
        floor = min(start + max(min_size, (target_end - start) // 2), target_end)
        window = text[floor:target_end]
        end = target_end

        # Priority 1: triple newline section break
        pos = window.rfind("\n\n\n")
        if pos != -1:
            end = floor + pos + 3
        else:
            # Priority 2: double newline paragraph break
            pos = window.rfind("\n\n")
            if pos != -1:
                end = floor + pos + 2
            else:
                # Priority 3: single newline
                pos = window.rfind("\n")
                if pos != -1:
                    end = floor + pos + 1
                else:
                    # Priority 4: sentence boundary ". "
                    pos = window.rfind(". ")
                    if pos != -1:
                        end = floor + pos + 2
                    else:
                        # Priority 5: whitespace
                        pos = window.rfind(" ")
                        if pos != -1:
                            end = floor + pos + 1

    # Enforce MAX_CHUNK_BYTES UTF-8 cap
    while len(text[start:end].encode("utf-8")) > MAX_CHUNK_BYTES and end > start + 1:
        end -= max(1, (end - start) // 8)
    return end


def _infer_page_span(
    char_start: int,
    char_end: int,
    pages: Sequence[ExtractedPage] | None,
) -> tuple[int, int]:
    if not pages:
        return 1, 1
    p_start = pages[0].page_num
    p_end = pages[-1].page_num
    for p in pages:
        if p.char_end >= char_start:
            p_start = p.page_num
            break
    for p in pages:
        if p.char_start <= char_end:
            p_end = p.page_num
    return p_start, max(p_start, p_end)


def _infer_para_span(snippet: str, chunk_idx: int) -> tuple[int, int]:
    nums = [int(m.group(1)) for m in _NUMBERED_PARA_RE.finditer(snippet)]
    if nums:
        return min(nums), max(nums)
    fallback = chunk_idx + 1
    return fallback, fallback


def chunk_legal_text(
    text: str,
    *,
    doc_id: str,
    chunk_size: int = CHUNK_SIZE,
    chunk_overlap: int = CHUNK_OVERLAP,
    min_chunk_size: int = MIN_CHUNK_SIZE,
    pages: Sequence[ExtractedPage] | None = None,
    section_type: str = "body",
) -> list[LegalChunk]:
    """Split legal text into overlapping boundary-aligned chunks (`<= 16 KiB` each)
    with exact `char_start` and `char_end` offsets enabling 100% lossless reconstruction.
    """
    if not text:
        return []

    n = len(text)
    spans: list[tuple[int, int]] = []
    start = 0

    while start < n:
        target_end = min(start + chunk_size, n)
        end = _find_best_break(text, start, target_end, min_chunk_size)
        if end <= start:
            end = min(start + chunk_size, n)
        spans.append((start, end))
        if end >= n:
            break
        next_start = max(start + 1, end - chunk_overlap)
        # Align overlap start cleanly to a paragraph or line boundary if nearby
        nl = text.find("\n", next_start, min(end, next_start + 120))
        if nl != -1 and nl + 1 < end:
            next_start = nl + 1
        start = next_start

    total = len(spans)
    chunks: list[LegalChunk] = []
    for idx, (c_start, c_end) in enumerate(spans):
        slice_text = text[c_start:c_end]
        p_start, p_end = _infer_page_span(c_start, c_end, pages)
        para_start, para_end = _infer_para_span(slice_text, idx)
        sha = hashlib.sha256(slice_text.encode("utf-8")).hexdigest()
        chunks.append(
            LegalChunk(
                doc_id=doc_id,
                chunk_index=idx,
                total_chunks=total,
                char_start=c_start,
                char_end=c_end,
                page_start=p_start,
                page_end=p_end,
                para_start=para_start,
                para_end=para_end,
                section_type=section_type,
                text=slice_text,
                chunk_sha256=sha,
            )
        )
    return chunks


def reconstruct_from_chunks(chunks: Sequence[LegalChunk]) -> str:
    """Losslessly reassemble the full document text from overlapping `LegalChunk` objects."""
    if not chunks:
        return ""
    ordered = sorted(chunks, key=lambda c: c.chunk_index)
    out: list[str] = []
    cursor = 0
    for ch in ordered:
        if ch.char_end <= cursor:
            continue
        offset = max(0, cursor - ch.char_start)
        out.append(ch.text[offset:])
        cursor = ch.char_end
    return "".join(out)
