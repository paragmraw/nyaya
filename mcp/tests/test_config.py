"""Unit tests for the config loader."""

from __future__ import annotations

import pytest

from nyaya.exceptions import ConfigurationError


def test_settings_required_vars(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/db")
    from nyaya import config
    config.get_settings.cache_clear()
    s = config.get_settings()
    assert s.database_url.startswith("postgresql://")
    # Pool config defaults (constants)
    assert s.pool_min == config.POOL_MIN
    assert s.pool_max == config.POOL_MAX
    assert s.pool_timeout == config.POOL_TIMEOUT


def test_settings_missing_required(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from nyaya import config
    config.get_settings.cache_clear()
    with pytest.raises(ConfigurationError) as exc_info:
        config.get_settings()
    assert "DATABASE_URL" in str(exc_info.value)


def test_port_from_railway_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/db")
    monkeypatch.setenv("PORT", "8080")
    from nyaya import config
    config.get_settings.cache_clear()
    s = config.get_settings()
    assert s.port == 8080


def test_invalid_port_raises(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/db")
    monkeypatch.setenv("PORT", "abc")
    from nyaya import config
    config.get_settings.cache_clear()
    with pytest.raises(ConfigurationError):
        config.get_settings()


def test_as_dict_redacts_credentials(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:secret@host:5432/db")
    from nyaya import config
    config.get_settings.cache_clear()
    s = config.get_settings()
    d = s.as_dict()
    # The password must NOT appear in the redacted URL.
    assert "secret" not in d["database_url"]
    assert "***" in d["database_url"]


def test_redact_postgres_scheme(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgres://user:secret@host:5432/db")
    from nyaya import config
    config.get_settings.cache_clear()
    s = config.get_settings()
    d = s.as_dict()
    assert "secret" not in d["database_url"]
    assert "***" in d["database_url"]


def test_redact_no_credentials(monkeypatch):
    """A URL with no userinfo (no user@) passes through without redaction."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://nowhere.example.com:5432/db")
    from nyaya import config
    config.get_settings.cache_clear()
    s = config.get_settings()
    d = s.as_dict()
    # No credentials to redact — URL passes through without ***.
    assert "***" not in d["database_url"]


def test_as_dict_all_fields(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/db")
    from nyaya import config
    config.get_settings.cache_clear()
    s = config.get_settings()
    d = s.as_dict()
    assert set(d.keys()) == {"database_url", "port", "pool_min", "pool_max",
                              "pool_timeout", "statement_timeout_ms", "log_level",
                              "embedding_model", "redis_url"}


def test_constants_match_settings(monkeypatch):
    """Settings fields reflect the module-level constants."""
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/db")
    from nyaya import config
    config.get_settings.cache_clear()
    s = config.get_settings()
    assert s.statement_timeout_ms == config.STATEMENT_TIMEOUT_MS
    assert s.log_level == config.LOG_LEVEL
    assert s.embedding_model == config.EMBEDDING_MODEL
    assert s.redis_url == config.REDIS_URL


def test_rate_limit_constants():
    from nyaya import config
    rl = config.get_rate_limit_settings()
    assert rl.read_per_min == config.RATE_READ_PER_MIN
    assert rl.embedding_per_min == config.RATE_MCP_PER_MIN
    assert rl.chat_per_min == config.RATE_CHAT_PER_MIN
    assert rl.body_size_max_bytes == config.RATE_BODY_MAX_BYTES
