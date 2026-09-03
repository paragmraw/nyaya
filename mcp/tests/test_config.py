"""Unit tests for the config loader."""

from __future__ import annotations

import pytest

from nyaya import config
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


# ---------------------------------------------------------------------------
# Optional .env loading (Task 11): get_settings() must NOT touch .env unless
# the operator explicitly opts in with NYAYA_DOTENV=1 — otherwise a stray
# .env file silently changes test/CI behavior (e.g. pointing DATABASE_URL at
# a local dev database).
# ---------------------------------------------------------------------------

@pytest.fixture
def _dotenv_env(monkeypatch, tmp_path):
    """A tmp cwd with a .env file, a fake ``dotenv`` module that applies it,
    and a cleared get_settings cache. Yields the list of load_dotenv calls."""
    import sys
    import types

    from nyaya import config

    env_file = tmp_path / ".env"
    env_file.write_text("PORT=9911\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("NYAYA_DOTENV", raising=False)
    monkeypatch.delenv("PORT", raising=False)

    calls: list[tuple] = []

    def fake_load_dotenv(*args, **kwargs):
        calls.append((args, kwargs))
        for line in env_file.read_text().splitlines():
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


def test_dotenv_not_loaded_by_default(_dotenv_env):
    """Without NYAYA_DOTENV the .env file is ignored (PORT stays unset)."""
    from nyaya import config

    s = config.get_settings()
    assert _dotenv_env == []
    assert s.port != 9911


@pytest.mark.parametrize("value", ["1", "true", "YES", " on ", "1  "])
def test_dotenv_opt_in_loads_env_file(monkeypatch, _dotenv_env, value):
    """NYAYA_DOTENV=<truthy> makes get_settings() load .env from the CWD."""
    monkeypatch.setenv(config.DOTENV_ENV_VAR, value)
    s = config.get_settings()
    assert _dotenv_env, "load_dotenv was not called despite NYAYA_DOTENV being set"
    assert s.port == 9911


@pytest.mark.parametrize("value", ["0", "false", "off", "", "no"])
def test_dotenv_falsy_values_do_not_load(monkeypatch, _dotenv_env, value):
    monkeypatch.setenv(config.DOTENV_ENV_VAR, value)
    from nyaya import config as _config  # noqa: F401 - gate is inside get_settings

    s = config.get_settings()
    assert _dotenv_env == []
    assert s.port != 9911


def test_dotenv_missing_package_is_a_no_op(monkeypatch, _dotenv_env):
    """python-dotenv not installed (optional extra) -> silent no-op, settings
    still build from the real environment."""
    import sys

    monkeypatch.setenv(config.DOTENV_ENV_VAR, "1")
    monkeypatch.setitem(sys.modules, "dotenv", None)  # import raises ImportError

    s = config.get_settings()
    assert _dotenv_env == []
    assert s.database_url.startswith("postgresql://")
