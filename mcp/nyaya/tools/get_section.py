"""get_section: fetch a specific section of an act by number."""

from __future__ import annotations

from .. import db
from ..exceptions import NotFound
from ..models import Section


def register(mcp) -> None:
    @mcp.tool(
        name="get_section",
        description=(
            "Fetch the full text of a specific section of an Indian act by its number. "
            "Supports IPC, CrPC, CPC, Evidence Act, BNS, BNSS, BSA, Companies Act, GST acts, "
            "IT Act, Arbitration Act, Consumer Protection Act, etc. Use get_article instead "
            "for Constitution articles. If the section isn't in the corpus, returns an error "
            "with the corpus as-of date so the LLM can explain the limitation."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "Get a section by number"},
    )
    def get_section(act: str, section: str) -> Section:
        """Get a section by act and number.

        Args:
            act: Act short name, e.g. 'IPC', 'CrPC', 'BNS', 'Companies'.
            section: Section number as a string, e.g. '302', '354A', '154'.
        """
        result = db.get_section(act, section)
        if result is None:
            raise NotFound(
                f"Section {section} of {act} is not in the nyaya corpus. "
                "The corpus is a frozen snapshot; some sections may be missing. "
                "Try search_law to find related provisions."
            )
        return result