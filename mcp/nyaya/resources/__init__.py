"""MCP resources and resource templates for nyaya."""

from __future__ import annotations

import json

from .. import db
from ..config import SNIPPET_CHARS
from ..exceptions import NotFound
from ..models import Document


def _with_snippet(doc: Document, chars: int = SNIPPET_CHARS) -> dict:
    """Serialize a Document with its text truncated to ``chars``.

    List-style resources (judgments://, schedules://, amendments://) would
    otherwise ship multi-KB texts per row; full text stays available through
    the singular resources (judgment://{slug}, schedule://{n}, ...).
    """
    data = doc.model_dump(mode="json")
    data["text"] = doc.text[: max(1, int(chars))]
    return data


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
        description=(
            "All schedules of the Constitution of India (text snippets; use "
            "schedule://{number} for a full schedule text)."
        ),
        mime_type="application/json",
    )
    def all_schedules() -> str:
        scheds = db.list_schedules()
        return json.dumps([_with_snippet(s) for s in scheds], indent=2)

    @mcp.resource(
        "amendments://",
        name="Constitution amendments",
        description=(
            "All amendments to the Constitution of India (text snippets; use "
            "amendment://{number} for full amendment details)."
        ),
        mime_type="application/json",
    )
    def all_amendments() -> str:
        ams = db.list_amendments()
        return json.dumps([_with_snippet(a) for a in ams], indent=2)

    @mcp.resource(
        "judgments://",
        name="All landmark judgments",
        description=(
            "List of landmark Supreme Court judgments in the corpus, first 100 "
            "(text snippets; use judgment://{slug} for a case's full text)."
        ),
        mime_type="application/json",
    )
    def all_judgments() -> str:
        juds, _ = db.list_judgments(limit=100)
        return json.dumps([_with_snippet(j) for j in juds], indent=2)

    @mcp.resource(
        "act://{short_name}",
        name="Act metadata + table of contents",
        description="Metadata and chapter listing for a single act, by short name (e.g. 'IPC', 'BNS').",
        mime_type="application/json",
    )
    def act_metadata(short_name: str) -> str:
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
                "chapters": chapters,
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
                hint="Call semantic_query or list_chapters to find the right section.",
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
                hint="Call semantic_query or list_articles to find the right article.",
            )
        return art.model_dump_json(indent=2)

    @mcp.resource(
        "judgment://{case_slug}",
        name="Landmark judgment",
        description="Full text of a landmark Supreme Court judgment by citation or slugified case name.",
        mime_type="application/json",
    )
    def judgment_resource(case_slug: str) -> str:
        jud = db.get_judgment(case_slug)
        if jud is None:
            raise NotFound(
                f"Judgment {case_slug!r} not found in corpus.",
                kind="judgment",
                hint="Call judgments:// or semantic_query to find cases.",
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
