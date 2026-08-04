"""Corpus sanitization helpers for the ingestion pipeline.

Strips control characters and enforces length caps on legal text before it
enters the database — the first line of defense against indirect prompt
injection (OWASP LLM01) via the legal corpus.

Control characters (U+0000-U+001F except \\n\\r\\t, plus U+007F-U+009F)
can be used to hide injection payloads from human review (RTL overrides
visually reorder text; zero-width characters obfuscate keywords).
"""

from __future__ import annotations

import re

# Maximum text length per row (200 KB).
MAX_TEXT_BYTES = 200_000

# Strips C0 controls except \n (0x0A), \r (0x0D), \t (0x09); DEL; and the C1
# range (U+0080-U+009F).
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")

# Unicode bidi/format characters that can visually reorder text.
_BIDI_RE = re.compile(r"[\u200B-\u200F\u202A-\u202E\u2066-\u2069]")


def strip_control_chars(s: str) -> str:
    """Remove control and bidi/format characters from text.

    Preserves newlines (\\n), carriage returns (\\r), and tabs (\\t) which
    are legitimate in legal text. Removes all other C0 control characters
    (U+0000-U+001F), DEL (U+007F), C1 control characters (U+0080-U+009F),
    and Unicode bidi/format characters that could be used to spoof text.
    """
    return _BIDI_RE.sub("", _CONTROL_RE.sub("", s))


def cap_length(s: str, max_bytes: int = MAX_TEXT_BYTES) -> str:
    """Validate that ``s`` fits within ``max_bytes`` when UTF-8 encoded.

    Raises ``ValueError`` rather than silently truncating, so the ingest
    pipeline can log and skip the offending row.
    """
    encoded = s.encode("utf-8")
    if len(encoded) > max_bytes:
        raise ValueError(
            f"Text exceeds maximum allowed length ({len(encoded)} bytes > {max_bytes} bytes)."
        )
    return s


def sanitize_text(s: str | None, max_bytes: int = MAX_TEXT_BYTES) -> str:
    """Strip control characters, then enforce the length cap.

    Returns ``""`` if input is None. Raises ``ValueError`` if the cleaned
    text exceeds ``max_bytes``.
    """
    if s is None:
        return ""
    cleaned = strip_control_chars(s)
    return cap_length(cleaned, max_bytes)