"""Build embeddings for all sections, articles, and judgments.

Uses the same fastembed model as the runtime semantic_query tool so query
and document vectors share the same space. Run after all ingestion is done.

The document text is *enriched* with an ``act | ref | title`` prefix before
embedding, matching the hydration notebook's approach for better retrieval
quality. The enrichment and truncation logic mirrors the notebook exactly so
CLI and notebook embeddings are identical.

To survive Supabase's idle-connection drops during the (slow) CPU embedding
loop, embeddings are upserted in batches with a **fresh connection per batch**
— mirroring the notebook's pattern. Each batch is committed independently so
a mid-run failure preserves prior batches.
"""

from __future__ import annotations

import psycopg
from psycopg.rows import dict_row

from ..config import get_settings

MAX_CHARS = 8000


def _enrich_section(act: str, number: str, title: str | None, text: str) -> str:
    head = f"Act: {act} | s. {number}"
    if title:
        head += f" | {title}"
    return (head + "\n" + (text or ""))[:MAX_CHARS]


def _enrich_article(number: str, title: str | None, text: str) -> str:
    head = f"art. {number}"
    if title:
        head += f" | {title}"
    return (head + "\n" + (text or ""))[:MAX_CHARS]


def _enrich_judgment(case_name: str, citation: str | None, summary: str | None, text: str) -> str:
    parts = [case_name]
    if citation:
        parts.append(f"({citation})")
    head = " ".join(parts)
    if summary:
        head += f"\n{summary}"
    return (head + "\n" + (text or ""))[:MAX_CHARS]


def _embed_and_upsert(rows: list, enrich_fn, emb_table: str, db_url: str,
                      batch_size: int = 64, desc: str = "embedding") -> int:
    """Embed all rows and upsert in batches with a fresh connection per batch.

    Phase 1: embed all texts in memory (fastembed handles batching internally).
    Phase 2: upsert per batch with a fresh psycopg.connect, committing each.
    This mirrors the notebook's two-phase pattern and survives Supabase idle drops.
    """
    from ..embeddings import embed_texts

    if not rows:
        return 0
    texts = [enrich_fn(r) for r in rows]
    vectors = embed_texts(texts)

    try:
        from tqdm import tqdm
        iterator = tqdm(range(0, len(rows), batch_size), desc=desc)
    except ImportError:
        iterator = range(0, len(rows), batch_size)

    total = 0
    for start in iterator:
        batch_rows = rows[start:start + batch_size]
        batch_vecs = vectors[start:start + batch_size]
        # Fresh connection per batch — survives Supabase idle drops.
        conn = psycopg.connect(db_url, row_factory=dict_row, connect_timeout=15)
        try:
            with conn.cursor() as cur:
                for r, emb in zip(batch_rows, batch_vecs):
                    cur.execute(
                        f"insert into {emb_table}_embeddings ({emb_table}_id, embedding) "
                        f"values (%s, %s::vector) "
                        f"on conflict ({emb_table}_id) do update set embedding = excluded.embedding",
                        (str(r["id"]), emb),
                    )
            conn.commit()
            total += len(batch_rows)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()
    return total


def build_embeddings(db) -> None:
    print("→ Building enriched embeddings (downloads the model on first run)…")
    db_url = get_settings().database_url
    total = 0

    # Sections: enrich with act + number + title prefix.
    sec_rows = db.fetch_all(
        "select s.id, s.number, s.title, s.text, a.short_name as act "
        "from sections s join acts a on a.id = s.act_id"
    )
    n = _embed_and_upsert(sec_rows, lambda r: _enrich_section(r["act"], r["number"], r["title"], r["text"]),
                          "section", db_url, batch_size=64, desc="embedding sections")
    print(f"  ✓ section: {n} embeddings.")
    total += n

    # Articles: enrich with number + title prefix.
    art_rows = db.fetch_all("select id, number, title, text from articles")
    n = _embed_and_upsert(art_rows, lambda r: _enrich_article(r["number"], r["title"], r["text"]),
                          "article", db_url, batch_size=64, desc="embedding articles")
    print(f"  ✓ article: {n} embeddings.")
    total += n

    # Judgments: enrich with case_name + citation + summary prefix.
    jud_rows = db.fetch_all("select id, case_name, citation, summary, text from judgments")
    n = _embed_and_upsert(jud_rows, lambda r: _enrich_judgment(r["case_name"], r["citation"], r["summary"], r["text"]),
                          "judgment", db_url, batch_size=32, desc="embedding judgments")
    print(f"  ✓ judgment: {n} embeddings.")
    total += n

    print(f"✓ Embedding build complete ({total} total).")


def main() -> None:
    from .db import IngestDB
    with IngestDB() as db:
        build_embeddings(db)


if __name__ == "__main__":
    main()