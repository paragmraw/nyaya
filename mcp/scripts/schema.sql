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
create index if not exists sections_act_number_idx on sections (act_id, number);
create index if not exists sections_act_id_idx on sections (act_id);

-- Constitution articles -------------------------------------------------------
create table if not exists articles (
    id          uuid primary key default gen_random_uuid(),
    number      text not null unique,         -- e.g. '21', '21A', '32'
    title       text not null,
    text        text not null,
    part        text,
    search_tsv  tsvector generated always as (
        to_tsvector('english', coalesce(title, '') || ' ' || coalesce(text, ''))
    ) stored
);
create index if not exists articles_search_idx on articles using gin (search_tsv);
create index if not exists articles_number_idx on articles (number);

-- Constitution schedules ------------------------------------------------------
create table if not exists schedules (
    id          uuid primary key default gen_random_uuid(),
    number      int not null unique,
    title       text not null,
    text        text not null
);

-- Constitution amendments -----------------------------------------------------
create table if not exists amendments (
    id                uuid primary key default gen_random_uuid(),
    number            int not null unique,
    year              int not null,
    title             text not null,
    articles_affected text,
    date              date
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

-- ivfflat indexes need to be built after data is loaded; we create them with
-- an explicit list count. For small corpora (<10k rows) brute-force scan is
-- also fine — these indexes are a bonus, not a requirement.
create index if not exists section_embeddings_idx
    on section_embeddings using ivfflat (embedding vector_cosine_ops) with (lists = 100);
create index if not exists article_embeddings_idx
    on article_embeddings using ivfflat (embedding vector_cosine_ops) with (lists = 100);
create index if not exists judgment_embeddings_idx
    on judgment_embeddings using ivfflat (embedding vector_cosine_ops) with (lists = 100);

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