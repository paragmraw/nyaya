"""Encode LangGraph v2 stream events into Server-Sent Events.

LangGraph's ``astream(..., stream_mode=["messages", "updates"], version="v2")``
yields ``StreamPart`` dicts with ``type``/``ns``/``data``. We project them
into typed SSE events the frontend parses:

  event: status      data: {"msg": "analyzing"}      # stream opened (server.py)
  event: status      data: {"msg": "searching"}      # supervisor → tool calls
  event: status      data: {"msg": "composing"}      # tools done → synthesis
  event: plan        data: {"content": "..."}        # supervisor plan text
  event: token       data: {"content": "..."}        # synthesis LLM token deltas
  event: reasoning   data: {"content": "..."}        # reasoning_content deltas
  event: tool_start  data: {"name","args","id"}      # the model called a tool
  event: tool_result data: {"name","summary","id"}   # a tool finished
  event: error       data: {"message": "..."}        # a node threw
  event: done        data: {}                        # stream complete

All ``data`` payloads are single line JSON. The frontend ``useChat`` hook
dispatches on ``event:``.

We use **dual stream mode** (``["messages", "updates"]``):

* ``"messages"`` yields per-token chunks (with metadata identifying which
  LangGraph node produced them) and complete ``AIMessage``/``ToolMessage``
  objects. We route supervisor-node content to ``plan`` events and
  synthesis-node content to ``token`` events so the supervisor's plan text
  doesn't mix with the final answer.

* ``"updates"`` yields a dict keyed by node name when each node completes.
  We use this to emit phase-transition ``status`` events so the user sees
  real-time progress (``analyzing`` → ``searching`` → ``composing``).

The ``reasoning`` event carries Nemotron's ``reasoning_content`` (the model's
chain-of-thought), which appears in ``AIMessageChunk.additional_kwargs`` when
a reasoning-capable model is used. The current default model (Lightning-30b)
does not emit reasoning content, so these events will not fire — the code
remains for forward compatibility with reasoning-capable models.
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
    """Collapse a ToolMessage content blob to a string for the UI panel.

    The cap is generous (8 KB) so full tool results — typically a JSON object
    describing a provision — survive intact and the frontend can format them
    as readable key-value fields rather than a truncated blob.

    Handles three content shapes:
    - ``str``: returned as-is, unless it looks like a stringified Python list
      of content blocks (``[{'type': 'text', 'text': '...'}]``), in which case
      the text is extracted.
    - ``list``: LangChain tool content as a list of blocks; text is extracted.
    - anything else: ``str()``-ified.
    """
    if isinstance(content, str):
        # Check if it looks like a stringified list of content blocks
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
                pass  # not a valid Python literal — return as-is
        return content[:8000]
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
        return (" ".join(parts))[:8000]
    return str(content)[:8000]


async def stream_turn(
    graph: Any,
    messages: list[Any],
) -> AsyncIterator[bytes]:
    """Yield SSE-encoded bytes for a single agent turn.

    Iterates the graph's v2 async stream (dual mode: ``messages`` +
    ``updates``), emitting the events documented at module level. Catches
    per-node exceptions and emits an ``error`` event followed by ``done`` so
    the client always sees a clean stream end.
    """
    try:
        # Track tool_call ids so we can pair tool_start (from AIMessage) with
        # tool_result (from ToolMessage) for the UI.
        pending_tool_calls: dict[str, dict[str, Any]] = {}
        # Track which phase statuses we've already emitted to avoid dupes.
        emitted_phases: set[str] = set()

        async for part in graph.astream(
            {"messages": messages},
            stream_mode=["messages", "updates"],
            version="v2",
        ):
            if not isinstance(part, dict) or "type" not in part:
                continue
            ptype: str = part["type"]
            data = part.get("data")

            # ── "messages" stream: token-level chunks ──
            if ptype == "messages":
                msg_chunk, metadata = (
                    data if isinstance(data, (list, tuple)) and len(data) == 2
                    else (data, {})
                )
                # Identify which LangGraph node produced this chunk.
                node = (
                    metadata.get("langgraph_node", "")
                    if isinstance(metadata, dict) else ""
                )

                # Stream reasoning_content deltas as reasoning events
                # (Nemotron thinking mode: reasoning appears in
                # additional_kwargs).
                ak = getattr(msg_chunk, "additional_kwargs", None) or {}
                reasoning = ak.get("reasoning_content")
                if isinstance(reasoning, str) and reasoning:
                    yield _sse("reasoning", {"content": reasoning})

                # Route content based on which node produced it:
                #   supervisor → plan events (so it doesn't mix with answer)
                #   synthesis/agent → token events (the actual answer)
                # Whitespace-only chunks (e.g. trailing newlines from the
                # supervisor) are skipped so they don't produce empty plan
                # events that render as an empty collapsible in the UI.
                #
                # Some models return content as a list of content blocks
                # (e.g. [{'type': 'text', 'text': '...'}]) instead of a plain
                # string. We extract the text from each block so the raw repr
                # of the list-of-dicts never leaks into the SSE stream.
                #
                # CRITICAL: Only route content for AIMessage chunks.
                # ToolMessage content is tool output (JSON results, etc.)
                # and must NEVER be emitted as token events — it would leak
                # raw tool-result data into the answer text. ToolMessage
                # content is handled separately below as tool_result events.
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

                # Detect a completed AIMessage with tool_calls → emit starts.
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
                # ToolMessage → pair with its tool_call_id and emit result.
                if isinstance(msg_chunk, ToolMessage):
                    tc_id = getattr(msg_chunk, "tool_call_id", "")
                    name = getattr(msg_chunk, "name", "") or tc_id
                    summary = _summarise_tool_result(getattr(msg_chunk, "content", ""))
                    yield _sse("tool_result", {"id": tc_id, "name": name, "summary": summary})

            # ── "updates" stream: node completion → phase status ──
            elif ptype == "updates" and isinstance(data, dict):
                # Supervisor just finished: did it emit tool calls?
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

                # Tools node just finished → synthesis is about to start.
                elif "tools" in data and "composing" not in emitted_phases:
                    yield _sse("status", {"msg": "composing"})
                    emitted_phases.add("composing")

    except Exception as exc:  # noqa: BLE001 — we want to surface any failure.
        log.error("agent stream failed: %s", exc, exc_info=True)
        yield _sse("error", {"message": "agent_error", "detail": str(exc), "trace": traceback.format_exc(limit=3)})

    yield _sse("done", {})
