"""Tests for the FastMCP resource templates."""

from __future__ import annotations

import json

import pytest


def _make_app(fake_db):
    from fastmcp import FastMCP
    from nyaya.tools import register as register_tools
    from nyaya.resources import register as register_resources

    mcp = FastMCP(name="nyaya-test")
    register_tools(mcp)
    register_resources(mcp)
    return mcp


async def test_corpus_resource(fake_db):
    app = _make_app(fake_db)
    res = await app.get_resource("corpus://")
    out = json.loads(res.fn())
    assert out["name"] == "nyaya"
    assert out["counts"]["acts"] == 2


async def test_acts_resource(fake_db):
    app = _make_app(fake_db)
    res = await app.get_resource("acts://")
    out = json.loads(res.fn())
    assert isinstance(out, list)
    assert any(a["short_name"] == "IPC" for a in out)


async def test_act_template(fake_db):
    app = _make_app(fake_db)
    res = await app.get_resource_template("act://{short_name}")
    out = json.loads(res.fn(short_name="IPC"))
    assert out["act"]["short_name"] == "IPC"
    assert out["chapters"][0]["title"] == "Preliminary"


async def test_section_template(fake_db):
    app = _make_app(fake_db)
    res = await app.get_resource_template("section://{act}/{number}")
    out = json.loads(res.fn(act="IPC", number="302"))
    assert out["section"] == "302"
    assert "murder" in out["text"].lower()


async def test_article_template(fake_db):
    app = _make_app(fake_db)
    res = await app.get_resource_template("article://{number}")
    out = json.loads(res.fn(number="21"))
    assert out["number"] == "21"


async def test_judgment_template(fake_db):
    app = _make_app(fake_db)
    res = await app.get_resource_template("judgment://{case_slug}")
    out = json.loads(res.fn(case_slug="kesavananda-bharati-v-state-of-kerala"))
    assert "Kesavananda" in out["case_name"]


async def test_section_template_missing(fake_db):
    app = _make_app(fake_db)
    from nyaya.exceptions import NotFound
    res = await app.get_resource_template("section://{act}/{number}")
    with pytest.raises(NotFound):
        res.fn(act="IPC", number="999")