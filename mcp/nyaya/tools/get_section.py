"""get_section: fetch a specific section of an act by number or citation string."""

from __future__ import annotations

import re

from .. import db
from ..exceptions import NotFound
from ..models import Document
from ._util import run_sync

# Matches "IPC s.302", "s.302 IPC", "s.302 of IPC", "BNS s.103".
_CITATION_RE = re.compile(
    r"(?:s(?:ec(?:tion)?)?\.?\s*(?P<num>\d+[A-Z]?)\s*(?:of\s+)?(?P<act>[A-Za-z]+)?)"
    r"|(?P<act2>[A-Za-z]+)\s+s(?:ec(?:tion)?)?\.?\s*(?P<num2>\d+[A-Z]?)",
    re.IGNORECASE,
)


def register(mcp) -> None:
    @mcp.tool(
        name="get_section",
        description=(
            "Fetch the full text of a specific section of an Indian act by its number. "
            "Supports IPC, CrPC, CPC, Evidence Act, BNS, BNSS, BSA, Companies Act, GST acts, "
            "IT Act, Arbitration Act, Consumer Protection Act, etc. Act names and section "
            "numbers are normalized (case-insensitive, whitespace-trimmed, common aliases "
            "like 'ipc' accepted). Also accepts combined citation strings like 'IPC s.302' "
            "or 's.302 of IPC' — the act and section number are parsed automatically. "
            "Use get_article instead for Constitution articles. "
            "If the section isn't in the corpus, raises a not_found error."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "Get a section by number"},
    )
    @run_sync
    def get_section(act: str = "", section: str = "") -> Document:
        """Get a section by act and number, or by a combined citation string.

        Args:
            act: Act short name or alias, e.g. 'IPC', 'ipc', 'Indian Penal Code', 'BNS'.
                If empty, the section argument is parsed as a citation string (e.g. 'IPC s.302').
            section: Section number as a string, e.g. '302', '354A', '154'. A leading
                's.' or 'section ' prefix is stripped automatically. If act is empty,
                this can be a full citation like 'IPC s.302' or 's.302 of IPC'.
        """
        # If act is empty or not provided, try parsing the section arg as a citation.
        if not act or not act.strip():
            m = _CITATION_RE.search(section or "")
            if m:
                parsed_act = m.group("act") or m.group("act2")
                parsed_num = m.group("num") or m.group("num2")
                if parsed_act and parsed_num:
                    result = db.get_section(parsed_act, parsed_num)
                    if result is not None:
                        return result
                    raise NotFound(
                        f"Section {parsed_num} of {parsed_act} is not in the nyaya corpus.",
                        kind="section",
                        hint="Call semantic_query with a topical query.",
                    )
        result = db.get_section(act, section)
        if result is None:
            raise NotFound(
                f"Section {section} of {act} is not in the nyaya corpus. "
                "The corpus is a frozen snapshot; some sections may be missing. "
                "Try semantic_query to find related provisions.",
                kind="section",
                hint="Call semantic_query with a topical query, or list_chapters to see the act's structure.",
            )
        return result
