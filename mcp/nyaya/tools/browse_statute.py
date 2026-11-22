"""browse_statute: browse the statute/regulator catalog, table of contents, or provision index."""

from __future__ import annotations

from typing import Any, Literal

from ..models import (
    ActInfo,
    ActSummary,
    ProvisionIndexEntry,
    ProvisionKind,
    StatuteBrowseResponse,
)
from ..retrieval import decode_cursor, encode_cursor
from ..storage import get_backend
from ._error import structured_errors
from ._util import effective_commercial_safe, ensure_framed, run_sync


def _act_to_summary(a: ActInfo) -> ActSummary:
    return ActSummary(
        short_name=a.short_name,
        full_name=a.full_name,
        jurisdiction=a.jurisdiction,
        category=a.category,
        year=a.year,
        status=a.status,
        provision_count=a.provision_count,
        provenance=a.provenance,
    )


def browse_statute(
    act: str | None = None,
    query: str | None = None,
    jurisdiction: str | None = None,
    category: str | None = None,
    status: Literal["in_force", "repealed", "all"] = "all",
    chapter: str | int | None = None,
    part: str | None = None,
    kind: ProvisionKind | None = None,
    ref_from: str | None = None,
    ref_to: str | None = None,
    include_snippet: bool = False,
    commercial_safe_only: bool = False,
    limit: int = 50,
    offset: int = 0,
    cursor: str | None = None,
) -> StatuteBrowseResponse:
    """Browse the Act/Regulator catalog, an Act's table of contents, or paginated provisions."""
    backend = get_backend()
    cs_only = effective_commercial_safe(commercial_safe_only)
    clamped_limit = max(1, min(int(limit), 200))
    resolved_offset = decode_cursor(cursor, offset)

    res = backend.browse_statute(
        act=act.strip() if act and act.strip() else None,
        query=query.strip() if query and query.strip() else None,
        jurisdiction=jurisdiction.strip() if jurisdiction and jurisdiction.strip() else None,
        category=category.strip() if category and category.strip() else None,
        status=status,
        chapter=chapter,
        part=part.strip() if part and part.strip() else None,
        ref_from=ref_from.strip() if ref_from and ref_from.strip() else None,
        ref_to=ref_to.strip() if ref_to and ref_to.strip() else None,
        kind=kind,
        include_text=bool(include_snippet),
        limit=clamped_limit,
        offset=resolved_offset,
        commercial_safe_only=cs_only,
    )

    acts = [_act_to_summary(a) for a in res.acts]
    act_info = _act_to_summary(res.act_info) if res.act_info is not None else None

    provisions: list[ProvisionIndexEntry] = []
    for p in res.provisions:
        snippet = p.snippet
        if include_snippet and snippet and p.provenance is not None:
            snippet = ensure_framed(
                snippet[:300],
                doc_id=p.doc_id,
                provenance=p.provenance,
            )
        elif not include_snippet:
            snippet = None
        provisions.append(p.model_copy(update={"snippet": snippet}))

    has_more = (resolved_offset + res.returned) < res.total
    next_cursor = encode_cursor(resolved_offset + res.returned) if has_more else None

    return StatuteBrowseResponse(
        mode=res.mode,
        total=res.total,
        returned=res.returned,
        next_cursor=next_cursor,
        has_more=has_more,
        acts=acts,
        act_info=act_info,
        chapters_or_parts=res.chapters_or_parts,
        provisions=provisions,
    )


def register(mcp: Any) -> None:
    @mcp.tool(
        name="browse_statute",
        description=(
            "Three-mode statutory explorer: (1) when act is omitted, searches/lists the catalog of "
            "Central Acts, State/UT Acts, and Regulators; (2) when act is set without chapter/part/kind/range "
            "filters, returns the Act's metadata and Chapters/Parts Table of Contents; (3) when act plus "
            "a chapter, part, kind, or ref_from..ref_to filter is provided, lists paginated provisions "
            "(with optional <=300-char <corpus_text> snippets when include_snippet=True)."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
            "title": "Browse statute catalog, TOC & provision index",
        },
    )
    @structured_errors
    @run_sync
    def _tool(
        act: str | None = None,
        query: str | None = None,
        jurisdiction: str | None = None,
        category: str | None = None,
        status: Literal["in_force", "repealed", "all"] = "all",
        chapter: str | int | None = None,
        part: str | None = None,
        kind: ProvisionKind | None = None,
        ref_from: str | None = None,
        ref_to: str | None = None,
        include_snippet: bool = False,
        commercial_safe_only: bool = False,
        limit: int = 50,
        offset: int = 0,
        cursor: str | None = None,
    ) -> StatuteBrowseResponse:
        return browse_statute(
            act=act,
            query=query,
            jurisdiction=jurisdiction,
            category=category,
            status=status,
            chapter=chapter,
            part=part,
            kind=kind,
            ref_from=ref_from,
            ref_to=ref_to,
            include_snippet=include_snippet,
            commercial_safe_only=commercial_safe_only,
            limit=limit,
            offset=offset,
            cursor=cursor,
        )
