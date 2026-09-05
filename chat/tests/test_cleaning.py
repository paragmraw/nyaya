"""Tests for nyaya_chat.tools_layer.cleaning — the shared tool-result cleaner."""

from __future__ import annotations

import json

from nyaya_chat.config import MAX_TOOL_CHARS
from nyaya_chat.tools_layer.cleaning import (
    clean_tool_content,
    prune_list_result,
    strip_corpus_tags,
)


def _search_response(n_hits: int, snippet_len: int = 2000):
    """A SearchResponse-shaped payload like semantic_query returns."""
    return json.dumps({
        "query": "punishment for murder",
        "total": n_hits * 5,
        "returned": n_hits,
        "offset": 0,
        "limit": n_hits,
        "source": "nyaya",
        "as_of": "2024-01-01",
        "fallback_reason": None,
        "results": [
            {
                "act": f"Act{i}", "ref": f"s. {100 + i}", "title": f"T{i}",
                "snippet": f"HIT{i}-" + "z" * snippet_len,
                "rank": 1.0 - i * 0.01, "citation": None, "kind": "section",
            }
            for i in range(n_hits)
        ],
    })


def test_clean_tool_content_string():
    assert clean_tool_content("short") == "short"
    assert clean_tool_content("x" * 9000) == "x" * MAX_TOOL_CHARS


def test_clean_tool_content_keeps_corpus_tags_by_default():
    """The tools node cleans results BEFORE they are wrapped for the
    synthesis prompt, so the default must not strip a wrapper."""
    content = "<corpus_text>\nIPC s.302 punishment text\n</corpus_text>"
    assert clean_tool_content(content) == content


def test_clean_tool_content_strips_corpus_tags_when_asked():
    content = "<corpus_text>\nIPC s.302 punishment text\n</corpus_text>"
    out = clean_tool_content(content, strip_corpus=True)
    assert out == "IPC s.302 punishment text"


def test_strip_corpus_tags_leaves_unwrapped_text():
    assert strip_corpus_tags("plain result") == "plain result"


def test_clean_tool_content_stringified_blocks():
    """A stringified content-block list (Python literal, not JSON) collapses
    to its text."""
    content = "[{'type': 'text', 'text': 'alpha'}, {'type': 'text', 'text': 'beta'}]"
    assert clean_tool_content(content) == "alpha beta"


def test_clean_tool_content_list_of_blocks():
    blocks = [{"text": "alpha"}, {"text": "beta"}, {"type": "img", "src": "x"}]
    s = clean_tool_content(blocks)
    assert "alpha" in s and "beta" in s
    assert len(s) <= MAX_TOOL_CHARS


def test_clean_tool_content_other_types():
    assert clean_tool_content(123) == "123"
    assert clean_tool_content(None) == "None"


# ---------------------------------------------------------------------------
# prune_list_result — conservative pruning of LIST-type tool results
# ---------------------------------------------------------------------------


def test_prune_keeps_top_hit_verbatim_and_condenses_rest():
    content = _search_response(8)
    out = prune_list_result(content, "semantic_query")
    data = json.loads(out)
    assert len(data["results"]) == 8
    # Hit 0 verbatim: full snippet and all fields.
    assert data["results"][0]["snippet"].startswith("HIT0-")
    assert len(data["results"][0]["snippet"]) == 5 + 2000
    # Later hits: identification fields kept, snippet truncated to 300 chars.
    tail = data["results"][3]
    assert tail["act"] == "Act3"
    assert tail["ref"] == "s. 103"
    assert tail["rank"] == 1.0 - 3 * 0.01
    assert len(tail["snippet"]) == 300
    assert len(tail["snippet"]) < len("HIT3-" + "z" * 2000)
    # Envelope metadata dropped except the counts.
    assert "total" in data and "returned" in data
    for dropped in ("query", "source", "as_of", "offset", "limit", "fallback_reason"):
        assert dropped not in data
    assert len(out) < len(content)


def test_prune_single_hit_untouched():
    content = _search_response(1)
    assert prune_list_result(content, "semantic_query") is content


def test_prune_single_document_tool_untouched():
    """Full text of get_section/get_article/get_judgment must never be pruned."""
    full = json.dumps({"act": "IPC", "ref": "s. 302", "text": "FULL " * 500})
    assert prune_list_result(full, "get_section") is full
    assert prune_list_result(full, "get_article") is full
    assert prune_list_result(full, "get_judgment") is full
    assert prune_list_result(full) is full  # unknown tool: don't prune


def test_prune_unparseable_and_non_list_content_untouched():
    assert prune_list_result("plain prose result", "semantic_query") == "plain prose result"
    assert prune_list_result("{not json", "semantic_query") == "{not json"
    assert prune_list_result('{"results": []}', "semantic_query") == '{"results": []}'
    assert prune_list_result(None, "semantic_query") is None
    assert prune_list_result(123, "semantic_query") == 123
