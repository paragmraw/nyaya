"""compare_provisions: IPC<->BNS, CrPC<->BNSS, IEA<->BSA concordance and side-by-side comparison."""

from __future__ import annotations

import difflib
import re
from typing import Any

from ..exceptions import InvalidFilter, NotFound
from ..models import (
    ClauseDiff,
    ComparisonMappingType,
    ProvisionComparisonResponse,
    ProvisionRecord,
    ProvisionResponse,
)
from ..storage import get_backend
from ._error import structured_errors
from ._util import (
    effective_commercial_safe,
    ensure_framed,
    resolve_act_and_ref,
    run_sync,
)

_CLAUSE_SPLIT_RE = re.compile(r"(?<=[.;:])\s+|\n+")


def _record_to_response(rec: ProvisionRecord, *, include_text: bool) -> ProvisionResponse:
    framed_text = (
        ensure_framed(rec.text, doc_id=rec.doc_id, provenance=rec.provenance)
        if include_text and rec.text
        else ""
    )
    return ProvisionResponse(
        act=rec.act,
        act_full_name=rec.act_full_name,
        jurisdiction=rec.jurisdiction,
        kind=rec.kind,
        ref=rec.ref,
        title=rec.title,
        chapter_num=rec.chapter_num,
        chapter_title=rec.chapter_title,
        part=rec.part,
        text=framed_text,
        validity=rec.validity,
        top_links=rec.top_links[:15],
        provenance=rec.provenance,
    )


def _compute_clause_diffs(src_text: str, tgt_text: str) -> list[ClauseDiff]:
    if not src_text or not tgt_text:
        return []
    src_clauses = [c.strip() for c in _CLAUSE_SPLIT_RE.split(src_text) if c.strip()]
    tgt_clauses = [c.strip() for c in _CLAUSE_SPLIT_RE.split(tgt_text) if c.strip()]
    matcher = difflib.SequenceMatcher(a=src_clauses, b=tgt_clauses)
    diffs: list[ClauseDiff] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        src_ex = " ".join(src_clauses[i1:i2])[:400] or None
        tgt_ex = " ".join(tgt_clauses[j1:j2])[:400] or None
        if tag == "replace":
            diffs.append(ClauseDiff(op="modified", source_excerpt=src_ex, target_excerpt=tgt_ex))
        elif tag == "delete":
            diffs.append(ClauseDiff(op="removed", source_excerpt=src_ex, target_excerpt=None))
        elif tag == "insert":
            diffs.append(ClauseDiff(op="added", source_excerpt=None, target_excerpt=tgt_ex))
    return diffs[:15]


def compare_provisions(
    source: str = "",
    target: str | None = None,
    *,
    old_act: str | None = None,
    old_section: str | None = None,
    new_act: str | None = None,
    new_section: str | None = None,
    include_text: bool = True,
    commercial_safe_only: bool = False,
) -> ProvisionComparisonResponse:
    """Compare old/new criminal code provisions (IPC<->BNS, CrPC<->BNSS, IEA<->BSA) or two explicit provisions."""
    backend = get_backend()
    cs_only = effective_commercial_safe(commercial_safe_only)
    alias_map = backend.get_alias_map()

    src_cit = source.strip()
    if not src_cit and old_act and old_section:
        src_cit = f"{old_act} s.{old_section}"
    if not src_cit:
        raise InvalidFilter(
            "source citation is required (e.g. 'IPC s.302', 'CrPC s.438', 'IEA s.65B', 'BNS s.103').",
            kind="source",
            hint="Pass source='IPC s.302' to compare with its BNS counterpart.",
        )

    src_act, src_ref, _ = resolve_act_and_ref(
        act=old_act or "",
        ref=old_section or "",
        citation=src_cit,
        alias_map=alias_map,
    )
    src_rec = backend.get_provision(
        src_act,
        src_ref,
        include_text=True,
        include_links=True,
        commercial_safe_only=cs_only,
    )
    if src_rec is None:
        raise NotFound(
            f"Source provision {src_cit!r} not found in corpus.",
            kind="provision",
            hint="Use search_statutes or browse_statute to find valid provision references.",
        )

    explicit_target = (target or "").strip()
    if not explicit_target and new_act and new_section:
        explicit_target = f"{new_act} s.{new_section}"

    entries = backend.compare_provisions(src_rec.act, src_rec.ref, commercial_safe_only=cs_only)

    target_records: list[ProvisionRecord] = []
    mapping_type: ComparisonMappingType = "one_to_one"
    diff_summary: str | None = None
    punishment_delta: str | None = None
    procedural_delta: str | None = None
    transitional_rule: str | None = src_rec.validity.transitional_note
    provenance = src_rec.provenance

    if explicit_target:
        tgt_act, tgt_ref, _ = resolve_act_and_ref(
            act=new_act or "",
            ref=new_section or "",
            citation=explicit_target,
            alias_map=alias_map,
        )
        tgt_rec = backend.get_provision(
            tgt_act,
            tgt_ref,
            include_text=True,
            include_links=True,
            commercial_safe_only=cs_only,
        )
        if tgt_rec is None:
            raise NotFound(
                f"Target provision {explicit_target!r} not found in corpus.",
                kind="provision",
            )
        target_records.append(tgt_rec)
        matched_entry = next(
            (
                e
                for e in entries
                if (e.old_doc_id == src_rec.doc_id and e.new_doc_id == tgt_rec.doc_id)
                or (e.new_doc_id == src_rec.doc_id and e.old_doc_id == tgt_rec.doc_id)
            ),
            None,
        )
        if matched_entry is not None:
            mapping_type = matched_entry.mapping_type
            diff_summary = matched_entry.substantive_delta
            punishment_delta = matched_entry.punishment_delta
            procedural_delta = matched_entry.procedural_delta
            transitional_rule = matched_entry.savings_note or transitional_rule
            provenance = matched_entry.provenance
        else:
            mapping_type = "manual_comparison"
            diff_summary = f"Manual side-by-side comparison between {src_rec.act} {src_rec.ref} and {tgt_rec.act} {tgt_rec.ref}."
    else:
        if entries:
            first = entries[0]
            mapping_type = first.mapping_type
            diff_summary = first.substantive_delta
            punishment_delta = first.punishment_delta
            procedural_delta = first.procedural_delta
            transitional_rule = first.savings_note or transitional_rule
            provenance = first.provenance
            for e in entries:
                counterpart_ref = e.new_ref if e.old_doc_id == src_rec.doc_id else e.old_ref
                if counterpart_ref is not None:
                    c_rec = backend.get_provision(
                        counterpart_ref.act,
                        counterpart_ref.ref,
                        include_text=True,
                        include_links=True,
                        commercial_safe_only=cs_only,
                    )
                    if c_rec is not None and all(r.doc_id != c_rec.doc_id for r in target_records):
                        target_records.append(c_rec)
        else:
            # Check validity replaced_by / replaces pointers
            fallback_refs = src_rec.validity.replaced_by or src_rec.validity.replaces
            for pr in fallback_refs:
                c_rec = backend.get_provision(
                    pr.act,
                    pr.ref,
                    include_text=True,
                    include_links=True,
                    commercial_safe_only=cs_only,
                )
                if c_rec is not None:
                    target_records.append(c_rec)
            if not target_records:
                raise NotFound(
                    f"No concordance mapping found for {src_rec.act} {src_rec.ref}.",
                    kind="concordance",
                    hint="Pass target='...' explicitly to compare two arbitrary provisions.",
                )

    clause_diffs: list[ClauseDiff] = []
    if include_text and target_records:
        clause_diffs = _compute_clause_diffs(src_rec.text, target_records[0].text)

    if not transitional_rule and src_rec.act in {"IPC", "CrPC", "EvidenceAct", "BNS", "BNSS", "BSA"}:
        transitional_rule = (
            "BNSS s.531 savings rule: offences committed, FIRs registered, or trials/appeals pending "
            "before 2024-07-01 continue to be governed by IPC/CrPC/IEA; causes on or after 2024-07-01 "
            "are governed by BNS/BNSS/BSA."
        )

    return ProvisionComparisonResponse(
        mapping_type=mapping_type,
        source_provision=_record_to_response(src_rec, include_text=include_text),
        target_provisions=[_record_to_response(r, include_text=include_text) for r in target_records],
        diff_summary=diff_summary,
        clause_diffs=clause_diffs,
        punishment_delta=punishment_delta,
        procedural_delta=procedural_delta,
        transitional_rule=transitional_rule,
        provenance=provenance,
    )


def register(mcp: Any) -> None:
    @mcp.tool(
        name="compare_provisions",
        description=(
            "Compare statutory provisions across the 1 July 2024 criminal code transition "
            "(IPC <-> BNS, CrPC <-> BNSS, Indian Evidence Act <-> BSA) or between any two explicit "
            "provisions. Auto-resolves verified one-to-one, one-to-many, many-to-one, and omitted "
            "concordance mappings when target is omitted, returning both verbatim texts (<corpus_text>), "
            "clause-level diffs, substantive/punishment/procedural deltas, and the BNSS s.531 savings rule."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
            "title": "Compare IPC/CrPC/IEA and BNS/BNSS/BSA provisions",
        },
    )
    @structured_errors
    @run_sync
    def _tool(
        source: str,
        target: str | None = None,
        include_text: bool = True,
        commercial_safe_only: bool = False,
    ) -> ProvisionComparisonResponse:
        return compare_provisions(
            source=source,
            target=target,
            include_text=include_text,
            commercial_safe_only=commercial_safe_only,
        )
