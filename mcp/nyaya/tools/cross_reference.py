"""cross_reference: bidirectional statutory and judicial citation cross-references."""

from __future__ import annotations

from typing import Any, Literal

from ..exceptions import NotFound
from ..models import (
    CitationEdgeRecord,
    CrossRefDirection,
    CrossRefResponse,
    ProvisionRef,
    StatutoryLink,
)
from ..retrieval import decode_cursor, encode_cursor
from ..storage import get_backend
from ._error import structured_errors
from ._util import (
    effective_commercial_safe,
    ensure_framed,
    resolve_act_and_ref,
    run_sync,
)

RelationKind = Literal[
    "replaced_by",
    "repeals",
    "references",
    "corresponds_to",
    "amends",
    "delegates_to",
    "cites",
    "invalidates",
    "interprets",
]


def cross_reference(
    act: str,
    ref: str = "",
    section: str | None = None,
    direction: CrossRefDirection = "both",
    relation_kind: RelationKind | None = None,
    commercial_safe_only: bool = False,
    limit: int = 25,
    offset: int = 0,
    cursor: str | None = None,
) -> CrossRefResponse:
    """Look up bidirectional statutory cross-references for a section, article, schedule, or rule."""
    backend = get_backend()
    cs_only = effective_commercial_safe(commercial_safe_only)
    raw_ref = ref or (section or "")
    resolved_act, resolved_ref, _ = resolve_act_and_ref(
        act=act,
        ref=raw_ref,
        alias_map=backend.get_alias_map(),
    )
    prov = backend.get_provision(
        resolved_act,
        resolved_ref,
        include_text=False,
        include_links=False,
        commercial_safe_only=cs_only,
    )
    if prov is None:
        raise NotFound(
            f"Provision {resolved_ref!r} of {resolved_act!r} not found in corpus.",
            kind="provision",
            hint="Use browse_statute or search_statutes to find valid provision references.",
        )

    clamped_limit = max(1, min(int(limit), 100))
    resolved_offset = decode_cursor(cursor, offset)

    source_ref = ProvisionRef(
        act=prov.act,
        jurisdiction=prov.jurisdiction,
        ref=prov.ref,
        title=prov.title,
        kind=prov.kind,
    )

    def _edge_refs(e: CitationEdgeRecord) -> tuple[ProvisionRef, ProvisionRef]:
        from_r = e.from_ref or (source_ref if e.from_id == prov.doc_id else ProvisionRef(act=prov.act, ref=e.from_id))
        to_r = e.to_ref or (source_ref if e.to_id == prov.doc_id else ProvisionRef(act=prov.act, ref=e.to_id))
        return from_r, to_r

    def _dedup_key(from_r: ProvisionRef, to_r: ProvisionRef, rel: str) -> tuple[str, str, str, str, str]:
        return (
            from_r.act.upper(),
            from_r.ref.upper(),
            to_r.act.upper(),
            to_r.ref.upper(),
            rel,
        )

    def _edge_to_link(e: CitationEdgeRecord) -> StatutoryLink:
        from_r, to_r = _edge_refs(e)
        note = e.quoting_excerpt
        if note:
            note = ensure_framed(
                note,
                doc_id=f"{e.from_id}->{e.to_id}",
                provenance=e.provenance or prov.provenance,
            )
        return StatutoryLink(
            from_ref=from_r,
            to_ref=to_r,
            relation_kind=e.relation,
            context_note=note,
            provenance=e.provenance or prov.provenance,
        )

    extra_conc_links: list[StatutoryLink] = []
    edges: list[CitationEdgeRecord] = []
    edge_total = 0

    if relation_kind in (None, "replaced_by", "corresponds_to"):
        conc_rel = relation_kind if relation_kind in ("replaced_by", "corresponds_to") else "replaced_by"
        conc_entries = backend.compare_provisions(
            prov.act,
            prov.ref,
            commercial_safe_only=cs_only,
        )
        if conc_entries:
            # Fetch existing edges of the same relation to avoid duplicating concordance links
            # even when citation_edges has more rows than a single page.
            existing_edges, _ = backend.get_cross_refs(
                prov.doc_id,
                direction=direction,
                relation=conc_rel,
                limit=100,
                offset=0,
                commercial_safe_only=cs_only,
            )
            seen_keys: set[tuple[str, str, str, str, str]] = set()
            for e in existing_edges:
                fr, tr = _edge_refs(e)
                seen_keys.add(_dedup_key(fr, tr, e.relation))

            for ce in conc_entries:
                if ce.new_ref is None or ce.new_doc_id is None:
                    continue
                is_outgoing = ce.old_doc_id == prov.doc_id
                is_incoming = ce.new_doc_id == prov.doc_id
                if direction == "from" and not is_outgoing:
                    continue
                if direction == "to" and not is_incoming:
                    continue
                if direction == "both" and not (is_outgoing or is_incoming):
                    continue

                key = _dedup_key(ce.old_ref, ce.new_ref, conc_rel)
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                note_parts = [p for p in (ce.substantive_delta, ce.savings_note) if p]
                raw_note = " ".join(note_parts).strip()
                framed_note = (
                    ensure_framed(
                        raw_note,
                        doc_id=f"{ce.old_doc_id}->{ce.new_doc_id}",
                        provenance=ce.provenance or prov.provenance,
                    )
                    if raw_note
                    else None
                )
                extra_conc_links.append(
                    StatutoryLink(
                        from_ref=ce.old_ref,
                        to_ref=ce.new_ref,
                        relation_kind=conc_rel,
                        context_note=framed_note,
                        provenance=ce.provenance or prov.provenance,
                    )
                )

    n_extra = len(extra_conc_links)
    links: list[StatutoryLink] = []
    if resolved_offset < n_extra:
        page_extra = extra_conc_links[resolved_offset : resolved_offset + clamped_limit]
        links.extend(page_extra)
        rem = clamped_limit - len(page_extra)
        edges, edge_total = backend.get_cross_refs(
            prov.doc_id,
            direction=direction,
            relation=relation_kind,
            limit=max(1, rem),
            offset=0,
            commercial_safe_only=cs_only,
        )
        if rem > 0:
            links.extend(_edge_to_link(e) for e in edges[:rem])
    else:
        edges, edge_total = backend.get_cross_refs(
            prov.doc_id,
            direction=direction,
            relation=relation_kind,
            limit=clamped_limit,
            offset=resolved_offset - n_extra,
            commercial_safe_only=cs_only,
        )
        links.extend(_edge_to_link(e) for e in edges)

    total = edge_total + n_extra

    has_more = (resolved_offset + len(links)) < total
    next_cursor = encode_cursor(resolved_offset + len(links)) if has_more else None

    return CrossRefResponse(
        source=source_ref,
        direction=direction,
        total=total,
        returned=len(links),
        next_cursor=next_cursor,
        has_more=has_more,
        links=links,
    )


def register(mcp: Any) -> None:
    @mcp.tool(
        name="cross_reference",
        description=(
            "Return incoming and outgoing statutory cross-references for a section, Constitution "
            "article, schedule, or regulator rule (bidirectional by default). Covers IPC<->BNS / "
            "CrPC<->BNSS / IEA<->BSA replacement links, internal and cross-act citations, amending "
            "provisions, and delegated-legislation links."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
            "title": "Cross-reference a statutory provision",
        },
    )
    @structured_errors
    @run_sync
    def _tool(
        act: str,
        ref: str = "",
        section: str | None = None,
        direction: CrossRefDirection = "both",
        relation_kind: RelationKind | None = None,
        commercial_safe_only: bool = False,
        limit: int = 25,
        offset: int = 0,
        cursor: str | None = None,
    ) -> CrossRefResponse:
        return cross_reference(
            act=act,
            ref=ref,
            section=section,
            direction=direction,
            relation_kind=relation_kind,
            commercial_safe_only=commercial_safe_only,
            limit=limit,
            offset=offset,
            cursor=cursor,
        )
