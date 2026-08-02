"""Embedding helper for the semantic_query tool.

Model: BAAI/bge-large-en-v1.5 (1024-d). Matches the model used by the
hydration notebook so query and document vectors share the same space.

Uses CUDAExecutionProvider when available (NVIDIA GPU), falling back to
CPUExecutionProvider. The fallback is automatic: fastembed passes the
providers list to onnxruntime, which picks the first usable one.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

# CUDA first (NVIDIA GPU), CPU as the universal fallback. ORT picks the
# first usable provider in the list, so CUDA-capable machines use the GPU
# and everything else transparently uses CPU.
_PROVIDERS: list[str] = ["CUDAExecutionProvider", "CPUExecutionProvider"]
_MODEL_NAME = "BAAI/bge-large-en-v1.5"


@lru_cache(maxsize=1)
def _model() -> Any:
    try:
        from fastembed import TextEmbedding
    except ImportError as e:
        raise RuntimeError(
            "fastembed is not installed. Install with: pip install 'nyaya[semantic]'"
        ) from e

    return TextEmbedding(model_name=_MODEL_NAME, providers=_PROVIDERS)


def embed_query(text: str) -> list[float]:
    model = _model()
    embeddings = list(model.embed([text]))
    return embeddings[0].tolist() if embeddings else []


def embed_texts(texts: list[str]) -> list[list[float]]:
    model = _model()
    return [e.tolist() for e in model.embed(texts)]