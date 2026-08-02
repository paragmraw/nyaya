"""Embedding helper for the semantic_query tool."""

from __future__ import annotations

from functools import lru_cache
from typing import Any


@lru_cache(maxsize=1)
def _model() -> Any:
    try:
        from fastembed import TextEmbedding
    except ImportError as e:
        raise RuntimeError(
            "fastembed is not installed. Install with: pip install 'nyaya[semantic]'"
        ) from e

    return TextEmbedding(model_name="BAAI/bge-small-en-v1.5")


def embed_query(text: str) -> list[float]:
    model = _model()
    embeddings = list(model.embed([text]))
    return embeddings[0].tolist() if embeddings else []


def embed_texts(texts: list[str]) -> list[list[float]]:
    model = _model()
    return [e.tolist() for e in model.embed(texts)]