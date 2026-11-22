"""get_judgment: windowed judgment reader with direct AWS S3 pypdfium2 extraction and search_within."""

from __future__ import annotations

import asyncio
import re
from typing import Any, Literal

from ..exceptions import (
    AmbiguousReference,
    InvalidFilter,
    NotFound,
    TextUnavailableMetadataOnly,
)
from ..models import (
    CaseDocket,
    JudgmentExcerpt,
    JudgmentMeta,
    JudgmentViewResponse,
)
from ..retrieval.paragraph_bm25 import ParagraphBM25
from ..sanitize import check_publication_ban
from ..storage import get_backend, get_pdf_fetcher
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
    backend = get_backend()
    metas = await asyncio.to_thread(
        backend.resolve_judgment_id,
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

    if meta.pdf_s3_bucket and meta.pdf_s3_key:
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
        provenance=meta.provenance,
    )


def register(mcp: Any) -> None:
    @mcp.tool(
        name="get_judgment",
        description=(
            "Windowed judgment reader supporting 5 views: 'overview' (metadata card + opening and "
            "operative paragraphs), 'paragraphs' (1-based para_start..para_end window, max 30 paras / 25 KB), "
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
