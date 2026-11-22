"""Deterministic legal query analyzer and IPC<->BNS / CrPC<->BNSS / IEA<->BSA expander."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ..citations import ParsedCitation, parse_citations
from ..storage.protocols import CorpusBackend

# Built-in golden bidirectional criminal-code equivalents so query expansion
# works even on minimal/in-memory backends.
_STATIC_EQUIVALENTS: dict[tuple[str, str], list[tuple[str, str]]] = {
    ("IPC", "302"): [("BNS", "103")],
    ("BNS", "103"): [("IPC", "302")],
    ("IPC", "304A"): [("BNS", "106")],
    ("BNS", "106"): [("IPC", "304A")],
    ("IPC", "304B"): [("BNS", "80")],
    ("BNS", "80"): [("IPC", "304B")],
    ("IPC", "307"): [("BNS", "109")],
    ("BNS", "109"): [("IPC", "307")],
    ("IPC", "354"): [("BNS", "74")],
    ("BNS", "74"): [("IPC", "354")],
    ("IPC", "354A"): [("BNS", "75")],
    ("BNS", "75"): [("IPC", "354A")],
    ("IPC", "354D"): [("BNS", "78")],
    ("BNS", "78"): [("IPC", "354D")],
    ("IPC", "375"): [("BNS", "63")],
    ("BNS", "63"): [("IPC", "375")],
    ("IPC", "376"): [("BNS", "64")],
    ("BNS", "64"): [("IPC", "376")],
    ("IPC", "378"): [("BNS", "303")],
    ("BNS", "303"): [("IPC", "378")],
    ("IPC", "406"): [("BNS", "316(2)")],
    ("BNS", "316(2)"): [("IPC", "406")],
    ("IPC", "420"): [("BNS", "318(4)")],
    ("BNS", "318(4)"): [("IPC", "420")],
    ("IPC", "498A"): [("BNS", "85")],
    ("BNS", "85"): [("IPC", "498A")],
    ("IPC", "499"): [("BNS", "356")],
    ("BNS", "356"): [("IPC", "499")],
    ("IPC", "506"): [("BNS", "351")],
    ("BNS", "351"): [("IPC", "506")],
    ("IPC", "511"): [("BNS", "62")],
    ("BNS", "62"): [("IPC", "511")],
    ("CrPC", "41A"): [("BNSS", "35")],
    ("BNSS", "35"): [("CrPC", "41A")],
    ("CrPC", "125"): [("BNSS", "144")],
    ("BNSS", "144"): [("CrPC", "125")],
    ("CrPC", "154"): [("BNSS", "173")],
    ("BNSS", "173"): [("CrPC", "154")],
    ("CrPC", "161"): [("BNSS", "180")],
    ("BNSS", "180"): [("CrPC", "161")],
    ("CrPC", "167"): [("BNSS", "187")],
    ("BNSS", "187"): [("CrPC", "167")],
    ("CrPC", "438"): [("BNSS", "482")],
    ("BNSS", "482"): [("CrPC", "438")],
    ("CrPC", "482"): [("BNSS", "528")],
    ("BNSS", "528"): [("CrPC", "482")],
    ("EvidenceAct", "27"): [("BSA", "23")],
    ("BSA", "23"): [("EvidenceAct", "27")],
    ("EvidenceAct", "32"): [("BSA", "26")],
    ("BSA", "26"): [("EvidenceAct", "32")],
    ("EvidenceAct", "65B"): [("BSA", "63")],
    ("BSA", "63"): [("EvidenceAct", "65B")],
    ("EvidenceAct", "113B"): [("BSA", "118")],
    ("BSA", "118"): [("EvidenceAct", "113B")],
}


@dataclass(frozen=True)
class AnalyzedQuery:
    raw_query: str
    parsed_citations: list[ParsedCitation] = field(default_factory=list)
    exact_statute_refs: list[tuple[str, str]] = field(default_factory=list)
    equivalent_statute_refs: list[tuple[str, str]] = field(default_factory=list)
    exact_judgment_refs: list[str] = field(default_factory=list)


def expand_equivalent_provisions(
    act: str,
    ref: str,
    *,
    backend: CorpusBackend | None = None,
) -> list[tuple[str, str]]:
    """Expand an ``(act, ref)`` pair to pari-materia criminal code equivalents."""
    norm_ref = ref.strip().upper()
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    if backend is not None:
        try:
            entries = backend.compare_provisions(act, ref)
            for entry in entries:
                for p_ref in (entry.old_ref, entry.new_ref):
                    if p_ref is None:
                        continue
                    pair = (p_ref.act, p_ref.ref)
                    if (pair[0].lower(), pair[1].lower()) != (act.lower(), ref.strip().lower()) and pair not in seen:
                        seen.add(pair)
                        out.append(pair)
        except Exception:
            pass

    for pair in _STATIC_EQUIVALENTS.get((act, norm_ref), []):
        if pair not in seen:
            seen.add(pair)
            out.append(pair)
    return out


def analyze_query(
    query: str | None,
    *,
    act: str | None = None,
    section: str | None = None,
    include_equivalent_provisions: bool = True,
    alias_map: Mapping[str, str] | None = None,
    backend: CorpusBackend | None = None,
) -> AnalyzedQuery:
    """Deterministically extract citations and equivalent statutory provisions."""
    raw = (query or "").strip()
    parsed = parse_citations(raw, alias_map=alias_map) if raw else []

    exact_statutes: list[tuple[str, str]] = []
    exact_judgments: list[str] = []
    seen_statutes: set[tuple[str, str]] = set()

    if act and section:
        pair = (act.strip(), section.strip())
        seen_statutes.add(pair)
        exact_statutes.append(pair)

    for c in parsed:
        if c.kind in {
            "statute_section",
            "regulation_clause",
            "constitution_article",
            "constitution_schedule",
            "cpc_order_rule",
        }:
            if c.act_or_court:
                pair = (c.act_or_court, c.base_ref)
                if pair not in seen_statutes:
                    seen_statutes.add(pair)
                    exact_statutes.append(pair)
        elif c.kind in {"cnr", "sc_neutral", "hc_neutral", "air", "scc", "scr", "other_reporter"}:
            exact_judgments.append(c.base_ref)

    equiv_statutes: list[tuple[str, str]] = []
    if include_equivalent_provisions:
        for s_act, s_ref in exact_statutes:
            for eq in expand_equivalent_provisions(s_act, s_ref, backend=backend):
                if eq not in seen_statutes and eq not in equiv_statutes:
                    equiv_statutes.append(eq)

    return AnalyzedQuery(
        raw_query=raw,
        parsed_citations=parsed,
        exact_statute_refs=exact_statutes,
        equivalent_statute_refs=equiv_statutes,
        exact_judgment_refs=exact_judgments,
    )
