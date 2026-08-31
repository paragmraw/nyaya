"""Runtime configuration for nyaya.

Environment variables read:

``DATABASE_URL`` (required)
    Postgres/Supabase connection string.
``NVIDIA_API_KEY`` (required)
    NVIDIA API Catalog key for the embedder + reranker (integrate.api.nvidia.com).
    Previously only required by the chat sub-app; now required by the MCP server
    itself since retrieval is embedding-based.
``PORT`` (optional, default 8000)
    HTTP port for the uvicorn server. Railway injects this automatically.

Everything else is a Python constant in this module.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from .exceptions import ConfigurationError

# ---------------------------------------------------------------------------
# Tunable constants (edit here; no env vars).
# ---------------------------------------------------------------------------

PORT_DEFAULT = 8000

# Connection-pool size bounds for the read layer.
POOL_MIN = 1
POOL_MAX = 8
POOL_TIMEOUT = 3.0

# Postgres ``statement_timeout`` applied to each pooled connection (ms).
STATEMENT_TIMEOUT_MS = 15000

LOG_LEVEL = "INFO"

# NVIDIA API embedding + reranker model ids (used by embeddings.py).
EMBEDDING_MODEL = "nvidia/nemotron-3-embed-1b"
RERANKER_MODEL = "nvidia/llama-nemotron-rerank-1b-v2"
EMBEDDING_DIM = 2048

# Total wall-clock budget for one rerank call (attempts + backoff + HTTP).
# Without a deadline, 3 attempts x 120s httpx timeout + exponential backoff
# can hang ~6 minutes worst case; beyond this deadline the reranker is
# abandoned and search falls back to raw ANN scores (reranker_unavailable).
RERANK_DEADLINE_S = 15.0

# Default text-snippet length for list-style responses (matches the
# historical ``left(d.text, 300)`` used by semantic search).
SNIPPET_CHARS = 300

# Redis connection string for distributed rate limiting. When set,
# rate-limit counters are shared across all workers. When None, falls back
# to in-memory (single-worker only). Read from the REDIS_URL env var at
# get_settings() time; the module constant is the default.
REDIS_URL: str | None = None

# Directory containing the built Next.js static export, served at /.
WEB_OUT = "web/out"

# Rate-limit thresholds (requests-per-minute per client IP).
RATE_READ_PER_MIN = 120
RATE_MCP_PER_MIN = 30
RATE_CHAT_PER_MIN = 15
RATE_BODY_MAX_BYTES = 1_048_576


def _required(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise ConfigurationError(
            f"Required environment variable {name!r} is not set. "
            "Copy .env.example to .env and fill in the values."
        )
    return val


def _redact_url(url: str) -> str:
    from urllib.parse import urlsplit, urlunsplit

    parts = urlsplit(url)
    if parts.hostname and (parts.username or parts.password):
        netloc = f"***@{parts.hostname}"
        if parts.port:
            netloc += f":{parts.port}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return url


@dataclass(frozen=True)
class Settings:
    database_url: str
    nvidia_api_key: str
    port: int
    pool_min: int
    pool_max: int
    pool_timeout: float
    statement_timeout_ms: int
    log_level: str
    embedding_model: str
    reranker_model: str
    embedding_dim: int
    redis_url: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "database_url": _redact_url(self.database_url),
            "nvidia_api_key": "***",
            "port": self.port,
            "pool_min": self.pool_min,
            "pool_max": self.pool_max,
            "pool_timeout": self.pool_timeout,
            "statement_timeout_ms": self.statement_timeout_ms,
            "log_level": self.log_level,
            "embedding_model": self.embedding_model,
            "reranker_model": self.reranker_model,
            "embedding_dim": self.embedding_dim,
            "redis_url": _redact_url(self.redis_url) if self.redis_url else None,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    port_raw = os.environ.get("PORT", str(PORT_DEFAULT))
    try:
        port = int(port_raw)
    except ValueError:
        raise ConfigurationError(f"PORT must be an integer, got {port_raw!r}") from None

    if not 1 <= port <= 65535:
        raise ConfigurationError(f"PORT must be in range 1-65535, got {port}") from None

    if POOL_MIN > POOL_MAX:
        raise ConfigurationError(f"POOL_MIN ({POOL_MIN}) must be <= POOL_MAX ({POOL_MAX})")

    return Settings(
        database_url=_required("DATABASE_URL"),
        nvidia_api_key=_required("NVIDIA_API_KEY"),
        port=port,
        pool_min=POOL_MIN,
        pool_max=POOL_MAX,
        pool_timeout=POOL_TIMEOUT,
        statement_timeout_ms=STATEMENT_TIMEOUT_MS,
        log_level=LOG_LEVEL,
        embedding_model=EMBEDDING_MODEL,
        reranker_model=RERANKER_MODEL,
        embedding_dim=EMBEDDING_DIM,
        redis_url=os.environ.get("REDIS_URL", REDIS_URL),
    )


@dataclass(frozen=True)
class RateLimitSettings:
    """Rate-limit configuration (requests-per-minute per client IP)."""

    read_per_min: int = RATE_READ_PER_MIN
    embedding_per_min: int = RATE_MCP_PER_MIN
    chat_per_min: int = RATE_CHAT_PER_MIN
    body_size_max_bytes: int = RATE_BODY_MAX_BYTES


@lru_cache(maxsize=1)
def get_rate_limit_settings() -> RateLimitSettings:
    return RateLimitSettings()
