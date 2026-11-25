"""Un-gated HTTPS fetcher for Vaquill Open India Law (`oss-data-in.vaquill.ai/v2026.08.1/`)."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from ...models import TermsStatus
from ..lockfile import normalize_vaquill_terms_status
from ..state import IngestState
from .http import SafeHttpFetcher


@dataclass
class VaquillCoverageEntry:
    """Validated entry from Vaquill ``coverage.yml``."""

    dataset_id: str
    license_spdx: str
    terms_status: TermsStatus
    commercial_safe: bool


@dataclass
class VaquillSyncResult:
    """Result summary of syncing Vaquill Open India Law packs."""

    release_tag: str
    downloaded_files: list[str] = field(default_factory=list)
    skipped_unchanged: list[str] = field(default_factory=list)
    coverage: dict[str, VaquillCoverageEntry] = field(default_factory=dict)


def parse_vaquill_coverage_yaml(yaml_text: str) -> dict[str, VaquillCoverageEntry]:
    """Parse Vaquill ``coverage.yml`` and map ``green/amber/unknown`` to canonical ``TermsStatus``.

    Hard-rejects any entry with ``terms_status: red``.
    """
    raw = yaml.safe_load(yaml_text)
    if not isinstance(raw, dict):
        raise ValueError("coverage.yml must be a YAML mapping.")
    raw_datasets = raw.get("datasets")
    entries: list[Any] = raw_datasets if isinstance(raw_datasets, list) else []
    out: dict[str, VaquillCoverageEntry] = {}
    for item in entries:
        if not isinstance(item, dict):
            continue
        ds_id = str(item.get("id") or "").strip()
        lic = str(item.get("license_spdx") or "CC-BY-4.0").strip()
        raw_status = str(item.get("terms_status") or "green").strip()
        canon_status = normalize_vaquill_terms_status(raw_status, license_spdx=lic)
        comm_safe = canon_status != "non_commercial_only" and "NC" not in lic.upper()
        if ds_id:
            out[ds_id] = VaquillCoverageEntry(
                dataset_id=ds_id,
                license_spdx=lic,
                terms_status=canon_status,
                commercial_safe=comm_safe,
            )
    return out


_LEGACY_CATEGORY_MAP: dict[str, str] = {
    "in_central_legislation.parquet": "central",
    "in_state_legislation.parquet": "state",
    "in_regulators.parquet": "regulator",
    "in_tribunals.parquet": "tribunal",
}


def parse_sha256sums_json(payload: Mapping[str, Any]) -> dict[str, str]:
    """Parse Vaquill ``SHA256SUMS.json`` in either nested ``{"files": {name: {"sha256": ...}}}``
    or flat ``{name: sha256}`` format.
    """
    raw_files: Mapping[str, Any]
    nested = payload.get("files")
    if isinstance(nested, Mapping):
        raw_files = nested
    else:
        raw_files = payload

    out: dict[str, str] = {}
    for fname, entry in raw_files.items():
        key = str(fname).strip()
        if not key:
            continue
        if isinstance(entry, Mapping):
            sha = str(entry.get("sha256") or "").strip()
        else:
            sha = str(entry or "").strip()
        if sha:
            out[key] = sha
    return out


def classify_vaquill_file(filename: str) -> str:
    """Classify a Vaquill v2026.08.1 Parquet filename into central/state/regulator/tribunal."""
    name = filename.strip()
    if name in _LEGACY_CATEGORY_MAP:
        return _LEGACY_CATEGORY_MAP[name]
    if name.endswith("_legislation.parquet"):
        return "state"
    if name.endswith("_regulations.parquet"):
        return "regulator"
    if name.endswith("_tribunal_matters.parquet"):
        return "tribunal"
    return "other"


def list_files_by_category(
    checksums: Mapping[str, str] | Sequence[str],
) -> dict[str, list[str]]:
    """Group Vaquill Parquet filenames into ``central``, ``state``, ``regulator``, ``tribunal``."""
    grouped: dict[str, list[str]] = {
        "central": [],
        "state": [],
        "regulator": [],
        "tribunal": [],
    }
    names = list(checksums.keys()) if isinstance(checksums, Mapping) else list(checksums)
    for fname in sorted(names):
        cat = classify_vaquill_file(fname)
        if cat in grouped:
            grouped[cat].append(fname)
    return grouped


class VaquillFetcher:
    """Downloads Vaquill Open India Law Parquets and verifies ``SHA256SUMS.json``."""

    def __init__(
        self,
        *,
        state: IngestState,
        http_fetcher: SafeHttpFetcher | None = None,
        release_tag: str = "v2026.08.1",
        base_url: str = "https://oss-data-in.vaquill.ai",
    ) -> None:
        self.state = state
        self.release_tag = release_tag
        self.base_url = base_url.rstrip("/")
        self.http_fetcher = http_fetcher or SafeHttpFetcher(
            allowed_domains=["oss-data-in.vaquill.ai"]
        )

    def sync_packs(
        self,
        work_dir: Path,
        *,
        files: list[str] | None = None,
        categories: Sequence[str] | None = None,
        dry_run: bool = False,
        fixture_sha256sums: Mapping[str, Any] | None = None,
    ) -> VaquillSyncResult:
        if fixture_sha256sums is not None:
            checksums = parse_sha256sums_json(fixture_sha256sums)
        else:
            sums_url = f"{self.base_url}/{self.release_tag}/SHA256SUMS.json"
            raw_bytes = self.http_fetcher.fetch_bytes(sums_url)
            loaded: Any = json.loads(raw_bytes.decode("utf-8"))
            checksums = parse_sha256sums_json(loaded) if isinstance(loaded, Mapping) else {}

        if files is not None:
            target_files = list(files)
        elif categories is not None:
            wanted = {c.strip().lower() for c in categories if c.strip()}
            by_cat = list_files_by_category(checksums)
            target_files = [
                fname
                for cat in ("central", "state", "regulator", "tribunal")
                if cat in wanted
                for fname in by_cat[cat]
            ]
        elif fixture_sha256sums is not None:
            target_files = [
                "in_central_legislation.parquet",
                "in_state_legislation.parquet",
                "in_regulators.parquet",
                "in_tribunals.parquet",
            ]
        else:
            by_cat = list_files_by_category(checksums)
            target_files = [
                *by_cat["central"],
                *by_cat["state"],
                *by_cat["regulator"],
                *by_cat["tribunal"],
            ]

        result = VaquillSyncResult(release_tag=self.release_tag)
        dest_dir = work_dir / "raw" / "vaquill" / self.release_tag

        for fname in target_files:
            expected_sha = checksums.get(fname, "")
            if not self.state.has_artifact_changed(
                "vaquill_open_india_law",
                fname,
                sha256=expected_sha,
                release_tag=self.release_tag,
            ):
                result.skipped_unchanged.append(fname)
                continue

            if not dry_run:
                if fixture_sha256sums is not None:
                    dest_dir.mkdir(parents=True, exist_ok=True)
                    (dest_dir / fname).write_bytes(b"fixture-parquet")
                else:
                    file_url = f"{self.base_url}/{self.release_tag}/{fname}"
                    self.http_fetcher.download_to_file(
                        file_url,
                        dest_dir / fname,
                        expected_sha256=expected_sha or None,
                    )
                self.state.record_artifact_watermark(
                    "vaquill_open_india_law",
                    fname,
                    sha256=expected_sha,
                    release_tag=self.release_tag,
                )
            result.downloaded_files.append(fname)

        return result
