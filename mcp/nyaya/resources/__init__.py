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
        as_of = db.corpus_as_of()
        return json.dumps(
            {
                "name": "nyaya",
                "description": "Indian law MCP server",
                "as_of": as_of.isoformat() if as_of else None,
                "counts": stats,
                "acts_url": "acts://",
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
        "judgments://",
        name="All landmark judgments",
        description="List of landmark Supreme Court judgments in the corpus (first 100).",
        mime_type="application/json",
    )
    def all_judgments() -> str:
        juds, _ = db.list_judgments(limit=100)
        return json.dumps([j.model_dump(mode="json") for j in juds], indent=2)

    @mcp.resource(
        "act://{short_name}",
        name="Act metadata + table of contents",
        description="Metadata and chapter listing for a single act, by short name (e.g. 'IPC', 'BNS').",
        mime_type="application/json",
    )
    def act_metadata(short_name: str) -> str:
        # NOTE: optimization opportunity — get_act + list_chapters are two round
        # trips. They could be combined into a single query (e.g. a join or a
        # CTE returning the act row plus its chapters), but db.py has no such
        # combined function today. Left as-is: both calls are cheap and the code
        # is clearer with them separated.
        act = db.get_act(short_name)
        if act is None:
            raise NotFound(
                f"Act {short_name!r} not found in corpus.",
                kind="act",
                hint="Call acts:// or the list_acts tool to enumerate the corpus.",
            )
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
            raise NotFound(
                f"Section {number} of {act} not found in corpus.",
                kind="section",
                hint="Call search_law or list_chapters to find the right section.",
            )
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
            raise NotFound(
                f"Article {number!r} not found in corpus.",
                kind="article",
                hint="Call search_law or list_articles to find the right article.",
            )
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
            raise NotFound(
                f"Judgment {case_slug!r} not found in corpus.",
                kind="judgment",
                hint="Call judgments:// or search_judgments to find cases.",
            )
        return jud.model_dump_json(indent=2)

    @mcp.resource(
        "amendment://{number}",
        name="Constitution amendment",
        description="A specific Constitutional amendment by number, e.g. amendment://42.",
        mime_type="application/json",
    )
    def amendment_resource(number: str) -> str:
        try:
            num = int(number)
        except ValueError:
            raise NotFound(
                f"Amendment {number!r} is not a valid number.",
                kind="amendment",
                hint="Amendment numbers are integers, e.g. amendment://42.",
            )
        am = db.get_amendment(num)
        if am is None:
            raise NotFound(
                f"Amendment {number!r} not found in corpus.",
                kind="amendment",
                hint="Call amendments:// to list all amendments.",
            )
        return am.model_dump_json(indent=2)

    @mcp.resource(
        "schedule://{number}",
        name="Constitution schedule",
        description="A specific Constitution Schedule by number (1-12), e.g. schedule://9.",
        mime_type="application/json",
    )
    def schedule_resource(number: str) -> str:
        try:
            num = int(number)
        except ValueError:
            raise NotFound(
                f"Schedule {number!r} is not a valid number.",
                kind="schedule",
                hint="Schedule numbers are integers 1-12, e.g. schedule://9.",
            )
        sched = db.get_schedule(num)
        if sched is None:
            raise NotFound(
                f"Schedule {number!r} not found in corpus.",
                kind="schedule",
                hint="Call schedules:// to list all schedules.",
            )
        return sched.model_dump_json(indent=2)
