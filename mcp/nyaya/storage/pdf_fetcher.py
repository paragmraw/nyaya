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

_SCR_HEADER_LINE_1_RE = re.compile(
    r"(?im)^\s*(?:\d+\s+)?SUPREME\s+COURT\s+REPORTS\s*\[\d{4}\][^\n]*$"
)
_SCR_HEADER_LINE_2_RE = re.compile(
    r"(?im)^\s*\[\d{4}\]\s+\d+\s+S\.?C\.?R\.?[^\n]*$"
)
_SCR_HEADER_INLINE_RE = re.compile(
    r"(?i)\d+\s+SUPREME\s+COURT\s+REPORTS\s+\[\d{4}\]\s+\d+\s+S\.?C\.?R\.?\s*"
)
_SCR_HEADING_GUARD = (
    r"(?![ \t]*(?:JUDGMENT|ORDER|HELD|FACTS|ISSUES?|CONCLUSION|BACKGROUND|SUBMISSIONS?|HEADNOTE|INDEX|APPEARANCES?)[ \t]*$)"
)
_SCR_ALLCAPS_LINE = (
    rf"{_SCR_HEADING_GUARD}[ \t]*[A-Z](?:[A-Z0-9 ,&():./'-]|(?<=\s)v\.(?=\s)){{11,}}"
)
_SCR_PAGE_ANCHOR = (
    r"(?:(?:\d+[ \t]+)?SUPREME[ \t]+COURT[ \t]+REPORTS[ \t]*\[\d{4}\][^\n]*"
    r"|\[\d{4}\][ \t]+\d+[ \t]+S\.?C\.?R\.?[^\n]*"
    r"|\d{1,4})"
)
_SCR_BOUNDARY_AFTER_RE = re.compile(
    rf"(?m)^[ \t]*{_SCR_PAGE_ANCHOR}[ \t]*(?:\n{_SCR_ALLCAPS_LINE}){{1,4}}[ \t]*$"
)
_SCR_BOUNDARY_BEFORE_RE = re.compile(
    rf"(?m)^(?:{_SCR_ALLCAPS_LINE}\n){{1,4}}[ \t]*{_SCR_PAGE_ANCHOR}[ \t]*$"
)
_SCR_JUDGE_FOOTER_RE = re.compile(
    r"(?m)^(?:(?![ \t]*(?:JUDGMENT|ORDER|HELD|FACTS|ISSUES?|CONCLUSION|BACKGROUND|SUBMISSIONS?|HEADNOTE|INDEX|APPEARANCES?)[ \t]*$)"
    r"[A-Z0-9 ,&():./'-]{8,}\n){0,4}"
    r"[ \t]*(?:IN[ \t]+RE:[^\n]*\n(?:[A-Z0-9 ,&():./'-]{5,}\n){0,3})?"
    r"(?:[A-Z0-9 ,&():./'-]{0,120})\[[ \t]*[A-Z][A-Z., \t&'-]+(?:,[ \t]*)?(?:CJI|J\.|JJ\.)[ \t]*\][ \t]*$"
)
_SCR_ORPHAN_INTERPLAY_RE = re.compile(r"(?m)^[ \t]*IN[ \t]+RE:[ \t]*INTERPLAY[^\n]*$")
_STANDALONE_PAGE_NUM_RE = re.compile(r"(?m)^[ \t]*\d{1,4}[ \t]*$")
_ISOLATED_PAGE_NUM_RE = re.compile(r"(?m)^\s*\[\d+\]\s*$")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")

_OFF_LIGATURE_RE = re.compile(
    r"\b(off)\s+(er(?:ed|ing|s)?|ice(?:s|r|rs)?|icial(?:s|ly)?|"
    r"en(?:ce|ces|d|ded|der|ders|ding|se|ses|sive)|set(?:s)?|shoot(?:s)?|spring)\b",
    re.IGNORECASE,
)
_GENERAL_LIGATURE_RE = re.compile(r"\b([A-Za-z]*)((?i:ffi|ffl|fi|fl|ff))\s+([a-z]{2,})\b")
_LIGATURE_LEFT_EXCLUSIONS: frozenset[str] = frozenset(
    {
        "if",
        "of",
        "off",
        "staff",
        "tariff",
        "plaintiff",
        "sheriff",
        "bailiff",
        "mastiff",
        "pontiff",
        "chief",
        "brief",
        "grief",
        "relief",
        "belief",
        "proof",
        "roof",
        "self",
        "half",
        "wolf",
        "gulf",
        "shelf",
        "calf",
        "stiff",
        "cliff",
        "stuff",
        "bluff",
        "scoff",
        "puff",
        "cuff",
        "muff",
        "buff",
        "ruff",
        "sniff",
        "whiff",
        "skiff",
        "quaff",
        "chaff",
        "graff",
        "spiff",
        "cutoff",
        "layoff",
        "payoff",
        "runoff",
        "setoff",
        "standoff",
        "tradeoff",
        "takeoff",
        "kickoff",
        "liftoff",
    }
)
_LIGATURE_RIGHT_STOPSET: frozenset[str] = frozenset(
    {
        "in",
        "on",
        "of",
        "to",
        "is",
        "it",
        "as",
        "at",
        "by",
        "or",
        "an",
        "be",
        "we",
        "he",
        "no",
        "so",
        "up",
        "do",
        "if",
        "the",
        "and",
        "for",
        "are",
        "but",
        "not",
        "you",
        "all",
        "can",
        "had",
        "her",
        "was",
        "one",
        "our",
        "out",
        "has",
        "his",
        "how",
        "its",
        "may",
        "new",
        "now",
        "old",
        "see",
        "two",
        "way",
        "who",
        "did",
        "let",
        "put",
        "say",
        "she",
        "too",
        "use",
        "that",
        "with",
        "have",
        "this",
        "will",
        "your",
        "from",
        "they",
        "been",
        "were",
        "said",
        "each",
        "which",
        "their",
        "would",
        "there",
        "could",
        "other",
        "into",
        "more",
        "some",
        "such",
        "than",
        "then",
        "them",
        "these",
        "when",
        "where",
        "also",
        "only",
        "under",
        "upon",
        "shall",
        "must",
        "does",
        "done",
        "made",
        "make",
        "any",
        "per",
        "via",
        "non",
        "sub",
        "act",
        "law",
        "case",
        "rule",
        "court",
        "order",
    }
)
_YEAR_WORD_JOIN_RE = re.compile(r"(?<![\w/.-])((?:19|20)\d{2})(?!s\b)([a-z]{2,})\b")
_PAREN_WORD_JOIN_RE = re.compile(
    r"(\))((?:was|is|are|were|has|have|had|held|observed|stated|noted|in|of|to|the|and|that|which|for|on|at|by|as|be|it|this)\b)"
)
_CASE_V_JOIN_RE = re.compile(r"\b(?!(?:Pro|Uni|Con|Indi|Ele)v\.)([A-Z][a-z]{2,})(v\.)(?=\s)")

# Case-preserving PDF ligature space-dropout repairs (fi/fl/ff/ffi/ffl).
_LIGATURE_REPAIRS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(rf"\b({left})\s+({right})\b", re.IGNORECASE)
    for left, right in (
        (r"(?:in)?suffi", r"cient(?:ly|cy)?|ce|ces"),
        (r"(?:in)?suffici", r"ent(?:ly|cy)?"),
        (r"(?:in)?su", r"ffi(?:cient(?:ly|cy)?|ce|ces)"),
        (r"diff", r"eren(?:ce|ces|t|tly|tiate|tiated|tial)"),
        (r"diffi", r"cult(?:y|ies)?"),
        (r"(?:in|un)?eff", r"ect(?:ive|ively|iveness|s|ed|ing|uate|uated)?"),
        (r"effi", r"cien(?:t|tly|cy)|cac(?:y|ious)"),
        (r"(?:re)?refl", r"ect(?:s|ed|ing|ion|ions|ive)?"),
        (r"(?:re)?affi", r"rm(?:s|ed|ing|ation|ative|atively)?|davit(?:s)?"),
        (r"(?:re)?af", r"firm(?:s|ed|ing|ation|ative|atively)?"),
        (r"(?:un)?offi", r"c(?:e|es|er|ers|ial|ials|ially|iate|iating)"),
        (r"(?:un)?speci", r"fi(?:c|cally|city|ed|es|cation|cations)"),
        (r"(?:un)?specifi", r"c(?:ally|ity)?|ed|es|cation(?:s)?"),
        (r"(?:un)?confi", r"rm(?:s|ed|ing|ation|atory)?|d(?:e|ed|ence|ent|ential|entiality)"),
        (r"(?:in)?signi", r"fi(?:cant|cantly|cance)"),
        (r"(?:in)?signifi", r"can(?:t|tly|ce)"),
        (r"noti", r"fi(?:ed|es|cation|cations|able)"),
        (r"notifi", r"ed|es|cation(?:s)?|able"),
        (r"(?:de|re|un)?classi", r"fi(?:ed|es|cation|cations|able)"),
        (r"(?:de|re|un)?classifi", r"ed|es|cation(?:s)?|able"),
        (r"(?:dis|un)?quali", r"fi(?:ed|es|cation|cations|ying)"),
        (r"(?:dis|un)?qualifi", r"ed|es|cation(?:s)?|ying"),
        (r"(?:un)?identi", r"fi(?:ed|es|cation|able)"),
        (r"(?:un)?identifi", r"ed|es|cation|able"),
        (r"(?:un)?fulfi", r"l(?:l|led|ling|ment|lment|s)?"),
        (r"off", r"en(?:ce|ces|d|ded|der|ders|ding|se|ses|sive)"),
        (r"(?:non|un)?profi", r"t(?:s|ed|ing|able|ability)?"),
        (r"(?:in|re|un)?defi", r"n(?:e|ed|es|ing|ite|itely|ition|itions|itive|itively)"),
        (r"certi", r"fi(?:ed|es|cate|cates|cation|cations)"),
        (r"certifi", r"ed|es|cate(?:s)?|cation(?:s)?"),
        (r"(?:un)?veri", r"fi(?:ed|es|cation|able)"),
        (r"(?:un)?verifi", r"ed|es|cation|able"),
        (r"(?:un)?modi", r"fi(?:ed|es|cation|cations|able)"),
        (r"(?:un)?modifi", r"ed|es|cation(?:s)?|able"),
        (r"fi", r"nd(?:s|ing|ings)?|l(?:e|ed|es|ing|ings)|nal(?:ly|ity|ize|ized|ise|ised)?|rst|ve|x(?:ed|es|ing)?"),
        (r"confl", r"ict(?:s|ed|ing)?"),
        (r"benefi", r"t(?:s|ted|ting)?|cial(?:ly)?|ciar(?:y|ies)"),
        (r"plainti", r"ff(?:s)?"),
        (r"(?:un)?justi", r"fi(?:ed|es|cation|able)"),
        (r"(?:un)?justifi", r"ed|es|cation|able"),
        (r"(?:dis)?satis", r"fi(?:ed|es)"),
        (r"(?:dis)?satisfi", r"ed|es"),
        (r"clari", r"fi(?:ed|es|cation)"),
        (r"clarifi", r"ed|es|cation"),
        (r"rati", r"fi(?:ed|es|cation)"),
        (r"ratifi", r"ed|es|cation"),
        (r"recti", r"fi(?:ed|es|cation)"),
        (r"rectifi", r"ed|es|cation"),
        (r"nulli", r"fi(?:ed|es|cation)"),
        (r"nullifi", r"ed|es|cation"),
        (r"testi", r"fi(?:ed|es)"),
        (r"testifi", r"ed|es"),
    )
)

_MAX_PARAGRAPH_CHARS = 1800
_CHUNK_TARGET_CHARS = 1400
_HEADING_SPLIT_RE = re.compile(
    r"(?m)(?=^\s*(?:JUDGMENT|ORDER|O\s*R\s*D\s*E\s*R|J\s*U\s*D\s*G\s*M\s*E\s*N\s*T|"
    r"HELD|FACTS|ISSUES?|CONCLUSION|BACKGROUND|SUBMISSIONS?|HEADNOTE|INDEX|APPEARANCES?)\b)"
)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")
_NUMBERED_PARA_START_RE = re.compile(r"^\s*(?:\d{1,4}\.|\[\d{1,4}\]|\(\d{1,4}\))\s+\S")
_HEADER_NOISE_CUE_RE = re.compile(
    r"(?i)\b(?:supreme\s+court\s+of\s+india|high\s+court\s+of|civil\s+appellate\s+jurisdiction|"
    r"criminal\s+appellate\s+jurisdiction|original\s+jurisdiction|reportable|non-?reportable|"
    r"for\s+the\s+appellant|for\s+the\s+respondent|for\s+the\s+petitioner|appearances?|"
    r"counsel\s+for|advocate|coram\s*:|bench\s*:|case\s+no\.|civil\s+appeal\s+no\.|"
    r"criminal\s+appeal\s+no\.|writ\s+petition|special\s+leave\s+petition|versus|appellant\(?s\)?|"
    r"respondent\(?s\)?|date\s+of\s+judgment|index|headnote)\b"
)


def _merge_ligature_match(m: re.Match[str]) -> str:
    g1, g2, g3 = m.group(1), m.group(2), m.group(3)
    if (g1 + g2).lower() in _LIGATURE_LEFT_EXCLUSIONS or g3.lower() in _LIGATURE_RIGHT_STOPSET:
        return m.group(0)
    return f"{g1}{g2}{g3}"


def _repair_ligature_splits(text: str) -> str:
    """Rejoin PDF ligature space-dropouts without merging distinct real words."""
    out = text
    for pat in _LIGATURE_REPAIRS:
        out = pat.sub(r"\1\2", out)
    out = _OFF_LIGATURE_RE.sub(r"\1\2", out)
    out = _GENERAL_LIGATURE_RE.sub(_merge_ligature_match, out)
    return out


def _repair_pdf_word_joins(text: str) -> str:
    """Insert missing spaces on minor PDF word-joins (years, closing parens, case 'v.')."""
    out = _YEAR_WORD_JOIN_RE.sub(r"\1 \2", text)
    out = _PAREN_WORD_JOIN_RE.sub(r"\1 \2", out)
    out = _CASE_V_JOIN_RE.sub(r"\1 \2", out)
    return out


def _clean_pdf_text(text: str) -> str:
    """Normalize line endings, strip SCR running headers/footers, and repair ligature/word splits."""
    if not text:
        return ""
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")
    cleaned = _SCR_JUDGE_FOOTER_RE.sub("", cleaned)
    cleaned = _SCR_BOUNDARY_AFTER_RE.sub("", cleaned)
    cleaned = _SCR_BOUNDARY_BEFORE_RE.sub("", cleaned)
    cleaned = _SCR_ORPHAN_INTERPLAY_RE.sub("", cleaned)
    cleaned = _SCR_HEADER_LINE_1_RE.sub("", cleaned)
    cleaned = _SCR_HEADER_LINE_2_RE.sub("", cleaned)
    cleaned = _SCR_HEADER_INLINE_RE.sub("", cleaned)
    cleaned = _STANDALONE_PAGE_NUM_RE.sub("", cleaned)
    cleaned = _ISOLATED_PAGE_NUM_RE.sub("", cleaned)
    cleaned = _repair_ligature_splits(cleaned)
    cleaned = _repair_pdf_word_joins(cleaned)
    cleaned = _MULTI_NEWLINE_RE.sub("\n\n", cleaned)
    return cleaned.strip()


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


def _split_oversized_block(text: str, *, max_len: int = _CHUNK_TARGET_CHARS) -> list[str]:
    """Split a single block > _MAX_PARAGRAPH_CHARS into chunks <= max_len along headings/sentences."""
    pieces: list[str] = []
    for sub in re.split(r"\n\s*\n", text):
        sub = sub.strip()
        if not sub:
            continue
        if len(sub) <= max_len:
            pieces.append(sub)
            continue
        # Split on section headings or line breaks first
        heading_parts = [h.strip() for h in _HEADING_SPLIT_RE.split(sub) if h.strip()]
        if len(heading_parts) <= 1:
            heading_parts = [ln.strip() for ln in sub.split("\n") if ln.strip()]
        for hp in heading_parts:
            if len(hp) <= max_len:
                pieces.append(hp)
                continue
            # Split on sentence boundaries
            for sent in _SENTENCE_SPLIT_RE.split(hp):
                sent = sent.strip()
                if not sent:
                    continue
                if len(sent) <= max_len:
                    pieces.append(sent)
                else:
                    # Hard word-boundary wrap as last resort
                    words = sent.split()
                    cur = ""
                    for w in words:
                        if not cur:
                            if len(w) <= max_len:
                                cur = w
                            else:
                                for i in range(0, len(w), max_len):
                                    pieces.append(w[i : i + max_len])
                        elif len(cur) + 1 + len(w) <= max_len:
                            cur = f"{cur} {w}"
                        else:
                            pieces.append(cur)
                            cur = w if len(w) <= max_len else ""
                            if not cur:
                                for i in range(0, len(w), max_len):
                                    pieces.append(w[i : i + max_len])
                    if cur:
                        pieces.append(cur)

    # Greedily pack smaller pieces into chunks <= max_len
    packed: list[str] = []
    buf = ""
    for piece in pieces:
        if not buf:
            buf = piece
        elif len(buf) + 2 + len(piece) <= max_len:
            buf = f"{buf}\n\n{piece}"
        else:
            packed.append(buf)
            buf = piece
    if buf:
        packed.append(buf)
    return packed


def _split_paragraphs(pages: list[str]) -> list[str]:
    cleaned_pages = [_clean_pdf_text(p) for p in pages]
    joined = "\n\n".join(p for p in cleaned_pages if p)
    if not joined:
        return []
    parts = [p.strip() for p in _PARA_SPLIT_RE.split(joined) if p.strip()]
    if len(parts) <= 1:
        parts = [p.strip() for p in re.split(r"\n\s*\n", joined) if p.strip()]
    out: list[str] = []
    for part in parts:
        if len(part) > _MAX_PARAGRAPH_CHARS:
            out.extend(_split_oversized_block(part, max_len=_CHUNK_TARGET_CHARS))
        else:
            out.append(part)
    return out


def _is_header_noise(para: str) -> bool:
    """Return True if a leading paragraph looks like pure index/counsel-appearance header noise."""
    if _NUMBERED_PARA_START_RE.match(para):
        return False
    # Never drop if it looks like substantive judgment prose
    if re.search(
        r"(?i)\b(?:leave\s+granted|this\s+appeal|heard\s+learned\s+counsel|"
        r"the\s+question\s+(?:that\s+)?arises|we\s+have\s+heard)\b",
        para,
    ):
        return False
    return bool(_HEADER_NOISE_CUE_RE.search(para)) or len(para) <= 180


def _overview_start_index(paragraphs: list[str]) -> int:
    """Find opening substantive paragraph index, skipping leading header noise only if numbered paras follow."""
    if not paragraphs:
        return 0
    if _NUMBERED_PARA_START_RE.match(paragraphs[0]):
        return 0
    first_numbered: int | None = None
    for idx, p in enumerate(paragraphs):
        if _NUMBERED_PARA_START_RE.match(p) and len(p) >= 25:
            first_numbered = idx
            break
    if first_numbered is None or first_numbered == 0:
        return 0
    if all(_is_header_noise(paragraphs[i]) for i in range(first_numbered)):
        return first_numbered
    return 0


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
            cleaned_pages = pages_text
        else:
            quality = "truncated" if truncated_pages else "verified"
            paragraphs = _split_paragraphs(pages_text)
            cleaned_pages = [_clean_pdf_text(p) for p in pages_text]

        return {
            "total_pages": total_pages,
            "pages": cleaned_pages,
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
            start_idx = _overview_start_index(paragraphs)
            head_end = min(start_idx + 8, total_paras)
            head = [(i + 1, paragraphs[i]) for i in range(start_idx, head_end)]
            tail_start = max(head_end, total_paras - 8)
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
