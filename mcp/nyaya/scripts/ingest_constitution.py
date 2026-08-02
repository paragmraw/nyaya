"""Ingest the Constitution of India.

Source: the `indianconstitution` PyPI package (Apache-2.0), which bundles
Articles 1–395 as a JSON file. Schedules and amendments are loaded from
data/manual/ (curated from PRS PDFs, CC BY 4.0, and the official MoLJ PDF,
which is public domain).
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

from .db import IngestDB

AS_OF = date(2026, 7, 1)
SOURCE_ARTICLES = "Vikhram-S/IndianConstitution (Apache-2.0)"

PART_RE = re.compile(r"PART\s+[IVXLC]+", re.IGNORECASE)


def _load_articles() -> list[dict]:
    try:
        from indianconstitution import Constitution
    except ImportError as e:
        raise RuntimeError(
            "indianconstitution not installed. Install with: pip install 'nyaya[ingest]'"
        ) from e

    import tempfile, os
    c = Constitution()
    tmp = tempfile.NamedTemporaryFile(suffix=".json", delete=False, mode="w", encoding="utf-8")
    tmp_path = tmp.name
    tmp.close()
    c.export("json", tmp_path)
    with open(tmp_path, encoding="utf-8") as f:
        data = json.load(f)
    os.unlink(tmp_path)
    if isinstance(data, dict) and "articles" in data:
        data = data["articles"]
    return data


def _article_part(title: str, prev_part: str | None) -> str | None:
    m = PART_RE.search(title or "")
    if m:
        return m.group(0).upper()
    return prev_part


def ingest_constitution(db: IngestDB) -> None:
    print("→ Ingesting Constitution articles…")
    db.upsert_act(
        short_name="Constitution",
        full_name="The Constitution of India",
        year=1950,
        citation="26 Nov 1949 (commenced 26 Jan 1950)",
        kind="constitution",
        source=SOURCE_ARTICLES,
        source_license="Apache-2.0",
        as_of=AS_OF,
    )

    articles = _load_articles()
    current_part: str | None = None
    n = 0
    for art in articles:
        number = str(art.get("number") or "").strip()
        title = (art.get("title") or "").strip()
        text = (art.get("content") or art.get("text") or "").strip()
        if not number or not text:
            continue
        if number == "0":
            number = "Preamble"
            title = title or "Preamble"
        part = art.get("part") or _article_part(title, current_part)
        if part:
            current_part = part
        db.upsert_article(
            number=number,
            title=title or f"Article {number}",
            text=text,
            part=current_part,
        )
        n += 1
    print(f"  ✓ {n} articles ingested (Preamble + Articles 1–395).")

    schedules_dir = Path("data/manual/schedules")
    if schedules_dir.is_dir():
        print("→ Ingesting Constitution schedules…")
        ns = 0
        for path in sorted(schedules_dir.glob("*.md")):
            stem = path.stem
            parts = stem.split("_", 1)
            try:
                num = int(parts[0])
            except (ValueError, IndexError):
                continue
            title = parts[1].replace("_", " ").title() if len(parts) > 1 else f"Schedule {num}"
            text = path.read_text(encoding="utf-8").strip()
            db.upsert_schedule(number=num, title=title, text=text)
            ns += 1
        print(f"  ✓ {ns} schedules ingested.")
    else:
        print(f"  ! No schedules directory at {schedules_dir}; skipping schedules.")

    amendments_file = Path("data/manual/amendments.json")
    if amendments_file.is_file():
        print("→ Ingesting Constitution amendments…")
        items = json.loads(amendments_file.read_text(encoding="utf-8"))
        for item in items:
            db.upsert_amendment(
                number=int(item["number"]),
                year=int(item["year"]),
                title=item["title"],
                articles_affected=item.get("articles_affected"),
                date=date.fromisoformat(item["date"]) if item.get("date") else None,
            )
        print(f"  ✓ {len(items)} amendments ingested.")
    else:
        print(f"  ! No amendments file at {amendments_file}; skipping amendments.")

    db.commit()
    print("✓ Constitution ingestion complete.")


def main() -> None:
    with IngestDB() as db:
        ingest_constitution(db)


if __name__ == "__main__":
    main()