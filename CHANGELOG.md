# Changelog

All notable changes to nyaya will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.2.0] - 2026-08-18

### Security
- Restricted CORS to `https://nyaya.parag.tech` (was `*`).
- Middleware registration now fails fast instead of silently continuing without security controls.
- `/health` endpoint only catches `DatabaseUnavailable` (was catching all exceptions, masking config errors).
- Added Redis-backed rate limiting for multi-worker deployments (`REDIS_URL` env var).

### Changed
- Chat agent refactored from a single-node ReAct loop to a two-phase **supervisor-synthesis** LangGraph architecture. The supervisor node plans and emits all tool calls in one `AIMessage` for parallel execution; a `DedupToolNode` runs them concurrently (deduplicating repeated name+args calls); the synthesis node composes the final grounded answer from the `ToolMessage` results. See `chat/nyaya_chat/agent.py`.
- Chat streaming now uses LangGraph v2 dual stream mode (`["messages", "updates"]`). Supervisor content is routed to a new `plan` SSE event so it doesn't mix with the synthesis answer (`token` events). Phase-transition `status` events now report `analyzing` → `searching` → `composing`.
- Chat LLM default model is now `nvidia/nemotron-3.5-lightning-30b-a3b` (was `nvidia/nemotron-3-super-120b-a12b`). Supervisor and synthesis phases get distinct model instances with separate token caps (`SUPERVISOR_MAX_TOKENS=512`, `SYNTHESIS_MAX_TOKENS=4096`).
- Chat tool loading switched to `langchain-mcp-adapters`' `MultiServerMCPClient` with a curated 6-tool allowlist (`DEFAULT_TOOLS` in `chat/nyaya_chat/config.py`).
- `semantic_query` tool now returns a structured `SearchResponse` with `fallback_reason` instead of raising `EmbeddingUnavailable`.
- Consolidated 20 → 16 MCP tools for better LLM tool-selection accuracy (see Removed section).
- `load_dotenv()` moved from module import time to `get_settings()` for test isolation.
- Added `redis_url` to `Settings` dataclass.
- `tools/__init__.py` now wraps each tool registration in try/except so one failure doesn't block all tools.

### Added
- `article_amendments` junction table in schema (normalizes the `articles_affected` CSV column).
- Self-contained hydration notebook (`mcp/notebooks/hydrate.ipynb`) replaces the old CLI-based ingestion pipeline — fetches all sources live, embeds via NVIDIA API, writes to Postgres.
- `EmbeddingService` class with injectable caches for testing.
- Structured error contract: `NotFound` errors now return `ToolResult(is_error=True, structured_content={"error": {"code", "message", "kind", "hint"}})` via `@structured_errors` decorator (`tools/_error.py`).
- `get_judgment` fuzzy substring matching for inputs ≥ 8 chars (exact citation/title match tried first).
- `list_sections` now raises `NotFound` on nonexistent acts (consistent with `list_chapters`).
- `get_amendments_for_article` regex fix (POSIX `[[:<:]]`/`[[:>:]]` word boundaries) and input validation.
- Coverage configuration in `pyproject.toml` (`pytest --cov=nyaya`).
- New test files: `test_config.py`, `test_exceptions.py`, `test_sanitize.py`, `test_security.py`, `test_server.py`, `test_structured_errors.py`.
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
- `mcp/data/manual/` directory (judgments, amendments, schedules, IPC-BNS map — now ingested live).
- `Dockerfile.slim` references from `mcp/README.md`.
- `--extra semantic` and `--extra ingest` from `pyproject.toml`.
- Build artifacts from working tree: `.venv/`, `node_modules/`, `.next/`, `out/`, `__pycache__/`, `scratch/`.

## [0.1.0] - 2026-07-01

### Added
- Initial release of the nyaya MCP server for Indian law.
- 24 MCP tools and 11 resources over HTTP.
- Next.js 16 SPA frontend (Home, Corpus, Citations, Architecture).
- Docker multi-stage build for Railway deployment.
- Full-text search via Postgres `tsvector` + GIN indexes.
- Semantic search via `pgvector` + `fastembed` embeddings.