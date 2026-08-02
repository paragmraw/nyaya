"""semantic_query: embedding-based similarity search via pgvector."""

from __future__ import annotations

from .. import db
from ..exceptions import EmbeddingUnavailable
from ..models import SearchResponse
from ._util import run_sync


def register(mcp) -> None:
    @mcp.tool(
        name="semantic_query",
        description=(
            "Semantic (meaning-based) search across the entire nyaya corpus using vector "
            "embeddings. Better than search_law when the user's phrasing doesn't match the "
            "statutory wording — e.g. 'can police search my phone without a warrant?' finds "
            "relevant CrPC/BNS provisions and Article 21 even without keyword overlap. "
            "Returns matching provisions ranked by cosine similarity. If the semantic-search "
            "model is not installed in this build (e.g. the Alpine Docker image), raises an "
            "embedding_unavailable error — fall back to search_law in that case. The ``act`` "
            "parameter scopes the search to sections of one act (use 'Constitution' or "
            "'judgment' to scope to articles or judgments respectively)."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "Semantic search"},
    )
    @run_sync
    def semantic_query(query: str, act: str | None = None,
                       limit: int = 5) -> SearchResponse:
        """Semantic search across the corpus.

        Args:
            query: Natural-language query, e.g. 'right to silence during interrogation'.
                Must be non-empty.
            act: Optional act short-name to scope the search (e.g. 'IPC', 'Constitution',
                'judgment'). When omitted, searches sections + articles + judgments.
            limit: Maximum number of hits (1–20, default 5).
        """
        try:
            from ..embeddings import embed_query
            limit = max(1, min(int(limit), 20))
            embedding = embed_query(query)
            results = db.semantic_search_all(embedding, act=act, limit=limit)
        except EmbeddingUnavailable:
            # Re-raise so the MCP client gets a structured error code instead of
            # an empty result that looks like "no matches".
            raise
        return SearchResponse(
            query=query, total=len(results), returned=len(results), offset=0,
            results=results, as_of=db.corpus_as_of(), limit=limit,
        )