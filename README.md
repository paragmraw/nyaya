# nyaya

A monorepo for Indian-law tooling. The first component is an MCP server; other tools will follow.

## Components

| Path | Status | Description |
|---|---|---|
| [`mcp/`](mcp/) | alpha | MCP server for Indian law — Constitution, IPC, CrPC, CPC, Evidence Act, BNS/BNSS/BSA 2023, commercial statutes, landmark SC judgments. Exposes 24 tools and 13 resources over HTTP. Deployable to Railway via Docker. |
| [`web/`](web/) | alpha | Next.js 16 (App Router, static export) frontend — Home, Corpus, Citations, Architecture pages. Served from the same container as the MCP server via Starlette `StaticFiles`; live data fetched client-side from `/api/*` REST endpoints. |

See [`mcp/README.md`](mcp/README.md) for setup, deployment, and client-configuration instructions.

## Deploy to Railway

A single Railway service serves the SPA, the REST API, and the MCP endpoint from one origin.

1. `railway init` (or connect the GitHub repo for autodeploy from `main`).
2. Set `DATABASE_URL` (Supabase/Postgres connection string) in the Railway Variables tab. `PORT` is set automatically.
3. `railway up` — builds the root `Dockerfile` (Node 20 stage builds `web/out/`, Python 3.12-alpine stage serves it alongside the MCP server).
4. Railway polls `GET /health` (configured in `railway.toml`).
5. Run `nyaya-ingest all` locally once (with the same `.env`) to hydrate the corpus — the deployed server reads from the same database.
6. Smoke checks against the deployed domain:
   - `GET /` → SPA home renders, chat panel blurred ("Coming soon").
   - `GET /corpus/` → live numbers from `/api/corpus-stats`.
   - `GET /architecture/` → `mcp.json` copy button yields `{ "url": "https://<domain>/mcp", "transport": "http" }`.
   - `POST /mcp` with an MCP initialize envelope → succeeds.

## Local development

- **MCP + REST**: `cd mcp && nyaya` → uvicorn on `:8000`.
- **SPA (HMR)**: `cd web && npm run dev` → Next dev server on `:3000`. `next.config.mjs` rewrites `/api/*` and `/mcp` to `localhost:8000`.
- **Build check**: `cd web && npx eslint . && npm run build` → produces `web/out/`.
- **Serve the production bundle locally**: `NYAYA_WEB_OUT=web/out` `python -m nyaya.server` from `mcp/` → visit `http://localhost:8000/`.
- **Python tests / lint**: `cd mcp && pytest` and `ruff check .`.

## License

Apache-2.0. See [LICENSE](LICENSE).