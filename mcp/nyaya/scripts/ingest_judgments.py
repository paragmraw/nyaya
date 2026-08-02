"""Ingest landmark Supreme Court judgments from a manual YAML file.

The judgments.yaml file under data/manual/ contains a curated list of cases
with full judgment text pasted from indiankanoon.org's free browse view
(public domain — government edicts).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

from .db import IngestDB

DEFAULT_FILE = Path("data/manual/judgments.yaml")


def ingest_judgments(db: IngestDB, path: Path = DEFAULT_FILE) -> None:
    if not path.is_file():
        print(f"  ! No judgments file at {path}; skipping judgments.")
        return
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    judgments = data.get("judgments", []) if isinstance(data, dict) else data
    print(f"→ Ingesting {len(judgments)} judgments from {path}…")
    n = 0
    for idx, j in enumerate(judgments):
        try:
            date_val = j.get("date")
            if isinstance(date_val, str):
                date_val = date.fromisoformat(date_val)
            db.upsert_judgment(
                case_name=j["case_name"],
                citation=j.get("citation"),
                court=j.get("court", "Supreme Court of India"),
                date=date_val,
                summary=j.get("summary"),
                text=j["text"],
            )
            n += 1
        except (KeyError, ValueError, TypeError) as e:
            print(f"  ! Skipping judgment #{idx} (bad entry): {e}")
            continue
    db.commit()
    print(f"  ✓ {n} judgments ingested.")


def main() -> None:
    with IngestDB() as db:
        ingest_judgments(db)


if __name__ == "__main__":
    main()