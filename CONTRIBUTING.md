# Contributing to nyaya

Thank you for your interest in contributing to nyaya! This document covers setup, code style, and the PR process.

## Development setup

### Prerequisites

- Python 3.11+
- Node.js 20+
- A Supabase project (free tier) with the `pgvector` extension enabled

### MCP server (Python)

```bash
cd mcp
uv sync --extra dev
# Set DATABASE_URL and NVIDIA_API_KEY in .env (copy from .env.example)
nyaya              # start the server on :8000
pytest             # run offline unit tests
pytest -m integration  # run server boot tests
ruff check nyaya/ tests/  # lint
mypy nyaya/        # type check (advisory)
```

### Web frontend (Next.js)

```bash
cd web
npm install
npm run dev        # dev server on :3000 (proxies /api/* to :8000)
npx eslint .       # lint
npm run build      # static export to web/out/
```

### Running both together

Start the MCP server (`cd mcp && nyaya`) and the dev server (`cd web && npm run dev`). The Next.js dev server proxies `/api/*` and `/mcp` to `localhost:8000` via `next.config.mjs` rewrites.

## Code style

### Python

- **Formatter/linter**: `ruff` (line length 100, target py311)
- **Type checker**: `mypy` (advisory — run `uv run mypy nyaya/`)
- **Tests**: `pytest` with `asyncio_mode = "auto"`
- All modules use `from __future__ import annotations`
- Docstrings on all public functions/classes

### TypeScript/React

- **Linter**: ESLint 9 flat config with `next`, `@typescript-eslint`, `react-hooks`, `jsx-a11y`
- **Type checker**: `tsc --noEmit` (via `npm run build`)
- Strict TypeScript (`strict: true` in `tsconfig.json`)
- Path aliases: `@/*` -> `./src/*`

## Testing guidelines

- **Unit tests**: No DB or network required. The `fake_db` fixture in `conftest.py` stubs the DB layer with canned data.
- **Integration tests**: Marked with `@pytest.mark.integration`. Use `TestClient` against the ASGI app.
- **Coverage**: Run `pytest --cov=nyaya --cov-report=term-missing`
- Every new tool, resource, or DB function should have corresponding tests.

## PR process

1. Fork the repo and create a feature branch from `main` or `development`.
2. Write tests for your changes.
3. Ensure all checks pass: `ruff check`, `mypy`, `pytest`, `eslint`, `npm run build`.
4. Keep PRs focused — one feature or fix per PR.
5. Write a clear PR description explaining what and why.

## Project structure

```
nyaya/
├── mcp/          # Python MCP server (FastMCP + psycopg + pgvector)
│   ├── nyaya/    # the Python package
│   ├── notebooks/ # hydration notebook (fetch + embed + write)
│   └── tests/    # offline unit + integration tests
├── web/          # Next.js 16 SPA (App Router, static export)
│   └── src/      # pages, components, lib
└── .github/      # CI workflows
```

## License

Apache-2.0. By contributing, you agree that your contributions will be licensed under the same terms.