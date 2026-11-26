"""Unit tests for the v2 stateless runtime config loader."""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from nyaya import config
from nyaya.exceptions import ConfigurationError

_ALL_NYAYA_ENV_VARS = (
    "DATABASE_URL",
    "NVIDIA_API_KEY",
    "NYAYA_DATA_DIR",
    "NYAYA_CACHE_DIR",
    "NYAYA_BUCKET_ENDPOINT",
    "NYAYA_BUCKET_NAME",
    "NYAYA_BUCKET_ACCESS_KEY_ID",
    "NYAYA_BUCKET_SECRET_ACCESS_KEY",
    "NYAYA_BUCKET_REGION",
    "NYAYA_BUCKET_URL_STYLE",
    "NYAYA_MANIFEST_URL",
    "NYAYA_COMMERCIAL_SAFE_ONLY",
    "NYAYA_OFFLINE_ONLY",
    "NYAYA_DUCKDB_EXT_DIR",
    "NYAYA_LOG_LEVEL",
    "NYAYA_TRUSTED_PROXY_HOPS",
    "NYAYA_ENABLE_OCR",
    "NYAYA_HYBRID_SEARCH",
    "NYAYA_EMBEDDING_ENDPOINT",
    "NYAYA_EMBEDDING_MODEL",
    "NYAYA_LIVE_DOCKET_API_URL",
    "NYAYA_LIVE_DOCKET_URL",
    "NYAYA_LIVE_DOCKET_API_KEY",
    "PORT",
    "REDIS_URL",
)


def _clear_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in _ALL_NYAYA_ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    config.get_settings.cache_clear()


def test_settings_zero_env_vars_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    """v2 MCP requires ZERO DATABASE_URL and ZERO NVIDIA_API_KEY."""
    _clear_env(monkeypatch)
    s = config.get_settings()
    assert s.port == config.PORT_DEFAULT
    assert s.bucket_region == "auto"
    assert s.bucket_url_style == "vhost"
    assert s.commercial_safe_only is False
    assert s.offline_only is False
    assert s.trusted_proxy_hops == 1
    assert isinstance(s.data_dir, Path)
    assert isinstance(s.cache_dir, Path)
    assert not hasattr(s, "database_url")
    assert not hasattr(s, "nvidia_api_key")
    assert s.enable_ocr is False
    assert s.hybrid_search is False
    assert s.embedding_endpoint is None
    assert s.embedding_model is None
    assert s.live_docket_api_url is None
    assert s.live_docket_api_key is None


def test_bucket_and_flag_env_overrides(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    _clear_env(monkeypatch)
    data_dir = tmp_path / "data"
    cache_dir = tmp_path / "cache"
    ext_dir = tmp_path / "ext"
    monkeypatch.setenv("NYAYA_DATA_DIR", str(data_dir))
    monkeypatch.setenv("NYAYA_CACHE_DIR", str(cache_dir))
    monkeypatch.setenv("NYAYA_DUCKDB_EXT_DIR", str(ext_dir))
    monkeypatch.setenv("NYAYA_BUCKET_ENDPOINT", "https://t3.storageapi.dev")
    monkeypatch.setenv("NYAYA_BUCKET_NAME", "nyaya-prod")
    monkeypatch.setenv("NYAYA_BUCKET_ACCESS_KEY_ID", "tid_key")
    monkeypatch.setenv("NYAYA_BUCKET_SECRET_ACCESS_KEY", "tsec_supersecret")
    monkeypatch.setenv("NYAYA_BUCKET_REGION", "auto")
    monkeypatch.setenv("NYAYA_BUCKET_URL_STYLE", "path")
    monkeypatch.setenv("NYAYA_MANIFEST_URL", "https://huggingface.co/datasets/nyaya/manifest.json")
    monkeypatch.setenv("NYAYA_COMMERCIAL_SAFE_ONLY", "1")
    monkeypatch.setenv("NYAYA_OFFLINE_ONLY", "true")
    monkeypatch.setenv("NYAYA_TRUSTED_PROXY_HOPS", "2")
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setenv("REDIS_URL", "redis://user:redispass@localhost:6379/0")

    s = config.get_settings()
    assert s.data_dir == data_dir
    assert s.cache_dir == cache_dir
    assert s.duckdb_ext_dir == ext_dir
    assert s.bucket_endpoint == "https://t3.storageapi.dev"
    assert s.bucket_name == "nyaya-prod"
    assert s.bucket_url_style == "path"
    assert s.commercial_safe_only is True
    assert s.offline_only is True
    assert s.trusted_proxy_hops == 2
    assert s.port == 9000


def test_as_dict_redacts_bucket_secret_and_redis(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("NYAYA_BUCKET_ACCESS_KEY_ID", "tid_mykey")
    monkeypatch.setenv("NYAYA_BUCKET_SECRET_ACCESS_KEY", "tsec_topsecret_value")
    monkeypatch.setenv("REDIS_URL", "redis://default:s3cr3t@redis.internal:6379/0")

    d = config.get_settings().as_dict()
    assert d["bucket_secret_access_key"] == "***"
    assert d["bucket_access_key_id"] == "***"
    assert "tsec_topsecret_value" not in str(d)
    assert "s3cr3t" not in d["redis_url"]
    assert "***" in d["redis_url"]


def test_as_dict_uncredentialed_redis_preserved(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    d = config.get_settings().as_dict()
    assert d["redis_url"] == "redis://localhost:6379/0"


@pytest.mark.parametrize("bad_port", ["abc", "0", "65536", "-1"])
def test_invalid_port_raises(monkeypatch: pytest.MonkeyPatch, bad_port: str) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("PORT", bad_port)
    with pytest.raises(ConfigurationError):
        config.get_settings()


def test_invalid_bucket_url_style_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("NYAYA_BUCKET_URL_STYLE", "invalid-style")
    with pytest.raises(ConfigurationError):
        config.get_settings()


def test_invalid_trusted_proxy_hops_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("NYAYA_TRUSTED_PROXY_HOPS", "-1")
    with pytest.raises(ConfigurationError):
        config.get_settings()


def test_rate_limit_constants() -> None:
    rl = config.get_rate_limit_settings()
    assert rl.read_per_min == config.RATE_READ_PER_MIN
    assert rl.mcp_per_min == config.RATE_MCP_PER_MIN
    assert rl.analytics_per_min == config.RATE_ANALYTICS_PER_MIN == 20
    assert rl.chat_per_min == config.RATE_CHAT_PER_MIN
    assert rl.body_size_max_bytes == config.RATE_BODY_MAX_BYTES


@pytest.fixture
def _dotenv_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    env_file = tmp_path / ".env"
    env_file.write_text("PORT=9911\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NYAYA_DOTENV", raising=False)
    monkeypatch.delenv("PORT", raising=False)

    calls: list[tuple] = []

    def fake_load_dotenv(*args, **kwargs):
        calls.append((args, kwargs))
        for line in env_file.read_text(encoding="utf-8").splitlines():
            if "=" in line:
                key, _, value = line.partition("=")
                monkeypatch.setenv(key.strip(), value.strip())

    fake = types.ModuleType("dotenv")
    fake.load_dotenv = fake_load_dotenv  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "dotenv", fake)

    config.get_settings.cache_clear()
    try:
        yield calls
    finally:
        config.get_settings.cache_clear()


def test_dotenv_not_loaded_by_default(_dotenv_env) -> None:
    s = config.get_settings()
    assert _dotenv_env == []
    assert s.port != 9911


@pytest.mark.parametrize("value", ["1", "true", "YES", " on ", "1  "])
def test_dotenv_opt_in_loads_env_file(monkeypatch: pytest.MonkeyPatch, _dotenv_env, value: str) -> None:
    monkeypatch.setenv(config.DOTENV_ENV_VAR, value)
    s = config.get_settings()
    assert _dotenv_env
    assert s.port == 9911


def test_dotenv_missing_package_is_a_no_op(monkeypatch: pytest.MonkeyPatch, _dotenv_env) -> None:
    monkeypatch.setenv(config.DOTENV_ENV_VAR, "1")
    monkeypatch.setitem(sys.modules, "dotenv", None)
    s = config.get_settings()
    assert _dotenv_env == []
    assert s.port == config.PORT_DEFAULT


def test_pillar_settings_env_and_redaction(monkeypatch: pytest.MonkeyPatch) -> None:
    _clear_env(monkeypatch)
    monkeypatch.setenv("NYAYA_ENABLE_OCR", "true")
    monkeypatch.setenv("NYAYA_HYBRID_SEARCH", "1")
    monkeypatch.setenv("NYAYA_EMBEDDING_ENDPOINT", "https://embed.example.com/v1")
    monkeypatch.setenv("NYAYA_EMBEDDING_MODEL", "bge-m3")
    monkeypatch.setenv("NYAYA_LIVE_DOCKET_API_URL", "https://docket.example.com/api")
    monkeypatch.setenv("NYAYA_LIVE_DOCKET_API_KEY", "secret-docket-token")

    s = config.get_settings()
    assert s.enable_ocr is True
    assert s.hybrid_search is True
    assert s.embedding_endpoint == "https://embed.example.com/v1"
    assert s.embedding_model == "bge-m3"
    assert s.live_docket_api_url == "https://docket.example.com/api"
    assert s.live_docket_api_key == "secret-docket-token"

    d = s.as_dict()
    assert d["enable_ocr"] is True
    assert d["hybrid_search"] is True
    assert d["embedding_endpoint"] == "https://embed.example.com/v1"
    assert d["embedding_model"] == "bge-m3"
    assert d["live_docket_api_url"] == "https://docket.example.com/api"
    assert d["live_docket_api_key"] == "***"
    assert "secret-docket-token" not in str(d)
