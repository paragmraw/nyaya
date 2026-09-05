"""Parallel tool-execution node with per-request deduplication.

A ``ToolNode`` wrapper that skips duplicate (name+args) calls within one
request (dedup memory lives in ``ChatState`` — the compiled graph is shared
across requests, so instance-level state would leak tool calls between
users) and emits a ``tool_result`` SSE event per completion through the
custom stream. Results are cleaned (``tools_layer.cleaning``) before they
enter state: the synthesis prompt and the citation verifier see the same
cleaned text.
"""

from __future__ import annotations

import logging
import time
from typing import Any, cast

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.prebuilt import ToolNode

from ..tools_layer.cleaning import clean_tool_content
from .events import timed_phase, tool_result
from .state import ChatState

log = logging.getLogger("nyaya_chat.graph.tools_node")


def _tool_call_key(name: str, args: dict) -> str:
    """A stable hashable key identifying a (tool_name, args) pair."""
    normalised = {k: str(v) for k, v in sorted(args.items())}
    return f"{name}:{normalised}"


class DedupToolNode:
    """A ToolNode wrapper that skips duplicate (name+args) tool calls.

    The node itself is STATELESS: dedup memory lives in ``ChatState``
    (``dedup_seen`` maps a tool-call key to the id of the call that first
    issued it; ``dedup_results`` maps a key to its cleaned result) so it is
    scoped to a single request.
    """

    def __init__(self, tools: list[Any]):
        self._tool_node = ToolNode(tools)

    async def __call__(self, state: ChatState) -> dict[str, Any]:
        messages = state.get("messages", [])
        last_ai: AIMessage | None = None
        for m in reversed(messages):
            if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
                last_ai = m
                break
        if last_ai is None:
            return await self._execute(state, [])

        # Per-request dedup state; LangGraph merges the returned dicts back
        # into state, so rounds within one request share this memory.
        seen: dict[str, str] = dict(state.get("dedup_seen", {}))
        results: dict[str, str] = dict(state.get("dedup_results", {}))

        unique_calls: list[Any] = []
        duplicate_calls: list[Any] = []
        for tc in last_ai.tool_calls:
            key = _tool_call_key(tc["name"], tc.get("args", {}))
            if key in seen:
                duplicate_calls.append(tc)
            else:
                unique_calls.append(tc)
                seen[key] = tc.get("id") or ""

        if not duplicate_calls:
            result = await self._execute(state, unique_calls)
            return self._finish(result, last_ai, seen, results)

        new_msgs: list[ToolMessage] = []
        if unique_calls:
            modified_ai = AIMessage(content=last_ai.content, tool_calls=unique_calls)
            modified_messages = messages[:-1] + [modified_ai]
            modified_state = cast(ChatState, {**state, "messages": modified_messages})
            result = await self._execute(modified_state, unique_calls)
            for m in result.get("messages", []):
                if isinstance(m, ToolMessage):
                    cleaned = clean_tool_content(m.content)
                    m = ToolMessage(
                        content=cleaned,
                        tool_call_id=m.tool_call_id,
                        name=m.name,
                    )
                    for tc in unique_calls:
                        if tc.get("id") == m.tool_call_id:
                            key = _tool_call_key(tc["name"], tc.get("args", {}))
                            results[key] = cleaned
                            break
                    new_msgs.append(m)
                    tool_result(m.tool_call_id, m.name or "", cleaned)

        dup_msgs: list[ToolMessage] = []
        for tc in duplicate_calls:
            key = _tool_call_key(tc["name"], tc.get("args", {}))
            cached = results.get(key, "")
            dup_msgs.append(ToolMessage(
                content=cached or "(duplicate call skipped)",
                tool_call_id=tc.get("id", ""),
                name=tc["name"],
            ))
            log.info("dedup: skipped duplicate tool call %s args=%s", tc["name"], tc.get("args", {}))

        return {
            "messages": new_msgs + dup_msgs,
            "dedup_seen": seen,
            "dedup_results": results,
        }

    async def _execute(self, state: ChatState, calls: list[Any]) -> dict[str, Any]:
        """Run the underlying ToolNode (concurrently for parallel calls)."""
        t0 = time.monotonic()
        result = await self._tool_node.ainvoke(state)
        elapsed = timed_phase(state, "tools_ms", t0)
        log.info("tools node executed %d call(s) in %.0fms", len(calls), elapsed)
        return result

    def _finish(
        self,
        result: dict[str, Any],
        last_ai: AIMessage,
        seen: dict[str, str],
        results: dict[str, str],
    ) -> dict[str, Any]:
        """Clean the ToolMessages, emit tool_result events, return the update."""
        cleaned_msgs: list[ToolMessage] = []
        for m in result.get("messages", []):
            if isinstance(m, ToolMessage):
                cleaned = clean_tool_content(m.content)
                m = ToolMessage(
                    content=cleaned,
                    tool_call_id=m.tool_call_id,
                    name=m.name,
                )
                for tc in last_ai.tool_calls:
                    if tc.get("id") == m.tool_call_id:
                        key = _tool_call_key(tc["name"], tc.get("args", {}))
                        results[key] = cleaned
                        break
                cleaned_msgs.append(m)
                tool_result(m.tool_call_id, m.name or "", cleaned)
        return {
            "messages": cleaned_msgs,
            "dedup_seen": seen,
            "dedup_results": results,
        }
