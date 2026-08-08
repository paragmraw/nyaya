"""Tests for the FastMCP resource templates.

The ``_make_app`` fixture is shared from ``conftest.py`` — no local duplicate.
"""

from __future__ import annotations

import json

import pytest

from nyaya.exceptions import NotFound


async def test_corpus_resource(_make_app):
    app = _make_app
    res = await app.get_resource("corpus://")
    out = json.loads(res.fn())
    assert out["name"] == "nyaya"
    assert out["counts"]["acts"] == 2
    # Regression guard: acts_url must be "acts://" (not the broken "act://").
    assert out["acts_url"] == "acts://"
    # as_of is now derived from the DB, not a hardcoded string.
    assert out["as_of"] is not None


async def test_acts_resource(_make_app):
    app = _make_app
    res = await app.get_resource("acts://")
    out = json.loads(res.fn())
    assert isinstance(out, list)
    assert any(a["short_name"] == "IPC" for a in out)


async def test_schedules_resource(_make_app):
    app = _make_app
    res = await app.get_resource("schedules://")
    out = json.loads(res.fn())
    assert isinstance(out, list)
    assert len(out) == 1
    assert out[0]["number"] == 1


async def test_amendments_resource(_make_app):
    app = _make_app
    res = await app.get_resource("amendments://")
    out = json.loads(res.fn())
    assert isinstance(out, list)
    assert len(out) == 1
    assert out[0]["number"] == 1


async def test_judgments_resource(_make_app):
    app = _make_app
    res = await app.get_resource("judgments://")
    out = json.loads(res.fn())
    assert isinstance(out, list)
    assert len(out) == 1
    assert "Kesavananda" in out[0]["case_name"]


async def test_act_template(_make_app):
    app = _make_app
    res = await app.get_resource_template("act://{short_name}")
    out = json.loads(res.fn(short_name="IPC"))
    assert out["act"]["short_name"] == "IPC"
    assert out["chapters"][0]["title"] == "Preliminary"


async def test_act_template_missing(_make_app):
    app = _make_app
    res = await app.get_resource_template("act://{short_name}")
    with pytest.raises(NotFound):
        res.fn(short_name="Nonexistent")


async def test_section_template(_make_app):
    app = _make_app
    res = await app.get_resource_template("section://{act}/{number}")
    out = json.loads(res.fn(act="IPC", number="302"))
    assert out["section"] == "302"
    assert "murder" in out["text"].lower()


async def test_article_template(_make_app):
    app = _make_app
    res = await app.get_resource_template("article://{number}")
    out = json.loads(res.fn(number="21"))
    assert out["number"] == "21"


async def test_article_template_missing(_make_app):
    app = _make_app
    res = await app.get_resource_template("article://{number}")
    with pytest.raises(NotFound):
        res.fn(number="999")


async def test_judgment_template(_make_app):
    app = _make_app
    res = await app.get_resource_template("judgment://{case_slug}")
    out = json.loads(res.fn(case_slug="kesavananda-bharati-v-state-of-kerala"))
    assert "Kesavananda" in out["case_name"]


async def test_judgment_template_missing(_make_app):
    app = _make_app
    res = await app.get_resource_template("judgment://{case_slug}")
    with pytest.raises(NotFound):
        res.fn(case_slug="nonexistent-case")


async def test_section_template_missing(_make_app):
    app = _make_app
    res = await app.get_resource_template("section://{act}/{number}")
    with pytest.raises(NotFound):
        res.fn(act="IPC", number="999")


async def test_amendment_template(_make_app):
    app = _make_app
    res = await app.get_resource_template("amendment://{number}")
    out = json.loads(res.fn(number="1"))
    assert out["number"] == 1


async def test_schedule_template(_make_app):
    app = _make_app
    res = await app.get_resource_template("schedule://{number}")
    out = json.loads(res.fn(number="1"))
    assert out["number"] == 1


async def test_amendment_template_missing(_make_app):
    app = _make_app
    res = await app.get_resource_template("amendment://{number}")
    with pytest.raises(NotFound):
        res.fn(number="99")


async def test_amendment_template_non_integer(_make_app):
    app = _make_app
    res = await app.get_resource_template("amendment://{number}")
    with pytest.raises(NotFound):
        res.fn(number="abc")


async def test_schedule_template_missing(_make_app):
    app = _make_app
    res = await app.get_resource_template("schedule://{number}")
    with pytest.raises(NotFound):
        res.fn(number="99")


async def test_schedule_template_non_integer(_make_app):
    app = _make_app
    res = await app.get_resource_template("schedule://{number}")
    with pytest.raises(NotFound):
        res.fn(number="abc")


async def test_corpus_as_of_value(_make_app):
    """corpus:// as_of is the actual derived value, not just non-None."""
    app = _make_app
    res = await app.get_resource("corpus://")
    out = json.loads(res.fn())
    assert out["as_of"] == "2026-07-01"  # isoformat of the stub's corpus_as_of
