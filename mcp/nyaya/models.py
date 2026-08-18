"""Typed data models returned by MCP tools and resources.

The v0.2 schema collapses the old per-kind tables (sections, articles, judgments,
schedules, amendments, chapters) into a single ``documents`` table with a ``kind``
discriminator and ``metadata jsonb`` for per-kind fields. These models mirror that
structure: one ``Document`` model covers all kinds, with ``metadata`` carrying
kind-specific fields (chapter_num, chapter_title, part, court, date, etc.).
"""

from __future__ import annotations

from datetime import date as Date
from typing import Any, Literal

from pydantic import BaseModel, Field

Kind = Literal["section", "article", "judgment", "schedule", "amendment"]
HitKind = Literal["section", "article", "judgment", "schedule", "amendment"]


class Provenance(BaseModel):
    source: str = Field(
        description="Where this text was sourced from, e.g. 'PRS (CC BY 4.0)' "
        "or 'indianconstitution PyPI (Apache-2.0)'."
    )
    source_license: str | None = Field(
        default=None,
        description="License under which the source was redistributed, if known.",
    )
    as_of: Date | None = Field(
        default=None,
        description="Date the corpus snapshot was taken. Use this when citing currency.",
    )


class Act(BaseModel):
    short_name: str = Field(description="Stable short identifier, e.g. 'IPC', 'BNS', 'Constitution'.")
    full_name: str = Field(description="Official long name, e.g. 'The Indian Penal Code, 1860'.")
    year: int | None = None
    citation: str | None = Field(default=None, description="Canonical citation form, e.g. 'Act No. 45 of 1860'.")
    kind: Literal["constitution", "criminal", "civil", "commercial", "judgment"] = Field(
        description="Corpus bucket the act belongs to."
    )
    source: str
    source_license: str | None = None
    as_of: Date | None = None


class Document(Provenance):
    """A single document in the unified ``documents`` table.

    ``kind`` discriminates between sections, articles, judgments, schedules, and
    amendments. ``metadata`` carries kind-specific fields:
    - sections: ``chapter_num``, ``chapter_title``, ``act_full``, ``act_year``, ``act_kind``
    - articles: ``part``
    - judgments: ``court``, ``date``, ``summary``, ``citation``
    - schedules: ``number``
    - amendments: ``number``, ``year``, ``date``, ``articles_affected``
    """

    kind: Kind
    ref: str = Field(description="Section/article/citation/schedule/amendment identifier, e.g. '302', '21', 'AIR 1973 SC 1461'.")
    act: str | None = Field(default=None, description="Act short name, e.g. 'IPC'. Null for standalone kinds (judgments, schedules, amendments).")
    title: str | None = None
    text: str = Field(max_length=200_000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CrossRef(BaseModel):
    from_act: str
    from_section: str
    to_act: str
    to_section: str
    kind: Literal["repeals", "replaced_by", "references", "corresponds_to", "amends"] = Field(
        description="How the source relates to the target."
    )


class SearchResult(BaseModel):
    act: str
    ref: str = Field(description="Section/article identifier, e.g. 's. 302' or 'art. 21'.")
    title: str | None = None
    snippet: str = Field(description="Relevance-ordered snippet of the matching text.")
    rank: float = Field(description="Relevance score. Higher = more relevant.")
    citation: str | None = None
    kind: HitKind | None = Field(
        default=None,
        description="Whether this hit is a section, article, judgment, schedule, or amendment.",
    )


class SearchResponse(BaseModel):
    query: str
    total: int = Field(description="Total number of matches found (before limit/offset).")
    returned: int = Field(default=0, description="Number of results returned in this response (<= limit).")
    offset: int = Field(default=0, description="Offset of the first result. 0 for the first page.")
    results: list[SearchResult]
    source: str = "nyaya"
    as_of: Date | None = None
    limit: int = Field(default=10, description="The limit applied to this query.")
    fallback_reason: str | None = Field(
        default=None,
        description="Set when a search component failed and the response degraded to a fallback.",
    )


class ActsList(BaseModel):
    acts: list[Act]


class CrossRefList(BaseModel):
    from_act: str
    from_section: str
    references: list[CrossRef]
    direction: Literal["from", "to", "both"] = Field(
        default="both", description="Whether the references are outgoing (from), incoming (to), or both."
    )


class DocumentsList(BaseModel):
    """A paginated list of documents, returned by list_sections/list_articles/etc."""

    documents: list[Document]
    total: int = Field(default=0, description="Total documents matching (before limit/offset).")
    offset: int = Field(default=0)
    limit: int = Field(default=100)
    # When list_sections is filtered by chapter, these are set to the chapter metadata.
    chapter_title: str | None = Field(default=None, description="Title of the chapter when filtered by chapter.")
    chapter_number: int | None = Field(default=None, description="Chapter number when filtered by chapter.")


class CorpusStats(BaseModel):
    """Corpus counts returned by the corpus_stats tool."""

    acts: int
    sections: int
    articles: int
    judgments: int
    amendments: int
    schedules: int
    cross_refs: int
    as_of: Date | None = None
