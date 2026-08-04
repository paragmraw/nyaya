"""hybrid_search: combine FTS + semantic search via Reciprocal Rank Fusion."""

from __future__ import annotations

from .. import db
from ..exceptions import EmbeddingUnavailable, SearchError
from ..models import SearchResponse
from ._util import run_sync


def register(mcp) -> None:
    @mcp.tool(
        name="hybrid_search",
        description=(
            "Hybrid search combining keyword (FTS) and semantic (vector) ranking for the "
            "best of both worlds. Uses Reciprocal Rank Fusion (RRF) to merge the two ranked "
            "lists into a single ranking. Better than search_law for paraphrased queries, "
            "and better than semantic_query for exact-term queries. Falls back to search_law "
            "if the embedding model is unavailable (e.g. Alpine image)."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "Hybrid search"},
    )
    @run_sync
    def hybrid_search(query: str, act: str | None = None,
                      limit: int = 10, offset: int = 0) -> SearchResponse:
        """Hybrid search (FTS + semantic via RRF).

        Args:
            query: Free-text or natural-language query.
            act: Optional act short-name to scope the search.
            limit: Max hits (1–50, default 10).
            offset: Pagination offset (default 0, clamped to >= 0).
        """
        limit = max(1, min(int(limit), 50))
        offset = max(0, int(offset))
        if not query or not query.strip():
            return SearchResponse(query=query or "", total=0, returned=0, offset=offset,
                                  results=[], as_of=db.corpus_as_of(), limit=limit)

        # Fetch FTS results.
        fts_results, fts_total = db.search_all(query, act=act, limit=limit * 2, offset=0)

        # Try semantic results; fall back to FTS-only if unavailable.
        try:
            from ..embeddings import embed_query
            embedding = embed_query(query)
            sem_results = db.semantic_search_all(embedding, act=act, limit=limit * 2)
        except (EmbeddingUnavailable, SearchError):
            sem_results = []

        # Reciprocal Rank Fusion: score = sum(1 / (k + rank)) over both lists.
        k = 60  # standard RRF constant
        scores: dict[tuple[str, str], float] = {}
        meta: dict[tuple[str, str], object] = {}
        for rank, r in enumerate(fts_results, 1):
            key = (r.act, r.ref)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            meta[key] = r
        for rank, r in enumerate(sem_results, 1):
            key = (r.act, r.ref)
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + rank)
            if key not in meta:
                meta[key] = r

        merged = sorted(scores.items(), key=lambda x: -x[1])
        results = [meta[key] for key, _ in merged]
        # Apply offset after the merge for correct global pagination.
        results = results[offset:offset + limit]
        total = len(merged)
        return SearchResponse(
            query=query, total=total, returned=len(results), offset=offset,
            results=results, as_of=db.corpus_as_of(), limit=limit,
        )
