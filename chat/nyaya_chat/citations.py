"""Citation verification: parse and verify [[act: X, ref: Y]] markers via nyaya.citations.

Delegates canonical Indian legal citation extraction and grounding verification
to ``nyaya.citations`` (with an extra pass for Constitutional/statutory
amendments, regulator circulars/notifications, and dynamic act names present
in tool outputs):

- Fixes the v0.2 first-number bug (``AIR 1973 SC 9999`` never matches
  ``AIR 1973 SC 1461``; ``(1973) 4 SCC 999`` never matches ``(1973) 4 SCC 225``).
- Fixes the ``act=None`` judgment drop bug (grounds ``2017 INSC 801``,
  ``2024:DHC:1234``, ``2024:BHC-OS:567``, ``AIR 1973 SC 1461``,
  ``(2017) 10 SCC 1``, ``[1973] Supp SCR 1``, and 16-char CNRs like
  ``MHAU019999992015``).
- Supports sub-sections (``s. 65B(4)``), Constitution articles (``Art. 21A``,
  ``Art. 19(1)(a)``), Constitution schedules (``Schedule 7 List I Entry 82``),
  CPC Orders/Rules (``O. VII R. 11(d)``), regulations (``Reg. 30(1)``),
  circulars, notifications, and Constitutional Amendments (``42nd Amendment``).
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from nyaya.citations import (
    _DEFAULT_ALIAS_MAP,
    CITATION_MARKER_RE,
    ParsedCitation,
    _marker_candidate_keys,
    _merged_alias_map,
    _repair_whitespace_around_markers,
    _resolve_act_alias,
    canonicalize_court,
    parse_single_citation,
)
from nyaya.citations import (
    extract_grounded_keys as _nyaya_extract_grounded_keys,
)
from nyaya.citations import (
    parse_citations as parse_legal_citations,
)

log = logging.getLogger("nyaya_chat.citations")

# Single source of truth for inline marker syntax [[act: <short_name>, ref: <ref>]]
CITATION_RE = CITATION_MARKER_RE

_CIRCULAR_OR_NOTIF_RE = re.compile(
    r"^(?P<kind>cir(?:cular)?|notif(?:ication)?)\.?\s*(?:no\.?\s*)?(?P<num>[A-Za-z0-9_./()-]+)$",
    re.IGNORECASE,
)
_AMEND_NUM_RE = re.compile(
    r"^(?:(?P<ord>\d{1,3})(?:st|nd|rd|th)\s+(?:constitutional\s+|constitution\s+)?amendment(?:\s+act)?|amendment\s+(?P<num>\d{1,3}))$",
    re.IGNORECASE,
)

__all__ = [
    "CITATION_MARKER_RE",
    "CITATION_RE",
    "Citation",
    "ParsedCitation",
    "canonicalize_court",
    "extract_grounded_keys",
    "parse_citations",
    "parse_legal_citations",
    "parse_single_citation",
    "verify_citations",
    "verify_inline_markers",
]


@dataclass
class Citation:
    act: str
    ref: str


def parse_citations(text: str) -> list[Citation]:
    """Extract all [[act: X, ref: Y]] inline markers from the answer text."""
    citations: list[Citation] = []
    seen: set[str] = set()
    for m in CITATION_RE.finditer(text):
        act = m.group(1).strip()
        ref = m.group(2).strip()
        key = f"{act}|{ref}"
        if key not in seen:
            seen.add(key)
            citations.append(Citation(act=act, ref=ref))
    return citations


def _collect_dynamic_aliases(
    node: Any,
    aliases: dict[str, str],
) -> None:
    if isinstance(node, list):
        for item in node:
            _collect_dynamic_aliases(item, aliases)
        return
    if not isinstance(node, dict) or "error" in node:
        return
    for key in ("act", "short_name", "from_act", "to_act"):
        val = node.get(key)
        if isinstance(val, str) and val.strip():
            clean = val.strip()
            low = clean.lower()
            if low not in aliases:
                aliases[low] = clean
    for v in node.values():
        if isinstance(v, (dict, list)):
            _collect_dynamic_aliases(v, aliases)


def _index_extra_keys(
    node: Any,
    aliases: Mapping[str, str],
    out: set[str],
) -> None:
    """Index AmendmentRecord numbers and Circular/Notification references."""
    if isinstance(node, list):
        for item in node:
            _index_extra_keys(item, aliases, out)
        return
    if not isinstance(node, dict) or "error" in node:
        return

    # 1. AmendmentRecord: {"act": "Constitution", "number": 42, ...}
    num = node.get("number")
    if isinstance(num, int) and (
        "amendment_id" in node
        or "provisions_affected" in node
        or "enactment_date" in node
        or "year" in node
    ):
        raw_act = str(node.get("act") or "Constitution")
        canon_act = _resolve_act_alias(raw_act, aliases)
        out.add(f"amendment::{canon_act}::{num}")

    # 2. Circulars / Notifications: {"act": "RBI", "ref": "Circular 2024-25/12"}
    act = node.get("act")
    ref = node.get("ref")
    if isinstance(act, str) and act.strip() and isinstance(ref, str) and ref.strip():
        canon_act = _resolve_act_alias(act.strip(), aliases)
        m_cir = _CIRCULAR_OR_NOTIF_RE.match(ref.strip())
        if m_cir:
            k_prefix = "circular" if m_cir.group("kind").lower().startswith("cir") else "notification"
            out.add(f"{k_prefix}::{canon_act}::{m_cir.group('num').upper()}")

    for v in node.values():
        if isinstance(v, (dict, list)):
            _index_extra_keys(v, aliases, out)


def _extra_marker_keys(
    act: str,
    ref: str,
    aliases: Mapping[str, str],
) -> set[str]:
    canon_act = _resolve_act_alias(act.strip(), aliases)
    ref_clean = ref.strip()
    keys: set[str] = set()

    m_cir = _CIRCULAR_OR_NOTIF_RE.match(ref_clean)
    if m_cir:
        k_prefix = "circular" if m_cir.group("kind").lower().startswith("cir") else "notification"
        keys.add(f"{k_prefix}::{canon_act}::{m_cir.group('num').upper()}")

    m_am = _AMEND_NUM_RE.match(ref_clean)
    if m_am:
        num_str = m_am.group("ord") or m_am.group("num")
        if num_str:
            keys.add(f"amendment::{canon_act}::{int(num_str)}")

    return keys


def _build_aliases_for_payloads(
    tool_payloads: Sequence[str],
    alias_map: Mapping[str, str] | None = None,
) -> dict[str, str]:
    merged = _merged_alias_map(alias_map)
    for content in tool_payloads:
        if not content or not content.strip():
            continue
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            continue
        _collect_dynamic_aliases(data, merged)
    return merged


def extract_grounded_keys(
    tool_payloads: Sequence[str],
    *,
    alias_map: Mapping[str, str] | None = None,
) -> set[str]:
    """Extract canonical citation/provision/judgment keys from serialized tool outputs."""
    aliases = _build_aliases_for_payloads(tool_payloads, alias_map)
    keys = _nyaya_extract_grounded_keys(tool_payloads, alias_map=aliases)
    for content in tool_payloads:
        if not content or not content.strip():
            continue
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            continue
        _index_extra_keys(data, aliases, keys)
    return keys


def verify_inline_markers(
    answer_text: str,
    tool_payloads: Sequence[str],
    *,
    alias_map: Mapping[str, str] | None = None,
    had_tool_calls: bool = False,
) -> str:
    """Verify ``[[act: X, ref: Y]]`` inline markers strictly against tool payloads."""
    matches = list(CITATION_MARKER_RE.finditer(answer_text))
    if not matches:
        if had_tool_calls:
            return answer_text.rstrip() + (
                "\n\n> **Note:** The model's response did not include verifiable "
                "citations from the corpus. Please verify the information independently."
            )
        return answer_text

    aliases = _build_aliases_for_payloads(tool_payloads, alias_map)
    grounded_keys = extract_grounded_keys(tool_payloads, alias_map=aliases)
    grounded_count = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal grounded_count
        act_str = match.group(1).strip()
        ref_str = match.group(2).strip()
        candidates = _marker_candidate_keys(act_str, ref_str, aliases) | _extra_marker_keys(
            act_str, ref_str, aliases
        )
        if candidates and (candidates & grounded_keys):
            grounded_count += 1
            return match.group(0)
        log.info("ungrounded citation stripped: act=%s ref=%s", act_str, ref_str)
        return ""

    result = CITATION_MARKER_RE.sub(_replace, answer_text)
    result = _repair_whitespace_around_markers(result)

    if grounded_count == 0 and had_tool_calls:
        result = result.rstrip() + (
            "\n\n> **Note:** The model's response did not include verifiable "
            "citations from the corpus. Please verify the information independently."
        )
    return result


def verify_citations(
    answer_text: str,
    tool_messages_content: Sequence[str],
    *,
    had_tool_calls: bool = False,
) -> str:
    """Verify inline ``[[act: X, ref: Y]]`` markers in the answer against tool results."""
    return verify_inline_markers(
        answer_text,
        tool_messages_content,
        alias_map=_DEFAULT_ALIAS_MAP,
        had_tool_calls=had_tool_calls,
    )
