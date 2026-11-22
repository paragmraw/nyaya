"""Fetchers and sanitizers for academic/research packs:
- DDL 81M District Courts (`CC-BY-NC-SA-4.0`, `commercial_safe=False`, strips ML religion/caste columns)
- ILDC Supreme Court Research Pack (`CC-BY-NC-4.0`, `commercial_safe=False`)
- Justice Hub KHOJ High Court Judges (`CC-BY-4.0`, `commercial_safe=True`)
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

DDL_BANNED_DEMOGRAPHIC_COLUMNS = frozenset(
    {
        "predicted_religion",
        "predicted_caste",
        "judge_predicted_religion",
        "judge_predicted_caste",
        "defendant_religion",
        "petitioner_religion",
        "defendant_caste",
        "petitioner_caste",
    }
)


def sanitize_ddl_district_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    extra_drop_columns: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Strip ML-inferred religion/caste columns from DDL District Court rows and force
    ``commercial_safe=False``, ``license_spdx="CC-BY-NC-SA-4.0"``, ``terms_status="non_commercial_only"``.
    """
    drop_set = set(DDL_BANNED_DEMOGRAPHIC_COLUMNS)
    if extra_drop_columns:
        drop_set.update(c.strip() for c in extra_drop_columns if c.strip())

    cleaned: list[dict[str, Any]] = []
    for row in rows:
        item = {k: v for k, v in row.items() if k not in drop_set}
        item["commercial_safe"] = False
        item["license_spdx"] = "CC-BY-NC-SA-4.0"
        item["terms_status"] = "non_commercial_only"
        item["source_id"] = "ddl_81m"
        cleaned.append(item)
    return cleaned


def tag_ildc_research_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Ensure ILDC research rows carry mandatory non-commercial provenance metadata."""
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["commercial_safe"] = False
        item["license_spdx"] = "CC-BY-NC-4.0"
        item["terms_status"] = "non_commercial_only"
        item["source_id"] = str(item.get("source_id") or "ildc")
        out.append(item)
    return out
