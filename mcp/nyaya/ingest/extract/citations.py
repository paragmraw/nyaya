"""Citation graph edge extractor delegating to shared ``nyaya.citations.parse_citations``."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal

from ...citations import parse_citations


def _excerpt_around(text: str, span: tuple[int, int] | None, window: int = 120) -> str:
    if not span:
        return text[: window * 2].strip()
    start = max(0, span[0] - window)
    end = min(len(text), span[1] + window)
    return " ".join(text[start:end].split())


def extract_citation_edges(
    *,
    from_id: str,
    from_kind: Literal["provision", "judgment"],
    text: str,
    from_year: int | None = None,
    from_bench_strength: int | None = None,
    alias_map: Mapping[str, str] | None = None,
    known_judgment_map: Mapping[str, str] | None = None,
    known_provision_map: Mapping[str, str] | None = None,
    para_ref: str | None = None,
) -> list[dict[str, Any]]:
    """Extract deduplicated citation graph edges from judgment or provision text."""
    parsed_list = parse_citations(text, alias_map=alias_map)
    j_map = known_judgment_map or {}
    p_map = known_provision_map or {}

    seen: set[tuple[str, str, str]] = set()
    edges: list[dict[str, Any]] = []

    for p in parsed_list:
        to_kind: Literal["provision", "judgment"]
        relation: str
        if p.kind in ("cnr", "sc_neutral", "hc_neutral", "air", "scc", "scr", "other_reporter"):
            to_kind = "judgment"
            to_id = j_map.get(p.canonical_key, p.canonical_key)
            relation = "cites"
        else:
            to_kind = "provision"
            to_id = p_map.get(p.canonical_key, p.canonical_key)
            relation = "interprets" if from_kind == "judgment" else "references"

        if to_id == from_id:
            continue

        edge_key = (from_id, to_id, relation)
        if edge_key in seen:
            continue
        seen.add(edge_key)

        edges.append(
            {
                "from_id": from_id,
                "from_kind": from_kind,
                "to_id": to_id,
                "to_kind": to_kind,
                "relation": relation,
                "para_ref": para_ref,
                "quoting_excerpt": _excerpt_around(text, p.span),
                "from_year": from_year,
                "from_bench_str": from_bench_strength,
                "precedent_status": "unverified_automated",
            }
        )

    return edges
