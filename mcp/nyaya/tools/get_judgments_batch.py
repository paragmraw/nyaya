"""get_judgments_batch: fetch up to 20 judgments with publication-ban and OWASP framing enforcement."""

from __future__ import annotations

from typing import Any

from ..exceptions import AmbiguousReference, InvalidFilter, NotFound, NyayaError
from ..models import BatchJudgmentsResponse, JudgmentViewResponse
from ._error import structured_errors
from ._util import effective_commercial_safe
from .get_judgment import ViewMode, get_judgment

MAX_BATCH_JUDGMENTS = 20
_ALLOWED_VIEWS = frozenset({"overview", "paragraphs", "pages", "search_within", "docket"})


async def get_judgments_batch(
    identifiers: list[str],
    view: ViewMode = "overview",
    commercial_safe_only: bool = False,
) -> BatchJudgmentsResponse:
    """Fetch up to 20 judgments in one partial-success call, enforcing publication bans & framing."""
    if not identifiers:
        raise InvalidFilter(
            "identifiers must contain at least 1 judgment identifier.",
            kind="batch",
            hint="Pass up to 20 CNRs, neutral citations, AIR/SCC/SCR citations, or case IDs.",
        )
    if len(identifiers) > MAX_BATCH_JUDGMENTS:
        raise InvalidFilter(
            f"Batch size {len(identifiers)} exceeds maximum of {MAX_BATCH_JUDGMENTS} judgments.",
            kind="batch",
            hint=f"Split the request into batches of at most {MAX_BATCH_JUDGMENTS} identifiers.",
        )
    if view not in _ALLOWED_VIEWS:
        raise InvalidFilter(
            f"Unsupported view {view!r}.",
            kind="view",
            allowed_values=sorted(_ALLOWED_VIEWS),
        )

    cs_only = effective_commercial_safe(commercial_safe_only)
    results: list[JudgmentViewResponse] = []
    not_found: list[str] = []
    ambiguous: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []

    for raw_ident in identifiers:
        ident = str(raw_ident or "").strip()
        if not ident:
            errors.append(
                {
                    "identifier": raw_ident,
                    "code": "invalid_filter",
                    "message": "Blank judgment identifier.",
                }
            )
            continue
        try:
            res = await get_judgment(
                identifier=ident,
                view=view,
                commercial_safe_only=cs_only,
            )
            results.append(res)
        except NotFound:
            not_found.append(ident)
        except AmbiguousReference as exc:
            ambiguous.append(
                {
                    "identifier": ident,
                    "message": exc.message,
                    "candidates": exc.candidates,
                }
            )
        except NyayaError as exc:
            err_dict = exc.to_dict()
            err_dict["identifier"] = ident
            errors.append(err_dict)

    return BatchJudgmentsResponse(
        total_requested=len(identifiers),
        returned=len(results),
        view=view,
        results=results,
        not_found=not_found,
        ambiguous=ambiguous,
        errors=errors,
    )


def register(mcp: Any) -> None:
    @mcp.tool(
        name="get_judgments_batch",
        description=(
            "Fetch up to 20 judgments (overview or docket views) in a single partial-success call. "
            "Enforces statutory/judicial publication bans (check_publication_ban) and OWASP "
            "<corpus_text> framing on every excerpt and snippet."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
            "title": "Batch fetch judgments",
        },
    )
    @structured_errors
    async def _tool(
        identifiers: list[str],
        view: ViewMode = "overview",
        commercial_safe_only: bool = False,
    ) -> BatchJudgmentsResponse:
        return await get_judgments_batch(
            identifiers=identifiers,
            view=view,
            commercial_safe_only=commercial_safe_only,
        )
