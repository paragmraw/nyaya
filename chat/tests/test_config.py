"""Tests for nyaya_chat.config — env parsing, validation, redaction."""

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
    assert "hybrid_search" in s.tool_allowlist
    assert s.nvidia_api_key.get_secret_value() == "nvapi-abcdef1234567890"


def test_required_nvidia_key(monkeypatch):
    from nyaya_chat import config
    config.reset_settings_cache()
    monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
    with pytest.raises(Exception):  # pydantic ValidationError
        config.get_settings()


def test_mcp_url_trailing_slash_stripped(monkeypatch):
    from nyaya_chat import config
    config.reset_settings_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")
    monkeypatch.setenv("NYAYA_MCP_URL", "http://localhost:8000/mcp/")
    s = config.get_settings()
    assert s.mcp_url == "http://localhost:8000/mcp"


def test_mcp_url_must_be_http(monkeypatch):
    from nyaya_chat import config
    config.reset_settings_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")
    monkeypatch.setenv("NYAYA_MCP_URL", "ftp://x/mcp")
    with pytest.raises(Exception):
        config.get_settings()


def test_tools_allowlist_parsed(monkeypatch):
    from nyaya_chat import config
    config.reset_settings_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")
    monkeypatch.setenv("CHAT_TOOLS", "get_section, get_article ,,")
    s = config.get_settings()
    assert s.tool_allowlist == ("get_section", "get_article")


def test_tools_empty_falls_back_to_default(monkeypatch):
    from nyaya_chat import config
    config.reset_settings_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")
    monkeypatch.setenv("CHAT_TOOLS", "")
    s = config.get_settings()
    assert s.tool_allowlist == config.DEFAULT_TOOLS


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
    assert s.supervisor_max_tokens == 512
    assert s.synthesis_max_tokens == 4096


def test_per_phase_token_caps_via_env(monkeypatch):
    from nyaya_chat import config
    config.reset_settings_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")
    monkeypatch.setenv("CHAT_SUPERVISOR_MAX_TOKENS", "256")
    monkeypatch.setenv("CHAT_SYNTHESIS_MAX_TOKENS", "8192")
    s = config.get_settings()
    assert s.supervisor_max_tokens == 256
    assert s.synthesis_max_tokens == 8192


def test_per_phase_model_defaults(monkeypatch):
    from nyaya_chat import config
    config.reset_settings_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")
    s = config.get_settings()
    assert s.supervisor_model == _LIGHTNING
    assert s.synthesis_model == _LIGHTNING


def test_per_phase_model_via_env(monkeypatch):
    from nyaya_chat import config
    config.reset_settings_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")
    monkeypatch.setenv("CHAT_SUPERVISOR_MODEL", "meta/llama-3.1-8b-instruct")
    monkeypatch.setenv("CHAT_SYNTHESIS_MODEL", "nvidia/nemotron-3-nano-30b-a3b")
    s = config.get_settings()
    assert s.supervisor_model == "meta/llama-3.1-8b-instruct"
    assert s.synthesis_model == "nvidia/nemotron-3-nano-30b-a3b"


def test_per_phase_model_in_log_dict(monkeypatch):
    from nyaya_chat import config
    config.reset_settings_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")
    s = config.get_settings()
    d = s.as_log_dict()
    assert "supervisor_model" in d
    assert "synthesis_model" in d
    assert d["supervisor_model"] == _LIGHTNING
    assert d["synthesis_model"] == _LIGHTNING


def test_retry_in_log_dict(monkeypatch):
    from nyaya_chat import config
    config.reset_settings_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")
    s = config.get_settings()
    d = s.as_log_dict()
    assert "llm_max_retries" in d
    assert d["llm_max_retries"] == 4
