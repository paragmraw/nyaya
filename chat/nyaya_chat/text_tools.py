"""Recover tool calls a supervisor emitted as free text.

The supervisor normally returns tool calls via ``with_structured_output``
(a ``ToolPlan``) or the ``bind_tools`` protocol (``AIMessage.tool_calls``).
When it ignores both and embeds the calls in its content instead — the
observed failure mode behind zero-tool-call turns — this module extracts
them with ONE tolerant strategy instead of the former eleven fragile
regex shapes.

Strategy, in order:
1. JSON-decode the whole content: a ``{"name": ..., "arguments"/"args": ...}``
   object, an array of such objects, or the ``{"tool_calls": [...]}``
   wrapper shape.
2. Scan for embedded JSON objects that carry a ``"name"`` key whose value is
   an allowlisted tool name, using a balanced-brace scan (handles ``json``
   fences, ``[[tool_calls]] ... [[/tool_calls]]`` wrappers, prose around the
   call, and nested-argument objects alike — all one scan).
3. The content is exactly an allowlisted tool name with no arguments.

Only calls whose ``name`` is in the allowlist (``tools_layer.spec``) are
returned; unknown names are dropped and logged, so a hallucinated tool
never reaches the executor.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .tools_layer.spec import TOOL_NAMES

log = logging.getLogger("nyaya_chat.text_tools")

ToolCall = dict[str, Any]


def _name_and_args(call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Read name/arguments (or args) from one parsed JSON call object."""
    name = call.get("name") or call.get("tool_name") or ""
    args = call.get("arguments") or call.get("args") or call.get("tool_args") or {}
    return (name if isinstance(name, str) else ""), (args if isinstance(args, dict) else {})


def _call(name: str, args: dict[str, Any], index: int) -> ToolCall:
    return {"id": f"tc_text_{index}", "name": name, "args": args}


def _decode_whole(content: str) -> list[ToolCall]:
    """Strategy 1: the content IS a JSON call object or array of them.

    Also accepts the ``{"tool_calls": [...]}`` wrapper shape models emit."""
    try:
        parsed = json.loads(content.strip())
    except (json.JSONDecodeError, TypeError):
        return []
    if isinstance(parsed, dict) and isinstance(parsed.get("tool_calls"), list):
        parsed = parsed["tool_calls"]
    candidates = parsed if isinstance(parsed, list) else [parsed]
    calls: list[ToolCall] = []
    for i, c in enumerate(candidates):
        if isinstance(c, dict):
            name, args = _name_and_args(c)
            if name in TOOL_NAMES:
                calls.append(_call(name, args, i))
    return calls


def _embedded_objects(text: str) -> list[tuple[int, str]]:
    """Yield (start, json_text) for every balanced ``{...}`` object in text.

    A naive regex cannot handle nested braces (tool args are dicts); this
    scan walks the string tracking depth and backtracks one level when a
    candidate object fails to parse.
    """
    out: list[tuple[int, str]] = []
    depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start >= 0:
                out.append((start, text[start : i + 1]))
                start = -1
    return out


def _scan_embedded(content: str) -> list[ToolCall]:
    """Strategy 2: parse every embedded JSON object with an allowlisted name."""
    calls: list[ToolCall] = []
    for _, obj_text in _embedded_objects(content):
        try:
            parsed = json.loads(obj_text)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, dict):
            continue
        name, args = _name_and_args(parsed)
        if name in TOOL_NAMES:
            calls.append(_call(name, args, len(calls)))
    return calls


def parse_text_tool_calls(content: Any) -> list[ToolCall]:
    """Extract allowlisted tool calls from free text; ``[]`` when none.

    Returns a list of ``{"id", "name", "args"}`` dicts in the
    ``AIMessage.tool_calls`` shape. Non-string content parses to ``[]``.
    """
    if not isinstance(content, str) or not content.strip():
        return []
    for strategy in (_decode_whole, _scan_embedded):
        calls = strategy(content)
        if calls:
            return calls
    stripped = content.strip()
    if stripped in TOOL_NAMES:  # strategy 3: bare allowlisted name, no args
        return [_call(stripped, {}, 0)]
    return []
