"""semantic_query: embedding-based retrieval + reranking (the primary search tool).

Replaces the v0.1 search_law / hybrid_search / search_by_kind / search_judgments
tools, and absorbs get_definition via the ``promote_definitions`` flag. Uses the
NVIDIA nemotron-3-embed-1b embedder + llama-nemotron-rerank-1b-v2 reranker via the
NVIDIA API.
"""

from __future__ import annotations

from .. import db
from ..models import SearchResponse
from ._error import structured_errors
from ._util import run_sync, validate_query_length

# Note: the definition-promotion regex has a single home — ``db._DEF_RE`` —
# where the promote_definitions re-sort actually runs (inside db.rerank_search).


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
            "Optional 'act' scopes to one act short-name (e.g. 'IPC', 'BNS'). "
            "Set promote_definitions=true to boost sections whose title contains "
            "'definition' or 'interpretation' — use this when looking up the "
            "statutory meaning of a legal term (e.g. 'good faith', 'fraud')."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "Semantic search"},
    )
    @structured_errors
    @run_sync
    def semantic_query(
        query: str,
        kind: str | None = None,
        act: str | None = None,
        limit: int = 10,
        offset: int = 0,
        promote_definitions: bool = False,
    ) -> SearchResponse:
        """Semantic search (embed + ANN + rerank).

        Args:
            query: Free-text or natural-language query.
            kind: Optional filter: 'section', 'article', 'judgment', 'schedule', or 'amendment'.
            act: Optional act short-name to scope the search (e.g. 'IPC', 'BNS').
            limit: Max hits (1–50, default 10).
            offset: Pagination offset (default 0).
            promote_definitions: When true, promote results whose title contains
                'definition' or 'interpretation' to the top. Use for statutory
                definition lookups (e.g. 'good faith', 'dishonestly').
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
            query, kind=kind, act=act, limit=limit, offset=offset,
            promote_definitions=promote_definitions,
        )
        return SearchResponse(
            query=query, total=total, returned=len(results), offset=offset,
            results=results, as_of=db.corpus_as_of(), limit=limit,
            fallback_reason=fallback_reason,
        )
