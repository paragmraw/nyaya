"""Embedding + reranker services via the NVIDIA API.

Replaces the v0.1 fastembed/onnxruntime-based local embedder. The v0.2 pipeline
uses the NVIDIA API Catalog (``integrate.api.nvidia.com``) for both embedding
(``nvidia/nemotron-3-embed-1b``, 2048-d) and reranking
(``nvidia/llama-nemotron-rerank-1b-v2``). This works on any platform — including
the Alpine Docker image — with no native wheels.

Both services are CPU-only from the caller's perspective: the heavy compute
happens on NVIDIA's GPUs. Latency is ~400–500 ms per query embed and ~1 s per
50-candidate rerank batch.

Raises:
    EmbeddingUnavailable: the NVIDIA API call failed (network, auth, or model error).
    SearchError: the query is empty or the returned vector has the wrong dimensionality.
"""

from __future__ import annotations

import time

import httpx
from cachetools import TTLCache, cached
from cachetools.keys import hashkey

from .config import get_settings
from .exceptions import EmbeddingUnavailable, SearchError

EXPECTED_DIM = 2048

# Query embeddings: cached 1 hour, 256 entries. The NVIDIA API is stateless;
# caching avoids re-embedding identical queries within the TTL.
_query_cache: TTLCache = TTLCache(maxsize=256, ttl=3600)


def _embed_via_api(texts: list[str], input_type: str) -> list[list[float]]:
    """Embed a batch of texts via the NVIDIA API.

    ``input_type`` is 'query' or 'passage' — the nemotron model uses this to
    apply the appropriate encoding strategy.
    """
    from openai import OpenAI

    settings = get_settings()
    client = OpenAI(
        base_url="https://integrate.api.nvidia.com/v1",
        api_key=settings.nvidia_api_key,
    )
    out: list[list[float]] = []
    for i in range(0, len(texts), 64):
        batch = texts[i : i + 64]
        last_err: Exception | None = None
        for attempt in range(3):
            try:
                r = client.embeddings.create(
                    input=batch,
                    model=settings.embedding_model,
                    encoding_format="float",
                    extra_body={"input_type": input_type, "truncate": "NONE"},
                )
                out.extend([d.embedding for d in r.data])
                last_err = None
                break
            except Exception as e:
                last_err = e
                if attempt < 2:
                    time.sleep(2**attempt)
        if last_err is not None:
            raise EmbeddingUnavailable(
                f"NVIDIA embedding API call failed after 3 attempts: {last_err}",
                hint="Check NVIDIA_API_KEY and network connectivity to integrate.api.nvidia.com.",
            ) from last_err
    return out


@cached(_query_cache)
def embed_query(text: str) -> list[float]:
    """Embed a single query string. Results are cached per query string (1h TTL).

    Raises EmbeddingUnavailable if the API call fails, and SearchError if the
    query is empty or the produced vector has the wrong dimensionality.
    """
    if not text or not text.strip():
        raise SearchError(
            "Cannot embed an empty query.",
            hint="Provide a non-empty query string to semantic_query.",
        )
    vecs = _embed_via_api([text], "query")
    if not vecs:
        raise SearchError(
            "The embedding API returned no vector for the query.",
            hint="This is unexpected; check the NVIDIA API status.",
        )
    vec = vecs[0]
    if len(vec) != EXPECTED_DIM:
        raise SearchError(
            f"Embedding dimension mismatch: expected {EXPECTED_DIM}, got {len(vec)}.",
            hint=f"Re-build embeddings with a {EXPECTED_DIM}-d model, or update EXPECTED_DIM.",
        )
    return vec


def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed a batch of texts as passages (used by the hydration notebook).

    Not cached — the notebook calls this once per corpus.
    """
    if not texts:
        return []
    vecs = _embed_via_api(texts, "passage")
    if vecs:
        first = vecs[0]
        if len(first) != EXPECTED_DIM:
            raise SearchError(
                f"Embedding dimension mismatch: expected {EXPECTED_DIM}, got {len(first)}.",
                hint=f"Re-build embeddings with a {EXPECTED_DIM}-d model.",
            )
    return vecs


def rerank_query(query: str, candidates: list[str]) -> list[float]:
    """Rerank candidate passages against a query via the NVIDIA rerank API.

    Returns a list of relevance logits (higher = more relevant), one per
    candidate, in the same order as ``candidates``.
    """
    if not candidates:
        return []
    settings = get_settings()
    short = settings.reranker_model.split("/", 1)[-1] if settings.reranker_model.startswith("nvidia/") else settings.reranker_model
    url = f"https://ai.api.nvidia.com/v1/retrieval/nvidia/{short}/reranking"
    payload = {
        "model": settings.reranker_model,
        "query": {"text": query},
        "passages": [{"text": c} for c in candidates],
        "truncate": "END",
    }
    last_err: Exception | None = None
    for attempt in range(3):
        try:
            with httpx.Client(timeout=120.0, headers={
                "Authorization": f"Bearer {settings.nvidia_api_key}",
                "Accept": "application/json",
            }) as client:
                r = client.post(url, json=payload)
                r.raise_for_status()
                data = r.json()
                arr = sorted(data["rankings"], key=lambda x: x["index"])
                return [float(x["logit"]) for x in arr]
        except Exception as e:
            last_err = e
            if attempt < 2:
                time.sleep(2**attempt)
    raise EmbeddingUnavailable(
        f"NVIDIA rerank API call failed after 3 attempts: {last_err}",
        hint="Check NVIDIA_API_KEY and network connectivity to ai.api.nvidia.com.",
    ) from last_err


class EmbeddingService:
    """Encapsulates embedding + reranking so they can be injected / tested.

    Accepts optional cache instances so tests can inject fresh caches or fakes.
    A default singleton (backed by the module-level caches) is exposed via
    :func:`get_default_service` for backward compatibility.
    """

    def __init__(
        self,
        query_cache: TTLCache | None = None,
    ) -> None:
        self._query_cache: TTLCache = query_cache if query_cache is not None else TTLCache(maxsize=256, ttl=3600)

    def embed_query(self, text: str) -> list[float]:
        k = hashkey(text)
        if k in self._query_cache:
            return self._query_cache[k]
        if not text or not text.strip():
            raise SearchError(
                "Cannot embed an empty query.",
                hint="Provide a non-empty query string to semantic_query.",
            )
        vecs = _embed_via_api([text], "query")
        if not vecs:
            raise SearchError(
                "The embedding API returned no vector for the query.",
                hint="This is unexpected; check the NVIDIA API status.",
            )
        vec = vecs[0]
        if len(vec) != EXPECTED_DIM:
            raise SearchError(
                f"Embedding dimension mismatch: expected {EXPECTED_DIM}, got {len(vec)}.",
                hint=f"Re-build embeddings with a {EXPECTED_DIM}-d model, or update EXPECTED_DIM.",
            )
        self._query_cache[k] = vec
        return vec

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return embed_texts(texts)

    def rerank(self, query: str, candidates: list[str]) -> list[float]:
        return rerank_query(query, candidates)


# Default singleton for backward compatibility with existing callers.
_default_service: EmbeddingService | None = None


def get_default_service() -> EmbeddingService:
    """Return the process-wide default :class:`EmbeddingService`."""
    global _default_service
    if _default_service is None:
        _default_service = EmbeddingService(query_cache=_query_cache)
    return _default_service
