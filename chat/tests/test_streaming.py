"""Tests for nyaya_chat.streaming — SSE encoding of LangGraph stream parts."""

from __future__ import annotations

import asyncio
import json
import logging

import pytest
from langchain_core.messages import AIMessage

from nyaya_chat.streaming import (
    _sse,
    _stream_with_keepalive,
    _summarise_tool_result,
    stream_turn,
)


def _parse_events(out: bytes) -> list[tuple[str, dict]]:
    """Parse SSE bytes into a list of (event, payload) tuples."""
    events: list[tuple[str, dict]] = []
    for block in out.decode("utf-8").split("\n\n"):
        if not block.strip():
            continue
        event = "message"
        data = ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data = line[len("data:"):]
        events.append((event, json.loads(data)))
    return events


def test_sse_format():
    b = _sse("token", {"content": "hi"})
    assert b == b'event: token\ndata: {"content": "hi"}\n\n'


def test_sse_non_ascii_preserved():
    b = _sse("token", {"content": "Namaste — §"})
    # ensure_ascii=False so the em-dash and section sign stay literal.
    assert "Namaste — §".encode() in b


def test_summarise_tool_result_string():
    assert _summarise_tool_result("short") == "short"
    assert _summarise_tool_result("x" * 9000) == "x" * 8000


def test_summarise_tool_result_strips_corpus_text_wrapper():
    """Regression: <corpus_text> wrapper tags must not reach the UI summary."""
    content = "<corpus_text>\nIPC s.302 punishment text\n</corpus_text>"
    s = _summarise_tool_result(content)
    assert "<corpus_text>" not in s
    assert "</corpus_text>" not in s
    assert "IPC s.302 punishment text" in s


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

    def __init__(self, content: str, additional_kwargs: dict | None = None):
        self.content = content
        self.additional_kwargs = additional_kwargs or {}


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
async def test_stream_turn_emits_status_skipped_for_non_messages():
    """Non-messages/non-updates part types are skipped."""
    parts = [{"type": "custom", "data": {"msg": "thinking"}}]
    out = b"".join([c async for c in stream_turn(_FakeGraph(parts), [])])
    # No status emitted from custom-type parts.
    assert b"event: status" not in out
    assert out.endswith(b"event: done\ndata: {}\n\n")


@pytest.mark.asyncio
async def test_stream_turn_emits_error_on_exception():
    class _Boom:
        async def astream(self, *a, **kw):
            raise RuntimeError("boom")
            yield  # makes the function an async generator

    out = b"".join([c async for c in stream_turn(_Boom(), [], rid="rid-err")])
    assert b"event: error" in out
    payload_line = [ln for ln in out.split(b"\n") if ln.startswith(b"data:")][0]
    data = json.loads(payload_line[len(b"data: "):])
    assert data["message"] == "agent_error"
    assert data["detail"] == "internal server error"
    assert data["rid"] == "rid-err"
    assert out.endswith(b"event: done\ndata: {}\n\n")


@pytest.mark.asyncio
async def test_stream_turn_error_rid_generated_when_not_supplied():
    """The error contract's rid is always non-blank, even without an explicit rid."""
    class _Boom:
        async def astream(self, *a, **kw):
            raise RuntimeError("boom")
            yield  # makes the function an async generator

    out = b"".join([c async for c in stream_turn(_Boom(), [])])
    errors = [p for e, p in _parse_events(out) if e == "error"]
    assert len(errors) == 1
    assert errors[0]["rid"]  # non-blank


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


@pytest.mark.asyncio
async def test_stream_turn_ignores_custom_part_type():
    """Custom-typed parts (from stream modes we don't use) are skipped."""
    parts = [{"type": "custom", "data": {"type": "citations", "citations": []}}]
    out = b"".join([c async for c in stream_turn(_FakeGraph(parts), [])])
    assert b"event: citations" not in out
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


class _FakeChunkListContent:
    """Stand-in for a message chunk whose .content is a list of content blocks.

    Some models return content as ``[{'type': 'text', 'text': '...'}]`` instead
    of a plain string. The streamer must extract the text rather than emit the
    Python repr of the list-of-dicts.
    """

    def __init__(self, blocks: list, additional_kwargs: dict | None = None):
        self.content = blocks
        self.additional_kwargs = additional_kwargs or {}


@pytest.mark.asyncio
async def test_stream_turn_list_content_blocks_extracted():
    """Content returned as a list of blocks is flattened to text — no repr leak."""
    blocks = [
        {"type": "text", "text": "Hello "},
        {"type": "text", "text": "world"},
    ]
    parts = [{"type": "messages", "data": (_FakeChunkListContent(blocks), {})}]
    out = b"".join([c async for c in stream_turn(_FakeGraph(parts), [])])
    assert b'event: token\ndata: {"content": "Hello world"}\n\n' in out
    # The raw Python repr of the list must NOT appear in the output.
    assert b"type" not in out  # no {'type': 'text', ...} leak
    assert b"lc_" not in out   # no 'lc_...' id leak


@pytest.mark.asyncio
async def test_stream_turn_list_content_non_text_blocks_ignored():
    """Non-text blocks (images, etc.) are silently skipped, not stringified."""
    blocks = [
        {"type": "text", "text": "answer"},
        {"type": "image_url", "image_url": {"url": "data:..."}},
    ]
    parts = [{"type": "messages", "data": (_FakeChunkListContent(blocks), {})}]
    out = b"".join([c async for c in stream_turn(_FakeGraph(parts), [])])
    assert b'event: token\ndata: {"content": "answer"}\n\n' in out
    assert b"image_url" not in out
    assert b"data:..." not in out


@pytest.mark.asyncio
async def test_stream_turn_tool_message_not_emitted_as_token():
    """ToolMessage content must NEVER leak as token events.

    ToolMessage content is tool output (JSON results from MCP tools).
    It should only appear in tool_result events, not in the answer text.
    This is a regression test for the raw tool-result repr that was leaking
    into the bot's answer bubble.
    """
    parts = [{"type": "messages", "data": (_tool_msg(), {})}]
    out = b"".join([c async for c in stream_turn(_FakeGraph(parts), [])])
    assert b"event: token" not in out
    assert b"event: tool_result" in out
    assert b"murder" in out  # appears in tool_result, not token


@pytest.mark.asyncio
async def test_stream_turn_tool_message_list_content_not_emitted_as_token():
    """ToolMessage with list-of-blocks content must not leak as token events.

    MCP adapters return content as ``[{'type': 'text', 'text': '...'}]``.
    This must be extracted for the tool_result summary but NEVER routed as
    token events into the answer text.
    """
    from langchain_core.messages import ToolMessage

    tool_msg = ToolMessage(
        content=[{"type": "text", "text": '{"act":"IPC","section":"302"}'}],
        tool_call_id="tc1",
        name="get_section",
    )
    parts = [{"type": "messages", "data": (tool_msg, {})}]
    out = b"".join([c async for c in stream_turn(_FakeGraph(parts), [])])
    assert b"event: token" not in out
    assert b"event: tool_result" in out
    # The content should appear in the tool_result summary
    assert b"IPC" in out


@pytest.mark.asyncio
async def test_stream_turn_emits_reasoning_event():
    """Reasoning content in additional_kwargs should emit event: reasoning."""
    parts = [
        {"type": "messages", "data": (_FakeChunk("", additional_kwargs={"reasoning_content": "Let me think..."}), {})},
        {"type": "messages", "data": (_FakeChunk("The answer is 42."), {})},
    ]
    out = b"".join([c async for c in stream_turn(_FakeGraph(parts), [])])
    assert b"event: reasoning" in out
    assert b"Let me think..." in out
    assert b"event: token" in out
    assert b"The answer is 42." in out


@pytest.mark.asyncio
async def test_stream_turn_reasoning_before_token():
    """Reasoning chunks should be emitted alongside tokens in order."""
    parts = [
        {"type": "messages", "data": (_FakeChunk("", additional_kwargs={"reasoning_content": "step 1"}), {})},
        {"type": "messages", "data": (_FakeChunk("Answer"), {})},
    ]
    out = b"".join([c async for c in stream_turn(_FakeGraph(parts), [])])
    reasoning_pos = out.find(b"event: reasoning")
    token_pos = out.find(b"event: token")
    assert reasoning_pos != -1
    assert token_pos != -1
    assert reasoning_pos < token_pos


# ── New tests for dual stream mode, plan events, and phase status ──


def _ai_supervisor_with_tools():
    """An AIMessage from the supervisor node with tool_calls."""
    from langchain_core.messages import AIMessage
    return AIMessage(
        content="I need to look up IPC section 302.",
        tool_calls=[{"id": "tc1", "name": "get_section", "args": {"act": "IPC", "section_number": "302"}}],
    )


def _ai_supervisor_no_tools():
    """An AIMessage from the supervisor node without tool_calls (degraded)."""
    from langchain_core.messages import AIMessage
    return AIMessage(content="I can answer this directly.")


@pytest.mark.asyncio
async def test_stream_turn_supervisor_content_emits_plan_not_token():
    """Content from the supervisor node should emit as 'plan', not 'token'."""
    parts = [
        {"type": "messages", "data": (_FakeChunk("Planning..."), {"langgraph_node": "supervisor"})},
    ]
    out = b"".join([c async for c in stream_turn(_FakeGraph(parts), [])])
    assert b"event: plan" in out
    assert b"Planning..." in out
    assert b"event: token" not in out


@pytest.mark.asyncio
async def test_stream_turn_synthesis_content_emits_token():
    """Content from the synthesis node should emit as 'token'."""
    parts = [
        {"type": "messages", "data": (_FakeChunk("Answer"), {"langgraph_node": "synthesis"})},
    ]
    out = b"".join([c async for c in stream_turn(_FakeGraph(parts), [])])
    assert b"event: token" in out
    assert b"Answer" in out
    assert b"event: plan" not in out


@pytest.mark.asyncio
async def test_stream_turn_unknown_node_content_emits_token():
    """Content from an unknown node (no metadata) should default to 'token'."""
    parts = [
        {"type": "messages", "data": (_FakeChunk("fallback"), {})},
    ]
    out = b"".join([c async for c in stream_turn(_FakeGraph(parts), [])])
    assert b"event: token" in out
    assert b"fallback" in out


@pytest.mark.asyncio
async def test_stream_turn_emits_searching_status_on_supervisor_with_tools():
    """When supervisor completes with tool_calls, emit 'searching' status."""
    parts = [
        {"type": "messages", "data": (_ai_supervisor_with_tools(), {"langgraph_node": "supervisor"})},
        {"type": "updates", "data": {"supervisor": {"messages": [_ai_supervisor_with_tools()]}}},
    ]
    out = b"".join([c async for c in stream_turn(_FakeGraph(parts), [])])
    assert b'event: status' in out
    assert b'searching' in out


@pytest.mark.asyncio
async def test_stream_turn_emits_composing_status_on_supervisor_no_tools():
    """When supervisor completes without tool_calls, emit 'composing' status."""
    parts = [
        {"type": "updates", "data": {"supervisor": {"messages": [_ai_supervisor_no_tools()]}}},
    ]
    out = b"".join([c async for c in stream_turn(_FakeGraph(parts), [])])
    assert b'event: status' in out
    assert b'composing' in out


@pytest.mark.asyncio
async def test_stream_turn_emits_composing_status_after_tools_complete():
    """When tools node completes, emit 'composing' status."""
    parts = [
        {"type": "updates", "data": {"supervisor": {"messages": [_ai_supervisor_with_tools()]}}},
        {"type": "updates", "data": {"tools": {"messages": [_tool_msg()]}}},
    ]
    out = b"".join([c async for c in stream_turn(_FakeGraph(parts), [])])
    assert b'searching' in out
    assert b'composing' in out


@pytest.mark.asyncio
async def test_stream_turn_does_not_emit_duplicate_status():
    """Phase status should not be emitted more than once."""
    parts = [
        {"type": "updates", "data": {"supervisor": {"messages": [_ai_supervisor_with_tools()]}}},
        {"type": "updates", "data": {"supervisor": {"messages": [_ai_supervisor_with_tools()]}}},
    ]
    out = b"".join([c async for c in stream_turn(_FakeGraph(parts), [])])
    # Should only have one 'searching' status event
    assert out.count(b'event: status') == 1


# ── Unified SSE contract: rid on every status event, {message, detail, rid}
#    errors, citations derived from the verified message, correction only on
#    diff ──


@pytest.mark.asyncio
async def test_stream_turn_status_events_carry_rid():
    """Every status event (all emitters in stream_turn) echoes the request id."""
    parts = [
        {"type": "updates", "data": {"supervisor": {"messages": [_ai_supervisor_with_tools()]}}},
        {"type": "updates", "data": {"tools": {"messages": [_tool_msg()]}}},
    ]
    out = b"".join([c async for c in stream_turn(_FakeGraph(parts), [], rid="rid-1")])
    statuses = [p for e, p in _parse_events(out) if e == "status"]
    assert [p["msg"] for p in statuses] == ["searching", "composing"]
    assert all(p["rid"] == "rid-1" for p in statuses)


def _synthesis_round(raw: str, verified: str) -> list[dict]:
    """Scripted parts: synthesis tokens streamed, then the node's verified update."""
    return [
        {"type": "messages", "data": (_FakeChunk(raw), {"langgraph_node": "synthesis"})},
        {"type": "updates", "data": {"synthesis": {"messages": [AIMessage(content=verified)]}}},
    ]


@pytest.mark.asyncio
async def test_stream_turn_citations_derived_from_verified_message():
    """The citations event parses the VERIFIED answer, not the raw streamed text."""
    raw = "Murder is punishable [[act: IPC, ref: s. 302]] and [[act: GhostAct, ref: 9]]."
    verified = "Murder is punishable [[act: IPC, ref: s. 302]]."
    out = b"".join([c async for c in stream_turn(_FakeGraph(_synthesis_round(raw, verified)), [])])
    citations = [p for e, p in _parse_events(out) if e == "citations"]
    assert citations == [{"citations": [{"act": "IPC", "ref": "s. 302"}]}]


@pytest.mark.asyncio
async def test_stream_turn_no_citations_event_when_verified_has_none():
    verified = "No citations here."
    out = b"".join([c async for c in stream_turn(_FakeGraph(_synthesis_round(verified, verified)), [])])
    assert b"event: citations" not in out


@pytest.mark.asyncio
async def test_stream_turn_correction_emitted_only_when_raw_differs():
    """correction carries the verified answer, ONLY when raw != verified."""
    raw = "Answer."
    verified = "Answer.\n\n*This is not legal advice; verify citations before filing.*"
    out = b"".join([c async for c in stream_turn(_FakeGraph(_synthesis_round(raw, verified)), [])])
    corrections = [p for e, p in _parse_events(out) if e == "correction"]
    assert corrections == [{"content": verified}]


@pytest.mark.asyncio
async def test_stream_turn_no_correction_when_raw_equals_verified():
    """When the streamed tokens already match the verified answer, no correction."""
    answer = "Answer with citation [[act: IPC, ref: s. 302]]."
    out = b"".join([c async for c in stream_turn(_FakeGraph(_synthesis_round(answer, answer)), [])])
    assert b"event: correction" not in out
    # Citations are still derived from the verified message.
    citations = [p for e, p in _parse_events(out) if e == "citations"]
    assert citations == [{"citations": [{"act": "IPC", "ref": "s. 302"}]}]


@pytest.mark.asyncio
async def test_stream_turn_no_post_stream_disclaimer_append():
    """The disclaimer lives in the verified message; the streamer never appends it."""
    raw = "Answer without a disclaimer."
    out = b"".join([c async for c in stream_turn(_FakeGraph(_synthesis_round(raw, raw)), [])])
    assert b"not legal advice" not in out
    assert b"event: correction" not in out


@pytest.mark.asyncio
async def test_stream_turn_last_synthesis_round_wins():
    """With reflection, the LAST verified synthesis round is authoritative."""
    parts = [
        *_synthesis_round("Round 1 answer.", "Round 1 verified."),
        *_synthesis_round("Round 2 answer.", "Round 2 verified."),
    ]
    out = b"".join([c async for c in stream_turn(_FakeGraph(parts), [])])
    corrections = [p for e, p in _parse_events(out) if e == "correction"]
    assert corrections == [{"content": "Round 2 verified."}]


@pytest.mark.asyncio
async def test_stream_turn_degraded_agent_node_verified_answer_used():
    """The degraded no-tools graph names its synthesis node 'agent'; its update
    is still treated as the verified answer."""
    parts = [
        {"type": "messages", "data": (_FakeChunk("raw"), {"langgraph_node": "agent"})},
        {"type": "updates", "data": {"agent": {"messages": [AIMessage(content="verified")]}}},
    ]
    out = b"".join([c async for c in stream_turn(_FakeGraph(parts), [])])
    corrections = [p for e, p in _parse_events(out) if e == "correction"]
    assert corrections == [{"content": "verified"}]


# ── Keepalive: asyncio.timeout-based ping emit + exact ordering ────────


class _StallGraph:
    """Yields scripted parts, sleeping ``delay`` seconds before each one."""

    def __init__(self, spec: list[tuple[dict, float]]):
        self._spec = spec

    async def astream(self, _input, stream_mode=None, version=None):
        for part, delay in self._spec:
            await asyncio.sleep(delay)
            yield part


class _HangGraph:
    """Yields one part, then stalls (simulates a slow synthesis round)."""

    async def astream(self, _input, stream_mode=None, version=None):
        yield {"type": "messages", "data": (_FakeChunk("first"), {})}
        await asyncio.sleep(30)  # stalled; the client disconnects meanwhile
        yield {"type": "messages", "data": (_FakeChunk("never"), {})}


@pytest.mark.asyncio
async def test_stream_turn_keepalive_pings_between_items_in_order():
    """A stream that stalls longer than the keepalive emits ping events between
    items and still yields ALL items, in order."""
    spec = [
        ({"type": "messages", "data": (_FakeChunk("one"), {})}, 0.15),
        ({"type": "messages", "data": (_FakeChunk("two"), {})}, 0.35),
    ]
    out = b"".join([
        c async for c in stream_turn(_StallGraph(spec), [], keepalive_interval=0.05, rid="ka")
    ])
    events = _parse_events(out)
    tokens = [p["content"] for e, p in events if e == "token"]
    assert tokens == ["one", "two"]  # all items, in order
    pings = [p for e, p in events if e == "ping"]
    assert len(pings) >= 4  # pings were emitted during both stalls
    assert all(isinstance(p["ts"], int) for p in pings)
    # done is still the last event; no error event crept in.
    assert events[-1][0] == "done"
    assert not any(e == "error" for e, _ in events)


@pytest.mark.asyncio
async def test_stream_turn_keepalive_no_ping_when_chunks_flow():
    """Chunks arriving faster than the keepalive interval produce no pings."""
    spec = [
        ({"type": "messages", "data": (_FakeChunk("a"), {})}, 0.0),
        ({"type": "messages", "data": (_FakeChunk("b"), {})}, 0.01),
    ]
    out = b"".join([
        c async for c in stream_turn(_StallGraph(spec), [], keepalive_interval=1.0)
    ])
    assert b"event: ping" not in out
    assert b'event: token\ndata: {"content": "a"}' in out


@pytest.mark.asyncio
async def test_stream_with_keepalive_no_interval_streams_directly():
    """Without a keepalive interval the graph stream is iterated as before."""
    parts = [{"type": "messages", "data": (_FakeChunk("x"), {})}]
    got = [p async for p in _stream_with_keepalive(_FakeGraph(parts), [], 0)]
    assert got == parts


@pytest.mark.asyncio
async def test_stream_turn_cancellation_midstream_is_clean():
    """Cancelling a mid-stream client closes cleanly: the background producer
    task is reaped, no tasks linger, and the stream output stays pristine."""
    agen = stream_turn(_HangGraph(), [], keepalive_interval=0.02, rid="cc")
    first = await agen.__anext__()
    assert first == b'event: token\ndata: {"content": "first"}\n\n'
    second = await agen.__anext__()
    assert second.startswith(b"event: ping\n")  # pings while idle

    await agen.aclose()
    await asyncio.sleep(0.05)  # let the cancelled producer settle

    pending = [t for t in asyncio.all_tasks()
               if t is not asyncio.current_task() and not t.done()]
    assert not pending, f"lingering stream tasks: {pending}"

    await agen.aclose()
    await asyncio.sleep(0.05)  # let the cancelled producer settle

    pending = [t for t in asyncio.all_tasks()
               if t is not asyncio.current_task() and not t.done()]
    assert not pending, f"lingering stream tasks: {pending}"


# ── Per-turn token accounting (usage_metadata in the turn log) ──────────


class _UsageChunk:
    """A message chunk that also carries the model's usage_metadata block."""

    def __init__(self, content: str, usage_metadata: dict):
        self.content = content
        self.additional_kwargs: dict = {}
        self.usage_metadata = usage_metadata


@pytest.mark.asyncio
async def test_stream_turn_logs_token_count_from_usage_metadata(caplog):
    """usage_metadata on the streamed chunks lands in the per-turn log line."""
    parts = [{
        "type": "messages",
        "data": (_UsageChunk(
            "Answer.",
            {"input_tokens": 900, "output_tokens": 84, "total_tokens": 984},
        ), {"langgraph_node": "synthesis"}),
    }]
    with caplog.at_level(logging.INFO, logger="nyaya_chat.streaming"):
        b"".join([c async for c in stream_turn(_FakeGraph(parts), [], rid="tok")])
    records = [r for r in caplog.records if "token_count" in r.getMessage()]
    assert records, "expected a per-turn token_count log record"
    line = records[-1].getMessage()
    assert "rid=tok" in line
    assert "duration_ms=" in line
    assert "token_count=984" in line
    assert "input_tokens=900" in line
    assert "output_tokens=84" in line


@pytest.mark.asyncio
async def test_stream_turn_logs_token_count_zero_when_usage_absent(caplog):
    """No usage_metadata on the chunks -> token_count=0, no exception."""
    parts = [{"type": "messages", "data": (_FakeChunk("Answer."), {})}]
    with caplog.at_level(logging.INFO, logger="nyaya_chat.streaming"):
        b"".join([c async for c in stream_turn(_FakeGraph(parts), [], rid="nou")])
    lines = [r.getMessage() for r in caplog.records if "token_count" in r.getMessage()]
    assert any("rid=nou" in ln and "token_count=0" in ln for ln in lines)
