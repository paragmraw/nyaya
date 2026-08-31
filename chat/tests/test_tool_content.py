"""Tests for nyaya_chat.tool_content — the shared tool-result cleaner."""

from __future__ import annotations

from nyaya_chat.config import MAX_TOOL_CHARS
from nyaya_chat.tool_content import clean_tool_content, strip_corpus_tags


def test_clean_tool_content_string():
    assert clean_tool_content("short") == "short"
    assert clean_tool_content("x" * 9000) == "x" * MAX_TOOL_CHARS


def test_clean_tool_content_keeps_corpus_tags_by_default():
    """The agent's dedup node cleans results BEFORE they are wrapped for the
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
