"""Tests for nyaya_chat.tools_layer.native — direct-import LangChain tools."""

from __future__ import annotations

import asyncio
import json


def test_load_native_tools_returns_tools(monkeypatch):
    """load_native_tools should return StructuredTool instances."""
    from nyaya_chat import config
    config.reset_settings_cache()
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-abcdef1234567890")

    from nyaya_chat.tools_layer.native import load_native_tools
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
    # Override the allowlist (spec.py is the single source of truth; the
    # Settings.tool_allowlist property reads it lazily, at call time).
    monkeypatch.setattr("nyaya_chat.tools_layer.spec.DEFAULT_TOOLS", ("semantic_query",))

    from nyaya_chat.tools_layer.native import load_native_tools
    tools = asyncio.run(load_native_tools())
    assert len(tools) == 1
    assert tools[0].name == "semantic_query"


def test_native_tools_use_spec_descriptions():
    """Tool descriptions come from the spec table — no drift between the
    description the model sees and the allowlist spec."""
    from nyaya_chat.tools_layer import native
    from nyaya_chat.tools_layer.spec import tool_specs_by_name
    specs = tool_specs_by_name()
    for t in native._IMPLS:
        assert t in specs


def test_error_json_format():
    from nyaya_chat.tools_layer.native import _error_json
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
    from nyaya_chat.tools_layer.native import _CITATION_RE
    m = _CITATION_RE.search("IPC s.302")
    assert m is not None
    # Should parse act and num
    act = m.group("act") or m.group("act2")
    num = m.group("num") or m.group("num2")
    assert act is not None
    assert num is not None


def test_art_citation_re_parses():
    from nyaya_chat.tools_layer.native import _ART_CITATION_RE
    m = _ART_CITATION_RE.match("Art.21")
    assert m is not None
    assert m.group("num") == "21"

    m = _ART_CITATION_RE.match("Article 14")
    assert m is not None
    assert m.group("num") == "14"


def test_semantic_query_runs_search_and_corpus_as_of_concurrently(monkeypatch):
    """rerank_search and corpus_as_of are independent queries — they must run
    concurrently, not serially (the serial pair cost an extra full DB round
    trip per semantic_query on the turn's critical path)."""
    import sys
    import time as _time
    import types

    class FakeResult:
        def model_dump(self):
            return {"act": "IPC", "ref": "s. 302", "text": "intentional killing"}

    search_finished_at: list[float] = []
    corpus_started_at: list[float] = []

    def fake_rerank_search(query, kind=None, act=None, limit=10, offset=0,
                           promote_definitions=False):
        _time.sleep(0.25)  # the embed+ANN+rerank work
        search_finished_at.append(_time.monotonic())
        return [FakeResult()], 1, None

    def fake_corpus_as_of():
        corpus_started_at.append(_time.monotonic())
        return None

    fake_db = types.SimpleNamespace(
        rerank_search=fake_rerank_search, corpus_as_of=fake_corpus_as_of,
    )
    fake_nyaya = types.SimpleNamespace(db=fake_db)
    fake_exceptions = types.SimpleNamespace(
        SearchError=type("SearchError", (Exception,), {}),
        NotFound=type("NotFound", (Exception,), {}),
    )
    monkeypatch.setitem(sys.modules, "nyaya", fake_nyaya)
    monkeypatch.setitem(sys.modules, "nyaya.exceptions", fake_exceptions)

    from nyaya_chat.tools_layer.native import _semantic_query
    result = asyncio.run(_semantic_query("murder punishment"))
    data = json.loads(result)

    # Correctness: the output is unchanged either way.
    assert data["total"] == 1
    assert data["results"] == [{"act": "IPC", "ref": "s. 302", "text": "intentional killing"}]
    assert data["fallback_reason"] is None
    # Concurrency: corpus_as_of must have started before rerank_search finished.
    assert corpus_started_at and search_finished_at, "both calls must run"
    assert corpus_started_at[0] < search_finished_at[0], (
        "corpus_as_of started after rerank_search finished — the calls ran serially"
    )
