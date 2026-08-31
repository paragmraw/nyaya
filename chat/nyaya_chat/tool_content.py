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
import json
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


# ---------------------------------------------------------------------------
# Synthesis-input pruning for LIST-type tool results
# ---------------------------------------------------------------------------

# Tools whose results are LIST-shaped (a JSON object with a multi-hit array)
# and can arrive with bulk fields that the synthesiser does not need. Entries
# 1..N of a result array beyond the top hit are condensed to their
# identification fields plus a truncated snippet. Single-document tools
# (get_section / get_article / get_judgment — full text on purpose) are
# deliberately NOT listed here: when in doubt, don't prune.
_LIST_RESULT_TOOLS: dict[str, dict[str, Any]] = {
    # SearchResponse: {query, total, returned, offset, limit, source, as_of,
    # fallback_reason, results: [{act, ref, title, snippet, rank, citation,
    # kind}, ...]}
    "semantic_query": {
        "list_key": "results",
        "keep_hit_fields": ("act", "ref", "title", "kind", "rank", "citation"),
        "keep_snippet_field": "snippet",
        # Envelope metadata kept; everything else (query echo, source, as_of,
        # offset, limit, fallback_reason) is dropped as redundant.
        "keep_envelope_fields": ("total", "returned"),
    },
}
_SNIPPET_CHARS = 300  # matches the ~300-char snippet convention of the corpus


def prune_list_result(content: Any, tool_name: str | None = None) -> Any:
    """Bound the bulk of a LIST-type tool result before it reaches the model.

    **Pruning rule (conservative)** — this exists to cut the ~12K-token worst
    case a ``semantic_query`` (up to 50 hits) can contribute to one synthesis
    round:

    * Only the listed LIST-type tools (:data:`_LIST_RESULT_TOOLS`) with a
      parseable JSON-object payload are touched.
    * The **top hit (index 0) is kept verbatim** — full snippet, all fields —
      because it is the content the answer will most likely quote and the
      citation verifier will match against.
    * Every further hit is condensed to its identification fields (act, ref,
      title, kind, rank, citation) plus its snippet truncated to 300 chars
      (the corpus's own snippet convention): enough to find the provision via
      ``get_section``/``get_article`` for full text, without shipping every
      hit's text to the model.
    * Redundant envelope metadata (query echo, source, as_of, offset, limit,
      fallback_reason) is dropped; total/returned counts are kept.

    Single-document results (get_section / get_article / get_judgment) and
    anything not matching the expected JSON shape pass through UNCHANGED —
    full text is exactly what the answer quality (and citation verification)
    needs.
    """
    config = _LIST_RESULT_TOOLS.get(tool_name or "")
    if config is None or not isinstance(content, str):
        return content
    stripped = content.strip()
    if not stripped.startswith("{"):
        return content
    try:
        data = json.loads(stripped)
    except Exception:
        return content
    if not isinstance(data, dict):
        return content
    results = data.get(config["list_key"])
    if not isinstance(results, list) or len(results) <= 1:
        return content

    keep_hit_fields: tuple[str, ...] = config["keep_hit_fields"]
    snippet_field: str = config["keep_snippet_field"]

    def _condense(hit: Any) -> Any:
        if not isinstance(hit, dict):
            return hit
        out = {k: hit[k] for k in keep_hit_fields if k in hit}
        snippet = hit.get(snippet_field)
        if isinstance(snippet, str):
            out[snippet_field] = snippet[:_SNIPPET_CHARS]
        return out

    first = results[0]
    rest = [_condense(hit) for hit in results[1:]]
    pruned: dict[str, Any] = {
        k: data[k] for k in config["keep_envelope_fields"] if k in data
    }
    pruned[config["list_key"]] = [first, *rest]
    return json.dumps(pruned, default=str)[:MAX_TOOL_CHARS]
