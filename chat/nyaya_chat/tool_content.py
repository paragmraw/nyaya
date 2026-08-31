"""Shared normalisation of tool-result content.

Tool results arrive from the nyaya MCP server as plain strings, as
stringified Python literals of LangChain content-block lists
(``"[{'type': 'text', 'text': ...}]"``), or as the block lists themselves.
Both the agent's dedup node (which caches cleaned results in per-request
state before they are wrapped for the synthesis prompt) and the SSE
streamer (which renders a UI summary from whatever the graph emits) need
the same collapse-to-string behaviour and the same length cap, so it lives
here once — including the ``<corpus_text>`` wrapper strip, whose single
implementation is :func:`strip_corpus_tags`.
"""

from __future__ import annotations

import ast
import re
from collections.abc import Iterable
from typing import Any

from .config import MAX_TOOL_CHARS

# The <corpus_text>...</corpus_text> wrapper the agent adds around tool
# results handed to the synthesis prompt (prompt-injection defence). The
# agent strips it defensively when reading results back; the streamer
# strips it so it never reaches the UI summary.
_CORPUS_OPEN_RE = re.compile(r"^<corpus_text>\n?")
_CORPUS_CLOSE_RE = re.compile(r"\n?</corpus_text>$")


def strip_corpus_tags(text: str) -> str:
    """Remove a surrounding <corpus_text>...</corpus_text> wrapper if present."""
    text = _CORPUS_OPEN_RE.sub("", text)
    return _CORPUS_CLOSE_RE.sub("", text)


def _block_parts(blocks: Iterable[Any]) -> list[str]:
    """Collect the text-bearing pieces of a LangChain content-block list."""
    parts: list[str] = []
    for block in blocks:
        if isinstance(block, dict):
            t = block.get("text") or block.get("content")
            if t:
                parts.append(str(t))
        elif isinstance(block, str):
            parts.append(block)
    return parts


def _stringified_blocks(stripped: str) -> str | None:
    """Collapse a stringified content-block list to its joined text.

    Some MCP transports stringify the content-block list into the
    ToolMessage string. It is a Python literal (single quotes), not JSON,
    so ``ast.literal_eval`` recovers it. Returns the joined text — possibly
    empty — or ``None`` when the string is not such a literal, in which
    case the caller uses the string as-is.
    """
    if not (stripped.startswith("[{") and "'type'" in stripped and "'text'" in stripped):
        return None
    try:
        parsed = ast.literal_eval(stripped)
    except Exception:
        return None
    if not isinstance(parsed, list):
        return None
    return " ".join(_block_parts(parsed))


def clean_tool_content(content: Any, *, strip_corpus: bool = False) -> str:
    """Normalise a ToolMessage's content to a clean string capped at MAX_TOOL_CHARS.

    Behaviour shared by every caller: stringified content-block lists are
    collapsed to their text, block lists are joined, anything else is
    stringified, and the result is capped.

    The two call sites differ in exactly one way, expressed by
    ``strip_corpus``:

    * The agent's dedup node (``agent.DedupToolNode``) cleans results
      BEFORE they are wrapped for the synthesis prompt, so no wrapper can
      be present; it passes the default and keeps the raw text.
    * The SSE streamer's UI summary (``streaming._summarise_tool_result``)
      may see results already wrapped in an earlier round, so it asks for
      the wrapper to be stripped before capping.
    """
    if isinstance(content, str):
        stripped = content.strip()
        joined = _stringified_blocks(stripped)
        if joined is not None:
            return joined[:MAX_TOOL_CHARS]
        if strip_corpus:
            return strip_corpus_tags(stripped)[:MAX_TOOL_CHARS]
        return content[:MAX_TOOL_CHARS]
    if isinstance(content, list):
        return " ".join(_block_parts(content))[:MAX_TOOL_CHARS]
    return str(content)[:MAX_TOOL_CHARS]
