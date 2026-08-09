"""Tests for nyaya_chat.streaming — SSE encoding of LangGraph stream parts."""

from __future__ import annotations

import json

import pytest

from nyaya_chat.streaming import _sse, _summarise_tool_result, stream_turn


def test_sse_format():
    b = _sse("token", {"content": "hi"})
    assert b == b'event: token\ndata: {"content": "hi"}\n\n'


def test_sse_non_ascii_preserved():
    b = _sse("token", {"content": "Namaste — §"})
    # ensure_ascii=False so the em-dash and section sign stay literal.
    assert "Namaste — §".encode() in b


def test_summarise_tool_result_string():
    assert _summarise_tool_result("short") == "short"
    assert _summarise_tool_result("x" * 500) == "x" * 400


def test_summarise_tool_result_list_of_blocks():
    blocks = [{"text": "alpha"}, {"text": "beta"}, {"type": "img", "src": "x"}]
    s = _summarise_tool_result(blocks)
    assert "alpha" in s and "beta" in s
    assert len(s) <= 400


def test_summarise_tool_result_other_types():
    assert _summarise_tool_result(123) == "123"
    assert _summarise_tool_result(None) == "None"


class _FakeChunk:
    """Minimal stand-in for a LangChain message chunk with .content."""

    def __init__(self, content: str):
        self.content = content


class _FakeGraph:
    """Yields scripted v2 StreamParts. Supports ``astream`` only."""

    def __init__(self, parts: list[dict]):
        self._parts = parts

    async def astream(self, _input, stream_mode=None, version=None):
        for p in self._parts:
            yield p


@pytest.mark.asyncio
async def test_stream_turn_emits_token_and_done():
    parts = [
        {"type": "messages", "data": (_FakeChunk("Hello "), {})},
        {"type": "messages", "data": (_FakeChunk("world"), {})},
    ]
    out = b"".join([c async for c in stream_turn(_FakeGraph(parts), [])])
    assert b"event: token" in out
    assert b"Hello " in out
    assert b"world" in out
    assert out.endswith(b"event: done\ndata: {}\n\n")


@pytest.mark.asyncio
async def test_stream_turn_emits_status_from_custom():
    parts = [{"type": "custom", "data": {"msg": "thinking"}}]
    out = b"".join([c async for c in stream_turn(_FakeGraph(parts), [])])
    assert b'event: status\ndata: {"msg": "thinking"}\n\n' in out


@pytest.mark.asyncio
async def test_stream_turn_emits_error_on_exception():
    class _Boom:
        async def astream(self, *a, **kw):
            raise RuntimeError("boom")
            yield  # makes the function an async generator

    out = b"".join([c async for c in stream_turn(_Boom(), [])])
    assert b"event: error" in out
    payload_line = [ln for ln in out.split(b"\n") if ln.startswith(b"data:")][0]
    data = json.loads(payload_line[len(b"data: "):])
    assert data["message"] == "agent_error"
    assert "boom" in data["detail"]
    assert out.endswith(b"event: done\ndata: {}\n\n")


@pytest.mark.asyncio
async def test_stream_turn_ignores_unknown_part_type():
    parts = [{"type": "debug", "data": {"x": 1}}]
    out = b"".join([c async for c in stream_turn(_FakeGraph(parts), [])])
    # No token/status emitted, just the trailing done.
    assert b"event: token" not in out
    assert b"event: status" not in out
    assert out.endswith(b"event: done\ndata: {}\n\n")


@pytest.mark.asyncio
async def test_stream_turn_handles_malformed_part():
    parts = [{"weird": True}, "not a dict", None]
    out = b"".join([c async for c in stream_turn(_FakeGraph(parts), [])])
    assert out.endswith(b"event: done\ndata: {}\n\n")


def _ai_with_tool_calls():
    from langchain_core.messages import AIMessage
    return AIMessage(content="", tool_calls=[{"id": "tc1", "name": "get_section", "args": {"act": "IPC", "section_number": "302"}}])


def _tool_msg():
    from langchain_core.messages import ToolMessage
    return ToolMessage(content="Whoever commits murder shall be punished…", tool_call_id="tc1", name="get_section")


@pytest.mark.asyncio
async def test_stream_turn_emits_tool_start_and_result():
    parts = [
        {"type": "messages", "data": (_ai_with_tool_calls(), {})},
        {"type": "messages", "data": (_tool_msg(), {})},
    ]
    out = b"".join([c async for c in stream_turn(_FakeGraph(parts), [])])
    assert b"event: tool_start" in out
    assert b"event: tool_result" in out
    assert b"get_section" in out
    assert b"murder" in out


@pytest.mark.asyncio
async def test_stream_turn_string_content_emits_token():
    # A chunk whose .content is a non-empty string.
    parts = [{"type": "messages", "data": (_FakeChunk("answer"), {})}]
    out = b"".join([c async for c in stream_turn(_FakeGraph(parts), [])])
    assert b'event: token\ndata: {"content": "answer"}\n\n' in out


@pytest.mark.asyncio
async def test_stream_turn_empty_content_skipped():
    parts = [{"type": "messages", "data": (_FakeChunk(""), {})}]
    out = b"".join([c async for c in stream_turn(_FakeGraph(parts), [])])
    assert b"event: token" not in out
