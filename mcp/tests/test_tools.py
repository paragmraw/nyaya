"""Tests for the MCP tool functions."""

from __future__ import annotations

import pytest


def _make_app(fake_db):
    from fastmcp import FastMCP
    from nyaya.tools import register as register_tools
    from nyaya.resources import register as register_resources

    mcp = FastMCP(name="nyaya-test")
    register_tools(mcp)
    register_resources(mcp)
    return mcp


async def _tool(app, name, **args):
    tool = await app.get_tool(name)
    return tool.fn(**args)


async def test_list_acts_tool(fake_db):
    app = _make_app(fake_db)
    result = await _tool(app, "list_acts")
    assert result.acts[0].short_name == "IPC"
    assert any(a.short_name == "Constitution" for a in result.acts)


async def test_list_chapters_tool(fake_db):
    app = _make_app(fake_db)
    result = await _tool(app, "list_chapters", act="IPC")
    assert result.act == "IPC"
    assert result.chapters[0].title == "Preliminary"


async def test_list_chapters_unknown_act(fake_db):
    app = _make_app(fake_db)
    from nyaya.exceptions import NotFound
    with pytest.raises(NotFound):
        await _tool(app, "list_chapters", act="Nonexistent")


async def test_get_section_tool(fake_db):
    app = _make_app(fake_db)
    s = await _tool(app, "get_section", act="IPC", section="302")
    assert s.act == "IPC"
    assert s.section == "302"
    assert "murder" in s.text.lower()
    assert s.source == "mratanusarkar/Indian-Laws"


async def test_get_section_missing(fake_db):
    app = _make_app(fake_db)
    from nyaya.exceptions import NotFound
    with pytest.raises(NotFound):
        await _tool(app, "get_section", act="IPC", section="999")


async def test_get_article_tool(fake_db):
    app = _make_app(fake_db)
    a = await _tool(app, "get_article", article="21")
    assert a.number == "21"
    assert "life" in a.text.lower()
    assert a.part.startswith("PART III")


async def test_get_article_missing(fake_db):
    app = _make_app(fake_db)
    from nyaya.exceptions import NotFound
    with pytest.raises(NotFound):
        await _tool(app, "get_article", article="999")


async def test_search_law_tool(fake_db):
    app = _make_app(fake_db)
    r = await _tool(app, "search_law", query="murder")
    assert r.total == 1
    assert r.results[0].act == "IPC"
    assert r.results[0].ref == "s. 302"


async def test_search_law_limit_clamped(fake_db):
    app = _make_app(fake_db)
    r = await _tool(app, "search_law", query="murder", limit=999)
    assert r.total <= 50


async def test_cross_reference_tool(fake_db):
    app = _make_app(fake_db)
    r = await _tool(app, "cross_reference", act="IPC", section="302")
    assert r.from_act == "IPC"
    assert r.references[0].to_act == "BNS"
    assert r.references[0].kind == "corresponds_to"


async def test_semantic_query_disabled(fake_db):
    app = _make_app(fake_db)
    r = await _tool(app, "semantic_query", query="right to privacy")
    assert r.total == 0
    assert r.results == []