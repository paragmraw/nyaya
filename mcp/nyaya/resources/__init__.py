"""MCP resources and resource templates for nyaya."""

from __future__ import annotations

import json

from .. import db
from ..exceptions import NotFound


def register(mcp) -> None:
    @mcp.resource(
        "corpus://",
        name="Corpus overview",
        description="Summary of the nyaya corpus: act count, section count, article count, judgment count, and the as-of date.",
        mime_type="application/json",
    )
    def corpus_overview() -> str:
        stats = db.corpus_stats()
        return json.dumps(
            {
                "name": "nyaya",
                "description": "Indian law MCP server",
                "as_of": "2026-07-01",
                "counts": stats,
                "acts_url": "act://",
            },
            indent=2,
        )

    @mcp.resource(
        "acts://",
        name="All acts",
        description="List of all acts in the corpus with provenance.",
        mime_type="application/json",
    )
    def all_acts() -> str:
        acts = db.list_acts()
        return json.dumps([a.model_dump(mode="json") for a in acts], indent=2)

    @mcp.resource(
        "schedules://",
        name="Constitution schedules",
        description="All schedules of the Constitution of India.",
        mime_type="application/json",
    )
    def all_schedules() -> str:
        scheds = db.list_schedules()
        return json.dumps([s.model_dump(mode="json") for s in scheds], indent=2)

    @mcp.resource(
        "amendments://",
        name="Constitution amendments",
        description="All amendments to the Constitution of India.",
        mime_type="application/json",
    )
    def all_amendments() -> str:
        ams = db.list_amendments()
        return json.dumps([a.model_dump(mode="json") for a in ams], indent=2)

    @mcp.resource(
        "act://{short_name}",
        name="Act metadata + table of contents",
        description="Metadata and chapter listing for a single act, by short name (e.g. 'IPC', 'BNS').",
        mime_type="application/json",
    )
    def act_metadata(short_name: str) -> str:
        act = db.get_act(short_name)
        if act is None:
            raise NotFound(f"Act {short_name!r} not found in corpus.")
        chapters = db.list_chapters(short_name)
        return json.dumps(
            {
                "act": act.model_dump(mode="json"),
                "chapters": [c.model_dump(mode="json") for c in chapters],
            },
            indent=2,
        )

    @mcp.resource(
        "section://{act}/{number}",
        name="Section text",
        description="Full text of a section by act short name and section number, e.g. section://IPC/302.",
        mime_type="application/json",
    )
    def section_resource(act: str, number: str) -> str:
        sec = db.get_section(act, number)
        if sec is None:
            raise NotFound(f"Section {number} of {act} not found in corpus.")
        return sec.model_dump_json(indent=2)

    @mcp.resource(
        "article://{number}",
        name="Constitution article",
        description="Full text of a Constitution article by number, e.g. article://21.",
        mime_type="application/json",
    )
    def article_resource(number: str) -> str:
        art = db.get_article(number)
        if art is None:
            raise NotFound(f"Article {number!r} not found in corpus.")
        return art.model_dump_json(indent=2)

    @mcp.resource(
        "judgment://{case_slug}",
        name="Landmark judgment",
        description="Full text of a landmark Supreme Court judgment by citation or slugified case name, e.g. judgment://kesavananda-bharati-v-state-of-kerala.",
        mime_type="application/json",
    )
    def judgment_resource(case_slug: str) -> str:
        jud = db.get_judgment(case_slug)
        if jud is None:
            raise NotFound(f"Judgment {case_slug!r} not found in corpus.")
        return jud.model_dump_json(indent=2)