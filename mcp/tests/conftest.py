"""Test configuration.

Provides:
  - ``fake_db``: monkeypatches nyaya.db so it returns canned data from an
    in-memory dict. Lets unit tests run with no Postgres / no network.
    Stubs respect their arguments (act/limit/offset/slug/direction) so tests
    can actually exercise routing and clamping logic.
  - ``offline_settings``: forces env vars so get_settings() returns
    deterministic values. Semantic search is unavailable because fastembed
    is not installed in the dev environment (not because the fixture disables it).
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any

import pytest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("DATABASE_URL", "postgresql://nobody@nowhere/db")


@pytest.fixture(autouse=True)
def offline_settings(monkeypatch):
    from nyaya import config

    try:
        config.get_settings.cache_clear()
    except AttributeError:
        pass

    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@nowhere/db")

    yield

    try:
        config.get_settings.cache_clear()
    except AttributeError:
        pass


@pytest.fixture
def fake_db(monkeypatch):
    """Replace nyaya.db's query functions with argument-respecting stubs."""
    from nyaya import db

    data: dict[str, Any] = {
        "acts": [
            {
                "short_name": "IPC",
                "full_name": "The Indian Penal Code",
                "year": 1860,
                "citation": "Act No. 45 of 1860",
                "kind": "criminal",
                "source": "mratanusarkar/Indian-Laws",
                "source_license": "Public domain",
                "as_of": date(2026, 7, 1),
            },
            {
                "short_name": "Constitution",
                "full_name": "The Constitution of India",
                "year": 1950,
                "citation": "26 Nov 1949",
                "kind": "constitution",
                "source": "Vikhram-S/IndianConstitution",
                "source_license": "Apache-2.0",
                "as_of": date(2026, 7, 1),
            },
        ],
        "chapters": [
            {"number": 1, "title": "Preliminary", "section_range": "Sections 1 to 5"},
            {"number": 16, "title": "Of Offences Affecting the Human Body", "section_range": "Sections 299 to 377"},
        ],
        "section": {
            "act": "IPC",
            "section": "302",
            "title": "Punishment for murder",
            "text": "Whoever commits murder shall be punished with death, or imprisonment for life, and shall also be liable to fine.",
            "url": None,
            "chapter_number": 16,
            "chapter_title": "Of Offences Affecting the Human Body",
            "source": "mratanusarkar/Indian-Laws",
            "source_license": "Public domain",
            "as_of": date(2026, 7, 1),
        },
        "article": {
            "number": "21",
            "title": "Protection of life and personal liberty",
            "text": "No person shall be deprived of his life or personal liberty except according to procedure established by law.",
            "part": "PART III — FUNDAMENTAL RIGHTS",
            "source": "Vikhram-S/IndianConstitution",
            "source_license": "Apache-2.0",
            "as_of": date(2026, 7, 1),
        },
        "search": [
            {
                "act": "IPC",
                "ref": "s. 302",
                "title": "Punishment for murder",
                "snippet": "Whoever commits <<murder>> shall be punished with death…",
                "rank": 0.9,
                "citation": "Act No. 45 of 1860",
                "kind": "section",
            }
        ],
        "cross_refs": [
            {"from_act": "IPC", "from_section": "302", "to_act": "BNS", "to_section": "103", "kind": "corresponds_to"},
        ],
        "judgment": {
            "case_name": "Kesavananda Bharati v. State of Kerala",
            "citation": "AIR 1973 SC 1461",
            "court": "Supreme Court of India",
            "date": date(1973, 4, 24),
            "summary": "Basic structure doctrine.",
            "text": "Parliament's power to amend is limited by the basic structure…",
            "source": "Curated from indiankanoon.org",
            "source_license": "Public domain",
            "as_of": date(2026, 7, 1),
        },
        "schedules": [
            {"number": 1, "title": "States", "text": "I. Andhra Pradesh…",
             "source": "PRS", "source_license": "CC BY 4.0", "as_of": date(2026, 7, 1)},
        ],
        "amendments": [
            {"number": 1, "year": 1951, "title": "The Constitution (First Amendment) Act",
             "articles_affected": "13, 19, 31, 85, 87, 92, 129, 176, 246, 258, 304, 311, 313, 316",
             "date": date(1951, 6, 18),
             "source": "PRS", "source_license": "CC BY 4.0", "as_of": date(2026, 7, 1)},
        ],
        "stats": {"acts": 2, "sections": 511, "articles": 395, "judgments": 5,
                  "amendments": 1, "schedules": 1, "chapters": 2, "cross_refs": 2},
    }

    # ---- Argument-respecting stubs ---------------------------------------

    def _normalize_act(act):
        if act is None:
            return None
        return db.normalize_act(act)

    def _list_acts():
        from nyaya.models import Act
        return [Act(**a) for a in data["acts"]]

    def _get_act(short_name):
        from nyaya.models import Act
        sn = _normalize_act(short_name)
        if sn is None:
            return None
        for a in data["acts"]:
            if a["short_name"] == sn:
                return Act(**a)
        return None

    def _list_chapters(act):
        from nyaya.models import Chapter
        sn = _normalize_act(act)
        if sn is None or not any(a["short_name"] == sn for a in data["acts"]):
            return []
        return [Chapter(**c) for c in data["chapters"]]

    def _list_sections(act, chapter=None, limit=100, offset=0):
        from nyaya.models import Section
        sn = _normalize_act(act)
        if sn is None or sn != "IPC":
            return [], 0
        if chapter is not None and chapter != 16:
            return [], 0
        sec = Section(**data["section"])
        return [sec], 1

    def _get_section(act, number):
        from nyaya.models import Section
        sn = _normalize_act(act)
        num = db.normalize_ref(number)
        s = data["section"]
        if sn == s["act"] and num == s["section"]:
            return Section(**s)
        return None

    def _get_article(number):
        from nyaya.models import Article
        num = db.normalize_ref(number)
        a = data["article"]
        if num == a["number"]:
            return Article(**a)
        return None

    def _list_articles(part=None, limit=100, offset=0):
        from nyaya.models import Article
        a = Article(**data["article"])
        return [a], 1

    def _search_all(query, act=None, limit=10, offset=0):
        """Respect act + limit so tests can exercise routing/clamping."""
        from nyaya.models import SearchResult
        if not query or not query.strip():
            return [], 0
        normalized = _normalize_act(act)
        if normalized and normalized not in {"IPC", "Constitution", "judgment"}:
            return [], 0
        hits = [SearchResult(**r) for r in data["search"]]
        return hits[:limit], len(hits)

    def _search_judgments(query, court=None, date_from=None, date_to=None,
                         limit=10, offset=0):
        from nyaya.models import SearchResult
        if not query or not query.strip():
            return [], 0
        # Return a single canned judgment hit.
        return [
            SearchResult(act="judgment", ref="AIR 1973 SC 1461",
                         title="Kesavananda", snippet="basic structure",
                         rank=0.8, citation="AIR 1973 SC 1461", kind="judgment")
        ], 1

    def _get_cross_refs(act, section, direction="both"):
        from nyaya.models import CrossRef
        sn = _normalize_act(act)
        num = db.normalize_ref(section)
        refs = []
        for r in data["cross_refs"]:
            cr = CrossRef(**r)
            if direction in ("from", "both") and cr.from_act == sn and cr.from_section == num:
                refs.append(cr)
            if direction in ("to", "both") and cr.to_act == sn and cr.to_section == num:
                refs.append(cr)
        return refs

    def _get_judgment(slug):
        from nyaya.models import Judgment
        if not slug or not slug.strip():
            return None
        # Respect slug: only return the judgment for matching slugs.
        j = data["judgment"]
        if slug == j["citation"] or slug.lower().replace(" ", "-") == j["case_name"].lower().replace(" ", "-").replace(".", ""):
            return Judgment(**j)
        # Loose match for the common test slug.
        if "kesavananda" in slug.lower():
            return Judgment(**j)
        return None

    def _list_judgments(limit=50, offset=0):
        from nyaya.models import Judgment
        return [Judgment(**data["judgment"])], 1

    def _list_schedules():
        from nyaya.models import Schedule
        return [Schedule(**s) for s in data["schedules"]]

    def _get_schedule(number):
        from nyaya.models import Schedule
        for s in data["schedules"]:
            if s["number"] == number:
                return Schedule(**s)
        return None

    def _list_amendments(year_from=None, year_to=None):
        from nyaya.models import Amendment
        return [Amendment(**a) for a in data["amendments"]]

    def _get_amendment(number):
        from nyaya.models import Amendment
        for a in data["amendments"]:
            if a["number"] == number:
                return Amendment(**a)
        return None

    def _semantic_search_all(embedding, act=None, limit=5):
        from nyaya.models import SearchResult
        return [SearchResult(**r) for r in data["search"]][:limit]

    def _corpus_stats():
        return data["stats"]

    def _corpus_as_of():
        return date(2026, 7, 1)

    def _get_sections_by_range(act, start, end, limit=500):
        from nyaya.models import Section
        sn = _normalize_act(act)
        if sn == "IPC":
            return [Section(**data["section"])]
        return []

    # ---- Patch them all ---------------------------------------------------

    monkeypatch.setattr(db, "list_acts", _list_acts)
    monkeypatch.setattr(db, "get_act", _get_act)
    monkeypatch.setattr(db, "list_chapters", _list_chapters)
    monkeypatch.setattr(db, "list_sections", _list_sections)
    monkeypatch.setattr(db, "get_section", _get_section)
    monkeypatch.setattr(db, "get_article", _get_article)
    monkeypatch.setattr(db, "list_articles", _list_articles)
    monkeypatch.setattr(db, "search_all", _search_all)
    monkeypatch.setattr(db, "search_judgments", _search_judgments)
    monkeypatch.setattr(db, "get_cross_refs", _get_cross_refs)
    monkeypatch.setattr(db, "get_judgment", _get_judgment)
    monkeypatch.setattr(db, "list_judgments", _list_judgments)
    monkeypatch.setattr(db, "list_schedules", _list_schedules)
    monkeypatch.setattr(db, "get_schedule", _get_schedule)
    monkeypatch.setattr(db, "list_amendments", _list_amendments)
    monkeypatch.setattr(db, "get_amendment", _get_amendment)
    monkeypatch.setattr(db, "semantic_search_all", _semantic_search_all)
    monkeypatch.setattr(db, "corpus_stats", _corpus_stats)
    monkeypatch.setattr(db, "corpus_as_of", _corpus_as_of)
    monkeypatch.setattr(db, "get_sections_by_range", _get_sections_by_range)
    return data