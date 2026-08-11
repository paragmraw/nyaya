"""Runtime configuration for nyaya.

Only two environment variables are read:

``DATABASE_URL`` (required)
    Postgres/Supabase connection string.
``PORT`` (optional, default 8000)
    HTTP port for the uvicorn server. Railway injects this automatically.

Everything else is a Python constant in this module. Edit this file to tune
pool sizing, statement timeouts, log level, embedding model, rate limits,
or the Redis URL.
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
# Seconds to wait when acquiring a pooled connection before failing.
POOL_TIMEOUT = 3.0

# Postgres ``statement_timeout`` applied to each pooled connection (ms).
# 0 disables. Protects against slow ts_headline calls over very large
# judgment text. Default: 15000 (15s).
STATEMENT_TIMEOUT_MS = 15000

# Root log level (DEBUG, INFO, WARNING, ERROR).
LOG_LEVEL = "INFO"

# fastembed model name. Must produce 1024-d vectors to match the schema.
EMBEDDING_MODEL = "BAAI/bge-large-en-v1.5"

# Redis connection string for distributed rate limiting. When set,
# rate-limit counters are shared across all workers for strict global
# limits. When None, rate limiting falls back to in-memory (single-worker
# only). Set to a "redis://..." URL to enable.
REDIS_URL: str | None = None

# Directory containing the built Next.js static export, served at / by
# Starlette StaticFiles. Relative to the process CWD.
WEB_OUT = "web/out"

# Rate-limit thresholds (requests-per-minute per client IP).
RATE_READ_PER_MIN = 120
RATE_MCP_PER_MIN = 30
RATE_CHAT_PER_MIN = 15
# Maximum request body size in bytes (1 MB).
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
    """Strip userinfo from a database URL for safe logging.

    Handles URL-encoded passwords (e.g. ``%40`` for ``@``) via
    ``urllib.parse``: split the netloc and reassemble with a redacted marker.

    ``postgresql://user:secret@host:5432/db`` -> ``postgresql://***@host:5432/db``

    URLs without userinfo pass through unchanged.
    """
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
    port: int
    pool_min: int
    pool_max: int
    pool_timeout: float
    statement_timeout_ms: int
    log_level: str
    embedding_model: str
    redis_url: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "database_url": _redact_url(self.database_url),
            "port": self.port,
            "pool_min": self.pool_min,
            "pool_max": self.pool_max,
            "pool_timeout": self.pool_timeout,
            "statement_timeout_ms": self.statement_timeout_ms,
            "log_level": self.log_level,
            "embedding_model": self.embedding_model,
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
        raise ConfigurationError(
            f"POOL_MIN ({POOL_MIN}) must be <= POOL_MAX ({POOL_MAX})"
        )

    return Settings(
        database_url=_required("DATABASE_URL"),
        port=port,
        pool_min=POOL_MIN,
        pool_max=POOL_MAX,
        pool_timeout=POOL_TIMEOUT,
        statement_timeout_ms=STATEMENT_TIMEOUT_MS,
        log_level=LOG_LEVEL,
        embedding_model=EMBEDDING_MODEL,
        redis_url=REDIS_URL,
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
