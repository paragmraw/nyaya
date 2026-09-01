# Changelog

All notable changes to nyaya will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed
- MCP tool registration now fails fast: a duplicate tool name, a raising register function, or a module that does not land the tools it claims aborts startup with a `RuntimeError` instead of logging and continuing with a silently crippled tool surface (supersedes the 0.2.0 log-and-continue behavior — FastMCP itself only warns on duplicates and keeps the first registration).
- `cross_reference`'s `direction` parameter is now a closed Literal (`from`/`to`/`both`) enforced by FastMCP schema validation at the tool boundary, replacing runtime validation; `db.get_cross_refs` types it as `CrossRefDirection`.
- `Kind`/`HitKind` consolidated: `HitKind` is now an alias of `Kind` (one definition, one place to extend).
- `load_dotenv()` in `get_settings()` is now opt-in: set `NYAYA_DOTENV=1` to load a local `.env` (the quickstart flow). Off by default so tests/CI are never surprised by a stray `.env`; a no-op when the optional `python-dotenv` dependency (new `dotenv` extra) is not installed.
- `list_chapters` tool signature now returns a typed `dict[str, Any]`.
- Chat eval harnesses merged: `eval/validate_chat.py` folded into `eval/chat_eval.py` (superset of both scenario datasets and check sets). Time-to-first-token is now measured off the live incremental SSE stream instead of being estimated as `latency * 0.3`, and the harness is wrapped by offline-skipping pytest tests under the `eval` marker (`chat/tests/test_chat_eval.py`, opt-in via `NYAYA_EVAL_HOST`).
- `mypy` is genuinely clean on `nyaya.db`: the pool/connection are typed as `ConnectionPool[psycopg.Connection[dict[str, Any]]]` so dict rows propagate, and the module-wide `disable_error_code` override that hid 28 real errors is removed (24 source files, 0 errors, 0 suppression overrides left).

### Added
- `mcp/tests/test_rest.py` — REST endpoint contract tests against the real ASGI app (stats shapes, degraded health, error redaction, clamping, tool listing), all offline via fakes.
- `mcp/tests/test_tools_contract.py` — per-tool contract tests (16-tool registration surface, readOnly annotations, Literal enum in the `cross_reference` schema, `include_text`/`snippet_chars` parameters) plus fail-fast registration tests, TTL-cache unit tests for `_LockedTTLCache`, and a middleware-stack-order structural test on the real app.
- `NYAYA_DOTENV=1` opt-in `.env` loading (see Changed) documented in the mcp README quickstart.

### Removed
- Unused `EmbeddingService`/`get_default_service`/`embed_texts` indirection from `nyaya.embeddings` (the module-level `embed_query`/`rerank_query` functions are the only API).
- Duplicate `_BIDI_RE` (now a single `BIDI_RE` in `nyaya/sanitize.py`, shared with `db.py`) and duplicate `_redact_url` (single home in `config.py`; `ratelimit.py` imports it).
- The dead `mcp_instance._nyaya_chat_app` write-only attribute in `server.py`.

### Fixed
- CHANGELOG 0.2.0 entry: `SYNTHESIS_MAX_TOKENS` was recorded as 4096 but ships as 2048; the `article_amendments` junction-table claim was wrong — the v0.2 schema **dropped** `article_amendments` (schema.sql only `DROP`s it) and keeps `articles_affected` as a CSV in `documents.metadata`.

## [0.2.0] - 2026-08-18

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

### Added
- `articles_affected` stays a CSV column inside `documents.metadata` in the v0.2 unified schema (the old `article_amendments` junction table is dropped by schema.sql, not added — the original entry here claimed the opposite).
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