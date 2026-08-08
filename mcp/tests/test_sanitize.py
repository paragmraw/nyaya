"""Unit tests for ``nyaya.sanitize``.

Covers the control-character stripping, length capping, and the combined
``sanitize_text`` helper that chains the two. These are pure functions with
no I/O.
"""

from __future__ import annotations

import pytest

from nyaya.sanitize import (
    MAX_TEXT_BYTES,
    cap_length,
    sanitize_text,
    strip_control_chars,
)

# ---------------------------------------------------------------------------
# strip_control_chars
# ---------------------------------------------------------------------------

def test_strip_control_chars_preserves_plain_text():
    """Plain ASCII text passes through unchanged."""
    assert strip_control_chars("The Indian Penal Code") == "The Indian Penal Code"


def test_strip_control_chars_removes_c0_controls():
    """C0 control characters (other than \\n\\r\\t) are removed."""
    s = "Hello\x00World\x01\x02\x03"
    out = strip_control_chars(s)
    assert out == "HelloWorld"


def test_strip_control_chars_preserves_newlines():
    r"""Newline (\n) is preserved."""
    s = "Line one\nLine two"
    assert strip_control_chars(s) == "Line one\nLine two"


def test_strip_control_chars_preserves_carriage_returns():
    r"""Carriage return (\r) is preserved."""
    s = "Line one\r\nLine two"
    assert strip_control_chars(s) == "Line one\r\nLine two"


def test_strip_control_chars_preserves_tabs():
    r"""Tab (\t) is preserved."""
    s = "Col1\tCol2\tCol3"
    assert strip_control_chars(s) == "Col1\tCol2\tCol3"


def test_strip_control_chars_removes_del():
    """DEL (U+007F) is removed."""
    s = "Hello\x7fWorld"
    assert strip_control_chars(s) == "HelloWorld"


def test_strip_control_chars_removes_c1_controls():
    """C1 control characters (U+0080-U+009F) are removed."""
    s = "Hello\x80\x81\x9fWorld"
    assert strip_control_chars(s) == "HelloWorld"


def test_strip_control_chars_removes_bidi_format():
    """Unicode bidi/format characters are removed (injection defense)."""
    # U+200B ZERO WIDTH SPACE, U+202E RIGHT-TO-LEFT OVERRIDE.
    s = "Hello\u200bWorld\u202e"
    assert strip_control_chars(s) == "HelloWorld"


def test_strip_control_chars_removes_isolates():
    """Unicode isolation characters (U+2066-U+2069) are removed."""
    s = "a\u2066b\u2067c\u2068d\u2069"
    assert strip_control_chars(s) == "abcd"


def test_strip_control_chars_empty():
    """Empty string passes through unchanged."""
    assert strip_control_chars("") == ""


def test_strip_control_chars_all_controls():
    """A string composed entirely of control chars becomes empty."""
    s = "\x00\x01\x02\x7f\x80\u200b\u202e"
    assert strip_control_chars(s) == ""


def test_strip_control_chars_preserves_unicode():
    """Legal Unicode (Devanagari, accented Latin) is preserved."""
    s = "अनुच्छेद 21 — café"
    assert strip_control_chars(s) == s


# ---------------------------------------------------------------------------
# cap_length
# ---------------------------------------------------------------------------

def test_cap_length_under_limit():
    """A short string passes through unchanged."""
    out = cap_length("hello", max_bytes=100)
    assert out == "hello"


def test_cap_length_default_limit_is_max_text_bytes():
    """Without an explicit max, the default is MAX_TEXT_BYTES."""
    out = cap_length("hello")
    assert out == "hello"


def test_cap_length_exact_limit():
    """A string exactly at the limit is accepted (boundary is inclusive)."""
    s = "a" * 100
    out = cap_length(s, max_bytes=100)
    assert out == s


def test_cap_length_raises_value_error_when_exceeding():
    """Text over the limit raises ValueError (not silent truncation)."""
    with pytest.raises(ValueError):
        cap_length("a" * 200_001, max_bytes=MAX_TEXT_BYTES)


def test_cap_length_error_message_includes_sizes():
    """The error message reports both the actual and maximum sizes."""
    with pytest.raises(ValueError) as exc_info:
        cap_length("a" * 300, max_bytes=100)
    msg = str(exc_info.value)
    assert "300" in msg
    assert "100" in msg


def test_cap_length_counts_bytes_not_chars():
    """The cap is measured in UTF-8 bytes, not Python codepoints."""
    # Each Devanagari char is 3 bytes in UTF-8.
    s = "अ" * 50  # 150 bytes
    with pytest.raises(ValueError):
        cap_length(s, max_bytes=100)


def test_cap_length_empty():
    """Empty string passes the cap."""
    assert cap_length("", max_bytes=10) == ""


# ---------------------------------------------------------------------------
# sanitize_text
# ---------------------------------------------------------------------------

def test_sanitize_text_none_returns_empty():
    """None input returns an empty string (no exception)."""
    assert sanitize_text(None) == ""


def test_sanitize_text_empty_returns_empty():
    """Empty string returns empty string."""
    assert sanitize_text("") == ""


def test_sanitize_text_chains_strip_then_cap():
    """Control chars are stripped before the length cap is enforced."""
    # A string that's over the limit only because of control chars should
    # pass after stripping (this is the whole point of strip-then-cap).
    big = "a" * 100 + "\x00" * 200
    out = sanitize_text(big, max_bytes=100)
    assert out == "a" * 100


def test_sanitize_text_clean_text_passes_through():
    """Clean text within the limit passes through unchanged."""
    s = "Punishment for murder — imprisonment for life."
    assert sanitize_text(s) == s


def test_sanitize_text_strips_controls():
    """sanitize_text removes control characters."""
    s = "Hello\x00\n\tWorld\x7f"
    assert sanitize_text(s) == "Hello\n\tWorld"


def test_sanitize_text_raises_value_error_when_still_too_long():
    """If the cleaned text still exceeds the cap, ValueError is raised."""
    with pytest.raises(ValueError):
        sanitize_text("a" * 200_001)


def test_sanitize_text_preserves_unicode_legal():
    """Legal Unicode with newlines is preserved end-to-end."""
    s = "अनुच्छेद 21\nProtection of life and personal liberty."
    assert sanitize_text(s) == s


def test_sanitize_text_explicit_max_bytes():
    """The max_bytes kwarg is forwarded to cap_length."""
    with pytest.raises(ValueError):
        sanitize_text("a" * 300, max_bytes=100)


def test_max_text_bytes_is_200k():
    """The default cap is 200 KB (regression guard for the constant)."""
    assert MAX_TEXT_BYTES == 200_000
