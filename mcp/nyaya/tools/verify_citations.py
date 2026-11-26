"""verify_citations: deterministic citation & verbatim-quote audit against the Nyaya corpus."""

from __future__ import annotations

import asyncio
import re
import unicodedata
from datetime import date as Date
from typing import Any

from ..citations import ParsedCitation, parse_citations, parse_single_citation
from ..config import get_settings
from ..exceptions import CommercialLicenseBlocked, InvalidFilter, PdfFetchError, StorageUnavailable
from ..models import (
    AuditFinding,
    AuditStatus,
    CitationAuditResponse,
    JudgmentMeta,
    QuoteCheckInput,
    SourceProvenance,
)
from ..storage import get_backend, get_pdf_fetcher, get_retrieval
from ..storage.sqlite_store import SqliteStore
from ._error import structured_errors
from ._util import (
    MAX_DRAFT_TEXT_LENGTH,
    effective_commercial_safe,
    ensure_framed,
    parse_optional_date,
    validate_query_length,
)
from .get_judgment import resolve_judgment_across_tiers

_WS_RE = re.compile(r"\s+")
_CURLY_QUOTE_TRANS = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u201a": "'",
        "\u201b": "'",
        "\u201c": '"',
        "\u201d": '"',
        "\u201e": '"',
        "\u201f": '"',
        "\u2013": "-",
        "\u2014": "-",
        "\u2212": "-",
    }
)
_MD_STRIP_RE = re.compile(r"\*\*|__|##+")
_INDIACODE_SUPERSCRIPT_RE = re.compile(r"(?<!\w)\d+\[")
_PROVISION_KINDS = frozenset(
    {
        "statute_section",
        "constitution_article",
        "constitution_schedule",
        "cpc_order_rule",
        "regulation_clause",
    }
)


def _excerpt_cap(compact: bool) -> int:
    return 180 if compact else 400


def _summarize_findings(findings: list[AuditFinding]) -> dict[str, int]:
    summary: dict[str, int] = {}
    for f in findings:
        summary[f.status] = summary.get(f.status, 0) + 1
    return summary


def _framed_or_raw(text: str, *, cap: int, doc_id: str, provenance: SourceProvenance | None) -> str:
    """Return ``text[:cap]`` wrapped in the provenance frame when provenance is known."""
    if provenance is None:
        return text[:cap]
    return ensure_framed(text[:cap], doc_id=doc_id, provenance=provenance)


def _norm_quote(s: str) -> str:
    norm = unicodedata.normalize("NFKC", s)
    norm = norm.translate(_CURLY_QUOTE_TRANS)
    norm = _MD_STRIP_RE.sub("", norm)
    norm = _INDIACODE_SUPERSCRIPT_RE.sub("[", norm)
    return _WS_RE.sub(" ", norm.strip().lower())


def _audit_parsed_citation(
    p: ParsedCitation,
    *,
    backend: Any,
    check_date: Date,
    cs_only: bool,
    compact: bool = False,
) -> AuditFinding:
    parsed_dict = None if compact else p.model_dump(mode="json")
    excerpt_cap = _excerpt_cap(compact)

    # 1. Statutory / Constitutional / Regulatory provisions
    if p.kind in _PROVISION_KINDS:
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
            if p.kind == "cpc_order_rule":
                return AuditFinding(
                    raw_citation=p.raw_text,
                    span=p.span,
                    parsed=parsed_dict,
                    status="NOT_FOUND_IN_CORPUS",
                    coverage_note=(
                        f"CPC Order/Rule {p.base_ref!r} is not indexed in the local SQLite core "
                        "(partial CPC Order/Rule coverage)."
                    ),
                )
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
        metas = resolve_judgment_across_tiers(p.base_ref, commercial_safe_only=cs_only)
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


def _run_verify_citations(
    text: str | None = None,
    citations: list[str] | None = None,
    check_date: Date | str | None = None,
    verify_quotes: list[QuoteCheckInput | dict[str, str]] | None = None,
    commercial_safe_only: bool = False,
    compact: bool = False,
) -> tuple[CitationAuditResponse, list[tuple[int, QuoteCheckInput, JudgmentMeta]]]:
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
    pdf_candidates: list[tuple[int, QuoteCheckInput, JudgmentMeta]] = []

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
        excerpt_cap = _excerpt_cap(compact)
        for item in verify_quotes:
            q_in = item if isinstance(item, QuoteCheckInput) else QuoteCheckInput(**item)
            parsed = parse_single_citation(q_in.citation, alias_map=alias_map)
            doc_id = q_in.citation
            prov = None
            canon_rec: dict[str, Any] | None = None
            norm_needle = _norm_quote(q_in.quote)
            q_status: AuditStatus = "NOT_FOUND_IN_CORPUS"
            matched_para: int | None = None
            excerpt: str | None = None
            resolved_jm: JudgmentMeta | None = None

            if parsed is not None and parsed.kind in _PROVISION_KINDS:
                rec = backend.get_provision(
                    parsed.act_or_court or "Constitution",
                    parsed.base_ref,
                    include_text=True,
                    include_links=False,
                    commercial_safe_only=cs_only,
                )
                if rec is not None:
                    doc_id = rec.doc_id
                    prov = rec.provenance
                    canon_rec = {"act": rec.act, "ref": rec.ref, "title": rec.title}
                    if rec.text and norm_needle and norm_needle in _norm_quote(rec.text):
                        q_status = "QUOTE_VERIFIED"
                        matched_para = 1
                    else:
                        q_status = "QUOTE_MISMATCH" if rec.text else "NOT_FOUND_IN_CORPUS"
                    if rec.text and prov is not None:
                        excerpt = ensure_framed(
                            rec.text[:excerpt_cap], doc_id=doc_id, provenance=prov
                        )
                    elif rec.text and q_status == "QUOTE_VERIFIED":
                        excerpt = rec.text[:excerpt_cap]
            else:
                ident = parsed.base_ref if parsed is not None else q_in.citation
                try:
                    metas = resolve_judgment_across_tiers(ident, commercial_safe_only=cs_only)
                except CommercialLicenseBlocked as exc:
                    q_status = "COMMERCIAL_LICENSE_BLOCKED"
                    metas = []
                    findings.append(
                        AuditFinding(
                            raw_citation=q_in.citation,
                            parsed=None if compact else (parsed.model_dump(mode="json") if parsed else None),
                            status=q_status,
                            coverage_note=exc.message,
                        )
                    )
                    continue
                if len(metas) == 1:
                    jm = metas[0]
                    resolved_jm = jm
                    doc_id = jm.case_id
                    prov = jm.provenance
                    canon_rec = SqliteStore._meta_to_card(jm).model_dump(mode="json")

                    # 1. Check LanceDB judgment chunks first when full-text chunks exist (for accurate para_start)
                    if jm.has_lance_text:
                        try:
                            lance = getattr(get_retrieval(), "lance", None)
                            if lance is not None and hasattr(lance, "get_judgment_chunks"):
                                chunks = lance.get_judgment_chunks(
                                    jm.case_id, commercial_safe_only=cs_only
                                )
                                for ch in chunks:
                                    ch_text = str(ch.get("text") or "")
                                    if ch_text and norm_needle and norm_needle in _norm_quote(ch_text):
                                        q_status = "QUOTE_VERIFIED"
                                        matched_para = int(ch.get("para_start") or 1)
                                        excerpt = _framed_or_raw(
                                            ch_text, cap=excerpt_cap, doc_id=doc_id, provenance=prov
                                        )
                                        break
                        except StorageUnavailable:
                            pass

                    # 2. Check jm.snippet and jm.title
                    if q_status != "QUOTE_VERIFIED":
                        matched_src: str | None = None
                        for candidate in (jm.snippet, jm.title):
                            if candidate and norm_needle and norm_needle in _norm_quote(candidate):
                                matched_src = candidate
                                break

                        if matched_src is not None:
                            q_status = "QUOTE_VERIFIED"
                            matched_para = 1
                            excerpt = _framed_or_raw(
                                matched_src, cap=excerpt_cap, doc_id=doc_id, provenance=prov
                            )
                        else:
                            # 3. Check LanceDB judgment chunks if not yet checked
                            if not jm.has_lance_text:
                                try:
                                    lance = getattr(get_retrieval(), "lance", None)
                                    if lance is not None and hasattr(lance, "get_judgment_chunks"):
                                        chunks = lance.get_judgment_chunks(
                                            jm.case_id, commercial_safe_only=cs_only
                                        )
                                        for ch in chunks:
                                            ch_text = str(ch.get("text") or "")
                                            if ch_text and norm_needle and norm_needle in _norm_quote(ch_text):
                                                q_status = "QUOTE_VERIFIED"
                                                matched_para = int(ch.get("para_start") or 1)
                                                excerpt = _framed_or_raw(
                                                    ch_text, cap=excerpt_cap, doc_id=doc_id, provenance=prov
                                                )
                                                break
                                except StorageUnavailable:
                                    pass

                            if q_status != "QUOTE_VERIFIED":
                                fallback_text = jm.snippet or jm.title
                                q_status = "QUOTE_MISMATCH" if fallback_text else "NOT_FOUND_IN_CORPUS"
                                if fallback_text and prov is not None:
                                    excerpt = ensure_framed(
                                        fallback_text[:excerpt_cap],
                                        doc_id=doc_id,
                                        provenance=prov,
                                    )

            finding_idx = len(findings)
            findings.append(
                AuditFinding(
                    raw_citation=q_in.citation,
                    parsed=None if compact else (parsed.model_dump(mode="json") if parsed else None),
                    status=q_status,
                    canonical_record=canon_rec,
                    matched_para_num=matched_para,
                    matched_excerpt=excerpt,
                    provenance=prov,
                )
            )
            if (
                q_status == "QUOTE_MISMATCH"
                and resolved_jm is not None
                and resolved_jm.pdf_s3_bucket
                and resolved_jm.pdf_s3_key
            ):
                pdf_candidates.append((finding_idx, q_in, resolved_jm))

    return (
        CitationAuditResponse(
            checked_as_of=effective_date,
            precedent_status="unverified_automated",
            summary=_summarize_findings(findings),
            findings=findings,
        ),
        pdf_candidates,
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
    resp, pdf_candidates = await asyncio.to_thread(
        _run_verify_citations,
        text=text,
        citations=citations,
        check_date=check_date,
        verify_quotes=verify_quotes,
        commercial_safe_only=commercial_safe_only,
        compact=compact,
    )
    if pdf_candidates:
        try:
            offline = get_settings().offline_only
        except Exception:
            offline = True
        if not offline:
            try:
                fetcher = get_pdf_fetcher()
            except StorageUnavailable:
                fetcher = None
            if fetcher is not None:
                excerpt_cap = _excerpt_cap(compact)
                upgraded = False
                for idx, q_in, jm in pdf_candidates:
                    norm_needle = _norm_quote(q_in.quote)
                    if not norm_needle:
                        continue
                    try:
                        content = await fetcher.fetch_and_read(
                            jm,
                            mode="search_within",
                            query=q_in.quote,
                            max_chars=24_000,
                        )
                    except (PdfFetchError, StorageUnavailable):
                        continue
                    for ex in content.excerpts:
                        if ex.text and norm_needle in _norm_quote(ex.text):
                            finding = resp.findings[idx]
                            finding.status = "QUOTE_VERIFIED"
                            finding.matched_para_num = ex.para_num or 1
                            finding.matched_excerpt = _framed_or_raw(
                                ex.text,
                                cap=excerpt_cap,
                                doc_id=jm.case_id,
                                provenance=jm.provenance,
                            )
                            upgraded = True
                            break
                if upgraded:
                    resp.summary = _summarize_findings(resp.findings)
    return resp


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
