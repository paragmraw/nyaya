"""Corpus-wide automated citation graph builder using ``extract_citation_edges``."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from ...citations import parse_citations
from ..extract.citations import extract_citation_edges

_PARA_BLOCK_RE = re.compile(r"(?m)(?=^\s*(?:\d{1,4}\.|\[\d{1,4}\])\s+)")
_PARA_NUM_RE = re.compile(r"^\s*(?:(\d{1,4})\.|\[(\d{1,4})\])\s+")


def _build_known_judgment_map(
    sc_judgments: Iterable[Mapping[str, Any]],
    hc_judgments: Iterable[Mapping[str, Any]],
    *,
    alias_map: Mapping[str, str] | None = None,
) -> dict[str, str]:
    known: dict[str, str] = {}
    for row in (*sc_judgments, *hc_judgments):
        case_id = str(row.get("case_id") or "").strip()
        if not case_id:
            continue
        candidates: list[str] = []
        for field in (
            "cnr",
            "neutral_citation",
            "air_citation",
            "scc_citation",
            "scr_citation",
            "citation",
        ):
            val = row.get(field)
            if isinstance(val, str) and val.strip():
                candidates.append(val.strip())
        reps = row.get("reporter_citations")
        if isinstance(reps, (list, tuple)):
            for rep in reps:
                if isinstance(rep, str) and rep.strip():
                    candidates.append(rep.strip())
        for raw in candidates:
            for parsed in parse_citations(raw, alias_map=alias_map):
                if parsed.kind in (
                    "cnr",
                    "sc_neutral",
                    "hc_neutral",
                    "air",
                    "scc",
                    "scr",
                    "other_reporter",
                ):
                    known.setdefault(parsed.canonical_key, case_id)
    return known


def _build_known_provision_map(
    provisions: Iterable[Mapping[str, Any]],
    *,
    alias_map: Mapping[str, str] | None = None,
) -> dict[str, str]:
    known: dict[str, str] = {}
    for prov in provisions:
        doc_id = str(prov.get("doc_id") or "").strip()
        if not doc_id:
            continue
        act = str(
            prov.get("act_short") or prov.get("act") or prov.get("short_name") or ""
        ).strip()
        ref = str(prov.get("ref") or prov.get("ref_norm") or "").strip()
        if not act or not ref:
            continue

        probes = [f"{act} {ref}", f"{act} s.{ref}"]
        if act.lower() == "constitution":
            probes.extend([f"Article {ref}", f"Schedule {ref}"])
        elif act.lower() == "cpc":
            probes.append(f"CPC {ref}")

        for probe in probes:
            for parsed in parse_citations(probe, alias_map=alias_map):
                if parsed.kind in (
                    "statute_section",
                    "regulation_clause",
                    "constitution_article",
                    "constitution_schedule",
                    "cpc_order_rule",
                    "constitution_amendment",
                ):
                    known.setdefault(parsed.canonical_key, doc_id)
    return known


def _split_numbered_blocks(text: str, default_para_ref: str | None) -> list[tuple[str | None, str]]:
    blocks = [b.strip() for b in _PARA_BLOCK_RE.split(text) if b.strip()]
    if not blocks:
        return [(default_para_ref, text)]
    out: list[tuple[str | None, str]] = []
    for blk in blocks:
        m = _PARA_NUM_RE.match(blk)
        if m:
            num = m.group(1) or m.group(2)
            out.append((f"para {num}", blk))
        else:
            out.append((default_para_ref, blk))
    return out


def build_citation_edges_for_corpus(
    sc_judgments: Iterable[Mapping[str, Any]],
    hc_judgments: Iterable[Mapping[str, Any]],
    provisions: Iterable[Mapping[str, Any]] = (),
    alias_map: Mapping[str, str] | None = None,
    max_edges_per_doc: int = 100,
) -> list[dict[str, Any]]:
    """Parse judgment texts across SC + HC corpora, resolve citations to known IDs,
    deduplicate ``(from_id, to_id, relation)``, and cap edges per judgment.
    """
    if max_edges_per_doc <= 0:
        return []

    sc_list = list(sc_judgments)
    hc_list = list(hc_judgments)
    prov_list = list(provisions)

    known_judgment_map = _build_known_judgment_map(sc_list, hc_list, alias_map=alias_map)
    known_provision_map = _build_known_provision_map(prov_list, alias_map=alias_map)

    all_edges: list[dict[str, Any]] = []
    global_seen: set[tuple[str, str, str]] = set()

    for j in (*sc_list, *hc_list):
        from_id = str(j.get("case_id") or "").strip()
        text = str(j.get("text") or "").strip()
        if not from_id or not text:
            continue

        from_year = int(j["year"]) if j.get("year") is not None else None
        from_bench_str = int(j["bench_strength"]) if j.get("bench_strength") is not None else None
        default_para = str(j["para_ref"]).strip() if j.get("para_ref") else None

        doc_count = 0
        for para_ref, block_text in _split_numbered_blocks(text, default_para):
            if doc_count >= max_edges_per_doc:
                break
            extracted = extract_citation_edges(
                from_id=from_id,
                from_kind="judgment",
                text=block_text,
                from_year=from_year,
                from_bench_strength=from_bench_str,
                alias_map=alias_map,
                known_judgment_map=known_judgment_map,
                known_provision_map=known_provision_map,
                para_ref=para_ref,
            )
            for edge in extracted:
                key = (str(edge["from_id"]), str(edge["to_id"]), str(edge["relation"]))
                if key in global_seen:
                    continue
                global_seen.add(key)
                edge["from_year"] = from_year
                edge["from_bench_str"] = from_bench_str
                edge["precedent_status"] = "unverified_automated"
                all_edges.append(edge)
                doc_count += 1
                if doc_count >= max_edges_per_doc:
                    break

    return all_edges
