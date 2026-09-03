-- nyaya v0.2 unified schema (single source of truth — applied by the
-- "## 3. Apply schema" cell of notebooks/hydrate.ipynb, which reads this file).
--
-- WARNING: this file contains `drop table ... cascade` statements. Running the
-- WHOLE file against a populated database (e.g. `psql -f mcp/schema.sql`)
-- DESTROYS the corpus. Two safe ways to apply:
--   * full re-hydration via the notebook (the schema cell rebuilds the
--     corpus from scratch), or
--   * the additive migration block at the bottom ONLY (idempotent
--     `if not exists` forms; touches no existing data) — paste just that
--     block's two statements into a psql session against the deployed DB.

-- Extensions
create extension if not exists vector;
create extension if not exists pgcrypto;

-- Drop old tables (the 1024-d embeddings are useless with the new 2048-d model)
drop table if exists section_embeddings cascade;
drop table if exists article_embeddings cascade;
drop table if exists judgment_embeddings cascade;
drop table if exists cross_refs cascade;
drop table if exists article_amendments cascade;
drop table if exists judgments cascade;
drop table if exists amendments cascade;
drop table if exists schedules cascade;
drop table if exists articles cascade;
drop table if exists sections cascade;
drop table if exists chapters cascade;
drop table if exists acts cascade;
drop table if exists documents cascade;
drop view if exists documents;

-- Acts: relational metadata about each statute
create table acts (
    id            uuid primary key default gen_random_uuid(),
    short_name    text not null unique,
    full_name     text,
    year          int,
    citation      text,
    kind          text check (kind in ('constitution','criminal','civil','commercial','judgment')),
    source        text,
    source_license text,
    as_of         date
);

-- Unified documents table: sections, articles, judgments, schedules, amendments
create table documents (
    id          uuid primary key default gen_random_uuid(),
    act_id      uuid references acts(id) on delete cascade,
    kind        text not null check (kind in ('section','article','judgment','schedule','amendment')),
    ref         text not null,
    title       text,
    text        text not null,
    metadata    jsonb not null default '{}',
    embedding   vector(2048),
    created_at  timestamptz default now()
);
create unique index if not exists documents_act_ref_idx
    on documents (act_id, ref) where act_id is not null;
create unique index if not exists documents_kind_ref_idx
    on documents (kind, ref) where act_id is null;
-- ANN indexing (intentional no-index): embeddings are vector(2048), above
-- pgvector's 2000-d limit for BOTH ivfflat and HNSW, so no ANN index exists
-- and semantic_search falls back to a sequential scan with brute-force
-- cosine similarity. At the current corpus scale (~3.8k rows) that scan is
-- sub-100ms — the right trade-off today. (An earlier comment here claimed an
-- ivfflat index was "created after data load" — no such statement ever
-- existed anywhere; the comment was false.)
--
-- Future scaling path (only if the corpus order-of-magnitude grows, and with
-- pgvector >= 0.7): index a half-precision projection, which lifts the
-- dimension cap to 4000, via a halfvec expression index with HNSW:
--
--     create index documents_embedding_hnsw_idx on documents
--         using hnsw ((embedding::halfvec(2048)) halfvec_cosine_ops);
--
-- and rewrite the ANN ORDER BY as
--     order by d.embedding::halfvec(2048) <=> %s::halfvec(2048)
-- so the planner can use the expression index.
create index if not exists documents_kind_idx on documents (kind);
create index if not exists documents_act_idx on documents (act_id);
create index if not exists documents_ref_idx on documents (kind, lower(ref));

-- Cross-references between documents (UUID-keyed for referential integrity)
create table cross_refs (
    id          uuid primary key default gen_random_uuid(),
    from_doc    uuid references documents(id) on delete cascade,
    to_doc      uuid references documents(id) on delete cascade,
    kind        text check (kind in ('repeals','replaced_by','references','corresponds_to','amends')),
    unique (from_doc, to_doc, kind)
);
create index if not exists cross_refs_from_idx on cross_refs (from_doc);
create index if not exists cross_refs_to_idx on cross_refs (to_doc);

-- ---------------------------------------------------------------------------
-- Additive migration (idempotent): ref_num stored generated column + index.
--
-- db.list_sections previously ordered/filtered on the expression
--     coalesce(nullif(regexp_replace(d.ref, '[^0-9].*$', ''), '')::int, 0)
-- recomputed per row on every page. The generated column below is that exact
-- expression (byte-for-byte in semantics; only the unqualified column name
-- differs, as a generated column cannot reference the table alias). The
-- nullif/coalesce wrapper keeps rows whose ref has no leading digits
-- (e.g. 'AIR 1973 SC 1461') and would otherwise fail the int cast; they get
-- ref_num = 0, matching the historic ordering.
-- This block (the two statements below) is the ONLY part of this file that is
-- safe to apply to a populated database — see the WARNING at the top: a full
-- `psql -f mcp/schema.sql` run's drop-table section would destroy the corpus.
-- ---------------------------------------------------------------------------
alter table documents add column if not exists ref_num int
    generated always as (coalesce(nullif(regexp_replace(ref, '[^0-9].*$', ''), '')::int, 0)) stored;
create index if not exists documents_act_ref_num_idx on documents (act_id, ref_num);