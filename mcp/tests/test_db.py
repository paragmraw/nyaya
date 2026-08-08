"""Unit tests for ``nyaya.db`` normalization and query-building helpers.

These cover the pure functions in ``nyaya/db.py`` that do not require a live
Postgres connection: ``normalize_act``, ``normalize_ref``, ``_escape_like``,
and the ``_ACT_ALIASES`` table. The DB query functions are exercised via the
``fake_db`` fixture in ``conftest.py`` (see ``test_tools.py``).
"""

from __future__ import annotations

import pytest

from nyaya import db

# ---------------------------------------------------------------------------
# normalize_act
# ---------------------------------------------------------------------------

def test_normalize_act_none():
    """None input returns None (no alias lookup, no exception)."""
    assert db.normalize_act(None) is None


def test_normalize_act_empty_string():
    """Empty string returns None."""
    assert db.normalize_act("") is None


def test_normalize_act_whitespace_only():
    """Whitespace-only input returns None after stripping."""
    assert db.normalize_act("   ") is None
    assert db.normalize_act("\t\n") is None


def test_normalize_act_alias_ipc():
    """The canonical 'IPC' alias resolves from multiple spellings."""
    assert db.normalize_act("ipc") == "IPC"
    assert db.normalize_act("IPC") == "IPC"
    assert db.normalize_act("Ipc") == "IPC"


def test_normalize_act_alias_full_name():
    """Full act names map to their canonical short_name."""
    assert db.normalize_act("indian penal code") == "IPC"
    assert db.normalize_act("Indian Penal Code") == "IPC"
    assert db.normalize_act("penal code") == "IPC"


def test_normalize_act_case_insensitive():
    """Alias lookup is case-insensitive across mixed-case input."""
    assert db.normalize_act("CrPc") == "CrPC"
    assert db.normalize_act("CRPC") == "CrPC"
    assert db.normalize_act("Code of Criminal Procedure") == "CrPC"


def test_normalize_act_strips_whitespace():
    """Leading/trailing whitespace is stripped before alias lookup."""
    assert db.normalize_act("  ipc  ") == "IPC"
    assert db.normalize_act("\tipc\n") == "IPC"


def test_normalize_act_strips_bidi_chars():
    """Unicode bidi/format characters are stripped so lookups cannot be spoofed."""
    # U+200B ZERO WIDTH SPACE, U+202E RIGHT-TO-LEFT OVERRIDE.
    assert db.normalize_act("\u200bipc\u200b") == "IPC"
    assert db.normalize_act("\u202eIPC") == "IPC"


def test_normalize_act_unknown_passthrough():
    """Unknown act names return stripped input with original case preserved."""
    assert db.normalize_act("CustomAct") == "CustomAct"
    assert db.normalize_act("  CustomAct  ") == "CustomAct"


def test_normalize_act_all_aliases_resolve():
    """Every entry in _ACT_ALIASES maps to a non-empty canonical name."""
    for key, canonical in db._ACT_ALIASES.items():
        # Lowercase input must resolve to the canonical name.
        assert db.normalize_act(key) == canonical, (
            f"alias {key!r} should map to {canonical!r}"
        )
        # Case-insensitive: uppercase also resolves.
        assert db.normalize_act(key.upper()) == canonical, (
            f"alias {key.upper()!r} should map to {canonical!r}"
        )
        # Whitespace-padded also resolves.
        assert db.normalize_act(f"  {key}  ") == canonical


@pytest.mark.parametrize(
    "alias,canonical",
    [
        ("ipc", "IPC"),
        ("crpc", "CrPC"),
        ("cpc", "CPC"),
        ("iea", "EvidenceAct"),
        ("evidenceact", "EvidenceAct"),
        ("bns", "BNS"),
        ("bnss", "BNSS"),
        ("bsa", "BSA"),
        ("companies", "Companies"),
        ("igst", "IGST"),
        ("cgst", "CGST"),
        ("gst", "CGST"),
        ("itact", "ITAct"),
        ("arbitration", "Arbitration"),
        ("consumerprotection", "ConsumerProtection"),
        ("constitution", "Constitution"),
        ("judgment", "judgment"),
        ("cases", "judgment"),
    ],
)
def test_normalize_act_alias_matrix(alias: str, canonical: str):
    """Spot-check each major alias maps to its canonical short_name."""
    assert db.normalize_act(alias) == canonical


def test_act_aliases_table_not_empty():
    """The alias table must be populated (regression guard)."""
    assert len(db._ACT_ALIASES) > 20, "Expected a reasonably populated alias map"


# ---------------------------------------------------------------------------
# normalize_ref
# ---------------------------------------------------------------------------

def test_normalize_ref_none():
    """None returns None."""
    assert db.normalize_ref(None) is None


def test_normalize_ref_empty():
    """Empty string returns None."""
    assert db.normalize_ref("") is None


def test_normalize_ref_whitespace_only():
    """Whitespace-only returns None."""
    assert db.normalize_ref("   ") is None


def test_normalize_ref_strips_s_prefix():
    """'s.' prefix is removed."""
    assert db.normalize_ref("s. 302") == "302"
    assert db.normalize_ref("s.302") == "302"
    assert db.normalize_ref("s302") == "302"


def test_normalize_ref_strips_section_prefix():
    """'section' prefix (with and without dot/space) is removed."""
    assert db.normalize_ref("section 302") == "302"
    assert db.normalize_ref("section302") == "302"
    assert db.normalize_ref("Section 302") == "302"
    assert db.normalize_ref("sec. 302") == "302"
    assert db.normalize_ref("sec 302") == "302"


def test_normalize_ref_strips_art_prefix():
    """'art.'/'article' prefix is removed."""
    assert db.normalize_ref("art. 21") == "21"
    assert db.normalize_ref("art21") == "21"
    assert db.normalize_ref("article 21") == "21"
    assert db.normalize_ref("Article 21") == "21"
    assert db.normalize_ref("article21") == "21"


def test_normalize_ref_preserves_trailing_whitespace_strip():
    """Trailing whitespace after the prefix is stripped."""
    assert db.normalize_ref("s. 302   ") == "302"


def test_normalize_ref_preserves_suffix_alpha():
    """Section numbers with letter suffixes are preserved (e.g. '354A')."""
    assert db.normalize_ref("s. 354A") == "354A"
    assert db.normalize_ref("354A") == "354A"


def test_normalize_ref_no_prefix():
    """A bare number passes through unchanged (after stripping whitespace)."""
    assert db.normalize_ref("302") == "302"
    assert db.normalize_ref("  302  ") == "302"


def test_normalize_ref_empty_after_prefix_strip():
    """If only the prefix is present, the result is None (not empty string)."""
    # 's.' alone -> after stripping -> '' -> None.
    assert db.normalize_ref("s.") is None
    assert db.normalize_ref("section ") is None


# ---------------------------------------------------------------------------
# _escape_like
# ---------------------------------------------------------------------------

def test_escape_like_percent():
    """Percent signs are escaped for literal LIKE matching."""
    assert "\\%" in db._escape_like("50%")
    assert db._escape_like("50%") == "50\\%"


def test_escape_like_underscore():
    """Underscores are escaped for literal LIKE matching."""
    assert "\\_" in db._escape_like("a_b")
    assert db._escape_like("a_b") == "a\\_b"


def test_escape_like_backslash():
    """Backslashes are escaped first so they don't pre-escape the added escapes."""
    assert db._escape_like("a\\b") == "a\\\\b"


def test_escape_like_combined():
    """All special characters in one string are escaped together."""
    result = db._escape_like("a\\b%c_d")
    assert result == "a\\\\b\\%c\\_d"


def test_escape_like_no_special():
    """A plain string with no special chars is returned unchanged."""
    assert db._escape_like("Supreme Court") == "Supreme Court"


def test_escape_like_empty():
    """Empty string passes through."""
    assert db._escape_like("") == ""
