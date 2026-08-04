"""Test configuration.

Provides:
  - ``fake_db``: monkeypatches nyaya.db so it returns canned data from an
    in-memory dict. Lets unit tests run with no Postgres / no network.
    Stubs respect their arguments (act/limit/offset/slug/direction/filters)
    so tests can exercise routing, clamping, pagination, and filter logic.
  - ``offline_settings``: forces env vars so get_settings() returns
    deterministic values.
"""

from __future__ import annotations

import os
import sys
from datetime import date
from typing import Any

import pytest

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
        "section_303": {
            "act": "IPC", "section": "303", "title": "Punishment for murder by life-convict",
            "text": "Whoever, being under sentence of imprisonment for life, commits murder…",
            "url": None, "chapter_number": 16, "chapter_title": "Of Offences Affecting the Human Body",
            "source": "mratanusarkar/Indian-Laws", "source_license": "Public domain", "as_of": date(2026, 7, 1),
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
                "act": "IPC", "ref": "s. 302", "title": "Punishment for murder",
                "snippet": "Whoever commits <<murder>> shall be punished with death…",
                "rank": 0.9, "citation": "Act No. 45 of 1860", "kind": "section",
            },
            {
                "act": "IPC", "ref": "s. 303", "title": "Punishment for murder by life-convict",
                "snippet": "Whoever, being under sentence…commits <<murder>>…",
                "rank": 0.7, "citation": "Act No. 45 of 1860", "kind": "section",
            },
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
             "articles_affected": "13, 19, 31", "date": date(1951, 6, 18),
             "source": "PRS", "source_license": "CC BY 4.0", "as_of": date(2026, 7, 1)},
        ],
        "stats": {"acts": 2, "sections": 511, "articles": 395, "judgments": 5,
                  "amendments": 1, "schedules": 1, "chapters": 2, "cross_refs": 2},
    }

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
            if a["short_name"].lower() == sn.lower():
                return Act(**a)
        return None

    def _list_chapters(act):
        from nyaya.models import Chapter
        sn = _normalize_act(act)
        if sn is None or not any(a["short_name"].lower() == sn.lower() for a in data["acts"]):
            return []
        return [Chapter(**c) for c in data["chapters"]]

    def _list_sections(act, chapter=None, limit=100, offset=0):
        """Respect chapter filter, limit, and offset. Returns (sections, total)
        where total > len(sections) when offset > 0 (to test pagination)."""
        from nyaya.models import Section
        sn = _normalize_act(act)
        if sn is None or sn.lower() != "ipc":
            return [], 0
        all_sections = [Section(**data["section"]), Section(**data["section_303"])]
        if chapter is not None:
            all_sections = [s for s in all_sections if s.chapter_number == chapter]
        total = len(all_sections)
        paginated = all_sections[offset:offset + limit]
        return paginated, total

    def _get_section(act, number):
        from nyaya.models import Section
        sn = _normalize_act(act)
        num = db.normalize_ref(number)
        for key in ("section", "section_303"):
            s = data[key]
            if sn and sn.lower() == s["act"].lower() and num == s["section"]:
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
        """Respect part filter and offset. Returns (articles, total)."""
        from nyaya.models import Article
        a = Article(**data["article"])
        if part and part.lower() not in (a.part or "").lower():
            return [], 0
        return [a], 1

    def _search_all(query, act=None, limit=10, offset=0):
        """Respect act + limit + offset. Returns (results, total) where
        total > len(results) when there are more hits than limit."""
        from nyaya.models import SearchResult
        if not query or not query.strip():
            return [], 0
        normalized = _normalize_act(act)
        if normalized and normalized.lower() not in {"ipc", "constitution", "judgment"}:
            return [], 0
        hits = [SearchResult(**r) for r in data["search"]]
        total = len(hits)
        return hits[offset:offset + limit], total

    def _search_sections(query, act=None, limit=10, offset=0):
        from nyaya.models import SearchResult
        if not query or not query.strip():
            return [], 0
        hits = [SearchResult(**r) for r in data["search"]]
        return hits[offset:offset + limit], len(hits)

    def _search_articles(query, limit=10, offset=0):
        if not query or not query.strip():
            return [], 0
        return [], 0

    def _search_judgments(query, court=None, date_from=None, date_to=None,
                         limit=10, offset=0):
        """Respect query non-empty. Returns (results, total)."""
        from nyaya.models import SearchResult
        if not query or not query.strip():
            return [], 0
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
            if direction in ("from", "both") and cr.from_act.lower() == (sn or "").lower() and cr.from_section == num:
                refs.append(cr)
            if direction in ("to", "both") and cr.to_act.lower() == (sn or "").lower() and cr.to_section == num:
                refs.append(cr)
        return refs

    def _get_judgment(slug):
        from nyaya.models import Judgment
        if not slug or not slug.strip():
            return None
        j = data["judgment"]
        if slug == j["citation"]:
            return Judgment(**j)
        if slug.lower().replace(" ", "-") == j["case_name"].lower().replace(" ", "-").replace(".", ""):
            return Judgment(**j)
        if len(slug) >= 4 and "kesavananda" in slug.lower():
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
        """Respect year_from/year_to filters."""
        from nyaya.models import Amendment
        result = []
        for a in data["amendments"]:
            if year_from is not None and a["year"] < year_from:
                continue
            if year_to is not None and a["year"] > year_to:
                continue
            result.append(Amendment(**a))
        return result

    def _get_amendment(number):
        from nyaya.models import Amendment
        for a in data["amendments"]:
            if a["number"] == number:
                return Amendment(**a)
        return None

    def _get_amendments_for_article(article):
        import re

        from nyaya.models import Amendment
        art = db.normalize_ref(article)
        result = []
        for a in data["amendments"]:
            if a.get("articles_affected") and re.search(rf"\b{re.escape(art or '')}\b", a["articles_affected"]):
                result.append(Amendment(**a))
        return result

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
        if sn and sn.lower() == "ipc":
            return [Section(**data["section"])]
        return []

    monkeypatch.setattr(db, "list_acts", _list_acts)
    monkeypatch.setattr(db, "get_act", _get_act)
    monkeypatch.setattr(db, "list_chapters", _list_chapters)
    monkeypatch.setattr(db, "list_sections", _list_sections)
    monkeypatch.setattr(db, "get_section", _get_section)
    monkeypatch.setattr(db, "get_article", _get_article)
    monkeypatch.setattr(db, "list_articles", _list_articles)
    monkeypatch.setattr(db, "search_all", _search_all)
    monkeypatch.setattr(db, "search_sections", _search_sections)
    monkeypatch.setattr(db, "search_articles", _search_articles)
    monkeypatch.setattr(db, "search_judgments", _search_judgments)
    monkeypatch.setattr(db, "get_cross_refs", _get_cross_refs)
    monkeypatch.setattr(db, "get_judgment", _get_judgment)
    monkeypatch.setattr(db, "list_judgments", _list_judgments)
    monkeypatch.setattr(db, "list_schedules", _list_schedules)
    monkeypatch.setattr(db, "get_schedule", _get_schedule)
    monkeypatch.setattr(db, "list_amendments", _list_amendments)
    monkeypatch.setattr(db, "get_amendment", _get_amendment)
    monkeypatch.setattr(db, "get_amendments_for_article", _get_amendments_for_article)
    monkeypatch.setattr(db, "semantic_search_all", _semantic_search_all)
    monkeypatch.setattr(db, "corpus_stats", _corpus_stats)
    monkeypatch.setattr(db, "corpus_as_of", _corpus_as_of)
    monkeypatch.setattr(db, "get_sections_by_range", _get_sections_by_range)
    return data
