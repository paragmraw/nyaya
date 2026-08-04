"""Runtime configuration from environment variables.

Environment variables
---------------------
``DATABASE_URL`` (required)
    Postgres/Supabase connection string.
``PORT`` (optional, default 8000)
    HTTP port for the uvicorn server.
``NYAYA_POOL_MIN`` / ``NYAYA_POOL_MAX`` (optional, defaults 1 / 8)
    Connection-pool size bounds for the read layer.
``NYAYA_POOL_TIMEOUT`` (optional, default 3.0 seconds)
    Seconds to wait when acquiring a pooled connection before failing.
``NYAYA_STATEMENT_TIMEOUT`` (optional, default 15000 ms)
    Postgres ``statement_timeout`` applied to each pooled connection.
``NYAYA_LOG_LEVEL`` (optional, default INFO)
    Logging level for the root logger.
``NYAYA_EMBEDDING_MODEL`` (optional)
    Override the fastembed model name (default BAAI/bge-large-en-v1.5).
    Must produce 1024-d vectors to match the schema.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from dotenv import load_dotenv

from .exceptions import ConfigurationError

load_dotenv()


def _required(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise ConfigurationError(
            f"Required environment variable {name!r} is not set. "
            "Copy .env.example to .env and fill in the values."
        )
    return val


def _int_env(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigurationError(f"{name!r} must be an integer, got {raw!r}") from None


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        raise ConfigurationError(f"{name!r} must be a number, got {raw!r}") from None


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
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    port_raw = os.environ.get("PORT", "8000")
    try:
        port = int(port_raw)
    except ValueError:
        raise ConfigurationError(f"PORT must be an integer, got {port_raw!r}") from None

    if not 1 <= port <= 65535:
        raise ConfigurationError(f"PORT must be in range 1-65535, got {port}")

    pool_min = _int_env("NYAYA_POOL_MIN", 1)
    pool_max = _int_env("NYAYA_POOL_MAX", 8)
    if pool_min > pool_max:
        raise ConfigurationError(
            f"NYAYA_POOL_MIN ({pool_min}) must be <= NYAYA_POOL_MAX ({pool_max})"
        )

    return Settings(
        database_url=_required("DATABASE_URL"),
        port=port,
        pool_min=pool_min,
        pool_max=pool_max,
        pool_timeout=_float_env("NYAYA_POOL_TIMEOUT", 3.0),
        statement_timeout_ms=_int_env("NYAYA_STATEMENT_TIMEOUT", 15000),
        log_level=os.environ.get("NYAYA_LOG_LEVEL", "INFO"),
        embedding_model=os.environ.get("NYAYA_EMBEDDING_MODEL", "BAAI/bge-large-en-v1.5"),
    )


@dataclass(frozen=True)
class RateLimitSettings:
    """Rate-limit configuration. Edit defaults here to tune; not env-driven.

    All rates are requests-per-minute per client IP.
    """

    # Read tools (get_section, search_law, etc.): generous for a public corpus.
    read_per_min: int = 120
    # Embedding tools (semantic_query, hybrid_search): expensive pgvector queries.
    embedding_per_min: int = 10
    # Maximum request body size in bytes (1 MB).
    body_size_max_bytes: int = 1_048_576


@lru_cache(maxsize=1)
def get_rate_limit_settings() -> RateLimitSettings:
    return RateLimitSettings()
