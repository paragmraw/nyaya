"""Parquet/corpus manifest loader, SHA-256 verifier, and partition pruner."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, Field

from ..citations import canonicalize_court, normalize_jurisdiction
from ..exceptions import CommercialLicenseBlocked, StorageUnavailable
from ..models import TermsStatus


class PartitionEntry(BaseModel):
    court_code: str | None = None
    court_name: str | None = None
    state_code: str | None = None
    forum_code: str | None = None
    regulator_code: str | None = None
    year_min: int | None = None
    year_max: int | None = None
    rows: int = 0
    s3_key: str
    public_url: str | None = None
    bytes: int = 0
    sha256: str = ""


class DatasetManifest(BaseModel):
    license_spdx: str
    commercial_safe: bool = True
    terms_status: TermsStatus = "cleared"
    row_count: int = 0
    partitions: list[PartitionEntry] = Field(default_factory=list)


class CorpusManifest(BaseModel):
    schema_version: str = "2.0"
    generated_at: str = ""
    datasets: dict[str, DatasetManifest] = Field(default_factory=dict)


def verify_partition_sha256(path: Path, expected_sha256: str) -> bool:
    """Verify SHA-256 checksum of a downloaded local partition file."""
    if not expected_sha256:
        return True
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest().lower() == expected_sha256.strip().lower()


def load_manifest(
    source: Path | str,
    *,
    offline_only: bool = False,
    timeout_s: float = 5.0,
) -> CorpusManifest:
    """Load and validate ``manifest.json`` from a local path or HTTPS URL."""
    if isinstance(source, Path) or not str(source).startswith(("http://", "https://")):
        path = Path(source)
        if not path.is_file():
            raise StorageUnavailable(
                f"Manifest file not found at {path}",
                degraded_capabilities=["parquet_cold"],
            )
        raw = path.read_text(encoding="utf-8")
        return CorpusManifest.model_validate(json.loads(raw))

    if offline_only:
        raise StorageUnavailable(
            "Remote manifest fetch blocked in offline_only mode",
            degraded_capabilities=["parquet_cold"],
        )
    try:
        resp = httpx.get(str(source), timeout=timeout_s, follow_redirects=False)
        resp.raise_for_status()
        data: Any = resp.json()
    except Exception as exc:
        raise StorageUnavailable(
            f"Failed to fetch manifest from {source}: {exc}",
            degraded_capabilities=["parquet_cold"],
        ) from exc
    return CorpusManifest.model_validate(data)


def _match_court(part_court: str | None, requested: str | list[str] | None) -> bool:
    if requested is None:
        return True
    if not part_court:
        return False
    req_list = [requested] if isinstance(requested, str) else list(requested)
    p_norm = part_court.strip().lower()
    p_canon = (canonicalize_court(part_court) or "").lower()
    for r in req_list:
        r_norm = r.strip().lower()
        r_canon = (canonicalize_court(r) or "").lower()
        if r_norm == p_norm or (r_canon and r_canon == p_canon):
            return True
    return False


def resolve_partitions(
    manifest: CorpusManifest,
    dataset: str,
    *,
    court: str | list[str] | None = None,
    state: str | None = None,
    forum: str | None = None,
    regulator: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    commercial_safe_only: bool = False,
    base_dir: Path | None = None,
    s3_bucket: str | None = None,
    offline_only: bool = False,
) -> list[str]:
    """Prune partitions in Python and return concrete local/S3/HTTPS URIs."""
    ds = manifest.datasets.get(dataset)
    if ds is None:
        return []
    if commercial_safe_only and not ds.commercial_safe:
        raise CommercialLicenseBlocked(
            f"Dataset {dataset!r} ({ds.license_spdx}) is non-commercial only and blocked when commercial_safe_only=True.",
            license_spdx=ds.license_spdx,
            hint="Disable commercial_safe_only for academic/non-commercial research.",
        )

    norm_state = normalize_jurisdiction(state)
    uris: list[str] = []
    for part in ds.partitions:
        if court is not None and not _match_court(part.court_code or part.court_name, court):
            continue
        if norm_state is not None and (part.state_code or "").lower() != norm_state.lower():
            continue
        if forum is not None and (part.forum_code or "").lower() != forum.strip().lower():
            continue
        if regulator is not None and (part.regulator_code or "").lower() != regulator.strip().lower():
            continue
        if year_from is not None and part.year_max is not None and part.year_max < year_from:
            continue
        if year_to is not None and part.year_min is not None and part.year_min > year_to:
            continue

        if base_dir is not None:
            local_path = base_dir / part.s3_key
            if local_path.is_file():
                uris.append(str(local_path))
                continue
        if s3_bucket and not offline_only:
            uris.append(f"s3://{s3_bucket}/{part.s3_key}")
        elif part.public_url and not offline_only:
            uris.append(part.public_url)

    return uris
