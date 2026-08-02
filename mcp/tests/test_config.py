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
    # Pool config defaults
    assert s.pool_min == 1
    assert s.pool_max == 8
    assert s.pool_timeout == 3.0


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


def test_pool_size_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/db")
    monkeypatch.setenv("NYAYA_POOL_MAX", "20")
    from nyaya import config
    config.get_settings.cache_clear()
    s = config.get_settings()
    assert s.pool_max == 20


def test_pool_min_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/db")
    monkeypatch.setenv("NYAYA_POOL_MIN", "2")
    from nyaya import config
    config.get_settings.cache_clear()
    s = config.get_settings()
    assert s.pool_min == 2


def test_pool_timeout_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/db")
    monkeypatch.setenv("NYAYA_POOL_TIMEOUT", "5.5")
    from nyaya import config
    config.get_settings.cache_clear()
    s = config.get_settings()
    assert s.pool_timeout == 5.5


def test_statement_timeout_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/db")
    monkeypatch.setenv("NYAYA_STATEMENT_TIMEOUT", "500")
    from nyaya import config
    config.get_settings.cache_clear()
    s = config.get_settings()
    assert s.statement_timeout_ms == 500


def test_log_level_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/db")
    monkeypatch.setenv("NYAYA_LOG_LEVEL", "DEBUG")
    from nyaya import config
    config.get_settings.cache_clear()
    s = config.get_settings()
    assert s.log_level == "DEBUG"


def test_embedding_model_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/db")
    monkeypatch.setenv("NYAYA_EMBEDDING_MODEL", "custom/model")
    from nyaya import config
    config.get_settings.cache_clear()
    s = config.get_settings()
    assert s.embedding_model == "custom/model"


def test_invalid_pool_max_raises(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/db")
    monkeypatch.setenv("NYAYA_POOL_MAX", "abc")
    from nyaya import config
    config.get_settings.cache_clear()
    with pytest.raises(ConfigurationError):
        config.get_settings()


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
                              "embedding_model"}