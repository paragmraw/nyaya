"""get_provision: fetch verbatim statutory text, validity envelope, and top cross-links."""

from __future__ import annotations

from datetime import date as Date
from typing import Any

from ..exceptions import InvalidFilter, NotFound
from ..models import PointInTimeMeta, ProvisionResponse, StatutoryLink
from ..storage import get_backend
from ._error import structured_errors
from ._util import (
    apply_point_in_time,
    effective_commercial_safe,
    ensure_framed,
    parse_optional_date,
    resolve_act_and_ref,
    run_sync,
)


def get_provision(
    act: str = "",
    ref: str = "",
    citation: str | None = None,
    section: str | None = None,
    jurisdiction: str | None = None,
    as_of: Date | str | None = None,
    include_links: bool = True,
    commercial_safe_only: bool = False,
) -> ProvisionResponse:
    """Fetch a single statutory section, Constitution article/schedule, CPC order/rule, or regulation."""
    as_of_date = parse_optional_date(as_of, field="as_of")
    backend = get_backend()
    cs_only = effective_commercial_safe(commercial_safe_only)
    raw_ref = ref or (section or "")
    alias_map = backend.get_alias_map()

    resolved_act, resolved_ref, sub_ref = resolve_act_and_ref(
        act=act,
        ref=raw_ref,
        citation=citation,
        alias_map=alias_map,
    )
    if not resolved_act and not resolved_ref:
        raise InvalidFilter(
            "Provide act and ref (or a combined citation string such as 'IPC s.302' or 'Article 21').",
            kind="provision",
            hint="Example: get_provision(citation='IPC s.302') or get_provision(act='BNS', ref='103').",
        )
    if not resolved_act:
        resolved_act = "Constitution"

    rec = backend.get_provision(
        resolved_act,
        resolved_ref,
        jurisdiction=jurisdiction,
        include_text=True,
        include_links=include_links,
        commercial_safe_only=cs_only,
    )
    if rec is None:
        raise NotFound(
            f"Provision {resolved_ref!r} of {resolved_act!r} not found in corpus.",
            kind="provision",
            hint="Use search_statutes or browse_statute to locate the act and provision reference.",
        )

    req_sub = rec.metadata.get("requested_sub_ref") or sub_ref
    sub_found = rec.metadata.get("sub_ref_found")
    if req_sub and sub_found is None:
        sub_found = str(req_sub) in rec.text

    raw_text = rec.text
    validity = rec.validity
    as_of_meta: PointInTimeMeta | None = None
    if as_of_date is not None:
        raw_text, validity, pit_payload = apply_point_in_time(
            rec,
            backend,
            as_of_date,
            commercial_safe_only=cs_only,
        )
        as_of_meta = PointInTimeMeta.model_validate(pit_payload)

    framed_links: list[StatutoryLink] = []
    for link in rec.top_links[:15]:
        note = link.context_note
        if note:
            note = ensure_framed(
                note,
                doc_id=f"{link.from_ref.act}:{link.from_ref.ref}->{link.to_ref.act}:{link.to_ref.ref}",
                provenance=link.provenance or rec.provenance,
            )
        framed_links.append(link.model_copy(update={"context_note": note}))

    return ProvisionResponse(
        act=rec.act,
        act_full_name=rec.act_full_name,
        jurisdiction=rec.jurisdiction,
        kind=rec.kind,
        ref=rec.ref,
        requested_sub_ref=req_sub,
        sub_ref_found=sub_found,
        title=rec.title,
        chapter_num=rec.chapter_num,
        chapter_title=rec.chapter_title,
        part=rec.part,
        text=ensure_framed(raw_text, doc_id=rec.doc_id, provenance=rec.provenance),
        validity=validity,
        top_links=framed_links,
        provenance=rec.provenance,
        as_of_meta=as_of_meta,
    )


def register(mcp: Any) -> None:
    @mcp.tool(
        name="get_provision",
        description=(
            "Fetch the verbatim text of a statutory section, Constitution article, schedule, "
            "CPC Order/Rule, or regulator clause along with its StatutoryValidity envelope "
            "(in_force / repealed / struck_down / read_down / kept_in_abeyance, replacement pointers, "
            "BNSS s.531 transitional note, and invalidating Supreme Court judgment) and up to 15 "
            "cross-reference links. Supports optional point-in-time reconstruction via as_of='YYYY-MM-DD'. "
            "Accepts either (act, ref) or a combined citation string "
            "(e.g. 'IPC s.302', 'Art. 21A', 'CPC O.7 R.11', 'Schedule 7 List I Entry 82')."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
            "title": "Get verbatim statutory provision & validity envelope",
        },
    )
    @structured_errors
    @run_sync
    def _tool(
        act: str = "",
        ref: str = "",
        citation: str | None = None,
        section: str | None = None,
        jurisdiction: str | None = None,
        as_of: str | None = None,
        include_links: bool = True,
        commercial_safe_only: bool = False,
    ) -> ProvisionResponse:
        return get_provision(
            act=act,
            ref=ref,
            citation=citation,
            section=section,
            jurisdiction=jurisdiction,
            as_of=as_of,
            include_links=include_links,
            commercial_safe_only=commercial_safe_only,
        )
