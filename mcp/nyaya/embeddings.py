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
from cachetools.keys import hashkey

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


class EmbeddingService:
    """Encapsulates embedding caches and methods so they can be injected.

    Accepts optional cache instances so tests can inject fresh caches (or
    fakes) and avoid sharing global mutable state across test cases. A
    default singleton (backed by the module-level caches) is exposed via
    :func:`get_default_service` for backward compatibility.
    """

    def __init__(
        self,
        model_cache: TTLCache | None = None,
        query_cache: TTLCache | None = None,
    ) -> None:
        self._model_cache: TTLCache = model_cache if model_cache is not None else TTLCache(maxsize=1, ttl=3600)
        self._query_cache: TTLCache = query_cache if query_cache is not None else TTLCache(maxsize=256, ttl=3600)
        self._model_inst: Any = None
        self._model_loaded: bool = False

    def _model_name(self) -> str:
        from .config import get_settings

        return get_settings().embedding_model

    def _model(self) -> Any:
        if not self._model_loaded:
            try:
                from fastembed import TextEmbedding
            except ImportError as e:
                raise EmbeddingUnavailable(
                    "fastembed is not installed. Install with: pip install 'nyaya[semantic]'",
                    hint="Re-install the package with the 'semantic' extra or use the slim Docker image.",
                ) from e
            self._model_inst = TextEmbedding(
                model_name=self._model_name(), providers=list(_PROVIDERS)
            )
            self._model_loaded = True
        return self._model_inst

    def embed_query(self, text: str) -> list[float]:
        """Embed a single query string. Results are cached per query string."""
        k = hashkey(text)
        if k in self._query_cache:
            return self._query_cache[k]
        if not text or not text.strip():
            raise SearchError(
                "Cannot embed an empty query.",
                hint="Provide a non-empty query string to semantic_query.",
            )
        model = self._model()
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
        self._query_cache[k] = vec
        return vec

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts (used by the ingestion CLI). Not cached."""
        model = self._model()
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


# Default singleton backed by the module-level caches, for backward
# compatibility with existing callers that use the module-level functions.
_default_service: EmbeddingService | None = None


def get_default_service() -> EmbeddingService:
    """Return the process-wide default :class:`EmbeddingService`."""
    global _default_service
    if _default_service is None:
        _default_service = EmbeddingService(
            model_cache=_model_cache,
            query_cache=_query_cache,
        )
    return _default_service
