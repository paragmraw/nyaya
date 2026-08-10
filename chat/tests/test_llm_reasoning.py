"""Tests for nyaya_chat.llm — thinking mode application and model caching."""

from __future__ import annotations

import pytest


def test_get_model_caches_instance(monkeypatch):
    """get_model should return the same instance on repeated calls."""
    from nyaya_chat import config, llm
    config.reset_settings_cache()
    llm.reset_model_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")

    # Patch ChatNVIDIA to a fake so we don't need a real API key
    calls: list = []

    class FakeChatNVIDIA:
        def __init__(self, **kw):
            calls.append(kw)

        def with_thinking_mode(self, enabled=True, **kw):
            self._thinking = enabled
            return self

    monkeypatch.setattr(
        "langchain_nvidia_ai_endpoints.ChatNVIDIA", FakeChatNVIDIA
    )
    m1 = llm.get_model()
    m2 = llm.get_model()
    assert m1 is m2


def test_get_base_model_caches_separately(monkeypatch):
    """get_base_model should return the same base instance on repeated calls."""
    from nyaya_chat import config, llm
    config.reset_settings_cache()
    llm.reset_model_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")

    class FakeChatNVIDIA:
        def __init__(self, **kw):
            pass

        def with_thinking_mode(self, enabled=True, **kw):
            return self

    monkeypatch.setattr(
        "langchain_nvidia_ai_endpoints.ChatNVIDIA", FakeChatNVIDIA
    )
    b1 = llm.get_base_model()
    b2 = llm.get_base_model()
    assert b1 is b2


def test_thinking_mode_applied_when_enabled(monkeypatch):
    """When llm_enable_thinking=True, get_model should call with_thinking_mode(True)."""
    from nyaya_chat import config, llm
    config.reset_settings_cache()
    llm.reset_model_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")
    monkeypatch.setenv("CHAT_LLM_ENABLE_THINKING", "true")

    thinking_calls: list[bool] = []

    class FakeChatNVIDIA:
        def __init__(self, **kw):
            pass

        def with_thinking_mode(self, enabled=True, **kw):
            thinking_calls.append(enabled)
            return self

    monkeypatch.setattr(
        "langchain_nvidia_ai_endpoints.ChatNVIDIA", FakeChatNVIDIA
    )
    llm.get_model()
    assert thinking_calls == [True]


def test_thinking_mode_not_applied_when_disabled(monkeypatch):
    """When llm_enable_thinking=False, get_model should NOT call with_thinking_mode."""
    from nyaya_chat import config, llm
    config.reset_settings_cache()
    llm.reset_model_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")
    monkeypatch.setenv("CHAT_LLM_ENABLE_THINKING", "false")

    thinking_calls: list[bool] = []

    class FakeChatNVIDIA:
        def __init__(self, **kw):
            pass

        def with_thinking_mode(self, enabled=True, **kw):
            thinking_calls.append(enabled)
            return self

    monkeypatch.setattr(
        "langchain_nvidia_ai_endpoints.ChatNVIDIA", FakeChatNVIDIA
    )
    model = llm.get_model()
    assert thinking_calls == []  # should not be called
    # get_model should return the base model directly
    assert model is llm.get_base_model()


def test_reset_model_cache_clears_both(monkeypatch):
    """reset_model_cache should clear both base and thinking model caches."""
    from nyaya_chat import config, llm
    config.reset_settings_cache()
    llm.reset_model_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")

    class FakeChatNVIDIA:
        def __init__(self, **kw):
            pass

        def with_thinking_mode(self, enabled=True, **kw):
            return self

    monkeypatch.setattr(
        "langchain_nvidia_ai_endpoints.ChatNVIDIA", FakeChatNVIDIA
    )
    m1 = llm.get_model()
    llm.reset_model_cache()
    m2 = llm.get_model()
    assert m1 is not m2  # new instance after reset


def test_synthesis_prompt_exists():
    """SYNTHESIS_PROMPT should be defined and non-empty."""
    from nyaya_chat.llm import SYNTHESIS_PROMPT
    assert isinstance(SYNTHESIS_PROMPT, str)
    assert len(SYNTHESIS_PROMPT) > 50
    assert "citation" in SYNTHESIS_PROMPT.lower()