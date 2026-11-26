"""CLI entrypoint for ``nyaya-mcp`` and ``nyaya`` (dual stdio and HTTP transport).

- ``nyaya-mcp`` (default ``--transport stdio``): configures logging strictly to
  ``sys.stderr`` before importing runtime modules so ``sys.stdout`` remains
  100% pure JSON-RPC 2.0.
- ``nyaya-mcp --transport http`` (or ``nyaya serve``): boots the Starlette HTTP
  server via Uvicorn.
- ``nyaya-mcp sync``: pre-downloads or builds local data packs for offline use.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import stat
import sys
import tarfile
import tempfile
import zipfile
from collections.abc import Sequence
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import httpx
import zstandard as zstd

from . import __version__
from .config import Settings, get_rate_limit_settings, get_settings
from .storage.manifest import CorpusManifest

log = logging.getLogger("nyaya.cli")


def _configure_stderr_logging(level_name: str = "WARNING") -> None:
    """Bind root logging strictly to sys.stderr so stdout is never polluted."""
    level = getattr(logging, level_name.upper(), logging.INFO)
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    root.addHandler(handler)
    root.setLevel(level)


def _download_and_verify_core(settings: Settings) -> bool:
    """Download nyaya-core.sqlite(.zst) from settings.manifest_url if configured."""
    target = settings.data_dir / "nyaya-core.sqlite"
    if target.is_file():
        return True
    if settings.offline_only or not settings.manifest_url:
        return False

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    try:
        with httpx.Client(follow_redirects=True, timeout=30.0) as client:
            resp = client.get(settings.manifest_url)
            resp.raise_for_status()
            manifest = resp.json()
            core_info = manifest.get("core_sqlite") or {}
            core_url = core_info.get("url")
            expected_sha = (core_info.get("sha256") or "").lower()
            if not core_url:
                return False

            with client.stream("GET", str(core_url)) as dl:
                dl.raise_for_status()
                fd, tmp_str = tempfile.mkstemp(dir=str(settings.data_dir), suffix=".download")
                tmp_path = Path(tmp_str)
                hasher = hashlib.sha256()
                with os.fdopen(fd, "wb") as f:
                    for chunk in dl.iter_bytes(65_536):
                        f.write(chunk)
                        hasher.update(chunk)

            if expected_sha and hasher.hexdigest().lower() != expected_sha:
                tmp_path.unlink(missing_ok=True)
                raise RuntimeError("Downloaded nyaya-core.sqlite SHA-256 mismatch")

            if str(core_url).endswith(".zst"):
                out_tmp = settings.data_dir / "nyaya-core.sqlite.tmp"
                dctx = zstd.ZstdDecompressor()
                with tmp_path.open("rb") as src, out_tmp.open("wb") as dst:
                    dctx.copy_stream(src, dst)
                tmp_path.unlink(missing_ok=True)
                out_tmp.replace(target)
            else:
                tmp_path.replace(target)
            return True
    except Exception as exc:
        log.warning("Could not auto-download nyaya-core.sqlite: %s", exc)
        return False


def _download_verified_file(
    client: httpx.Client,
    url: str,
    dest_dir: Path,
    *,
    expected_sha256: str | None = None,
    suffix: str = ".download",
) -> Path:
    """Stream-download ``url`` to a temp file in ``dest_dir`` and verify SHA-256."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    with client.stream("GET", url) as dl:
        dl.raise_for_status()
        fd, tmp_str = tempfile.mkstemp(dir=str(dest_dir), suffix=suffix)
        tmp_path = Path(tmp_str)
        hasher = hashlib.sha256()
        try:
            with os.fdopen(fd, "wb") as f:
                for chunk in dl.iter_bytes(65_536):
                    f.write(chunk)
                    hasher.update(chunk)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    expected = (expected_sha256 or "").strip().lower()
    if expected and hasher.hexdigest().lower() != expected:
        tmp_path.unlink(missing_ok=True)
        raise RuntimeError(
            f"SHA-256 mismatch for {url}: expected {expected}, got {hasher.hexdigest().lower()}"
        )
    return tmp_path


def _safe_dest_path(member_name: str, dest_dir: Path, *, is_link: bool = False) -> Path:
    """Resolve an archive/manifest relative path inside ``dest_dir``, rejecting traversal."""
    if is_link:
        raise ValueError(f"Unsafe archive member link rejected: {member_name!r}")
    raw = member_name.strip()
    if not raw or raw.startswith(("/", "\\")):
        raise ValueError(f"Unsafe archive member path: {member_name!r}")
    win_path = PureWindowsPath(raw)
    posix_path = PurePosixPath(raw)
    if win_path.drive or win_path.is_absolute() or posix_path.is_absolute():
        raise ValueError(f"Unsafe archive member path: {member_name!r}")
    if ".." in win_path.parts or ".." in posix_path.parts:
        raise ValueError(f"Unsafe archive member path: {member_name!r}")

    rel_parts = [p for p in raw.replace("\\", "/").split("/") if p and p != "."]
    if not rel_parts:
        return dest_dir.resolve()
    resolved_root = dest_dir.resolve()
    candidate = resolved_root.joinpath(*rel_parts).resolve()
    if not candidate.is_relative_to(resolved_root):
        raise ValueError(f"Unsafe archive member path escapes target dir: {member_name!r}")
    return candidate


def _extract_lance_archive(archive_path: Path, url: str, lance_dir: Path) -> None:
    """Extract a ``.zip`` or ``.tar.zst``/``.tar`` archive safely into ``lance_dir``."""
    lance_dir.mkdir(parents=True, exist_ok=True)
    lower_url = url.lower().split("?", 1)[0]

    if lower_url.endswith(".zip") or zipfile.is_zipfile(archive_path):
        with zipfile.ZipFile(archive_path, "r") as zf:
            members: list[tuple[zipfile.ZipInfo, Path]] = []
            for info in zf.infolist():
                is_symlink = stat.S_ISLNK((info.external_attr >> 16) & 0xFFFF)
                target = _safe_dest_path(info.filename, lance_dir, is_link=is_symlink)
                members.append((info, target))
            for info, target in members:
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zf.open(info, "r") as src, target.open("wb") as dst:
                        shutil.copyfileobj(src, dst)
        return

    tar_path = archive_path
    tmp_tar: Path | None = None
    try:
        if lower_url.endswith(".zst") or lower_url.endswith(".tzst"):
            fd, tmp_tar_str = tempfile.mkstemp(dir=str(lance_dir.parent), suffix=".tar")
            os.close(fd)
            tmp_tar = Path(tmp_tar_str)
            dctx = zstd.ZstdDecompressor()
            with archive_path.open("rb") as src, tmp_tar.open("wb") as dst:
                dctx.copy_stream(src, dst)
            tar_path = tmp_tar

        tar_cm = (
            tarfile.open(tar_path, "r:")
            if lower_url.endswith((".zst", ".tzst"))
            else tarfile.open(tar_path, "r:*")
        )
        with tar_cm as tf:
            safe_members: list[tuple[tarfile.TarInfo, Path]] = []
            for m in tf.getmembers():
                is_bad = m.issym() or m.islnk() or not (m.isdir() or m.isreg())
                target = _safe_dest_path(m.name, lance_dir, is_link=is_bad)
                safe_members.append((m, target))
            for m, target in safe_members:
                if m.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                elif m.isreg():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    extracted = tf.extractfile(m)
                    if extracted is not None:
                        with extracted, target.open("wb") as dst:
                            shutil.copyfileobj(extracted, dst)
    finally:
        if tmp_tar is not None:
            tmp_tar.unlink(missing_ok=True)


def _sync_core_pack(
    settings: Settings,
    manifest: dict[str, Any] | None,
    client: httpx.Client | None,
) -> Path | None:
    target = settings.data_dir / "nyaya-core.sqlite"
    core_info = (manifest or {}).get("core_sqlite") or {}
    core_url = core_info.get("url")
    if core_url and client is not None and not settings.offline_only:
        tmp_dl = _download_verified_file(
            client,
            str(core_url),
            settings.data_dir,
            expected_sha256=core_info.get("sha256"),
            suffix=".core.dl",
        )
        if str(core_url).lower().split("?", 1)[0].endswith(".zst"):
            out_tmp = settings.data_dir / "nyaya-core.sqlite.tmp"
            dctx = zstd.ZstdDecompressor()
            try:
                with tmp_dl.open("rb") as src, out_tmp.open("wb") as dst:
                    dctx.copy_stream(src, dst)
            finally:
                tmp_dl.unlink(missing_ok=True)
            out_tmp.replace(target)
        else:
            tmp_dl.replace(target)
        return target

    db_path = ensure_local_data(
        settings,
        no_download=settings.offline_only,
        ensure_data=True,
    )
    return db_path if db_path.is_file() else None


def _sync_lance_pack(
    settings: Settings,
    manifest: dict[str, Any] | None,
    client: httpx.Client | None,
) -> Path | None:
    lance_dir = settings.data_dir / "lancedb"
    lance_info = (manifest or {}).get("lance_fts") or {}
    lance_url = lance_info.get("url")
    if not lance_url or client is None or settings.offline_only:
        return lance_dir if lance_dir.is_dir() else None

    tmp_archive = _download_verified_file(
        client,
        str(lance_url),
        settings.data_dir,
        expected_sha256=lance_info.get("sha256"),
        suffix=".lance.dl",
    )
    try:
        _extract_lance_archive(tmp_archive, str(lance_url), lance_dir)
    finally:
        tmp_archive.unlink(missing_ok=True)
    return lance_dir


def _sync_parquet_pack(
    settings: Settings,
    manifest: dict[str, Any] | None,
    client: httpx.Client | None,
    *,
    cs_only: bool,
) -> dict[str, Path]:
    synced: dict[str, Path] = {}
    manifest_path = settings.data_dir / "manifest.json"
    corpus_manifest: CorpusManifest | None = None

    if manifest and isinstance(manifest.get("datasets"), dict):
        corpus_manifest = CorpusManifest.model_validate(manifest)
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        synced["parquet_manifest"] = manifest_path
    elif manifest and isinstance(manifest.get("parquet_manifest"), dict) and client is not None and not settings.offline_only:
        pm_info = manifest["parquet_manifest"]
        pm_url = pm_info.get("url")
        if pm_url:
            tmp_mf = _download_verified_file(
                client,
                str(pm_url),
                settings.data_dir,
                expected_sha256=pm_info.get("sha256"),
                suffix=".manifest.dl",
            )
            raw_mf = tmp_mf.read_text(encoding="utf-8")
            corpus_manifest = CorpusManifest.model_validate(json.loads(raw_mf))
            tmp_mf.replace(manifest_path)
            synced["parquet_manifest"] = manifest_path
    elif manifest_path.is_file():
        corpus_manifest = CorpusManifest.model_validate(
            json.loads(manifest_path.read_text(encoding="utf-8"))
        )
        synced["parquet_manifest"] = manifest_path

    if corpus_manifest is None or client is None or settings.offline_only:
        return synced

    base_url = str((manifest or {}).get("parquet_base_url") or "").rstrip("/")
    for ds_name, ds in corpus_manifest.datasets.items():
        if cs_only and not ds.commercial_safe:
            continue
        for part in ds.partitions:
            part_url = part.public_url or (f"{base_url}/{part.s3_key.lstrip('/')}" if base_url else None)
            if not part_url:
                continue
            dest = _safe_dest_path(part.s3_key.lstrip("/"), settings.data_dir)
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp_part = _download_verified_file(
                client,
                str(part_url),
                dest.parent,
                expected_sha256=part.sha256 or None,
                suffix=".parquet.dl",
            )
            tmp_part.replace(dest)
            synced[f"parquet:{ds_name}:{part.s3_key}"] = dest
    return synced


def sync_packs(
    settings: Settings,
    *,
    pack: str = "core",
    commercial_safe_only: bool = False,
) -> dict[str, Path]:
    """Download and SHA-256-verify Nyaya data packs (``core``, ``lance``, ``parquet``, or ``all``)."""
    valid_packs = ("core", "lance", "parquet", "all")
    if pack not in valid_packs:
        raise ValueError(f"Unsupported pack {pack!r}; expected one of {valid_packs}.")

    cs_only = bool(commercial_safe_only or settings.commercial_safe_only)
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    synced: dict[str, Path] = {}

    manifest: dict[str, Any] | None = None
    client: httpx.Client | None = None
    try:
        if not settings.offline_only and settings.manifest_url:
            client = httpx.Client(follow_redirects=True, timeout=60.0)
            resp = client.get(settings.manifest_url)
            resp.raise_for_status()
            raw = resp.json()
            if isinstance(raw, dict):
                manifest = raw

        if pack in ("core", "all"):
            core_path = _sync_core_pack(settings, manifest, client)
            if core_path is not None:
                synced["core"] = core_path

        if pack in ("lance", "all"):
            lance_path = _sync_lance_pack(settings, manifest, client)
            if lance_path is not None:
                synced["lance"] = lance_path

        if pack in ("parquet", "all"):
            synced.update(_sync_parquet_pack(settings, manifest, client, cs_only=cs_only))
    finally:
        if client is not None:
            client.close()

    return synced


def ensure_local_data(
    settings: Settings,
    *,
    no_download: bool = False,
    ensure_data: bool = False,
) -> Path:
    """Ensure a playable nyaya-core.sqlite exists in settings.data_dir (or build mini fixture if pointed at fixture source)."""
    db_file = settings.data_dir / "nyaya-core.sqlite"
    if db_file.is_file():
        return db_file

    # If --data-dir points at tests/fixtures/mini_corpus (where builder.py lives),
    # build the fixture into a writable temp cache dir or directly if writable.
    builder_py = settings.data_dir / "builder.py"
    if builder_py.is_file() or ensure_data:
        try:
            from tests.fixtures.mini_corpus.builder import build_mini_corpus

            out_dir = settings.data_dir
            try:
                out_dir.mkdir(parents=True, exist_ok=True)
                probe = out_dir / ".write_probe"
                probe.write_text("ok", encoding="utf-8")
                probe.unlink(missing_ok=True)
            except OSError:
                out_dir = Path(tempfile.mkdtemp(prefix="nyaya_mini_corpus_"))
                os.environ["NYAYA_DATA_DIR"] = str(out_dir)
                get_settings.cache_clear()
            build_mini_corpus(out_dir)
            return out_dir / "nyaya-core.sqlite"
        except Exception as exc:
            log.debug("Mini corpus builder fallback skipped: %s", exc)

    if not no_download and not settings.offline_only:
        _download_and_verify_core(settings)

    return settings.data_dir / "nyaya-core.sqlite"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nyaya-mcp",
        description="Nyaya v2.0 Indian Law MCP Server (stdio & HTTP)",
    )
    parser.add_argument("--version", action="version", version=f"nyaya-mcp {__version__}")
    parser.add_argument(
        "subcommand",
        nargs="?",
        choices=["serve", "sync"],
        help="Optional subcommand: 'serve' (HTTP mode) or 'sync' (download data packs).",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        default="stdio",
        help="MCP transport mode (default: stdio).",
    )
    parser.add_argument("--data-dir", type=Path, default=None, help="Path to local Nyaya data directory.")
    parser.add_argument("--cache-dir", type=Path, default=None, help="Path to local Nyaya cache directory.")
    parser.add_argument(
        "--commercial-safe-only",
        action="store_true",
        default=False,
        help="Exclude non-commercial datasets (DDL 81M & ILDC).",
    )
    parser.add_argument(
        "--offline-only",
        action="store_true",
        default=False,
        help="Disable all remote S3/Bucket/manifest network fetches.",
    )
    parser.add_argument(
        "--no-download",
        action="store_true",
        default=False,
        help="Skip automatic startup download of missing core data pack.",
    )
    parser.add_argument(
        "--ensure-data",
        action="store_true",
        default=False,
        help="Ensure local SQLite data pack is present (downloading or building fixture if needed).",
    )
    parser.add_argument(
        "--pack",
        choices=["core", "lance", "parquet", "all"],
        default="core",
        help="Data pack to sync when running `nyaya-mcp sync`.",
    )
    parser.add_argument("--host", default="0.0.0.0", help="HTTP bind host (default: 0.0.0.0).")
    parser.add_argument("--port", type=int, default=None, help="HTTP bind port (default: PORT env or 8000).")
    return parser


def _apply_cli_env(args: argparse.Namespace) -> Settings:
    if args.data_dir is not None:
        os.environ["NYAYA_DATA_DIR"] = str(args.data_dir)
    if args.cache_dir is not None:
        os.environ["NYAYA_CACHE_DIR"] = str(args.cache_dir)
    if args.commercial_safe_only:
        os.environ["NYAYA_COMMERCIAL_SAFE_ONLY"] = "1"
    if args.offline_only:
        os.environ["NYAYA_OFFLINE_ONLY"] = "1"
    if args.port is not None:
        os.environ["PORT"] = str(args.port)
    get_settings.cache_clear()
    get_rate_limit_settings.cache_clear()
    return get_settings()


def main(argv: Sequence[str] | None = None) -> int:
    # Always protect stdout before parsing or importing anything else
    _configure_stderr_logging(os.environ.get("NYAYA_LOG_LEVEL", "WARNING"))

    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    settings = _apply_cli_env(args)

    transport = "http" if args.subcommand == "serve" else args.transport

    if args.subcommand == "sync":
        _configure_stderr_logging(settings.log_level)
        synced = sync_packs(
            settings,
            pack=str(args.pack),
            commercial_safe_only=bool(args.commercial_safe_only or settings.commercial_safe_only),
        )
        log.info("Synced Nyaya %s pack: %s", args.pack, {k: str(v) for k, v in synced.items()})
        return 0

    if transport == "stdio":
        _configure_stderr_logging(os.environ.get("NYAYA_LOG_LEVEL", "ERROR"))
        ensure_local_data(
            settings,
            no_download=bool(args.no_download),
            ensure_data=bool(args.ensure_data),
        )
        from .mcp_app import create_mcp

        mcp = create_mcp()
        mcp.run(transport="stdio", show_banner=False)
        return 0

    # HTTP transport
    _configure_stderr_logging(settings.log_level)
    ensure_local_data(
        settings,
        no_download=bool(args.no_download),
        ensure_data=bool(args.ensure_data),
    )
    try:
        import uvicorn
    except ImportError:
        sys.stderr.write(
            "Error: uvicorn is required for '--transport http'. "
            "Install HTTP extras with: pip install \"nyaya-mcp[http]\"\n"
        )
        return 1

    log.info(
        "Starting nyaya HTTP server (host=%s, port=%d, offline_only=%s, commercial_safe_only=%s)",
        args.host,
        settings.port,
        settings.offline_only,
        settings.commercial_safe_only,
    )
    uvicorn.run(
        "nyaya.http_app:create_http_app",
        factory=True,
        host=args.host,
        port=settings.port,
        log_level=settings.log_level.lower(),
        timeout_keep_alive=30,
        limit_concurrency=64,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
