"""Deterministic legal query analyzer and IPC<->BNS / CrPC<->BNSS / IEA<->BSA expander."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from ..citations import _DEFAULT_ALIAS_MAP, ParsedCitation, parse_citations
from ..storage.protocols import CorpusBackend

_PRACTITIONER_SHORTHANDS: tuple[tuple[tuple[str, ...], list[tuple[str, str]]], ...] = (
    (("anticipatory bail",), [("BNSS", "482"), ("CrPC", "438")]),
    (("cheque bounce", "cheque dishonour"), [("NIAct", "138")]),
    (("rejection of plaint",), [("CPC", "Order 7 Rule 11")]),
)

# Built-in golden bidirectional criminal-code and Income-tax equivalents so query
# expansion works even on minimal/in-memory backends.
_STATIC_EQUIVALENTS: dict[tuple[str, str], list[tuple[str, str]]] = {
    ("IPC", "34"): [("BNS", "3(5)")],
    ("BNS", "3(5)"): [("IPC", "34")],
    ("IPC", "107"): [("BNS", "45")],
    ("BNS", "45"): [("IPC", "107")],
    ("IPC", "120B"): [("BNS", "61(2)")],
    ("BNS", "61(2)"): [("IPC", "120B")],
    ("IPC", "124A"): [("BNS", "152")],
    ("BNS", "152"): [("IPC", "124A")],
    ("IPC", "149"): [("BNS", "190")],
    ("BNS", "190"): [("IPC", "149")],
    ("IPC", "299"): [("BNS", "100")],
    ("BNS", "100"): [("IPC", "299")],
    ("IPC", "300"): [("BNS", "101")],
    ("BNS", "101"): [("IPC", "300")],
    ("IPC", "302"): [("BNS", "103")],
    ("BNS", "103"): [("IPC", "302")],
    ("IPC", "304"): [("BNS", "105")],
    ("BNS", "105"): [("IPC", "304")],
    ("IPC", "304A"): [("BNS", "106")],
    ("BNS", "106"): [("IPC", "304A")],
    ("IPC", "304B"): [("BNS", "80")],
    ("BNS", "80"): [("IPC", "304B")],
    ("IPC", "307"): [("BNS", "109")],
    ("BNS", "109"): [("IPC", "307")],
    ("IPC", "323"): [("BNS", "115(2)")],
    ("BNS", "115(2)"): [("IPC", "323")],
    ("IPC", "324"): [("BNS", "118(1)")],
    ("BNS", "118(1)"): [("IPC", "324")],
    ("IPC", "325"): [("BNS", "117(2)")],
    ("BNS", "117(2)"): [("IPC", "325")],
    ("IPC", "326"): [("BNS", "118(2)")],
    ("BNS", "118(2)"): [("IPC", "326")],
    ("IPC", "354"): [("BNS", "74")],
    ("BNS", "74"): [("IPC", "354")],
    ("IPC", "354A"): [("BNS", "75")],
    ("BNS", "75"): [("IPC", "354A")],
    ("IPC", "354D"): [("BNS", "78")],
    ("BNS", "78"): [("IPC", "354D")],
    ("IPC", "363"): [("BNS", "137(2)")],
    ("BNS", "137(2)"): [("IPC", "363")],
    ("IPC", "375"): [("BNS", "63")],
    ("BNS", "63"): [("IPC", "375")],
    ("IPC", "376"): [("BNS", "64")],
    ("BNS", "64"): [("IPC", "376")],
    ("IPC", "378"): [("BNS", "303")],
    ("BNS", "303"): [("IPC", "378")],
    ("IPC", "379"): [("BNS", "303(2)")],
    ("BNS", "303(2)"): [("IPC", "379")],
    ("IPC", "392"): [("BNS", "309(4)")],
    ("BNS", "309(4)"): [("IPC", "392")],
    ("IPC", "406"): [("BNS", "316(2)")],
    ("BNS", "316(2)"): [("IPC", "406")],
    ("IPC", "420"): [("BNS", "318(4)")],
    ("BNS", "318(4)"): [("IPC", "420")],
    ("IPC", "441"): [("BNS", "329(1)")],
    ("BNS", "329(1)"): [("IPC", "441")],
    ("IPC", "498A"): [("BNS", "85")],
    ("BNS", "85"): [("IPC", "498A")],
    ("IPC", "499"): [("BNS", "356")],
    ("BNS", "356"): [("IPC", "499")],
    ("IPC", "500"): [("BNS", "356(2)")],
    ("BNS", "356(2)"): [("IPC", "500")],
    ("IPC", "506"): [("BNS", "351")],
    ("BNS", "351"): [("IPC", "506")],
    ("IPC", "511"): [("BNS", "62")],
    ("BNS", "62"): [("IPC", "511")],
    ("CrPC", "41"): [("BNSS", "35")],
    ("CrPC", "41A"): [("BNSS", "35")],
    ("BNSS", "35"): [("CrPC", "41A"), ("CrPC", "41")],
    ("CrPC", "125"): [("BNSS", "144")],
    ("BNSS", "144"): [("CrPC", "125")],
    ("CrPC", "144"): [("BNSS", "163")],
    ("BNSS", "163"): [("CrPC", "144")],
    ("CrPC", "154"): [("BNSS", "173")],
    ("BNSS", "173"): [("CrPC", "154")],
    ("CrPC", "156"): [("BNSS", "175")],
    ("BNSS", "175"): [("CrPC", "156")],
    ("CrPC", "161"): [("BNSS", "180")],
    ("BNSS", "180"): [("CrPC", "161")],
    ("CrPC", "164"): [("BNSS", "183")],
    ("BNSS", "183"): [("CrPC", "164")],
    ("CrPC", "167"): [("BNSS", "187")],
    ("BNSS", "187"): [("CrPC", "167")],
    ("CrPC", "173"): [("BNSS", "193")],
    ("BNSS", "193"): [("CrPC", "173")],
    ("CrPC", "197"): [("BNSS", "218")],
    ("BNSS", "218"): [("CrPC", "197")],
    ("CrPC", "200"): [("BNSS", "223")],
    ("BNSS", "223"): [("CrPC", "200")],
    ("CrPC", "313"): [("BNSS", "351")],
    ("BNSS", "351"): [("CrPC", "313")],
    ("CrPC", "389"): [("BNSS", "430")],
    ("BNSS", "430"): [("CrPC", "389")],
    ("CrPC", "438"): [("BNSS", "482")],
    ("BNSS", "482"): [("CrPC", "438")],
    ("CrPC", "439"): [("BNSS", "483")],
    ("BNSS", "483"): [("CrPC", "439")],
    ("CrPC", "482"): [("BNSS", "528")],
    ("BNSS", "528"): [("CrPC", "482")],
    ("EvidenceAct", "3"): [("BSA", "2")],
    ("BSA", "2"): [("EvidenceAct", "3")],
    ("EvidenceAct", "27"): [("BSA", "23")],
    ("BSA", "23"): [("EvidenceAct", "27")],
    ("EvidenceAct", "32"): [("BSA", "26")],
    ("BSA", "26"): [("EvidenceAct", "32")],
    ("EvidenceAct", "45"): [("BSA", "39")],
    ("BSA", "39"): [("EvidenceAct", "45")],
    ("EvidenceAct", "65A"): [("BSA", "62")],
    ("BSA", "62"): [("EvidenceAct", "65A")],
    ("EvidenceAct", "65B"): [("BSA", "63")],
    ("BSA", "63"): [("EvidenceAct", "65B")],
    ("EvidenceAct", "113A"): [("BSA", "117")],
    ("BSA", "117"): [("EvidenceAct", "113A")],
    ("EvidenceAct", "113B"): [("BSA", "118")],
    ("BSA", "118"): [("EvidenceAct", "113B")],
    ("EvidenceAct", "114A"): [("BSA", "120")],
    ("BSA", "120"): [("EvidenceAct", "114A")],
    ("IncomeTax1961", "54"): [("IncomeTax2025", "82")],
    ("IncomeTax2025", "82"): [("IncomeTax1961", "54")],
    ("IncomeTax1961", "68"): [("IncomeTax2025", "102")],
    ("IncomeTax2025", "102"): [("IncomeTax1961", "68")],
    ("IncomeTax1961", "69"): [("IncomeTax2025", "103")],
    ("IncomeTax2025", "103"): [("IncomeTax1961", "69")],
    ("IncomeTax1961", "80C"): [("IncomeTax2025", "123")],
    ("IncomeTax2025", "123"): [("IncomeTax1961", "80C")],
    ("IncomeTax1961", "139"): [("IncomeTax2025", "263")],
    ("IncomeTax2025", "263"): [("IncomeTax1961", "139")],
    ("IncomeTax1961", "143"): [("IncomeTax2025", "270")],
    ("IncomeTax2025", "270"): [("IncomeTax1961", "143")],
    ("IncomeTax1961", "147"): [("IncomeTax2025", "279")],
    ("IncomeTax2025", "279"): [("IncomeTax1961", "147")],
    ("IncomeTax1961", "148"): [("IncomeTax2025", "280")],
    ("IncomeTax2025", "280"): [("IncomeTax1961", "148")],
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

    canon_act = _DEFAULT_ALIAS_MAP.get(act.strip().lower(), act.strip())
    static_hits = _STATIC_EQUIVALENTS.get((act, norm_ref)) or _STATIC_EQUIVALENTS.get((canon_act, norm_ref), [])
    for pair in static_hits:
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

        if raw:
            raw_lower = raw.lower()
            canon_filter_act: str | None = None
            if act and act.strip():
                act_clean = act.strip()
                canon_filter_act = (alias_map or {}).get(act_clean.lower(), act_clean)
            for phrases, pairs in _PRACTITIONER_SHORTHANDS:
                if any(phrase in raw_lower for phrase in phrases):
                    allowed_acts = {a.lower() for a, _ in pairs}
                    if canon_filter_act and canon_filter_act.lower() not in allowed_acts:
                        continue
                    for pair in pairs:
                        if pair not in seen_statutes and pair not in equiv_statutes:
                            equiv_statutes.append(pair)

    return AnalyzedQuery(
        raw_query=raw,
        parsed_citations=parsed,
        exact_statute_refs=exact_statutes,
        equivalent_statute_refs=equiv_statutes,
        exact_judgment_refs=exact_judgments,
    )
