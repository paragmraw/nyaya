"""cross_reference: bidirectional statutory and judicial citation cross-references."""

from __future__ import annotations

from typing import Any, Literal

from ..exceptions import NotFound
from ..models import CrossRefDirection, CrossRefResponse, ProvisionRef, StatutoryLink
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

    edges, total = backend.get_cross_refs(
        prov.doc_id,
        direction=direction,
        relation=relation_kind,
        limit=clamped_limit,
        offset=resolved_offset,
        commercial_safe_only=cs_only,
    )

    source_ref = ProvisionRef(
        act=prov.act,
        jurisdiction=prov.jurisdiction,
        ref=prov.ref,
        title=prov.title,
        kind=prov.kind,
    )

    links: list[StatutoryLink] = []
    for e in edges:
        from_r = e.from_ref or (source_ref if e.from_id == prov.doc_id else ProvisionRef(act=prov.act, ref=e.from_id))
        to_r = e.to_ref or (source_ref if e.to_id == prov.doc_id else ProvisionRef(act=prov.act, ref=e.to_id))
        note = e.quoting_excerpt
        if note:
            note = ensure_framed(
                note,
                doc_id=f"{e.from_id}->{e.to_id}",
                provenance=e.provenance or prov.provenance,
            )
        links.append(
            StatutoryLink(
                from_ref=from_r,
                to_ref=to_r,
                relation_kind=e.relation,
                context_note=note,
                provenance=e.provenance or prov.provenance,
            )
        )

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
