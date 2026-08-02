"""Typed data models returned by MCP tools and resources."""

from __future__ import annotations

from datetime import date as Date
from typing import Literal

from pydantic import BaseModel, Field


class Provenance(BaseModel):
    source: str = Field(description="Where this text was sourced from, e.g. 'PRS (CC BY 4.0)' or 'indianconstitution PyPI (Apache-2.0)'.")
    source_license: str | None = Field(default=None, description="License under which the source was redistributed, if known.")
    as_of: Date | None = Field(default=None, description="Date the corpus snapshot was taken. Use this when citing currency.")


class Act(BaseModel):
    short_name: str = Field(description="Stable short identifier, e.g. 'IPC', 'BNS', 'Constitution'.")
    full_name: str = Field(description="Official long name, e.g. 'The Indian Penal Code, 1860'.")
    year: int | None = None
    citation: str | None = Field(default=None, description="Canonical citation form, e.g. 'Act No. 45 of 1860'.")
    kind: Literal["constitution", "criminal", "civil", "commercial", "judgment"] = Field(description="Corpus bucket the act belongs to.")
    source: str
    source_license: str | None = None
    as_of: Date | None = None


class Chapter(BaseModel):
    number: int
    title: str
    section_range: str | None = Field(default=None, description="Human-readable range of sections in the chapter, e.g. 'Sections 154 to 176'.")


class SectionRef(BaseModel):
    act: str = Field(description="Act short name, e.g. 'IPC'.")
    section: str = Field(description="Section number as a string, e.g. '302', '354A'.")
    title: str | None = None


class Section(SectionRef, Provenance):
    chapter_number: int | None = None
    chapter_title: str | None = None
    text: str = Field(description="Full section text, including any explanations and illustrations.")
    url: str | None = Field(default=None, description="Optional canonical URL for the section.")


class Article(Provenance):
    number: str = Field(description="Article number as a string, e.g. '21', '21A', '32'.")
    title: str
    text: str = Field(description="Full article text, including sub-clauses.")
    part: str | None = Field(default=None, description="Part of the Constitution the article belongs to, e.g. 'Part III — Fundamental Rights'.")


class Schedule(Provenance):
    number: int
    title: str
    text: str


class Amendment(Provenance):
    number: int
    year: int
    title: str
    articles_affected: str | None = Field(default=None, description="Comma-separated list of articles amended/inserted by this amendment.")
    date: Date | None = None


class Judgment(Provenance):
    case_name: str
    citation: str | None = None
    court: str = Field(default="Supreme Court of India")
    date: Date | None = None
    summary: str | None = Field(default=None, description="One-paragraph summary of the holding, if available.")
    text: str = Field(description="Full judgment text or a substantial excerpt.")


class CrossRef(BaseModel):
    from_act: str
    from_section: str
    to_act: str
    to_section: str
    kind: Literal["repeals", "replaced_by", "references", "corresponds_to", "amends"] = Field(description="How the source relates to the target.")


class SearchResult(BaseModel):
    act: str
    ref: str = Field(description="Section/article identifier, e.g. 's. 302' or 'art. 21'.")
    title: str | None = None
    snippet: str = Field(description="Relevance-ordered snippet of the matching text.")
    rank: float = Field(description="Relevance score. Higher = more relevant. Scale differs between FTS and semantic.")
    citation: str | None = None


class SearchResponse(BaseModel):
    query: str
    total: int
    results: list[SearchResult]
    source: str = "nyaya"
    as_of: Date | None = None


class ActsList(BaseModel):
    acts: list[Act]


class ChaptersList(BaseModel):
    act: str
    chapters: list[Chapter]


class CrossRefList(BaseModel):
    from_act: str
    from_section: str
    references: list[CrossRef]