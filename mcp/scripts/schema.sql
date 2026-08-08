-- nyaya schema for Supabase / Postgres + pgvector.
-- Idempotent: safe to run multiple times.
--
-- Run via:
--   psql "$DATABASE_URL" -f scripts/schema.sql
-- or paste into the Supabase SQL editor.

-- Extensions ------------------------------------------------------------------
create extension if not exists "pgcrypto";   -- for gen_random_uuid()
create extension if not exists "vector";     -- pgvector

-- Acts ------------------------------------------------------------------------
create table if not exists acts (
    id            uuid primary key default gen_random_uuid(),
    short_name    text not null unique,
    full_name     text not null,
    year          int,
    citation      text,
    kind          text not null check (kind in ('constitution','criminal','civil','commercial','judgment')),
    source        text not null,
    source_license text,
    as_of         date
);
create index if not exists acts_kind_year_idx on acts (kind, year nulls last, short_name);

-- Chapters --------------------------------------------------------------------
create table if not exists chapters (
    id          uuid primary key default gen_random_uuid(),
    act_id      uuid not null references acts(id) on delete cascade,
    number      int not null,
    title       text not null,
    section_range text,
    unique (act_id, number)
);

-- Sections (IPC, CrPC, CPC, Evidence, BNS, BNSS, BSA, commercial acts) --------
create table if not exists sections (
    id          uuid primary key default gen_random_uuid(),
    act_id      uuid not null references acts(id) on delete cascade,
    chapter_id  uuid references chapters(id) on delete set null,
    number      text not null,                -- e.g. '302', '354A'
    title       text,
    text        text not null,
    url         text,
    search_tsv  tsvector generated always as (
        to_tsvector('english', coalesce(title, '') || ' ' || coalesce(text, ''))
    ) stored,
    unique (act_id, number)
);
create index if not exists sections_search_idx on sections using gin (search_tsv);
-- Functional index for get_sections_by_range: numeric-prefix comparison.
create index if not exists sections_act_numval_idx
    on sections (act_id, (coalesce(nullif(regexp_replace(number, '[^0-9].*$', ''), '')::int, 0)));
-- The unique (act_id, number) constraint already creates a btree index that
-- covers act_id-prefix lookups, so we no longer add the redundant
-- sections_act_number_idx / sections_act_id_idx.

-- Constitution articles -------------------------------------------------------
create table if not exists articles (
    id          uuid primary key default gen_random_uuid(),
    number      text not null unique,         -- e.g. '21', '21A', '32'
    title       text not null,
    text        text not null,
    part        text,
    source      text,                         -- provenance (optional; falls back to Python constant)
    source_license text,
    as_of       date,
    search_tsv  tsvector generated always as (
        to_tsvector('english', coalesce(title, '') || ' ' || coalesce(text, ''))
    ) stored
);
create index if not exists articles_search_idx on articles using gin (search_tsv);
-- The unique constraint on number already indexes it; no separate articles_number_idx.

-- Constitution schedules ------------------------------------------------------
create table if not exists schedules (
    id          uuid primary key default gen_random_uuid(),
    number      int not null unique,
    title       text not null,
    text        text not null,
    source      text,
    source_license text,
    as_of       date
);

-- Constitution amendments -----------------------------------------------------
create table if not exists amendments (
    id                uuid primary key default gen_random_uuid(),
    number            int not null unique,
    year              int not null,
    title             text not null,
    articles_affected text,
    date              date,
    source            text,
    source_license    text,
    as_of             date
);

-- Judgments -------------------------------------------------------------------
create table if not exists judgments (
    id          uuid primary key default gen_random_uuid(),
    case_name   text not null,
    citation    text,
    court       text not null default 'Supreme Court of India',
    date        date,
    summary     text,
    text        text not null,
    source      text,
    source_license text,
    as_of       date,
    search_tsv  tsvector generated always as (
        to_tsvector('english',
            coalesce(case_name, '') || ' ' ||
            coalesce(citation, '') || ' ' ||
            coalesce(summary, '') || ' ' ||
            coalesce(text, ''))
    ) stored
);
create index if not exists judgments_search_idx on judgments using gin (search_tsv);
create unique index if not exists judgments_case_name_idx on judgments (case_name);
create index if not exists judgments_citation_idx on judgments (citation) where citation is not null;
create index if not exists judgments_date_idx on judgments (date desc) where date is not null;

-- Article ↔ amendment junction (normalizes the articles_affected CSV column) --
-- The legacy articles_affected text column on amendments is kept for backward
-- compatibility; this junction table is the normalized form.
create table if not exists article_amendments (
    article_id    text not null,
    amendment_id  int  not null,
    primary key (article_id, amendment_id)
);
create index if not exists article_amendments_article_idx on article_amendments (article_id);
create index if not exists article_amendments_amendment_idx on article_amendments (amendment_id);

-- Cross-references ------------------------------------------------------------
-- Stores relationships like "IPC s.302 corresponds_to BNS s.103",
-- "CPC s.151 references Evidence Act s.65", "IPC s.377 repealed_by BNS s…",
create table if not exists cross_refs (
    id            uuid primary key default gen_random_uuid(),
    from_act      text not null,
    from_section  text not null,
    to_act        text not null,
    to_section    text not null,
    kind          text not null check (kind in ('repeals','replaced_by','references','corresponds_to','amends')),
    -- Dedupe so re-running build_cross_refs is idempotent: the same
    -- from→to relationship of a given kind is inserted once, not N times.
    unique (from_act, from_section, to_act, to_section, kind)
);
create index if not exists cross_refs_from_idx on cross_refs (from_act, from_section);
create index if not exists cross_refs_to_idx on cross_refs (to_act, to_section);

-- Embeddings (pgvector) -------------------------------------------------------
-- 1024 dims matches BAAI/bge-large-en-v1.5 (the nyaya embedding model).
create table if not exists section_embeddings (
    section_id uuid primary key references sections(id) on delete cascade,
    embedding  vector(1024)
);
create table if not exists article_embeddings (
    article_id uuid primary key references articles(id) on delete cascade,
    embedding  vector(1024)
);
create table if not exists judgment_embeddings (
    judgment_id uuid primary key references judgments(id) on delete cascade,
    embedding   vector(1024)
);

-- HNSW indexes (pgvector >= 0.5.0). HNSW does not need pre-training on existing
-- data the way ivfflat does, so it's safe to create before loading data and
-- gives good recall at any corpus size. For very small corpora (<10k rows)
-- brute-force is also fine — these indexes are a bonus, not a requirement.
-- Fall back to ivfflat if HNSW is unavailable (older pgvector < 0.5.0).
-- Note: ivfflat created here (before data load) will have suboptimal centroids;
-- re-run `nyaya-ingest embeddings` after data load to rebuild, or use HNSW.
do $$
begin
    begin
        create index if not exists section_embeddings_idx
            on section_embeddings using hnsw (embedding vector_cosine_ops);
    exception when feature_not_supported then
        create index if not exists section_embeddings_idx
            on section_embeddings using ivfflat (embedding vector_cosine_ops) with (lists = 100);
    end;
    begin
        create index if not exists article_embeddings_idx
            on article_embeddings using hnsw (embedding vector_cosine_ops);
    exception when feature_not_supported then
        create index if not exists article_embeddings_idx
            on article_embeddings using ivfflat (embedding vector_cosine_ops) with (lists = 100);
    end;
    begin
        create index if not exists judgment_embeddings_idx
            on judgment_embeddings using hnsw (embedding vector_cosine_ops);
    exception when feature_not_supported then
        create index if not exists judgment_embeddings_idx
            on judgment_embeddings using ivfflat (embedding vector_cosine_ops) with (lists = 100);
    end;
end $$;

-- Helpful view: unified "documents" for semantic search ----------------------
-- Makes it easy to query across sections + articles + judgments in one go.
create or replace view documents as
    select s.id::text as doc_id, 'section' as kind, a.short_name as act, s.number as ref,
           coalesce(s.title, '') as title, s.text as text
    from sections s join acts a on a.id = s.act_id
    union all
    select ar.id::text, 'article', 'Constitution', ar.number, ar.title, ar.text
    from articles ar
    union all
    select j.id::text, 'judgment', 'judgment', coalesce(j.citation, j.case_name), j.case_name, j.text
    from judgments j;

-- Security: text-length CHECK constraints (defense-in-depth against DoS) ------
-- These prevent accidental insertion of pathologically large text rows that
-- would slow down ts_headline / search operations. The limits are generous
-- (1 MB) and well above any legitimate legal text.
do $$
begin
    begin execute 'alter table sections add constraint sections_text_len check (length(text) < 1048576)';
    exception when duplicate_object then null; end;
    begin execute 'alter table articles add constraint articles_text_len check (length(text) < 1048576)';
    exception when duplicate_object then null; end;
    begin execute 'alter table judgments add constraint judgments_text_len check (length(text) < 1048576)';
    exception when duplicate_object then null; end;
    begin execute 'alter table schedules add constraint schedules_text_len check (length(text) < 1048576)';
    exception when duplicate_object then null; end;
end $$;

-- Performance: functional index for case-insensitive act lookups --------------
-- Every get_section / get_cross_refs uses lower(short_name) = lower(%s); without
-- this index, Postgres does a sequential scan on every call.
create index if not exists acts_short_name_lower_idx on acts (lower(short_name));