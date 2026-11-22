"""Un-gated HTTPS fetcher for Vaquill Open India Law (`oss-data-in.vaquill.ai/v2026.08.1/`)."""

from __future__ import annotations

import json
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
        dry_run: bool = False,
        fixture_sha256sums: dict[str, str] | None = None,
    ) -> VaquillSyncResult:
        target_files = files or [
            "in_central_legislation.parquet",
            "in_state_legislation.parquet",
            "in_regulators.parquet",
            "in_tribunals.parquet",
        ]
        if fixture_sha256sums is not None:
            checksums = dict(fixture_sha256sums)
        else:
            sums_url = f"{self.base_url}/{self.release_tag}/SHA256SUMS.json"
            raw_bytes = self.http_fetcher.fetch_bytes(sums_url)
            loaded: Any = json.loads(raw_bytes.decode("utf-8"))
            checksums = {str(k): str(v) for k, v in loaded.items()} if isinstance(loaded, dict) else {}

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
