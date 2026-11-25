"""Tests for the atomic blue/green publisher (`publish.py`) and `nyaya-ingest` CLI (`cli.py`)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nyaya.ingest.builders.lance_fts import build_lance_fts_tables
from nyaya.ingest.builders.parquet_compact import build_compacted_parquets
from nyaya.ingest.builders.sqlite_core import build_sqlite_core, create_mini_ingest_input
from nyaya.ingest.cli import main as ingest_cli_main
from nyaya.ingest.gate import run_quality_gate
from nyaya.ingest.publish import (
    InMemoryUploadClient,
    PublishGateError,
    publish_release,
)


def _build_gated_dir(tmp_path: Path) -> Path:
    bundle = create_mini_ingest_input()
    build_sqlite_core(tmp_path / "nyaya-core.sqlite", input_data=bundle)
    build_lance_fts_tables(tmp_path / "lancedb", input_data=bundle)
    build_compacted_parquets(tmp_path, input_data=bundle)
    report = run_quality_gate(tmp_path)
    assert report.passed
    return tmp_path


def test_publish_refuses_when_gate_pass_missing(tmp_path: Path) -> None:
    bundle = create_mini_ingest_input()
    build_sqlite_core(tmp_path / "nyaya-core.sqlite", input_data=bundle)
    build_compacted_parquets(tmp_path, input_data=bundle)
    # No run_quality_gate() called -> gate-pass.json is absent

    bucket_client = InMemoryUploadClient()
    with pytest.raises(PublishGateError, match="gate-pass.json"):
        publish_release(
            tmp_path,
            tag="v2026.11.22",
            target="railway-bucket",
            bucket_client=bucket_client,
        )
    assert len(bucket_client.uploads) == 0


def test_publish_refuses_when_artifact_sha256_drifted_after_gate(tmp_path: Path) -> None:
    build_dir = _build_gated_dir(tmp_path)
    # Tamper with nyaya-core.sqlite AFTER gate-pass.json was signed
    with (build_dir / "nyaya-core.sqlite").open("ab") as f:
        f.write(b"tampered-bytes")

    bucket_client = InMemoryUploadClient()
    with pytest.raises(PublishGateError, match="SHA-256|drifted|mismatch"):
        publish_release(
            build_dir,
            tag="v2026.11.22",
            target="railway-bucket",
            bucket_client=bucket_client,
        )
    assert len(bucket_client.uploads) == 0


def test_publish_uploads_immutable_release_before_atomic_current_manifest_swap(
    tmp_path: Path,
) -> None:
    build_dir = _build_gated_dir(tmp_path)
    bucket_client = InMemoryUploadClient()
    public_client = InMemoryUploadClient()

    result = publish_release(
        build_dir,
        tag="v2026.11.22",
        target="all",
        bucket_client=bucket_client,
        public_client=public_client,
    )
    assert result.tag == "v2026.11.22"
    assert len(bucket_client.upload_order) >= 5
    assert len(public_client.upload_order) >= 5

    # Blue/green invariant: every immutable `releases/v2026.11.22/...` and `lancedb/...` key
    # is uploaded BEFORE `current/manifest.json` is atomically swapped at the very end!
    assert bucket_client.upload_order[-1] == "current/manifest.json"
    for key in bucket_client.upload_order[:-1]:
        assert key.startswith(("releases/v2026.11.22/", "lancedb/")), (
            f"Unexpected pre-swap key: {key}"
        )
    assert any(k.startswith("lancedb/") for k in bucket_client.upload_order)
    assert "releases/v2026.11.22/nyaya-core.sqlite.zst" in bucket_client.uploads
    assert "releases/v2026.11.22/nyaya-core.sqlite.zst.sha256" in bucket_client.uploads

    assert public_client.upload_order[-1] == "current/manifest.json"
    for key in public_client.upload_order[:-1]:
        assert key.startswith("releases/v2026.11.22/"), f"Unexpected public pre-swap key: {key}"

    # Verify manifest.json partitions point to releases/<tag>/... S3 keys and preserve NC metadata
    swapped_manifest = json.loads(public_client.uploads["current/manifest.json"].decode("utf-8"))
    ddl_meta = swapped_manifest["datasets"]["district_court"]
    assert ddl_meta["commercial_safe"] is False
    assert ddl_meta["license_spdx"] == "CC-BY-NC-SA-4.0"
    assert ddl_meta["terms_status"] == "non_commercial_only"
    assert ddl_meta["partitions"][0]["s3_key"].startswith("releases/v2026.11.22/parquet/")


def test_nyaya_ingest_cli_end_to_end_with_fixture(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    build_dir = tmp_path / "build"

    rc = ingest_cli_main(
        [
            "all",
            "--fixture",
            "--work-dir",
            str(work_dir),
            "--build-dir",
            str(build_dir),
            "--tag",
            "v2026.11.22",
            "--dry-run",
        ]
    )
    assert rc == 0
    assert (build_dir / "nyaya-core.sqlite").is_file()
    assert (build_dir / "manifest.json").is_file()
    assert (build_dir / "gate-pass.json").is_file()


def test_publish_rejects_removed_hf_dataset_target() -> None:
    with pytest.raises(SystemExit):
        ingest_cli_main(["publish", "--target", "hf-dataset"])
