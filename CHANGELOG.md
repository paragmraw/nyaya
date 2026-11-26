# Changelog

All notable changes to nyaya will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [2.1.0] - 2026-11-26

Coverage, statutory-depth, retrieval, and practitioner-workflow expansion (**Nyaya MCP v2.1**), including the `corpus-v2026.11.25-p1` retrieval and corpus quality overhaul (`1,569` central acts, `8,544` state acts, `12,152` regulator instruments, `40,353` core provisions, `38,292` SC judgments, `20,696,054` HC judgments, `813,168+` tribunal orders, `615+` citation edges).

### Added
- **Expanded MCP v2.1 Surface (15 Tools · 8 Resources · 4 Prompts):**
  - Added 3 new read-only MCP tools in `mcp/nyaya/tools/`: `get_provisions_batch` (partial-success batch fetch of up to 50 provisions with optional `as_of`), `get_judgments_batch` (partial-success batch fetch of up to 20 judgments enforcing `check_publication_ban`), and `get_cause_list` (local `cause_lists` SQLite search or BYO live docket adapter when `live=True`).
  - Added optional `as_of='YYYY-MM-DD'` point-in-time statutory reconstruction (`mcp/nyaya/storage/point_in_time.py`) to `get_provision` and `get_provisions_batch`.
  - Added `ITAct1961 <-> ITAct2025` (**33** income-tax concordance mappings in `tax_concordance.yaml`, `ITAct2025 s.536` savings) and BNSS First Schedule `offence_classification` metadata to `compare_provisions`, while expanding `criminal_concordance.yaml` to **148** verified mappings.
- **Expanded Corpus, Courts & LanceDB Tables:**
  - Expanded `mcp/nyaya/ingest/data/courts_directory.yaml` to **63** entries, growing from 15 to **24** Tribunals & Consumer Commissions (`NCDRC`, `SCDRC`, `DCDRC`, `DRAT`, `AFT`, `GSTAT`, `RCT`, `CIC`, `SEBI_ADJ`), plus `cpc_first_schedule.yaml` (**58 CPC Orders**, `104` rules) and `19` pinned entries in `sources.lock.yaml` (adding `ncdrc_jagriti`, `nclat_orders`, `egazette_rules`, `sci_cause_lists`).
  - Added `tribunal_chunks` and `cad_debates` (Constituent Assembly Debates) LanceDB tables (5 total LanceDB tables) with `hot_hc_limit=500_000`, and added the `cause_lists` table to `nyaya-core.sqlite`.
- **Opt-in OCR, Hybrid Retrieval & Live Docket Settings (`mcp/nyaya/config.py`):**
  - Added optional disabled-by-default environment variables: `NYAYA_ENABLE_OCR` (Apache-2.0 `TesseractOcrEngine` / `RapidOcrEngine` in `mcp/nyaya/ingest/extract/ocr.py`), `NYAYA_HYBRID_SEARCH` / `NYAYA_EMBEDDING_ENDPOINT` / `NYAYA_EMBEDDING_MODEL` (`mcp/nyaya/retrieval/hybrid.py` BM25 + vector RRF with zero-dependency `HashedNgramEmbeddingProvider` fallback), and `NYAYA_LIVE_DOCKET_API_URL` / `NYAYA_LIVE_DOCKET_API_KEY` (`mcp/nyaya/storage/live_docket.py`).
- **Release Automation & MCP Registry Metadata:** Added `.github/workflows/release-pypi.yml` (Trusted Publishing to PyPI and Official MCP Registry via `server.json` on `v*` tags).
- **Curated bare-act provisions (`mcp/nyaya/ingest/data/curated_statute_provisions.yaml`):** loaded by `load_curated_provisions_yaml()` in `mcp/nyaya/ingest/fetchers/official_statutes.py` and overlaid in `ingest/loader.py`, overriding truncated Vaquill entries and adding CrPC provisions.
- **`court_display_name()` and `normalize_jurisdiction()` (`mcp/nyaya/citations.py`):** Dattam court codes (`_` / `~` forms) resolve to human-readable court names on judgment cards; full state/UT names resolve case-insensitively to canonical codes in jurisdiction filters (SQLite, LanceDB, manifest pruning).
- **Widened citation parser:** CPC Order/Rule plural and inverted forms, `YYYY:INSC:N` neutral citations, and AIR court suffixes beyond `SC`.
- **Query analysis:** practitioner shorthand expansion (`_PRACTITIONER_SHORTHANDS`) and alias-aware `act` filters in `QueryAnalyzer`.
- **Counsel-aware `search_within` (`mcp/nyaya/retrieval/paragraph_bm25.py`):** counsel-appearance and counsel-submission paragraphs without holding cues are suppressed.
- **Extra quote normalization in `verify_citations`:** curly quotes, Markdown markers, and India Code superscript footnote markers are normalized before quote matching.

### Changed
- **Version Standardization (`2.1.0`):** Standardized canonical package/registry versions to `2.1.0` across `mcp/nyaya/__init__.py`, `chat/pyproject.toml`, `web/package.json`, and `server.json`, and updated `chat/nyaya_chat/tools_layer/spec.py` `TOOL_SCHEMAS` to all 15 MCP v2.1 tools.
- **`search_judgments`:** cross-High-Court diversity (round-robin interleave by court in `lexical_engine.py`; round-robin partition selection across courts, newest year first, in `duckdb_store.py`), card de-duplication and metadata enrichment, and strict anticipatory-bail filtering (`CrPC s. 438` / `BNSS s. 482` signals).
- **Statute and LanceDB OR-fallback precision:** word-boundary token matching with inflection stems, length-scaled minimum token matches, and title/bigram scoring; in-force provisions are preferred when `status="all"`.
- **`verify_citations`:** unindexed CPC Order/Rule citations now return `NOT_FOUND_IN_CORPUS` with a `coverage_note` instead of a misleading match.
- **PDF extraction (`mcp/nyaya/storage/pdf_fetcher.py`):** strips SCR running headers/footers, same-line SCR judge footers, and standalone page numbers; repairs ligature space-dropouts (`_repair_ligature_splits`) and minor year/parenthesis/`v.` word-joins.
- **Ingest hygiene (`mcp/nyaya/ingest/`):** Vaquill provision text is cleaned of boilerplate headers, Markdown, repeated headings, and India Code footnotes; chapter titles are extracted; the best-scoring multi-part stem is chosen; BNS/BNSS sub-clause rows inherit parent provision text and chapter and are emitted only when the parent exists; Act commencement/repeal dates come from the act rows instead of hard-coded values; per-source record counts are computed and FTS5 tables rebuilt and integrity-checked.
- **`nyaya-ingest build-sqlite`:** no longer loads the ~20.8M High Court rows (`include_hc=False`); the HC row total is carried as a count. `build-lance-fts` selects hot HC rows round-robin across courts (`hot_hc_limit=500_000`).
- **`Dockerfile` Stage 2b:** added `curl --retry 4 --retry-delay 5 --retry-all-errors` when downloading `nyaya-core.sqlite.zst` to handle transient upstream errors.

### Fixed
- Year filter applied to equivalent-provision lookups across High Courts, and recall when a query spans multiple High Courts.
- Same-line SCR judge footers leaking into extracted judgment text.
- HC search ordering when the Parquet partition lacks a `decision_date` column.

### Removed
- Removed unused legacy `.trivyignore`, the `hf-dataset` publish target (`mcp/nyaya/ingest/publish.py`, `mcp/nyaya/ingest/cli.py`, `.github/workflows/ingest-refresh.yml`), and dead config/model/prompt/fetcher aliases.

## [2.0.0] - 2026-11-22

Full monorepo re-architecture to a **database-free embedded and object-storage architecture (pure-lexical, metadata & graph)** and **Nyaya MCP v2.0 (`nyaya-mcp`)**.

### Added
- **Embedded + Object-Storage Hybrid Retrieval (`mcp/nyaya/storage/`, `mcp/nyaya/retrieval/`):**
  - Baked read-only immutable SQLite core (`nyaya-core.sqlite`) with FTS5 (`porter unicode61 remove_diacritics 2`; tokenchars `'.-/()'` for `provisions_fts`, `'.-:/()'` for `judgments_meta_fts`, so neutral citations like `2024:DHC:1234` stay searchable), Constitutional Amendments 1–106, IPC/CrPC/IEA ↔ BNS/BNSS/BSA concordance + substantive diffs, Judicial Invalidations registry, SC metadata, citation graph edges, and courts directory.
  - Railway Storage Bucket (Tigris S3) cold tiers: 3 LanceDB native Rust BM25 FTS tables (`sc_chunks`, `hc_hot_chunks`, `state_acts_regulators`) and DuckDB `httpfs` over compacted ZSTD Parquet partitions (17.77M HC, 81M DDL District Courts, 15 Tribunals, 12 Regulators, SC mirror) with `manifest.json` partition pruning.
  - Direct anonymous AWS Open Data PDF extraction via Apache-2.0 `pypdfium2` (`pdf_fetcher.py`) with strict bucket/key regex allowlist, `follow_redirects=False`, 15 MB / 500-page caps, `%PDF-` magic check, `chars_per_page >= 50` scanned-PDF detection, ephemeral `ParagraphBM25`, and a 256 MB disk zstd LRU cache.
  - Pure-lexical `RetrievalEngine` (`QueryAnalyzer`, `LexicalEngine`, weighted RRF `k=60`, court-hierarchy prior, exact-match top-3 floor).
- **MCP v2.0 Surface (12 Tools · 8 Resources · 4 Prompts):**
  - 12 tools: `search_statutes`, `get_provision`, `browse_statute`, `compare_provisions`, `cross_reference`, `get_provision_history`, `search_judgments`, `get_judgment`, `trace_precedent`, `judgment_analytics`, `verify_citations`, `corpus_status` (enforcing `openWorldHint` split, `StatutoryValidity` envelopes, `precedent_status="unverified_automated"`, and `commercial_safe_only`).
  - 8 `nyaya://` resources (`corpus/status`, `statutes`, `statute/{act}`, `provision/{act}/{ref}`, `amendment/{act}/{number}`, `judgment/{identifier}`, `judgment/{identifier}/paragraphs/{start}-{end}`, `courts`).
  - 4 practitioner/student MCP prompts (`audit_legal_draft`, `transition_criminal_code`, `precedent_research_memo`, `irac_case_brief`).
- **Dual Transport & CLI (`mcp/nyaya/{cli,mcp_app,http_app}.py`):** Renamed PyPI distribution to `nyaya-mcp` with `nyaya-mcp`, `nyaya`, and `nyaya-ingest` scripts supporting local zero-secret `stdio` (`uvx nyaya-mcp`), `sync`, and stateless `--transport http`.
- **Shared Indian Citation Parser (`mcp/nyaya/citations.py`):** Priority-ordered parser covering 16-char CNRs, `INSC`, HC neutral citations (`2024:DHC:1234`), AIR, SCC, SCR, CPC Orders/Rules, Constitution Articles/Schedules/Amendments, and chained statute sections.
- **Ingestion CLI & Automated Legal Integrity Gate (`mcp/nyaya/ingest/`):** `nyaya-ingest` (`sync --tier daily|static`, `build-sqlite`, `build-lance-fts`, `compact-parquet`, `verify-gate`, `publish`), verified reference YAML tables, `.github/workflows/ingest-refresh.yml`, and root [`NOTICE`](NOTICE).
- **CI/CD & Security Hygiene:** Added `mypy` gate on `chat/nyaya_chat/`, `stdio` JSON-RPC pipe test (12 tools, zero stdout pollution), offline `retrieval_eval.py`, zero-secret hardened Docker smoke test (`--read-only --memory=1g`), `chat/` `pip-audit` job, and lockfile copyleft/Postgres ban check in `.github/workflows/security.yml`.

### Changed
- **Container & Infrastructure:** Migrated `Dockerfile` from Alpine to digest-pinned `python:3.12-slim-bookworm` with pre-installed `/app/duckdb_ext` `httpfs`, installed `nyaya-mcp` console scripts (`nyaya-mcp`, `nyaya`, `nyaya-ingest`) and package data into site-packages with `/app/web/out` static fallback, and added a dual-mode `data-stage` (`NYAYA_CORE_SQLITE_URL` + SHA-256 or offline `tests.fixtures.mini_corpus.builder`). Updated `docker-compose.yml` to mount disk-backed `nyaya_cache:/var/cache/nyaya` (preventing Linux `tmpfs` OOM under `mem_limit: 1g`). Updated `.railway/railway.ts` for Railway Storage Bucket variables and `asia-southeast1` region guidance.
- **v2 REST API (`mcp/nyaya/rest.py`):** Migrated endpoints to `/api/corpus-status`, `/api/acts`, `/api/judgments`, `/api/analytics`, `/api/tools`, and `/api/health-summary`.
- **Chat Sub-App (`chat/`):** Migrated `TOOL_SPECS` to 8 v2 tools, delegated citation verification to shared `nyaya.citations`, updated `cleaning.py` for parameterized `<corpus_text ...>` tags, and expanded `eval/golden.jsonl` from 25 to 90 cases.
- **Web Frontend (`web/`):** Bumped `API_VERSION` to `2.0.0`, hydrated against v2 REST endpoints, updated `McpConfig` with `Local (uvx stdio)` + `Remote (HTTP)` tabs, added commercial-safe/category filters and academic CC-BY-NC notice, and purged v0.2 Postgres/Supabase/LLM-embedding copy.
- **Legal & Security Docs:** Replaced US public-domain claims in [`TRUSTED_SOURCES.md`](TRUSTED_SOURCES.md) with Indian Copyright Act 1957 s. 52(1)(q) + *EBC v. D.B. Modak* + s. 228A/BNS s. 72 exclusions; revoked "corpus is trusted" in favor of OWASP LLM01 `<corpus_text>` isolation.

### Security
- **OWASP LLM01 Sanitization (`mcp/nyaya/sanitize.py`):** Added `frame_corpus_text()`, Unicode Tag (`U+E0000–U+E007F`) stripping, inner delimiter escaping, non-destructive `detect_suspected_injection()`, and operative `check_publication_ban()`.
- **HTTP Middleware Hardening (`mcp/nyaya/ratelimit.py`, `mcp/nyaya/security_headers.py`):** Right-most trusted `X-Forwarded-For` (`NYAYA_TRUSTED_PROXY_HOPS`), 50k-key bounded LRU `InMemoryBackend`, streaming body-size cap (`400` on invalid `Content-Length`, `413` on chunked overflow), `analytics_per_min=20` bucket, COOP/CORP headers, and proxy-aware HSTS.
- **DuckDB & PDF Hardening:** Scoped S3 secret (`SCOPE 's3://<bucket>'`), scheme stripping + `NYAYA_BUCKET_URL_STYLE="vhost"`, 256 MB memory limit, 5s `con.interrupt()`, and strict S3 PDF bucket/key regex + `follow_redirects=False`.

### Fixed
- Eliminated citation verification first-number collision (`AIR 1973 SC 9999` vs `AIR 1973 SC 1461`) and `act=None` judgment citation drops.
- Fixed `retrieval_eval.py` act-only recall false positives to require exact canonical `(act, ref)` / judgment key matches.
- Fixed v0.2 Constitutional Amendment year/article errors (1st–106th), IPC↔BNS collapse errors, and the 200 KB document truncation limit.

### Removed
- Completely removed Supabase, Postgres, `pgvector`, `DATABASE_URL`, `psycopg`, `psycopg-pool`, `openai` (from `mcp/`), `mcp/schema.sql`, `mcp/notebooks/hydrate.ipynb`, `mcp/nyaya/db.py`, `mcp/nyaya/embeddings.py`, and all 16 legacy v0.2 MCP tools.

## [0.2.0] - 2026-09-05

### Security
- Restricted CORS to `https://nyaya.parag.tech` (was `*`).
- Middleware registration now fails fast instead of silently continuing without security controls.
- `/health` endpoint only catches `DatabaseUnavailable` (was catching all exceptions, masking config errors).
- Added Redis-backed rate limiting for multi-worker deployments (`REDIS_URL` env var).

### Changed
- Chat agent refactored from a single-node ReAct loop to a two-phase **supervisor-synthesis** LangGraph architecture. The supervisor node plans and emits all tool calls in one `AIMessage` for parallel execution; a `DedupToolNode` runs them concurrently (deduplicating repeated name+args calls); the synthesis node composes the final grounded answer from the `ToolMessage` results. See `chat/nyaya_chat/agent.py`.
- Chat streaming now uses LangGraph v2 dual stream mode (`["messages", "updates"]`). Supervisor content is routed to a new `plan` SSE event so it doesn't mix with the synthesis answer (`token` events). Phase-transition `status` events now report `analyzing` → `searching` → `composing`.
- Chat LLM default model is now `nvidia/nemotron-3.5-lightning-30b-a3b` (was `nvidia/nemotron-3-super-120b-a12b`). Supervisor and synthesis phases get distinct model instances with separate token caps (`SUPERVISOR_MAX_TOKENS=512`, `SYNTHESIS_MAX_TOKENS=2048`).
- Chat tool loading switched to `langchain-mcp-adapters`' `MultiServerMCPClient` with a curated 6-tool allowlist (`DEFAULT_TOOLS` in `chat/nyaya_chat/config.py`).
- `semantic_query` tool now returns a structured `SearchResponse` with `fallback_reason` instead of raising `EmbeddingUnavailable`.
- Consolidated 20 → 16 MCP tools for better LLM tool-selection accuracy (see Removed section).
- `load_dotenv()` moved from module import time to `get_settings()` for test isolation.
- Added `redis_url` to `Settings` dataclass.
- `tools/__init__.py` now wraps each tool registration in try/except so one failure doesn't block all tools.
- Deployment config migrated from `railway.toml` to `.railway/railway.ts`, `NEXT_PUBLIC_CHAT_ENABLED` Dockerfile default flipped to `true`, fail-fast tool registration, `cross_reference` closed Literal `direction`, `Kind`/`HitKind` alias, opt-in `NYAYA_DOTENV=1`, merged `eval/chat_eval.py` harness, and strict `mypy` on `nyaya.db` (subsequently superseded in v2.0.0).

### Added
- `articles_affected` stays a CSV column inside `documents.metadata` in the v0.2 unified schema (the old `article_amendments` junction table is dropped by schema.sql, not added; the original entry here claimed the opposite).
- Self-contained hydration notebook (`mcp/notebooks/hydrate.ipynb`) replaces the old CLI-based ingestion pipeline: it fetches all sources live, embeds via NVIDIA API, and writes to Postgres.
- `EmbeddingService` class with injectable caches for testing.
- Structured error contract: `NotFound` errors now return `ToolResult(is_error=True, structured_content={"error": {"code", "message", "kind", "hint"}})` via `@structured_errors` decorator (`tools/_error.py`).
- `get_judgment` fuzzy substring matching for inputs ≥ 8 chars (exact citation/title match tried first).
- `list_sections` now raises `NotFound` on nonexistent acts (consistent with `list_chapters`).
- `get_amendments_for_article` regex fix (POSIX `[[:<:]]`/`[[:>:]]` word boundaries) and input validation.
- Coverage configuration in `pyproject.toml` (`pytest --cov=nyaya`).
- New test files: `test_config.py`, `test_exceptions.py`, `test_sanitize.py`, `test_security.py`, `test_server.py`, `test_structured_errors.py`, `test_rest.py`, `test_tools_contract.py`.
- Web: `lib/types.ts` and `lib/data.ts` for shared types and curated data.
- Web: SWR `fallbackData` support for `useHealthSummary` and `useTools`.
- Web: fetch timeout (10s) with `AbortController`.
- Web: ESLint rules for `@typescript-eslint`, `react-hooks`, `jsx-a11y`.
- `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`.

### Fixed
- SWR `useJudgments` cache key stability (uses array key instead of template literal).
- `get_amendments_for_article` PostgreSQL regex: `\b` (not POSIX ERE) replaced with `[[:<:]]`/`[[:>:]]` word boundaries.
- `background-attachment: fixed` only on hover-capable devices (mobile perf).

### Removed
- 4 MCP tools folded into `semantic_query`: `search_law`, `search_judgments`, `search_by_kind`, `hybrid_search`.
- 4 MCP tools folded into other tools or dropped: `get_chapter` (use `list_chapters`), `get_definition` (use `semantic_query` with `promote_definitions`), `resolve_citation` (folded into `get_section`/`get_article`), `get_sections_by_range` (use `list_sections` with `start`/`end`).
- `nyaya-ingest` CLI and `mcp/scripts/` directory (replaced by hydration notebook).
- `mcp/data/manual/` directory (judgments, amendments, schedules, IPC-BNS map, now ingested live).
- `Dockerfile.slim` references from `mcp/README.md`.
- `--extra semantic` and `--extra ingest` from `pyproject.toml`.
- Build artifacts from working tree: `.venv/`, `node_modules/`, `.next/`, `out/`, `__pycache__/`, `scratch/`.

## [0.1.0] - 2026-08-02

### Added
- Initial release of the nyaya MCP server for Indian law.
- 24 MCP tools and 11 resources over HTTP.
- Next.js 16 SPA frontend (Home, Corpus, Citations, Architecture).
- Docker multi-stage build for Railway deployment.
- Full-text search via Postgres `tsvector` + GIN indexes.
- Semantic search via `pgvector` + `fastembed` embeddings.
