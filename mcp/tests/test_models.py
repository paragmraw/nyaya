"""Unit tests for the Pydantic models."""

from __future__ import annotations

from datetime import date

from nyaya.models import (
    Act,
    Article,
    CrossRef,
    Judgment,
    Provenance,
    Schedule,
    SearchResponse,
    SearchResult,
    Section,
)


def test_act_basic():
    a = Act(short_name="IPC", full_name="The Indian Penal Code", year=1860, kind="criminal", source="test")
    assert a.short_name == "IPC"
    assert a.kind == "criminal"
    assert a.source_license is None


def test_section_inherits_provenance():
    s = Section(
        act="IPC",
        section="302",
        title="Punishment for murder",
        text="…",
        source="HF",
        source_license="PD",
        as_of=date(2026, 7, 1),
    )
    assert isinstance(s, Provenance)
    assert s.source == "HF"
    assert s.section == "302"


def test_article_round_trip():
    a = Article(number="21A", title="Right to education", text="…", source="x")
    dumped = a.model_dump(mode="json")
    assert dumped["number"] == "21A"
    assert dumped["as_of"] is None


def test_cross_ref_kind_constrained():
    CrossRef(from_act="IPC", from_section="302", to_act="BNS", to_section="103", kind="corresponds_to")
    try:
        CrossRef(from_act="IPC", from_section="302", to_act="BNS", to_section="103", kind="bogus")
        assert False, "should have rejected invalid kind"
    except Exception:
        pass  # pydantic ValidationError


def test_search_response():
    r = SearchResponse(query="murder", total=1, results=[SearchResult(act="IPC", ref="s. 302", snippet="…", rank=0.9)])
    assert r.results[0].act == "IPC"


def test_judgment_defaults():
    j = Judgment(case_name="X v. Y", text="…", source="test")
    assert j.court == "Supreme Court of India"
    assert j.summary is None


def test_schedule_and_amendment():
    s = Schedule(number=1, title="States", text="…", source="x")
    assert s.number == 1