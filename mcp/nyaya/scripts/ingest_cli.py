"""CLI dispatcher for ingestion scripts.

Usage:
    nyaya-ingest schema           # apply schema.sql
    nyaya-ingest constitution     # ingest Constitution articles/schedules/amendments
    nyaya-ingest bare-acts        # ingest IPC/CrPC/CPC/Evidence/commercial from HF
    nyaya-ingest sanhitas         # ingest BNS/BNSS/BSA from PRS PDFs
    nyaya-ingest judgments        # ingest landmark SC judgments from YAML
    nyaya-ingest cross-refs       # build cross-references
    nyaya-ingest embeddings       # build pgvector embeddings
    nyaya-ingest all              # run everything in order
    nyaya-ingest counts           # print row counts
"""

from __future__ import annotations

import sys

from .db import IngestDB


def main() -> None:
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(2)
    cmd = sys.argv[1].lower().replace("-", "_")

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
            db.apply_schema(); db.commit(); print("✓ Schema applied.")
            from .ingest_constitution import ingest_constitution
            ingest_constitution(db)
            from .ingest_bare_acts import ingest_bare_acts
            ingest_bare_acts(db)
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
            print(__doc__)
            sys.exit(2)

    if cmd != "counts":
        print("\nFinal counts:")
        db2 = IngestDB()
        db2.connect()
        db2.print_counts()
        db2.close()