"""Build embeddings for all sections, articles, and judgments.

Uses the same fastembed model as the runtime semantic_query tool so query
and document vectors share the same space. Run after all ingestion is done.
"""

from __future__ import annotations

from .db import IngestDB


def _embed_table(db: IngestDB, table: str, id_col: str, text_col: str) -> int:
    from ..embeddings import embed_texts

    rows = db.fetch_all(f"select {id_col} as id, {text_col} as text from {table}")
    if not rows:
        return 0
    texts = [(r["text"] or "")[:8000] for r in rows]
    embeddings = embed_texts(texts)
    emb_table = table.rstrip("s") if table.endswith("s") else table
    for r, emb in zip(rows, embeddings):
        db.upsert_embedding(table=emb_table, owner_id=str(r["id"]), embedding=emb)
    return len(rows)


def build_embeddings(db: IngestDB) -> None:
    print("→ Building embeddings (this downloads the model on first run, ~130MB)…")
    total = 0
    for table, id_col, text_col in [
        ("sections", "id", "text"),
        ("articles", "id", "text"),
        ("judgments", "id", "text"),
    ]:
        emb_table = table.rstrip("s") if table.endswith("s") else table
        n = _embed_table(db, table, id_col, text_col)
        print(f"  ✓ {emb_table}: {n} embeddings.")
        total += n
    db.commit()
    print(f"✓ Embedding build complete ({total} total).")


def main() -> None:
    with IngestDB() as db:
        build_embeddings(db)


if __name__ == "__main__":
    main()