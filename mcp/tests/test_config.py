"""Unit tests for the config loader."""

from __future__ import annotations

import pytest

from nyaya.exceptions import ConfigurationError


def _set_env(monkeypatch, **extra):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/db")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-key")
    for k, v in extra.items():
        monkeypatch.setenv(k, v)


def test_settings_required_vars(monkeypatch):
    _set_env(monkeypatch)
    from nyaya import config
    config.get_settings.cache_clear()
    s = config.get_settings()
    assert s.database_url.startswith("postgresql://")
    assert s.nvidia_api_key == "nvapi-test-key"
    assert s.pool_min == config.POOL_MIN
    assert s.pool_max == config.POOL_MAX
    assert s.pool_timeout == config.POOL_TIMEOUT


def test_settings_missing_database_url(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")
    from nyaya import config
    config.get_settings.cache_clear()
    with pytest.raises(ConfigurationError) as exc_info:
        config.get_settings()
    assert "DATABASE_URL" in str(exc_info.value)


def test_settings_missing_nvidia_key(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/db")
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    from nyaya import config
    config.get_settings.cache_clear()
    with pytest.raises(ConfigurationError) as exc_info:
        config.get_settings()
    assert "NVIDIA_API_KEY" in str(exc_info.value)


def test_port_from_railway_env(monkeypatch):
    _set_env(monkeypatch, PORT="8080")
    from nyaya import config
    config.get_settings.cache_clear()
    s = config.get_settings()
    assert s.port == 8080


def test_invalid_port_raises(monkeypatch):
    _set_env(monkeypatch, PORT="abc")
    from nyaya import config
    config.get_settings.cache_clear()
    with pytest.raises(ConfigurationError):
        config.get_settings()


def test_as_dict_redacts_credentials(monkeypatch):
    _set_env(monkeypatch, DATABASE_URL="postgresql://user:secret@host:5432/db")
    from nyaya import config
    config.get_settings.cache_clear()
    s = config.get_settings()
    d = s.as_dict()
    assert "secret" not in d["database_url"]
    assert "***" in d["database_url"]
    assert d["nvidia_api_key"] == "***"


def test_redact_postgres_scheme(monkeypatch):
    _set_env(monkeypatch, DATABASE_URL="postgres://user:secret@host:5432/db")
    from nyaya import config
    config.get_settings.cache_clear()
    s = config.get_settings()
    d = s.as_dict()
    assert "secret" not in d["database_url"]
    assert "***" in d["database_url"]


def test_redact_no_credentials(monkeypatch):
    _set_env(monkeypatch, DATABASE_URL="postgresql://nowhere.example.com:5432/db")
    from nyaya import config
    config.get_settings.cache_clear()
    s = config.get_settings()
    d = s.as_dict()
    assert "***" not in d["database_url"]


def test_as_dict_all_fields(monkeypatch):
    _set_env(monkeypatch)
    from nyaya import config
    config.get_settings.cache_clear()
    s = config.get_settings()
    d = s.as_dict()
    assert set(d.keys()) == {
        "database_url", "nvidia_api_key", "port", "pool_min", "pool_max",
        "pool_timeout", "statement_timeout_ms", "log_level",
        "embedding_model", "reranker_model", "embedding_dim", "redis_url",
    }


def test_constants_match_settings(monkeypatch):
    _set_env(monkeypatch)
    from nyaya import config
    config.get_settings.cache_clear()
    s = config.get_settings()
    assert s.statement_timeout_ms == config.STATEMENT_TIMEOUT_MS
    assert s.log_level == config.LOG_LEVEL
    assert s.embedding_model == config.EMBEDDING_MODEL
    assert s.reranker_model == config.RERANKER_MODEL
    assert s.embedding_dim == config.EMBEDDING_DIM
    assert s.redis_url == config.REDIS_URL


def test_rate_limit_constants():
    from nyaya import config
    rl = config.get_rate_limit_settings()
    assert rl.read_per_min == config.RATE_READ_PER_MIN
    assert rl.embedding_per_min == config.RATE_MCP_PER_MIN
    assert rl.chat_per_min == config.RATE_CHAT_PER_MIN
    assert rl.body_size_max_bytes == config.RATE_BODY_MAX_BYTES