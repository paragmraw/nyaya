"""Parse tool calls the model emitted as free text.

The supervisor normally returns tool calls via the tool-calling protocol
(``AIMessage.tool_calls``) or as a structured ``ToolPlan``. When a model
ignores both and embeds the calls in its content instead, this module
recovers them. Each recognised shape is a small matcher function; the
``_PATTERNS`` table lists the matchers in priority order and
:func:`parse_text_tool_calls` returns the first one that yields calls —
exactly one shape ever fires on a given text.

Recognised shapes, in priority order:

1. ``[[tool_calls]] [ {...}, ... ] [[/tool_calls]]``
2. a bare JSON object ``{"name": ..., "arguments": {...}}``
3. a bare JSON array of such objects
4. a YAML-ish ``tool_calls:`` block with inline ``name: ... argument: {...}``
5. XML-tag wrapped JSON: ``<tool_name>{...}</tool_name>``
6. bracket-pipe: ``[[<tool> tool call|tool_name: "...", tool_args: {...}]]``
7. attribute style: ``[[tool_name key="value" key2="value2"]]``
8. function-call style: ``[[tool_name(key="val", key2="val2")]]``
9. a bare function call: ``tool_name(key="val", key2="val2")``
10. a tool marker ``[[tool: tool_name]]`` with args from the following text
11. a bare allowlisted tool name with no arguments

The low-confidence fallback matchers (9-11) only accept names in
``DEFAULT_TOOLS`` (``config.py``); the earlier, more explicit shapes are
trusted as-is, matching the historical behaviour.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from typing import Any

from .config import DEFAULT_TOOLS

# Tool names accepted by the fallback matchers (patterns 9-11), where the
# shape alone is too weak to trust an arbitrary name.
_ALLOWED_TOOLS = frozenset(DEFAULT_TOOLS)

# A parsed tool call: {"id", "name", "args"} in the AIMessage.tool_calls shape.
ToolCall = dict[str, Any]


def _call(name: str, args: dict[str, Any], index: int) -> ToolCall:
    """Build one tool-call dict; ``index`` keeps ids unique within a match."""
    return {"id": f"tc_text_{index}", "name": name, "args": args}


def _name_and_args(call: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Read name/arguments (or args) from one parsed JSON call object."""
    name = call.get("name", "")
    args = call.get("arguments") or call.get("args") or {}
    return name, args


def _match_tool_calls_tag(content: str) -> list[ToolCall]:
    """Pattern 1: ``[[tool_calls]] ... JSON array ... [[/tool_calls]]``."""
    m = re.search(r"\[\[tool_?calls\]\](.*?)\[\[/tool_?calls\]\]", content, re.DOTALL | re.IGNORECASE)
    if not m:
        return []
    calls: list[ToolCall] = []
    try:
        parsed = json.loads(m.group(1).strip())
        for i, call in enumerate(parsed):
            name, args = _name_and_args(call)
            if name:
                calls.append(_call(name, args, i))
    except (json.JSONDecodeError, KeyError):
        return []
    return calls


def _match_bare_json_object(content: str) -> list[ToolCall]:
    """Pattern 2: a bare JSON object with "name" and "arguments"."""
    try:
        parsed = json.loads(content.strip())
    except (json.JSONDecodeError, TypeError):
        return []
    if isinstance(parsed, dict) and "name" in parsed:
        name, args = _name_and_args(parsed)
        if name:
            return [_call(name, args, 0)]
    return []


def _match_bare_json_array(content: str) -> list[ToolCall]:
    """Pattern 3: a bare JSON array of tool calls (no [[tool_calls]] wrapper)."""
    try:
        parsed = json.loads(content.strip())
    except (json.JSONDecodeError, TypeError):
        return []
    calls: list[ToolCall] = []
    if isinstance(parsed, list):
        for i, call in enumerate(parsed):
            if isinstance(call, dict) and "name" in call:
                name, args = _name_and_args(call)
                if name:
                    calls.append(_call(name, args, i))
    return calls


def _match_yaml_tool_calls(content: str) -> list[ToolCall]:
    """Pattern 4: a YAML-ish ``tool_calls:`` block (Nemotron style).

    Only the inline ``name: <tool> argument: {...}`` line form is
    recognised: the historical ``- name:`` line branch set a name variable
    but never parsed arguments, so it could never emit a call and was
    removed.
    """
    m = re.search(r"tool_calls:\s*\n(.*?)(?:\n\w+:|\Z)", content, re.DOTALL | re.IGNORECASE)
    if not m:
        return []
    calls: list[ToolCall] = []
    for raw_line in m.group(1).strip().split("\n"):
        line = raw_line.strip()
        if "name:" not in line or "arguments:" in line:
            continue
        match = re.search(r"name:\s*(\S+).*?arguments?:\s*(\{.*?\})", line)
        if not match:
            continue
        name = match.group(1).strip()
        args_str = match.group(2).strip()
        try:
            args = json.loads(args_str)
        except json.JSONDecodeError:
            try:
                args = json.loads(args_str.replace("'", '"'))
            except json.JSONDecodeError:
                continue
        if name:
            calls.append(_call(name, args, len(calls)))
    return calls


def _match_xml_tag_json(content: str) -> list[ToolCall]:
    """Pattern 5: XML-tag wrapped JSON ``<tool_name>{...json args...}</tool_name>``."""
    calls: list[ToolCall] = []
    for m in re.finditer(r"<(\w+)>(.*?)</\1>", content, re.DOTALL):
        name = m.group(1)
        try:
            args = json.loads(m.group(2).strip())
        except json.JSONDecodeError:
            continue
        if name and isinstance(args, dict):
            calls.append(_call(name, args, len(calls)))
    return calls


def _match_bracket_pipe(content: str) -> list[ToolCall]:
    """Pattern 6: ``[[<tool> tool call|tool_name: "...", tool_args: {...}]]``.

    The tool_args JSON may contain nested braces, so the end of the JSON
    object is found with a balanced-brace scan rather than a regex.
    """
    m = re.search(
        r"\[\[\w+\s+tool\s*call\|tool_name:\s*\"(\w+)\"\s*,\s*tool_args:\s*",
        content, re.IGNORECASE,
    )
    if not m:
        return []
    name = m.group(1)
    json_start = m.end()
    depth = 0
    json_end = json_start
    for i in range(json_start, len(content)):
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
            if depth == 0:
                json_end = i + 1
                break
    if depth != 0 or json_end <= json_start:
        return []
    try:
        args = json.loads(content[json_start:json_end])
    except json.JSONDecodeError:
        return []
    return [_call(name, args, 0)]


def _match_attribute_style(content: str) -> list[ToolCall]:
    """Pattern 7: attribute style ``[[tool_name key="value" key2="value2"]]``."""
    m = re.search(
        r'\[\[(\w+)\s+(\w+="[^"]*"(?:\s+\w+="[^"]*")*)\s*\]\]',
        content,
    )
    if not m:
        return []
    name = m.group(1)
    attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(2).strip()))
    if not attrs:
        return []
    return [_call(name, attrs, 0)]


def _match_function_call(content: str) -> list[ToolCall]:
    """Pattern 8: function-call style ``[[tool_name(key="val", key2="val2")]]``.

    Guarded like every other pattern: the table stops at the first matcher
    that yields calls, so a function-call match never stacks onto a
    pattern-7 (attribute-style) match on the same text — historically both
    could fire and double-emit.
    """
    m = re.search(r"\[\[(\w+)\((.*?)\)\]\]", content)
    if not m:
        return []
    name = m.group(1)
    args = dict(re.findall(r'(\w+)=["\']([^"\']*)["\']', m.group(2).strip()))
    if not args:
        return []
    return [_call(name, args, 0)]


def _match_bare_function_call(content: str) -> list[ToolCall]:
    """Pattern 9: a bare function call ``tool_name(key="val", ...)`` (allowlisted)."""
    m = re.search(
        r'(?<![\w\[])(\w+)\((\w+=["\'][^"\']*["\'](?:\s*,\s*\w+=["\'][^"\']*["\'])*)\)',
        content,
    )
    if not m:
        return []
    name = m.group(1)
    args = dict(re.findall(r'(\w+)=["\']([^"\']*)["\']', m.group(2).strip()))
    if args and name in _ALLOWED_TOOLS:
        return [_call(name, args, 0)]
    return []


def _match_tool_marker(content: str) -> list[ToolCall]:
    """Pattern 10: ``[[tool: tool_name]]`` with args from the following text."""
    m = re.search(r"\[\[tool:\s*(\w+)\]\]", content, re.IGNORECASE)
    if not m:
        return []
    name = m.group(1)
    if name not in _ALLOWED_TOOLS:
        return []
    after_marker = content[m.end():]
    args = dict(re.findall(r'(\w+)=["\']([^"\']*)["\']', after_marker[:200]))
    return [_call(name, args, 0)]


def _match_bare_tool_name(content: str) -> list[ToolCall]:
    """Pattern 11: the content is ONLY an allowlisted tool name, no args."""
    stripped = content.strip()
    if stripped in _ALLOWED_TOOLS:
        return [_call(stripped, {}, 0)]
    return []


# The pattern table in priority order. parse_text_tool_calls returns the
# first entry that yields at least one call; entries are independent and
# individually readable.
PatternMatcher = Callable[[str], list[ToolCall]]

_PATTERNS: tuple[PatternMatcher, ...] = (
    _match_tool_calls_tag,
    _match_bare_json_object,
    _match_bare_json_array,
    _match_yaml_tool_calls,
    _match_xml_tag_json,
    _match_bracket_pipe,
    _match_attribute_style,
    _match_function_call,
    _match_bare_function_call,
    _match_tool_marker,
    _match_bare_tool_name,
)


def parse_text_tool_calls(content: Any) -> list[ToolCall]:
    """Parse tool calls from free text; ``[]`` when none could be parsed.

    Returns a list of ``{"id", "name", "args"}`` dicts in the
    ``AIMessage.tool_calls`` shape. Non-string content parses to ``[]``.
    """
    if not isinstance(content, str):
        return []
    for matcher in _PATTERNS:
        calls = matcher(content)
        if calls:
            return calls
    return []
