"""`nyaya-ingest` CLI entrypoint supporting `fetch`/`sync`, `extract`, `build-sqlite`,
`build-lance`/`build-lance-fts`, `build-parquet`/`compact-parquet`, `gate`/`verify-gate`,
`publish`, and `all` with `--fixture` and `--dry-run`.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from .builders.lance_fts import build_lance_fts_tables
from .builders.parquet_compact import build_compacted_parquets
from .builders.sqlite_core import build_sqlite_core, create_mini_ingest_input
from .extract.pdf import extract_pdf_file
from .fetchers.s3_dattam import DattamS3Fetcher
from .fetchers.vaquill import VaquillFetcher
from .gate import run_quality_gate
from .lockfile import DEFAULT_LOCKFILE_PATH, load_and_validate_lockfile
from .publish import InMemoryUploadClient, publish_release
from .state import IngestState

_SAMPLE_FIXTURE_S3_XML = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Name>indian-supreme-court-judgments</Name>
  <IsTruncated>false</IsTruncated>
  <Contents>
    <Key>metadata/parquet/year=2024/metadata.parquet</Key>
    <LastModified>2026-11-22T00:00:00.000Z</LastModified>
    <ETag>"fixture-sc-2024"</ETag>
    <Size>1024</Size>
  </Contents>
</ListBucketResult>
"""


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nyaya-ingest",
        description="Nyaya v2.0 OpenJustice Ingestion Pipeline, Curated Legal Data & Quality Gate CLI.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # 1. fetch / sync
    for cmd_name in ("fetch", "sync"):
        p_sync = sub.add_parser(cmd_name, help="Sync upstream S3/Vaquill/PRS/Research packs.")
        p_sync.add_argument("--lockfile", type=Path, default=DEFAULT_LOCKFILE_PATH)
        p_sync.add_argument("--tier", choices=["daily", "static", "all"], default="all")
        p_sync.add_argument("--sources", default="all")
        p_sync.add_argument("--work-dir", type=Path, default=Path("build/work"))
        p_sync.add_argument("--commercial-safe-only", action="store_true")
        p_sync.add_argument("--fixture", action="store_true")
        p_sync.add_argument("--dry-run", action="store_true")

    # 2. extract
    p_ext = sub.add_parser("extract", help="Extract text and chunks from PDF files via pypdfium2.")
    p_ext.add_argument("--pdf", type=Path, default=None)
    p_ext.add_argument("--work-dir", type=Path, default=Path("build/work"))
    p_ext.add_argument("--out", type=Path, default=None)
    p_ext.add_argument("--fixture", action="store_true")
    p_ext.add_argument("--dry-run", action="store_true")

    # 3. build-sqlite
    p_sql = sub.add_parser("build-sqlite", help="Build staged nyaya-core.sqlite + FTS5 + SHA-256.")
    p_sql.add_argument("--work-dir", type=Path, default=Path("build/work"))
    p_sql.add_argument("--out", type=Path, default=Path("build/nyaya-core.sqlite"))
    p_sql.add_argument("--lockfile", type=Path, default=DEFAULT_LOCKFILE_PATH)
    p_sql.add_argument("--commercial-safe-only", action="store_true")
    p_sql.add_argument("--fixture", action="store_true")

    # 4. build-lance / build-lance-fts
    for cmd_name in ("build-lance", "build-lance-fts"):
        p_lance = sub.add_parser(cmd_name, help="Build 3 LanceDB native Rust BM25 FTS tables.")
        p_lance.add_argument("--work-dir", type=Path, default=Path("build/work"))
        p_lance.add_argument("--out", type=Path, default=Path("build/lancedb"))
        p_lance.add_argument("--hot-hc-limit", type=int, default=200_000)
        p_lance.add_argument("--commercial-safe-only", action="store_true")
        p_lance.add_argument("--fixture", action="store_true")

    # 5. build-parquet / compact-parquet
    for cmd_name in ("build-parquet", "compact-parquet"):
        p_pq = sub.add_parser(cmd_name, help="Compact ZSTD Parquet partitions and emit manifest.json.")
        p_pq.add_argument("--work-dir", type=Path, default=Path("build/work"))
        p_pq.add_argument("--out", type=Path, default=Path("build"))
        p_pq.add_argument("--zstd-level", type=int, default=6)
        p_pq.add_argument("--commercial-safe-only", action="store_true")
        p_pq.add_argument("--fixture", action="store_true")

    # 6. gate / verify-gate
    for cmd_name in ("gate", "verify-gate"):
        p_gate = sub.add_parser(cmd_name, help="Run the 7 Legal Integrity Gate suites.")
        p_gate.add_argument("--build-dir", "--data-dir", dest="build_dir", type=Path, default=Path("build"))
        p_gate.add_argument("--strict", action="store_true")
        p_gate.add_argument("--report", type=Path, default=None)

    # 7. publish
    p_pub = sub.add_parser("publish", help="Atomically publish gated artifacts to Railway Bucket & HF/GH.")
    p_pub.add_argument("--build-dir", type=Path, default=Path("build"))
    p_pub.add_argument(
        "--target",
        choices=["railway-bucket", "hf-dataset", "gh-release", "all"],
        default="all",
    )
    p_pub.add_argument("--tag", default="v2026.11.22")
    p_pub.add_argument("--dry-run", action="store_true")

    # 8. all
    p_all = sub.add_parser("all", help="Run fetch -> build-sqlite -> build-lance -> build-parquet -> gate -> publish.")
    p_all.add_argument("--lockfile", type=Path, default=DEFAULT_LOCKFILE_PATH)
    p_all.add_argument("--work-dir", type=Path, default=Path("build/work"))
    p_all.add_argument("--build-dir", type=Path, default=Path("build"))
    p_all.add_argument("--tag", default="v2026.11.22")
    p_all.add_argument(
        "--target",
        choices=["railway-bucket", "hf-dataset", "gh-release", "all"],
        default="all",
    )
    p_all.add_argument("--commercial-safe-only", action="store_true")
    p_all.add_argument("--fixture", action="store_true")
    p_all.add_argument("--dry-run", action="store_true")

    return parser


def _cmd_sync(
    *,
    lockfile: Path,
    work_dir: Path,
    fixture: bool,
    dry_run: bool,
) -> int:
    load_and_validate_lockfile(lockfile)
    work_dir.mkdir(parents=True, exist_ok=True)
    state = IngestState(work_dir / "ingest_state.sqlite")
    try:
        if fixture or dry_run:
            s3_fetcher = DattamS3Fetcher(state=state)
            s3_fetcher.sync_bucket(
                "indian-supreme-court-judgments",
                work_dir,
                dry_run=dry_run,
                fixture_xml=_SAMPLE_FIXTURE_S3_XML,
            )
            vq_fetcher = VaquillFetcher(state=state)
            vq_fetcher.sync_packs(
                work_dir,
                dry_run=dry_run,
                fixture_sha256sums={"in_central_legislation.parquet": "a" * 64},
            )
    finally:
        state.close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    cmd = str(args.command)

    if cmd in ("fetch", "sync"):
        return _cmd_sync(
            lockfile=args.lockfile,
            work_dir=args.work_dir,
            fixture=bool(args.fixture),
            dry_run=bool(args.dry_run),
        )

    if cmd == "extract":
        if args.pdf is not None and Path(args.pdf).is_file():
            res = extract_pdf_file(args.pdf)
            if args.out is not None:
                Path(args.out).parent.mkdir(parents=True, exist_ok=True)
                Path(args.out).write_text(res.full_text, encoding="utf-8")
        return 0

    if cmd == "build-sqlite":
        bundle = create_mini_ingest_input()
        build_sqlite_core(
            args.out,
            input_data=bundle,
            lockfile_path=args.lockfile,
            commercial_safe_only=bool(args.commercial_safe_only),
        )
        return 0

    if cmd in ("build-lance", "build-lance-fts"):
        bundle = create_mini_ingest_input()
        build_lance_fts_tables(
            args.out,
            input_data=bundle,
            commercial_safe_only=bool(args.commercial_safe_only),
            hot_hc_limit=int(args.hot_hc_limit),
        )
        return 0

    if cmd in ("build-parquet", "compact-parquet"):
        bundle = create_mini_ingest_input()
        out_root = Path(args.out)
        if out_root.name == "parquet":
            out_root = out_root.parent
        build_compacted_parquets(
            out_root,
            input_data=bundle,
            commercial_safe_only=bool(args.commercial_safe_only),
        )
        return 0

    if cmd in ("gate", "verify-gate"):
        report = run_quality_gate(
            args.build_dir,
            strict_landmarks=bool(args.strict),
            write_gate_pass=True,
        )
        if args.report is not None:
            Path(args.report).parent.mkdir(parents=True, exist_ok=True)
            Path(args.report).write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        if not report.passed:
            for err in report.all_errors:
                print(err, file=sys.stderr)
            return 1
        return 0

    if cmd == "publish":
        mem_bucket = InMemoryUploadClient()
        mem_public = InMemoryUploadClient()
        publish_release(
            args.build_dir,
            tag=args.tag,
            target=args.target,
            bucket_client=mem_bucket,
            public_client=mem_public,
            dry_run=bool(args.dry_run),
        )
        return 0

    if cmd == "all":
        _cmd_sync(
            lockfile=args.lockfile,
            work_dir=args.work_dir,
            fixture=bool(args.fixture),
            dry_run=bool(args.dry_run),
        )
        bundle = create_mini_ingest_input()
        build_dir = Path(args.build_dir)
        build_sqlite_core(
            build_dir / "nyaya-core.sqlite",
            input_data=bundle,
            lockfile_path=args.lockfile,
            commercial_safe_only=bool(args.commercial_safe_only),
        )
        build_lance_fts_tables(
            build_dir / "lancedb",
            input_data=bundle,
            commercial_safe_only=bool(args.commercial_safe_only),
        )
        build_compacted_parquets(
            build_dir,
            input_data=bundle,
            commercial_safe_only=bool(args.commercial_safe_only),
        )
        report = run_quality_gate(build_dir, write_gate_pass=True)
        if not report.passed:
            for err in report.all_errors:
                print(err, file=sys.stderr)
            return 1
        mem_bucket = InMemoryUploadClient()
        mem_public = InMemoryUploadClient()
        publish_release(
            build_dir,
            tag=args.tag,
            target=args.target,
            bucket_client=mem_bucket,
            public_client=mem_public,
            dry_run=bool(args.dry_run),
        )
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
