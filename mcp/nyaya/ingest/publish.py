"""Atomic blue/green publisher (`publish.py`) for Railway Storage Bucket (Tigris S3)
and public HuggingFace Datasets / GitHub Releases mirrors.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from ..storage.manifest import CorpusManifest, load_manifest
from .gate import collect_artifact_sha256s

PublishTarget = Literal["railway-bucket", "hf-dataset", "gh-release", "all"]


class PublishGateError(RuntimeError):
    """Raised when `publish` is invoked without a valid, un-drifted `gate-pass.json`."""


class UploadClient(Protocol):
    """Protocol for injectable S3/Tigris or HuggingFace/GitHub upload clients."""

    def upload_bytes(
        self,
        remote_key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> None: ...


class InMemoryUploadClient:
    """In-memory upload recorder for hermetic unit tests."""

    def __init__(self) -> None:
        self.uploads: dict[str, bytes] = {}
        self.upload_order: list[str] = []

    def upload_bytes(
        self,
        remote_key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> None:
        _ = content_type
        self.uploads[remote_key] = bytes(data)
        self.upload_order.append(remote_key)


class Boto3BucketUploadClient:
    """SigV4 S3/Tigris upload client for Railway Storage Buckets."""

    def __init__(
        self,
        *,
        endpoint_url: str,
        bucket_name: str,
        access_key_id: str,
        secret_access_key: str,
        region_name: str = "auto",
    ) -> None:
        import boto3  # type: ignore[import-untyped]

        self.bucket_name = bucket_name
        self._s3 = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=region_name,
        )

    def upload_bytes(
        self,
        remote_key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> None:
        self._s3.put_object(
            Bucket=self.bucket_name,
            Key=remote_key,
            Body=data,
            ContentType=content_type,
        )


@dataclass
class PublishResult:
    """Summary of a completed blue/green corpus release publish."""

    tag: str
    target: PublishTarget
    uploaded_keys: list[str] = field(default_factory=list)
    swapped_manifest_key: str = "current/manifest.json"
    dry_run: bool = False


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def verify_gate_pass_for_publish(build_dir: Path) -> dict[str, Any]:
    """Verify `gate-pass.json` exists, passed=True, and no artifact SHA-256 has drifted."""
    gate_pass_path = build_dir / "gate-pass.json"
    if not gate_pass_path.is_file():
        raise PublishGateError(
            f"Refusing to publish: missing {gate_pass_path}. Run `nyaya-ingest verify-gate` first."
        )

    try:
        gate_data: Any = json.loads(gate_pass_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise PublishGateError(f"Failed to parse {gate_pass_path}: {exc}") from exc

    if not isinstance(gate_data, dict) or gate_data.get("passed") is not True:
        raise PublishGateError(f"Refusing to publish: {gate_pass_path} does not record passed=True.")

    expected_shas = gate_data.get("artifact_sha256s")
    if not isinstance(expected_shas, dict) or not expected_shas:
        raise PublishGateError(
            f"Refusing to publish: {gate_pass_path} contains no bound artifact_sha256s."
        )

    current_shas = collect_artifact_sha256s(build_dir)
    for rel_key, expected_sha in expected_shas.items():
        actual_sha = current_shas.get(str(rel_key))
        if actual_sha is None:
            raise PublishGateError(
                f"Refusing to publish: gated artifact {rel_key!r} is missing from {build_dir}."
            )
        if actual_sha.lower() != str(expected_sha).strip().lower():
            raise PublishGateError(
                f"Refusing to publish: artifact {rel_key!r} SHA-256 drifted after gate "
                f"(expected {expected_sha}, got {actual_sha})."
            )

    return gate_data


def generate_release_manifest(
    build_dir: Path,
    *,
    tag: str,
    public_base_url: str = "https://huggingface.co/datasets/nyaya-mcp/corpus/resolve/main",
) -> CorpusManifest:
    """Load and validate `manifest.json` in `build_dir` and bind release-tagged partition keys."""
    manifest_path = build_dir / "manifest.json"
    manifest = load_manifest(manifest_path)

    for ds in manifest.datasets.values():
        for part in ds.partitions:
            pfile = build_dir / part.s3_key
            if pfile.is_file():
                part.bytes = pfile.stat().st_size
                part.sha256 = _sha256_file(pfile)
            rel_clean = part.s3_key.lstrip("/")
            if not rel_clean.startswith("releases/"):
                part.public_url = f"{public_base_url.rstrip('/')}/releases/{tag}/{rel_clean}"

    return CorpusManifest.model_validate(manifest.model_dump())


def publish_release(
    build_dir: Path | str,
    *,
    tag: str,
    target: PublishTarget = "all",
    bucket_client: UploadClient | None = None,
    public_client: UploadClient | None = None,
    dry_run: bool = False,
) -> PublishResult:
    """Upload immutable `releases/<tag>/...` artifacts BEFORE atomically swapping `current/manifest.json`."""
    bdir = Path(build_dir)
    verify_gate_pass_for_publish(bdir)
    release_manifest = generate_release_manifest(bdir, tag=tag)
    manifest_bytes = json.dumps(release_manifest.model_dump(), indent=2).encode("utf-8")

    # Collect immutable release artifacts in deterministic order
    immutable_items: list[tuple[str, bytes, str]] = []
    sqlite_path = bdir / "nyaya-core.sqlite"
    if sqlite_path.is_file():
        immutable_items.append(
            (
                f"releases/{tag}/nyaya-core.sqlite",
                sqlite_path.read_bytes(),
                "application/vnd.sqlite3",
            )
        )
    sqlite_sha = bdir / "nyaya-core.sqlite.sha256"
    if sqlite_sha.is_file():
        immutable_items.append(
            (
                f"releases/{tag}/nyaya-core.sqlite.sha256",
                sqlite_sha.read_bytes(),
                "text/plain",
            )
        )

    for ds in release_manifest.datasets.values():
        for part in ds.partitions:
            pfile = bdir / part.s3_key
            if pfile.is_file():
                immutable_items.append(
                    (
                        f"releases/{tag}/{part.s3_key.lstrip('/')}",
                        pfile.read_bytes(),
                        "application/vnd.apache.parquet",
                    )
                )

    immutable_items.append(
        (
            f"releases/{tag}/manifest.json",
            manifest_bytes,
            "application/json",
        )
    )

    clients: list[UploadClient] = []
    if target in ("railway-bucket", "all") and bucket_client is not None:
        clients.append(bucket_client)
    if target in ("hf-dataset", "gh-release", "all") and public_client is not None:
        clients.append(public_client)

    result = PublishResult(tag=tag, target=target, dry_run=dry_run)
    for key, _, _ in immutable_items:
        result.uploaded_keys.append(key)
    result.uploaded_keys.append("current/manifest.json")

    if dry_run:
        return result

    for client in clients:
        # Phase 1: Upload all immutable release objects under `releases/<tag>/...`
        for remote_key, payload, ctype in immutable_items:
            client.upload_bytes(remote_key, payload, content_type=ctype)
        # Phase 2: Atomically swap `current/manifest.json` ONLY after all immutable objects succeed
        client.upload_bytes("current/manifest.json", manifest_bytes, content_type="application/json")

    return result
