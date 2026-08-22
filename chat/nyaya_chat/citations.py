"""Citation verification: parse and verify [[act: X, ref: Y]] markers.

After the synthesis LLM produces an answer, this module:
1. Parses all [[act: X, ref: Y]] citation markers from the answer text.
2. Verifies each citation against the tool results returned during this turn.
3. Strips ungrounded citations (those not backed by any tool result).
4. If zero grounded citations remain AND tools were called, appends a caveat.

This is a programmatic safety net — the synthesis prompt already instructs
the model to ground citations, but prompts are not guarantees. Legal Q&A
demands verifiable provenance.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger("nyaya_chat.citations")

# Matches [[act: <short_name>, ref: <ref>]] inline citation markers.
_CITE_RE = re.compile(r"\[\[act:\s*([^,\]]+?)\s*,\s*ref:\s*([^\]]+?)\s*\]\]")


@dataclass
class Citation:
    act: str
    ref: str


def parse_citations(text: str) -> list[Citation]:
    """Extract all [[act: X, ref: Y]] markers from the answer text."""
    citations: list[Citation] = []
    seen: set[str] = set()
    for m in _CITE_RE.finditer(text):
        act = m.group(1).strip()
        ref = m.group(2).strip()
        key = f"{act}|{ref}"
        if key not in seen:
            seen.add(key)
            citations.append(Citation(act=act, ref=ref))
    return citations


def _normalize(s: str) -> str:
    """Normalize for comparison: lowercase, strip whitespace and common prefixes."""
    s = s.strip().lower()
    # Strip leading "s." / "section " / "art." / "article " prefixes
    s = re.sub(r"^(?:s(?:ec(?:tion)?)?\.?\s*|art(?:icle)?\.?\s*)", "", s)
    return s.strip()


def _extract_numeric(s: str) -> str:
    """Extract the numeric core from a ref string (e.g. 's. 302' -> '302', 'art. 21A' -> '21a')."""
    s = _normalize(s)
    m = re.search(r"(\d+[a-z]*)", s)
    return m.group(1) if m else ""


def _extract_tool_acts_refs(tool_messages_content: list[str]) -> set[str]:
    """Extract (act, ref) pairs from tool result JSON strings.

    Tool results are JSON strings from the native tools. They may be:
    - A Document: {"act": "IPC", "ref": "302", ...}
    - A SearchResponse: {"results": [{"act": "IPC", "ref": "s. 302", ...}]}
    - A CrossRefList: {"references": [{"from_act": "IPC", "from_section": "302", ...}]}
    - An ActsList: {"acts": [{"short_name": "IPC", ...}]}
    - An error: {"error": {...}}
    """
    import json

    pairs: set[str] = set()
    for content in tool_messages_content:
        if not content or not content.strip():
            continue
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            # Not JSON — might be a plain text result; skip
            continue

        if not isinstance(data, dict):
            continue

        # Error responses don't contribute citations
        if "error" in data:
            continue

        # Single document: {"act": "IPC", "ref": "302", "kind": "section", ...}
        if "act" in data and "ref" in data:
            act = _normalize(str(data.get("act") or ""))
            ref = _normalize(str(data.get("ref") or ""))
            if act and ref:
                pairs.add(f"{act}|{ref}")
            # Also add without the "s." prefix normalization
            act_raw = str(data.get("act") or "").strip().lower()
            ref_raw = str(data.get("ref") or "").strip().lower()
            if act_raw and ref_raw:
                pairs.add(f"{act_raw}|{ref_raw}")

        # SearchResponse: {"results": [{"act": "IPC", "ref": "s. 302", ...}]}
        if "results" in data and isinstance(data["results"], list):
            for r in data["results"]:
                if isinstance(r, dict):
                    act = _normalize(str(r.get("act") or ""))
                    ref = _normalize(str(r.get("ref") or ""))
                    if act and ref:
                        pairs.add(f"{act}|{ref}")

        # CrossRefList: {"references": [{"from_act": ..., "from_section": ..., "to_act": ..., "to_section": ...}]}
        if "references" in data and isinstance(data["references"], list):
            for r in data["references"]:
                if isinstance(r, dict):
                    for act_key, ref_key in [("from_act", "from_section"), ("to_act", "to_section")]:
                        act = _normalize(str(r.get(act_key) or ""))
                        ref = _normalize(str(r.get(ref_key) or ""))
                        if act and ref:
                            pairs.add(f"{act}|{ref}")

        # ActsList: {"acts": [{"short_name": "IPC", ...}]}
        if "acts" in data and isinstance(data["acts"], list):
            for a in data["acts"]:
                if isinstance(a, dict):
                    act = str(a.get("short_name") or "").strip().lower()
                    if act:
                        # Acts don't have a ref, but we record the act name
                        # so citations referencing this act are considered grounded
                        pairs.add(f"{act}|")

    return pairs


def verify_citations(
    answer_text: str,
    tool_messages_content: list[str],
    *,
    had_tool_calls: bool = False,
) -> str:
    """Verify citations in the answer against tool results.

    Args:
        answer_text: The synthesis LLM's full answer text.
        tool_messages_content: List of ToolMessage content strings from this turn.
        had_tool_calls: Whether any tool calls were made during this turn.

    Returns:
        The answer text with ungrounded citations stripped. If zero grounded
        citations remain and tools were called, a caveat is appended.
    """
    citations = parse_citations(answer_text)
    if not citations:
        if had_tool_calls:
            return answer_text.rstrip() + (
                "\n\n> **Note:** The model's response did not include verifiable "
                "citations from the corpus. Please verify the information independently."
            )
        return answer_text

    tool_pairs = _extract_tool_acts_refs(tool_messages_content)

    # Check each citation against tool results
    grounded: list[Citation] = []
    ungrounded: list[Citation] = []
    for cite in citations:
        act_n = _normalize(cite.act)
        ref_n = _normalize(cite.ref)
        # Check exact (act, ref) match (both normalized -- "s." prefix stripped)
        if f"{act_n}|{ref_n}" in tool_pairs:
            grounded.append(cite)
        # Also check if the ref matches as a substring (e.g. "302" in "s. 302")
        elif any(
            p.startswith(f"{act_n}|") and p.split("|", 1)[1] == ref_n
            for p in tool_pairs
        ):
            grounded.append(cite)
        # Also try whitespace-insensitive ref match
        elif any(
            p.startswith(f"{act_n}|")
            and p.split("|", 1)[1].replace(" ", "") == ref_n.replace(" ", "")
            for p in tool_pairs
        ):
            grounded.append(cite)
        # Also try numeric core match: extract the numeric part from both refs
        # e.g. "s. 302" -> "302", "302" -> "302", "s.302" -> "302"
        elif any(
            p.startswith(f"{act_n}|")
            and _extract_numeric(p.split("|", 1)[1]) == _extract_numeric(ref_n)
            and _extract_numeric(ref_n)  # only if there IS a numeric part
            for p in tool_pairs
        ):
            grounded.append(cite)
        else:
            ungrounded.append(cite)
            log.info("ungrounded citation stripped: act=%s ref=%s", cite.act, cite.ref)

    # Strip ungrounded citation markers from the text
    result = answer_text
    for cite in ungrounded:
        marker = f"[[act: {cite.act}, ref: {cite.ref}]]"
        result = result.replace(marker, "")

    # Clean up any double spaces left by removed markers
    result = re.sub(r"  +", " ", result)

    # If zero grounded citations remain and tools were called, add caveat
    if not grounded and had_tool_calls:
        result = result.rstrip() + (
            "\n\n> **Note:** The model's response did not include verifiable "
            "citations from the corpus. Please verify the information independently."
        )

    return result
