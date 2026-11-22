"""Lockfile parser and validator for ``sources.lock.yaml`` with strict license & domain hygiene."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

import yaml  # type: ignore[import-untyped]
from pydantic import BaseModel, Field, model_validator

from ..models import TermsStatus

DATA_DIR = Path(__file__).resolve().parent / "data"
DEFAULT_LOCKFILE_PATH = DATA_DIR / "sources.lock.yaml"
DEFAULT_AMENDMENTS_PATH = DATA_DIR / "amendments_1_106.yaml"
DEFAULT_CONCORDANCE_PATH = DATA_DIR / "criminal_concordance.yaml"
DEFAULT_INVALIDATIONS_PATH = DATA_DIR / "judicial_invalidations.yaml"
DEFAULT_COURTS_PATH = DATA_DIR / "courts_directory.yaml"

AccessMethod = Literal[
    "https_parquet",
    "s3_anonymous",
    "https_archive",
    "https_json",
    "https_pdf",
    "git_release",
]

IngestTier = Literal["daily", "static"]

_ALLOWED_S3_BUCKETS = frozenset(
    {
        "indian-supreme-court-judgments",
        "indian-high-court-judgments",
    }
)

_BANNED_URI_SUBSTRINGS = (
    "indiankanoon.org",
    "indiavotes",
    "sansadsaar",
    "civictech-india",
    "mratanusarkar",
    "ecourts",
    "pymupdf",
    "surya-ocr",
)

_BANNED_LICENSE_PREFIXES = (
    "GPL",
    "AGPL",
    "LGPL",
    "CC-BY-ND",
    "CC-BY-NC-ND",
    "UNKNOWN",
    "PROPRIETARY",
)

_VALID_SPDX_LICENSES = frozenset(
    {
        "CC-BY-4.0",
        "CC0-1.0",
        "Apache-2.0",
        "MIT",
        "BSD-3-Clause",
        "IN-S52-1-q",
        "CC-BY-NC-4.0",
        "CC-BY-NC-SA-4.0",
    }
)


def is_non_commercial_license(license_spdx: str) -> bool:
    """Return True if an SPDX identifier restricts commercial use."""
    upper = license_spdx.strip().upper()
    return "NC" in upper.split("-")


def normalize_vaquill_terms_status(
    raw: str,
    *,
    license_spdx: str = "",
) -> TermsStatus:
    """Map Vaquill ``coverage.yml`` status or canonical string to ``TermsStatus``.

    Hard-rejects ``"red"`` or any unknown posture.
    """
    cleaned = raw.strip().lower()
    if cleaned == "red":
        raise ValueError(
            "Upstream source with terms_status='red' is strictly prohibited by Nyaya license gate."
        )
    if license_spdx and is_non_commercial_license(license_spdx):
        if cleaned in ("green", "amber", "cleared", "attribution_required", "non_commercial_only"):
            return "non_commercial_only"
    mapping: dict[str, TermsStatus] = {
        "green": "cleared",
        "cleared": "cleared",
        "amber": "attribution_required",
        "attribution_required": "attribution_required",
        "non_commercial_only": "non_commercial_only",
        "unknown": "upstream_unknown",
        "upstream_unknown": "upstream_unknown",
    }
    if cleaned not in mapping:
        raise ValueError(
            f"Invalid terms_status {raw!r}; must be one of {sorted(mapping.keys())} (never 'red')."
        )
    return mapping[cleaned]


class SourceEntry(BaseModel):
    """Single verified upstream legal data source entry in ``sources.lock.yaml``."""

    id: str
    name: str
    domain: str
    access_method: AccessMethod
    uri: str
    checksum_manifest: str | None = None
    release_tag: str | None = None
    sha256: str | None = None
    region: str | None = None
    etag_watermark_prefixes: list[str] = Field(default_factory=list)
    license_spdx: str
    commercial_safe: bool | None = None
    terms_status: TermsStatus
    copyright_basis: str
    attribution: str
    target_store: str
    tier: IngestTier = "static"
    update_cadence: str = "static"
    drop_columns: list[str] = Field(default_factory=list)
    pack_counts: dict[str, int] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _preprocess_terms_and_commercial_safe(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        out = dict(data)
        lic = str(out.get("license_spdx") or "").strip()
        raw_terms = str(out.get("terms_status") or "").strip()
        if raw_terms:
            # Normalize Vaquill green/amber/unknown or validate canonical TermsStatus;
            # raises ValueError on 'red'.
            out["terms_status"] = normalize_vaquill_terms_status(raw_terms, license_spdx="")
        is_nc = is_non_commercial_license(lic) or out.get("terms_status") == "non_commercial_only"
        if out.get("commercial_safe") is None:
            out["commercial_safe"] = not is_nc
        return out

    @model_validator(mode="after")
    def _validate_hygiene(self) -> SourceEntry:
        if not self.id.strip():
            raise ValueError("Source id must be non-empty.")
        lic = self.license_spdx.strip()
        if not lic:
            raise ValueError(f"Source {self.id!r} has empty license_spdx.")
        lic_upper = lic.upper()
        for banned_prefix in _BANNED_LICENSE_PREFIXES:
            if lic_upper.startswith(banned_prefix):
                raise ValueError(
                    f"Source {self.id!r} uses banned/copyleft license {self.license_spdx!r}."
                )
        if lic not in _VALID_SPDX_LICENSES:
            raise ValueError(
                f"Source {self.id!r} uses unapproved SPDX license {self.license_spdx!r}."
            )

        if not self.copyright_basis or not self.copyright_basis.strip():
            raise ValueError(
                f"Source {self.id!r} is missing mandatory copyright_basis (e.g. Copyright Act s.52(1)(q))."
            )
        if not self.attribution or not self.attribution.strip():
            raise ValueError(f"Source {self.id!r} is missing mandatory attribution.")

        uri_lower = self.uri.strip().lower()
        for banned in _BANNED_URI_SUBSTRINGS:
            if banned in uri_lower:
                raise ValueError(
                    f"Source {self.id!r} URI {self.uri!r} contains banned upstream {banned!r}."
                )

        is_nc = is_non_commercial_license(lic) or self.terms_status == "non_commercial_only"
        if is_nc and self.commercial_safe is True:
            raise ValueError(
                f"Source {self.id!r} has non-commercial license/terms ({lic} / {self.terms_status}) "
                "and cannot set commercial_safe=True."
            )
        if not is_nc and self.commercial_safe is None:
            self.commercial_safe = True
        return self


class Lockfile(BaseModel):
    """Top-level validated ``sources.lock.yaml`` model."""

    schema_version: int = 2
    updated_at: str
    allowed_domains: list[str]
    sources: list[SourceEntry]

    @model_validator(mode="after")
    def _validate_domains_and_uniqueness(self) -> Lockfile:
        if self.schema_version != 2:
            raise ValueError(f"Unsupported lockfile schema_version {self.schema_version}; expected 2.")
        allowed = {d.strip().lower() for d in self.allowed_domains if d.strip()}
        for d in allowed:
            for banned in _BANNED_URI_SUBSTRINGS:
                if banned in d:
                    raise ValueError(f"Banned domain {d!r} cannot appear in allowed_domains.")

        seen_ids: set[str] = set()
        for src in self.sources:
            if src.id in seen_ids:
                raise ValueError(f"Duplicate source id {src.id!r} in lockfile.")
            seen_ids.add(src.id)

            parsed = urlsplit(src.uri)
            if parsed.scheme == "s3":
                bucket = parsed.netloc.strip()
                if bucket not in _ALLOWED_S3_BUCKETS:
                    raise ValueError(
                        f"Source {src.id!r} references non-allowlisted S3 bucket {bucket!r}."
                    )
            elif parsed.scheme == "https":
                host = (parsed.hostname or "").lower()
                if host not in allowed:
                    raise ValueError(
                        f"Source {src.id!r} host {host!r} is not in lockfile allowed_domains."
                    )
            else:
                raise ValueError(
                    f"Source {src.id!r} URI {src.uri!r} must use 'https://' or 's3://' scheme."
                )
        return self

    def get_source(self, source_id: str) -> SourceEntry:
        for src in self.sources:
            if src.id == source_id:
                return src
        raise KeyError(f"Source {source_id!r} not found in lockfile.")

    def filter_sources(
        self,
        *,
        tier: IngestTier | None = None,
        source_ids: list[str] | None = None,
        commercial_safe_only: bool = False,
    ) -> list[SourceEntry]:
        out: list[SourceEntry] = []
        id_filter = {s.strip() for s in source_ids} if source_ids else None
        for src in self.sources:
            if tier is not None and src.tier != tier:
                continue
            if id_filter is not None and src.id not in id_filter and src.domain not in id_filter:
                continue
            if commercial_safe_only and not src.commercial_safe:
                continue
            out.append(src)
        return out


def load_and_validate_lockfile(path: Path | str = DEFAULT_LOCKFILE_PATH) -> Lockfile:
    """Load and validate ``sources.lock.yaml`` from disk."""
    lock_path = Path(path)
    if not lock_path.is_file():
        raise FileNotFoundError(f"Lockfile not found at {lock_path}")
    raw = yaml.safe_load(lock_path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Lockfile at {lock_path} must be a YAML mapping.")
    return Lockfile.model_validate(raw)
