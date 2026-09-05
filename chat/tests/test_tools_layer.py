"""Tests for nyaya_chat.tools_layer — tool loading (native + MCP fallback)."""

from __future__ import annotations

import sys

import pytest


class _FakeTool:
    def __init__(self, name):
        self.name = name
        self.description = f"fake {name}"


def _patch_mcp_client(monkeypatch, tools):
    """Install a fake langchain_mcp_adapters.client.MultiServerMCPClient."""
    fake_mod = type(sys)("langchain_mcp_adapters.client")

    class _Client:
        def __init__(self, servers, **kwargs):
            self._tools = tools
            self.kwargs = kwargs

        async def get_tools(self):
            return self._tools
    fake_mod.MultiServerMCPClient = _Client
    monkeypatch.setitem(sys.modules, "langchain_mcp_adapters.client", fake_mod)


def _patch_native_tools_empty(monkeypatch):
    """Patch native.load_native_tools to return [] (simulate nyaya not importable)."""
    from nyaya_chat.tools_layer import native as native_mod
    async def _empty(_=None):
        return []
    monkeypatch.setattr(native_mod, "load_native_tools", _empty)


def _clear_settings(monkeypatch):
    from nyaya_chat.config import get_settings
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")
    get_settings.cache_clear()
    return get_settings


@pytest.mark.asyncio
async def test_load_tools_falls_back_to_mcp_when_native_empty(monkeypatch):
    """When native tools return empty, load_tools falls back to MCP client."""
    from nyaya_chat.tools_layer import load_tools
    _patch_native_tools_empty(monkeypatch)
    _patch_mcp_client(monkeypatch, [_FakeTool(n) for n in ["semantic_query", "get_section"]])
    get_settings = _clear_settings(monkeypatch)
    result = await load_tools(get_settings())
    assert len(result) == 2
    assert {t.name for t in result} == {"semantic_query", "get_section"}


@pytest.mark.asyncio
async def test_load_tools_mcp_empty_returns_empty(monkeypatch):
    """When both native and MCP return empty, load_tools returns empty."""
    from nyaya_chat.tools_layer import load_tools
    _patch_native_tools_empty(monkeypatch)
    _patch_mcp_client(monkeypatch, [])
    get_settings = _clear_settings(monkeypatch)
    result = await load_tools(get_settings())
    assert result == []


@pytest.mark.asyncio
async def test_load_tools_default_allowlist_via_mcp(monkeypatch):
    """MCP fallback respects the default allowlist."""
    from nyaya_chat.tools_layer import load_tools
    from nyaya_chat.tools_layer.spec import DEFAULT_TOOLS
    _patch_native_tools_empty(monkeypatch)
    all_names = list(DEFAULT_TOOLS) + ["some_extra"]
    _patch_mcp_client(monkeypatch, [_FakeTool(n) for n in all_names])
    get_settings = _clear_settings(monkeypatch)
    result = await load_tools(get_settings())
    names = {t.name for t in result}
    assert set(DEFAULT_TOOLS).issubset(names)
    assert "some_extra" not in names


def test_spec_table_is_single_source_of_truth():
    """Every spec has a name, description, args model; TOOL_NAMES mirrors it."""
    from pydantic import BaseModel

    from nyaya_chat.tools_layer.spec import TOOL_NAMES, TOOL_SPECS, spec_for
    names = {s.name for s in TOOL_SPECS}
    assert names == set(TOOL_NAMES)
    for spec in TOOL_SPECS:
        assert spec.description
        assert isinstance(spec.args_model, type) and issubclass(spec.args_model, BaseModel)
        assert spec_for(spec.name) is spec
