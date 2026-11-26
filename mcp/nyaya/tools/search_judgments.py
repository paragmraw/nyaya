"""search_judgments: multi-facet judgment search across Supreme Court, High Courts, Tribunals, and District Courts."""

from __future__ import annotations

from datetime import date as Date
from typing import Any, Literal

from ..models import (
    CourtTier,
    JudgmentCard,
    JudgmentSearchQuery,
    JudgmentSearchResponse,
)
from ..sanitize import check_publication_ban
from ..storage import get_retrieval
from ._error import structured_errors
from ._util import (
    effective_commercial_safe,
    ensure_framed,
    parse_optional_date,
    run_sync,
    validate_query_length,
)

SortBy = Literal[
    "authority_relevance",
    "relevance",
    "date_desc",
    "date_asc",
    "bench_strength_desc",
    "most_cited",
]


def search_judgments(
    query: str | None = None,
    query_mode: Literal["auto", "bm25", "metadata"] = "auto",
    court_tier: CourtTier | None = None,
    court: str | list[str] | None = None,
    bench: str | None = None,
    binding_for_court: str | None = None,
    min_bench_strength: int | None = None,
    judge: str | None = None,
    author_judge: str | None = None,
    party: str | None = None,
    act: str | None = None,
    section: str | None = None,
    include_equivalent_provisions: bool = True,
    disposal_nature: str | None = None,
    case_type: str | None = None,
    date_from: Date | str | None = None,
    date_to: Date | str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    reportable_only: bool = False,
    sort_by: SortBy = "authority_relevance",
    commercial_safe_only: bool = False,
    limit: int = 10,
    offset: int = 0,
    cursor: str | None = None,
) -> JudgmentSearchResponse:
    """Search Indian judgments across SC, 25 High Courts, 24 Tribunals & Consumer Commissions, and DDL District Courts."""
    if query is not None and query.strip():
        validate_query_length(query)
        clean_query: str | None = query.strip()
    else:
        clean_query = None

    cs_only = effective_commercial_safe(commercial_safe_only)
    clamped_limit = max(1, min(int(limit), 25))
    clamped_offset = max(0, int(offset))
    parsed_date_from = parse_optional_date(date_from, field="date_from")
    parsed_date_to = parse_optional_date(date_to, field="date_to")

    q = JudgmentSearchQuery(
        query=clean_query,
        query_mode=query_mode,
        court_tier=court_tier,
        court=court,
        bench=bench.strip() if bench and bench.strip() else None,
        binding_for_court=binding_for_court.strip() if binding_for_court and binding_for_court.strip() else None,
        min_bench_strength=min_bench_strength,
        judge=judge.strip() if judge and judge.strip() else None,
        author_judge=author_judge.strip() if author_judge and author_judge.strip() else None,
        party=party.strip() if party and party.strip() else None,
        act=act.strip() if act and act.strip() else None,
        section=section.strip() if section and section.strip() else None,
        include_equivalent_provisions=bool(include_equivalent_provisions),
        disposal_nature=disposal_nature.strip() if disposal_nature and disposal_nature.strip() else None,
        case_type=case_type.strip() if case_type and case_type.strip() else None,
        date_from=parsed_date_from,
        date_to=parsed_date_to,
        year_from=year_from,
        year_to=year_to,
        reportable_only=bool(reportable_only),
        sort_by=sort_by,
        commercial_safe_only=cs_only,
        limit=clamped_limit,
        offset=clamped_offset,
        cursor=cursor,
    )
    resp = get_retrieval().search_judgments(q)

    framed_cards: list[JudgmentCard] = []
    for card in resp.results:
        raw_snip = (card.snippet or "")[:450]
        if raw_snip:
            ban = check_publication_ban(raw_snip)
            if ban.blocked:
                continue
            framed_snip: str | None = ensure_framed(
                raw_snip,
                doc_id=card.case_id,
                provenance=card.provenance,
            )
        else:
            framed_snip = None
        framed_cards.append(card.model_copy(update={"snippet": framed_snip}))

    return resp.model_copy(
        update={
            "results": framed_cards,
            "returned": len(framed_cards),
        }
    )


def register(mcp: Any) -> None:
    @mcp.tool(
        name="search_judgments",
        description=(
            "Multi-facet search across Indian Supreme Court (38k+), 25 High Courts (20.7M+), "
            "24 Tribunals & Consumer Commissions (NCLT, NCLAT, NCDRC, ITAT, NGT, SAT, etc.), and DDL District Courts (81M metadata). "
            "Supports authority-weighted sorting for a target forum via binding_for_court, automatic "
            "IPC<->BNS / CrPC<->BNSS / IEA<->BSA pari-materia expansion via include_equivalent_provisions=True, "
            "bench-strength filtering, and commercial_safe_only enforcement."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
            "title": "Search Indian court & tribunal judgments",
        },
    )
    @structured_errors
    @run_sync
    def _tool(
        query: str | None = None,
        query_mode: Literal["auto", "bm25", "metadata"] = "auto",
        court_tier: CourtTier | None = None,
        court: str | list[str] | None = None,
        bench: str | None = None,
        binding_for_court: str | None = None,
        min_bench_strength: int | None = None,
        judge: str | None = None,
        author_judge: str | None = None,
        party: str | None = None,
        act: str | None = None,
        section: str | None = None,
        include_equivalent_provisions: bool = True,
        disposal_nature: str | None = None,
        case_type: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        reportable_only: bool = False,
        sort_by: SortBy = "authority_relevance",
        commercial_safe_only: bool = False,
        limit: int = 10,
        offset: int = 0,
        cursor: str | None = None,
    ) -> JudgmentSearchResponse:
        return search_judgments(
            query=query,
            query_mode=query_mode,
            court_tier=court_tier,
            court=court,
            bench=bench,
            binding_for_court=binding_for_court,
            min_bench_strength=min_bench_strength,
            judge=judge,
            author_judge=author_judge,
            party=party,
            act=act,
            section=section,
            include_equivalent_provisions=include_equivalent_provisions,
            disposal_nature=disposal_nature,
            case_type=case_type,
            date_from=date_from,
            date_to=date_to,
            year_from=year_from,
            year_to=year_to,
            reportable_only=reportable_only,
            sort_by=sort_by,
            commercial_safe_only=commercial_safe_only,
            limit=limit,
            offset=offset,
            cursor=cursor,
        )
