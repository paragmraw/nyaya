"""Unit tests for the Pydantic models."""

from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from nyaya.models import (
    Act,
    Amendment,
    Article,
    ArticlesList,
    ChapterWithSections,
    CorpusStats,
    CrossRef,
    Judgment,
    JudgmentsList,
    Provenance,
    Schedule,
    SearchResponse,
    SearchResult,
    Section,
    SectionsList,
)


def test_act_basic():
    a = Act(short_name="IPC", full_name="The Indian Penal Code", year=1860, kind="criminal", source="test")
    assert a.short_name == "IPC"
    assert a.kind == "criminal"
    assert a.source_license is None


def test_act_kind_invalid_rejected():
    with pytest.raises(ValidationError):
        Act(short_name="X", full_name="Y", kind="bogus", source="t")


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
    with pytest.raises(ValidationError):
        CrossRef(from_act="IPC", from_section="302", to_act="BNS", to_section="103", kind="bogus")


def test_search_response():
    r = SearchResponse(query="murder", total=1, results=[SearchResult(act="IPC", ref="s. 302", snippet="…", rank=0.9)])
    assert r.results[0].act == "IPC"
    assert r.source == "nyaya"


def test_search_result_kind():
    r = SearchResult(act="IPC", ref="s. 302", snippet="…", rank=0.9, kind="section")
    assert r.kind == "section"


def test_judgment_defaults():
    j = Judgment(case_name="X v. Y", text="…", source="test")
    assert j.court == "Supreme Court of India"
    assert j.summary is None


def test_schedule():
    s = Schedule(number=1, title="States", text="…", source="x")
    assert s.number == 1


def test_amendment():
    a = Amendment(number=42, year=1976, title="The Constitution (Forty-second Amendment) Act", source="PRS")
    assert a.number == 42
    assert a.year == 1976


def test_cross_ref_list_direction():
    from nyaya.models import CrossRefList
    crl = CrossRefList(from_act="IPC", from_section="302", references=[], direction="both")
    assert crl.direction == "both"


def test_cross_ref_list_direction_invalid_rejected():
    from nyaya.models import CrossRefList
    with pytest.raises(ValidationError):
        CrossRefList(from_act="IPC", from_section="302", references=[], direction="sideways")


def test_sections_list_model():
    s = Section(act="IPC", section="302", title="Murder", text="…", source="x")
    sl = SectionsList(act="IPC", sections=[s], total=100, offset=0, limit=10)
    assert sl.total == 100
    assert sl.limit == 10


def test_articles_list_model():
    a = Article(number="21", title="Life", text="…", source="x")
    al = ArticlesList(articles=[a], total=395, offset=0, limit=100)
    assert al.total == 395


def test_judgments_list_model():
    j = Judgment(case_name="X v. Y", text="…", source="x")
    jl = JudgmentsList(judgments=[j], total=5, offset=0, limit=50)
    assert jl.total == 5


def test_corpus_stats_model():
    cs = CorpusStats(acts=2, sections=511, articles=395, judgments=5,
                     amendments=106, schedules=12, chapters=23, cross_refs=200)
    assert cs.acts == 2
    assert cs.cross_refs == 200


def test_chapter_with_sections_model():
    s = Section(act="IPC", section="302", title="Murder", text="…", source="x")
    cws = ChapterWithSections(act="IPC", number=16, title="Offences", sections=[s])
    assert cws.number == 16
    assert len(cws.sections) == 1


def test_search_response_returned_offset_limit():
    r = SearchResponse(query="x", total=100, returned=10, offset=20,
                       results=[SearchResult(act="IPC", ref="s.1", snippet="…", rank=0.5)],
                       limit=10)
    assert r.returned == 10
    assert r.offset == 20
    assert r.limit == 10


def test_act_kind_all_valid():
    for kind in ("constitution", "criminal", "civil", "commercial", "judgment"):
        a = Act(short_name="X", full_name="Y", kind=kind, source="t")
        assert a.kind == kind


def test_amendment_full_fields():
    a = Amendment(number=42, year=1976, title="The Constitution (42nd Amendment) Act",
                  articles_affected="13, 368", date=date(1976, 6, 18),
                  source="PRS", source_license="CC BY 4.0", as_of=date(2026, 7, 1))
    assert a.articles_affected == "13, 368"
    assert a.date == date(1976, 6, 18)
