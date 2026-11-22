"""judgment_analytics: closed-schema DuckDB Parquet aggregations across court tiers."""

from __future__ import annotations

from datetime import date as Date
from typing import Any, Literal

from ..exceptions import CommercialLicenseBlocked
from ..models import (
    AnalyticsBucket,
    AnalyticsTier,
    JudgmentAnalyticsResponse,
    JudgmentCard,
)
from ..storage import get_analytics
from ._error import structured_errors
from ._util import (
    effective_commercial_safe,
    ensure_framed,
    parse_optional_date,
    run_sync,
)

GroupByField = Literal[
    "court",
    "court_tier",
    "bench",
    "year",
    "disposal_nature",
    "act",
    "case_type",
    "judge",
    "state",
    "district",
    "regulator",
]


def judgment_analytics(
    group_by: GroupByField,
    court_tier: AnalyticsTier = "high_court",
    court: str | list[str] | None = None,
    bench: str | None = None,
    state: str | None = None,
    district: str | None = None,
    judge: str | None = None,
    act: str | None = None,
    section: str | None = None,
    disposal_nature: str | None = None,
    date_from: Date | str | None = None,
    date_to: Date | str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    top_k: int = 25,
    sample_cases: int = 0,
    commercial_safe_only: bool = False,
) -> JudgmentAnalyticsResponse:
    """Run closed-schema partition-guarded aggregations over compacted ZSTD Parquet datasets."""
    cs_only = effective_commercial_safe(commercial_safe_only)
    if cs_only and court_tier == "district_court":
        raise CommercialLicenseBlocked(
            "District Court analytics (DDL 81M, CC-BY-NC-SA-4.0) is blocked when commercial_safe_only=True.",
            license_spdx="CC-BY-NC-SA-4.0",
            hint="Use court_tier='high_court', 'supreme_court', or 'tribunal' in commercial-safe mode.",
        )

    parsed_from = parse_optional_date(date_from, field="date_from")
    parsed_to = parse_optional_date(date_to, field="date_to")
    eff_year_from = year_from or (parsed_from.year if parsed_from else None)
    eff_year_to = year_to or (parsed_to.year if parsed_to else None)
    clamped_top_k = max(1, min(int(top_k), 100))
    clamped_samples = max(0, min(int(sample_cases), 5))

    filters: dict[str, Any] = {
        "court": court,
        "bench": bench.strip() if bench and bench.strip() else None,
        "state": state.strip() if state and state.strip() else None,
        "district": district.strip() if district and district.strip() else None,
        "judge": judge.strip() if judge and judge.strip() else None,
        "act": act.strip() if act and act.strip() else None,
        "section": section.strip() if section and section.strip() else None,
        "disposal_nature": disposal_nature.strip() if disposal_nature and disposal_nature.strip() else None,
        "year_from": eff_year_from,
        "year_to": eff_year_to,
    }

    res = get_analytics().aggregate(
        dataset=court_tier,
        group_by=group_by,
        filters=filters,
        top_n=clamped_top_k,
        sample_cases=clamped_samples,
        commercial_safe_only=cs_only,
    )

    framed_buckets: list[AnalyticsBucket] = []
    for b in res.buckets:
        samples: list[JudgmentCard] = []
        for s in b.samples:
            snip = (
                ensure_framed(s.snippet, doc_id=s.case_id, provenance=s.provenance)
                if s.snippet
                else None
            )
            samples.append(s.model_copy(update={"snippet": snip}))
        framed_buckets.append(b.model_copy(update={"samples": samples}))

    caveat = res.completeness_caveat or (
        "eCourts disposal labels vary across High Courts; pre-2015 act tags may be incomplete; "
        "DDL District dataset covers 2010-2018 only (CC-BY-NC-SA-4.0)."
    )

    return JudgmentAnalyticsResponse(
        group_by=group_by,
        court_tier=court_tier,
        total_matched=res.total_matched,
        buckets=framed_buckets,
        completeness_caveat=caveat,
        provenances=res.provenances,
    )


def register(mcp: Any) -> None:
    @mcp.tool(
        name="judgment_analytics",
        description=(
            "Run closed-schema DuckDB aggregations over compacted ZSTD Parquet partitions "
            "(Supreme Court, 17.77M High Court, 81M DDL District Court, 15 Tribunals, 12 Regulators). "
            "Requires partition narrowing (e.g. court or <=5-year window on High Court; state or <=5-year "
            "window on District Court) to stay within the 256 MB / 5s execution budget."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
            "title": "Run judicial & regulatory Parquet analytics",
        },
    )
    @structured_errors
    @run_sync
    def _tool(
        group_by: GroupByField,
        court_tier: AnalyticsTier = "high_court",
        court: str | list[str] | None = None,
        bench: str | None = None,
        state: str | None = None,
        district: str | None = None,
        judge: str | None = None,
        act: str | None = None,
        section: str | None = None,
        disposal_nature: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        top_k: int = 25,
        sample_cases: int = 0,
        commercial_safe_only: bool = False,
    ) -> JudgmentAnalyticsResponse:
        return judgment_analytics(
            group_by=group_by,
            court_tier=court_tier,
            court=court,
            bench=bench,
            state=state,
            district=district,
            judge=judge,
            act=act,
            section=section,
            disposal_nature=disposal_nature,
            date_from=date_from,
            date_to=date_to,
            year_from=year_from,
            year_to=year_to,
            top_k=top_k,
            sample_cases=sample_cases,
            commercial_safe_only=commercial_safe_only,
        )
