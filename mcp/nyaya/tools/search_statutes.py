"""search_statutes: lexical BM25 and exact-phrase search over statutes, Constitution, and regulations."""

from __future__ import annotations

from typing import Any, Literal

from ..exceptions import InvalidFilter
from ..models import ProvisionKind, StatuteHit, StatuteSearchQuery, StatuteSearchResponse
from ..storage import get_retrieval
from ._error import structured_errors
from ._util import (
    effective_commercial_safe,
    ensure_framed,
    run_sync,
    validate_query_length,
)


def search_statutes(
    query: str,
    act: str | None = None,
    jurisdiction: str | None = None,
    kind: ProvisionKind | None = None,
    category: str | None = None,
    status: Literal["in_force", "repealed", "all"] = "all",
    query_mode: Literal["bm25", "exact_phrase"] = "bm25",
    promote_definitions: bool = False,
    commercial_safe_only: bool = False,
    limit: int = 10,
    offset: int = 0,
    cursor: str | None = None,
) -> StatuteSearchResponse:
    """Search Indian Central Acts, Constitution, State/UT Acts, and Regulator texts."""
    if not query or not query.strip():
        raise InvalidFilter(
            "query must not be empty.",
            kind="query",
            hint="Provide a statutory keyword, phrase, or section citation.",
        )
    validate_query_length(query)
    cs_only = effective_commercial_safe(commercial_safe_only)
    clamped_limit = max(1, min(int(limit), 25))
    clamped_offset = max(0, int(offset))

    q = StatuteSearchQuery(
        query=query.strip(),
        act=act.strip() if act and act.strip() else None,
        jurisdiction=jurisdiction.strip() if jurisdiction and jurisdiction.strip() else None,
        kind=kind,
        category=category.strip() if category and category.strip() else None,
        status=status,
        query_mode=query_mode,
        promote_definitions=bool(promote_definitions),
        commercial_safe_only=cs_only,
        limit=clamped_limit,
        offset=clamped_offset,
        cursor=cursor,
    )
    resp = get_retrieval().search_statutes(q)
    framed_results: list[StatuteHit] = []
    for hit in resp.results:
        raw_snippet = hit.snippet[:400] if hit.snippet else ""
        framed_results.append(
            hit.model_copy(
                update={
                    "snippet": ensure_framed(
                        raw_snippet,
                        doc_id=hit.doc_id,
                        provenance=hit.provenance,
                    )
                }
            )
        )
    return resp.model_copy(update={"results": framed_results})


def register(mcp: Any) -> None:
    @mcp.tool(
        name="search_statutes",
        description=(
            "Lexical BM25 and exact-phrase search across Indian Central Acts, the Constitution "
            "(articles, schedules, amendments), 36 State/UT legislation packs, and 12 financial/sector "
            "regulators (SEBI, RBI, IRDAI, TRAI, etc.). Automatically boosts exact section/article "
            "citations to the top 3 results and attaches repeal/replacement validity metadata. "
            "Returns up to 400-character snippets wrapped in <corpus_text>; call get_provision for full text."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
            "title": "Search Indian statutes, constitution & regulations",
        },
    )
    @structured_errors
    @run_sync
    def _tool(
        query: str,
        act: str | None = None,
        jurisdiction: str | None = None,
        kind: ProvisionKind | None = None,
        category: str | None = None,
        status: Literal["in_force", "repealed", "all"] = "all",
        query_mode: Literal["bm25", "exact_phrase"] = "bm25",
        promote_definitions: bool = False,
        commercial_safe_only: bool = False,
        limit: int = 10,
        offset: int = 0,
        cursor: str | None = None,
    ) -> StatuteSearchResponse:
        return search_statutes(
            query=query,
            act=act,
            jurisdiction=jurisdiction,
            kind=kind,
            category=category,
            status=status,
            query_mode=query_mode,
            promote_definitions=promote_definitions,
            commercial_safe_only=commercial_safe_only,
            limit=limit,
            offset=offset,
            cursor=cursor,
        )
