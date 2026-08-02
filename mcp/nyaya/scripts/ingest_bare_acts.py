"""Ingest bare acts and commercial statutes from the `mratanusarkar/Indian-Laws`
HuggingFace dataset.

IPC, Indian Evidence Act, and CPC are NOT in ACT_MAP: the HF dataset is sparse
for those (IPC has only 12 of ~511 sections). They are ingested from the
civictech-India JSON repo instead — see `ingest_civictech.py`. Sourcing each
act from exactly one place keeps the upsert order irrelevant: whichever ingest
runs last no longer overwrites better data with worse.

The remaining acts here (CrPC, commercial statutes) are well-covered in the HF
dataset. `_match_act` uses word-boundary matching so "Code of Criminal Procedure"
does NOT also match "Code of Criminal Procedure (Amendment) Act, 1980" or the
Goa CPC-extension act (a substring `in` match would wrongly ingest those).
"""

from __future__ import annotations

import re
from datetime import date

from .db import IngestDB

AS_OF = date(2026, 7, 1)
SOURCE = "mratanusarkar/Indian-Laws (HuggingFace) sourced from indiankanoon.org (public domain)"
LICENSE = "Public domain (government edicts)"

ACT_MAP: list[tuple[str, str, str, int | None, str]] = [
    ("code of criminal procedure", "CrPC", "The Code of Criminal Procedure", 1973, "criminal"),
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
        # The needle must be followed by end-of-string, a comma, or " act" —
        # NOT by "(" (which signals an amendment/extension act, e.g.
        # "Code of Criminal Procedure (Amendment) Act, 1980" or the Goa
        # CPC-extension act). Strip the leading space so " (" is caught.
        m = re.search(re.escape(needle) + r"\b", low)
        if m:
            tail = low[m.end():].lstrip()
            if tail == "" or tail.startswith(",") or tail.startswith("act"):
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
                citation=f"Act of {year}" if year else None,
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