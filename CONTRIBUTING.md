# Contributing to nyaya

Thank you for your interest in contributing to nyaya! This document covers local setup, code style, testing gates, and the PR process for **Nyaya v2.0**.

## Development Setup

### Prerequisites

- Python 3.11.5+ (`mcp/pyproject.toml` floor; CI targets Python 3.12)
- Node.js 20+
- [`uv`](https://docs.astral.sh/uv/) (`0.11.x`; CI pins `0.11.25`)

No external database service and no API keys are required for MCP development or offline tests.

### MCP & Chat (Python)

```bash
cd mcp
uv sync --extra http --extra ingest --dev
uv export --project ../chat --locked --no-dev --no-emit-project --no-editable --format requirements-txt > /tmp/chat-requirements.txt
uv pip install -r /tmp/chat-requirements.txt && uv pip install --no-deps -e ../chat && rm -f /tmp/chat-requirements.txt

# Run MCP server locally over stdio (uses baked/local data or --data-dir)
uv run nyaya-mcp --transport stdio --data-dir tests/fixtures/mini_corpus --no-download --offline-only

# Or run HTTP server (MCP + REST + built SPA + optional /chat when NVIDIA_API_KEY is set)
uv run nyaya-mcp --transport http --host 127.0.0.1 --port 8000

# Lint, strict type checks, lockfiles & tests
uv lock --check && (cd ../chat && uv lock --check)
uv run ruff check nyaya/ tests/ ../chat/nyaya_chat/ ../chat/tests/ ../chat/eval/
uv run mypy nyaya/ ../chat/nyaya_chat/
uv run pytest --cov=nyaya --cov-fail-under=78
NVIDIA_API_KEY=nvapi-test-key-for-ci uv run pytest ../chat/tests/ --cov=nyaya_chat --cov-fail-under=84
uv run python ../chat/eval/retrieval_eval.py --fixture tests/fixtures/mini_corpus
uv run nyaya-ingest all --fixture --dry-run
```

### Web Frontend (Next.js)

```bash
cd web
npm install
npm run dev        # dev server on :3000 (proxies /api/*, /mcp, /chat/* to :8000)
npx eslint .       # lint
npm test           # unit tests (node:test via tsx)
npm run build      # static export to web/out/
```

## Code Style

### Python

- **Formatter/linter:** `ruff` (line length 100, target `py311`)
- **Type checker:** `mypy` across both `mcp/nyaya/` and `chat/nyaya_chat/`
- **Tests:** `pytest` with `asyncio_mode = "auto"`
- Use `from __future__ import annotations` and concise docstrings on public classes/functions
- Never print or configure logging to `stdout` in `nyaya` runtime paths (`logging` must target `sys.stderr` so `stdio` JSON-RPC framing stays pure)

### TypeScript / React

- **Linter:** ESLint 9 flat config (`next`, `@typescript-eslint`, `react-hooks`, `jsx-a11y`)
- **Type checking:** Strict TypeScript (`tsc --noEmit` via `npm run build`)
- **Static export constraint:** `web/` builds with `output: "export"`. Do not introduce dynamic `[slug]` routes; use query/hash deep-linking (`src/lib/deep-link.ts`)

## Testing & Supply-Chain Guidelines

- **Offline hermeticity:** Unit and integration tests run with zero network calls and zero live credentials using `InMemoryCorpusBackend` (`nyaya.storage.memory_backend`) and `mcp/tests/fixtures/mini_corpus/builder.py`.
- **Lockfile synchronization:** `mcp/uv.lock` and `chat/uv.lock` share `[tool.uv] constraint-dependencies` so the multi-stage `Dockerfile` (`-c ../requirements.txt`) builds cleanly. Always verify `uv lock --check` in both `mcp/` and `chat/`.
- **License hygiene (hard CI gate):** Never introduce copyleft runtime dependencies (`ecourts`, `pymupdf`, `fitz`, `surya-ocr`), legacy relational/vector database drivers, or scraped/unlicensed sources (`terms_status="red"` in `mcp/nyaya/ingest/data/sources.lock.yaml`), all enforced by `.github/workflows/security.yml` and `mcp/nyaya/ingest/lockfile.py`.

## PR Process

1. Fork the repository and branch from `main` or `development`.
2. Add or update unit/contract tests for every behavior change.
3. Run all CI checks locally (`ruff`, `mypy`, `pytest` coverage gates, `uv lock --check`, `eslint`, `npm test`, `npm run build`).
4. Keep PRs focused with a clear explanation of the change and verification outputs.

## Project Structure

```text
nyaya/
├── .github/workflows/  # ci.yml, security.yml, ingest-refresh.yml
├── .railway/           # Railway IaC (railway.ts)
├── .env.example        # Environment variable reference (zero secrets required for MCP)
├── Dockerfile          # Multi-stage Debian-slim build (Node 20 + Python 3.12)
├── docker-compose.yml  # Hardened local container run (disk-backed nyaya_cache)
├── CHANGELOG.md        # Keep-a-Changelog release & corpus history
├── CODE_OF_CONDUCT.md  # Contributor Covenant v2.1
├── CONTRIBUTING.md     # Local setup, style, testing & PR guidelines
├── NOTICE              # Apache-2.0 Section 4(d) corpus & dependency attributions
├── README.md           # Project overview & quickstart
├── SECURITY.md         # Vulnerability reporting & defense-in-depth architecture
├── TRUSTED_SOURCES.md  # Indian Copyright Act s. 52(1)(q) & OWASP LLM01 trust model
├── mcp/                # nyaya-mcp server, storage/retrieval, ingest CLI & tests
│   ├── nyaya/          # cli, mcp_app, http_app, citations, storage/, retrieval/, ingest/, tools/, resources/, prompts/
│   └── tests/          # Offline unit, contract & mini_corpus fixture tests
├── chat/               # Optional LangGraph + FastAPI /chat sub-app & evals
└── web/                # Next.js 16 static export SPA
```

## License

Apache-2.0 (`LICENSE` + `NOTICE`). By contributing, you agree that your contributions will be licensed under Apache-2.0.
