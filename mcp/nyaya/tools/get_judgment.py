"""get_judgment: windowed judgment reader with direct AWS S3 pypdfium2 extraction and search_within."""

from __future__ import annotations

import asyncio
import re
from datetime import date as Date
from typing import Any, Literal

from ..citations import court_display_name
from ..exceptions import (
    AmbiguousReference,
    InvalidFilter,
    NotFound,
    PdfFetchError,
    StorageUnavailable,
    TextUnavailableMetadataOnly,
)
from ..models import (
    CaseDocket,
    CourtTier,
    JudgmentExcerpt,
    JudgmentMeta,
    JudgmentViewResponse,
    SourceProvenance,
    TermsStatus,
)
from ..retrieval.paragraph_bm25 import ParagraphBM25
from ..sanitize import check_publication_ban
from ..storage import get_analytics, get_backend, get_pdf_fetcher, get_retrieval
from ..storage.lance_store import TRIBUNAL_CHUNKS_TABLE
from ..storage.sqlite_store import SqliteStore
from ._error import structured_errors
from ._util import effective_commercial_safe, ensure_framed, validate_query_length

ViewMode = Literal["overview", "paragraphs", "pages", "search_within", "docket"]
AliasMode = Literal[
    "overview",
    "paragraphs",
    "pages",
    "search_within",
    "docket",
    "summary",
    "full",
    "query_excerpt",
]


_DIGITS_COURT_CODE_RE = re.compile(r"^\d+[_~]\d+$")
_VALID_TERMS_STATUS = frozenset(
    {"cleared", "attribution_required", "non_commercial_only", "upstream_unknown"}
)


def _parse_iso_date(val: Any) -> Date | None:
    raw = str(val or "").strip()[:10]
    if not raw:
        return None
    try:
        return Date.fromisoformat(raw)
    except ValueError:
        return None


def _resolve_court_name(raw_name: Any, court_code: str, *, fallback: str) -> str:
    name = str(raw_name or "").strip()
    if (
        not name
        or name.lower() == court_code.lower()
        or _DIGITS_COURT_CODE_RE.match(name)
    ):
        resolved = court_display_name(court_code) or court_display_name(name)
        if resolved:
            return resolved
    return name or court_code or fallback


def _safe_terms_status(raw: Any, *, commercial_safe: bool) -> TermsStatus:
    val = str(raw or "").strip()
    if val in _VALID_TERMS_STATUS:
        return val  # type: ignore[return-value]
    return "cleared" if commercial_safe else "non_commercial_only"


def _lance_chunks_to_metas(
    table_name: str | None,
    rows: list[dict[str, Any]],
    *,
    fallback_id: str,
) -> list[JudgmentMeta]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        cid = str(r.get("case_id") or fallback_id)
        grouped.setdefault(cid, []).append(r)

    court_tier: CourtTier = (
        "tribunal" if table_name == TRIBUNAL_CHUNKS_TABLE else "high_court"
    )
    metas: list[JudgmentMeta] = []
    for cid, group_rows in grouped.items():
        group_rows.sort(key=lambda r: int(r.get("para_start") or 0))
        first = group_rows[0]
        court_code = str(
            first.get("court_code")
            or first.get("forum_code")
            or ("TRIBUNAL" if court_tier == "tribunal" else "HC")
        )
        court_name = _resolve_court_name(
            first.get("court_name"),
            court_code,
            fallback="Tribunal" if court_tier == "tribunal" else "High Court",
        )
        d_val = _parse_iso_date(first.get("decision_date"))
        yr_raw = first.get("year")
        year = int(yr_raw) if yr_raw is not None else (d_val.year if d_val else None)
        author = str(first.get("author_judge") or first.get("judge") or "").strip() or None
        coram = [
            j.strip()
            for j in str(first.get("judge") or first.get("author_judge") or "").split(";")
            if j.strip()
        ]
        air_cit = str(first.get("air_citation") or "").strip() or None
        scc_cit = str(first.get("scc_citation") or "").strip() or None
        reps = [c for c in (air_cit, scc_cit) if c]
        snippet = (
            "\n\n".join(
                str(r.get("text") or "").strip()
                for r in group_rows
                if str(r.get("text") or "").strip()
            )
            or None
        )
        comm_safe = bool(first.get("commercial_safe", True))
        sha = str(first.get("text_sha256") or "")
        default_src = "vaquill_tribunals" if court_tier == "tribunal" else "dattam-hc"
        default_name = (
            "Vaquill Open India Law Tribunals"
            if court_tier == "tribunal"
            else "Dattam Labs HC Judgments"
        )
        default_url = (
            "https://oss-data-in.vaquill.ai"
            if court_tier == "tribunal"
            else "https://indian-high-court-judgments.s3.ap-south-1.amazonaws.com"
        )
        prov = SourceProvenance(
            source_id=str(first.get("source_id") or default_src),
            source_name=default_name,
            source_url=str(first.get("source_url") or default_url),
            license_spdx=str(first.get("license_spdx") or "CC-BY-4.0"),
            commercial_safe=comm_safe,
            terms_status=_safe_terms_status(
                first.get("terms_status"), commercial_safe=comm_safe
            ),
            as_of=d_val or Date.today(),
            content_sha256=sha or None,
        )
        metas.append(
            JudgmentMeta(
                case_id=cid,
                court_tier=court_tier,
                court_code=court_code,
                court_name=court_name,
                bench=str(first.get("bench") or "").strip() or None,
                cnr=str(first.get("cnr") or "").strip() or None,
                neutral_citation=str(first.get("neutral_citation") or "").strip() or None,
                air_citation=air_cit,
                scc_citation=scc_cit,
                reporter_citations=reps,
                title=str(first.get("title") or "Untitled"),
                decision_date=d_val,
                year=year,
                coram=coram,
                author_judge=author,
                bench_strength=int(first["bench_strength"])
                if first.get("bench_strength") is not None
                else None,
                disposal_nature=str(first.get("disposal_nature") or "").strip() or None,
                case_type=str(first.get("case_type") or "").strip() or None,
                acts_cited=[str(a) for a in (first.get("acts_cited") or []) if a],
                is_reportable=bool(first.get("is_reportable", False)),
                source_url=str(first.get("source_url") or default_url),
                has_lance_text=True,
                text_sha256=sha,
                provenance=prov,
                snippet=snippet,
            )
        )
    return metas


def _duckdb_row_to_meta(
    dataset_name: str | None,
    row: dict[str, Any],
    *,
    analytics: Any,
    fallback_id: str,
) -> JudgmentMeta:
    court_tier: CourtTier = "tribunal" if dataset_name == "tribunal" else "high_court"
    ds_key = dataset_name or court_tier
    court_code = str(
        row.get("court_code")
        or row.get("forum_code")
        or ("TRIBUNAL" if court_tier == "tribunal" else "HC")
    )
    court_name = _resolve_court_name(
        row.get("court_name"),
        court_code,
        fallback="Tribunal" if court_tier == "tribunal" else "High Court",
    )
    d_val = _parse_iso_date(row.get("decision_date"))
    yr_raw = row.get("year")
    year = int(yr_raw) if yr_raw is not None else (d_val.year if d_val else None)
    author = str(row.get("author_judge") or row.get("judge") or "").strip() or None
    coram = [
        j.strip()
        for j in str(row.get("judge") or row.get("author_judge") or "").split(";")
        if j.strip()
    ]
    manifest = getattr(analytics, "manifest", None)
    ds_meta = manifest.datasets.get(ds_key) if manifest is not None else None
    as_of_fn = getattr(analytics, "_manifest_as_of", None)
    m_date = as_of_fn() if callable(as_of_fn) else Date.today()
    comm_safe = ds_meta.commercial_safe if ds_meta else bool(row.get("commercial_safe", True))
    prov = SourceProvenance(
        source_id=f"parquet-{ds_key}",
        source_name=f"Compacted Parquet ({ds_key})",
        source_url="s3://nyaya-bucket/parquet",
        license_spdx=ds_meta.license_spdx if ds_meta else "CC-BY-4.0",
        commercial_safe=comm_safe,
        terms_status=ds_meta.terms_status
        if ds_meta
        else _safe_terms_status(None, commercial_safe=comm_safe),
        as_of=m_date,
    )
    return JudgmentMeta(
        case_id=str(row.get("case_id") or fallback_id),
        court_tier=court_tier,
        court_code=court_code,
        court_name=court_name,
        bench=str(row.get("bench") or "").strip() or None,
        cnr=str(row.get("cnr") or "").strip() or None,
        neutral_citation=str(row.get("neutral_citation") or "").strip() or None,
        title=str(row.get("title") or "Untitled"),
        petitioner=str(row.get("petitioner") or "").strip() or None,
        respondent=str(row.get("respondent") or "").strip() or None,
        decision_date=d_val,
        year=year,
        coram=coram,
        author_judge=author,
        bench_strength=int(row["bench_strength"])
        if row.get("bench_strength") is not None
        else None,
        disposal_nature=str(row.get("disposal_nature") or "").strip() or None,
        case_type=str(row.get("case_type") or "").strip() or None,
        acts_cited=[str(row["act"])] if row.get("act") else [],
        has_lance_text=False,
        provenance=prov,
        snippet=None,
    )


def resolve_judgment_across_tiers(
    ident: str,
    *,
    commercial_safe_only: bool,
) -> list[JudgmentMeta]:
    """Resolve a judgment identifier across SQLite core, LanceDB chunks, and DuckDB Parquet."""
    backend_unavail: StorageUnavailable | None = None
    try:
        metas = get_backend().resolve_judgment_id(
            ident,
            commercial_safe_only=commercial_safe_only,
        )
        if metas:
            return metas
    except StorageUnavailable as exc:
        backend_unavail = exc

    try:
        lance = getattr(get_retrieval(), "lance", None)
        if lance is not None and hasattr(lance, "lookup_judgment_by_id"):
            table_name, rows = lance.lookup_judgment_by_id(
                ident,
                commercial_safe_only=commercial_safe_only,
            )
            if rows:
                return _lance_chunks_to_metas(table_name, rows, fallback_id=ident)
    except StorageUnavailable:
        pass

    try:
        analytics = get_analytics()
        if analytics is not None and hasattr(analytics, "lookup_judgment_by_id"):
            ds_name, row = analytics.lookup_judgment_by_id(
                ident,
                commercial_safe_only=commercial_safe_only,
            )
            if row is not None:
                return [
                    _duckdb_row_to_meta(
                        ds_name, row, analytics=analytics, fallback_id=ident
                    )
                ]
    except StorageUnavailable:
        pass

    if backend_unavail is not None:
        raise backend_unavail
    return []


def _parse_paragraphs_arg(
    paragraphs: list[int] | str | None,
    para_start: int | None,
    para_end: int | None,
) -> tuple[int | None, int | None]:
    if paragraphs is None:
        return para_start, para_end
    if isinstance(paragraphs, list):
        nums = [int(x) for x in paragraphs]
        if not nums:
            return para_start, para_end
        return min(nums), max(nums)
    raw = str(paragraphs).strip()
    m = re.match(r"^(\d+)\s*[-..:]+\s*(\d+)$", raw)
    if m:
        return int(m.group(1)), int(m.group(2))
    if raw.isdigit():
        n = int(raw)
        return n, n
    return para_start, para_end


def _resolve_view(
    view: ViewMode,
    mode: AliasMode | None,
    paragraphs: list[int] | str | None,
    within_query: str | None,
    excerpt_query: str | None,
) -> ViewMode:
    if mode is not None:
        mapping: dict[str, ViewMode] = {
            "summary": "overview",
            "overview": "overview",
            "full": "paragraphs",
            "paragraphs": "paragraphs",
            "pages": "pages",
            "query_excerpt": "search_within",
            "search_within": "search_within",
            "docket": "docket",
        }
        return mapping.get(mode, "overview")
    if paragraphs is not None and view == "overview":
        return "paragraphs"
    if (excerpt_query or within_query) and view == "overview":
        return "search_within"
    return view


def _build_docket(meta: JudgmentMeta) -> CaseDocket:
    extra = meta.docket_extra or {}
    return CaseDocket(
        cnr=meta.cnr,
        case_number=extra.get("case_number"),
        case_type=meta.case_type,
        filing_date=extra.get("filing_date"),
        registration_date=extra.get("registration_date"),
        decision_date=meta.decision_date,
        disposal_nature=meta.disposal_nature,
        petitioner=meta.petitioner,
        respondent=meta.respondent,
        petitioner_advocates=list(extra.get("petitioner_advocates") or []),
        respondent_advocates=list(extra.get("respondent_advocates") or []),
        acts_sections=list(meta.acts_cited),
        hearing_count=extra.get("hearing_count"),
        sibling_order_ids=list(extra.get("sibling_order_ids") or []),
    )


async def get_judgment(
    identifier: str = "",
    view: ViewMode = "overview",
    para_start: int | None = None,
    para_end: int | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
    within_query: str | None = None,
    *,
    case_id: str | None = None,
    mode: AliasMode | None = None,
    paragraphs: list[int] | str | None = None,
    excerpt_query: str | None = None,
    max_chars: int = 24_000,
    commercial_safe_only: bool = False,
) -> JudgmentViewResponse:
    """Fetch windowed judgment excerpts, in-judgment BM25 search, or docket metadata."""
    ident = (identifier or case_id or "").strip()
    if not ident:
        raise InvalidFilter(
            "identifier is required (CNR, Neutral Citation, AIR/SCC/SCR, case_id, or case name).",
            kind="identifier",
            hint="Example: get_judgment(identifier='2017 INSC 801') or get_judgment(identifier='AIR 1973 SC 1461').",
        )

    effective_view = _resolve_view(view, mode, paragraphs, within_query, excerpt_query)
    p_start, p_end = _parse_paragraphs_arg(paragraphs, para_start, para_end)
    q_within = (within_query or excerpt_query or "").strip() or None
    if effective_view == "search_within":
        if not q_within:
            raise InvalidFilter(
                "within_query (or excerpt_query) is required when view='search_within'.",
                kind="within_query",
                hint="Pass within_query='...' to rank paragraphs inside this judgment.",
            )
        validate_query_length(q_within, field="within_query")

    cs_only = effective_commercial_safe(commercial_safe_only)
    metas = await asyncio.to_thread(
        resolve_judgment_across_tiers,
        ident,
        commercial_safe_only=cs_only,
    )
    if not metas:
        raise NotFound(
            f"Judgment {ident!r} not found in corpus.",
            kind="judgment",
            hint="Use search_judgments to search by party name, citation, court, or topic.",
        )
    if len(metas) > 1:
        candidates = [
            SqliteStore._meta_to_card(m).model_dump(mode="json")
            for m in metas[:5]
        ]
        raise AmbiguousReference(
            f"Identifier {ident!r} matched {len(metas)} distinct judgments; please specify a unique CNR, neutral_citation, or case_id.",
            kind="judgment",
            candidates=candidates,
            hint="Pass one of the candidate case_id or neutral_citation values.",
        )

    meta = metas[0]
    card = SqliteStore._meta_to_card(meta)
    ban_meta = check_publication_ban(meta.snippet or meta.title, meta.docket_extra)
    if ban_meta.blocked:
        raise TextUnavailableMetadataOnly(
            f"Judgment {meta.case_id} is subject to a statutory/judicial publication ban ({ban_meta.statute}: {ban_meta.reason}).",
            kind="judgment",
            metadata={"case_id": meta.case_id, "statute": ban_meta.statute, "reason": ban_meta.reason},
            hint="Operative sealed-cover, in-camera, and s.228A/BNS s.72 victim-identity suppression orders are excluded.",
        )

    if effective_view == "docket":
        framed_card = card.model_copy(
            update={
                "snippet": ensure_framed(card.snippet, doc_id=card.case_id, provenance=card.provenance)
                if card.snippet
                else None
            }
        )
        return JudgmentViewResponse(
            card=framed_card,
            view="docket",
            excerpts=[],
            docket=_build_docket(meta),
            extraction_method="metadata_only",
            provenance=meta.provenance,
        )

    clamped_max_chars = max(1_000, min(int(max_chars), 30_000))
    warning: str | None = None
    total_paras: int | None
    total_pages: int | None
    has_more: bool
    next_para: int | None
    next_page: int | None
    extraction_method: Literal["indexed_text", "pypdfium2_s3_pdf", "metadata_only"]
    chars_per_page: float | None

    if meta.pdf_s3_bucket and meta.pdf_s3_key:
        try:
            content = await get_pdf_fetcher().fetch_and_read(
                meta,
                mode=effective_view,
                para_start=p_start,
                para_end=p_end,
                page_start=page_start,
                page_end=page_end,
                query=q_within,
                max_chars=clamped_max_chars,
            )
        except PdfFetchError as exc:
            if effective_view != "overview":
                raise PdfFetchError(
                    exc.message,
                    reason=exc.reason,
                    hint=exc.hint
                    or "Use view='docket' or view='overview' for verified case metadata when the upstream S3 PDF is unavailable.",
                ) from exc
            warning = (
                f"Upstream S3 PDF unavailable ({exc.message}); "
                "returning verified case metadata and docket."
            )
            if meta.snippet:
                paras = [p.strip() for p in meta.snippet.split("\n\n") if p.strip()] or [meta.snippet]
                raw_excerpts = [
                    JudgmentExcerpt(para_num=i, text=p)
                    for i, p in enumerate(paras[:30], start=1)
                ]
                total_paras = len(paras)
            else:
                summary_parts = [
                    f"{meta.title} ({meta.court_name}, {meta.decision_date or meta.year or 'N/A'})."
                ]
                cites = [c for c in [meta.neutral_citation, *meta.reporter_citations] if c]
                if cites:
                    summary_parts.append(f"Citations: {', '.join(cites)}.")
                if meta.disposal_nature:
                    summary_parts.append(f"Disposal: {meta.disposal_nature}.")
                if meta.acts_cited:
                    summary_parts.append(f"Acts cited: {', '.join(meta.acts_cited)}.")
                raw_excerpts = [JudgmentExcerpt(para_num=1, text=" ".join(summary_parts))]
                total_paras = 1
            total_pages = None
            has_more = False
            next_para = None
            next_page = None
            extraction_method = "metadata_only"
            chars_per_page = None
        else:
            if content.extraction_quality == "scanned_image_only":
                raise TextUnavailableMetadataOnly(
                    f"Judgment {meta.case_id} PDF is a scanned image without an embedded text layer "
                    f"(chars_per_page={content.chars_per_page}).",
                    kind="judgment",
                    metadata=card.model_dump(mode="json"),
                    hint="Use view='docket' for structured metadata; pre-2010 scanned PDFs have no OCR text.",
                )

            raw_excerpts = content.excerpts
            total_paras = content.total_paragraphs
            total_pages = content.total_pages
            has_more = content.has_more
            next_para = content.next_para
            next_page = content.next_page
            extraction_method = content.extraction_method
            chars_per_page = content.chars_per_page
            warning = content.warning
    elif meta.snippet:
        # Indexed text / snippet available without S3 PDF
        paras = [p.strip() for p in meta.snippet.split("\n\n") if p.strip()] or [meta.snippet]
        if effective_view == "search_within" and q_within:
            ranked = ParagraphBM25(paras).rank(q_within, top_k=5)
            raw_excerpts = [
                JudgmentExcerpt(para_num=idx + 1, text=paras[idx], score=round(sc, 4))
                for idx, sc in ranked
            ]
        else:
            start = max(1, p_start or 1)
            end = min(len(paras), p_end or min(len(paras), start + 29))
            raw_excerpts = [
                JudgmentExcerpt(para_num=i, text=paras[i - 1])
                for i in range(start, end + 1)
            ]
        total_paras = len(paras)
        total_pages = 1
        has_more = False
        next_para = None
        next_page = None
        extraction_method = "indexed_text"
        chars_per_page = float(len(meta.snippet))
    else:
        if effective_view in ("paragraphs", "pages", "search_within"):
            raise TextUnavailableMetadataOnly(
                f"Full text is unavailable for metadata-only record {meta.case_id}.",
                kind="judgment",
                metadata=card.model_dump(mode="json"),
                hint="Use view='docket' or view='overview' for metadata-only records.",
            )
        raw_excerpts = [
            JudgmentExcerpt(
                para_num=1,
                text=f"{meta.title} ({meta.court_name}, {meta.decision_date or meta.year or 'N/A'}).",
            )
        ]
        total_paras = 1
        total_pages = None
        has_more = False
        next_para = None
        next_page = None
        extraction_method = "metadata_only"
        chars_per_page = None

    framed_excerpts: list[JudgmentExcerpt] = []
    for ex in raw_excerpts:
        ban = check_publication_ban(ex.text, meta.docket_extra)
        if ban.blocked:
            raise TextUnavailableMetadataOnly(
                f"Judgment {meta.case_id} text is restricted by publication ban ({ban.statute}: {ban.reason}).",
                kind="judgment",
                metadata={"case_id": meta.case_id, "statute": ban.statute, "reason": ban.reason},
            )
        framed_excerpts.append(
            ex.model_copy(
                update={
                    "text": ensure_framed(
                        ex.text,
                        doc_id=f"{meta.case_id}:p{ex.para_num or ex.page_num or 1}",
                        provenance=meta.provenance,
                    )
                }
            )
        )

    framed_card = card.model_copy(
        update={
            "snippet": ensure_framed(card.snippet, doc_id=card.case_id, provenance=card.provenance)
            if card.snippet
            else None
        }
    )

    return JudgmentViewResponse(
        card=framed_card,
        view=effective_view,
        excerpts=framed_excerpts,
        docket=_build_docket(meta) if effective_view == "overview" else None,
        total_paragraphs=total_paras,
        total_pages=total_pages,
        has_more=has_more,
        next_para=next_para,
        next_page=next_page,
        extraction_method=extraction_method,
        chars_per_page=chars_per_page,
        warning=warning,
        provenance=meta.provenance,
    )


def register(mcp: Any) -> None:
    @mcp.tool(
        name="get_judgment",
        description=(
            "Windowed judgment reader supporting 5 views: 'overview' (metadata card + opening and "
            "operative paragraphs), 'paragraphs' (1-based para_start..para_end window, max 30 paras / "
            "24,000 chars by default, max_chars clamped to 1,000-30,000), "
            "'pages' (1-based page_start..page_end window, max 10 pages / 30 KB), 'search_within' "
            "(ephemeral BM25 ranking inside the judgment PDF for within_query), and 'docket' "
            "(structured case-level docket metadata). Never guesses on ambiguous case names "
            "(raises ambiguous_reference with top-5 candidate cards)."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
            "title": "Read judgment overview, paragraph windows, or search within PDF",
        },
    )
    @structured_errors
    async def _tool(
        identifier: str = "",
        view: ViewMode = "overview",
        para_start: int | None = None,
        para_end: int | None = None,
        page_start: int | None = None,
        page_end: int | None = None,
        within_query: str | None = None,
        mode: AliasMode | None = None,
        paragraphs: list[int] | str | None = None,
        excerpt_query: str | None = None,
        max_chars: int = 24_000,
        commercial_safe_only: bool = False,
    ) -> JudgmentViewResponse:
        return await get_judgment(
            identifier=identifier,
            view=view,
            para_start=para_start,
            para_end=para_end,
            page_start=page_start,
            page_end=page_end,
            within_query=within_query,
            mode=mode,
            paragraphs=paragraphs,
            excerpt_query=excerpt_query,
            max_chars=max_chars,
            commercial_safe_only=commercial_safe_only,
        )
