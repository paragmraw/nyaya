"""Tests for nyaya_chat.streaming — SSE projection of custom-stream payloads.

The graph's nodes emit typed event dicts on LangGraph's custom stream mode;
``stream_turn`` is a pure projection onto ``event: <type>`` SSE frames. The
old dual-mode (messages/updates) inference and its node-name string coupling
are gone, so tests here cover the projection, keepalive, error bookends, and
per-turn usage logging — semantic event content is asserted at the node level
(test_graph.py).
"""

from __future__ import annotations

import asyncio
import json
import logging

import pytest

from nyaya_chat.streaming import (
    _sse,
    _stream_with_keepalive,
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


class _FakeGraph:
    """Yields scripted custom-stream parts. Supports ``astream`` only."""

    def __init__(self, parts: list):
        self._parts = parts
        self.inputs: list = []
        self.kwargs: list[dict] = []

    async def astream(self, _input, **kw):
        self.inputs.append(_input)
        self.kwargs.append(kw)
        for p in self._parts:
            yield p


@pytest.mark.asyncio
async def test_stream_turn_projects_payload_dicts_to_sse():
    """Each emitted event dict becomes one SSE frame with the type as event."""
    parts = [
        {"type": "status", "msg": "searching", "rid": "r1"},
        {"type": "token", "content": "Hello "},
        {"type": "token", "content": "world"},
        {"type": "citations", "citations": [{"act": "IPC", "ref": "s. 302"}]},
    ]
    out = b"".join([c async for c in stream_turn(_FakeGraph(parts), [])])
    events = _parse_events(out)
    assert events == [
        ("status", {"msg": "searching", "rid": "r1"}),
        ("token", {"content": "Hello "}),
        ("token", {"content": "world"}),
        ("citations", {"citations": [{"act": "IPC", "ref": "s. 302"}]}),
        ("done", {}),
    ]


@pytest.mark.asyncio
async def test_stream_turn_unwraps_mode_tuples():
    """With stream_mode as a list, LangGraph yields (mode, data) tuples —
    the data half is projected, the mode marker is dropped."""
    parts = [
        ("custom", {"type": "token", "content": "hi"}),
        ("custom", {"type": "plan", "content": "plan text"}),
    ]
    out = b"".join([c async for c in stream_turn(_FakeGraph(parts), [])])
    assert b'event: token\ndata: {"content": "hi"}\n\n' in out
    assert b'event: plan\ndata: {"content": "plan text"}\n\n' in out


@pytest.mark.asyncio
async def test_stream_turn_seeds_rid_into_graph_state():
    """The request id is seeded into graph input so every node's emitters
    share it without threading it through node signatures."""
    graph = _FakeGraph([])
    out = b"".join([c async for c in stream_turn(graph, [], rid="rid-7")])
    assert graph.inputs == [{"messages": [], "rid": "rid-7"}]
    assert out.endswith(b"event: done\ndata: {}\n\n")


@pytest.mark.asyncio
async def test_stream_turn_passes_config_to_astream():
    """The observability config flows to the graph as ONE astream kwarg."""
    graph = _FakeGraph([])
    cfg = {"callbacks": ["cb"]}
    b"".join([c async for c in stream_turn(graph, [], config=cfg)])
    assert graph.kwargs[0]["config"] == cfg
    assert graph.kwargs[0]["stream_mode"] == ["custom"]


@pytest.mark.asyncio
async def test_stream_turn_maps_turn_error_to_code():
    """A TurnError from a node projects its code as ``error.message`` — the
    stable machine key the frontend humanizer maps — instead of the generic
    agent_error bookend."""
    from nyaya_chat.errors import TurnError

    class _TurnErrorGraph:
        async def astream(self, *a, **kw):
            raise TurnError("empty_response", "the synthesis model returned nothing")
            yield  # makes the function an async generator

    out = b"".join([c async for c in stream_turn(_TurnErrorGraph(), [], rid="rid-te")])
    errors = [p for e, p in _parse_events(out) if e == "error"]
    assert len(errors) == 1
    assert errors[0]["message"] == "empty_response"
    assert errors[0]["detail"] == "the synthesis model returned nothing"
    assert errors[0]["rid"] == "rid-te"
    assert out.endswith(b"event: done\ndata: {}\n\n")


@pytest.mark.asyncio
async def test_stream_turn_seeds_deadline_into_graph_state():
    """budget_s > 0 seeds a ``deadline`` (monotonic future timestamp) into the
    stream input so nodes can check it between phases."""
    import time as _time

    graph = _FakeGraph([])
    b"".join([c async for c in stream_turn(graph, [], rid="dl", budget_s=120.0)])
    deadline = graph.inputs[0]["deadline"]
    assert deadline > _time.monotonic()  # in the future
    assert deadline - _time.monotonic() <= 120.0

    # No budget → no deadline key.
    graph2 = _FakeGraph([])
    b"".join([c async for c in stream_turn(graph2, [], rid="dl2")])
    assert "deadline" not in graph2.inputs[0]


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
async def test_stream_turn_handles_malformed_part():
    """Non-dict payloads and dicts without a type key are skipped."""
    parts = [{"weird": True}, "not a dict", None]
    out = b"".join([c async for c in stream_turn(_FakeGraph(parts), [])])
    assert out == b"event: done\ndata: {}\n\n"


@pytest.mark.asyncio
async def test_stream_turn_usage_event_captured_not_projected():
    """The usage event feeds the per-turn log, never reaches the wire."""
    parts = [
        {"type": "usage", "usage": {"input_tokens": 10, "output_tokens": 5}},
        {"type": "token", "content": "Answer."},
    ]
    out = b"".join([c async for c in stream_turn(_FakeGraph(parts), [])])
    assert b"event: usage" not in out
    assert b"event: token" in out


# ── Keepalive: asyncio.timeout-based ping emit + exact ordering ────────


class _StallGraph:
    """Yields scripted parts, sleeping ``delay`` seconds before each one."""

    def __init__(self, spec: list[tuple[dict, float]]):
        self._spec = spec

    async def astream(self, _input, **kw):
        for part, delay in self._spec:
            await asyncio.sleep(delay)
            yield part


class _HangGraph:
    """Yields one part, then stalls (simulates a slow synthesis round)."""

    async def astream(self, _input, **kw):
        yield {"type": "token", "content": "first"}
        await asyncio.sleep(30)  # stalled; the client disconnects meanwhile
        yield {"type": "token", "content": "never"}


@pytest.mark.asyncio
async def test_stream_turn_keepalive_pings_between_items_in_order():
    """A stream that stalls longer than the keepalive emits ping events between
    items and still yields ALL items, in order."""
    spec = [
        ({"type": "token", "content": "one"}, 0.15),
        ({"type": "token", "content": "two"}, 0.35),
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
        ({"type": "token", "content": "a"}, 0.0),
        ({"type": "token", "content": "b"}, 0.01),
    ]
    out = b"".join([
        c async for c in stream_turn(_StallGraph(spec), [], keepalive_interval=1.0)
    ])
    assert b"event: ping" not in out
    assert b'event: token\ndata: {"content": "a"}' in out


@pytest.mark.asyncio
async def test_stream_with_keepalive_no_interval_streams_directly():
    """Without a keepalive interval the graph stream is iterated as before."""
    parts = [{"type": "token", "content": "x"}]
    got = [p async for p in _stream_with_keepalive(_FakeGraph(parts), [], "r", 0, None)]
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


# ── Per-turn token accounting (the usage event feeds the turn log) ──────


@pytest.mark.asyncio
async def test_stream_turn_logs_token_count_from_usage_event(caplog):
    """The graph's usage event lands in the per-turn log line."""
    parts = [
        {"type": "token", "content": "Answer."},
        {"type": "usage", "usage": {"input_tokens": 900, "output_tokens": 84, "total_tokens": 984}},
    ]
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
    """No usage event -> token_count=0, no exception."""
    parts = [{"type": "token", "content": "Answer."}]
    with caplog.at_level(logging.INFO, logger="nyaya_chat.streaming"):
        b"".join([c async for c in stream_turn(_FakeGraph(parts), [], rid="nou")])
    lines = [r.getMessage() for r in caplog.records if "token_count" in r.getMessage()]
    assert any("rid=nou" in ln and "token_count=0" in ln for ln in lines)
