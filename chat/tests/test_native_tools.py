"""Tests for nyaya_chat.native_tools — direct-import LangChain tools."""

from __future__ import annotations

import asyncio
import json


def test_load_native_tools_returns_tools(monkeypatch):
    """load_native_tools should return StructuredTool instances."""
    from nyaya_chat import config
    config.reset_settings_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")

    from nyaya_chat.native_tools import load_native_tools
    tools = asyncio.run(load_native_tools())
    assert len(tools) > 0
    names = {t.name for t in tools}
    assert "semantic_query" in names
    assert "get_section" in names
    assert "get_article" in names


def test_load_native_tools_respects_allowlist(monkeypatch):
    """Tools not in the allowlist should not be loaded."""
    from nyaya_chat import config
    config.reset_settings_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")
    # Override the allowlist to only include one tool
    monkeypatch.setattr(config, "DEFAULT_TOOLS", ("semantic_query",))

    from nyaya_chat.native_tools import load_native_tools
    tools = asyncio.run(load_native_tools())
    assert len(tools) == 1
    assert tools[0].name == "semantic_query"


def test_error_json_format():
    from nyaya_chat.native_tools import _error_json
    class FakeError(Exception):
        code = "not_found"
        message = "Section 999 not found"
        kind = "section"
        hint = "try semantic_query"
    result = _error_json(FakeError("Section 999 not found"))
    data = json.loads(result)
    assert data["error"]["code"] == "not_found"
    assert data["error"]["kind"] == "section"


def test_citation_re_parses_combined():
    from nyaya_chat.native_tools import _CITATION_RE
    m = _CITATION_RE.search("IPC s.302")
    assert m is not None
    # Should parse act and num
    act = m.group("act") or m.group("act2")
    num = m.group("num") or m.group("num2")
    assert act is not None
    assert num is not None


def test_art_citation_re_parses():
    from nyaya_chat.native_tools import _ART_CITATION_RE
    m = _ART_CITATION_RE.match("Art.21")
    assert m is not None
    assert m.group("num") == "21"

    m = _ART_CITATION_RE.match("Article 14")
    assert m is not None
    assert m.group("num") == "14"
