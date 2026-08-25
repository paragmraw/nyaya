"""Encode LangGraph v2 stream events into Server-Sent Events.

LangGraph's ``astream(..., stream_mode=["messages", "updates"], version="v2")``
yields ``StreamPart`` dicts with ``type``/``ns``/``data``. We project them
into typed SSE events the frontend parses:

  event: meta        data: {"request_id": "..."}   — request id (server.py)
  event: status      data: {"msg": "analyzing"}    — stream opened (server.py)
  event: status      data: {"msg": "searching"}    — supervisor → tool calls
  event: status      data: {"msg": "composing"}    — tools done → synthesis
  event: plan        data: {"content": "..."}       — supervisor plan text
  event: token       data: {"content": "..."}       — synthesis LLM token deltas
  event: reasoning   data: {"content": "..."}       — reasoning_content deltas
  event: tool_start  data: {"name","args","id"}     — the model called a tool
  event: tool_result data: {"name","summary","id"}  — a tool finished
  event: citations data: {"citations": [...]}       — verified citations (post-synthesis)
  event: correction data: {"content": "..."}        — corrected answer (post-stream fixes)
  event: ping        data: {"ts": 1234567890}       — keepalive (every ~15s)
  event: error       data: {"message": "..."}       — a node threw
  event: done        data: {}                        — stream complete

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
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

log = logging.getLogger("nyaya_chat.streaming")


def _sse(event: str, payload: Any) -> bytes:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def _summarise_tool_result(content: Any) -> str:
    """Collapse a ToolMessage content blob to a string for the UI panel."""
    if isinstance(content, str):
        stripped = content.strip()
        if stripped.startswith("[{") and "'type'" in stripped and "'text'" in stripped:
            try:
                import ast
                parsed = ast.literal_eval(stripped)
                if isinstance(parsed, list):
                    parts = []
                    for block in parsed:
                        if isinstance(block, dict):
                            t = block.get("text") or block.get("content")
                            if t:
                                parts.append(str(t))
                        elif isinstance(block, str):
                            parts.append(block)
                    return (" ".join(parts))[:8000]
            except Exception:
                pass
        # Strip <corpus_text> wrapper if present (added by agent for synthesis)
        import re
        stripped = re.sub(r"^<corpus_text>\n?", "", stripped)
        stripped = re.sub(r"\n?</corpus_text>$", "", stripped)
        return content[:8000]
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict):
                t = block.get("text") or block.get("content")
                if t:
                    parts.append(str(t))
            elif isinstance(block, str):
                parts.append(block)
        return (" ".join(parts))[:8000]
    return str(content)[:8000]


async def stream_turn(
    graph: Any,
    messages: list[Any],
    *,
    keepalive_interval: float = 0,
) -> AsyncIterator[bytes]:
    """Yield SSE-encoded bytes for a single agent turn.

    Iterates the graph's v2 async stream (dual mode: ``messages`` +
    ``updates``), emitting the events documented at module level. Catches
    per-node exceptions and emits an ``error`` event followed by ``done`` so
    the client always sees a clean stream end.

    If ``keepalive_interval`` > 0, emits a ``ping`` event every N seconds
    to prevent proxy timeouts. The ping is emitted between graph chunks
    using asyncio timeout on the stream iteration.
    """
    try:
        pending_tool_calls: dict[str, dict[str, Any]] = {}
        emitted_phases: set[str] = set()
        # Track answer text and tool results for the citations event
        full_answer_parts: list[str] = []
        tool_result_contents: list[str] = []
        had_any_tool_calls = False

        # Set up keepalive ping task if interval is configured
        ping_queue: asyncio.Queue[bool] | None = None
        ping_task: asyncio.Task[None] | None = None

        if keepalive_interval > 0:
            ping_queue = asyncio.Queue()

            async def _ping_loop():
                while True:
                    await asyncio.sleep(keepalive_interval)
                    if ping_queue is not None:
                        await ping_queue.put(True)

            ping_task = asyncio.create_task(_ping_loop())

        try:
            async for part in _stream_with_keepalive(
                graph, messages, ping_queue,
            ):
                if isinstance(part, bool) and part:
                    # Ping signal from keepalive task
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
                            had_any_tool_calls = True
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
                        # Track tool content for citation verification
                        raw_content = getattr(msg_chunk, "content", "")
                        if isinstance(raw_content, str):
                            # Strip corpus_text wrapper if present
                            import re as _re
                            stripped_content = _re.sub(r"^<corpus_text>\n?", "", raw_content)
                            stripped_content = _re.sub(r"\n?</corpus_text>$", "", stripped_content)
                            tool_result_contents.append(stripped_content)
                        yield _sse("tool_result", {"id": tc_id, "name": name, "summary": summary})

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
                            yield _sse("status", {"msg": "searching"})
                            emitted_phases.add("searching")
                        elif "composing" not in emitted_phases:
                            yield _sse("status", {"msg": "composing"})
                            emitted_phases.add("composing")

                    elif "tools" in data and "composing" not in emitted_phases:
                        yield _sse("status", {"msg": "composing"})
                        emitted_phases.add("composing")

        finally:
            if ping_task is not None:
                ping_task.cancel()
                try:
                    await ping_task
                except asyncio.CancelledError:
                    pass

        # After the stream completes, emit a structured citations event with
        # the verified citation list parsed from the answer text. This gives
        # the frontend authoritative citation data alongside the inline markers.
        full_answer = "".join(full_answer_parts)
        if full_answer and had_any_tool_calls:
            try:
                from .citations import parse_citations
                cites = parse_citations(full_answer)
                if cites:
                    citations_data = [
                        {"act": c.act, "ref": c.ref} for c in cites
                    ]
                    yield _sse("citations", {"citations": citations_data})
            except Exception as exc:
                log.warning("citations event emission failed: %s", exc)

            # Post-stream correction: re-verify citations on the streamed
            # text (the agent's verify_citations runs on the final AIMessage
            # but the streamed tokens may differ) and check for missing
            # disclaimer. Emit a correction event if anything changed.
            corrected = full_answer
            try:
                from .citations import verify_citations
                verified = verify_citations(
                    corrected, tool_result_contents, had_tool_calls=True,
                )
                if verified != corrected:
                    corrected = verified
            except Exception as exc:
                log.warning("post-stream citation re-verification failed: %s", exc)

            # Check for missing disclaimer
            if "not legal advice" not in corrected.lower():
                corrected = corrected.rstrip() + (
                    "\n\n*This is not legal advice; verify citations before filing.*"
                )

            # Emit correction if anything changed
            if corrected != full_answer:
                yield _sse("correction", {"content": corrected})

    except asyncio.CancelledError:
        log.info("stream cancelled by client")
        raise
    except Exception as exc:
        log.error("agent stream failed: %s", exc, exc_info=True)
        yield _sse("error", {"message": "agent_error", "detail": "internal server error"})

    yield _sse("done", {})


async def _stream_with_keepalive(
    graph: Any,
    messages: list[Any],
    ping_queue: asyncio.Queue[bool] | None,
) -> AsyncIterator[Any]:
    """Yield graph stream parts, interleaving ping signals from the queue.

    Uses a producer-consumer pattern: the graph stream runs as a background
    task that puts chunks into a queue. The consumer races between the chunk
    queue and the ping queue, so chunks are never lost.
    """
    if ping_queue is None:
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
            chunk_task = asyncio.ensure_future(chunk_queue.get())
            ping_task = asyncio.ensure_future(ping_queue.get())

            done, pending = await asyncio.wait(
                {chunk_task, ping_task},
                return_when=asyncio.FIRST_COMPLETED,
            )

            for t in pending:
                t.cancel()
                try:
                    await t
                except asyncio.CancelledError:
                    pass

            if ping_task in done and not ping_task.cancelled():
                yield True  # ping signal

            if chunk_task in done and not chunk_task.cancelled():
                item = chunk_task.result()
                if item is _SENTINEL:
                    break
                if isinstance(item, Exception):
                    raise item
                yield item
    finally:
        if not producer_task.done():
            producer_task.cancel()
            try:
                await producer_task
            except asyncio.CancelledError:
                pass
