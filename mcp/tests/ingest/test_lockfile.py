"""Tests for sources.lock.yaml schema validation, license hygiene, and safe HTTP fetcher."""

from __future__ import annotations

import copy
import hashlib
from pathlib import Path
from typing import Any

import httpx
import pytest
import yaml
from pydantic import ValidationError

from nyaya.ingest.fetchers.http import SafeHttpFetcher, SecurityError
from nyaya.ingest.lockfile import (
    DEFAULT_LOCKFILE_PATH,
    Lockfile,
    SourceEntry,
    load_and_validate_lockfile,
    normalize_vaquill_terms_status,
)


def test_real_checked_in_sources_lockfile_is_valid() -> None:
    """The checked-in sources.lock.yaml must validate cleanly and cover all required sources."""
    assert DEFAULT_LOCKFILE_PATH.is_file(), f"Missing {DEFAULT_LOCKFILE_PATH}"
    lock = load_and_validate_lockfile(DEFAULT_LOCKFILE_PATH)
    assert isinstance(lock, Lockfile)
    assert lock.schema_version == 2
    assert len(lock.sources) >= 10

    by_id = {s.id: s for s in lock.sources}
    # OpenJustice / Dattam S3 buckets
    assert "dattam_sc_judgments" in by_id
    assert by_id["dattam_sc_judgments"].uri == "s3://indian-supreme-court-judgments"
    assert by_id["dattam_sc_judgments"].commercial_safe is True
    assert by_id["dattam_sc_judgments"].terms_status == "cleared"

    assert "dattam_hc_judgments" in by_id
    assert by_id["dattam_hc_judgments"].uri == "s3://indian-high-court-judgments"
    assert by_id["dattam_hc_judgments"].commercial_safe is True

    # Vaquill Open India Law v2026.08.1 (37 legislation packs, 12 regulators, 15 tribunals)
    assert "vaquill_open_india_law" in by_id
    vaquill = by_id["vaquill_open_india_law"]
    assert vaquill.release_tag == "v2026.08.1"
    assert vaquill.pack_counts.get("legislation_packs") == 37
    assert vaquill.pack_counts.get("regulators") == 12
    assert vaquill.pack_counts.get("tribunals") == 15
    assert vaquill.commercial_safe is True

    # Non-commercial research packs MUST have commercial_safe=False and non_commercial_only
    assert "ddl_81m" in by_id
    ddl = by_id["ddl_81m"]
    assert ddl.license_spdx == "CC-BY-NC-SA-4.0"
    assert ddl.commercial_safe is False
    assert ddl.terms_status == "non_commercial_only"
    assert "predicted_religion" in ddl.drop_columns
    assert "predicted_caste" in ddl.drop_columns

    assert "ildc" in by_id
    ildc = by_id["ildc"]
    assert ildc.license_spdx == "CC-BY-NC-4.0"
    assert ildc.commercial_safe is False
    assert ildc.terms_status == "non_commercial_only"


def test_vaquill_terms_status_normalization_and_red_rejection() -> None:
    """Vaquill green/amber/unknown map to canonical TermsStatus; red is hard-rejected."""
    assert normalize_vaquill_terms_status("green") == "cleared"
    assert normalize_vaquill_terms_status("amber") == "attribution_required"
    assert normalize_vaquill_terms_status("unknown") == "upstream_unknown"
    assert normalize_vaquill_terms_status("green", license_spdx="CC-BY-NC-4.0") == "non_commercial_only"

    with pytest.raises(ValueError, match="red"):
        normalize_vaquill_terms_status("red")


def _load_raw_lockfile_dict() -> dict[str, Any]:
    return yaml.safe_load(DEFAULT_LOCKFILE_PATH.read_text(encoding="utf-8"))


def test_lockfile_rejects_terms_status_red(tmp_path: Path) -> None:
    raw = _load_raw_lockfile_dict()
    raw["sources"][0]["terms_status"] = "red"
    bad_path = tmp_path / "bad_red.yaml"
    bad_path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises((ValidationError, ValueError), match="red"):
        load_and_validate_lockfile(bad_path)


@pytest.mark.parametrize(
    "bad_license",
    ["GPL-3.0-only", "AGPL-3.0-or-later", "LGPL-2.1", "UNKNOWN", "", "CC-BY-ND-4.0"],
)
def test_lockfile_rejects_copyleft_and_unknown_licenses(
    tmp_path: Path, bad_license: str
) -> None:
    raw = _load_raw_lockfile_dict()
    raw["sources"][0]["license_spdx"] = bad_license
    bad_path = tmp_path / "bad_license.yaml"
    bad_path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises((ValidationError, ValueError)):
        load_and_validate_lockfile(bad_path)


def test_lockfile_rejects_missing_copyright_basis(tmp_path: Path) -> None:
    raw = _load_raw_lockfile_dict()
    raw["sources"][0]["copyright_basis"] = "   "
    bad_path = tmp_path / "bad_basis.yaml"
    bad_path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises((ValidationError, ValueError), match="copyright_basis"):
        load_and_validate_lockfile(bad_path)


@pytest.mark.parametrize(
    "bad_uri",
    [
        "https://indiankanoon.org/doc/12345/",
        "https://evil.example.com/data.parquet",
        "https://github.com/indiavotes/sansadsaar-17",
        "https://github.com/civictech-India/Indian-Law-Penal-Code-Json",
        "https://github.com/mratanusarkar/Indian-Laws",
    ],
)
def test_lockfile_rejects_banned_and_non_allowlisted_domains(
    tmp_path: Path, bad_uri: str
) -> None:
    raw = _load_raw_lockfile_dict()
    raw["sources"][0]["uri"] = bad_uri
    bad_path = tmp_path / "bad_domain.yaml"
    bad_path.write_text(yaml.safe_dump(raw), encoding="utf-8")

    with pytest.raises((ValidationError, ValueError)):
        load_and_validate_lockfile(bad_path)


def test_lockfile_rejects_nc_license_marked_commercial_safe() -> None:
    raw = _load_raw_lockfile_dict()
    entry_dict = copy.deepcopy(raw["sources"][0])
    entry_dict["license_spdx"] = "CC-BY-NC-4.0"
    entry_dict["commercial_safe"] = True
    entry_dict["terms_status"] = "non_commercial_only"

    with pytest.raises((ValidationError, ValueError), match="commercial_safe"):
        SourceEntry.model_validate(entry_dict)


def test_lockfile_computes_commercial_safe_when_omitted() -> None:
    raw = _load_raw_lockfile_dict()
    entry_nc = copy.deepcopy(raw["sources"][0])
    entry_nc.pop("commercial_safe", None)
    entry_nc["license_spdx"] = "CC-BY-NC-SA-4.0"
    entry_nc["terms_status"] = "non_commercial_only"
    parsed_nc = SourceEntry.model_validate(entry_nc)
    assert parsed_nc.commercial_safe is False

    entry_cs = copy.deepcopy(raw["sources"][0])
    entry_cs.pop("commercial_safe", None)
    entry_cs["license_spdx"] = "CC-BY-4.0"
    entry_cs["terms_status"] = "cleared"
    parsed_cs = SourceEntry.model_validate(entry_cs)
    assert parsed_cs.commercial_safe is True


def test_safe_http_fetcher_blocks_cross_domain_redirect_and_verifies_sha256(
    tmp_path: Path,
) -> None:
    payload = b"verified legal parquet bytes"
    good_sha = hashlib.sha256(payload).hexdigest()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/redirect-cross-domain":
            return httpx.Response(
                302,
                headers={"Location": "https://indiankanoon.org/scrape.parquet"},
            )
        if request.url.path == "/valid.parquet":
            return httpx.Response(200, content=payload)
        return httpx.Response(404)

    transport = httpx.MockTransport(handler)
    fetcher = SafeHttpFetcher(
        allowed_domains=["oss-data-in.vaquill.ai"],
        transport=transport,
    )

    # 1. Cross-domain redirect raises SecurityError
    with pytest.raises(SecurityError, match="Cross-domain|Disallowed"):
        fetcher.download_to_file(
            "https://oss-data-in.vaquill.ai/redirect-cross-domain",
            tmp_path / "out1.parquet",
        )

    # 2. Disallowed initial domain raises SecurityError
    with pytest.raises(SecurityError, match="Disallowed"):
        fetcher.download_to_file(
            "https://indiankanoon.org/doc/1",
            tmp_path / "out2.parquet",
        )

    # 3. SHA-256 mismatch aborts download and removes partial file
    dest_bad = tmp_path / "bad_sha.parquet"
    with pytest.raises(SecurityError, match="SHA-256"):
        fetcher.download_to_file(
            "https://oss-data-in.vaquill.ai/valid.parquet",
            dest_bad,
            expected_sha256="0" * 64,
        )
    assert not dest_bad.exists()

    # 4. Matching SHA-256 succeeds
    dest_ok = tmp_path / "ok.parquet"
    digest = fetcher.download_to_file(
        "https://oss-data-in.vaquill.ai/valid.parquet",
        dest_ok,
        expected_sha256=good_sha,
    )
    assert digest == good_sha
    assert dest_ok.read_bytes() == payload
