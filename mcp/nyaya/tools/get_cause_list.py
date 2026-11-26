"""get_cause_list: search local CauseListStore or BYO live docket adapter."""

from __future__ import annotations

import threading
from datetime import date as Date
from typing import Any, Literal

from ..config import get_settings
from ..models import CauseListResponse, SourceProvenance
from ..storage import get_backend
from ..storage.cause_list_store import CauseListEntry, CauseListStore
from ..storage.live_docket import get_live_docket_adapter
from ._error import structured_errors
from ._util import ensure_framed, parse_optional_date, run_sync

_store_lock = threading.Lock()
_cause_list_store: CauseListStore | None = None


def set_cause_list_store(store: CauseListStore | None) -> None:
    """Override or clear the process-wide CauseListStore (primarily for tests)."""
    global _cause_list_store
    with _store_lock:
        _cause_list_store = store


def get_cause_list_store() -> CauseListStore | None:
    """Return any explicitly configured CauseListStore override."""
    with _store_lock:
        return _cause_list_store


def _entry_provenance(entry: CauseListEntry, *, source_mode: str, fallback_date: Date | None) -> SourceProvenance:
    as_of = fallback_date
    if as_of is None and entry.cause_list_date:
        try:
            as_of = Date.fromisoformat(entry.cause_list_date[:10])
        except ValueError:
            as_of = None
    return SourceProvenance(
        source_id=f"cause_list_{source_mode}",
        source_name=f"Official Cause List ({entry.court or 'Court'})",
        source_url=entry.source_url or "https://www.sci.gov.in",
        license_spdx="IN-S52-1-q",
        commercial_safe=True,
        terms_status="cleared",
        as_of=as_of or Date.today(),
    )


def _frame_entry(entry: CauseListEntry, *, source_mode: str, fallback_date: Date | None) -> CauseListEntry:
    prov = _entry_provenance(entry, source_mode=source_mode, fallback_date=fallback_date)
    doc_base = f"causelist:{entry.court}:{entry.cause_list_date}:{entry.case_number or entry.item_number or 'item'}"

    framed_parties = (
        ensure_framed(entry.parties, doc_id=f"{doc_base}:parties", provenance=prov)
        if entry.parties
        else None
    )
    framed_purpose = (
        ensure_framed(entry.purpose, doc_id=f"{doc_base}:purpose", provenance=prov)
        if entry.purpose
        else None
    )
    framed_bench = (
        ensure_framed(entry.bench, doc_id=f"{doc_base}:bench", provenance=prov)
        if entry.bench
        else None
    )
    framed_judge = (
        ensure_framed(entry.judge, doc_id=f"{doc_base}:judge", provenance=prov)
        if entry.judge
        else None
    )
    framed_advocates = [
        ensure_framed(adv, doc_id=f"{doc_base}:adv{idx}", provenance=prov)
        for idx, adv in enumerate(entry.advocates)
        if adv
    ]

    return entry.model_copy(
        update={
            "parties": framed_parties,
            "purpose": framed_purpose,
            "bench": framed_bench,
            "judge": framed_judge,
            "advocates": framed_advocates,
        }
    )


def get_cause_list(
    court: str | None = None,
    date: Date | str | None = None,
    bench: str | None = None,
    judge: str | None = None,
    party: str | None = None,
    advocate: str | None = None,
    case_number: str | None = None,
    cnr: str | None = None,
    live: bool = False,
    limit: int = 25,
) -> CauseListResponse:
    """Search Supreme Court / High Court daily cause lists locally or via BYO live docket adapter."""
    parsed_date = parse_optional_date(date, field="date")
    date_str = parsed_date.isoformat() if parsed_date else None
    clamped_limit = max(1, min(int(limit), 100))

    raw_entries: list[CauseListEntry]
    source_mode: Literal["local", "live"]

    if live:
        adapter = get_live_docket_adapter()
        raw_entries = adapter.search_cause_lists(
            court=court,
            date=date_str,
            bench=bench,
            judge=judge,
            party=party,
            advocate=advocate,
            case_number=case_number,
            cnr=cnr,
            limit=clamped_limit,
        )
        source_mode = "live"
    else:
        source_mode = "local"
        override_store = get_cause_list_store()
        if override_store is not None:
            raw_entries = override_store.search_cause_lists(
                court=court,
                date=date_str,
                bench=bench,
                judge=judge,
                party=party,
                advocate=advocate,
                case_number=case_number,
                cnr=cnr,
                limit=clamped_limit,
            )
        else:
            backend = get_backend()
            search_fn = getattr(backend, "search_cause_lists", None)
            if callable(search_fn):
                raw_entries = list(
                    search_fn(
                        court=court,
                        date=date_str,
                        bench=bench,
                        judge=judge,
                        party=party,
                        advocate=advocate,
                        case_number=case_number,
                        cnr=cnr,
                        limit=clamped_limit,
                    )
                )
            else:
                store = CauseListStore(data_dir=get_settings().data_dir)
                raw_entries = store.search_cause_lists(
                    court=court,
                    date=date_str,
                    bench=bench,
                    judge=judge,
                    party=party,
                    advocate=advocate,
                    case_number=case_number,
                    cnr=cnr,
                    limit=clamped_limit,
                )

    framed = [
        _frame_entry(e, source_mode=source_mode, fallback_date=parsed_date)
        for e in raw_entries[:clamped_limit]
    ]
    return CauseListResponse(
        total=len(framed),
        returned=len(framed),
        source=source_mode,
        entries=framed,
    )


def register(mcp: Any) -> None:
    @mcp.tool(
        name="get_cause_list",
        description=(
            "Search daily cause lists across Supreme Court and High Court feeds by court, date "
            "(YYYY-MM-DD), bench, judge, party, advocate, case_number, or CNR. Set live=True to "
            "query a configured BYO official/licensed live docket endpoint."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
            "title": "Search court daily cause lists",
        },
    )
    @structured_errors
    @run_sync
    def _tool(
        court: str | None = None,
        date: str | None = None,
        bench: str | None = None,
        judge: str | None = None,
        party: str | None = None,
        advocate: str | None = None,
        case_number: str | None = None,
        cnr: str | None = None,
        live: bool = False,
        limit: int = 25,
    ) -> CauseListResponse:
        return get_cause_list(
            court=court,
            date=date,
            bench=bench,
            judge=judge,
            party=party,
            advocate=advocate,
            case_number=case_number,
            cnr=cnr,
            live=live,
            limit=limit,
        )
