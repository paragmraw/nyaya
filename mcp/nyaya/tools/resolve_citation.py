"""resolve_citation: parse a legal citation and fetch the referenced provision."""

from __future__ import annotations

import re

from .. import db
from ..exceptions import NotFound, SearchError
from ..models import Article, Judgment, Section
from ._util import run_sync

# Matches "IPC s.302", "s.302 IPC", "Art.21", "AIR 1973 SC 1461".
_SEC_RE = re.compile(
    r"(?:s(?:ec(?:tion)?)?\.?\s*(?P<num>\d+[A-Z]?)\s*(?:of\s+)?(?P<act>[A-Za-z]+)?)"
    r"|(?P<act2>[A-Za-z]+)\s+s(?:ec(?:tion)?)?\.?\s*(?P<num2>\d+[A-Z]?)",
    re.IGNORECASE,
)
_ART_RE = re.compile(r"art(?:icle)?\.?\s*(?P<num>\d+[A-Z]?)", re.IGNORECASE)


def register(mcp) -> None:
    @mcp.tool(
        name="resolve_citation",
        description=(
            "Parse a legal citation string (e.g. 'IPC s.302', 's.302 IPC', 'Art.21', "
            "'AIR 1973 SC 1461') and fetch the referenced section, article, or judgment. "
            "Use this when the user pastes a citation from another document and you need to "
            "fetch the actual provision text."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "Resolve a citation"},
    )
    @run_sync
    def resolve_citation(citation: str) -> Section | Article | Judgment:
        """Resolve a legal citation to a section, article, or judgment.

        Args:
            citation: A citation string, e.g. 'IPC s.302', 'Art.21', 'AIR 1973 SC 1461'.
        """
        if not citation or not citation.strip():
            raise SearchError("citation must be non-empty.")

        c = citation.strip()

        # Try section pattern first: "IPC s.302" or "s.302 IPC".
        m = _SEC_RE.search(c)
        if m:
            num = m.group("num") or m.group("num2")
            act = m.group("act") or m.group("act2")
            if num and act:
                sec = db.get_section(act, num)
                if sec:
                    return sec

        # Try article pattern: "Art.21".
        m = _ART_RE.search(c)
        if m:
            art = db.get_article(m.group("num"))
            if art:
                return art

        # Try judgment by citation or case name.
        jud = db.get_judgment(c)
        if jud:
            return jud

        raise NotFound(
            f"Could not resolve citation {citation!r}.",
            kind="unknown",
            hint="Try search_law with the citation text to find related provisions.",
        )