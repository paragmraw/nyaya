"""Single source of truth for the chat agent's v2 tool allowlist and schemas.

Every consumer derives from this module:

* ``native.py`` builds the LangChain ``StructuredTool`` objects (name,
  description, args schema) straight from :data:`TOOL_SPECS`.
* ``mcp_fallback.py`` filters the MCP-over-HTTP tools by
  :data:`TOOL_SPECS` names, so both paths expose the SAME interface.
* ``prompts.py`` renders the agent prompt's tool list from the specs, killing
  the hand-maintained duplicate that used to live in the prompt text.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Args schemas (Pydantic models for all 12 MCP v2 tools)
# ---------------------------------------------------------------------------


class SearchStatutesInput(BaseModel):
    query: str = Field(description="Free-text or lexical BM25 query over Indian statutes, Constitution, and regulations.")
    act: str | None = Field(default=None, description="Optional act short_name to scope (e.g. 'IPC', 'BNS', 'Constitution', 'CPC', 'SEBI-LODR').")
    jurisdiction: str | None = Field(default=None, description="Optional jurisdiction: 'Central', state/UT code, or regulator ('SEBI', 'RBI').")
    kind: str | None = Field(default=None, description="Optional filter: 'section', 'article', 'schedule', 'amendment', 'regulation', 'circular', or 'rule'.")
    status: str = Field(default="all", description="Filter by statutory status: 'in_force', 'repealed', or 'all'.")
    query_mode: str = Field(default="bm25", description="'bm25' (default) or 'exact_phrase'.")
    promote_definitions: bool = Field(default=False, description="Boost provisions whose title contains 'definition' or 'interpretation'.")
    commercial_safe_only: bool = Field(default=False, description="When True, exclude non-commercial datasets.")
    limit: int = Field(default=10, description="Max results to return (1-25).")
    cursor: str | None = Field(default=None, description="Opaque pagination cursor.")


class GetProvisionInput(BaseModel):
    act: str = Field(default="", description="Act short name or alias (e.g. 'IPC', 'BNS', 'Constitution', 'CPC', 'EvidenceAct', 'SEBI-LODR').")
    ref: str = Field(default="", description="Section, article, schedule, or order/rule number (e.g. '302', '21A', 'Order 7 Rule 11', 'Schedule 7').")
    citation: str | None = Field(default=None, description="Combined citation string (e.g. 'IPC s.302', 'Art. 21A', 'CPC O.7 R.11', 'IEA s.65B(4)').")
    jurisdiction: str | None = Field(default=None, description="Optional jurisdiction disambiguator for State/UT acts or regulators.")
    include_links: bool = Field(default=True, description="Include up to 15 compact incoming/outgoing statutory cross-links.")
    commercial_safe_only: bool = Field(default=False, description="When True, block non-commercial records.")


class CompareProvisionsInput(BaseModel):
    source: str = Field(description="Source provision citation (e.g. 'IPC s.302', 'CrPC s.438', 'IEA s.65B', 'BNS s.103').")
    target: str | None = Field(default=None, description="Optional target provision citation. When omitted, auto-resolves IPC<->BNS / CrPC<->BNSS / IEA<->BSA concordance.")
    include_text: bool = Field(default=True, description="Include full verbatim provision text for both source and target.")
    commercial_safe_only: bool = Field(default=False, description="When True, block non-commercial records.")


class BrowseStatuteInput(BaseModel):
    act: str | None = Field(default=None, description="Act short_name (None lists/searches the act & regulator catalog; set to browse TOC or provisions).")
    query: str | None = Field(default=None, description="Title/alias substring filter when browsing the catalog (act=None).")
    jurisdiction: str | None = Field(default=None, description="'Central', State/UT code, or Regulator code.")
    category: str | None = Field(default=None, description="Optional category filter ('constitution', 'central_act', 'state_act', 'regulator', etc.).")
    status: str = Field(default="all", description="'in_force', 'repealed', or 'all'.")
    chapter: int | str | None = Field(default=None, description="Filter to a specific chapter number/roman numeral.")
    part: str | None = Field(default=None, description="Filter to a specific part (e.g. 'Part III').")
    kind: str | None = Field(default=None, description="Filter by provision kind ('section', 'article', 'schedule', 'regulation').")
    ref_from: str | None = Field(default=None, description="Starting section/article number for range browse.")
    ref_to: str | None = Field(default=None, description="Ending section/article number for range browse.")
    include_snippet: bool = Field(default=False, description="Include a <=300-char text snippet per provision.")
    commercial_safe_only: bool = Field(default=False, description="When True, exclude non-commercial datasets.")
    limit: int = Field(default=50, description="Max entries to return (1-200).")
    cursor: str | None = Field(default=None, description="Opaque pagination cursor.")


class GetProvisionHistoryInput(BaseModel):
    act: str = Field(default="Constitution", description="Act short_name (default 'Constitution').")
    ref: str | None = Field(default=None, description="Optional article/section ref to filter chronological amendments and judicial invalidations.")
    amendment_number: int | None = Field(default=None, description="Exact Constitutional Amendment number (1..106).")
    year_from: int | None = Field(default=None, description="Filter amendments enacted on or after this year.")
    year_to: int | None = Field(default=None, description="Filter amendments enacted on or before this year.")
    commercial_safe_only: bool = Field(default=False, description="When True, exclude non-commercial records.")
    limit: int = Field(default=50, description="Max records to return (1-106).")
    cursor: str | None = Field(default=None, description="Opaque pagination cursor.")


class SearchJudgmentsInput(BaseModel):
    query: str | None = Field(default=None, description="Lexical BM25 or citation query over SC, High Court, Tribunal, and District judgments.")
    query_mode: str = Field(default="auto", description="'auto', 'bm25', or 'metadata'.")
    court_tier: str | None = Field(default=None, description="Optional tier filter: 'supreme_court', 'high_court', 'tribunal', 'district_court', or 'all'.")
    court: str | list[str] | None = Field(default=None, description="Canonical court code(s) or name(s): 'SC', 'DHC', 'BHC', 'NCLAT', 'ITAT', etc.")
    bench: str | None = Field(default=None, description="Optional bench name (e.g. 'nagpur', 'lucknow', 'principal').")
    binding_for_court: str | None = Field(default=None, description="Target forum court for authority-weighted ranking (binding SC & home HC first).")
    min_bench_strength: int | None = Field(default=None, description="Minimum coram size (e.g. 2 for Division Bench, 5 for Constitution Bench).")
    judge: str | None = Field(default=None, description="Filter by judge on the coram.")
    author_judge: str | None = Field(default=None, description="Filter by judgment author.")
    party: str | None = Field(default=None, description="Petitioner or respondent name substring.")
    act: str | None = Field(default=None, description="Filter by cited statute short_name (e.g. 'IPC', 'BNS', 'CrPC').")
    section: str | None = Field(default=None, description="Filter by cited section/article ref.")
    include_equivalent_provisions: bool = Field(default=True, description="Auto-expand IPC<->BNS, CrPC<->BNSS, IEA<->BSA pari-materia provisions.")
    disposal_nature: str | None = Field(default=None, description="Filter by disposal outcome (e.g. 'Allowed', 'Dismissed').")
    case_type: str | None = Field(default=None, description="Filter by case type.")
    date_from: str | None = Field(default=None, description="Start decision date (YYYY-MM-DD).")
    date_to: str | None = Field(default=None, description="End decision date (YYYY-MM-DD).")
    sort_by: str = Field(default="authority_relevance", description="'authority_relevance', 'relevance', 'date_desc', 'date_asc', 'bench_strength_desc', or 'most_cited'.")
    commercial_safe_only: bool = Field(default=False, description="When True, exclude non-commercial DDL District & ILDC records.")
    limit: int = Field(default=10, description="Max results to return (1-25).")
    cursor: str | None = Field(default=None, description="Opaque pagination cursor.")


class GetJudgmentInput(BaseModel):
    identifier: str = Field(description="CNR ('MHAU019999992015'), Neutral Citation ('2017 INSC 801', '2024:DHC:1234'), AIR/SCC/SCR ('AIR 1973 SC 1461'), case_id, or case name.")
    view: str = Field(default="overview", description="'overview' (default), 'paragraphs', 'pages', 'search_within', or 'docket'.")
    para_start: int | None = Field(default=None, description="1-based start paragraph for view='paragraphs' (max window 30 paras).")
    para_end: int | None = Field(default=None, description="1-based end paragraph for view='paragraphs'.")
    page_start: int | None = Field(default=None, description="1-based start page for view='pages' (max window 10 pages).")
    page_end: int | None = Field(default=None, description="1-based end page for view='pages'.")
    within_query: str | None = Field(default=None, description="Required when view='search_within' (runs ephemeral BM25 over the judgment's paragraphs).")
    commercial_safe_only: bool = Field(default=False, description="When True, block non-commercial records.")


class TracePrecedentInput(BaseModel):
    identifier: str = Field(description="Target judgment CNR, Neutral Citation, AIR/SCC/SCR, or case_id.")
    direction: str = Field(default="cited_by", description="'cited_by' (default), 'cites', or 'both'.")
    court: str | None = Field(default=None, description="Optional court code filter on citing/cited cases.")
    min_bench_strength: int | None = Field(default=None, description="Minimum coram size on related cases.")
    larger_bench_only: bool = Field(default=False, description="When True, restrict to citing benches larger than the target judgment's coram.")
    date_from: str | None = Field(default=None, description="Optional decision date lower bound (YYYY-MM-DD).")
    commercial_safe_only: bool = Field(default=False, description="When True, exclude non-commercial records.")
    limit: int = Field(default=15, description="Max citation edges to return (1-25).")
    cursor: str | None = Field(default=None, description="Opaque pagination cursor.")


class CrossReferenceInput(BaseModel):
    act: str = Field(description="Act short_name (e.g. 'IPC', 'BNS', 'Constitution').")
    ref: str = Field(description="Section, article, schedule, or rule ref.")
    direction: str = Field(default="both", description="'both', 'from', or 'to'.")
    relation_kind: str | None = Field(default=None, description="Optional relation filter ('replaced_by', 'repeals', 'references', 'corresponds_to', 'amends', 'delegates_to').")
    commercial_safe_only: bool = Field(default=False, description="When True, exclude non-commercial records.")
    limit: int = Field(default=25, description="Max links to return (1-100).")
    cursor: str | None = Field(default=None, description="Opaque pagination cursor.")


class VerifyCitationsInput(BaseModel):
    text: str | None = Field(default=None, description="Raw draft or brief text (up to 50,000 chars) from which to extract and verify citations.")
    citations: list[str] | None = Field(default=None, description="Explicit list of up to 50 citation strings to audit.")
    check_date: str | None = Field(default=None, description="Optional date (YYYY-MM-DD) as of which to evaluate statutory validity.")
    commercial_safe_only: bool = Field(default=False, description="When True, block non-commercial records.")


class JudgmentAnalyticsInput(BaseModel):
    group_by: str = Field(description="Aggregation dimension: 'court', 'court_tier', 'bench', 'year', 'disposal_nature', 'act', 'case_type', 'state', or 'district'.")
    court_tier: str = Field(default="high_court", description="'supreme_court', 'high_court', 'tribunal', 'district_court', or 'all'.")
    court: str | list[str] | None = Field(default=None, description="Court code(s) filter.")
    bench: str | None = Field(default=None, description="Bench filter.")
    state: str | None = Field(default=None, description="State filter for District Courts.")
    district: str | None = Field(default=None, description="District filter.")
    judge: str | None = Field(default=None, description="Judge filter.")
    act: str | None = Field(default=None, description="Act filter.")
    section: str | None = Field(default=None, description="Section filter.")
    disposal_nature: str | None = Field(default=None, description="Disposal nature filter.")
    date_from: str | None = Field(default=None, description="Start date (YYYY-MM-DD).")
    date_to: str | None = Field(default=None, description="End date (YYYY-MM-DD).")
    top_k: int = Field(default=25, description="Max buckets (1-100).")
    sample_cases: int = Field(default=0, description="Sample JudgmentCards per bucket (0-5).")
    commercial_safe_only: bool = Field(default=False, description="When True, block non-commercial datasets.")


class CorpusStatusInput(BaseModel):
    court: str | None = Field(default=None, description="Optional court/tribunal/regulator code for drill-down.")
    include_sources: bool = Field(default=True, description="Include per-source provenance and coverage matrix.")
    commercial_safe_only: bool = Field(default=False, description="When True, report only commercial-safe datasets.")


TOOL_SCHEMAS: dict[str, type[BaseModel]] = {
    "search_statutes": SearchStatutesInput,
    "get_provision": GetProvisionInput,
    "compare_provisions": CompareProvisionsInput,
    "browse_statute": BrowseStatuteInput,
    "get_provision_history": GetProvisionHistoryInput,
    "cross_reference": CrossReferenceInput,
    "search_judgments": SearchJudgmentsInput,
    "get_judgment": GetJudgmentInput,
    "trace_precedent": TracePrecedentInput,
    "verify_citations": VerifyCitationsInput,
    "judgment_analytics": JudgmentAnalyticsInput,
    "corpus_status": CorpusStatusInput,
}


# ---------------------------------------------------------------------------
# Tool specs (8 allowlisted v2 tools for the Nemotron chat loop)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolSpec:
    """One corpus tool as the chat agent sees it."""

    name: str
    description: str
    args_model: type[BaseModel]
    list_result: bool = False


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="search_statutes",
        description=(
            "Lexical BM25 and exact-phrase search over Indian Central Acts, 36 State/UT "
            "legislation packs, the Constitution of India (articles & schedules), and 12 "
            "regulators (SEBI, RBI, IRDAI, TRAI, CCI, IBBI, etc.). Returns provisions with "
            "statutory validity status (in_force, repealed, struck_down, read_down, "
            "kept_in_abeyance) and replacement pointers. Set promote_definitions=true for "
            "statutory definitions."
        ),
        args_model=SearchStatutesInput,
        list_result=True,
    ),
    ToolSpec(
        name="get_provision",
        description=(
            "Fetch verbatim text, StatutoryValidity envelope (commencement/repeal dates, "
            "BNS/BNSS/BSA replacements, judicial invalidation holdings, BNSS s.531 savings "
            "notes), and top cross-links for a specific section, Constitution article, "
            "schedule entry, CPC Order/Rule, or regulator clause. Accepts either (act, ref) "
            "or a combined citation string like 'IPC s.302', 'Art. 21A', 'CPC O.7 R.11', "
            "or 'IEA s.65B(4)'."
        ),
        args_model=GetProvisionInput,
        list_result=False,
    ),
    ToolSpec(
        name="compare_provisions",
        description=(
            "Compare old and new criminal/statutory provisions side by side (auto-resolving "
            "IPC<->BNS, CrPC<->BNSS, and IEA<->BSA concordance when target is omitted). "
            "Returns mapping_type, verbatim source and target texts, substantive diff_summary, "
            "punishment_delta, procedural_delta, and BNSS s.531 transitional savings rules."
        ),
        args_model=CompareProvisionsInput,
        list_result=False,
    ),
    ToolSpec(
        name="browse_statute",
        description=(
            "Browse the statute & regulator catalog (when act=None), inspect a statute's "
            "Chapters/Parts table of contents (when act is set without filters), or list "
            "provisions within a chapter, part, or section range."
        ),
        args_model=BrowseStatuteInput,
        list_result=True,
    ),
    ToolSpec(
        name="get_provision_history",
        description=(
            "Retrieve chronological Constitutional Amendments (1st through 106th) or statutory "
            "amendments and audited judicial invalidation events (struck_down, read_down, "
            "kept_in_abeyance) for an act or specific article/section."
        ),
        args_model=GetProvisionHistoryInput,
        list_result=True,
    ),
    ToolSpec(
        name="search_judgments",
        description=(
            "Search judgments across the Supreme Court of India, 25 High Courts, 15 Tribunals "
            "(NCLT, NCLAT, ITAT, NGT, SAT, etc.), and District Court metadata by BM25 query, "
            "citation, court, bench, judge, party, cited act/section (with automatic "
            "IPC<->BNS / CrPC<->BNSS / IEA<->BSA pari-materia expansion), bench strength, "
            "and binding authority for a target forum court."
        ),
        args_model=SearchJudgmentsInput,
        list_result=True,
    ),
    ToolSpec(
        name="get_judgment",
        description=(
            "Fetch a judgment by CNR, Neutral Citation ('2017 INSC 801', '2024:DHC:1234'), "
            "reporter citation ('AIR 1973 SC 1461', '(2017) 10 SCC 1'), case_id, or case name. "
            "Supports windowed views: 'overview' (metadata + opening/operative excerpts), "
            "'paragraphs' (1-based range), 'pages', 'search_within' (BM25 over paragraphs "
            "within the judgment), and 'docket'."
        ),
        args_model=GetJudgmentInput,
        list_result=False,
    ),
    ToolSpec(
        name="trace_precedent",
        description=(
            "Trace incoming ('cited_by') and outgoing ('cites') citation-graph edges for a "
            "judgment, including bench-strength comparisons (larger_bench, co_equal_bench, "
            "smaller_bench) and quoting paragraph excerpts. Always note that automated graph "
            "edges carry precedent_status='unverified_automated'."
        ),
        args_model=TracePrecedentInput,
        list_result=True,
    ),
)

TOOL_NAMES: frozenset[str] = frozenset(s.name for s in TOOL_SPECS)


def spec_for(name: str) -> ToolSpec | None:
    """Return the spec for ``name``, or None when the tool is not allowlisted."""
    for s in TOOL_SPECS:
        if s.name == name:
            return s
    return None


def tool_specs_by_name() -> dict[str, ToolSpec]:
    """A name->spec map for the 8 allowlisted chat tools."""
    return {s.name: s for s in TOOL_SPECS}


def prune_config() -> dict[str, dict[str, Any]]:
    """Synthesis-input pruning config for LIST-type v2 tools (cleaning.py).

    Hit 0 is kept verbatim; hits 1..N keep identification fields (preserving
    all canonical keys required by ``extract_grounded_keys``) plus a 300-char
    snippet.
    """
    return {
        "search_statutes": {
            "list_key": "results",
            "list_keys": ("results",),
            "keep_hit_fields": (
                "act",
                "jurisdiction",
                "kind",
                "ref",
                "title",
                "status",
                "replaced_by",
                "bm25_score",
            ),
            "keep_snippet_field": "snippet",
            "keep_envelope_fields": ("total", "returned", "has_more"),
        },
        "browse_statute": {
            "list_key": "provisions",
            "list_keys": ("provisions", "acts", "chapters_or_parts"),
            "keep_hit_fields": (
                "short_name",
                "full_name",
                "jurisdiction",
                "ref",
                "title",
                "status",
                "number",
                "chapter_num",
                "chapter_title",
                "part",
                "kind",
                "provision_count",
            ),
            "keep_snippet_field": "snippet",
            "keep_envelope_fields": ("mode", "total", "returned", "has_more"),
        },
        "get_provision_history": {
            "list_key": "amendments",
            "list_keys": ("amendments",),
            "keep_hit_fields": (
                "number",
                "title",
                "year",
                "enactment_date",
                "provisions_affected",
            ),
            "keep_snippet_field": "summary",
            "keep_envelope_fields": (
                "act",
                "ref",
                "total",
                "returned",
                "judicial_validity_events",
            ),
        },
        "search_judgments": {
            "list_key": "results",
            "list_keys": ("results",),
            "keep_hit_fields": (
                "case_id",
                "cnr",
                "neutral_citation",
                "reporter_citations",
                "title",
                "court",
                "court_tier",
                "bench",
                "decision_date",
                "bench_strength",
                "disposal_nature",
                "authority_level",
                "text_availability",
            ),
            "keep_snippet_field": "snippet",
            "keep_envelope_fields": (
                "total",
                "total_is_estimate",
                "returned",
                "has_more",
                "coverage_note",
            ),
        },
        "trace_precedent": {
            "list_key": "edges",
            "list_keys": ("edges",),
            "keep_hit_fields": (
                "direction",
                "bench_comparison",
                "citing_para_num",
                "related_case",
            ),
            "keep_snippet_field": "quoting_excerpt",
            "keep_envelope_fields": (
                "target",
                "precedent_status",
                "cited_by_total",
                "cites_total",
                "cited_by_larger_bench_count",
                "verification_notice",
            ),
        },
    }


DEFAULT_TOOLS: tuple[str, ...] = tuple(s.name for s in TOOL_SPECS)
