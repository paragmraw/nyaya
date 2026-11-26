"""Opt-in hybrid retrieval: zero-dependency legal n-gram embeddings, OpenAI-compatible
HTTP embeddings, and lexical + vector Reciprocal Rank Fusion (RRF).
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

import httpx

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Curated Indian legal synonym / concept clusters for zero-dependency offline recall.
_LEGAL_SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("anticipatory bail", "pre arrest bail", "pre-arrest bail", "apprehending arrest", "438", "482"),
    ("murder", "culpable homicide", "homicide", "302", "103"),
    ("cheating", "fraud", "dishonestly inducing", "420", "318"),
    ("defamation", "libel", "slander", "499", "356"),
    ("electronic evidence", "digital record", "electronic record", "65b", "63"),
    ("privacy", "personal liberty", "life and liberty", "article 21", "puttaswamy"),
    ("free speech", "freedom of speech", "expression", "article 19", "19(1)(a)"),
    ("basic structure", "constitutional amendment", "article 368", "kesavananda"),
    ("arbitration", "arbitral award", "arbitral tribunal", "arbitration agreement"),
    ("cheque bounce", "cheque dishonour", "dishonour of cheque", "negotiable instruments", "138"),
    ("quashing", "inherent powers", "abuse of process", "528"),
    ("insolvency", "resolution professional", "cirp", "ibc", "nclt", "nclat", "creditors"),
    ("consumer", "deficiency in service", "unfair trade practice", "ncdrc", "scdrc", "dcdrc"),
    ("dowry death", "cruelty", "498a", "304b", "85", "80"),
    ("maintenance", "alimony", "125", "144"),
    ("income tax", "assessment", "reassessment", "escaped assessment", "143", "147", "148", "270", "279", "280"),
    ("environment", "pollution", "forest", "green tribunal", "ngt"),
)

_SYNONYM_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (
        f"__legal_concept_{idx}__",
        re.compile(
            "(?:" + "|".join(rf"\b{re.escape(term)}\b" for term in group) + ")",
            re.IGNORECASE,
        ),
    )
    for idx, group in enumerate(_LEGAL_SYNONYM_GROUPS)
)


@runtime_checkable
class EmbeddingProvider(Protocol):
    """Protocol for dense embedding providers used by opt-in hybrid search."""

    dim: int

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    """Compute cosine similarity between two equal-length float vectors."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


class HashedNgramEmbeddingProvider:
    """Pure-Python hashed word/char n-gram + legal-synonym embedding provider (zero external deps)."""

    def __init__(self, dim: int = 256, *, include_legal_synonyms: bool = True) -> None:
        if dim <= 0:
            raise ValueError(f"Embedding dimension must be positive, got {dim}.")
        self.dim = int(dim)
        self.include_legal_synonyms = include_legal_synonyms

    def _hash_feature(self, vec: list[float], feature: str, weight: float) -> None:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        val = int.from_bytes(digest, "little", signed=False)
        idx = val % self.dim
        sign = 1.0 if ((val >> 63) & 1) == 0 else -1.0
        vec[idx] += sign * weight

    def _embed_one(self, text: str) -> list[float]:
        cleaned = (text or "").strip().lower()
        if not cleaned:
            return [0.0] * self.dim

        vec = [0.0] * self.dim
        tokens = _TOKEN_RE.findall(cleaned)
        for tok in tokens:
            self._hash_feature(vec, f"w:{tok}", 1.5)
            padded = f"<{tok}>"
            for n in (3, 4, 5):
                if len(padded) >= n:
                    for i in range(len(padded) - n + 1):
                        self._hash_feature(vec, f"c{n}:{padded[i : i + n]}", 0.5)

        for i in range(len(tokens) - 1):
            self._hash_feature(vec, f"bg:{tokens[i]}_{tokens[i + 1]}", 1.2)

        if self.include_legal_synonyms:
            for concept_tag, pattern in _SYNONYM_PATTERNS:
                if pattern.search(cleaned):
                    self._hash_feature(vec, concept_tag, 2.5)

        norm = math.sqrt(sum(x * x for x in vec))
        if norm == 0.0:
            return [0.0] * self.dim
        return [x / norm for x in vec]

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed_one(text)


class OpenAICompatibleEmbeddingProvider:
    """OpenAI-compatible ``/embeddings`` HTTP embedding client using ``httpx``."""

    def __init__(
        self,
        endpoint: str,
        model: str,
        api_key: str | None = None,
        *,
        dim: int = 1536,
        timeout: float = 15.0,
        client: httpx.Client | None = None,
    ) -> None:
        clean_ep = endpoint.strip().rstrip("/")
        if not clean_ep:
            raise ValueError("Embedding endpoint URL must be non-empty.")
        self.endpoint = clean_ep if clean_ep.endswith("/embeddings") else f"{clean_ep}/embeddings"
        self.model = model.strip()
        if not self.model:
            raise ValueError("Embedding model name must be non-empty.")
        self._api_key = api_key
        self.dim = int(dim)
        self._owns_client = client is None
        self._client = client or httpx.Client(timeout=timeout)

    def __repr__(self) -> str:
        return f"OpenAICompatibleEmbeddingProvider(endpoint={self.endpoint!r}, model={self.model!r})"

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        items = [str(t) for t in texts]
        if not items:
            return []
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        resp = self._client.post(
            self.endpoint,
            headers=headers,
            json={"model": self.model, "input": items},
        )
        resp.raise_for_status()
        payload = resp.json()
        data = sorted(payload.get("data") or [], key=lambda d: int(d.get("index", 0)))
        vectors = [[float(x) for x in entry["embedding"]] for entry in data]
        if vectors and vectors[0]:
            self.dim = len(vectors[0])
        return vectors

    def embed_query(self, text: str) -> list[float]:
        vecs = self.embed([text])
        return vecs[0] if vecs else [0.0] * self.dim

    def close(self) -> None:
        if self._owns_client:
            self._client.close()


def fuse_lexical_and_vector_rrf(
    lexical_hits: Sequence[Mapping[str, Any]],
    vector_hits: Sequence[Mapping[str, Any]],
    *,
    id_key: str = "chunk_id",
    k: int = 60,
    alpha: float = 0.5,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    """Fuse lexical BM25 hits and dense vector hits using weighted Reciprocal Rank Fusion."""
    alpha_clamped = max(0.0, min(1.0, float(alpha)))
    lex_weight = 1.0 - alpha_clamped
    vec_weight = alpha_clamped
    rrf_k = max(1, int(k))

    scores: dict[str, float] = {}
    records: dict[str, dict[str, Any]] = {}

    def _item_id(row: Mapping[str, Any], fallback_idx: int) -> str:
        raw = row.get(id_key) or row.get("doc_id") or row.get("case_id")
        return str(raw) if raw is not None else f"__row_{fallback_idx}"

    for rank, hit in enumerate(lexical_hits, start=1):
        key = _item_id(hit, rank)
        if key not in records:
            records[key] = dict(hit)
        scores[key] = scores.get(key, 0.0) + (lex_weight / (rrf_k + rank))

    for rank, hit in enumerate(vector_hits, start=1):
        key = _item_id(hit, rank)
        if key not in records:
            records[key] = dict(hit)
        scores[key] = scores.get(key, 0.0) + (vec_weight / (rrf_k + rank))

    ordered = sorted(scores.keys(), key=lambda key: scores[key], reverse=True)
    fused: list[dict[str, Any]] = []
    for key in ordered:
        item = records[key]
        item["_rrf_score"] = round(scores[key], 8)
        fused.append(item)
    if limit is not None:
        return fused[: max(1, int(limit))]
    return fused
