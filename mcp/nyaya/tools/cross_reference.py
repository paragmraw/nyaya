"""cross_reference: look up references to/from a given section."""

from __future__ import annotations

from .. import db
from ..models import CrossRefList


def register(mcp) -> None:
    @mcp.tool(
        name="cross_reference",
        description=(
            "Given a section or article, return other provisions it references or that "
            "reference it. Covers: (1) IPC ↔ BNS/BNSS/BSA correspondence (the 2023 Sanhitas "
            "replaced IPC/CrPC/Evidence), (2) cross-act references parsed from section text "
            "(e.g. CPC s.151 references Evidence Act s.65), (3) repealed-by relationships. "
            "Use this when the user asks 'what replaced IPC 302?' or 'what does Article 21 "
            "override?'."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "Cross-reference a section"},
    )
    def cross_reference(act: str, section: str) -> CrossRefList:
        """Find cross-references for a section.

        Args:
            act: Act short name, e.g. 'IPC', 'Constitution'.
            section: Section or article number, e.g. '302', '21'.
        """
        refs = db.get_cross_refs(act, section)
        return CrossRefList(from_act=act, from_section=section, references=refs)