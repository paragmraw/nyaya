"""Pure-lexical + metadata + citation-graph retrieval package for Nyaya MCP v2.0."""

from .lexical_engine import (
    LexicalRetrievalEngine,
    build_retrieval_engine,
    decode_cursor,
    encode_cursor,
)
from .paragraph_bm25 import ParagraphBM25
from .query_analyzer import AnalyzedQuery, analyze_query, expand_equivalent_provisions
from .ranking import (
    apply_definition_promotion,
    apply_exact_citation_floor,
    compute_authority_level,
    rank_judgment_cards,
    reciprocal_rank_fusion,
)

__all__ = [
    "AnalyzedQuery",
    "LexicalRetrievalEngine",
    "ParagraphBM25",
    "analyze_query",
    "apply_definition_promotion",
    "apply_exact_citation_floor",
    "build_retrieval_engine",
    "compute_authority_level",
    "decode_cursor",
    "encode_cursor",
    "expand_equivalent_provisions",
    "rank_judgment_cards",
    "reciprocal_rank_fusion",
]
