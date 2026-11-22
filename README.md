# nyaya

**Zero-DB-ops Indian legal intelligence monorepo (`nyaya-mcp` v2.0)** — pure-lexical/metadata/graph MCP server, Next.js static SPA, and optional LangGraph chat assistant covering the Constitution of India, 37 Central & State/UT legislative jurisdictions, 12 Regulators, 15 Tribunals, Supreme Court (35k+), High Courts (17.77M+ across 25 HCs), and District Court metadata (81M DDL records).

## Architecture (Option R2 — Zero-DB-Ops, Scale-to-Zero)

Nyaya v2.0 runs a 100% external-database-free, pure-lexical/metadata/graph retrieval architecture requiring zero vector databases or embedding APIs:

- **Baked SQLite Core (`nyaya-core.sqlite`, ~250 MB):** Read-only immutable SQLite3 with FTS5 (`porter unicode61`), Constitutional Amendments 1–106, IPC/CrPC/IEA ↔ BNS/BNSS/BSA concordance + substantive diffs, Judicial Invalidations registry (e.g. IT Act s. 66A, Electoral Bonds), SC metadata, citation graph edges, and courts directory.
- **Railway Storage Bucket (Private Tigris S3):** Holds 3 **LanceDB native Rust BM25 FTS** tables (`sc_chunks`, `hc_hot_chunks`, `state_acts_regulators`) and **compacted ZSTD Parquet** partitions queried by **DuckDB `httpfs`** (pre-installed into `/app/duckdb_ext`, capped at `256 MB` RAM / 2 threads / 5s `con.interrupt()`).
- **Direct AWS Open Data PDFs (`pypdfium2`, Apache-2.0):** On-demand anonymous HTTPS range/stream reads from `s3://indian-supreme-court-judgments` and `s3://indian-high-court-judgments` with strict bucket/key regex allowlisting, `%PDF-` magic gate, 15 MB / 500-page caps, scanned-PDF detection (`chars_per_page < 50`), and a 256 MB disk zstd LRU cache.
- **Local `uvx nyaya-mcp` (`stdio`):** Public packs and `manifest.json` are dual-published to Hugging Face Datasets / GitHub Releases so `uvx nyaya-mcp` runs on laptops with **zero secrets** and **zero stdout pollution**.

## Components

| Path | Version | Description |
|---|---|---|
| [`mcp/`](mcp/) | `2.0.0` | `nyaya-mcp` Python package: 12 MCP tools, 8 `nyaya://` resources, 4 practitioner/student prompts, pure-ASGI security/rate-limit middleware, thin `/api/*` REST routes, and `nyaya-ingest` CLI. |
| [`web/`](web/) | `2.0.0` | Next.js 16 static export (`web/out/`) — Home, Corpus, Citations, and Architecture pages served from the same Starlette origin (`/`), hydrating live from `/api/corpus-status`, `/api/acts`, `/api/judgments`, `/api/analytics`, `/api/tools`, and `/api/health-summary`. |
| [`chat/`](chat/) | `2.0.0` | Optional FastAPI + LangGraph assistant (`nyaya-chat`) over an 8-tool v2 allowlist using shared `nyaya.citations` verification and `ChatNVIDIA` (`langchain-nvidia-ai-endpoints`); mounted automatically at `/chat` only when `NVIDIA_API_KEY` is set. |

## Quickstart

### 1. Local `stdio` MCP (`uvx` — zero API keys)

```bash
uvx nyaya-mcp
```

Useful CLI flags (`mcp/nyaya/cli.py`):

- `--transport stdio|http` (default `stdio`; or `nyaya-mcp serve` for HTTP mode)
- `--commercial-safe-only` (or `NYAYA_COMMERCIAL_SAFE_ONLY=1`): exclude CC-BY-NC datasets (`DDL 81M District Courts`, `ILDC`)
- `--offline-only --no-download --data-dir /path/to/data` (plus `--cache-dir`, `--ensure-data`): air-gapped local mode
- `nyaya-mcp sync --pack core|lance|parquet|all`: pre-download public packs from `NYAYA_MANIFEST_URL`
- `--host 0.0.0.0 --port 8000`: HTTP bind host and port

### 2. Local Docker & Docker Compose (hardened `read_only: true` + 1 GB RAM)

```bash
cp .env.example .env   # optional — works with zero secrets out of the box
docker compose up -d --build

# Or build and run stdio MCP directly inside a hardened read-only container:
docker build -t nyaya-mcp .
docker run --rm -i --read-only --tmpfs /tmp:size=64m -w /tmp nyaya-mcp nyaya-mcp --offline-only --no-download
```

`docker-compose.yml` runs with `read_only: true` and mounts a disk-backed named volume (`nyaya_cache:/var/cache/nyaya`) for `NYAYA_CACHE_DIR` so the PDF zstd LRU and DuckDB temp spill never consume Linux RAM `tmpfs` under `mem_limit: 1g`.

### 3. Deploy to Railway (Option R2)

Service infrastructure is declared in [`.railway/railway.ts`](.railway/railway.ts):

1. Provision a **Railway Storage Bucket** attached to the `nyaya` service and set the deployment region to **`asia-southeast1` (Singapore)** in the Railway dashboard (minimal RTT to Indian users and AWS `ap-south-1` PDFs).
2. Configure `NYAYA_BUCKET_ENDPOINT`, `NYAYA_BUCKET_NAME`, `NYAYA_BUCKET_ACCESS_KEY_ID`, `NYAYA_BUCKET_SECRET_ACCESS_KEY` (plus optional `NVIDIA_API_KEY` for `/chat`) in Railway Variables. All are `preserve()`d in `.railway/railway.ts`.
3. Populate the bucket via `nyaya-ingest` (or `.github/workflows/ingest-refresh.yml`):
   ```bash
   cd mcp && uv sync --extra ingest
   uv run nyaya-ingest sync --tier static && uv run nyaya-ingest sync --tier daily
   uv run nyaya-ingest build-sqlite && uv run nyaya-ingest build-lance-fts && uv run nyaya-ingest compact-parquet
   uv run nyaya-ingest verify-gate --strict
   uv run nyaya-ingest publish --target all
   # Hermetic offline pipeline test:
   uv run nyaya-ingest all --fixture --dry-run
   ```
4. Review and apply Railway IaC with `railway config plan` and `railway config apply`.
5. Verify live endpoints: `GET /health`, `GET /api/corpus-status`, `GET /api/acts`, `GET /api/judgments`, `GET /api/analytics?dataset=sc&group_by=disposal_nature`, `GET /api/tools`, `GET /api/health-summary`, `POST /mcp` (`initialize`), and (if `NVIDIA_API_KEY` is set) `GET /chat/health` & `POST /chat/turn`.

## Local Development

```bash
# 1. MCP + Chat tests, lint, types & offline eval
cd mcp
uv sync --extra http --extra ingest --dev
uv pip install -e ../chat
uv run ruff check nyaya/ tests/ ../chat/nyaya_chat/ ../chat/tests/ ../chat/eval/
uv run mypy nyaya/ ../chat/nyaya_chat/
uv run pytest --cov=nyaya --cov-fail-under=78
NVIDIA_API_KEY=nvapi-test-key-for-ci uv run pytest ../chat/tests/ --cov=nyaya_chat --cov-fail-under=84
uv run python ../chat/eval/retrieval_eval.py --fixture tests/fixtures/mini_corpus

# 2. Run local HTTP server (MCP + REST + built SPA + optional /chat)
uv run nyaya-mcp --transport http --host 127.0.0.1 --port 8000

# 3. Web frontend (Next.js static export)
cd ../web
npm install
npm run dev        # HMR on :3000 (proxies /api/*, /mcp, /chat/* to :8000)
npx eslint . && npm test && npm run build
```

## Licensing & Attribution

- **Source code:** Apache-2.0 — see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
- **Legal corpus & exclusions:** Primary Indian statutes and judgments are reproduced under **Indian Copyright Act, 1957 s. 52(1)(q)** and ***Eastern Book Company v. D.B. Modak*, (2008) 1 SCC 1** (commercial reporter headnotes and s. 228A / BNS s. 72 / POCSO protected orders are excluded).
- **Non-commercial datasets:** Default research packs bundle DDL 81M District Courts (`CC-BY-NC-SA-4.0`) and ILDC (`CC-BY-NC-4.0`) tagged `commercial_safe=false`. Commercial operators must set `NYAYA_COMMERCIAL_SAFE_ONLY=1` or pass `commercial_safe_only=true`. See [`TRUSTED_SOURCES.md`](TRUSTED_SOURCES.md).

## Security

Nyaya treats all primary legal text as **untrusted data** (`<corpus_text ... trust="untrusted_primary_law">`, OWASP LLM01). Rate limits, streaming body caps, security headers, DuckDB/S3 isolation, and vulnerability disclosure are documented in [`SECURITY.md`](SECURITY.md) and [`TRUSTED_SOURCES.md`](TRUSTED_SOURCES.md).
