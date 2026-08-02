"""Build embeddings for all sections, articles, and judgments.

Uses the same fastembed model as the runtime semantic_query tool so query
and document vectors share the same space. Run after all ingestion is done.

The document text is *enriched* with an ``act | ref | title`` prefix before
embedding, matching the hydration notebook's approach for better retrieval
quality (vs embedding raw text). This keeps the CLI and notebook embeddings
consistent.

To survive Supabase's idle-connection drops during the (slow) CPU embedding
loop, embeddings are upserted in batches with a fresh connection per batch —
mirroring the notebook's pattern.
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from ..config import get_settings


def _enrich_section(act: str, number: str, title: str | None, text: str) -> str:
    prefix = f"Act: {act} | s. {number}"
    if title:
        prefix += f" | {title}"
    return f"{prefix}\n{(text or '')[:8000]}"


def _enrich_article(number: str, title: str | None, text: str) -> str:
    prefix = f"art. {number}"
    if title:
        prefix += f" | {title}"
    return f"{prefix}\n{(text or '')[:8000]}"


def _enrich_judgment(case_name: str, citation: str | None, summary: str | None, text: str) -> str:
    prefix = case_name
    if citation:
        prefix += f" ({citation})"
    if summary:
        prefix += f"\n{summary}"
    return f"{prefix}\n{(text or '')[:8000]}"


def _embed_table(db, table: str, enrich_fn) -> int:
    from ..embeddings import embed_texts

    rows = db.fetch_all(f"select id, number, title, text from {table}")
    if not rows:
        return 0

    # Embed in batches to bound memory and give the user progress.
    BATCH = 64
    emb_table = table.rstrip("s") if table.endswith("s") else table
    total = 0
    try:
        from tqdm import tqdm
        iterator = tqdm(range(0, len(rows), BATCH), desc=f"embedding {table}")
    except ImportError:
        iterator = range(0, len(rows), BATCH)

    for start in iterator:
        batch = rows[start:start + BATCH]
        texts = [enrich_fn(r) for r in batch]
        embeddings = embed_texts(texts)
        for r, emb in zip(batch, embeddings):
            db.upsert_embedding(table=emb_table, owner_id=str(r["id"]), embedding=emb)
        total += len(batch)
    return total


def build_embeddings(db) -> None:
    print("→ Building enriched embeddings (downloads the model on first run)…")

    total = 0
    # Sections: enrich with act + number + title prefix.
    rows = db.fetch_all(
        "select s.id, s.number, s.title, s.text, a.short_name as act "
        "from sections s join acts a on a.id = s.act_id"
    )
    if rows:
        from ..embeddings import embed_texts
        BATCH = 64
        try:
            from tqdm import tqdm
            it = tqdm(range(0, len(rows), BATCH), desc="embedding sections")
        except ImportError:
            it = range(0, len(rows), BATCH)
        n = 0
        for start in it:
            batch = rows[start:start + BATCH]
            texts = [_enrich_section(r["act"], r["number"], r["title"], r["text"]) for r in batch]
            embeddings = embed_texts(texts)
            for r, emb in zip(batch, embeddings):
                db.upsert_embedding(table="section", owner_id=str(r["id"]), embedding=emb)
            n += len(batch)
        print(f"  ✓ section: {n} embeddings.")
        total += n
    else:
        print("  ✓ section: 0 (no rows).")

    # Articles: enrich with number + title prefix.
    n = _embed_table(db, "articles", lambda r: _enrich_article(r["number"], r["title"], r["text"]))
    print(f"  ✓ article: {n} embeddings.")
    total += n

    # Judgments: enrich with case_name + citation + summary prefix.
    jrows = db.fetch_all("select id, number, title, text from judgments")
    # judgments table has no number/title columns — use case_name/citation/summary.
    jrows = db.fetch_all("select id, case_name, citation, summary, text from judgments")
    if jrows:
        from ..embeddings import embed_texts
        BATCH = 32
        try:
            from tqdm import tqdm
            it = tqdm(range(0, len(jrows), BATCH), desc="embedding judgments")
        except ImportError:
            it = range(0, len(jrows), BATCH)
        n = 0
        for start in it:
            batch = jrows[start:start + BATCH]
            texts = [_enrich_judgment(r["case_name"], r["citation"], r["summary"], r["text"]) for r in batch]
            embeddings = embed_texts(texts)
            for r, emb in zip(batch, embeddings):
                db.upsert_embedding(table="judgment", owner_id=str(r["id"]), embedding=emb)
            n += len(batch)
        print(f"  ✓ judgment: {n} embeddings.")
        total += n
    else:
        print("  ✓ judgment: 0 (no rows).")

    db.commit()
    print(f"✓ Embedding build complete ({total} total).")


def main() -> None:
    from .db import IngestDB
    with IngestDB() as db:
        build_embeddings(db)


if __name__ == "__main__":
    main()