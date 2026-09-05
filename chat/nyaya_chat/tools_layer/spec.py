"""Single source of truth for the chat agent's tool allowlist.

Every consumer derives from this module:

* ``native.py`` builds the LangChain ``StructuredTool`` objects (name,
  description, args schema) straight from :data:`TOOL_SPECS`.
* ``mcp_fallback.py`` filters the MCP-over-HTTP tools by
  :data:`TOOL_SPECS` names, so both paths expose the SAME interface.
* ``prompts.py`` renders the supervisor's tool list from the specs, killing
  the hand-maintained duplicate that used to live in the prompt text.

The descriptions must stay consistent with what the MCP server advertises
(``mcp/nyaya/tools/*.py``); the MCP layer remains the interface for external
MCP clients, this spec is what the CHAT agent sees. When a description here
and one on the MCP server drift, this file wins for chat.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Args schemas (pydantic models, one per tool)
# ---------------------------------------------------------------------------

class SemanticQueryInput(BaseModel):
    query: str = Field(description="Free-text or natural-language query.")
    kind: str | None = Field(default=None, description="Filter: 'section', 'article', 'judgment', 'schedule', or 'amendment'.")
    act: str | None = Field(default=None, description="Act short-name to scope (e.g. 'IPC', 'BNS').")
    limit: int = Field(default=10, description="Max hits (1-50).")
    offset: int = Field(default=0, description="Pagination offset.")
    promote_definitions: bool = Field(default=False, description="Boost results whose title contains 'definition' or 'interpretation'.")


class GetSectionInput(BaseModel):
    act: str = Field(default="", description="Act short name or alias, e.g. 'IPC', 'BNS'. If empty, section is parsed as a citation string.")
    section: str = Field(default="", description="Section number, e.g. '302', '354A'. A leading 's.' prefix is stripped.")


class GetArticleInput(BaseModel):
    article: str = Field(description="Article number (e.g. '21', '21A') or citation string like 'Art.21'.")


class GetJudgmentInput(BaseModel):
    case_slug: str = Field(description="Citation (e.g. 'AIR 1973 SC 1461'), case name, or slugified name.")


class CrossReferenceInput(BaseModel):
    act: str = Field(description="Act short name or alias, e.g. 'IPC', 'Constitution'.")
    section: str = Field(description="Section or article number, e.g. '302', '21'.")
    direction: str = Field(default="both", description="'both' (default), 'from' (outgoing only), or 'to' (incoming only).")


class ListActsInput(BaseModel):
    pass


# ---------------------------------------------------------------------------
# Tool specs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ToolSpec:
    """One corpus tool as the chat agent sees it."""

    name: str
    description: str
    args_model: type[BaseModel]
    # Tools whose results are LIST-shaped and get synthesis-input pruning
    # (tools_layer/cleaning.py). Single-doc tools stay off this list: their
    # full text is what citation verification needs.
    list_result: bool = False


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(
        name="semantic_query",
        description=(
            "Semantic search over the Indian law corpus using embedding retrieval + "
            "cross-encoder reranking. Returns the most relevant sections, articles, "
            "and judgments for a natural-language query. Better than keyword search "
            "for paraphrased queries and cross-act comparisons (e.g. 'punishment "
            "for murder' finds both IPC s.302 and BNS s.103). "
            "Optional 'kind' filters to 'section', 'article', or 'judgment'. "
            "Optional 'act' scopes to one act short-name (e.g. 'IPC', 'BNS'). "
            "Set promote_definitions=true to boost sections whose title contains "
            "'definition' or 'interpretation'."
        ),
        args_model=SemanticQueryInput,
        list_result=True,
    ),
    ToolSpec(
        name="get_section",
        description=(
            "Fetch the full text of a specific section of an Indian act by its number. "
            "Supports IPC, CrPC, CPC, Evidence Act, BNS, BNSS, BSA, etc. Act names are "
            "normalized (case-insensitive). Also accepts combined citation strings like "
            "'IPC s.302' or 's.302 of IPC'. Use get_article for Constitution articles. "
            "List tools (list_sections, list_articles, list_judgments) return only "
            "~300-char text snippets; call get_section for full text."
        ),
        args_model=GetSectionInput,
    ),
    ToolSpec(
        name="get_article",
        description=(
            "Fetch the full text of a Constitution of India article by its number. "
            "Handles bare numbers ('21') and citation strings like 'Art.21' or 'Article 21'. "
            "List tools (list_articles) return only ~300-char snippets; use this for full text."
        ),
        args_model=GetArticleInput,
    ),
    ToolSpec(
        name="get_judgment",
        description=(
            "Fetch the full text of a landmark Supreme Court judgment by citation or "
            "case-name slug. Matches exact citation ('AIR 1973 SC 1461'), slugified "
            "case name, or fuzzy case-name substring (>= 8 chars). "
            "list_judgments returns only ~300-char snippets per case; call this for full text."
        ),
        args_model=GetJudgmentInput,
    ),
    ToolSpec(
        name="cross_reference",
        description=(
            "Given a section or article, return other provisions it references AND that "
            "reference it (bidirectional). Covers IPC-BNS correspondence, cross-act "
            "references, and repealed-by relationships."
        ),
        args_model=CrossReferenceInput,
    ),
    ToolSpec(
        name="list_acts",
        description=(
            "List all acts available in the nyaya corpus with provenance. Use this first "
            "to discover what's searchable. Note: list-style tools (list_sections, "
            "list_articles, list_judgments) return short text snippets (~300 chars); "
            "call get_section / get_article / get_judgment to read full text."
        ),
        args_model=ListActsInput,
    ),
)

TOOL_NAMES: frozenset[str] = frozenset(s.name for s in TOOL_SPECS)


def spec_for(name: str) -> ToolSpec | None:
    """Return the spec for ``name``, or None when the tool is not allowlisted."""
    for s in TOOL_SPECS:
        if s.name == name:
            return s
    return None


def tool_specs_by_name() -> dict[str, ToolSpec]:
    """A name->spec map (rebuilt per call; cheap at 6 entries)."""
    return {s.name: s for s in TOOL_SPECS}


def prune_config() -> dict[str, dict[str, Any]]:
    """Synthesis-input pruning config for LIST-type tools (cleaning.py).

    ``semantic_query`` results are condensed beyond the top hit: hits 1..N
    keep identification fields plus a 300-char snippet; redundant envelope
    metadata is dropped.
    """
    return {
        "semantic_query": {
            "list_key": "results",
            "keep_hit_fields": ("act", "ref", "title", "kind", "rank", "citation"),
            "keep_snippet_field": "snippet",
            "keep_envelope_fields": ("total", "returned"),
        },
    }


# Re-exported so callers that previously imported DEFAULT_TOOLS from config
# keep working; config.py imports this back for its Settings property.
DEFAULT_TOOLS: tuple[str, ...] = tuple(s.name for s in TOOL_SPECS)
