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
import logging
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path

import httpx
import zstandard as zstd

from . import __version__
from .config import Settings, get_rate_limit_settings, get_settings

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
        db_path = ensure_local_data(settings, no_download=False, ensure_data=True)
        log.info("Synced Nyaya %s pack at %s", args.pack, db_path)
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

    log.info("Starting nyaya HTTP server: %s", settings.as_dict())
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
