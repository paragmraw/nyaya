"""CLI dispatcher for ingestion scripts.

Usage:
    nyaya-ingest schema           # apply schema.sql
    nyaya-ingest constitution     # ingest Constitution articles/schedules/amendments
    nyaya-ingest bare-acts        # ingest CrPC + commercial statutes from HF
    nyaya-ingest civictech        # ingest IPC/IEA/CPC from civictech JSON (full text)
    nyaya-ingest sanhitas         # ingest BNS/BNSS/BSA from PRS PDFs
    nyaya-ingest judgments        # ingest landmark SC judgments from YAML
    nyaya-ingest cross-refs       # build cross-references
    nyaya-ingest embeddings       # build pgvector embeddings
    nyaya-ingest all              # run everything in order
    nyaya-ingest counts           # print row counts
"""

from __future__ import annotations

import argparse

from .db import IngestDB


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="nyaya-ingest",
        description="Ingest data into the nyaya legal corpus.",
    )
    sub = parser.add_subparsers(dest="command", required=True, metavar="<command>")

    sub.add_parser("schema", help="apply schema.sql (DDL)")
    sub.add_parser("constitution", help="ingest Constitution articles/schedules/amendments")
    sub.add_parser("bare-acts", help="ingest CrPC + commercial statutes from HuggingFace")
    sub.add_parser("civictech", help="ingest IPC/IEA/CPC from civictech JSON (full text)")
    sub.add_parser("sanhitas", help="ingest BNS/BNSS/BSA from PRS PDFs")
    sub.add_parser("judgments", help="ingest landmark SC judgments from YAML")
    sub.add_parser("cross-refs", help="build cross-references between sections")
    sub.add_parser("embeddings", help="build pgvector embeddings")
    sub.add_parser("all", help="run everything in order")
    sub.add_parser("counts", help="print row counts")

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    cmd = args.command.replace("-", "_")

    with IngestDB() as db:
        if cmd == "schema":
            db.apply_schema()
            db.commit()
            print("✓ Schema applied.")
        elif cmd == "constitution":
            from .ingest_constitution import ingest_constitution
            ingest_constitution(db)
        elif cmd == "bare_acts":
            from .ingest_bare_acts import ingest_bare_acts
            ingest_bare_acts(db)
        elif cmd == "civictech":
            from .ingest_civictech import ingest_civictech
            ingest_civictech(db)
        elif cmd == "sanhitas":
            from .ingest_sanhitas import ingest_sanhitas
            ingest_sanhitas(db)
        elif cmd == "judgments":
            from .ingest_judgments import ingest_judgments
            ingest_judgments(db)
        elif cmd == "cross_refs":
            from .build_cross_refs import build_cross_refs
            build_cross_refs(db)
        elif cmd == "embeddings":
            from .build_embeddings import build_embeddings
            build_embeddings(db)
        elif cmd == "all":
            db.apply_schema()
            db.commit()
            print("✓ Schema applied.")
            from .ingest_constitution import ingest_constitution
            ingest_constitution(db)
            from .ingest_bare_acts import ingest_bare_acts
            ingest_bare_acts(db)
            from .ingest_civictech import ingest_civictech
            ingest_civictech(db)
            from .ingest_sanhitas import ingest_sanhitas
            ingest_sanhitas(db)
            from .ingest_judgments import ingest_judgments
            ingest_judgments(db)
            from .build_cross_refs import build_cross_refs
            build_cross_refs(db)
            from .build_embeddings import build_embeddings
            build_embeddings(db)
        elif cmd == "counts":
            db.print_counts()
        else:
            print(f"Unknown command: {cmd!r}")
            raise SystemExit(2)

    if cmd != "counts":
        print("\nFinal counts:")
        db2 = IngestDB()
        db2.connect()
        db2.print_counts()
        db2.close()
