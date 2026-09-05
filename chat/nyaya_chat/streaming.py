"""Project the graph's custom-stream events onto Server-Sent Events.

The graph's nodes emit every semantic event themselves — through
``graph/events`` on LangGraph's custom stream mode — as dicts shaped like
the SSE contract. This module is a pure projection layer: it serializes
each dict as ``event: <type>`` with the remaining keys as the data payload,
interleaves keepalive pings, and wraps the whole turn in the ``error`` /
``done`` bookends:

  event: meta        data: {"request_id": "..."}   — request id (server.py)
  event: status      data: {"msg": "...", "rid": "..."} — phase transition (nodes)
  event: plan        data: {"content": "..."}       — supervisor plan text
  event: token       data: {"content": "..."}       — synthesis LLM token deltas
  event: reasoning   data: {"content": "..."}       — reasoning_content deltas
  event: tool_start  data: {"name","args","id"}     — the model called a tool
  event: tool_result data: {"name","summary","id"}  — a tool finished
  event: citations data: {"citations": [...]}       — parsed from the VERIFIED answer
  event: correction data: {"content": "..."}        — the verified answer, ONLY when
                                                      it differs from the raw tokens
  event: ping        data: {"ts": 1234567890}       — keepalive (every ~15s)
  event: error       data: {"message","detail","rid"} — a node threw (unified shape)
  event: done        data: {}                       — stream complete

Citation verification runs ONCE, inside the synthesis node; the ``citations``
and ``correction`` events arrive already computed from the node. Nothing here
re-derives semantics from node names or message chunks — the old
``["messages", "updates"]`` dual-mode inference (and its node-name string
coupling) is gone by construction.

A keepalive ``ping`` event is emitted every ``keepalive_interval`` seconds
to prevent proxy timeouts (nginx default: 60s). The client resets its
timeout timer on any event including ping.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

log = logging.getLogger("nyaya_chat.streaming")


def _sse(event: str, payload: Any) -> bytes:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


async def stream_turn(
    graph: Any,
    messages: list[Any],
    *,
    keepalive_interval: float = 0,
    rid: str = "",
    config: dict[str, Any] | None = None,
) -> AsyncIterator[bytes]:
    """Yield SSE-encoded bytes for a single agent turn.

    Iterates the graph's custom stream (``stream_mode=["custom"]``), emitting
    the events documented at module level. Catches exceptions and emits an
    ``error`` event followed by ``done`` so the client always sees a clean
    stream end.

    ``rid`` is the request id echoed on every ``status`` and ``error`` event;
    a fresh one is generated when the caller (server.py) doesn't supply it so
    the error contract's ``rid`` is always non-blank. It is seeded into graph
    state so every node's emitters share it.

    ``config`` (e.g. ``{"callbacks": [...]}`` for observability) is passed to
    ``graph.astream`` — the single observability wiring point for the turn.

    If ``keepalive_interval`` > 0, emits a ``ping`` event every N seconds
    to prevent proxy timeouts. The ping is emitted between graph events
    using asyncio timeout on the stream iteration.
    """
    if not rid:
        rid = uuid.uuid4().hex
    turn_started = time.monotonic()
    usage: dict[str, int] | None = None

    try:
        async for payload in _stream_with_keepalive(
            graph, messages, rid, keepalive_interval, config,
        ):
            if payload is True:
                # Keepalive signal from _stream_with_keepalive (wait expired)
                yield _sse("ping", {"ts": int(time.time())})
                continue

            if not isinstance(payload, dict) or "type" not in payload:
                continue

            if payload.get("type") == "usage":
                um = payload.get("usage")
                if isinstance(um, dict):
                    ints = {k: v for k, v in um.items() if isinstance(v, int)}
                    if ints:
                        usage = ints
                continue

            ptype = payload.pop("type")
            yield _sse(ptype, payload)

    except asyncio.CancelledError:
        log.info("stream cancelled by client")
        raise
    except Exception as exc:
        log.error("agent stream failed: %s", exc, exc_info=True)
        yield _sse("error", {
            "message": "agent_error",
            "detail": "internal server error",
            "rid": rid,
        })
    finally:
        # The per-turn usage log line (rid, duration, tokens), emitted
        # whenever the stream loop ends — clean finish, mid-stream failure,
        # or client cancellation — with tokens 0/absent when the usage
        # event was not seen.
        duration_ms = (time.monotonic() - turn_started) * 1000.0
        if usage:
            input_tokens = usage.get("input_tokens", 0)
            output_tokens = usage.get("output_tokens", 0)
            log.info(
                "chat turn complete: rid=%s duration_ms=%.0f token_count=%d "
                "(input_tokens=%d output_tokens=%d)",
                rid, duration_ms, input_tokens + output_tokens,
                input_tokens, output_tokens,
            )
        else:
            log.info(
                "chat turn complete: rid=%s duration_ms=%.0f token_count=0 "
                "(usage_metadata absent)",
                rid, duration_ms,
            )

    yield _sse("done", {})


async def _stream_with_keepalive(
    graph: Any,
    messages: list[Any],
    rid: str,
    keepalive_interval: float,
    config: dict[str, Any] | None,
) -> AsyncIterator[Any]:
    """Yield custom-stream payloads, interleaving ``True`` ping signals when idle.

    ``asyncio.timeout(keepalive_interval)`` wraps each ``__anext__`` of the
    payload source: when a wait expires, a ping signal is yielded and the
    wait resumes — no event is ever dropped or reordered, and the timeout is
    armed/disarmed around every single event instead of racing two tasks per
    event (the old ``asyncio.wait`` pattern).

    The graph stream itself is drained by ONE background producer task per
    stream (not per event) into an ``asyncio.Queue``; the timeout then fires
    on the consumer's ``queue.get()`` wait only, so an expiring timeout can
    never cancel the underlying graph stream (cancelling an async
    generator's ``__anext__`` would terminate the whole graph iteration).
    On cancellation of the outer stream, the producer is cancelled and
    awaited in ``finally`` so no tasks linger.
    """
    stream_input = {"messages": messages, "rid": rid}
    stream_kwargs: dict[str, Any] = {
        "stream_mode": ["custom"],
        "config": config or None,
    }

    if keepalive_interval is None or keepalive_interval <= 0:
        async for part in graph.astream(stream_input, **stream_kwargs):
            yield _unwrap_custom(part)
        return

    chunk_queue: asyncio.Queue[Any] = asyncio.Queue()
    _SENTINEL = object()

    async def _produce():
        try:
            async for part in graph.astream(stream_input, **stream_kwargs):
                await chunk_queue.put(part)
        except Exception as exc:
            await chunk_queue.put(exc)
        finally:
            await chunk_queue.put(_SENTINEL)

    producer_task = asyncio.ensure_future(_produce())

    try:
        while True:
            try:
                async with asyncio.timeout(keepalive_interval):
                    part = await chunk_queue.get()
            except TimeoutError:
                yield True  # keepalive ping signal
                continue
            if part is _SENTINEL:
                break
            if isinstance(part, Exception):
                raise part
            yield _unwrap_custom(part)
    finally:
        if not producer_task.done():
            producer_task.cancel()
        try:
            await producer_task
        except asyncio.CancelledError:
            pass


def _unwrap_custom(part: Any) -> Any:
    """Normalise a stream part to its custom payload.

    With ``stream_mode`` given as a list, LangGraph yields ``(mode, data)``
    tuples; with a single string mode it yields the data directly. Either
    way this returns the emitted event dict (or the part untouched when it
    isn't a recognisable tuple — the projection layer filters non-dicts).
    """
    if (
        isinstance(part, tuple)
        and len(part) == 2
        and isinstance(part[0], str)
    ):
        return part[1]
    return part
