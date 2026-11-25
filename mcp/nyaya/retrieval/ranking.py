"""Weighted Reciprocal Rank Fusion (RRF, k=60), court authority prior, and top-3 floor."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Sequence
from typing import TypeVar

from ..citations import canonicalize_court
from ..models import AuthorityLevel, CourtTier, JudgmentCard, StatuteHit

T = TypeVar("T")

_DEF_TITLE_RE = re.compile(r"\b(?:defin|interpret)", re.IGNORECASE)

_AUTHORITY_WEIGHTS: dict[AuthorityLevel, int] = {
    "binding_sc": 5,
    "binding_home_hc": 4,
    "persuasive_other_hc": 3,
    "tribunal": 2,
    "district": 1,
    "unspecified": 2,
}


def reciprocal_rank_fusion(
    ranked_lists: Sequence[tuple[float, Sequence[T]]],
    key_fn: Callable[[T], str],
    *,
    k: int = 60,
) -> list[tuple[T, float]]:
    """Fuse multiple ranked lists with weighted RRF (``weight / (k + rank)``)."""
    scores: dict[str, float] = {}
    items: dict[str, T] = {}
    for weight, seq in ranked_lists:
        for rank_idx, item in enumerate(seq, start=1):
            key = key_fn(item)
            if key not in items:
                items[key] = item
            scores[key] = scores.get(key, 0.0) + (weight / (k + rank_idx))
    ordered_keys = sorted(scores.keys(), key=lambda key: scores[key], reverse=True)
    return [(items[key], round(scores[key], 6)) for key in ordered_keys]


def apply_definition_promotion(hits: Sequence[StatuteHit]) -> list[StatuteHit]:
    """Boost statute hits whose title or ref defines terms (``promote_definitions=True``)."""
    defs: list[StatuteHit] = []
    rest: list[StatuteHit] = []
    for h in hits:
        if h.title and _DEF_TITLE_RE.search(h.title):
            defs.append(h.model_copy(update={"bm25_score": round(h.bm25_score + 10.0, 4)}))
        else:
            rest.append(h)
    return defs + rest


def apply_exact_citation_floor(
    fused: Sequence[T],
    exact_hits: Sequence[T],
    key_fn: Callable[[T], str],
    *,
    floor: int = 3,
) -> list[T]:
    """Ensure exact citation/provision hits never drop below the top ``floor`` slots."""
    if not exact_hits:
        return list(fused)
    exact_keys = {key_fn(x) for x in exact_hits}
    non_exact = [item for item in fused if key_fn(item) not in exact_keys]

    # Place exact hits in slots 0..min(floor, len(exact_hits))
    pinned = list(exact_hits[:floor])
    pinned_keys = {key_fn(x) for x in pinned}
    remaining_exact = [x for x in exact_hits[floor:] if key_fn(x) not in pinned_keys]
    return pinned + remaining_exact + non_exact


def compute_authority_level(
    court_tier: CourtTier,
    court_code: str | None,
    court_name: str | None,
    binding_for_court: str | None,
) -> AuthorityLevel:
    """Classify precedent authority relative to the practitioner's target forum."""
    if court_tier == "supreme_court":
        return "binding_sc"
    if court_tier == "tribunal":
        return "tribunal"
    if court_tier == "district_court":
        return "district"
    if court_tier == "high_court":
        if not binding_for_court:
            return "persuasive_other_hc"
        target_canon = canonicalize_court(binding_for_court)
        card_canon = canonicalize_court(court_code) or canonicalize_court(court_name)
        if target_canon and card_canon and target_canon == card_canon:
            return "binding_home_hc"
        return "persuasive_other_hc"
    return "unspecified"


def rank_judgment_cards(
    cards: Sequence[JudgmentCard],
    rrf_scores: dict[str, float],
    *,
    binding_for_court: str | None = None,
    sort_by: str = "authority_relevance",
) -> list[JudgmentCard]:
    """Stamp ``authority_level`` and sort judgment cards by requested strategy."""
    stamped: list[JudgmentCard] = [
        c.model_copy(
            update={
                "authority_level": compute_authority_level(
                    c.court_tier, c.court_code, c.court, binding_for_court
                )
            }
        )
        for c in cards
    ]

    if sort_by == "date_desc":
        stamped.sort(key=lambda c: (c.decision_date or "", rrf_scores.get(c.case_id, 0.0)), reverse=True)
    elif sort_by == "date_asc":
        stamped.sort(key=lambda c: (str(c.decision_date or "9999-99-99"), -rrf_scores.get(c.case_id, 0.0)))
    elif sort_by == "bench_strength_desc":
        stamped.sort(
            key=lambda c: (c.bench_strength or 0, rrf_scores.get(c.case_id, 0.0)),
            reverse=True,
        )
    elif sort_by == "most_cited":
        stamped.sort(
            key=lambda c: (c.cited_by_count or 0, rrf_scores.get(c.case_id, 0.0)),
            reverse=True,
        )
    elif sort_by == "relevance":
        stamped.sort(key=lambda c: rrf_scores.get(c.case_id, 0.0), reverse=True)
    else:
        # Default: authority_relevance
        def _eff(c: JudgmentCard) -> float:
            return rrf_scores.get(c.case_id, 0.0) * (
                1.0
                + 0.18 * math.log1p(max(0, c.cited_by_count or 0))
                + 0.04 * min(max(0, c.bench_strength or 0), 13)
            )

        stamped.sort(
            key=lambda c: (
                _AUTHORITY_WEIGHTS.get(c.authority_level, 2),
                1 if c.matched_via == "direct" else 0,
                round(_eff(c), 6),
                c.bench_strength or 0,
                c.cited_by_count or 0,
            ),
            reverse=True,
        )
    return stamped
