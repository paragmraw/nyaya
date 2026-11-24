"""verify_citations: deterministic citation & verbatim-quote audit against the Nyaya corpus."""

from __future__ import annotations

import asyncio
import re
from datetime import date as Date
from typing import Any

from ..citations import ParsedCitation, parse_citations, parse_single_citation
from ..exceptions import CommercialLicenseBlocked, InvalidFilter
from ..models import (
    AuditFinding,
    AuditStatus,
    CitationAuditResponse,
    QuoteCheckInput,
)
from ..storage import get_backend
from ..storage.sqlite_store import SqliteStore
from ._error import structured_errors
from ._util import (
    MAX_DRAFT_TEXT_LENGTH,
    effective_commercial_safe,
    ensure_framed,
    parse_optional_date,
    validate_query_length,
)

_WS_RE = re.compile(r"\s+")


def _norm_quote(s: str) -> str:
    return _WS_RE.sub(" ", s.strip().lower())


def _audit_parsed_citation(
    p: ParsedCitation,
    *,
    backend: Any,
    check_date: Date,
    cs_only: bool,
    compact: bool = False,
) -> AuditFinding:
    parsed_dict = None if compact else p.model_dump(mode="json")
    excerpt_cap = 180 if compact else 400

    # 1. Statutory / Constitutional / Regulatory provisions
    if p.kind in {
        "statute_section",
        "constitution_article",
        "constitution_schedule",
        "cpc_order_rule",
        "regulation_clause",
    }:
        act_name = p.act_or_court or "Constitution"
        try:
            rec = backend.get_provision(
                act_name,
                p.base_ref,
                include_text=True,
                include_links=False,
                commercial_safe_only=cs_only,
            )
        except CommercialLicenseBlocked as exc:
            return AuditFinding(
                raw_citation=p.raw_text,
                span=p.span,
                parsed=parsed_dict,
                status="COMMERCIAL_LICENSE_BLOCKED",
                coverage_note=exc.message,
            )

        if rec is None:
            # Check if the Act itself exists in catalog
            try:
                backend.browse_statute(act=act_name, commercial_safe_only=cs_only)
                status: AuditStatus = "INVALID_STATUTORY_REF"
                note = f"Act {act_name!r} is indexed, but provision {p.base_ref!r} does not exist."
            except Exception:
                status = "NOT_FOUND_IN_CORPUS"
                note = f"Neither Act {act_name!r} nor provision {p.base_ref!r} was found in the indexed corpus."
            return AuditFinding(
                raw_citation=p.raw_text,
                span=p.span,
                parsed=parsed_dict,
                status=status,
                coverage_note=note,
            )

        val = rec.validity
        if val.status in {"struck_down", "read_down", "partially_struck_down", "kept_in_abeyance"} or val.invalidating_judgment is not None:
            st: AuditStatus = "VERIFIED_STRUCK_DOWN_OR_READ_DOWN"
        elif val.status in {"repealed", "omitted"} and (val.repeal_date is None or val.repeal_date <= check_date):
            st = "VERIFIED_REPEALED"
        else:
            st = "VERIFIED"

        return AuditFinding(
            raw_citation=p.raw_text,
            span=p.span,
            parsed=parsed_dict,
            status=st,
            canonical_record={
                "doc_id": rec.doc_id,
                "act": rec.act,
                "ref": rec.ref,
                "title": rec.title,
                "status": val.status,
            },
            validity=val,
            matched_excerpt=ensure_framed(
                rec.text[:excerpt_cap],
                doc_id=rec.doc_id,
                provenance=rec.provenance,
            ),
            provenance=rec.provenance,
        )

    # 2. Constitutional Amendment
    if p.kind == "constitution_amendment":
        try:
            num = int(p.base_ref)
            hist = backend.get_provision_history(
                "Constitution",
                amendment_number=num,
                commercial_safe_only=cs_only,
            )
        except Exception:
            hist = None
        if hist and hist.amendments:
            am = hist.amendments[0]
            return AuditFinding(
                raw_citation=p.raw_text,
                span=p.span,
                parsed=parsed_dict,
                status="VERIFIED",
                canonical_record=am.model_dump(mode="json"),
                matched_excerpt=ensure_framed(
                    am.summary[:excerpt_cap],
                    doc_id=am.amendment_id,
                    provenance=am.provenance,
                ),
                provenance=am.provenance,
            )
        return AuditFinding(
            raw_citation=p.raw_text,
            span=p.span,
            parsed=parsed_dict,
            status="NOT_FOUND_IN_CORPUS",
            coverage_note="Constitutional amendment not found in verified Amendments 1-106 table.",
        )

    # 3. Judicial citations (CNR, INSC, HC neutral, AIR, SCC, SCR, other reporters)
    try:
        metas = backend.resolve_judgment_id(p.base_ref, commercial_safe_only=cs_only)
    except CommercialLicenseBlocked as exc:
        return AuditFinding(
            raw_citation=p.raw_text,
            span=p.span,
            parsed=parsed_dict,
            status="COMMERCIAL_LICENSE_BLOCKED",
            coverage_note=exc.message,
        )

    if not metas:
        return AuditFinding(
            raw_citation=p.raw_text,
            span=p.span,
            parsed=parsed_dict,
            status="NOT_FOUND_IN_CORPUS",
            coverage_note="Citation did not match any indexed judgment record.",
        )
    if len(metas) > 1:
        return AuditFinding(
            raw_citation=p.raw_text,
            span=p.span,
            parsed=parsed_dict,
            status="AMBIGUOUS",
            candidates=[SqliteStore._meta_to_card(m).model_dump(mode="json") for m in metas[:5]],
            coverage_note=f"Matched {len(metas)} distinct judgments.",
        )

    meta = metas[0]
    card = SqliteStore._meta_to_card(meta)
    larger_cnt = 0
    try:
        tr = backend.trace_precedent(meta.case_id, limit=1, commercial_safe_only=cs_only)
        larger_cnt = tr.cited_by_larger_bench_count
    except Exception:
        larger_cnt = 0

    return AuditFinding(
        raw_citation=p.raw_text,
        span=p.span,
        parsed=parsed_dict,
        status="VERIFIED",
        canonical_record=card.model_dump(mode="json"),
        cited_by_count=meta.cited_by_count,
        cited_by_larger_bench_count=larger_cnt,
        matched_excerpt=ensure_framed(
            meta.snippet[:excerpt_cap],
            doc_id=meta.case_id,
            provenance=meta.provenance,
        )
        if meta.snippet
        else None,
        provenance=meta.provenance,
    )


def _verify_citations_sync(
    text: str | None = None,
    citations: list[str] | None = None,
    check_date: Date | str | None = None,
    verify_quotes: list[QuoteCheckInput | dict[str, str]] | None = None,
    commercial_safe_only: bool = False,
    compact: bool = False,
) -> CitationAuditResponse:
    if not text and not citations and not verify_quotes:
        raise InvalidFilter(
            "Provide at least one of text, citations, or verify_quotes.",
            kind="input",
            hint="Pass draft text or an explicit list of citation strings to audit.",
        )
    if text is not None:
        validate_query_length(text, max_length=MAX_DRAFT_TEXT_LENGTH, field="text")
    if citations is not None and len(citations) > 50:
        raise InvalidFilter(
            f"Too many explicit citations ({len(citations)}); maximum is 50 per call.",
            kind="citations",
        )
    if verify_quotes is not None and len(verify_quotes) > 10:
        raise InvalidFilter(
            f"Too many verify_quotes items ({len(verify_quotes)}); maximum is 10 per call.",
            kind="verify_quotes",
        )

    effective_date = parse_optional_date(check_date, field="check_date") or Date.today()
    cs_only = effective_commercial_safe(commercial_safe_only)
    backend = get_backend()
    alias_map = backend.get_alias_map()

    findings: list[AuditFinding] = []

    if text and text.strip():
        for p in parse_citations(text, alias_map=alias_map):
            findings.append(
                _audit_parsed_citation(
                    p,
                    backend=backend,
                    check_date=effective_date,
                    cs_only=cs_only,
                    compact=compact,
                )
            )

    if citations:
        for raw in citations:
            clean = (raw or "").strip()
            if not clean:
                continue
            parsed = parse_single_citation(clean, alias_map=alias_map)
            if parsed is None:
                findings.append(
                    AuditFinding(
                        raw_citation=clean,
                        status="NOT_FOUND_IN_CORPUS",
                        coverage_note="Could not parse as a recognized Indian statutory or judicial citation.",
                    )
                )
            else:
                findings.append(
                    _audit_parsed_citation(
                        parsed.model_copy(update={"raw_text": clean}),
                        backend=backend,
                        check_date=effective_date,
                        cs_only=cs_only,
                        compact=compact,
                    )
                )

    if verify_quotes:
        for item in verify_quotes:
            q_in = item if isinstance(item, QuoteCheckInput) else QuoteCheckInput(**item)
            parsed = parse_single_citation(q_in.citation, alias_map=alias_map)
            target_text = ""
            doc_id = q_in.citation
            prov = None
            canon_rec: dict[str, Any] | None = None

            if parsed is not None and parsed.kind in {
                "statute_section",
                "constitution_article",
                "constitution_schedule",
                "cpc_order_rule",
                "regulation_clause",
            }:
                rec = backend.get_provision(
                    parsed.act_or_court or "Constitution",
                    parsed.base_ref,
                    include_text=True,
                    include_links=False,
                    commercial_safe_only=cs_only,
                )
                if rec is not None:
                    target_text = rec.text
                    doc_id = rec.doc_id
                    prov = rec.provenance
                    canon_rec = {"act": rec.act, "ref": rec.ref, "title": rec.title}
            else:
                ident = parsed.base_ref if parsed is not None else q_in.citation
                metas = backend.resolve_judgment_id(ident, commercial_safe_only=cs_only)
                if len(metas) == 1:
                    jm = metas[0]
                    target_text = jm.snippet or jm.title
                    doc_id = jm.case_id
                    prov = jm.provenance
                    canon_rec = SqliteStore._meta_to_card(jm).model_dump(mode="json")

            norm_needle = _norm_quote(q_in.quote)
            norm_hay = _norm_quote(target_text)
            excerpt_cap = 180 if compact else 400
            excerpt: str | None
            if target_text and norm_needle and norm_needle in norm_hay:
                q_status: AuditStatus = "QUOTE_VERIFIED"
                excerpt = (
                    ensure_framed(target_text[:excerpt_cap], doc_id=doc_id, provenance=prov)
                    if prov is not None
                    else target_text[:excerpt_cap]
                )
            else:
                q_status = "QUOTE_MISMATCH" if target_text else "NOT_FOUND_IN_CORPUS"
                excerpt = (
                    ensure_framed(target_text[:excerpt_cap], doc_id=doc_id, provenance=prov)
                    if (target_text and prov is not None)
                    else None
                )

            findings.append(
                AuditFinding(
                    raw_citation=q_in.citation,
                    parsed=None if compact else (parsed.model_dump(mode="json") if parsed else None),
                    status=q_status,
                    canonical_record=canon_rec,
                    matched_para_num=1 if q_status == "QUOTE_VERIFIED" else None,
                    matched_excerpt=excerpt,
                    provenance=prov,
                )
            )

    summary: dict[str, int] = {}
    for f in findings:
        summary[f.status] = summary.get(f.status, 0) + 1

    return CitationAuditResponse(
        checked_as_of=effective_date,
        precedent_status="unverified_automated",
        summary=summary,
        findings=findings,
    )


async def verify_citations(
    text: str | None = None,
    citations: list[str] | None = None,
    check_date: Date | str | None = None,
    verify_quotes: list[QuoteCheckInput | dict[str, str]] | None = None,
    commercial_safe_only: bool = False,
    compact: bool = False,
) -> CitationAuditResponse:
    """Audit citations and verbatim quotes in a legal brief or explicit citation list."""
    return await asyncio.to_thread(
        _verify_citations_sync,
        text=text,
        citations=citations,
        check_date=check_date,
        verify_quotes=verify_quotes,
        commercial_safe_only=commercial_safe_only,
        compact=compact,
    )


def register(mcp: Any) -> None:
    @mcp.tool(
        name="verify_citations",
        description=(
            "Deterministically extract and audit every Indian statutory and judicial citation in a "
            "legal brief (text up to 50,000 chars) or explicit citations list, plus optional verbatim "
            "quote checks (verify_quotes). Classifies each citation into VERIFIED, VERIFIED_REPEALED "
            "(e.g. IPC/CrPC/IEA after 2024-07-01), VERIFIED_STRUCK_DOWN_OR_READ_DOWN (e.g. IT Act s.66A, "
            "IPC s.497), NOT_FOUND_IN_CORPUS (hallucinated citations like AIR 1973 SC 9999), "
            "INVALID_STATUTORY_REF, AMBIGUOUS, QUOTE_VERIFIED, QUOTE_MISMATCH, or COMMERCIAL_LICENSE_BLOCKED."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
            "title": "Audit legal brief citations, statutory validity & quotes",
        },
    )
    @structured_errors
    async def _tool(
        text: str | None = None,
        citations: list[str] | None = None,
        check_date: str | None = None,
        verify_quotes: list[QuoteCheckInput | dict[str, str]] | None = None,
        commercial_safe_only: bool = False,
        compact: bool = False,
    ) -> CitationAuditResponse:
        return await verify_citations(
            text=text,
            citations=citations,
            check_date=check_date,
            verify_quotes=verify_quotes,
            commercial_safe_only=commercial_safe_only,
            compact=compact,
        )
