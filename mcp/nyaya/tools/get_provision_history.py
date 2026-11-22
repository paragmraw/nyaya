"""get_provision_history: Constitutional Amendments 1-106, statutory amendments, and judicial invalidations."""

from __future__ import annotations

from typing import Any

from ..exceptions import NotFound
from ..models import AmendmentRecord, ProvisionHistoryResponse
from ..retrieval import decode_cursor, encode_cursor
from ..storage import get_backend
from ._error import structured_errors
from ._util import effective_commercial_safe, ensure_framed, run_sync


def get_provision_history(
    act: str = "Constitution",
    ref: str | None = None,
    amendment_number: int | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    commercial_safe_only: bool = False,
    limit: int = 50,
    offset: int = 0,
    cursor: str | None = None,
) -> ProvisionHistoryResponse:
    """Return chronological amendment history and judicial invalidation events for an Act or provision."""
    backend = get_backend()
    cs_only = effective_commercial_safe(commercial_safe_only)
    clamped_limit = max(1, min(int(limit), 106))
    resolved_offset = decode_cursor(cursor, offset)

    hist = backend.get_provision_history(
        act=act,
        ref=ref.strip() if ref and ref.strip() else None,
        amendment_number=amendment_number,
        year_from=year_from,
        year_to=year_to,
        limit=clamped_limit,
        offset=resolved_offset,
        commercial_safe_only=cs_only,
    )
    if amendment_number is not None and not hist.amendments:
        raise NotFound(
            f"Amendment {amendment_number} for {act!r} not found in corpus.",
            kind="amendment",
            hint="Constitutional amendments are numbered 1 through 106.",
        )

    framed_amendments: list[AmendmentRecord] = []
    for am in hist.amendments:
        framed_amendments.append(
            am.model_copy(
                update={
                    "summary": ensure_framed(
                        am.summary,
                        doc_id=am.amendment_id,
                        provenance=am.provenance,
                    )
                }
            )
        )

    has_more = (resolved_offset + len(framed_amendments)) < hist.total
    next_cursor = encode_cursor(resolved_offset + len(framed_amendments)) if has_more else None

    return ProvisionHistoryResponse(
        act=hist.act,
        ref=hist.ref,
        total=hist.total,
        returned=len(framed_amendments),
        next_cursor=next_cursor,
        has_more=has_more,
        amendments=framed_amendments,
        judicial_validity_events=hist.judicial_validity_events,
    )


def register(mcp: Any) -> None:
    @mcp.tool(
        name="get_provision_history",
        description=(
            "Retrieve the chronological legislative amendment history (including verified "
            "Constitutional Amendments 1st through 106th) and Supreme Court judicial invalidation "
            "events (struck_down, read_down, partially_struck_down, kept_in_abeyance) for an Act "
            "or specific provision."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
            "title": "Get provision amendment & judicial validity history",
        },
    )
    @structured_errors
    @run_sync
    def _tool(
        act: str = "Constitution",
        ref: str | None = None,
        amendment_number: int | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        commercial_safe_only: bool = False,
        limit: int = 50,
        offset: int = 0,
        cursor: str | None = None,
    ) -> ProvisionHistoryResponse:
        return get_provision_history(
            act=act,
            ref=ref,
            amendment_number=amendment_number,
            year_from=year_from,
            year_to=year_to,
            commercial_safe_only=commercial_safe_only,
            limit=limit,
            offset=offset,
            cursor=cursor,
        )
