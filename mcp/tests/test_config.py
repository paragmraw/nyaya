"""Unit tests for the config loader."""

from __future__ import annotations


def test_settings_required_vars(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/db")
    from nyaya import config
    config.get_settings.cache_clear()
    s = config.get_settings()
    assert s.database_url.startswith("postgresql://")


def test_settings_missing_required(monkeypatch):
    monkeypatch.delenv("DATABASE_URL", raising=False)
    from nyaya import config
    config.get_settings.cache_clear()
    try:
        config.get_settings()
        assert False, "should have raised"
    except RuntimeError as e:
        assert "DATABASE_URL" in str(e)


def test_port_from_railway_env(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@host:5432/db")
    monkeypatch.setenv("PORT", "8080")
    from nyaya import config
    config.get_settings.cache_clear()
    s = config.get_settings()
    assert s.port == 8080