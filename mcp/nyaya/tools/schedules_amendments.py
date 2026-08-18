"""list_schedules / list_amendments / get_amendment / get_schedule: Constitution structural tools."""

from __future__ import annotations

from .. import db
from ..exceptions import NotFound
from ..models import Document
from ._util import run_sync


def register(mcp) -> None:
    @mcp.tool(
        name="list_schedules",
        description=(
            "List all 12 Schedules of the Constitution of India (First to Twelfth) with "
            "their titles and text. Schedules contain tabular/form provisions (States, "
            "Languages, Anti-defection, etc.) that are awkward to search by keyword."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "List Constitution schedules"},
    )
    @run_sync
    def list_schedules() -> list[Document]:
        """List all Constitution schedules."""
        return db.list_schedules()

    @mcp.tool(
        name="get_schedule",
        description=(
            "Fetch a specific Constitution Schedule by number (1–12). Returns the full "
            "schedule text with provenance."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "Get a Constitution schedule"},
    )
    @run_sync
    def get_schedule(number: int) -> Document:
        """Get a schedule by number.

        Args:
            number: Schedule number (1–12).
        """
        result = db.get_schedule(number)
        if result is None:
            raise NotFound(
                f"Schedule {number} is not in the corpus. The Constitution has 12 Schedules.",
                kind="schedule",
                hint="Call list_schedules to see all available schedules.",
            )
        return result

    @mcp.tool(
        name="list_amendments",
        description=(
            "List Constitutional amendments, optionally filtered by year range. The "
            "Constitution has been amended 100+ times; each amendment has a number, year, "
            "title, and the articles it affected."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "List Constitutional amendments"},
    )
    @run_sync
    def list_amendments(year_from: int | None = None,
                        year_to: int | None = None) -> list[Document]:
        """List Constitutional amendments.

        Args:
            year_from: Optional minimum year (inclusive).
            year_to: Optional maximum year (inclusive).
        """
        return db.list_amendments(year_from=year_from, year_to=year_to)

    @mcp.tool(
        name="get_amendment",
        description=(
            "Fetch a specific Constitutional amendment by number (e.g. 42, 44, 101). "
            "Returns the amendment title, year, and the articles it affected."
        ),
        annotations={"readOnlyHint": True, "openWorldHint": False, "title": "Get a Constitutional amendment"},
    )
    @run_sync
    def get_amendment(number: int) -> Document:
        """Get an amendment by number.

        Args:
            number: Amendment number (1–106+).
        """
        result = db.get_amendment(number)
        if result is None:
            raise NotFound(
                f"Amendment {number} is not in the corpus.",
                kind="amendment",
                hint="Call list_amendments to see all available amendments.",
            )
        return result
