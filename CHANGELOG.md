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
- `hybrid_search` tool now includes `fallback_reason` in the response when embedding is unavailable.
- `load_dotenv()` moved from module import time to `get_settings()` for test isolation.
- Added `redis_url` to `Settings` dataclass.
- `tools/__init__.py` now wraps each tool registration in try/except so one failure doesn't block all tools.

### Added
- `article_amendments` junction table in schema (normalizes the `articles_affected` CSV column).
- `argparse` CLI for `nyaya-ingest` with subcommands and `--help`.
- `EmbeddingService` class with injectable caches for testing.
- SQL statement splitter in `IngestDB.apply_schema` (respects dollar-quoted strings).
- Coverage configuration in `pyproject.toml` (`pytest --cov=nyaya`).
- New test files: `test_db.py`, `test_embeddings.py`, `test_security.py`, `test_exceptions.py`, `test_sanitize.py`, `test_scripts.py`.
- Web: `lib/types.ts` and `lib/data.ts` for shared types and curated data.
- Web: SWR `fallbackData` support for `useHealthSummary` and `useTools`.
- Web: fetch timeout (10s) with `AbortController`.
- Web: ESLint rules for `@typescript-eslint`, `react-hooks`, `jsx-a11y`.
- `CONTRIBUTING.md`, `CODE_OF_CONDUCT.md`, `SECURITY.md`.

### Fixed
- SWR `useJudgments` cache key stability (uses array key instead of template literal).
- `scripts/db.py` `upsert_embedding` uses `ValueError` instead of `assert` for table validation.
- `fake_db._get_sections_by_range` stub now respects `start`/`end` parameters.
- `background-attachment: fixed` only on hover-capable devices (mobile perf).

### Removed
- `Dockerfile.slim` references from `mcp/README.md`.
- Build artifacts from working tree: `.venv/`, `node_modules/`, `.next/`, `out/`, `__pycache__/`, `scratch/`.

## [0.1.0] - 2026-07-01

### Added
- Initial release of the nyaya MCP server for Indian law.
- 24 MCP tools and 11 resources over HTTP.
- Next.js 16 SPA frontend (Home, Corpus, Citations, Architecture).
- Docker multi-stage build for Railway deployment.
- Full-text search via Postgres `tsvector` + GIN indexes.
- Semantic search via `pgvector` + `fastembed` embeddings.