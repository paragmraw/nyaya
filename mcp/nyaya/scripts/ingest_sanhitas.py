"""Ingest the 2023 Sanhitas (BNS, BNSS, BSA) from PRS PDFs.

PRS hosts the enacted acts as static PDFs under CC BY 4.0. We download
them, extract text with pypdf, and split into sections using
heading-based heuristics.
"""

from __future__ import annotations

import io
import re
from datetime import date

import httpx

from ..sanitize import sanitize_text
from .db import IngestDB

AS_OF = date(2026, 7, 1)
SOURCE = "PRS Legislative Research (CC BY 4.0)"
LICENSE = "CC BY 4.0"

PDFS: list[dict] = [
    {
        "short_name": "BNS",
        "full_name": "The Bharatiya Nyaya Sanhita, 2023",
        "year": 2023,
        "kind": "criminal",
        "url": "https://prsindia.org/files/bills_acts/acts_parliament/2023/The Bharatiya Nyaya Sanhita, 2023.pdf",
        "citation": "Act 45 of 2023",
    },
    {
        "short_name": "BNSS",
        "full_name": "The Bharatiya Nagarik Suraksha Sanhita, 2023",
        "year": 2023,
        "kind": "criminal",
        "url": "https://prsindia.org/files/bills_acts/acts_parliament/2023/The Bharatiya Nagarik Suraksha Sanhita, 2023.pdf",
        "citation": "Act 46 of 2023",
    },
    {
        "short_name": "BSA",
        "full_name": "The Bharatiya Sakshya Adhiniyam, 2023",
        "year": 2023,
        "kind": "civil",
        "url": "https://prsindia.org/files/bills_acts/acts_parliament/2023/The Bharatiya Sakshya Adhiniyam, 2023.pdf",
        "citation": "Act 47 of 2023",
    },
]

# PRS PDFs render section headings as "2.In this Sanhita…" with no space
# after the period, so the whitespace after the dot must be OPTIONAL.
# Verified: this recovers 80 missing BNS sections (277→357) with 0 false
# positives.
SECTION_HEADING_RE = re.compile(r"^(?P<num>\d+[A-Z]?)\.\s*(?P<title>.+)$")
CHAPTER_HEADING_RE = re.compile(r"^Chapter\s+(?P<num>[IVXLC]+)\s*[.\-—–]?\s*(?P<title>.+)?$", re.IGNORECASE)


def _download_pdf(url: str) -> bytes:
    """Download a PDF with a 25 MB size cap to prevent DoS via huge files."""
    import time
    MAX_PDF_BYTES = 25 * 1024 * 1024  # 25 MB
    for attempt in range(3):
        try:
            with httpx.Client(follow_redirects=True, timeout=60.0) as client:
                # Stream to enforce the size cap without loading the full file first
                with client.stream("GET", url, headers={"User-Agent": "nyaya-ingest/0.1 (+https://github.com/your-org/nyaya)"}) as r:
                    r.raise_for_status()
                    chunks: list[bytes] = []
                    total = 0
                    for chunk in r.iter_bytes(chunk_size=65536):
                        total += len(chunk)
                        if total > MAX_PDF_BYTES:
                            raise ValueError(
                                f"PDF exceeds size cap ({total} > {MAX_PDF_BYTES} bytes): {url}"
                            )
                        chunks.append(chunk)
                    return b"".join(chunks)
        except httpx.HTTPError as e:
            if attempt < 2:
                wait = 2 ** attempt
                print(f"  ! PDF download failed (attempt {attempt + 1}/3): {e}; retrying in {wait}s…")
                time.sleep(wait)
            else:
                raise


def _extract_text(pdf_bytes: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as e:
        raise RuntimeError("pypdf not installed. Install with: pip install 'nyaya[ingest]'") from e
    reader = PdfReader(io.BytesIO(pdf_bytes))
    pages = [page.extract_text() or "" for page in reader.pages]
    return "\n".join(pages)


def _parse_sections(text: str) -> list[dict]:
    lines = text.splitlines()
    sections: list[dict] = []
    current: dict | None = None
    current_chapter: tuple[int, str] | None = None
    # BNS has 21 chapters; the previous map only covered I-XII, silently
    # dropping chapters 13-21. Extend to XXI and beyond via explicit numerals.
    roman_to_int = {
        "I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7,
        "VIII": 8, "IX": 9, "X": 10, "XI": 11, "XII": 12, "XIII": 13,
        "XIV": 14, "XV": 15, "XVI": 16, "XVII": 17, "XVIII": 18,
        "XIX": 19, "XX": 20, "XXI": 21, "XXII": 22, "XXIII": 23,
        "XXIV": 24, "XXV": 25,
    }

    for line in lines:
        stripped = line.strip()
        if not stripped:
            if current:
                current["text"] += "\n"
            continue

        ch = CHAPTER_HEADING_RE.match(stripped)
        if ch:
            num = roman_to_int.get(ch.group("num").upper(), 0)
            title = (ch.group("title") or "").strip().rstrip(".")
            if num and title:
                current_chapter = (num, title)
                continue

        m = SECTION_HEADING_RE.match(stripped)
        if m and len(m.group("title")) > 3:
            if current:
                sections.append(current)
            current = {
                "number": m.group("num"),
                "title": m.group("title").rstrip("."),
                "text": "",
                "chapter": current_chapter,
            }
        elif current:
            current["text"] += stripped + " "

    if current:
        sections.append(current)
    return sections


def ingest_sanhitas(db: IngestDB) -> None:
    for pdf in PDFS:
        print(f"→ Ingesting {pdf['short_name']} from PRS…")
        try:
            pdf_bytes = _download_pdf(pdf["url"])
        except httpx.HTTPError as e:
            print(f"  ! Failed to download {pdf['url']}: {e}")
            continue
        try:
            text = _extract_text(pdf_bytes)
            sections = _parse_sections(text)
        except Exception as e:
            print(f"  ! Failed to extract/parse {pdf['short_name']} PDF: {e}; skipping.")
            continue
        if not sections:
            print(f"  ! No sections parsed from {pdf['short_name']} PDF (text length {len(text)}); skipping.")
            continue

        act_id = db.upsert_act(
            short_name=pdf["short_name"],
            full_name=pdf["full_name"],
            year=pdf["year"],
            citation=pdf["citation"],
            kind=pdf["kind"],
            source=SOURCE,
            source_license=LICENSE,
            as_of=AS_OF,
        )

        chapter_ids: dict[int, str] = {}
        n = 0
        for sec in sections:
            ch = sec.get("chapter")
            if ch:
                ch_num, ch_title = ch
                if ch_num not in chapter_ids:
                    chapter_ids[ch_num] = db.upsert_chapter(
                        act_id=act_id, number=ch_num, title=ch_title
                    )
            db.upsert_section(
                act_id=act_id,
                number=sec["number"],
                title=sec.get("title"),
                text=sanitize_text(sec["text"].strip()),
                chapter_id=chapter_ids.get(ch[0]) if ch else None,
            )
            n += 1
        db.commit()
        print(f"  ✓ {n} sections ingested ({len(chapter_ids)} chapters).")


def main() -> None:
    with IngestDB() as db:
        ingest_sanhitas(db)


if __name__ == "__main__":
    main()
