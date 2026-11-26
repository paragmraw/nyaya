"""Canonical Pydantic v2 models, enums, query schemas, and tool responses for Nyaya MCP v2.0."""

from __future__ import annotations

from datetime import date as Date
from typing import Any, Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Canonical Cross-Module Enums (Single Source of Truth)
# ---------------------------------------------------------------------------

TermsStatus = Literal[
    "cleared",
    "attribution_required",
    "non_commercial_only",
    "upstream_unknown",
]

CourtTier = Literal[
    "supreme_court",
    "high_court",
    "tribunal",
    "district_court",
    "all",
]

AnalyticsTier = Literal[
    "supreme_court",
    "high_court",
    "tribunal",
    "district_court",
    "regulator",
    "all",
]

ProvisionKind = Literal[
    "section",
    "article",
    "schedule",
    "amendment",
    "order_rule",
    "preamble",
    "regulation",
    "circular",
    "rule",
    "cad_debate",
]

StatuteStatus = Literal[
    "in_force",
    "repealed",
    "omitted",
    "struck_down",
    "read_down",
    "partially_struck_down",
    "kept_in_abeyance",
    "substituted",
    "unverified",
]

ActStatus = Literal[
    "in_force",
    "repealed",
    "partially_repealed",
    "not_yet_commenced",
    "superseded",
]

MappingType = Literal[
    "one_to_one",
    "one_to_many",
    "many_to_one",
    "omitted",
    "newly_inserted",
]

ComparisonMappingType = Literal[
    "one_to_one",
    "one_to_many",
    "many_to_one",
    "omitted",
    "newly_inserted",
    "manual_comparison",
]

TraceDirection = Literal["cited_by", "cites", "both"]
CrossRefDirection = Literal["from", "to", "both"]

AuthorityLevel = Literal[
    "binding_sc",
    "binding_home_hc",
    "persuasive_other_hc",
    "tribunal",
    "district",
    "unspecified",
]

TextAvailability = Literal[
    "indexed_bm25",
    "on_demand_s3_pdf",
    "metadata_only",
]

AuditStatus = Literal[
    "VERIFIED",
    "VERIFIED_REPEALED",
    "VERIFIED_STRUCK_DOWN_OR_READ_DOWN",
    "NOT_FOUND_IN_CORPUS",
    "INVALID_STATUTORY_REF",
    "AMBIGUOUS",
    "QUOTE_VERIFIED",
    "QUOTE_MISMATCH",
    "COMMERCIAL_LICENSE_BLOCKED",
]

# ---------------------------------------------------------------------------
# Provenance, Statutory Validity & Graph Links
# ---------------------------------------------------------------------------


class SourceProvenance(BaseModel):
    source_id: str
    source_name: str
    source_url: str
    license_spdx: str
    commercial_safe: bool
    terms_status: TermsStatus
    as_of: Date
    content_sha256: str | None = None


class ProvisionRef(BaseModel):
    act: str
    jurisdiction: str = "Central"
    ref: str
    title: str | None = None
    kind: ProvisionKind = "section"


class InvalidatingJudgment(BaseModel):
    citation: str
    neutral_citation: str | None = None
    case_id: str | None = None
    case_name: str
    decision_date: Date | None = None
    coram_size: int | None = None
    effect: Literal["struck_down", "read_down", "partially_struck_down", "kept_in_abeyance"]
    holding_summary: str


class StatutoryValidity(BaseModel):
    status: StatuteStatus = "in_force"
    commencement_date: Date | None = None
    repeal_date: Date | None = None
    replaced_by: list[ProvisionRef] = Field(default_factory=list)
    replaces: list[ProvisionRef] = Field(default_factory=list)
    invalidating_judgment: InvalidatingJudgment | None = None
    transitional_note: str | None = None
    amendment_count: int = 0
    verified_as_of: Date | None = None


class StatutoryLink(BaseModel):
    from_ref: ProvisionRef
    to_ref: ProvisionRef
    relation_kind: str
    context_note: str | None = None
    provenance: SourceProvenance | None = None


# ---------------------------------------------------------------------------
# Backend Storage Records
# ---------------------------------------------------------------------------


class ActInfo(BaseModel):
    act_id: str
    short_name: str
    full_name: str
    year: int | None = None
    act_number: str | None = None
    jurisdiction: str = "Central"
    category: str = "central_act"
    status: ActStatus = "in_force"
    commencement_date: Date | None = None
    repeal_date: Date | None = None
    replaced_by_act: str | None = None
    storage_tier: Literal["sqlite_core", "lance_bucket"] = "sqlite_core"
    provision_count: int = 0
    provenance: SourceProvenance


class ActSummary(BaseModel):
    short_name: str
    full_name: str
    jurisdiction: str = "Central"
    category: str = "central_act"
    year: int | None = None
    status: ActStatus = "in_force"
    provision_count: int = 0
    provenance: SourceProvenance


class ProvisionRecord(BaseModel):
    doc_id: str
    act_id: str
    act: str
    act_full_name: str
    jurisdiction: str = "Central"
    kind: ProvisionKind = "section"
    ref: str
    ref_norm: str
    chapter_num: str | None = None
    chapter_title: str | None = None
    part: str | None = None
    title: str | None = None
    text: str
    validity: StatutoryValidity
    top_links: list[StatutoryLink] = Field(default_factory=list)
    provenance: SourceProvenance
    text_sha256: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class TocEntry(BaseModel):
    chapter_num: str | None = None
    chapter_title: str | None = None
    part: str | None = None
    kind: ProvisionKind | None = None
    provision_count: int = 0
    ref_start: str | None = None
    ref_end: str | None = None


class ProvisionIndexEntry(BaseModel):
    doc_id: str
    act: str
    jurisdiction: str = "Central"
    kind: ProvisionKind = "section"
    ref: str
    title: str | None = None
    status: StatuteStatus = "in_force"
    snippet: str | None = None
    provenance: SourceProvenance | None = None


class BrowseResult(BaseModel):
    mode: Literal["catalog", "toc", "provisions"]
    act_info: ActInfo | None = None
    acts: list[ActInfo] = Field(default_factory=list)
    chapters_or_parts: list[TocEntry] = Field(default_factory=list)
    provisions: list[ProvisionIndexEntry] = Field(default_factory=list)
    total: int = 0
    returned: int = 0


class OffenceClassification(BaseModel):
    cognizable: bool | None = None
    bailable: bool | None = None
    punishment: str | None = None
    triable_by: str | None = None
    compoundable: bool | str | None = None


class ConcordanceEntry(BaseModel):
    old_doc_id: str
    old_ref: ProvisionRef
    new_doc_id: str | None = None
    new_ref: ProvisionRef | None = None
    mapping_type: MappingType
    substantive_delta: str
    punishment_delta: str | None = None
    procedural_delta: str | None = None
    savings_note: str | None = None
    verified_by: str
    provenance: SourceProvenance
    offence_classification: OffenceClassification | dict[str, Any] | None = None


class AmendmentRecord(BaseModel):
    amendment_id: str
    number: int | None = None
    title: str
    act: str
    year: int | None = None
    enactment_date: Date | None = None
    effective_date: Date | None = None
    action: str = "amended"
    provisions_affected: list[str] = Field(default_factory=list)
    summary: str
    provenance: SourceProvenance


class ProvisionHistory(BaseModel):
    act: str
    ref: str | None = None
    amendments: list[AmendmentRecord] = Field(default_factory=list)
    judicial_validity_events: list[InvalidatingJudgment] = Field(default_factory=list)
    total: int = 0


class JudgmentMeta(BaseModel):
    case_id: str
    court_tier: CourtTier
    court_code: str
    court_name: str
    bench: str | None = None
    cnr: str | None = None
    neutral_citation: str | None = None
    air_citation: str | None = None
    scc_citation: str | None = None
    scr_citation: str | None = None
    reporter_citations: list[str] = Field(default_factory=list)
    title: str
    petitioner: str | None = None
    respondent: str | None = None
    decision_date: Date | None = None
    year: int | None = None
    coram: list[str] = Field(default_factory=list)
    author_judge: str | None = None
    bench_strength: int | None = None
    disposal_nature: str | None = None
    case_type: str | None = None
    acts_cited: list[str] = Field(default_factory=list)
    is_reportable: bool = False
    pdf_s3_bucket: str | None = None
    pdf_s3_key: str | None = None
    source_url: str | None = None
    has_lance_text: bool = False
    cited_by_count: int = 0
    text_sha256: str = ""
    provenance: SourceProvenance
    snippet: str | None = None
    docket_extra: dict[str, Any] = Field(default_factory=dict)


class JudgmentCard(BaseModel):
    case_id: str
    cnr: str | None = None
    neutral_citation: str | None = None
    reporter_citations: list[str] = Field(default_factory=list)
    title: str
    court: str
    court_code: str | None = None
    court_tier: CourtTier
    bench: str | None = None
    decision_date: Date | None = None
    coram: list[str] = Field(default_factory=list)
    bench_strength: int | None = None
    author_judge: str | None = None
    disposal_nature: str | None = None
    case_type: str | None = None
    authority_level: AuthorityLevel = "unspecified"
    acts_cited: list[str] = Field(default_factory=list)
    matched_via: Literal["direct", "pari_materia_equivalent", "metadata"] = "direct"
    cited_by_count: int | None = None
    snippet: str | None = None
    text_availability: TextAvailability = "metadata_only"
    provenance: SourceProvenance


class JudgmentExcerpt(BaseModel):
    para_num: int | None = None
    page_num: int | None = None
    text: str
    score: float | None = None


class CaseDocket(BaseModel):
    cnr: str | None = None
    case_number: str | None = None
    case_type: str | None = None
    filing_date: Date | None = None
    registration_date: Date | None = None
    decision_date: Date | None = None
    disposal_nature: str | None = None
    petitioner: str | None = None
    respondent: str | None = None
    petitioner_advocates: list[str] = Field(default_factory=list)
    respondent_advocates: list[str] = Field(default_factory=list)
    acts_sections: list[str] = Field(default_factory=list)
    hearing_count: int | None = None
    sibling_order_ids: list[str] = Field(default_factory=list)


class JudgmentContent(BaseModel):
    case_id: str
    mode: Literal["overview", "paragraphs", "pages", "search_within", "docket"]
    extraction_quality: Literal["verified", "scanned_image_only", "truncated", "metadata_only"]
    extraction_method: Literal["indexed_text", "pypdfium2_s3_pdf", "metadata_only"]
    excerpts: list[JudgmentExcerpt] = Field(default_factory=list)
    total_paragraphs: int | None = None
    total_pages: int | None = None
    chars_per_page: float | None = None
    has_more: bool = False
    next_para: int | None = None
    next_page: int | None = None
    pdf_url: str | None = None
    warning: str | None = None
    content_sha256: str | None = None


class CitationEdgeRecord(BaseModel):
    from_id: str
    from_kind: Literal["provision", "judgment"]
    to_id: str
    to_kind: Literal["provision", "judgment"]
    relation: str
    para_ref: str | None = None
    quoting_excerpt: str | None = None
    from_year: int | None = None
    from_bench_strength: int | None = None
    precedent_status: Literal["verified_curated", "unverified_automated"] = "unverified_automated"
    from_ref: ProvisionRef | None = None
    to_ref: ProvisionRef | None = None
    related_case: JudgmentCard | None = None
    direction: Literal["cited_by", "cites", "from", "to"] | None = None
    bench_comparison: Literal[
        "larger_bench",
        "co_equal_bench",
        "smaller_bench",
        "higher_court",
        "lower_court",
        "unknown",
    ] = "unknown"
    citing_para_num: int | None = None
    provenance: SourceProvenance | None = None


class PrecedentTrace(BaseModel):
    target: JudgmentCard
    precedent_status: Literal["unverified_automated"] = "unverified_automated"
    verification_notice: str
    cited_by_total: int = 0
    cites_total: int = 0
    cited_by_larger_bench_count: int = 0
    edges: list[CitationEdgeRecord] = Field(default_factory=list)
    total: int = 0


class CourtInfo(BaseModel):
    court_code: str
    court_name: str
    tier: Literal["sc", "hc", "district", "tribunal", "regulator"]
    bench_code: str | None = None
    bench_name: str | None = None
    state_ut: str
    seat_city: str | None = None
    hc_jurisdiction: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Typed Search Queries & Tool Responses
# ---------------------------------------------------------------------------


class StatuteSearchQuery(BaseModel):
    query: str
    act: str | None = None
    jurisdiction: str | None = None
    kind: ProvisionKind | None = None
    category: str | None = None
    status: Literal["in_force", "repealed", "all"] = "all"
    query_mode: Literal["bm25", "exact_phrase"] = "bm25"
    promote_definitions: bool = False
    commercial_safe_only: bool | None = None
    limit: int = 10
    offset: int = 0
    cursor: str | None = None


class JudgmentSearchQuery(BaseModel):
    query: str | None = None
    query_mode: Literal["auto", "bm25", "metadata"] = "auto"
    court_tier: CourtTier | None = None
    court: str | list[str] | None = None
    bench: str | None = None
    binding_for_court: str | None = None
    min_bench_strength: int | None = None
    judge: str | None = None
    author_judge: str | None = None
    party: str | None = None
    act: str | None = None
    section: str | None = None
    include_equivalent_provisions: bool = True
    disposal_nature: str | None = None
    case_type: str | None = None
    date_from: Date | None = None
    date_to: Date | None = None
    year_from: int | None = None
    year_to: int | None = None
    reportable_only: bool = False
    sort_by: Literal[
        "authority_relevance",
        "relevance",
        "date_desc",
        "date_asc",
        "bench_strength_desc",
        "most_cited",
    ] = "authority_relevance"
    commercial_safe_only: bool | None = None
    limit: int = 10
    offset: int = 0
    cursor: str | None = None


class StatuteHit(BaseModel):
    doc_id: str
    act: str
    jurisdiction: str = "Central"
    kind: ProvisionKind = "section"
    ref: str
    title: str | None = None
    status: StatuteStatus = "in_force"
    replaced_by: list[ProvisionRef] = Field(default_factory=list)
    snippet: str
    bm25_score: float = 0.0
    provenance: SourceProvenance


class StatuteSearchResponse(BaseModel):
    query: str
    query_mode: Literal["bm25", "exact_phrase"] = "bm25"
    total: int
    returned: int
    next_cursor: str | None = None
    has_more: bool = False
    results: list[StatuteHit] = Field(default_factory=list)
    degraded_sources: list[str] = Field(default_factory=list)


class JudgmentSearchResponse(BaseModel):
    query: str | None = None
    total: int | None = None
    total_is_estimate: bool = False
    returned: int = 0
    next_cursor: str | None = None
    has_more: bool = False
    expanded_provisions: list[str] = Field(default_factory=list)
    results: list[JudgmentCard] = Field(default_factory=list)
    coverage_note: str = ""
    degraded_sources: list[str] = Field(default_factory=list)


class PointInTimeMeta(BaseModel):
    as_of: Date
    effective_status: str
    applied_amendments: list[dict[str, Any]] = Field(default_factory=list)
    active_invalidation: InvalidatingJudgment | dict[str, Any] | None = None
    reconstruction_basis: str


class ProvisionResponse(BaseModel):
    act: str
    act_full_name: str
    jurisdiction: str = "Central"
    kind: ProvisionKind = "section"
    ref: str
    requested_sub_ref: str | None = None
    sub_ref_found: bool | None = None
    title: str | None = None
    chapter_num: str | None = None
    chapter_title: str | None = None
    part: str | None = None
    text: str
    validity: StatutoryValidity
    top_links: list[StatutoryLink] = Field(default_factory=list)
    provenance: SourceProvenance
    as_of_meta: dict[str, Any] | PointInTimeMeta | None = None


class StatuteBrowseResponse(BaseModel):
    mode: Literal["catalog", "toc", "provisions"]
    total: int
    returned: int
    next_cursor: str | None = None
    has_more: bool = False
    acts: list[ActSummary] = Field(default_factory=list)
    act_info: ActSummary | None = None
    chapters_or_parts: list[TocEntry] = Field(default_factory=list)
    provisions: list[ProvisionIndexEntry] = Field(default_factory=list)


class ClauseDiff(BaseModel):
    op: Literal["added", "removed", "modified"]
    source_excerpt: str | None = None
    target_excerpt: str | None = None


class ProvisionComparisonResponse(BaseModel):
    mapping_type: ComparisonMappingType
    source_provision: ProvisionResponse
    target_provisions: list[ProvisionResponse] = Field(default_factory=list)
    diff_summary: str | None = None
    clause_diffs: list[ClauseDiff] = Field(default_factory=list)
    punishment_delta: str | None = None
    procedural_delta: str | None = None
    transitional_rule: str | None = None
    provenance: SourceProvenance
    offence_classification: OffenceClassification | dict[str, Any] | None = None


class ProvisionHistoryResponse(BaseModel):
    act: str
    ref: str | None = None
    total: int
    returned: int
    next_cursor: str | None = None
    has_more: bool = False
    amendments: list[AmendmentRecord] = Field(default_factory=list)
    judicial_validity_events: list[InvalidatingJudgment] = Field(default_factory=list)


class CrossRefResponse(BaseModel):
    source: ProvisionRef
    direction: CrossRefDirection
    total: int
    returned: int
    next_cursor: str | None = None
    has_more: bool = False
    links: list[StatutoryLink] = Field(default_factory=list)


class JudgmentViewResponse(BaseModel):
    card: JudgmentCard
    view: Literal["overview", "paragraphs", "pages", "search_within", "docket"]
    excerpts: list[JudgmentExcerpt] = Field(default_factory=list)
    docket: CaseDocket | None = None
    total_paragraphs: int | None = None
    total_pages: int | None = None
    has_more: bool = False
    next_para: int | None = None
    next_page: int | None = None
    extraction_method: Literal["indexed_text", "pypdfium2_s3_pdf", "metadata_only"] = (
        "metadata_only"
    )
    chars_per_page: float | None = None
    warning: str | None = None
    provenance: SourceProvenance


class PrecedentEdge(BaseModel):
    direction: Literal["cited_by", "cites"]
    related_case: JudgmentCard
    bench_comparison: Literal[
        "larger_bench",
        "co_equal_bench",
        "smaller_bench",
        "higher_court",
        "lower_court",
        "unknown",
    ] = "unknown"
    citing_para_num: int | None = None
    quoting_excerpt: str | None = None
    provenance: SourceProvenance


class PrecedentTraceResponse(BaseModel):
    target: JudgmentCard
    precedent_status: Literal["unverified_automated"] = "unverified_automated"
    verification_notice: str
    cited_by_total: int = 0
    cites_total: int = 0
    cited_by_larger_bench_count: int = 0
    edges: list[PrecedentEdge] = Field(default_factory=list)
    total: int = 0
    returned: int = 0
    next_cursor: str | None = None
    has_more: bool = False


class AnalyticsBucket(BaseModel):
    key: str
    count: int
    share_pct: float
    samples: list[JudgmentCard] = Field(default_factory=list)


class AnalyticsResult(BaseModel):
    dataset: str
    group_by: str
    total_matched: int
    buckets: list[AnalyticsBucket] = Field(default_factory=list)
    completeness_caveat: str = ""
    provenances: list[SourceProvenance] = Field(default_factory=list)


class JudgmentAnalyticsResponse(BaseModel):
    group_by: str
    court_tier: AnalyticsTier
    total_matched: int
    buckets: list[AnalyticsBucket] = Field(default_factory=list)
    completeness_caveat: str
    provenances: list[SourceProvenance] = Field(default_factory=list)


class QuoteCheckInput(BaseModel):
    citation: str
    quote: str


class AuditFinding(BaseModel):
    raw_citation: str
    span: tuple[int, int] | None = None
    parsed: dict[str, Any] | None = None
    status: AuditStatus
    canonical_record: dict[str, Any] | None = None
    validity: StatutoryValidity | None = None
    cited_by_count: int | None = None
    cited_by_larger_bench_count: int | None = None
    matched_para_num: int | None = None
    matched_excerpt: str | None = None
    candidates: list[dict[str, Any]] = Field(default_factory=list)
    coverage_note: str = ""
    provenance: SourceProvenance | None = None


class CitationAuditResponse(BaseModel):
    checked_as_of: Date
    precedent_status: Literal["unverified_automated"] = "unverified_automated"
    summary: dict[str, int] = Field(default_factory=dict)
    findings: list[AuditFinding] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Shared Corpus Status Model (MCP corpus_status + REST /api/corpus-status)
# ---------------------------------------------------------------------------


class CorpusCounts(BaseModel):
    legislation_jurisdictions: int = 0
    central_acts: int = 0
    state_acts: int = 0
    regulators: int = 0
    tribunals: int = 0
    provisions: int = 0
    articles: int = 0
    schedules: int = 0
    amendments: int = 0
    concordance_mappings: int = 0
    judicial_invalidations: int = 0
    sc_judgments: int = 0
    hc_judgments: int = 0
    tribunal_orders: int = 0
    district_court_records: int = 0
    citation_edges: int = 0


class SourceStatusEntry(BaseModel):
    source_id: str
    record_count: int = 0
    year_coverage: str | None = None
    text_availability: str = "indexed_bm25"
    provenance: SourceProvenance


class CorpusStatusResponse(BaseModel):
    service_version: str = "2.0.0"
    as_of: Date | None = None
    retrieval_mode: Literal["lexical_metadata_graph"] = "lexical_metadata_graph"
    commercial_safe_locked: bool = False
    tiers: dict[str, str] = Field(default_factory=dict)
    counts: CorpusCounts = Field(default_factory=CorpusCounts)
    sources: list[SourceStatusEntry] = Field(default_factory=list)
    licensing: dict[str, Any] = Field(default_factory=dict)
    court_detail: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Batch, Cause-List, and Live Docket Models (v2.1)
# ---------------------------------------------------------------------------


class BatchProvisionResponse(BaseModel):
    """Partial-success response for ``get_provisions_batch``."""

    total_requested: int
    returned: int
    as_of: Date | None = None
    results: list[dict[str, Any]] = Field(default_factory=list)
    not_found: list[Any] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)


BatchProvisionsResponse = BatchProvisionResponse


class BatchJudgmentResponse(BaseModel):
    """Partial-success response for ``get_judgments_batch``."""

    total_requested: int
    returned: int
    view: str
    results: list[JudgmentViewResponse] = Field(default_factory=list)
    not_found: list[str] = Field(default_factory=list)
    ambiguous: list[dict[str, Any]] = Field(default_factory=list)
    errors: list[dict[str, Any]] = Field(default_factory=list)


BatchJudgmentsResponse = BatchJudgmentResponse


class CauseListEntry(BaseModel):
    """Structured daily cause-list item from an official Supreme Court or High Court feed."""

    court: str
    cause_list_date: str
    bench: str | None = None
    judge: str | None = None
    item_number: str | None = None
    case_number: str | None = None
    cnr: str | None = None
    parties: str | None = None
    advocates: list[str] = Field(default_factory=list)
    purpose: str | None = None
    source_url: str = ""


class CauseListResponse(BaseModel):
    """Structured response for ``get_cause_list``."""

    total: int
    returned: int
    source: Literal["local", "live"]
    entries: list[CauseListEntry] = Field(default_factory=list)


class LiveCaseStatus(BaseModel):
    """Structured live docket status snapshot returned by a BYO live docket adapter."""

    case_number: str | None = None
    cnr: str | None = None
    court: str
    status: str
    next_hearing_date: Date | None = None
    last_listed_date: Date | None = None
    bench: str | None = None
    purpose: str | None = None
    source_url: str = ""
    provenance: SourceProvenance | None = None
