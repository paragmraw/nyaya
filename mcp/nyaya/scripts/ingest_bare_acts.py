"""Ingest old bare acts (IPC, CrPC, CPC, Evidence) and commercial statutes
from the `mratanusarkar/Indian-Laws` HuggingFace dataset.
"""

from __future__ import annotations

import re
from datetime import date

from .db import IngestDB

AS_OF = date(2026, 7, 1)
SOURCE = "mratanusarkar/Indian-Laws (HuggingFace) sourced from indiankanoon.org (public domain)"
LICENSE = "Public domain (government edicts)"

ACT_MAP: list[tuple[str, str, str, int | None, str]] = [
    ("indian penal code", "IPC", "The Indian Penal Code", 1860, "criminal"),
    ("code of criminal procedure", "CrPC", "The Code of Criminal Procedure", 1973, "criminal"),
    ("code of civil procedure", "CPC", "The Code of Civil Procedure", 1908, "civil"),
    ("indian evidence act", "EvidenceAct", "The Indian Evidence Act", 1872, "civil"),
    ("companies act", "Companies", "The Companies Act", 2013, "commercial"),
    ("integrated goods and services tax", "IGST", "The Integrated Goods and Services Tax Act", 2017, "commercial"),
    ("central goods and services tax", "CGST", "The Central Goods and Services Tax Act", 2017, "commercial"),
    ("information technology act", "ITAct", "The Information Technology Act", 2000, "commercial"),
    ("arbitration and conciliation", "Arbitration", "The Arbitration and Conciliation Act", 1996, "commercial"),
    ("consumer protection", "ConsumerProtection", "The Consumer Protection Act", 2019, "commercial"),
]

CONTENT_PREFIX_RE = re.compile(r"^\s*Content\s*:\s*", re.IGNORECASE)


def _match_act(act_title: str) -> tuple[str, str, int | None, str] | None:
    low = act_title.lower()
    for needle, short, full, year, kind in ACT_MAP:
        if needle in low:
            return short, full, year, kind
    return None


def _load_rows():
    try:
        from datasets import load_dataset
    except ImportError as e:
        raise RuntimeError(
            "datasets not installed. Install with: pip install 'nyaya[ingest]'"
        ) from e
    ds = load_dataset("mratanusarkar/Indian-Laws", split="train")
    for row in ds:
        yield row


def ingest_bare_acts(db: IngestDB) -> None:
    print("→ Loading mratanusarkar/Indian-Laws from HuggingFace…")
    rows = _load_rows()

    act_ids: dict[str, str] = {}
    counts: dict[str, int] = {}

    for row in rows:
        act_title = row["act_title"]
        match = _match_act(act_title)
        if match is None:
            continue
        short, full, year, kind = match
        if short not in act_ids:
            act_ids[short] = db.upsert_act(
                short_name=short,
                full_name=full,
                year=year,
                citation=f"Act No. of {year}" if year else None,
                kind=kind,
                source=SOURCE,
                source_license=LICENSE,
                as_of=AS_OF,
            )
            counts[short] = 0

        section = str(row["section"]).strip()
        text = CONTENT_PREFIX_RE.sub("", str(row["law"]).strip())
        if not section or not text:
            continue
        title: str | None = None
        m = re.match(r"^(\d+[A-Z]?)\.?\s*(.*)$", section)
        if m and m.group(2):
            section = m.group(1)
            title = m.group(2).strip()
        db.upsert_section(
            act_id=act_ids[short],
            number=section,
            title=title,
            text=text,
        )
        counts[short] = counts.get(short, 0) + 1

    db.commit()
    print("✓ Bare-act ingestion complete:")
    for short, n in sorted(counts.items()):
        print(f"    {short:20s} {n:>5} sections")


def main() -> None:
    with IngestDB() as db:
        ingest_bare_acts(db)


if __name__ == "__main__":
    main()