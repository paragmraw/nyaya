"""Encode LangGraph v2 stream events into Server-Sent Events.

LangGraph's ``astream(..., stream_mode=["messages"], version="v2")`` yields
``StreamPart`` dicts with ``type``/``ns``/``data``. We project them into typed
SSE events the frontend parses:

  event: token       data: {"content": "..."}        # LLM token deltas
  event: reasoning   data: {"content": "..."}        # reasoning_content deltas
  event: tool_start  data: {"name","args","id"}      # the model called a tool
  event: tool_result data: {"name","summary","id"}   # a tool finished
  event: status      data: {"msg": "..."}            # progress
  event: error       data: {"message": "..."}        # a node threw
  event: done        data: {}                        # stream complete

All ``data`` payloads are single line JSON. The frontend ``useChat`` hook
dispatches on ``event:``.

The ``reasoning`` event carries Nemotron's ``reasoning_content`` (the model's
chain-of-thought), which appears in ``AIMessageChunk.additional_kwargs`` when
thinking mode is enabled via ``with_thinking_mode(True)``.
"""

from __future__ import annotations

import json
import logging
import traceback
from collections.abc import AsyncIterator
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

log = logging.getLogger("nyaya_chat.streaming")


def _sse(event: str, payload: Any) -> bytes:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def _summarise_tool_result(content: Any) -> str:
    """Collapse a ToolMessage content blob to a short string for the UI chip."""
    if isinstance(content, str):
        return content[:400]
    if isinstance(content, list):
        # LangChain tool content can be a list of blocks; pull out text.
        parts = []
        for block in content:
            if isinstance(block, dict):
                t = block.get("text") or block.get("content")
                if t:
                    parts.append(str(t))
            elif isinstance(block, str):
                parts.append(block)
        return (" ".join(parts))[:400]
    return str(content)[:400]


async def stream_turn(
    graph: Any,
    messages: list[Any],
) -> AsyncIterator[bytes]:
    """Yield SSE-encoded bytes for a single agent turn.

    Iterates the graph's v2 async stream, emitting the events above. Catches
    per-node exceptions and emits an ``error`` event followed by ``done`` so
    the client always sees a clean stream end.
    """
    try:
        # Track tool_call ids so we can pair tool_start (from AIMessage) with
        # tool_result (from ToolMessage) for the UI.
        pending_tool_calls: dict[str, dict[str, Any]] = {}

        async for part in graph.astream(
            {"messages": messages},
            stream_mode=["messages"],
            version="v2",
        ):
            if not isinstance(part, dict) or "type" not in part:
                continue
            ptype: str = part["type"]
            data = part.get("data")

            if ptype != "messages":
                continue
            msg_chunk, _metadata = data if isinstance(data, (list, tuple)) and len(data) == 2 else (data, {})
            # Stream reasoning_content deltas as reasoning events (Nemotron
            # thinking mode: reasoning appears in additional_kwargs).
            ak = getattr(msg_chunk, "additional_kwargs", None) or {}
            reasoning = ak.get("reasoning_content")
            if isinstance(reasoning, str) and reasoning:
                yield _sse("reasoning", {"content": reasoning})
            # Stream content deltas as tokens.
            content = getattr(msg_chunk, "content", None)
            if isinstance(content, str) and content:
                yield _sse("token", {"content": content})
            # Detect a completed AIMessage with tool_calls -> emit starts.
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
            # ToolMessage -> pair with its tool_call_id and emit result.
            if isinstance(msg_chunk, ToolMessage):
                tc_id = getattr(msg_chunk, "tool_call_id", "")
                name = getattr(msg_chunk, "name", "") or tc_id
                summary = _summarise_tool_result(getattr(msg_chunk, "content", ""))
                yield _sse("tool_result", {"id": tc_id, "name": name, "summary": summary})

    except Exception as exc:  # noqa: BLE001 — we want to surface any failure.
        log.error("agent stream failed: %s", exc, exc_info=True)
        yield _sse("error", {"message": "agent_error", "detail": str(exc), "trace": traceback.format_exc(limit=3)})

    yield _sse("done", {})
