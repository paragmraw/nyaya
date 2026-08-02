"""Embedding helper for the semantic_query tool.

Model: BAAI/bge-large-en-v1.5 (1024-d) by default. Matches the model used by
the hydration notebook so query and document vectors share the same space.

Uses CUDAExecutionProvider when available (NVIDIA GPU), falling back to
CPUExecutionProvider. The fallback is automatic: fastembed passes the
providers list to onnxruntime, which picks the first usable one.

The model name is configurable via ``NYAYA_EMBEDDING_MODEL`` but must produce
``EXPECTED_DIM``-dimensional vectors to match the pgvector columns.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from .exceptions import EmbeddingUnavailable

# CUDA first (NVIDIA GPU), CPU as the universal fallback. ORT picks the
# first usable provider in the list, so CUDA-capable machines use the GPU
# and everything else transparently uses CPU.
_PROVIDERS: tuple[str, ...] = ("CUDAExecutionProvider", "CPUExecutionProvider")
EXPECTED_DIM = 1024


def _model_name() -> str:
    from .config import get_settings

    return get_settings().embedding_model


@lru_cache(maxsize=1)
def _model() -> Any:
    try:
        from fastembed import TextEmbedding
    except ImportError as e:
        raise EmbeddingUnavailable(
            "fastembed is not installed. Install with: pip install 'nyaya[semantic]'",
            hint="Re-install the package with the 'semantic' extra or use the slim Docker image.",
        ) from e

    return TextEmbedding(model_name=_model_name(), providers=list(_PROVIDERS))


@lru_cache(maxsize=256)
def embed_query(text: str) -> list[float]:
    """Embed a single query string. Results are cached per query string.

    Raises EmbeddingUnavailable if fastembed is missing or the model fails to
    load, and SearchError if the produced vector has the wrong dimensionality.
    """
    if not text or not text.strip():
        raise EmbeddingUnavailable(
            "Cannot embed an empty query.",
            hint="Provide a non-empty query string to semantic_query.",
        )
    model = _model()
    embeddings = list(model.embed([text]))
    if not embeddings:
        raise EmbeddingUnavailable(
            "The embedding model returned no vector for the query.",
            hint="This is unexpected; check the model installation.",
        )
    vec = embeddings[0].tolist()
    if len(vec) != EXPECTED_DIM:
        raise EmbeddingUnavailable(
            f"Embedding dimension mismatch: expected {EXPECTED_DIM}, got {len(vec)}.",
            hint=f"Re-build embeddings with a {EXPECTED_DIM}-d model, or update EXPECTED_DIM.",
        )
    return vec


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts (used by the ingestion CLI). Not cached."""
    model = _model()
    return [e.tolist() for e in model.embed(texts)]