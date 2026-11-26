"""get_provisions_batch: fetch up to 50 statutory provisions with optional point-in-time reconstruction."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date as Date
from typing import Any

from ..exceptions import InvalidFilter, NyayaError
from ..models import BatchProvisionsResponse, ProvisionResponse, StatutoryLink
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

MAX_BATCH_PROVISIONS = 50


def _normalize_batch_items(
    citations: Sequence[str] | None,
    requests: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for cit in citations or ():
        items.append({"citation": cit, "label": cit})
    for req in requests or ():
        entry = dict(req)
        if "label" not in entry:
            entry["label"] = (
                entry.get("citation")
                or f"{entry.get('act', '')} {entry.get('ref') or entry.get('section') or ''}".strip()
                or repr(entry)
            )
        items.append(entry)
    return items


def get_provisions_batch(
    citations: list[str] | None = None,
    requests: list[dict[str, Any]] | None = None,
    as_of: Date | str | None = None,
    include_links: bool = True,
    commercial_safe_only: bool = False,
) -> BatchProvisionsResponse:
    """Fetch up to 50 statutory provisions in one partial-success call."""
    items = _normalize_batch_items(citations, requests)
    if not items:
        raise InvalidFilter(
            "Provide at least one citation or request item.",
            kind="batch",
            hint="Pass citations=['IPC s.302', 'BNS s.103'] or requests=[{'act': 'IPC', 'ref': '302'}].",
        )
    if len(items) > MAX_BATCH_PROVISIONS:
        raise InvalidFilter(
            f"Batch size {len(items)} exceeds maximum of {MAX_BATCH_PROVISIONS} provisions.",
            kind="batch",
            hint=f"Split the request into batches of at most {MAX_BATCH_PROVISIONS} items.",
        )

    as_of_date = parse_optional_date(as_of, field="as_of")
    backend = get_backend()
    cs_only = effective_commercial_safe(commercial_safe_only)
    alias_map = backend.get_alias_map()

    results: list[dict[str, Any]] = []
    not_found: list[Any] = []
    errors: list[dict[str, Any]] = []

    for item in items:
        label = item.get("label")
        act = str(item.get("act") or "")
        raw_ref = str(item.get("ref") or item.get("section") or "")
        cit = item.get("citation")
        jurisdiction = item.get("jurisdiction")

        resolved_act, resolved_ref, sub_ref = resolve_act_and_ref(
            act=act,
            ref=raw_ref,
            citation=str(cit) if cit is not None else None,
            alias_map=alias_map,
        )
        if not resolved_act and not resolved_ref:
            errors.append(
                {
                    "input": label,
                    "code": "invalid_filter",
                    "message": "Could not resolve act and provision reference.",
                }
            )
            continue
        if not resolved_act:
            resolved_act = "Constitution"

        try:
            rec = backend.get_provision(
                resolved_act,
                resolved_ref,
                jurisdiction=str(jurisdiction) if jurisdiction else None,
                include_text=True,
                include_links=include_links,
                commercial_safe_only=cs_only,
            )
        except NyayaError as exc:
            err_payload = exc.to_dict()
            err_payload["input"] = label
            errors.append(err_payload)
            continue

        if rec is None:
            not_found.append(label)
            continue

        req_sub = rec.metadata.get("requested_sub_ref") or sub_ref
        sub_found = rec.metadata.get("sub_ref_found")
        if req_sub and sub_found is None:
            sub_found = str(req_sub) in rec.text

        raw_text = rec.text
        validity = rec.validity
        pit_payload: dict[str, Any] | None = None

        if as_of_date is not None:
            raw_text, validity, pit_payload = apply_point_in_time(
                rec,
                backend,
                as_of_date,
                commercial_safe_only=cs_only,
            )

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

        prov_resp = ProvisionResponse(
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
        )
        item_dict = prov_resp.model_dump(mode="json")
        if pit_payload is not None:
            item_dict["point_in_time"] = pit_payload
        results.append(item_dict)

    return BatchProvisionsResponse(
        total_requested=len(items),
        returned=len(results),
        as_of=as_of_date,
        results=results,
        not_found=not_found,
        errors=errors,
    )


def register(mcp: Any) -> None:
    @mcp.tool(
        name="get_provisions_batch",
        description=(
            "Fetch up to 50 statutory provisions in a single partial-success batch call, "
            "with optional point-in-time reconstruction via `as_of` (YYYY-MM-DD) and OWASP "
            "<corpus_text> framing on every text field."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
            "title": "Batch fetch statutory provisions",
        },
    )
    @structured_errors
    @run_sync
    def _tool(
        citations: list[str] | None = None,
        requests: list[dict[str, Any]] | None = None,
        as_of: str | None = None,
        include_links: bool = True,
        commercial_safe_only: bool = False,
    ) -> BatchProvisionsResponse:
        return get_provisions_batch(
            citations=citations,
            requests=requests,
            as_of=as_of,
            include_links=include_links,
            commercial_safe_only=commercial_safe_only,
        )
