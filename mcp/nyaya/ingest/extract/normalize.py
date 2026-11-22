"""Unicode NFC normalizer, OWASP LLM01 sanitizer, commercial headnote stripper,
and publication-ban checker for ingested legal records.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from ...sanitize import (
    PublicationBanResult,
    check_publication_ban,
    detect_suspected_injection,
    escape_corpus_delimiters,
    strip_control_chars,
)

# Commercial reporter headnote markers prohibited under EBC v. D.B. Modak (2008) 1 SCC 1
_COMMERCIAL_HEADNOTE_LINE_RE = re.compile(
    r"^[^\n]*(?:SCC\s+Online|Manupatra|AIR\s+Headnote)[^\n]*$",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass
class NormalizedRecord:
    """Sanitized and normalized legal text record ready for storage indexing."""

    doc_id: str
    cleaned_text: str
    text_sha256: str
    blocked_by_publication_ban: bool
    publication_ban: PublicationBanResult
    suspected_injection_rules: list[str] = field(default_factory=list)


def strip_commercial_headnotes(text: str) -> str:
    """Remove proprietary reporter headnote lines (*EBC v. D.B. Modak* compliance)."""
    return _COMMERCIAL_HEADNOTE_LINE_RE.sub("", text)


def normalize_legal_record(
    text: str,
    *,
    doc_id: str,
    metadata: Mapping[str, Any] | None = None,
) -> NormalizedRecord:
    """Normalize text to Unicode NFC, strip control/Tag/BiDi chars, strip commercial
    headnotes, escape `<corpus_text>` breakout delimiters, and evaluate publication bans.
    """
    nfc = unicodedata.normalize("NFC", text or "")
    inj_rules = detect_suspected_injection(nfc)
    ban_res = check_publication_ban(nfc, metadata=metadata)

    cleaned = strip_control_chars(nfc)
    cleaned = strip_commercial_headnotes(cleaned)
    cleaned = escape_corpus_delimiters(cleaned)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n").strip()
    sha = hashlib.sha256(cleaned.encode("utf-8")).hexdigest()

    return NormalizedRecord(
        doc_id=doc_id,
        cleaned_text=cleaned,
        text_sha256=sha,
        blocked_by_publication_ban=ban_res.blocked,
        publication_ban=ban_res,
        suspected_injection_rules=inj_rules,
    )
