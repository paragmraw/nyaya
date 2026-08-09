"""Tests for nyaya_chat.tools — MCP client wrapper + allowlist filtering."""

from __future__ import annotations

import logging
import sys

import pytest


class _FakeTool:
    def __init__(self, name):
        self.name = name
        self.description = f"fake {name}"


class _FakeMCPClient:
    """Stand-in for langchain_mcp_adapters.MultiServerMCPClient."""
    def __init__(self, tools):
        self._tools = tools

    async def get_tools(self):
        return self._tools


def _patch_mcp_client(monkeypatch, tools):
    """Install a fake langchain_mcp_adapters.client.MultiServerMCPClient.

    The real client accepts ``(servers_dict, **kwargs)`` and returns an object
    with an async ``get_tools()``. Mirror that contract.
    """
    fake_mod = type(sys)("langchain_mcp_adapters.client")

    class _Client:
        def __init__(self, servers, **kwargs):
            self._tools = tools
            self.kwargs = kwargs

        async def get_tools(self):
            return self._tools
    fake_mod.MultiServerMCPClient = _Client
    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters.client", fake_mod)


def _clear_settings(monkeypatch, env_overrides: dict[str, str] | None = None):
    from nyaya_chat.config import get_settings
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")
    for k, v in (env_overrides or {}).items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    return get_settings  # return the callable, not the instance


@pytest.mark.asyncio
async def test_load_tools_returns_allowlist(monkeypatch):
    from nyaya_chat import tools as tools_mod
    _patch_mcp_client(monkeypatch, [_FakeTool("hybrid_search"), _FakeTool("get_section"), _FakeTool("get_article")])
    get_settings = _clear_settings(monkeypatch, {"CHAT_TOOLS": "hybrid_search,get_section"})
    result = await tools_mod.load_tools(get_settings())
    names = [t.name for t in result]
    assert names == ["hybrid_search", "get_section"]


@pytest.mark.asyncio
async def test_load_tools_warns_on_missing(monkeypatch, caplog):
    from nyaya_chat import tools as tools_mod
    _patch_mcp_client(monkeypatch, [_FakeTool("hybrid_search"), _FakeTool("get_section")])
    get_settings = _clear_settings(monkeypatch, {"CHAT_TOOLS": "hybrid_search,nonexistent_tool"})
    with caplog.at_level(logging.WARNING, logger="nyaya_chat.tools"):
        result = await tools_mod.load_tools(get_settings())
    names = [t.name for t in result]
    assert names == ["hybrid_search"]
    assert any("nonexistent_tool" in r.getMessage() for r in caplog.records)


@pytest.mark.asyncio
async def test_load_tools_empty_server(monkeypatch):
    from nyaya_chat import tools as tools_mod
    _patch_mcp_client(monkeypatch, [])
    get_settings = _clear_settings(monkeypatch)
    result = await tools_mod.load_tools(get_settings())
    assert result == []


@pytest.mark.asyncio
async def test_load_tools_default_allowlist(monkeypatch):
    from nyaya_chat import tools as tools_mod
    from nyaya_chat.config import DEFAULT_TOOLS
    all_names = list(DEFAULT_TOOLS) + ["some_extra"]
    _patch_mcp_client(monkeypatch, [_FakeTool(n) for n in all_names])
    get_settings = _clear_settings(monkeypatch)  # no CHAT_TOOLS -> default
    result = await tools_mod.load_tools(get_settings())
    names = {t.name for t in result}
    assert set(DEFAULT_TOOLS).issubset(names)
    assert "some_extra" not in names
