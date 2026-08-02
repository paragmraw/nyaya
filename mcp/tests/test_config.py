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