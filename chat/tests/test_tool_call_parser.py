"""Tests for nyaya_chat.tool_call_parser — the free-text tool-call contract.

These are the parser's contract tests: every recognised shape must keep
parsing exactly as it did when the parser lived in agent.py. Ported from
tests/test_agent.py when the parser was extracted.
"""

from __future__ import annotations

from nyaya_chat.tool_call_parser import parse_text_tool_calls


def test_parse_text_tool_calls_bracket_format():
    """Parse [[tool_calls]] JSON array [[/tool_calls]] format."""
    content = '[[tool_calls]]\n[\n {\n  "name": "get_section",\n  "arguments": {"act": "IPC", "section": "302"}\n }\n]\n[[/tool_calls]]'
    calls = parse_text_tool_calls(content)
    assert len(calls) == 1
    assert calls[0]["name"] == "get_section"
    assert calls[0]["args"]["act"] == "IPC"
    assert calls[0]["args"]["section"] == "302"


def test_parse_text_tool_calls_bare_json():
    """Parse bare JSON object format."""
    content = '{"name": "get_section", "arguments": {"act": "IPC", "section": "302"}}'
    calls = parse_text_tool_calls(content)
    assert len(calls) == 1
    assert calls[0]["name"] == "get_section"


def test_parse_text_tool_calls_json_array():
    """Parse bare JSON array format."""
    content = '[{"name": "get_section", "arguments": {"act": "IPC", "section": "302"}}]'
    calls = parse_text_tool_calls(content)
    assert len(calls) == 1
    assert calls[0]["name"] == "get_section"


def test_parse_text_tool_calls_no_tool_calls():
    """Return empty list when no tool calls are found."""
    assert parse_text_tool_calls("This is a plain text answer.") == []
    assert parse_text_tool_calls("") == []
    assert parse_text_tool_calls(None) == []


def test_parse_text_tool_calls_multiple():
    """Parse multiple tool calls from [[tool_calls]] format."""
    content = '[[tool_calls]]\n[\n {"name": "get_section", "arguments": {"act": "IPC", "section": "302"}},\n {"name": "get_section", "arguments": {"act": "BNS", "section": "103"}}\n]\n[[/tool_calls]]'
    calls = parse_text_tool_calls(content)
    assert len(calls) == 2
    assert calls[0]["name"] == "get_section"
    assert calls[1]["args"]["act"] == "BNS"


def test_parse_text_xml_tag_wrapped_json():
    """XML-tag wrapped JSON: <toolname>{...json...}</toolname>"""
    content = '<semantic_query>{"query": "dowry prohibition India laws", "limit": 10}</semantic_query>'
    calls = parse_text_tool_calls(content)
    assert len(calls) == 1
    assert calls[0]["name"] == "semantic_query"
    assert calls[0]["args"]["query"] == "dowry prohibition India laws"
    assert calls[0]["args"]["limit"] == 10


def test_parse_text_xml_tag_multiple():
    """Multiple XML tags in one response"""
    content = '<semantic_query>{"query": "IPC 302"}</semantic_query>\n<get_section>{"act": "IPC", "section": "302"}</get_section>'
    calls = parse_text_tool_calls(content)
    assert len(calls) == 2
    assert calls[0]["name"] == "semantic_query"
    assert calls[1]["name"] == "get_section"


def test_parse_text_bracket_pipe_format():
    """Bracket-pipe: [[tool tool call|tool_name: "...", tool_args: {...}]]"""
    content = '[[semantic_query tool call|tool_name: "semantic_query", tool_args: {"query": "dowry prohibition India laws", "limit": 10}]]'
    calls = parse_text_tool_calls(content)
    assert len(calls) == 1
    assert calls[0]["name"] == "semantic_query"
    assert calls[0]["args"]["query"] == "dowry prohibition India laws"


def test_parse_text_bracket_pipe_nested_json():
    """Bracket-pipe with nested JSON objects in tool_args"""
    content = '[[semantic_query tool call|tool_name: "semantic_query", tool_args: {"query": "test", "filter": {"act": "IPC", "kind": "section"}}}]]'
    calls = parse_text_tool_calls(content)
    assert len(calls) == 1
    assert calls[0]["args"]["filter"]["act"] == "IPC"


def test_parse_text_attribute_style():
    """Attribute-style: [[tool_name key="value" key2="value2"]]"""
    content = '[[get_section act="IPC" section="24"]]'
    calls = parse_text_tool_calls(content)
    assert len(calls) == 1
    assert calls[0]["name"] == "get_section"
    assert calls[0]["args"]["act"] == "IPC"
    assert calls[0]["args"]["section"] == "24"


def test_first_matching_pattern_wins():
    """Regression: exactly one pattern fires on a given text.

    Pattern 8 (function-call style) historically lacked the guard the
    fallback patterns had, so a text matching both pattern 7
    (attribute-style) and pattern 8 emitted BOTH calls. The table-driven
    parser returns the first match only.
    """
    content = '[[get_section act="IPC"]] then [[get_section(section="24")]]'
    calls = parse_text_tool_calls(content)
    assert len(calls) == 1
    assert calls[0]["name"] == "get_section"
    assert calls[0]["args"] == {"act": "IPC"}
