"""MCP v2.0 8 ``nyaya://`` resources and resource templates."""

from __future__ import annotations

import json
from typing import Any

from ..exceptions import NotFound
from ..storage import get_backend
from ..tools._util import effective_commercial_safe
from ..tools.browse_statute import browse_statute
from ..tools.corpus_status import corpus_status
from ..tools.get_judgment import get_judgment
from ..tools.get_provision import get_provision
from ..tools.get_provision_history import get_provision_history


def read_corpus_status() -> str:
    """nyaya://corpus/status -> Full corpus coverage matrix, per-source licenses, and active backends."""
    res = corpus_status(include_sources=True)
    return res.model_dump_json(indent=2)


def read_statutes() -> str:
    """nyaya://statutes -> Compact index of all indexed Central/State/UT Acts & Regulators."""
    res = browse_statute(act=None, limit=200)
    return res.model_dump_json(indent=2)


def read_statute(act: str) -> str:
    """nyaya://statute/{act} -> Single statute/regulator metadata + Chapters/Parts table of contents."""
    res = browse_statute(act=act)
    return res.model_dump_json(indent=2)


def read_provision(act: str, ref: str) -> str:
    """nyaya://provision/{act}/{ref} -> Verbatim provision text (<corpus_text>) + StatutoryValidity + links."""
    res = get_provision(act=act, ref=ref, include_links=True)
    return res.model_dump_json(indent=2)


def read_amendment(act: str, number: str) -> str:
    """nyaya://amendment/{act}/{number} -> Constitutional or statutory amendment details + affected provisions."""
    try:
        num = int(str(number).strip())
    except ValueError as exc:
        raise NotFound(
            f"Amendment {number!r} is not a valid integer.",
            kind="amendment",
            hint="Use an integer amendment number such as nyaya://amendment/Constitution/42.",
        ) from exc
    res = get_provision_history(act=act, amendment_number=num)
    return res.model_dump_json(indent=2)


async def read_judgment(identifier: str) -> str:
    """nyaya://judgment/{identifier} -> Judgment overview card + opening/operative paragraphs."""
    res = await get_judgment(identifier=identifier, view="overview")
    return res.model_dump_json(indent=2)


async def read_judgment_paragraphs(identifier: str, start: str, end: str) -> str:
    """nyaya://judgment/{identifier}/paragraphs/{start}-{end} -> Verbatim paragraph window (capped at 30 paras / 24,000 chars)."""
    try:
        p_start = max(1, int(str(start).strip()))
        p_end = max(p_start, int(str(end).strip()))
    except ValueError as exc:
        raise NotFound(
            f"Invalid paragraph range {start!r}-{end!r}; expected positive integers.",
            kind="paragraphs",
        ) from exc
    capped_end = min(p_end, p_start + 29)
    res = await get_judgment(
        identifier=identifier,
        view="paragraphs",
        para_start=p_start,
        para_end=capped_end,
        max_chars=25_000,
    )
    return res.model_dump_json(indent=2)


def read_courts() -> str:
    """nyaya://courts -> Canonical Indian Judicial & Regulatory Directory."""
    backend = get_backend()
    courts = [c.model_dump(mode="json") for c in backend.list_courts()]
    return json.dumps(
        {
            "total": len(courts),
            "commercial_safe_only": effective_commercial_safe(None),
            "courts": courts,
        },
        indent=2,
    )


def register(mcp: Any) -> None:
    """Register the 8 canonical ``nyaya://`` MCP v2.0 resources and templates."""

    @mcp.resource(
        "nyaya://corpus/status",
        name="Corpus coverage & licensing status",
        description=(
            "Full corpus coverage matrix, per-source SPDX licenses (commercial_safe, terms_status), "
            "freshness as_of, and active retrieval backends."
        ),
        mime_type="application/json",
    )
    def _corpus_status_resource() -> str:
        return read_corpus_status()

    @mcp.resource(
        "nyaya://statutes",
        name="All indexed statutes & regulators",
        description=(
            "Compact index of all 37 Central+State/UT Acts & 12 Regulators "
            "(short_name, full_name, jurisdiction, year, status, provision_count, provenance)."
        ),
        mime_type="application/json",
    )
    def _statutes_resource() -> str:
        return read_statutes()

    @mcp.resource(
        "nyaya://statute/{act}",
        name="Statute metadata & table of contents",
        description=(
            "Single statute/regulator metadata + Chapters/Parts table of contents "
            "(accepts canonical codes like BNS, IPC, Constitution, MH-MOFA, SEBI-LODR)."
        ),
        mime_type="application/json",
    )
    def _statute_resource(act: str) -> str:
        return read_statute(act)

    @mcp.resource(
        "nyaya://provision/{act}/{ref}",
        name="Provision verbatim text & StatutoryValidity envelope",
        description=(
            "Verbatim provision text (<corpus_text>) + StatutoryValidity envelope + compact "
            "cross-links (unifies sections, Constitution articles, schedules, and CPC orders/rules)."
        ),
        mime_type="application/json",
    )
    def _provision_resource(act: str, ref: str) -> str:
        return read_provision(act, ref)

    @mcp.resource(
        "nyaya://amendment/{act}/{number}",
        name="Constitutional or statutory amendment",
        description=(
            "Constitutional amendment (e.g. nyaya://amendment/Constitution/42) or Central Act "
            "amendment details + affected provisions."
        ),
        mime_type="application/json",
    )
    def _amendment_resource(act: str, number: str) -> str:
        return read_amendment(act, number)

    @mcp.resource(
        "nyaya://judgment/{identifier}",
        name="Judgment overview card & opening/operative excerpts",
        description=(
            "Judgment overview card + opening/operative paragraphs by CNR, Neutral Citation "
            "(2024 INSC 123, 2024:DHC:1234), AIR/SCC/SCR, or case_id."
        ),
        mime_type="application/json",
    )
    async def _judgment_resource(identifier: str) -> str:
        return await read_judgment(identifier)

    @mcp.resource(
        "nyaya://judgment/{identifier}/paragraphs/{start}-{end}",
        name="Judgment verbatim paragraph window",
        description=(
            "Verbatim paragraph-range citation deep-link (hard-capped at 30 paragraphs / 24,000 chars default, 30,000 max)."
        ),
        mime_type="application/json",
    )
    async def _judgment_paragraphs_resource(identifier: str, start: str, end: str) -> str:
        return await read_judgment_paragraphs(identifier, start, end)

    @mcp.resource(
        "nyaya://courts",
        name="Indian Judicial & Regulatory Directory",
        description=(
            "Canonical Indian Judicial & Regulatory Directory: Supreme Court, 25 High Courts & "
            "45 Benches (with State/UT territorial binding map), 15 Tribunals, 12 Regulators, "
            "and DDL District Court state codes."
        ),
        mime_type="application/json",
    )
    def _courts_resource() -> str:
        return read_courts()
