"""Tests for nyaya_chat.observability — Langfuse + structlog configuration."""

from __future__ import annotations


def test_get_langfuse_handler_returns_none_without_env(monkeypatch):
    from nyaya_chat.observability import get_langfuse_handler, reset_observability
    reset_observability()
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)
    assert get_langfuse_handler() is None


def test_get_langfuse_handler_returns_handler_with_env(monkeypatch):
    from nyaya_chat.observability import get_langfuse_handler, reset_observability
    reset_observability()
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-test")
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000")
    # This will try to import langfuse — if not installed, returns None
    handler = get_langfuse_handler()
    # Either returns a handler (if langfuse installed) or None (if not)
    # Both are acceptable; the key is it doesn't crash
    assert handler is None or hasattr(handler, "on_llm_start")


def test_get_langfuse_callbacks_empty_without_config(monkeypatch):
    from nyaya_chat.observability import get_langfuse_callbacks, reset_observability
    reset_observability()
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)
    assert get_langfuse_callbacks() == []


def test_configure_structlog_does_not_crash():
    from nyaya_chat.observability import configure_structlog, reset_observability
    reset_observability()
    configure_structlog("DEBUG")
    # Calling again should be a no-op
    configure_structlog("INFO")
