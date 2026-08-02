"""Test configuration.

Provides:
  - `fake_db`: monkeypatches nyaya.db so it returns canned data from an
    in-memory dict. Lets unit tests run with no Postgres / no network.
  - `offline_settings`: forces env vars so get_settings() returns deterministic
    values and disables semantic search.
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
    """Replace nyaya.db's query functions with ones returning canned data."""
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
        "stats": {"acts": 2, "sections": 511, "articles": 395, "judgments": 5, "amendments": 9, "schedules": 2},
    }

    def _list_acts():
        from nyaya.models import Act
        return [Act(**a) for a in data["acts"]]

    def _get_act(short_name):
        from nyaya.models import Act
        for a in data["acts"]:
            if a["short_name"] == short_name:
                return Act(**a)
        return None

    def _list_chapters(act):
        from nyaya.models import Chapter
        if not any(a["short_name"] == act for a in data["acts"]):
            return []
        return [Chapter(**c) for c in data["chapters"]]

    def _get_section(act, number):
        from nyaya.models import Section
        s = data["section"]
        if s["act"] == act and s["section"] == number:
            return Section(**s)
        return None

    def _get_article(number):
        from nyaya.models import Article
        a = data["article"]
        if a["number"] == number:
            return Article(**a)
        return None

    def _search_all(query, act=None, limit=10):
        from nyaya.models import SearchResult
        return [SearchResult(**r) for r in data["search"]]

    def _get_cross_refs(act, section):
        from nyaya.models import CrossRef
        return [CrossRef(**r) for r in data["cross_refs"]]

    def _get_judgment(slug):
        from nyaya.models import Judgment
        return Judgment(**data["judgment"])

    def _corpus_stats():
        return data["stats"]

    def _list_schedules():
        return []

    def _list_amendments():
        return []

    monkeypatch.setattr(db, "list_acts", _list_acts)
    monkeypatch.setattr(db, "get_act", _get_act)
    monkeypatch.setattr(db, "list_chapters", _list_chapters)
    monkeypatch.setattr(db, "get_section", _get_section)
    monkeypatch.setattr(db, "get_article", _get_article)
    monkeypatch.setattr(db, "search_all", _search_all)
    monkeypatch.setattr(db, "get_cross_refs", _get_cross_refs)
    monkeypatch.setattr(db, "get_judgment", _get_judgment)
    monkeypatch.setattr(db, "corpus_stats", _corpus_stats)
    monkeypatch.setattr(db, "list_schedules", _list_schedules)
    monkeypatch.setattr(db, "list_amendments", _list_amendments)
    return data