"""Tests for nyaya_chat.text_tools — tolerant free-text tool-call extraction.

The supervisor's last-resort recovery path: when the model embeds tool calls
in its content instead of using the tool-calling protocol. ONE tolerant
strategy set replaces the former eleven fragile regex shapes; only
allowlisted tool names are ever returned.
"""

from __future__ import annotations

import json

from nyaya_chat.text_tools import parse_text_tool_calls


def test_whole_content_is_call_object():
    content = json.dumps({"name": "get_section", "arguments": {"act": "IPC", "section": "302"}})
    calls = parse_text_tool_calls(content)
    assert len(calls) == 1
    assert calls[0]["name"] == "get_section"
    assert calls[0]["args"] == {"act": "IPC", "section": "302"}
    assert calls[0]["id"]  # tool-call id assigned


def test_whole_content_is_call_array():
    content = json.dumps([
        {"name": "get_section", "args": {"act": "IPC", "section": "302"}},
        {"name": "get_article", "arguments": {"article": "21"}},
    ])
    calls = parse_text_tool_calls(content)
    assert [c["name"] for c in calls] == ["get_section", "get_article"]


def test_embedded_in_json_fence():
    content = "```json\n{\"name\": \"semantic_query\", \"arguments\": {\"query\": \"murder\"}}\n```"
    calls = parse_text_tool_calls(content)
    assert len(calls) == 1
    assert calls[0]["name"] == "semantic_query"
    assert calls[0]["args"] == {"query": "murder"}


def test_embedded_in_prose_with_nested_args():
    content = (
        "Sure, let me look that up. {\"name\": \"get_section\", "
        "\"args\": {\"act\": \"IPC\", \"section\": \"302\", "
        "\"opts\": {\"limit\": 5}}} — one moment."
    )
    calls = parse_text_tool_calls(content)
    assert len(calls) == 1
    assert calls[0]["args"]["opts"] == {"limit": 5}  # balanced-brace scan


def test_unknown_tool_names_dropped():
    content = json.dumps({"name": "shell_exec", "args": {"cmd": "rm -rf /"}})
    assert parse_text_tool_calls(content) == []


def test_bare_allowlisted_name():
    calls = parse_text_tool_calls("list_acts")
    assert calls == [{"id": "tc_text_0", "name": "list_acts", "args": {}}]


def test_prose_without_calls_returns_empty():
    assert parse_text_tool_calls("The punishment for murder is death.") == []
    assert parse_text_tool_calls("") == []
    assert parse_text_tool_calls(None) == []
    assert parse_text_tool_calls(123) == []


def test_malformed_json_returns_empty():
    assert parse_text_tool_calls("{not json at all") == []
    assert parse_text_tool_calls('{"name": "get_section", "args": {') == []
