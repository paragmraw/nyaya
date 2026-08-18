"""semantic_query: embedding-based retrieval + reranking (the primary search tool).

Replaces the v0.1 search_law / hybrid_search / search_by_kind / search_judgments
tools. Uses the NVIDIA nemotron-3-embed-1b embedder + llama-nemotron-rerank-1b-v2
reranker via the NVIDIA API.
"""

from __future__ import annotations

from .. import db
from ..models import SearchResponse
from ._util import run_sync, validate_query_length


def register(mcp) -> None:
    @mcp.tool(
        name="semantic_query",
        description=(
            "Semantic search over the Indian law corpus using embedding retrieval + "
            "cross-encoder reranking. Returns the most relevant sections, articles, "
            "and judgments for a natural-language query. Better than keyword search "
            "for paraphrased queries and cross-act comparisons (e.g. 'punishment "
            "for murder' finds both IPC s.302 and BNS s.103). "
            "Optional 'kind' filters to 'section', 'article', or 'judgment'. "
            "Optional 'act' scopes to one act short-name (e.g. 'IPC', 'BNS')."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "Semantic search"},
    )
    @run_sync
    def semantic_query(
        query: str,
        kind: str | None = None,
        act: str | None = None,
        limit: int = 10,
        offset: int = 0,
    ) -> SearchResponse:
        """Semantic search (embed + ANN + rerank).

        Args:
            query: Free-text or natural-language query.
            kind: Optional filter: 'section', 'article', 'judgment', 'schedule', or 'amendment'.
            act: Optional act short-name to scope the search (e.g. 'IPC', 'BNS').
            limit: Max hits (1–50, default 10).
            offset: Pagination offset (default 0).
        """
        limit = max(1, min(int(limit), 50))
        offset = max(0, int(offset))
        if not query or not query.strip():
            return SearchResponse(
                query=query or "", total=0, returned=0, offset=offset,
                results=[], as_of=db.corpus_as_of(), limit=limit,
            )
        validate_query_length(query)

        results, total, fallback_reason = db.rerank_search(
            query, kind=kind, act=act, limit=limit, offset=offset
        )
        return SearchResponse(
            query=query, total=total, returned=len(results), offset=offset,
            results=results, as_of=db.corpus_as_of(), limit=limit,
            fallback_reason=fallback_reason,
        )
