"""get_section: fetch a specific section of an act by number."""

from __future__ import annotations

from .. import db
from ..exceptions import NotFound
from ..models import Section
from ._util import run_sync


def register(mcp) -> None:
    @mcp.tool(
        name="get_section",
        description=(
            "Fetch the full text of a specific section of an Indian act by its number. "
            "Supports IPC, CrPC, CPC, Evidence Act, BNS, BNSS, BSA, Companies Act, GST acts, "
            "IT Act, Arbitration Act, Consumer Protection Act, etc. Act names and section "
            "numbers are normalized (case-insensitive, whitespace-trimmed, common aliases "
            "like 'ipc' accepted). Use get_article instead for Constitution articles. "
            "If the section isn't in the corpus, raises a not_found error."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "Get a section by number"},
    )
    @run_sync
    def get_section(act: str, section: str) -> Section:
        """Get a section by act and number.

        Args:
            act: Act short name or alias, e.g. 'IPC', 'ipc', 'Indian Penal Code', 'BNS'.
            section: Section number as a string, e.g. '302', '354A', '154'. A leading
                's.' or 'section ' prefix is stripped automatically.
        """
        result = db.get_section(act, section)
        if result is None:
            raise NotFound(
                f"Section {section} of {act} is not in the nyaya corpus. "
                "The corpus is a frozen snapshot; some sections may be missing. "
                "Try search_law to find related provisions.",
                kind="section",
                hint="Call search_law with a topical query, or list_chapters to see the act's structure.",
            )
        return result