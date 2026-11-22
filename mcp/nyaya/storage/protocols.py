"""Protocol contracts for Nyaya v2.0 zero-Supabase storage and retrieval."""

from __future__ import annotations

from datetime import date as Date
from typing import Any, Literal, Protocol, runtime_checkable

from ..models import (
    AnalyticsResult,
    AnalyticsTier,
    BrowseResult,
    CitationEdgeRecord,
    ConcordanceEntry,
    CorpusStatusResponse,
    CourtInfo,
    CrossRefDirection,
    JudgmentContent,
    JudgmentMeta,
    JudgmentSearchQuery,
    JudgmentSearchResponse,
    PrecedentTrace,
    ProvisionHistory,
    ProvisionRecord,
    StatuteSearchQuery,
    StatuteSearchResponse,
    TraceDirection,
)


@runtime_checkable
class CorpusBackend(Protocol):
    """Exact lookups, statutory trees, concordance, validity, citation graph, provenance."""

    def get_provision(
        self,
        act: str,
        ref: str,
        *,
        jurisdiction: str | None = None,
        include_text: bool = True,
        include_links: bool = True,
        commercial_safe_only: bool | None = None,
    ) -> ProvisionRecord | None: ...

    def browse_statute(
        self,
        act: str | None = None,
        *,
        query: str | None = None,
        jurisdiction: str | None = None,
        category: str | None = None,
        status: str | None = None,
        chapter: str | int | None = None,
        part: str | None = None,
        ref_from: str | None = None,
        ref_to: str | None = None,
        kind: str | None = None,
        include_text: bool = False,
        limit: int = 50,
        offset: int = 0,
        commercial_safe_only: bool | None = None,
    ) -> BrowseResult: ...

    def compare_provisions(
        self,
        act: str,
        ref: str,
        *,
        commercial_safe_only: bool | None = None,
    ) -> list[ConcordanceEntry]: ...

    def get_provision_history(
        self,
        act: str,
        ref: str | None = None,
        *,
        amendment_number: int | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        limit: int = 50,
        offset: int = 0,
        commercial_safe_only: bool | None = None,
    ) -> ProvisionHistory: ...

    def get_cross_refs(
        self,
        doc_id: str,
        *,
        direction: CrossRefDirection = "both",
        relation: str | None = None,
        limit: int = 50,
        offset: int = 0,
        commercial_safe_only: bool | None = None,
    ) -> tuple[list[CitationEdgeRecord], int]: ...

    def resolve_judgment_id(
        self,
        identifier: str,
        *,
        court: str | None = None,
        year: int | None = None,
        commercial_safe_only: bool | None = None,
    ) -> list[JudgmentMeta]: ...

    def trace_precedent(
        self,
        case_id: str,
        *,
        direction: TraceDirection = "cited_by",
        court: str | None = None,
        min_bench_strength: int | None = None,
        larger_bench_only: bool = False,
        date_from: Date | None = None,
        limit: int = 25,
        offset: int = 0,
        commercial_safe_only: bool | None = None,
    ) -> PrecedentTrace: ...

    def list_courts(self, query: str | None = None) -> list[CourtInfo]: ...

    def get_alias_map(self) -> dict[str, str]: ...

    def corpus_status(
        self,
        *,
        court: str | None = None,
        include_sources: bool = True,
        commercial_safe_only: bool | None = None,
    ) -> CorpusStatusResponse: ...

    def close(self) -> None: ...


@runtime_checkable
class RetrievalEngine(Protocol):
    """Pluggable search contract: Phase 1 = pure lexical+metadata; Phase 2 swaps hybrid+rerank."""

    def search_statutes(self, q: StatuteSearchQuery) -> StatuteSearchResponse: ...

    def search_judgments(self, q: JudgmentSearchQuery) -> JudgmentSearchResponse: ...

    def health(self) -> dict[str, Any]: ...


@runtime_checkable
class PdfFetcher(Protocol):
    """On-demand direct S3 PDF fetch, extraction, quality gate, windowing & search_within."""

    async def fetch_and_read(
        self,
        meta: JudgmentMeta,
        *,
        mode: Literal["overview", "paragraphs", "pages", "search_within", "docket"] = "overview",
        para_start: int | None = None,
        para_end: int | None = None,
        page_start: int | None = None,
        page_end: int | None = None,
        query: str | None = None,
        max_chars: int = 24_000,
    ) -> JudgmentContent: ...

    async def aclose(self) -> None: ...


@runtime_checkable
class AnalyticsEngine(Protocol):
    """Safe bounded Parquet aggregations over SC, HC, District, Tribunal, and Regulator data."""

    def aggregate(
        self,
        dataset: AnalyticsTier,
        group_by: str,
        *,
        filters: dict[str, Any],
        top_n: int = 25,
        sample_cases: int = 0,
        commercial_safe_only: bool | None = None,
    ) -> AnalyticsResult: ...

    def close(self) -> None: ...
