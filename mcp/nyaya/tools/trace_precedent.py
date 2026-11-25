"""trace_precedent: raw citation-graph edges and bench-strength comparison (no heuristic NLP treatment)."""

from __future__ import annotations

from datetime import date as Date
from typing import Any

from ..exceptions import AmbiguousReference, NotFound
from ..models import (
    PrecedentEdge,
    PrecedentTraceResponse,
    TraceDirection,
)
from ..retrieval import decode_cursor, encode_cursor
from ..storage import get_backend
from ..storage.sqlite_store import SqliteStore
from ._error import structured_errors
from ._util import (
    effective_commercial_safe,
    ensure_framed,
    parse_optional_date,
    run_sync,
)


def trace_precedent(
    identifier: str,
    direction: TraceDirection = "cited_by",
    court: str | None = None,
    min_bench_strength: int | None = None,
    larger_bench_only: bool = False,
    date_from: Date | str | None = None,
    commercial_safe_only: bool = False,
    limit: int = 15,
    offset: int = 0,
    cursor: str | None = None,
) -> PrecedentTraceResponse:
    """Trace incoming ('cited_by'), outgoing ('cites'), or bidirectional citation edges for a judgment."""
    backend = get_backend()
    cs_only = effective_commercial_safe(commercial_safe_only)
    clamped_limit = max(1, min(int(limit), 25))
    resolved_offset = decode_cursor(cursor, offset)
    parsed_date_from = parse_optional_date(date_from, field="date_from")

    metas = backend.resolve_judgment_id(identifier, commercial_safe_only=cs_only)
    if not metas:
        raise NotFound(
            f"Judgment {identifier!r} not found in corpus.",
            kind="judgment",
            hint="Use search_judgments to locate the judgment's case_id, CNR, or citation.",
        )
    if len(metas) > 1:
        raise AmbiguousReference(
            f"Identifier {identifier!r} matched {len(metas)} distinct judgments; specify an exact case_id, CNR, or citation.",
            kind="judgment",
            candidates=[SqliteStore._meta_to_card(m).model_dump(mode="json") for m in metas[:5]],
            hint="Pass one of the candidate case_id or neutral_citation values.",
        )

    trace = backend.trace_precedent(
        metas[0].case_id,
        direction=direction,
        court=court.strip() if court and court.strip() else None,
        min_bench_strength=min_bench_strength,
        larger_bench_only=bool(larger_bench_only),
        date_from=parsed_date_from,
        limit=clamped_limit,
        offset=resolved_offset,
        commercial_safe_only=cs_only,
    )

    edges: list[PrecedentEdge] = []
    for e in trace.edges:
        if e.related_case is None:
            continue
        edge_dir: Any = e.direction if e.direction in ("cited_by", "cites") else "cited_by"
        prov = e.provenance or e.related_case.provenance
        excerpt = e.quoting_excerpt
        if excerpt:
            excerpt = ensure_framed(
                excerpt[:600],
                doc_id=f"{e.from_id}->{e.to_id}",
                provenance=prov,
            )
        edges.append(
            PrecedentEdge(
                direction=edge_dir,
                related_case=e.related_case,
                bench_comparison=e.bench_comparison,
                citing_para_num=e.citing_para_num,
                quoting_excerpt=excerpt,
                provenance=prov,
            )
        )

    has_more = (resolved_offset + len(edges)) < trace.total
    next_cursor = encode_cursor(resolved_offset + len(edges)) if has_more else None

    return PrecedentTraceResponse(
        target=trace.target,
        precedent_status="unverified_automated",
        verification_notice=trace.verification_notice,
        cited_by_total=trace.cited_by_total,
        cites_total=trace.cites_total,
        cited_by_larger_bench_count=trace.cited_by_larger_bench_count,
        edges=edges,
        total=trace.total,
        returned=len(edges),
        next_cursor=next_cursor,
        has_more=has_more,
    )


def register(mcp: Any) -> None:
    @mcp.tool(
        name="trace_precedent",
        description=(
            "Query the baked citation graph for cases citing ('cited_by') or cited by ('cites') a "
            "target judgment, including bench-strength comparison (larger_bench, co_equal_bench, "
            "smaller_bench) and quoting paragraph excerpts (<corpus_text>). Always stamps "
            "precedent_status='unverified_automated' with an explicit verification notice; it never "
            "emits uncalibrated heuristic overruled/good-law NLP labels."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
            "title": "Trace precedent citation graph & larger-bench citations",
        },
    )
    @structured_errors
    @run_sync
    def _tool(
        identifier: str,
        direction: TraceDirection = "cited_by",
        court: str | None = None,
        min_bench_strength: int | None = None,
        larger_bench_only: bool = False,
        date_from: str | None = None,
        commercial_safe_only: bool = False,
        limit: int = 15,
        offset: int = 0,
        cursor: str | None = None,
    ) -> PrecedentTraceResponse:
        return trace_precedent(
            identifier=identifier,
            direction=direction,
            court=court,
            min_bench_strength=min_bench_strength,
            larger_bench_only=larger_bench_only,
            date_from=date_from,
            commercial_safe_only=commercial_safe_only,
            limit=limit,
            offset=offset,
            cursor=cursor,
        )
