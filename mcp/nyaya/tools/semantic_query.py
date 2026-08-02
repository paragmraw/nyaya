"""semantic_query: embedding-based similarity search via pgvector."""

from __future__ import annotations

from .. import db
from ..models import SearchResponse


def register(mcp) -> None:
    @mcp.tool(
        name="semantic_query",
        description=(
            "Semantic (meaning-based) search across the entire nyaya corpus using vector "
            "embeddings. Better than search_law when the user's phrasing doesn't match the "
            "statutory wording — e.g. 'can police search my phone without a warrant?' finds "
            "relevant CrPC/BNS provisions and Article 21 even without keyword overlap. "
            "Returns matching provisions ranked by cosine similarity."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "Semantic search"},
    )
    def semantic_query(query: str, limit: int = 5) -> SearchResponse:
        """Semantic search across the corpus.

        Args:
            query: Natural-language query, e.g. 'right to silence during interrogation'.
            limit: Maximum number of hits (1–20, default 5).
        """
        try:
            from ..embeddings import embed_query
            limit = max(1, min(int(limit), 20))
            embedding = embed_query(query)
            results = db.semantic_search_all(embedding, limit=limit)
        except (ImportError, RuntimeError):
            return SearchResponse(query=query, total=0, results=[])