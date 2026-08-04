"""Embedding helper for the semantic_query tool.

Default model: BAAI/bge-large-en-v1.5 (1024-d). Matches the hydration
notebook so query and document vectors share the same space.

Uses CUDAExecutionProvider when available (NVIDIA GPU), falling back to
CPUExecutionProvider. onnxruntime picks the first usable provider from
the list, so CUDA-capable machines use the GPU and everything else
transparently uses CPU.

The model name is configurable via ``NYAYA_EMBEDDING_MODEL`` but must
produce ``EXPECTED_DIM``-dimensional vectors to match the pgvector columns.

Raises:
    EmbeddingUnavailable: fastembed is not installed or the model fails
        to load — a *system* condition that the ``semantic_query`` tool
        surfaces as a distinct error code so the LLM can fall back to
        ``search_law``.
    SearchError: query is empty (client input error) or the produced
        vector has the wrong dimensionality (configuration mismatch).
"""

from __future__ import annotations

from typing import Any

from cachetools import TTLCache, cached

from .exceptions import EmbeddingUnavailable, SearchError

# CUDA first (NVIDIA GPU), then CPU as the universal fallback.
_PROVIDERS: tuple[str, ...] = ("CUDAExecutionProvider", "CPUExecutionProvider")
EXPECTED_DIM = 1024

# Model: one entry, 1-hour TTL. Queries: 256 entries, 1-hour TTL.
# The TTL ensures model updates propagate without a process restart.
_model_cache: TTLCache = TTLCache(maxsize=1, ttl=3600)
_query_cache: TTLCache = TTLCache(maxsize=256, ttl=3600)


def _model_name() -> str:
    from .config import get_settings

    return get_settings().embedding_model


@cached(_model_cache)
def _model() -> Any:
    try:
        from fastembed import TextEmbedding
    except ImportError as e:
        raise EmbeddingUnavailable(
            "fastembed is not installed. Install with: pip install 'nyaya[semantic]'",
            hint="Re-install the package with the 'semantic' extra or use the slim Docker image.",
        ) from e

    return TextEmbedding(model_name=_model_name(), providers=list(_PROVIDERS))


@cached(_query_cache)
def embed_query(text: str) -> list[float]:
    """Embed a single query string. Results are cached per query string.

    Raises EmbeddingUnavailable if fastembed is missing or the model fails to
    load, and SearchError if the query is empty or the produced vector has
    the wrong dimensionality.
    """
    if not text or not text.strip():
        raise SearchError(
            "Cannot embed an empty query.",
            hint="Provide a non-empty query string to semantic_query.",
        )
    model = _model()
    embeddings = list(model.embed([text]))
    if not embeddings:
        raise SearchError(
            "The embedding model returned no vector for the query.",
            hint="This is unexpected; check the model installation.",
        )
    vec = embeddings[0].tolist()
    if len(vec) != EXPECTED_DIM:
        raise SearchError(
            f"Embedding dimension mismatch: expected {EXPECTED_DIM}, got {len(vec)}.",
            hint=f"Re-build embeddings with a {EXPECTED_DIM}-d model, or update EXPECTED_DIM.",
        )
    return vec


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts (used by the ingestion CLI). Not cached.

    Guards: skips empty/whitespace-only texts (fastembed returns a zero
    vector for them, which pollutes the embedding space) and asserts the
    dimensionality of the first non-empty result.
    """
    model = _model()
    clean = [t if t and t.strip() else " " for t in texts]
    vectors = [e.tolist() for e in model.embed(clean)]
    if vectors:
        first = vectors[0]
        if len(first) != EXPECTED_DIM:
            raise SearchError(
                f"Embedding dimension mismatch: expected {EXPECTED_DIM}, got {len(first)}.",
                hint=f"Re-build embeddings with a {EXPECTED_DIM}-d model.",
            )
    return vectors