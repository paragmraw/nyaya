"""Zero-Supabase runtime configuration for nyaya v2.0.

Importing this module or ``nyaya`` has zero I/O side effects: ``get_settings()``
is evaluated lazily only when requested by the runtime, CLI, or HTTP server.
Neither ``DATABASE_URL`` nor ``NVIDIA_API_KEY`` is required by the MCP server.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit, urlunsplit

from .exceptions import ConfigurationError

BucketUrlStyle = Literal["vhost", "path"]

PORT_DEFAULT = 8000
LOG_LEVEL = "INFO"
WEB_OUT = "web/out"

REDIS_URL: str | None = None

RATE_READ_PER_MIN = 120
RATE_MCP_PER_MIN = 30
RATE_ANALYTICS_PER_MIN = 20
RATE_CHAT_PER_MIN = 15
RATE_BODY_MAX_BYTES = 1_048_576

DOTENV_ENV_VAR = "NYAYA_DOTENV"
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _is_truthy(val: str | None) -> bool:
    return (val or "").strip().lower() in _TRUTHY


def _default_data_dir() -> Path:
    app_data = Path("/app/data")
    if app_data.is_dir():
        return app_data
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "nyaya" / "data"


def _default_cache_dir() -> Path:
    tmp_cache = Path("/tmp/nyaya-cache")
    if Path("/app/data").is_dir() or tmp_cache.is_dir():
        return tmp_cache
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    return base / "nyaya" / "cache"


def _default_duckdb_ext_dir() -> Path | None:
    app_ext = Path("/app/duckdb_ext")
    if app_ext.is_dir():
        return app_ext
    return None


def _maybe_load_dotenv() -> None:
    """Load a local ``.env`` file only when ``NYAYA_DOTENV`` is truthy."""
    if not _is_truthy(os.environ.get(DOTENV_ENV_VAR)):
        return
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv()


def _redact_url(url: str) -> str:
    parts = urlsplit(url)
    if parts.hostname and (parts.username or parts.password):
        netloc = f"***@{parts.hostname}"
        if parts.port:
            netloc += f":{parts.port}"
        return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
    return url


@dataclass(frozen=True)
class Settings:
    data_dir: Path
    cache_dir: Path
    bucket_endpoint: str | None
    bucket_name: str | None
    bucket_access_key_id: str | None
    bucket_secret_access_key: str | None
    bucket_region: str
    bucket_url_style: BucketUrlStyle
    manifest_url: str | None
    commercial_safe_only: bool
    offline_only: bool
    duckdb_ext_dir: Path | None
    log_level: str
    port: int
    redis_url: str | None
    trusted_proxy_hops: int

    @property
    def has_bucket_config(self) -> bool:
        return bool(self.bucket_endpoint and self.bucket_name)

    def as_dict(self) -> dict[str, Any]:
        return {
            "data_dir": str(self.data_dir),
            "cache_dir": str(self.cache_dir),
            "bucket_endpoint": self.bucket_endpoint,
            "bucket_name": self.bucket_name,
            "bucket_access_key_id": "***" if self.bucket_access_key_id else None,
            "bucket_secret_access_key": "***" if self.bucket_secret_access_key else None,
            "bucket_region": self.bucket_region,
            "bucket_url_style": self.bucket_url_style,
            "manifest_url": self.manifest_url,
            "commercial_safe_only": self.commercial_safe_only,
            "offline_only": self.offline_only,
            "duckdb_ext_dir": str(self.duckdb_ext_dir) if self.duckdb_ext_dir else None,
            "log_level": self.log_level,
            "port": self.port,
            "redis_url": _redact_url(self.redis_url) if self.redis_url else None,
            "trusted_proxy_hops": self.trusted_proxy_hops,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _maybe_load_dotenv()

    port_raw = os.environ.get("PORT", str(PORT_DEFAULT))
    try:
        port = int(port_raw)
    except ValueError:
        raise ConfigurationError(f"PORT must be an integer, got {port_raw!r}") from None
    if not 1 <= port <= 65535:
        raise ConfigurationError(f"PORT must be in range 1-65535, got {port}") from None

    hops_raw = os.environ.get("NYAYA_TRUSTED_PROXY_HOPS", "1")
    try:
        trusted_proxy_hops = int(hops_raw)
    except ValueError:
        raise ConfigurationError(
            f"NYAYA_TRUSTED_PROXY_HOPS must be an integer, got {hops_raw!r}"
        ) from None
    if trusted_proxy_hops < 0:
        raise ConfigurationError(
            f"NYAYA_TRUSTED_PROXY_HOPS must be >= 0, got {trusted_proxy_hops}"
        ) from None

    url_style_raw = (os.environ.get("NYAYA_BUCKET_URL_STYLE") or "vhost").strip().lower()
    if url_style_raw not in ("vhost", "path"):
        raise ConfigurationError(
            f"NYAYA_BUCKET_URL_STYLE must be 'vhost' or 'path', got {url_style_raw!r}"
        )
    bucket_url_style: BucketUrlStyle = "vhost" if url_style_raw == "vhost" else "path"

    data_dir_raw = os.environ.get("NYAYA_DATA_DIR")
    data_dir = Path(data_dir_raw) if data_dir_raw else _default_data_dir()

    cache_dir_raw = os.environ.get("NYAYA_CACHE_DIR")
    cache_dir = Path(cache_dir_raw) if cache_dir_raw else _default_cache_dir()

    ext_dir_raw = os.environ.get("NYAYA_DUCKDB_EXT_DIR")
    duckdb_ext_dir = Path(ext_dir_raw) if ext_dir_raw else _default_duckdb_ext_dir()

    return Settings(
        data_dir=data_dir,
        cache_dir=cache_dir,
        bucket_endpoint=os.environ.get("NYAYA_BUCKET_ENDPOINT") or None,
        bucket_name=os.environ.get("NYAYA_BUCKET_NAME") or None,
        bucket_access_key_id=os.environ.get("NYAYA_BUCKET_ACCESS_KEY_ID") or None,
        bucket_secret_access_key=os.environ.get("NYAYA_BUCKET_SECRET_ACCESS_KEY") or None,
        bucket_region=(os.environ.get("NYAYA_BUCKET_REGION") or "auto").strip() or "auto",
        bucket_url_style=bucket_url_style,
        manifest_url=os.environ.get("NYAYA_MANIFEST_URL") or None,
        commercial_safe_only=_is_truthy(os.environ.get("NYAYA_COMMERCIAL_SAFE_ONLY")),
        offline_only=_is_truthy(os.environ.get("NYAYA_OFFLINE_ONLY")),
        duckdb_ext_dir=duckdb_ext_dir,
        log_level=(os.environ.get("NYAYA_LOG_LEVEL") or LOG_LEVEL).strip().upper(),
        port=port,
        redis_url=os.environ.get("REDIS_URL", REDIS_URL) or None,
        trusted_proxy_hops=trusted_proxy_hops,
    )


@dataclass(frozen=True)
class RateLimitSettings:
    """Rate-limit configuration (requests-per-minute per client IP)."""

    read_per_min: int = RATE_READ_PER_MIN
    mcp_per_min: int = RATE_MCP_PER_MIN
    embedding_per_min: int = RATE_MCP_PER_MIN
    analytics_per_min: int = RATE_ANALYTICS_PER_MIN
    chat_per_min: int = RATE_CHAT_PER_MIN
    body_size_max_bytes: int = RATE_BODY_MAX_BYTES
    trusted_proxy_hops: int = 1


@lru_cache(maxsize=1)
def get_rate_limit_settings() -> RateLimitSettings:
    hops_raw = os.environ.get("NYAYA_TRUSTED_PROXY_HOPS", "1")
    try:
        hops = max(0, int(hops_raw))
    except ValueError:
        hops = 1
    return RateLimitSettings(trusted_proxy_hops=hops)
