"""Shared normalisation of tool-result content (one pipeline, both paths).

Tool results arrive from the nyaya v2 data layer as JSON strings (with inner
primary-law text fields wrapped in parameterized ``<corpus_text ...>``
envelopes), as stringified Python literals of LangChain content-block lists,
or as the block lists themselves.

* ``clean_tool_content(content, strip_corpus=False)`` keeps ``<corpus_text>``
  tags intact for the synthesis LLM (OWASP LLM01 isolation).
* ``clean_for_ui_summary(content)`` / ``clean_tool_content(content, strip_corpus=True)``
  strips unanchored ``<corpus_text ...>`` and ``</corpus_text>`` tags so UI
  summaries never leak ``<corpus_text>`` markup.
"""

from __future__ import annotations

import ast
import json
import re
from collections.abc import Callable, Iterable
from typing import Any

from ..config import MAX_TOOL_CHARS
from .spec import prune_config

# Unanchored regexes so parameterized <corpus_text doc_id="..." ...> tags
# embedded inside JSON string fields are stripped cleanly for UI summaries.
_CORPUS_OPEN_RE = re.compile(r"<corpus_text\b[^>]*>\n?")
_CORPUS_CLOSE_RE = re.compile(r"\n?</corpus_text>")


def strip_corpus_tags(text: str) -> str:
    """Remove all <corpus_text ...>...</corpus_text> wrappers if present."""
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
    """Collapse a stringified content-block list to its joined text."""
    if not (stripped.startswith("[{") and "'type'" in stripped and "'text'" in stripped):
        return None
    try:
        parsed = ast.literal_eval(stripped)
    except Exception:
        return None
    if not isinstance(parsed, list):
        return None
    return " ".join(_block_parts(parsed))


def _cap(text: str) -> str:
    """Cap at MAX_TOOL_CHARS with an explicit ellipsis (not a silent chop)."""
    if len(text) <= MAX_TOOL_CHARS:
        return text
    return text[: MAX_TOOL_CHARS - 1] + "…"


def clean_tool_content(content: Any, *, strip_corpus: bool = False) -> str:
    """Normalise a ToolMessage's content to a clean string capped at MAX_TOOL_CHARS."""
    if isinstance(content, str):
        stripped = content.strip()
        joined = _stringified_blocks(stripped)
        if joined is not None:
            out = strip_corpus_tags(joined) if strip_corpus else joined
            return _cap(out)
        if strip_corpus:
            return _cap(strip_corpus_tags(stripped))
        return _cap(stripped)
    if isinstance(content, list):
        joined_list = " ".join(_block_parts(content))
        if strip_corpus:
            joined_list = strip_corpus_tags(joined_list)
        return _cap(joined_list)
    raw = str(content)
    return _cap(strip_corpus_tags(raw) if strip_corpus else raw)


def clean_for_ui_summary(content: Any, tool_name: str | None = None) -> str:
    """Clean tool output for human-visible UI summaries, stripping all <corpus_text> tags."""
    return clean_tool_content(content, strip_corpus=True)


# ---------------------------------------------------------------------------
# Human-readable summarizers for all 12 MCP v2 tools
# ---------------------------------------------------------------------------

TOOL_SUMMARIZERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "search_statutes": lambda d: f"Found {d.get('returned', len(d.get('results', [])))} of {d.get('total', 0)} statutory provisions",
    "get_provision": lambda d: f"{d.get('act', '')} {d.get('ref', '')}: {d.get('title') or 'provision'} ({(d.get('validity') or {}).get('status', 'in_force')})".strip(),
    "browse_statute": lambda d: f"Browse ({d.get('mode', 'catalog')}): {d.get('returned', 0)} of {d.get('total', 0)} entries",
    "compare_provisions": lambda d: f"Comparison ({d.get('mapping_type', 'one_to_one')}): {(d.get('source_provision') or {}).get('act', '')} {(d.get('source_provision') or {}).get('ref', '')}",
    "get_provision_history": lambda d: f"History for {d.get('act', 'Constitution')} {d.get('ref') or ''}: {len(d.get('amendments', []))} amendments, {len(d.get('judicial_validity_events', []))} judicial events".strip(),
    "cross_reference": lambda d: f"Cross-references for {(d.get('source') or {}).get('act', '')} {(d.get('source') or {}).get('ref', '')}: {d.get('returned', len(d.get('links', [])))} links",
    "search_judgments": lambda d: f"Found {d.get('returned', len(d.get('results', [])))} of {d.get('total', 0)} judgments",
    "get_judgment": lambda d: f"{(d.get('card') or {}).get('title', 'Judgment')} ({d.get('view', 'overview')})",
    "trace_precedent": lambda d: f"Precedent trace for {(d.get('target') or {}).get('title', '')}: {len(d.get('edges', []))} edges ({d.get('precedent_status', 'unverified_automated')})",
    "verify_citations": lambda d: f"Verified {len(d.get('findings', []))} citations",
    "judgment_analytics": lambda d: f"Analytics ({d.get('court_tier', 'all')} by {d.get('group_by', '')}): {d.get('total_matched', 0)} matched cases",
    "corpus_status": lambda d: f"Corpus status v{d.get('service_version', '2.0.0')} ({d.get('retrieval_mode', 'lexical_metadata_graph')})",
}


def summarize_tool_output(tool_name: str, content: Any) -> str:
    """Return a clean UI summary for a tool output with <corpus_text> stripped."""
    cleaned = clean_for_ui_summary(content, tool_name=tool_name)
    summarizer = TOOL_SUMMARIZERS.get(tool_name)
    if summarizer is not None and cleaned.startswith("{"):
        try:
            data = json.loads(cleaned)
            if isinstance(data, dict) and "error" not in data:
                return strip_corpus_tags(summarizer(data))
        except Exception:
            pass
    return cleaned


# ---------------------------------------------------------------------------
# Synthesis-input pruning for LIST-type tool results
# ---------------------------------------------------------------------------

_SNIPPET_CHARS = 300


def prune_list_result(content: Any, tool_name: str | None = None) -> Any:
    """Bound the bulk of a LIST-type v2 tool result before it reaches the model.

    * Only LIST-type specs (:data:`tools_layer.spec.TOOL_SPECS` entries with
      ``list_result=True``) with a parseable JSON-object payload are touched.
    * Supports polymorphic ``list_keys: tuple[str, ...]`` (e.g. ``browse_statute``
      returning ``provisions``, ``acts``, or ``chapters_or_parts``).
    * Hit 0 is kept verbatim; hits 1..N keep identification fields plus a
      300-char snippet (preserving balanced ``<corpus_text>`` tags if present).
    """
    config = prune_config().get(tool_name or "")
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

    list_keys: tuple[str, ...] = tuple(config.get("list_keys") or (config["list_key"],))
    active_key: str | None = None
    results: list[Any] | None = None
    for candidate_key in list_keys:
        val = data.get(candidate_key)
        if isinstance(val, list) and len(val) > 0:
            active_key = candidate_key
            results = val
            break

    if active_key is None or results is None or len(results) <= 1:
        return content

    keep_hit_fields: tuple[str, ...] = config["keep_hit_fields"]
    snippet_field: str = config["keep_snippet_field"]

    def _condense(hit: Any) -> Any:
        if not isinstance(hit, dict):
            return hit
        out = {k: hit[k] for k in keep_hit_fields if k in hit}
        snippet = hit.get(snippet_field)
        if isinstance(snippet, str):
            if "<corpus_text" in snippet and "</corpus_text>" in snippet:
                inner = strip_corpus_tags(snippet)
                if len(inner) > _SNIPPET_CHARS:
                    m_open = _CORPUS_OPEN_RE.search(snippet)
                    open_tag = m_open.group(0).rstrip("\n") if m_open else "<corpus_text>"
                    inner_trunc = inner[: _SNIPPET_CHARS - 1] + "…"
                    out[snippet_field] = f"{open_tag}\n{inner_trunc}\n</corpus_text>"
                else:
                    out[snippet_field] = snippet
            else:
                out[snippet_field] = (
                    snippet[: _SNIPPET_CHARS - 1] + "…"
                    if len(snippet) > _SNIPPET_CHARS
                    else snippet
                )
        return out

    first = results[0]
    rest = [_condense(hit) for hit in results[1:]]
    pruned: dict[str, Any] = {
        k: data[k] for k in config["keep_envelope_fields"] if k in data
    }
    pruned[active_key] = [first, *rest]
    return _cap(json.dumps(pruned, default=str))
