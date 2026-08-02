"""cross_reference: look up references to/from a given section (bidirectional)."""

from __future__ import annotations

from .. import db
from ..models import CrossRefList
from ._util import run_sync


def register(mcp) -> None:
    @mcp.tool(
        name="cross_reference",
        description=(
            "Given a section or article, return other provisions it references AND that "
            "reference it (bidirectional). Covers: (1) IPC ↔ BNS correspondence (the 2023 "
            "Bharatiya Nyaya Sanhita replaced the IPC), (2) cross-act references parsed from "
            "section text (e.g. CPC s.151 references Evidence Act s.65), (3) repealed-by "
            "relationships. Act names and section numbers are normalized. Use this when the "
            "user asks 'what replaced IPC 302?' or 'what references Article 21?'. The "
            "``direction`` field on the response tells you whether refs are outgoing, "
            "incoming, or both. Use ``direction='from'`` for outgoing-only or "
            "``direction='to'`` for incoming-only."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "Cross-reference a section"},
    )
    @run_sync
    def cross_reference(act: str, section: str,
                        direction: str = "both") -> CrossRefList:
        """Find cross-references for a section (bidirectional by default).

        Args:
            act: Act short name or alias, e.g. 'IPC', 'Constitution'.
            section: Section or article number, e.g. '302', '21'.
            direction: 'both' (default), 'from' (outgoing only), or 'to' (incoming only).
                Invalid values raise a validation error.
        """
        if direction not in ("both", "from", "to"):
            from ..exceptions import SearchError
            raise SearchError(
                f"direction must be 'both', 'from', or 'to', got {direction!r}.",
            )
        refs = db.get_cross_refs(act, section, direction=direction)
        return CrossRefList(
            from_act=act, from_section=section, references=refs, direction=direction,
        )