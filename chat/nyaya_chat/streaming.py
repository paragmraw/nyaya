"""Encode LangGraph v2 stream events into Server-Sent Events.

LangGraph's ``astream(..., stream_mode=["messages", "updates"], version="v2")``
yields ``StreamPart`` dicts with ``type``/``ns``/``data``. We project them
into typed SSE events the frontend parses:

  event: meta        data: {"request_id": "..."}   — request id (server.py)
  event: status      data: {"msg": "...", "rid": "..."} — phase transition (all emitters)
  event: plan        data: {"content": "..."}       — supervisor plan text
  event: token       data: {"content": "..."}       — synthesis LLM token deltas
  event: reasoning   data: {"content": "..."}       — reasoning_content deltas
  event: tool_start  data: {"name","args","id"}     — the model called a tool
  event: tool_result data: {"name","summary","id"}  — a tool finished
  event: citations data: {"citations": [...]}       — citations parsed from the VERIFIED answer
  event: correction data: {"content": "..."}        — the verified answer, ONLY when it
                                                      differs from the raw streamed tokens
  event: ping        data: {"ts": 1234567890}       — keepalive (every ~15s)
  event: error       data: {"message","detail","rid"} — a node threw (unified error shape)
  event: done        data: {}                        — stream complete

Citation verification runs ONCE, in the synthesis node (agent.py); the
verified AIMessage it returns is the authoritative answer. This module never
re-verifies: it derives the ``citations`` event from the verified message and
emits ``correction`` only when the raw accumulated token text differs from it
(a plain string comparison). What the client ends with therefore equals what
the reflection check routes on.

We use **dual stream mode** (``["messages", "updates"]``):

* ``"messages"`` yields per-token chunks with metadata identifying which
  LangGraph node produced them. We route supervisor-node content to ``plan``
  events and synthesis-node content to ``token`` events.

* ``"updates"`` yields a dict keyed by node name when each node completes.
  We use this to emit phase-transition ``status`` events.

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

from langchain_core.messages import AIMessage, ToolMessage

from .agent import DEGRADED_NODE_NAME, SYNTHESIS_NODE_NAME
from .citations import parse_citations
from .tool_content import clean_tool_content

log = logging.getLogger("nyaya_chat.streaming")


def _sse(event: str, payload: Any) -> bytes:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def _summarise_tool_result(content: Any) -> str:
    """Collapse a ToolMessage content blob to a string for the UI panel.

    Thin wrapper over :func:`nyaya_chat.tool_content.clean_tool_content`
    with corpus-tag stripping enabled: by the time the streamer sees a
    ToolMessage, an earlier synthesis round may have wrapped its content in
    ``<corpus_text>`` tags, which must not reach the UI summary. The agent's
    dedup node cleans results before wrapping, so it uses the default.
    """
    return clean_tool_content(content, strip_corpus=True)


async def stream_turn(
    graph: Any,
    messages: list[Any],
    *,
    keepalive_interval: float = 0,
    rid: str = "",
) -> AsyncIterator[bytes]:
    """Yield SSE-encoded bytes for a single agent turn.

    Iterates the graph's v2 async stream (dual mode: ``messages`` +
    ``updates``), emitting the events documented at module level. Catches
    per-node exceptions and emits an ``error`` event followed by ``done`` so
    the client always sees a clean stream end.

    ``rid`` is the request id echoed on every ``status`` and ``error`` event;
    a fresh one is generated when the caller (server.py) doesn't supply it so
    the error contract's ``rid`` is always non-blank.

    If ``keepalive_interval`` > 0, emits a ``ping`` event every N seconds
    to prevent proxy timeouts. The ping is emitted between graph chunks
    using asyncio timeout on the stream iteration.
    """
    if not rid:
        rid = uuid.uuid4().hex
    try:
        pending_tool_calls: dict[str, dict[str, Any]] = {}
        emitted_phases: set[str] = set()
        # Raw synthesis token text (what the client has already rendered) and
        # the synthesis node's VERIFIED answer (the authoritative final text).
        full_answer_parts: list[str] = []
        verified_answer: str | None = None
        # Per-turn token usage accounting. LangChain message chunks carry
        # ``usage_metadata`` (cumulative per model call) — the last block seen
        # therefore holds the totals for the final synthesis round.
        turn_started = time.monotonic()
        usage: dict[str, int] | None = None

        try:
            async for part in _stream_with_keepalive(
                graph, messages, keepalive_interval,
            ):
                if isinstance(part, bool) and part:
                    # Keepalive signal from _stream_with_keepalive (wait expired)
                    yield _sse("ping", {"ts": int(time.time())})
                    continue

                if not isinstance(part, dict) or "type" not in part:
                    continue
                ptype: str = part["type"]
                data = part.get("data")

                if ptype == "messages":
                    msg_chunk, metadata = (
                        data if isinstance(data, (list, tuple)) and len(data) == 2
                        else (data, {})
                    )
                    node = (
                        metadata.get("langgraph_node", "")
                        if isinstance(metadata, dict) else ""
                    )

                    ak = getattr(msg_chunk, "additional_kwargs", None) or {}
                    reasoning = ak.get("reasoning_content")
                    if isinstance(reasoning, str) and reasoning:
                        yield _sse("reasoning", {"content": reasoning})

                    if not isinstance(msg_chunk, ToolMessage):
                        content = getattr(msg_chunk, "content", None)
                        if isinstance(content, list):
                            parts = []
                            for block in content:
                                if isinstance(block, str):
                                    parts.append(block)
                                elif isinstance(block, dict):
                                    t = block.get("text") or block.get("content")
                                    if isinstance(t, str):
                                        parts.append(t)
                            content = "".join(parts)
                        if isinstance(content, str) and content.strip():
                            if node == "supervisor":
                                yield _sse("plan", {"content": content})
                            else:
                                yield _sse("token", {"content": content})
                                full_answer_parts.append(content)

                    if isinstance(msg_chunk, AIMessage):
                        calls = getattr(msg_chunk, "tool_calls", None) or []
                        for tc in calls:
                            tc_id = tc.get("id") or tc.get("name", "")
                            pending_tool_calls[tc_id] = {
                                "id": tc_id,
                                "name": tc.get("name", ""),
                                "args": tc.get("args", {}),
                            }
                            yield _sse("tool_start", pending_tool_calls[tc_id])
                    if isinstance(msg_chunk, ToolMessage):
                        tc_id = getattr(msg_chunk, "tool_call_id", "")
                        name = getattr(msg_chunk, "name", "") or tc_id
                        summary = _summarise_tool_result(getattr(msg_chunk, "content", ""))
                        yield _sse("tool_result", {"id": tc_id, "name": name, "summary": summary})

                    usage_metadata = getattr(msg_chunk, "usage_metadata", None)
                    if isinstance(usage_metadata, dict):
                        ints = {
                            k: v for k, v in usage_metadata.items()
                            if isinstance(v, int)
                        }
                        if ints:
                            usage = ints

                elif ptype == "updates" and isinstance(data, dict):
                    if "supervisor" in data and "searching" not in emitted_phases:
                        sup_out = data["supervisor"]
                        sup_msgs = (
                            sup_out.get("messages", [])
                            if isinstance(sup_out, dict) else []
                        )
                        has_tools = (
                            bool(sup_msgs)
                            and isinstance(sup_msgs[-1], AIMessage)
                            and getattr(sup_msgs[-1], "tool_calls", None)
                        )
                        if has_tools:
                            yield _sse("status", {"msg": "searching", "rid": rid})
                            emitted_phases.add("searching")
                        elif "composing" not in emitted_phases:
                            yield _sse("status", {"msg": "composing", "rid": rid})
                            emitted_phases.add("composing")

                    elif "tools" in data and "composing" not in emitted_phases:
                        yield _sse("status", {"msg": "composing", "rid": rid})
                        emitted_phases.add("composing")

                    # The synthesis node's update carries its VERIFIED
                    # AIMessage (verification already ran inside the node).
                    # That message is the authoritative answer: the last
                    # synthesis round wins (DEGRADED_NODE_NAME is the
                    # degraded no-tools graph's synthesis node name — the
                    # constants are agent.py's, single source).
                    syn_out = data.get(SYNTHESIS_NODE_NAME) or data.get(DEGRADED_NODE_NAME)
                    if isinstance(syn_out, dict):
                        syn_msgs = syn_out.get("messages", [])
                        if syn_msgs:
                            content = getattr(syn_msgs[-1], "content", None)
                            if isinstance(content, str) and content.strip():
                                verified_answer = content

        finally:
            # The per-turn usage log observability.py promises: rid, duration,
            # and token usage on one line, emitted whenever the stream loop
            # ends — clean finish, mid-stream failure, or client cancellation
            # — with tokens 0/absent when usage_metadata was not available.
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

        # Post-stream events. The synthesis node's verified AIMessage is the
        # authoritative answer: derive the citations event from it (parsed,
        # never re-verified) and emit a correction ONLY when the raw
        # accumulated token text differs from it (plain string comparison).
        # When they match the client already holds the final text, so no
        # correction event is emitted at all.
        full_answer = "".join(full_answer_parts)
        if verified_answer is not None:
            try:
                cites = parse_citations(verified_answer)
                if cites:
                    yield _sse("citations", {
                        "citations": [{"act": c.act, "ref": c.ref} for c in cites],
                    })
            except Exception as exc:
                log.warning("citations event emission failed: %s", exc)

            if full_answer != verified_answer:
                yield _sse("correction", {"content": verified_answer})

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

    yield _sse("done", {})


async def _stream_with_keepalive(
    graph: Any,
    messages: list[Any],
    keepalive_interval: float,
) -> AsyncIterator[Any]:
    """Yield graph stream parts, interleaving ``True`` ping signals when idle.

    ``asyncio.timeout(keepalive_interval)`` wraps each ``__anext__`` of the
    chunk source: when a wait expires, a ping signal is yielded and the wait
    resumes — no chunk is ever dropped or reordered, and the timeout is
    armed/disarmed around every single chunk instead of racing two tasks per
    chunk (the old ``asyncio.wait`` pattern).

    The graph stream itself is drained by ONE background producer task per
    stream (not per chunk) into an ``asyncio.Queue``; the timeout then fires
    on the consumer's ``queue.get()`` wait only, so an expiring timeout can
    never cancel the underlying graph stream (cancelling an async
    generator's ``__anext__`` would terminate the whole graph iteration).
    On cancellation of the outer stream, the producer is cancelled and
    awaited in ``finally`` so no tasks linger.
    """
    if keepalive_interval is None or keepalive_interval <= 0:
        async for part in graph.astream(
            {"messages": messages},
            stream_mode=["messages", "updates"],
            version="v2",
        ):
            yield part
        return

    chunk_queue: asyncio.Queue[Any] = asyncio.Queue()
    _SENTINEL = object()

    async def _produce():
        try:
            async for part in graph.astream(
                {"messages": messages},
                stream_mode=["messages", "updates"],
                version="v2",
            ):
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
            yield part
    finally:
        if not producer_task.done():
            producer_task.cancel()
        try:
            await producer_task
        except asyncio.CancelledError:
            pass
