"""Ingest IPC, Indian Evidence Act, and CPC from the
`civictech-India/Indian-Law-Penal-Code-Json` GitHub repo.

The HuggingFace `mratanusarkar/Indian-Laws` dataset is sparse for these three
acts (IPC has only 12 of ~511 sections). This module loads the full verbatim
section text from the civictech repo, which packages the public-domain
government text as JSON.

The three JSON files use three slightly different schemas (IPC: `Section`/
`section_title`/`section_desc`/`chapter`/`chapter_title`; IEA: `section`/
`section_title`/`section_desc`/`chapter`; CPC: `section`/`title`/`description`,
no chapter). A normalizer maps them to the common (number, title, text,
chapter?) shape.
"""

from __future__ import annotations

import json
from datetime import date

import httpx

from .db import IngestDB

AS_OF = date(2026, 7, 1)
SOURCE = "civictech-India/Indian-Law-Penal-Code-Json (public-domain government edicts)"
LICENSE = "Public domain (government edicts)"
BASE = "https://raw.githubusercontent.com/civictech-India/Indian-Law-Penal-Code-Json/main"

ACTS: list[dict] = [
    {
        "short_name": "IPC",
        "full_name": "The Indian Penal Code",
        "year": 1860,
        "citation": "Act No. 45 of 1860",
        "kind": "criminal",
        "url": f"{BASE}/ipc.json",
        "num_key": "Section",
        "title_key": "section_title",
        "text_key": "section_desc",
        "has_chapter": True,
    },
    {
        "short_name": "EvidenceAct",
        "full_name": "The Indian Evidence Act",
        "year": 1872,
        "citation": "Act No. 1 of 1872",
        "kind": "civil",
        "url": f"{BASE}/iea.json",
        "num_key": "section",
        "title_key": "section_title",
        "text_key": "section_desc",
        "has_chapter": True,
    },
    {
        "short_name": "CPC",
        "full_name": "The Code of Civil Procedure",
        "year": 1908,
        "citation": "Act No. 5 of 1908",
        "kind": "civil",
        "url": f"{BASE}/cpc.json",
        "num_key": "section",
        "title_key": "title",
        "text_key": "description",
        "has_chapter": False,
    },
]


def _download(url: str) -> list[dict]:
    with httpx.Client(follow_redirects=True, timeout=60.0) as client:
        r = client.get(url, headers={"User-Agent": "nyaya-ingest/0.1 (+https://github.com/your-org/nyaya)"})
        r.raise_for_status()
        return r.json()


def _normalize(row: dict, act: dict) -> dict:
    number = str(row[act["num_key"]]).strip().rstrip(".")
    title = (str(row.get(act["title_key"]) or "")).strip() or None
    text = (str(row.get(act["text_key"]) or "")).strip()
    chapter_num: int | None = None
    chapter_title: str | None = None
    if act["has_chapter"] and row.get("chapter") is not None:
        chapter_num = int(row["chapter"])
        ct = str(row.get("chapter_title") or "").strip()
        if ct:
            chapter_title = ct[0].upper() + ct[1:]
    return {
        "number": number,
        "title": title,
        "text": text,
        "chapter_num": chapter_num,
        "chapter_title": chapter_title,
    }


def ingest_civictech(db: IngestDB) -> None:
    for act in ACTS:
        print(f"→ Ingesting {act['short_name']} from civictech…")
        rows = _download(act["url"])
        act_id = db.upsert_act(
            short_name=act["short_name"],
            full_name=act["full_name"],
            year=act["year"],
            citation=act["citation"],
            kind=act["kind"],
            source=SOURCE,
            source_license=LICENSE,
            as_of=AS_OF,
        )
        chapter_ids: dict[int, str] = {}
        n = 0
        for raw in rows:
            r = _normalize(raw, act)
            if not r["number"] or not r["text"]:
                continue
            chapter_id = None
            if r["chapter_num"] is not None:
                if r["chapter_num"] not in chapter_ids:
                    chapter_ids[r["chapter_num"]] = db.upsert_chapter(
                        act_id=act_id,
                        number=r["chapter_num"],
                        title=r["chapter_title"] or f"Chapter {r['chapter_num']}",
                    )
                chapter_id = chapter_ids[r["chapter_num"]]
            db.upsert_section(
                act_id=act_id,
                number=r["number"],
                title=r["title"],
                text=r["text"],
                chapter_id=chapter_id,
            )
            n += 1
        db.commit()
        print(f"  ✓ {n} sections ingested ({len(chapter_ids)} chapters).")


def main() -> None:
    with IngestDB() as db:
        ingest_civictech(db)


if __name__ == "__main__":
    main()