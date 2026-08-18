# nyaya

**A monorepo for Indian-law tooling** — MCP server, Next.js frontend, and LangGraph chat assistant for Indian legal research (Constitution, IPC, CrPC, CPC, Evidence Act, BNS/BNSS/BSA 2023, commercial statutes, landmark Supreme Court judgments).

The first component is an MCP server; other tools will follow.

## Components

| Path | Status | Description |
|---|---|---|
| [`mcp/`](mcp/) | alpha | MCP server for Indian law — Constitution, IPC, CrPC, CPC, Evidence Act, BNS/BNSS/BSA 2023, commercial statutes, landmark SC judgments. Exposes 16 tools and 11 resources over HTTP. Deployable to Railway via Docker. |
| [`web/`](web/) | alpha | Next.js 16 (App Router, static export) frontend — Home, Corpus, Citations, Architecture pages. Served from the same container as the MCP server via Starlette `StaticFiles`; live data fetched client-side from `/api/*` REST endpoints. |
| [`chat/`](chat/) | alpha | LangGraph + FastAPI chat backend — retrieval-grounded Indian-law assistant. A two-phase supervisor-synthesis agent over the nyaya MCP tools (supervisor plans + delegates parallel tool calls, synthesis composes the cited answer), powered by NVIDIA Nemotron, streamed to the SPA over SSE. Mounted into the main server at `/chat`, so the SPA, REST, MCP, and chat share one origin and one Railway service. |

See [`mcp/README.md`](mcp/README.md) for setup, deployment, and client-configuration instructions.

## Deploy to Railway

A single Railway service serves the SPA, the REST API, the MCP endpoint, and the chat assistant from one origin (root `Dockerfile`).

1. `railway init` (or connect the GitHub repo for autodeploy from `main`).
2. Set `DATABASE_URL` (Supabase/Postgres connection string) and `NVIDIA_API_KEY` (NVIDIA API Catalog key for the chat assistant) in the Railway Variables tab. `PORT` is set automatically.
3. `railway up` — builds the root `Dockerfile` (Node 20 stage builds `web/out/`, Python 3.12-alpine stage serves it alongside the MCP server and the chat sub-app).
4. Railway polls `GET /health` (configured in `railway.toml`).
5. Run `nyaya-ingest all` locally once (with the same `.env`) to hydrate the corpus — the deployed server reads from the same database.
6. Smoke checks against the deployed domain:
   - `GET /` → SPA home renders, chat panel live (streaming).
   - `GET /corpus/` → live numbers from `/api/corpus-stats`.
   - `GET /architecture/` → `mcp.json` copy button yields `{ "mcpServers": { "nyaya": { "url": "https://<domain>/mcp", "transport": "http" } } }`.
   - `POST /mcp` with an MCP initialize envelope → succeeds.
   - `POST /chat/turn` with `{"message":"What is IPC 302?"}` → SSE stream of tokens + citations.
   - `GET /chat/health` → `{"status":"healthy","model":"…","tools_loaded":N}`.

## Local development

- **MCP + REST + Chat**: `cd mcp && uv venv && pip install -e . && pip install -e ../chat && nyaya` → uvicorn on `:8000`. The chat sub-app mounts at `/chat` automatically when `NVIDIA_API_KEY` is set (Swagger UI at `http://localhost:8000/chat/docs`). The agent calls the same-process MCP server at `http://localhost:8000/mcp` (configured in `chat/nyaya_chat/config.py`).
- **SPA (HMR)**: `cd web && npm run dev` → Next dev server on `:3000`. `next.config.mjs` rewrites `/api/*`, `/mcp`, and `/chat/*` to `localhost:8000`.
- **Build check**: `cd web && npx eslint . && npm run build` → produces `web/out/`.
- **Serve the production bundle locally**: `python -m nyaya.server` from `mcp/` → visit `http://localhost:8000/`.
- **Python tests / lint**:
  - `cd mcp && pytest` and `ruff check .` (runs mcp + chat unit tests via installed package)
  - `cd chat && pytest` and `ruff check .` (chat package's own unit tests)

## License

Apache-2.0. See [LICENSE](LICENSE).

## Security

The Nyaya MCP server is designed to be deployed behind a reverse proxy (Cloudflare, Railway edge, nginx) that handles authentication and TLS. The server itself does not enforce authentication — this is intentional for the alpha so browser-based MCP clients can call the endpoint without configuration.

**Rate limiting** is configured in [`mcp/nyaya/config.py`](mcp/nyaya/config.py) via the `RateLimitSettings` dataclass (edit there to tune; no env vars needed). Defaults: 120 req/min/IP for read tools, 30 req/min/IP for embedding tools, 15 req/min/IP for chat turns, 1 MB body size cap.

**Security headers** (CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy, Permissions-Policy, HSTS) are set by the Starlette middleware in [`mcp/nyaya/security_headers.py`](mcp/nyaya/security_headers.py).

**Corpus integrity**: the ingest pipeline sanitizes all text at ingest time (strips control characters, caps row length at 200 KB) via [`mcp/nyaya/sanitize.py`](mcp/nyaya/sanitize.py). See [`TRUSTED_SOURCES.md`](TRUSTED_SOURCES.md) for the trust model and data provenance.

**Recommendations for production deployment**:
- Use a **read-only Postgres role** for `DATABASE_URL` (the server only reads; ingestion uses the same URL but should be run from a trusted environment).
- Deploy behind a reverse proxy with TLS, authentication, and IP-based rate limiting.
- Run `nyaya-ingest` from a trusted machine, not the public deployment.