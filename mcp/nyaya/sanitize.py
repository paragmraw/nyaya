"""Corpus sanitization, OWASP LLM01 isolation, and publication-ban helpers."""

from __future__ import annotations

import html
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# Legacy 200 KB cap preserved for backwards compatibility; v2 chunker uses 16 KB.
MAX_TEXT_BYTES = 200_000
MAX_CHUNK_BYTES = 16_384

# C0 controls except \n (0x0A), \r (0x0D), \t (0x09); DEL; and C1 controls (U+0080-U+009F).
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

# Unicode bidi/format characters that visually reorder or obfuscate text.
BIDI_RE = re.compile(r"[\u200B-\u200F\u202A-\u202E\u2066-\u2069]")

# Unicode Tag block (U+E0000-U+E007F) used for invisible ASCII smuggling.
UNICODE_TAG_RE = re.compile(r"[\U000E0000-\U000E007F]")

# Mid-text BOM, soft hyphens, and variation selectors.
_INVISIBLE_FORMAT_RE = re.compile(r"[\ufeff\u00ad\ufe00-\ufe0f]")

# Control/fence tags that could allow breakout from <corpus_text> wrappers.
_DELIMITER_TAG_RE = re.compile(
    r"<(/?(?:corpus_text|system|instructions?|\|im_(?:start|end)\||\|(?:system|assistant|user)\|)(?:\b[^>]*)?>?)",
    re.IGNORECASE,
)

_INJECTION_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("unicode_tag_smuggling", UNICODE_TAG_RE),
    ("delimiter_breakout_attempt", _DELIMITER_TAG_RE),
    (
        "ignore_previous_instructions",
        re.compile(
            r"\b(?:ignore|disregard|forget|override)\s+(?:all\s+)?"
            r"(?:previous|prior|above|system)\s+(?:instructions?|prompts?|rules?)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "system_prompt_override",
        re.compile(
            r"\b(?:new\s+system\s+prompt|you\s+are\s+now\s+in\s+developer\s+mode|"
            r"reveal\s+(?:your\s+)?system\s+prompt)\b",
            re.IGNORECASE,
        ),
    ),
)

_SEALED_COVER_RE = re.compile(
    r"\b(?:ordered|directed|kept|placed|submitted|filed|retained)\s+"
    r"(?:to\s+be\s+kept\s+)?in\s+(?:a\s+)?sealed\s+cover\b"
    r"|\bsealed\s+cover\b[^.]{0,60}\bnot\s+to\s+be\s+(?:opened|uploaded|published|disclosed)\b",
    re.IGNORECASE,
)

_IN_CAMERA_BAN_RE = re.compile(
    r"\b(?:proceedings|trial|hearing|matter)\s+(?:held|conducted)\s+in\s+camera\b"
    r"[^.\n]{0,80}\b(?:not\s+to\s+be\s+(?:uploaded|published|disclosed|released)|prohibited|barred)\b"
    r"|\bstrictly\s+in[\s-]camera\b[^.\n]{0,80}\b(?:non[\s-]?publication|not\s+to\s+be\s+(?:uploaded|published))\b",
    re.IGNORECASE,
)

_VICTIM_IDENTITY_BAN_RE = re.compile(
    r"\b(?:publication|disclosure)\s+of\s+(?:the\s+)?(?:name|identity|particulars)\s+of\s+"
    r"(?:the\s+)?(?:victim|prosecutrix|child|juvenile|survivor)\b"
    r"[^.\n]{0,100}\b(?:prohibited|barred|banned|restrained|not\s+to\s+be\s+(?:disclosed|published|revealed))\b"
    r"|\b(?:name|identity)\s+of\s+(?:the\s+)?(?:victim|prosecutrix|child|juvenile|survivor)\b"
    r"[^.\n]{0,80}\b(?:is\s+(?:strictly\s+)?(?:prohibited|barred|banned|restrained)|"
    r"not\s+to\s+be\s+(?:disclosed|published|revealed)|ordered\s+(?:redacted|suppressed))\b"
    r"[^.\n]{0,80}\b(?:228A|72|74|23|33\(7\)|34)\b",
    re.IGNORECASE,
)

_BAN_STATUTE_HINT_RE = re.compile(
    r"\b(?:s(?:ec(?:tion)?)?\.?\s*)?(228A|72|74|23|33\(7\)|34)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PublicationBanResult:
    """Outcome of checking a record for statutory/judicial publication bans."""

    blocked: bool
    statute: str | None = None
    reason: str | None = None


def strip_control_chars(s: str) -> str:
    """Strip C0/C1 controls, DEL, BiDi overrides, Unicode Tags, BOM, and soft hyphens."""
    cleaned = _CONTROL_RE.sub("", s)
    cleaned = BIDI_RE.sub("", cleaned)
    cleaned = UNICODE_TAG_RE.sub("", cleaned)
    return _INVISIBLE_FORMAT_RE.sub("", cleaned)


def cap_length(s: str, max_bytes: int = MAX_TEXT_BYTES) -> str:
    """Validate UTF-8 byte length against ``max_bytes`` (raises ValueError if exceeded)."""
    encoded = s.encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError(
            f"Text exceeds maximum allowed length ({len(encoded)} bytes > {max_bytes} bytes)."
        )
    return s


def sanitize_text(s: str | None, max_bytes: int = MAX_TEXT_BYTES) -> str:
    """Strip unsafe invisible/control characters and enforce byte length cap."""
    if s is None:
        return ""
    cleaned = strip_control_chars(s)
    return cap_length(cleaned, max_bytes)


def escape_corpus_delimiters(s: str) -> str:
    """Neutralize fence/control tags inside corpus text without altering math/prose."""

    def _replace(match: re.Match[str]) -> str:
        inner = match.group(1)
        if inner.endswith(">"):
            return f"&lt;{inner[:-1]}&gt;"
        return f"&lt;{inner}"

    return _DELIMITER_TAG_RE.sub(_replace, s)


def detect_suspected_injection(s: str) -> list[str]:
    """Non-destructively return matched prompt-injection rule IDs for audit logging."""
    if not s:
        return []
    return [rule_id for rule_id, pattern in _INJECTION_RULES if pattern.search(s)]


def check_publication_ban(
    text: str,
    metadata: Mapping[str, Any] | None = None,
) -> PublicationBanResult:
    """Detect operative sealed/in-camera/statutory non-publication directives.

    Distinguishes operative suppression directives from mere doctrinal
    references to s. 228A IPC / s. 72 BNS / POCSO / JJ Act in ordinary precedent.
    """
    meta = metadata or {}
    for flag in (
        "sealed",
        "in_camera",
        "publication_banned",
        "publication_ban_flagged",
        "non_reportable_suppression",
    ):
        if bool(meta.get(flag)):
            return PublicationBanResult(
                blocked=True,
                statute=str(meta.get("ban_statute") or "s.52(1)(q)(iv)"),
                reason=f"metadata flag {flag}=True",
            )

    if _SEALED_COVER_RE.search(text):
        return PublicationBanResult(
            blocked=True,
            statute="s.52(1)(q)(iv)",
            reason="operative sealed-cover suppression directive",
        )
    if _IN_CAMERA_BAN_RE.search(text):
        return PublicationBanResult(
            blocked=True,
            statute="s.52(1)(q)(iv)",
            reason="operative in-camera non-publication directive",
        )
    m = _VICTIM_IDENTITY_BAN_RE.search(text)
    if m:
        statute_m = _BAN_STATUTE_HINT_RE.search(m.group(0))
        statute = f"s.{statute_m.group(1)}" if statute_m else "s.228A/s.72 BNS"
        return PublicationBanResult(
            blocked=True,
            statute=statute,
            reason="operative victim/juvenile identity non-disclosure order",
        )
    return PublicationBanResult(blocked=False)


def _escape_attr(value: str) -> str:
    cleaned = strip_control_chars(value).replace("\r", " ").replace("\n", " ")
    return html.escape(cleaned, quote=True)


def frame_corpus_text(
    text: str,
    *,
    doc_id: str,
    source: str = "",
    license_spdx: str = "",
    commercial_safe: bool = True,
    as_of: str = "",
    sha256: str = "",
    extraction_quality: str | None = "verified",
    suspected_injection: bool = False,
    source_url: str | None = None,
) -> str:
    """Wrap untrusted primary legal text inside an escaped OWASP LLM01 envelope."""
    is_suspected = suspected_injection or bool(detect_suspected_injection(text))
    cleaned_body = escape_corpus_delimiters(strip_control_chars(text))
    src_value = source or source_url or ""

    attrs = [
        f'doc_id="{_escape_attr(doc_id)}"',
        f'source="{_escape_attr(src_value)}"',
        f'license="{_escape_attr(license_spdx)}"',
        f'commercial_safe="{"true" if commercial_safe else "false"}"',
        f'as_of="{_escape_attr(as_of)}"',
        f'sha256="{_escape_attr(sha256)}"',
    ]
    if extraction_quality is not None:
        attrs.append(f'extraction_quality="{_escape_attr(extraction_quality)}"')
    if is_suspected:
        attrs.append('suspected_injection="true"')
    attrs.append('trust="untrusted_primary_law"')

    return f"<corpus_text {' '.join(attrs)}>\n{cleaned_body}\n</corpus_text>"
