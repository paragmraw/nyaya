"""Tests for nyaya_chat.config — required key, defaults, redaction."""

from __future__ import annotations

import pytest

_LIGHTNING = "nvidia/nemotron-3.5-lightning-30b-a3b"


def test_defaults(monkeypatch):
    from nyaya_chat import config
    config.reset_settings_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")
    s = config.get_settings()
    assert s.mcp_url == "http://localhost:8000/mcp"
    assert s.llm_model == _LIGHTNING
    assert s.llm_temperature == 0.1
    assert s.llm_max_tokens == 2048
    assert s.max_history == 8
    assert s.tool_allowlist == config.DEFAULT_TOOLS
    assert "semantic_query" in s.tool_allowlist
    assert "hybrid_search" not in s.tool_allowlist
    assert s.nvidia_api_key.get_secret_value() == "nvapi-abcdef1234567890"


def test_agent_pipeline_defaults(monkeypatch):
    """The overhaul's pipeline-collapse defaults: one agent model with thinking
    off, a single gated reflection round, and a 60s turn budget (TTFT <4s /
    total <20s targets)."""
    from nyaya_chat import config
    config.reset_settings_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")
    s = config.get_settings()
    # Thinking off: reasoning tokens share the completion budget and delayed
    # the first answer word by 3k+ tokens (observed live).
    assert s.synthesis_thinking is False
    # One reflection round (total answer legs = value + 1).
    assert s.max_reflection_rounds == 1
    # A reflection round may only start when this much budget remains.
    assert s.reflection_deadline_s == 12.0
    assert s.turn_budget_s == 60.0


def test_supervisor_constants_dropped(monkeypatch):
    """The separate supervisor phase is gone — its config surface is too."""
    from nyaya_chat import config
    config.reset_settings_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")
    s = config.get_settings()
    assert not hasattr(s, "supervisor_model")
    assert not hasattr(s, "supervisor_max_tokens")
    assert not hasattr(s, "supervisor_temperature")
    d = s.as_log_dict()
    assert "supervisor_model" not in d
    assert "supervisor_max_tokens" not in d
    assert "synthesis_thinking" in d
    assert "reflection_deadline_s" in d


def test_required_nvidia_key(monkeypatch):
    from nyaya_chat import config
    config.reset_settings_cache()
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    with pytest.raises(Exception):  # RuntimeError
        config.get_settings()


def test_as_log_dict_redacts_key(monkeypatch):
    from nyaya_chat import config
    config.reset_settings_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")
    s = config.get_settings()
    d = s.as_log_dict()
    redacted = d["nvidia_api_key"]
    assert isinstance(redacted, str)
    assert "abcdef" not in redacted
    assert redacted.startswith("nvapi")
    assert redacted.endswith("890")


def test_retry_settings_defaults(monkeypatch):
    from nyaya_chat import config
    config.reset_settings_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")
    s = config.get_settings()
    assert s.llm_max_retries == 4


def test_per_phase_token_caps_defaults(monkeypatch):
    from nyaya_chat import config
    config.reset_settings_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")
    s = config.get_settings()
    # The agent's single cap for the whole answer (thinking is off, so the
    # completion budget belongs entirely to the answer).
    assert s.synthesis_max_tokens == 6144


def test_per_phase_model_defaults(monkeypatch):
    from nyaya_chat import config
    config.reset_settings_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")
    s = config.get_settings()
    assert s.synthesis_model == _LIGHTNING


def test_per_phase_model_in_log_dict(monkeypatch):
    from nyaya_chat import config
    config.reset_settings_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")
    s = config.get_settings()
    d = s.as_log_dict()
    assert d["synthesis_model"] == _LIGHTNING


def test_retry_in_log_dict(monkeypatch):
    from nyaya_chat import config
    config.reset_settings_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")
    s = config.get_settings()
    d = s.as_log_dict()
    assert "llm_max_retries" in d
    assert d["llm_max_retries"] == 4


def test_mcp_url_derived_from_port(monkeypatch):
    """MCP_URL auto-derives its port from the PORT env var (Railway sets this)."""
    from nyaya_chat import config
    config.reset_settings_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")
    monkeypatch.setenv("PORT", "8080")
    monkeypatch.delenv("MCP_URL", raising=False)
    s = config.get_settings()
    assert s.mcp_url == "http://localhost:8080/mcp"


def test_mcp_url_env_override(monkeypatch):
    """An explicit MCP_URL env var takes precedence over the PORT-derived default."""
    from nyaya_chat import config
    config.reset_settings_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")
    monkeypatch.setenv("PORT", "8080")
    monkeypatch.setenv("MCP_URL", "http://example.com:9000/mcp")
    s = config.get_settings()
    assert s.mcp_url == "http://example.com:9000/mcp"



