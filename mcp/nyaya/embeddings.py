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

import threading
import time
from typing import TYPE_CHECKING

import httpx
from cachetools import TTLCache, cached

from .config import RERANK_DEADLINE_S, get_settings
from .exceptions import EmbeddingUnavailable, SearchError

if TYPE_CHECKING:
    from openai import OpenAI

EXPECTED_DIM = 2048

# Query embeddings: cached 1 hour, 256 entries. The NVIDIA API is stateless;
# caching avoids re-embedding identical queries within the TTL.
_query_cache: TTLCache = TTLCache(maxsize=256, ttl=3600)

_NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"

# Lazily-built process-wide singletons (double-checked under a lock, matching
# the ``_pool_lock`` pattern in db.py). The OpenAI SDK client keeps its own
# connection pool, and httpx.Client reuses connections — constructing them per
# call wasted TLS handshakes and pooled sockets.
_openai_client: OpenAI | None = None
_http_client: httpx.Client | None = None
_client_lock = threading.Lock()


def _get_openai_client() -> OpenAI:
    """Return the shared OpenAI API client, building it on first use."""
    global _openai_client
    if _openai_client is None:
        with _client_lock:
            if _openai_client is None:
                from openai import OpenAI

                settings = get_settings()
                _openai_client = OpenAI(
                    base_url=_NVIDIA_BASE_URL,
                    api_key=settings.nvidia_api_key,
                )
    return _openai_client


def _get_http_client() -> httpx.Client:
    """Return the shared httpx client for the rerank API, built on first use.

    Per-request timeouts (see :func:`rerank_query`) override the client
    default, so a single long-lived client is safe.
    """
    global _http_client
    if _http_client is None:
        with _client_lock:
            if _http_client is None:
                settings = get_settings()
                _http_client = httpx.Client(
                    timeout=RERANK_DEADLINE_S,
                    headers={
                        "Authorization": f"Bearer {settings.nvidia_api_key}",
                        "Accept": "application/json",
                    },
                )
    return _http_client


def _embed_via_api(texts: list[str], input_type: str) -> list[list[float]]:
    """Embed a batch of texts via the NVIDIA API.

    ``input_type`` is 'query' or 'passage' — the nemotron model uses this to
    apply the appropriate encoding strategy.
    """
    settings = get_settings()
    client = _get_openai_client()
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


def rerank_query(query: str, candidates: list[str]) -> list[float]:
    """Rerank candidate passages against a query via the NVIDIA rerank API.

    Returns a list of relevance logits (higher = more relevant), one per
    candidate, in the same order as ``candidates``.

    The whole operation runs under a total deadline of ``RERANK_DEADLINE_S``
    seconds: each attempt's HTTP timeout is clamped to the remaining budget
    and retries are abandoned once it expires, so a stalled reranker fails
    into the ``reranker_unavailable`` fallback within the deadline instead of
    hanging for 3 attempts x 120s + backoff (~6 minutes worst case).
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
    deadline = time.monotonic() + RERANK_DEADLINE_S
    client = _get_http_client()
    last_err: Exception | None = None
    for attempt in range(3):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        try:
            r = client.post(url, json=payload, timeout=remaining)
            r.raise_for_status()
            data = r.json()
            arr = sorted(data["rankings"], key=lambda x: x["index"])
            return [float(x["logit"]) for x in arr]
        except Exception as e:
            last_err = e
            if attempt < 2:
                delay = min(2**attempt, max(0.0, deadline - time.monotonic()))
                if delay <= 0:
                    break
                time.sleep(delay)
    raise EmbeddingUnavailable(
        f"NVIDIA rerank API call failed within the {RERANK_DEADLINE_S:.0f}s deadline: {last_err}",
        hint="Check NVIDIA_API_KEY and network connectivity to ai.api.nvidia.com.",
    ) from last_err
