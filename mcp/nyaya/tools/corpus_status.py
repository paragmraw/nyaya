"""corpus_status: unified corpus coverage matrix, backend health, and licensing metadata."""

from __future__ import annotations

from typing import Any

from ..models import CorpusStatusResponse
from ..storage import enrich_corpus_status, get_backend
from ._error import structured_errors
from ._util import effective_commercial_safe, run_sync


def corpus_status(
    court: str | None = None,
    include_sources: bool = True,
    commercial_safe_only: bool = False,
) -> CorpusStatusResponse:
    """Return full corpus counts, active storage tiers, per-source provenance, and licensing posture."""
    backend = get_backend()
    cs_only = effective_commercial_safe(commercial_safe_only)
    resp = backend.corpus_status(
        court=court.strip() if court and court.strip() else None,
        include_sources=bool(include_sources),
        commercial_safe_only=cs_only,
    )
    return enrich_corpus_status(resp, commercial_safe_only=cs_only)


def register(mcp: Any) -> None:
    @mcp.tool(
        name="corpus_status",
        description=(
            "Return the Nyaya v2.1 corpus coverage matrix (37 Central+State/UT legislation packs, "
            "12 regulators, 24 tribunals & Consumer Commissions, Supreme Court, High Courts, DDL District Courts, concordance "
            "mappings, judicial invalidations, and citation edges), per-source SPDX license and "
            "commercial_safe metadata, and active storage tier health."
        ),
        annotations={
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
            "title": "Get corpus coverage, provenance & licensing status",
        },
    )
    @structured_errors
    @run_sync
    def _tool(
        court: str | None = None,
        include_sources: bool = True,
        commercial_safe_only: bool = False,
    ) -> CorpusStatusResponse:
        return corpus_status(
            court=court,
            include_sources=include_sources,
            commercial_safe_only=commercial_safe_only,
        )
