"""Point-in-time statutory reconstruction across commencement, amendments, repeal, and invalidation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date as Date
from typing import Any

from ..models import AmendmentRecord, InvalidatingJudgment, ProvisionRecord


@dataclass(frozen=True)
class PointInTimeResult:
    """Result of reconstructing a statutory provision as of a historical or target date."""

    text: str
    effective_status: str
    applied_amendments: list[dict[str, Any]]
    active_invalidation: InvalidatingJudgment | dict[str, Any] | None
    as_of: Date
    reconstruction_basis: str


def _parse_date(val: Any) -> Date | None:
    if val is None:
        return None
    if isinstance(val, Date):
        return val
    raw = str(val).strip()
    if not raw:
        return None
    return Date.fromisoformat(raw[:10])


def _get(obj: Any, key: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(key, default)
    return getattr(obj, key, default)


def _amendment_date(item: Any) -> Date | None:
    for field in ("effective_date", "valid_from", "enactment_date", "enacted_date"):
        dt = _parse_date(_get(item, field))
        if dt is not None:
            return dt
    year = _get(item, "year")
    if isinstance(year, int):
        return Date(year, 1, 1)
    return None


def _amendment_to_dict(item: Any) -> dict[str, Any]:
    if isinstance(item, AmendmentRecord):
        return {
            "amendment_id": item.amendment_id,
            "number": item.number,
            "title": item.title,
            "act": item.act,
            "year": item.year,
            "enactment_date": item.enactment_date.isoformat() if item.enactment_date else None,
            "effective_date": item.effective_date.isoformat() if item.effective_date else None,
            "action": item.action,
            "summary": item.summary,
        }
    if isinstance(item, Mapping):
        return dict(item)
    return {
        "amendment_id": _get(item, "amendment_id"),
        "number": _get(item, "number"),
        "title": _get(item, "title"),
        "effective_date": str(_get(item, "effective_date") or ""),
        "action": _get(item, "action", "amended"),
        "summary": _get(item, "summary", ""),
    }


def _extract_version_text(item: Any) -> str | None:
    for field in ("text", "new_text", "substituted_text", "resulting_text"):
        val = _get(item, field)
        if isinstance(val, str):
            return val
    return None


def _invalidation_date(item: Any) -> Date | None:
    return _parse_date(_get(item, "decision_date"))


def _invalidation_key(item: Any) -> tuple[str, str, str]:
    return (
        str(_get(item, "case_id") or ""),
        str(_get(item, "citation") or ""),
        str(_get(item, "decision_date") or ""),
    )


def reconstruct_provision_as_of(
    provision: ProvisionRecord | Mapping[str, Any],
    amendments: Sequence[AmendmentRecord | Mapping[str, Any]] | None,
    invalidations: Sequence[InvalidatingJudgment | Mapping[str, Any]] | None,
    as_of: Date | str,
) -> PointInTimeResult:
    """Reconstruct a provision's text and effective status as of ``as_of``."""
    target_date = _parse_date(as_of)
    if target_date is None:
        raise ValueError(f"Invalid as_of date {as_of!r}; expected YYYY-MM-DD or date.")

    validity = _get(provision, "validity")
    metadata = _get(provision, "metadata") or {}
    if not isinstance(metadata, Mapping):
        metadata = {}

    commencement_date = _parse_date(
        _get(validity, "commencement_date") if validity is not None else _get(provision, "commencement_date")
    )
    repeal_date = _parse_date(
        _get(validity, "repeal_date") if validity is not None else _get(provision, "repeal_date")
    )
    base_status = str(
        (_get(validity, "status") if validity is not None else _get(provision, "status")) or "in_force"
    )
    base_text = str(
        metadata.get("initial_text")
        or metadata.get("base_text")
        or _get(provision, "text")
        or ""
    )

    # 1. Check provision_versions snapshots (either in provision.metadata or provision attribute)
    raw_versions = metadata.get("provision_versions") or _get(provision, "provision_versions") or []
    applicable_versions: list[tuple[Date, int, Mapping[str, Any]]] = []
    for idx, ver in enumerate(raw_versions):
        if not isinstance(ver, Mapping):
            continue
        v_date = _amendment_date(ver)
        if v_date is not None and v_date <= target_date:
            applicable_versions.append((v_date, idx, ver))
    applicable_versions.sort(key=lambda t: (t[0], t[1]))

    # 2. Collect chronological amendments up to target_date
    dated_amends: list[tuple[Date, int, int, Any]] = []
    for idx, am in enumerate(amendments or ()):
        am_date = _amendment_date(am)
        if am_date is not None and am_date <= target_date:
            num = _get(am, "number")
            sort_num = int(num) if isinstance(num, int) else 0
            dated_amends.append((am_date, sort_num, idx, am))
    dated_amends.sort(key=lambda t: (t[0], t[1], t[2]))

    applied_amendments: list[dict[str, Any]] = []
    current_text = base_text
    amendment_status_override: str | None = None
    basis_parts: list[str] = []

    if applicable_versions:
        _, _, latest_ver = applicable_versions[-1]
        v_text = _extract_version_text(latest_ver)
        if v_text is not None:
            current_text = v_text
        basis_parts.append(f"provision_versions(effective={_amendment_date(latest_ver)})")

    for am_date, _, _, am in dated_amends:
        applied_amendments.append(_amendment_to_dict(am))
        action = str(_get(am, "action") or "amended").strip().lower()
        new_text = _extract_version_text(am)
        if new_text is not None and not applicable_versions:
            current_text = new_text
        if action in ("repealed", "omitted"):
            amendment_status_override = action
        elif action in ("inserted", "substituted"):
            amendment_status_override = None

    if applied_amendments:
        basis_parts.append(f"amendment_history({len(applied_amendments)} applied <= {target_date.isoformat()})")
    elif not basis_parts:
        basis_parts.append("base_provision_text")

    # 3. Collect judicial invalidations (explicit arg + embedded validity.invalidating_judgment)
    all_invals: list[Any] = list(invalidations or ())
    embedded_inval = _get(validity, "invalidating_judgment") if validity is not None else None
    if embedded_inval is not None:
        seen_keys = {_invalidation_key(iv) for iv in all_invals}
        if _invalidation_key(embedded_inval) not in seen_keys:
            all_invals.append(embedded_inval)

    applicable_invals: list[tuple[Date, int, Any]] = []
    for idx, iv in enumerate(all_invals):
        iv_date = _invalidation_date(iv)
        if iv_date is not None and iv_date <= target_date:
            applicable_invals.append((iv_date, idx, iv))
    applicable_invals.sort(key=lambda t: (t[0], t[1]))

    active_invalidation: InvalidatingJudgment | dict[str, Any] | None = None
    if applicable_invals:
        active_invalidation = applicable_invals[-1][2]

    # 4. Determine effective_status as of target_date
    if commencement_date is not None and target_date < commencement_date:
        effective_status = "not_yet_in_force"
        active_invalidation = None
        basis_parts.append(f"not_yet_commenced(commencement={commencement_date.isoformat()})")
    elif repeal_date is not None and repeal_date <= target_date:
        effective_status = "repealed"
        basis_parts.append(f"repealed(repeal_date={repeal_date.isoformat()})")
    elif amendment_status_override is not None:
        effective_status = amendment_status_override
    elif active_invalidation is not None:
        effective_status = str(_get(active_invalidation, "effect") or "struck_down")
        basis_parts.append(f"judicial_invalidation({effective_status})")
    else:
        # If current record status is repealed/invalidated due to a future event after target_date,
        # reconstruct historical status as in_force.
        future_repeal = repeal_date is not None and repeal_date > target_date
        future_inval = (
            embedded_inval is not None
            and (_invalidation_date(embedded_inval) is not None)
            and (_invalidation_date(embedded_inval) or target_date) > target_date
        )
        if base_status in (
            "repealed",
            "struck_down",
            "read_down",
            "partially_struck_down",
            "kept_in_abeyance",
        ) and (future_repeal or future_inval or bool(all_invals)):
            effective_status = "in_force"
        else:
            effective_status = base_status

    return PointInTimeResult(
        text=current_text,
        effective_status=effective_status,
        applied_amendments=applied_amendments,
        active_invalidation=active_invalidation,
        as_of=target_date,
        reconstruction_basis="; ".join(basis_parts),
    )
