"""Atomic blue/green publisher (`publish.py`) for Railway Storage Bucket (Tigris S3)
and public HuggingFace Datasets / GitHub Releases mirrors.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

import zstandard as zstd

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

    def upload_file(
        self,
        remote_key: str,
        file_path: Path,
        *,
        content_type: str = "application/octet-stream",
    ) -> None:
        self.upload_bytes(remote_key, file_path.read_bytes(), content_type=content_type)


class Boto3BucketUploadClient:
    """SigV4 S3/Tigris upload client for Railway Storage Buckets with streaming multipart upload."""

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

    def upload_file(
        self,
        remote_key: str,
        file_path: Path,
        *,
        content_type: str = "application/octet-stream",
    ) -> None:
        self._s3.upload_file(
            str(file_path),
            self.bucket_name,
            remote_key,
            ExtraArgs={"ContentType": content_type},
        )


class GitHubReleaseUploadClient:
    """Uploads release assets to a GitHub Release tag via the ``gh`` CLI."""

    def __init__(
        self,
        *,
        tag: str,
        repo: str | None = None,
        title: str | None = None,
        notes: str | None = None,
        gh_bin: str = "gh",
    ) -> None:
        self.tag = tag
        self.repo = repo or os.environ.get("GITHUB_REPOSITORY") or None
        self.title = title or f"Nyaya Corpus {tag}"
        self.notes = notes or f"Automated gated corpus release {tag}"
        self.gh_bin = gh_bin
        self._stage_dir = Path(tempfile.mkdtemp(prefix="nyaya-gh-release-"))
        self._assets: dict[str, Path] = {}

    def upload_bytes(
        self,
        remote_key: str,
        data: bytes,
        *,
        content_type: str = "application/octet-stream",
    ) -> None:
        _ = content_type
        if not remote_key.startswith(f"releases/{self.tag}/") and remote_key != "current/manifest.json":
            return
        asset_name = "current-manifest.json" if remote_key == "current/manifest.json" else Path(remote_key).name
        dest = self._stage_dir / asset_name
        dest.write_bytes(data)
        self._assets[asset_name] = dest

    def upload_file(
        self,
        remote_key: str,
        file_path: Path,
        *,
        content_type: str = "application/octet-stream",
    ) -> None:
        _ = content_type
        if not remote_key.startswith(f"releases/{self.tag}/"):
            return
        asset_name = Path(remote_key).name
        dest = self._stage_dir / asset_name
        shutil.copy2(file_path, dest)
        self._assets[asset_name] = dest

    def finalize(self) -> None:
        if not self._assets:
            return
        try:
            repo_args = ["--repo", self.repo] if self.repo else []
            asset_paths = [str(p) for p in self._assets.values()]
            view_cmd = [self.gh_bin, "release", "view", self.tag, *repo_args]
            exists = subprocess.run(view_cmd, check=False, capture_output=True, text=True).returncode == 0
            if exists:
                cmd = [self.gh_bin, "release", "upload", self.tag, *asset_paths, "--clobber", *repo_args]
            else:
                cmd = [
                    self.gh_bin,
                    "release",
                    "create",
                    self.tag,
                    *asset_paths,
                    "--title",
                    self.title,
                    "--notes",
                    self.notes,
                    *repo_args,
                ]
            subprocess.run(cmd, check=True, capture_output=True, text=True)
        finally:
            shutil.rmtree(self._stage_dir, ignore_errors=True)


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


def compress_sqlite_zst(sqlite_path: Path, *, level: int = 6) -> tuple[Path, Path]:
    """Stream-compress `nyaya-core.sqlite` -> `nyaya-core.sqlite.zst` and emit `.zst.sha256`."""
    zst_path = sqlite_path.with_name(f"{sqlite_path.name}.zst")
    cctx = zstd.ZstdCompressor(level=level)
    with sqlite_path.open("rb") as src, zst_path.open("wb") as dst:
        cctx.copy_stream(src, dst)
    zst_sha = _sha256_file(zst_path)
    zst_sha_path = zst_path.with_name(f"{zst_path.name}.sha256")
    zst_sha_path.write_text(f"{zst_sha}  {zst_path.name}\n", encoding="utf-8")
    return zst_path, zst_sha_path


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


def _local_rel_from_s3_key(s3_key: str, tag: str) -> str:
    cleaned = s3_key.lstrip("/")
    prefix = f"releases/{tag}/"
    if cleaned.startswith(prefix):
        return cleaned[len(prefix) :]
    return cleaned


def generate_release_manifest(
    build_dir: Path,
    *,
    tag: str,
    public_base_url: str = "https://huggingface.co/datasets/nyaya-mcp/corpus/resolve/main",
) -> CorpusManifest:
    """Load and validate `manifest.json` in `build_dir` and bind release-tagged S3 & public keys."""
    manifest_path = build_dir / "manifest.json"
    manifest = load_manifest(manifest_path)

    for ds in manifest.datasets.values():
        for part in ds.partitions:
            local_rel = _local_rel_from_s3_key(part.s3_key, tag)
            pfile = build_dir / local_rel
            if pfile.is_file():
                part.bytes = pfile.stat().st_size
                part.sha256 = _sha256_file(pfile)
            rel_clean = part.s3_key.lstrip("/")
            if not rel_clean.startswith("releases/"):
                part.s3_key = f"releases/{tag}/{rel_clean}"
                part.public_url = f"{public_base_url.rstrip('/')}/releases/{tag}/{rel_clean}"

    return CorpusManifest.model_validate(manifest.model_dump())


def _upload_payload(
    client: UploadClient,
    remote_key: str,
    payload: Path | bytes,
    content_type: str,
) -> None:
    if isinstance(payload, Path):
        upload_file_fn = getattr(client, "upload_file", None)
        if callable(upload_file_fn):
            upload_file_fn(remote_key, payload, content_type=content_type)
        else:
            client.upload_bytes(remote_key, payload.read_bytes(), content_type=content_type)
    else:
        client.upload_bytes(remote_key, payload, content_type=content_type)


def publish_release(
    build_dir: Path | str,
    *,
    tag: str,
    target: PublishTarget = "all",
    bucket_client: UploadClient | None = None,
    public_client: UploadClient | None = None,
    dry_run: bool = False,
) -> PublishResult:
    """Upload immutable `releases/<tag>/...` artifacts and `lancedb/` BEFORE atomically
    swapping `current/manifest.json`.
    """
    bdir = Path(build_dir)
    verify_gate_pass_for_publish(bdir)

    sqlite_path = bdir / "nyaya-core.sqlite"
    zst_path: Path | None = None
    zst_sha_path: Path | None = None
    if sqlite_path.is_file():
        sha_path = bdir / "nyaya-core.sqlite.sha256"
        if not sha_path.is_file():
            sha_path.write_text(
                f"{_sha256_file(sqlite_path)}  {sqlite_path.name}\n",
                encoding="utf-8",
            )
        zst_path, zst_sha_path = compress_sqlite_zst(sqlite_path)

    release_manifest = generate_release_manifest(bdir, tag=tag)
    manifest_bytes = json.dumps(release_manifest.model_dump(), indent=2).encode("utf-8")

    # Collect immutable release artifacts in deterministic order (streamed via Path where on disk)
    immutable_items: list[tuple[str, Path | bytes, str]] = []
    if sqlite_path.is_file():
        immutable_items.append(
            (f"releases/{tag}/nyaya-core.sqlite", sqlite_path, "application/vnd.sqlite3")
        )
    sqlite_sha = bdir / "nyaya-core.sqlite.sha256"
    if sqlite_sha.is_file():
        immutable_items.append(
            (f"releases/{tag}/nyaya-core.sqlite.sha256", sqlite_sha, "text/plain")
        )
    if zst_path is not None and zst_path.is_file():
        immutable_items.append(
            (f"releases/{tag}/nyaya-core.sqlite.zst", zst_path, "application/zstd")
        )
    if zst_sha_path is not None and zst_sha_path.is_file():
        immutable_items.append(
            (f"releases/{tag}/nyaya-core.sqlite.zst.sha256", zst_sha_path, "text/plain")
        )

    for ds in release_manifest.datasets.values():
        for part in ds.partitions:
            local_rel = _local_rel_from_s3_key(part.s3_key, tag)
            pfile = bdir / local_rel
            if pfile.is_file():
                remote_key = (
                    part.s3_key
                    if part.s3_key.startswith("releases/")
                    else f"releases/{tag}/{part.s3_key.lstrip('/')}"
                )
                immutable_items.append((remote_key, pfile, "application/vnd.apache.parquet"))

    immutable_items.append(
        (f"releases/{tag}/manifest.json", manifest_bytes, "application/json")
    )

    # Collect LanceDB directory files recursively for bucket_client
    lancedb_dir = bdir / "lancedb"
    lancedb_items: list[tuple[str, Path, str]] = []
    if lancedb_dir.is_dir():
        for fpath in sorted(p for p in lancedb_dir.rglob("*") if p.is_file()):
            rel_posix = fpath.relative_to(lancedb_dir).as_posix()
            lancedb_items.append((f"lancedb/{rel_posix}", fpath, "application/octet-stream"))

    use_bucket = target in ("railway-bucket", "all") and bucket_client is not None
    use_public = target in ("hf-dataset", "gh-release", "all") and public_client is not None

    result = PublishResult(tag=tag, target=target, dry_run=dry_run)
    for key, _, _ in immutable_items:
        result.uploaded_keys.append(key)
    if use_bucket:
        for key, _, _ in lancedb_items:
            result.uploaded_keys.append(key)
    result.uploaded_keys.append("current/manifest.json")

    if dry_run:
        return result

    if use_bucket and bucket_client is not None:
        # Phase 1: Upload all immutable release objects + LanceDB files
        for remote_key, payload, ctype in immutable_items:
            _upload_payload(bucket_client, remote_key, payload, ctype)
        for remote_key, fpath, ctype in lancedb_items:
            _upload_payload(bucket_client, remote_key, fpath, ctype)
        # Phase 2: Atomically swap `current/manifest.json` ONLY after all immutable objects succeed
        bucket_client.upload_bytes("current/manifest.json", manifest_bytes, content_type="application/json")
        fin = getattr(bucket_client, "finalize", None)
        if callable(fin):
            fin()

    if use_public and public_client is not None:
        for remote_key, payload, ctype in immutable_items:
            _upload_payload(public_client, remote_key, payload, ctype)
        public_client.upload_bytes("current/manifest.json", manifest_bytes, content_type="application/json")
        fin = getattr(public_client, "finalize", None)
        if callable(fin):
            fin()

    return result
