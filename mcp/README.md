# nyaya-mcp

An [MCP](https://modelcontextprotocol.io) server for Indian law (**v2.1.0**, package `nyaya-mcp`, Python `>=3.11.5`). Runs locally over `stdio` or as a stateless Railway HTTP server combining a baked SQLite FTS5 core, 5 LanceDB native Rust BM25 FTS tables (with optional opt-in hybrid vector RRF), DuckDB compacted ZSTD Parquet, and direct AWS Open Data PDF extraction (`pypdfium2`, plus opt-in Apache-2.0 OCR). Local/source (`uv run nyaya-mcp`), Docker, and hosted Streamable HTTP (`https://nyaya.parag.tech/mcp`) work immediately; PyPI (`nyaya-mcp`) and Official MCP Registry (`server.json`) artifacts publish via `.github/workflows/release-pypi.yml` on `v*` tags.

> **Not legal advice.** `nyaya-mcp` is a legal research and citation-auditing tool. Always verify primary texts against the official Gazette and check subsequent judicial treatment before filing.

## MCP v2.1 Surface (15 Tools · 8 Resources · 4 Prompts)

### 15 Tools

All 15 tools are read-only (`readOnlyHint=True`, `destructiveHint=False`, `idempotentHint=True`), enforce `commercial_safe_only`, attach `SourceProvenance`, and wrap primary text in `<corpus_text ... trust="untrusted_primary_law">`:

| Tool | Scope (`openWorldHint`) | Purpose |
|---|---|---|
| `search_statutes` | `True` (SQLite + Bucket LanceDB) | Lexical BM25 / exact-phrase search across Central Acts, Constitution, 36 State/UT Acts, CPC First Schedule, CAD debates, and 12 Regulators |
| `get_provision` | `True` | Verbatim section/article/schedule/order-rule + `StatutoryValidity` envelope + top cross-links; supports optional `as_of='YYYY-MM-DD'` point-in-time reconstruction |
| `get_provisions_batch` | `False` (local SQLite core) | Partial-success batch fetch of up to 50 statutory provisions (`citations` or structured `requests`) with optional `as_of='YYYY-MM-DD'` reconstruction |
| `browse_statute` | `True` | Search statute/regulator catalog, browse Act TOC, or paginate provisions |
| `compare_provisions` | `False` (local SQLite core) | IPC↔BNS, CrPC↔BNSS, IEA↔BSA (**148** criminal mappings + BNSS `offence_classification`), and `ITAct1961↔ITAct2025` (**33** tax mappings + `ITAct2025 s.536` savings) with clause diffs & deltas |
| `cross_reference` | `False` (local SQLite core) | Bidirectional statutory and judicial citation cross-references |
| `get_provision_history` | `False` (local SQLite core) | Constitutional Amendments 1–106, statutory amendments, and judicial invalidation timelines |
| `search_judgments` | `True` | Multi-facet search across SC (`38,292`), 25 High Courts (`20,696,054`), 24 Tribunals & Consumer Commissions, and DDL District Courts (`81M`) with `include_equivalent_provisions` and `binding_for_court` |
| `get_judgment` | `True` | Windowed judgment reader (`overview`, `paragraphs`, `pages`, `search_within`, `docket`) with direct AWS S3 `pypdfium2` extraction |
| `get_judgments_batch` | `True` | Partial-success batch fetch of up to 20 judgments (`overview`, `paragraphs`, `pages`, `search_within`, `docket`), enforcing `check_publication_ban` and `<corpus_text>` framing |
| `trace_precedent` | `False` (local SQLite core) | Citation graph (`cited_by` / `cites` / `both`), larger-bench filters, and quoting excerpts; always returns `precedent_status="unverified_automated"` |
| `judgment_analytics` | `True` (DuckDB Parquet) | Closed-schema partition-guarded aggregations over SC, 20.7M+ HC, 81M DDL District, 24 Tribunals, and 12 Regulators (256 MB limit, 10s `con.interrupt()`) |
| `get_cause_list` | `True` | Search Supreme Court & High Court daily cause lists locally (`cause_lists` SQLite table / `CauseListStore`) or via a configured BYO live docket endpoint (`live=True`) |
| `verify_citations` | `True` | Deterministic brief/citation audit (`VERIFIED`, `VERIFIED_REPEALED`, `VERIFIED_STRUCK_DOWN_OR_READ_DOWN`, `NOT_FOUND_IN_CORPUS`, `QUOTE_VERIFIED`, `QUOTE_MISMATCH`, etc.) |
| `corpus_status` | `False` (local SQLite core) | Unified coverage matrix, backend health, and `commercial_safe` licensing metadata |

*Corpus & reference breadth (`v2.1.0` / `corpus-v2026.11.25-p1`): **63** entries in `courts_directory.yaml` (SC, 25 High Courts & 45 Benches, **24** Tribunals & Consumer Commissions including `NCDRC`/`SCDRC`/`DCDRC`, `DRAT`, `AFT`, `GSTAT`, `RCT`, `CIC`, `SEBI_ADJ`, 12 Regulators, and DDL District Courts), **58 CPC Orders** (`104` rules), **148** criminal concordance mappings, **33** `ITAct1961↔ITAct2025` mappings, **19** `sources.lock.yaml` entries, `1,569` central acts, `8,544` state acts, `12,152` regulator instruments, `40,353` core provisions, `38,292` SC judgments (`38k+`), `20,696,054` HC judgments (`20.7M+`), `813,168+` tribunal orders, `615` citation edges, and `9` judicial invalidations.*

### 8 `nyaya://` Resources

- `nyaya://corpus/status`
- `nyaya://statutes`
- `nyaya://statute/{act}`
- `nyaya://provision/{act}/{ref}`
- `nyaya://amendment/{act}/{number}`
- `nyaya://judgment/{identifier}`
- `nyaya://judgment/{identifier}/paragraphs/{start}-{end}` *(capped at 30 paragraphs, 24,000 characters by default, 30,000-character maximum)*
- `nyaya://courts` *(Supreme Court, 25 High Courts & 45 Benches, 24 Tribunals & Consumer Commissions, 12 Regulators, DDL state codes; 63 directory entries)*

### 4 MCP Prompts

- `audit_legal_draft`: Filing-readiness citation and statutory-validity audit table
- `transition_criminal_code`: IPC/CrPC/IEA to BNS/BNSS/BSA transition matrix with BNSS s. 531 savings check
- `precedent_research_memo`: Binding SC vs binding home HC vs persuasive precedent memo
- `irac_case_brief`: Paragraph-pinned IRAC case brief with subsequent citations

## Quickstart

### Local `stdio` (zero configuration)

Local/source (`uv run nyaya-mcp`), Docker, and hosted HTTP (`https://nyaya.parag.tech/mcp`) work immediately. PyPI (`nyaya-mcp`) and Official MCP Registry (`server.json`) packages publish via `.github/workflows/release-pypi.yml` on `v*` tags (enabling `uvx nyaya-mcp` once released):

```bash
# Run locally from source (zero API keys needed)
cd mcp
uv sync --dev
uv run nyaya-mcp

# Or via uvx once published from a v* release tag:
uvx nyaya-mcp

# Restrict to commercial-safe datasets only (excludes CC-BY-NC DDL 81M & ILDC)
NYAYA_COMMERCIAL_SAFE_ONLY=1 uv run nyaya-mcp --commercial-safe-only

# Pre-download and SHA-256-verify local data packs (--pack accepts core|lance|parquet|all)
uv run nyaya-mcp sync --pack core
uv run nyaya-mcp --offline-only --no-download
```

### Local HTTP Server (from source)

```bash
cd mcp
uv sync --extra http --dev
uv run nyaya-mcp --transport http --host 127.0.0.1 --port 8000
# Equivalent subcommand: uv run nyaya-mcp serve --host 127.0.0.1 --port 8000 (alias CLI: `nyaya`)
# -> MCP endpoint: http://127.0.0.1:8000/mcp
# -> REST API:     http://127.0.0.1:8000/api/corpus-status
# -> Health check: http://127.0.0.1:8000/health
```

Optional `.env` loading: set `NYAYA_DOTENV=1` with the `dotenv` extra (`uv sync --extra dotenv`). Real environment variables always take precedence.

### HTTP & REST API Endpoints (`mcp/nyaya/http_app.py`, `mcp/nyaya/rest.py`)

| Endpoint | Method | Parameters & Purpose |
|---|---|---|
| `/mcp` | `POST` | Streamable HTTP MCP JSON-RPC 2.0 endpoint (`stateless_http=True`) |
| `/health` | `GET` | Service health check (`status`, `service`, `version`, `backends`, `counts`) |
| `/api/corpus-status`, `/api/corpus-stats` | `GET` | Full `CorpusStatusResponse` (`?court=&commercial_safe_only=`) |
| `/api/acts` | `GET` | Paginated statute & regulator catalog (`?jurisdiction=&category=&q=&limit=50&offset=0&commercial_safe_only=`, `limit` 1–200) |
| `/api/judgments` | `GET` | Multi-tier judgment search (`?tier=sc\|hc\|tribunal\|district\|all&court=&year_from=&year_to=&q=&limit=50&offset=0&commercial_safe_only=`; `tier` defaults to `supreme_court`, `limit` clamped 1–100 with `JudgmentSearchQuery` returning up to 25 per page) |
| `/api/analytics` | `GET` | Closed-schema DuckDB Parquet aggregations (`?dataset=sc\|hc\|district\|tribunal\|regulator&group_by=year\|disposal_nature\|court\|court_tier\|bench\|act\|case_type\|judge\|state\|district\|regulator&court=&state=&judge=&act=&year_from=&year_to=&top_n=25&commercial_safe_only=`; defaults `dataset=high_court`, `group_by=year`, `top_n` 1–100; 10s `con.interrupt()` timeout) |
| `/api/tools` | `GET` | Introspected MCP surface (`total: 15`, `resources_count: 8`, `prompts_count: 4`) |
| `/api/health-summary` | `GET` | Always-200 health + corpus summary for frontend SWR hydration |

When `NVIDIA_API_KEY` is set and `nyaya_chat` is installed, `http_app.py` also mounts the FastAPI chat sub-app at `/chat` (and serves the static Next.js SPA at `/` if `web/out` or `/app/web/out` exists).

## Environment Variables

All runtime settings are read lazily in `mcp/nyaya/config.py` (zero import-time I/O):

| Variable | Default | Purpose |
|---|---|---|
| `NYAYA_DATA_DIR` | `/app/data` (if dir exists) else `$XDG_CACHE_HOME/nyaya/data` (`~/.cache/nyaya/data`) | Directory containing `nyaya-core.sqlite` and optional local `lancedb/` / `parquet/` / `manifest.json` |
| `NYAYA_CACHE_DIR` | `/tmp/nyaya-cache` (in container) else `$XDG_CACHE_HOME/nyaya/cache` (`~/.cache/nyaya/cache`) | Cache directory for `pdf_cache/` (256 MiB zstd LRU) and `duckdb_tmp/` |
| `NYAYA_DUCKDB_EXT_DIR` | `/app/duckdb_ext` (if dir exists) else unset | Pre-installed DuckDB `httpfs` extension directory |
| `NYAYA_COMMERCIAL_SAFE_ONLY` | `false` | Globally exclude CC-BY-NC datasets (`DDL 81M`, `ILDC`) across SQLite, LanceDB, DuckDB, MCP, and REST |
| `NYAYA_OFFLINE_ONLY` | `false` | Disable all remote S3, Railway Bucket, and manifest network fetches |
| `NYAYA_MANIFEST_URL` | unset | Public `manifest.json` URL for `nyaya-mcp sync` / startup core download and remote Parquet manifest fallback |
| `NYAYA_DOTENV` | unset | Set truthy (`1`/`true`) with `[dotenv]` extra to load a local `.env` |
| `NYAYA_LOG_LEVEL` | `INFO` (`WARNING`/`ERROR` in `stdio` pre-init) | Server log level |
| `NYAYA_BUCKET_ENDPOINT`, `NYAYA_BUCKET_NAME`, `NYAYA_BUCKET_ACCESS_KEY_ID`, `NYAYA_BUCKET_SECRET_ACCESS_KEY` | unset | Optional Railway Storage Bucket (Tigris S3) config for remote LanceDB FTS, Parquet cold tier, and `nyaya-ingest publish` |
| `NYAYA_BUCKET_REGION`, `NYAYA_BUCKET_URL_STYLE` | `auto`, `vhost` (`vhost` \| `path`) | S3 region and addressing style (`vhost` for Railway/Tigris, `path` for MinIO) |
| `PORT` | `8000` | HTTP server bind port |
| `NYAYA_TRUSTED_PROXY_HOPS` | `1` | Right-most trusted proxy hop count for `X-Forwarded-For` / `X-Forwarded-Proto` |
| `NYAYA_ENABLE_OCR` | `false` | Optional opt-in Apache-2.0 OCR fallback (`tesseract` CLI or `rapidocr_onnxruntime`) for scanned pre-2010 PDFs |
| `NYAYA_HYBRID_SEARCH` | `false` | Optional opt-in hybrid BM25 + vector Reciprocal Rank Fusion (uses offline `HashedNgramEmbeddingProvider` when no endpoint is set) |
| `NYAYA_EMBEDDING_ENDPOINT` | unset | Optional OpenAI-compatible `/embeddings` endpoint URL (used only when `NYAYA_HYBRID_SEARCH=true`) |
| `NYAYA_EMBEDDING_MODEL` | unset | Optional embedding model name for `NYAYA_EMBEDDING_ENDPOINT` |
| `NYAYA_LIVE_DOCKET_API_URL` | unset | Optional BYO official/licensed live docket & cause-list endpoint URL for `get_cause_list(live=True)` (also accepts `NYAYA_LIVE_DOCKET_URL` fallback) |
| `NYAYA_LIVE_DOCKET_API_KEY` | unset | Optional bearer API key for `NYAYA_LIVE_DOCKET_API_URL` |
| `REDIS_URL` | unset | Optional Redis URL for distributed rate limiting across workers (`[http]` extra) |
| `NVIDIA_API_KEY` | unset | Optional NVIDIA API Catalog key (`nvapi-...`); mounts `/chat` sub-app when set |
| `GITHUB_TOKEN` / `GH_TOKEN`, `GITHUB_REPOSITORY` | unset | Used by `nyaya-ingest publish --target gh-release` via `gh` CLI |

## Storage, PDF Fetcher & HTTP Security

- **3-Tier Hybrid Corpus (`mcp/nyaya/storage/`):**
  - `sqlite_store.py`: Read-only immutable SQLite FTS5 core (`nyaya-core.sqlite`) with statutes, Constitution Amendments 1–106, CPC First Schedule (58 Orders / 104 rules), 148 criminal (`IPC/CrPC/IEA ↔ BNS/BNSS/BSA`) + 33 tax (`ITAct1961 ↔ ITAct2025`) concordance mappings, judicial invalidations, point-in-time reconstruction (`point_in_time.py`), SC metadata, `cause_lists` table, citation edges, and 63-entry courts directory.
  - `lance_store.py`: Lazy LanceDB native Rust BM25 FTS reader over **5** tables (`sc_chunks`, `hc_hot_chunks` with `hot_hc_limit=500_000`, `state_acts_regulators`, `tribunal_chunks`, `cad_debates`), local or on Railway Bucket (`s3://<bucket>/lancedb`), plus opt-in hybrid BM25 + vector RRF (`retrieval/hybrid.py` when `NYAYA_HYBRID_SEARCH=true`).
  - `duckdb_store.py` & `manifest.py`: Partition-pruned DuckDB `httpfs` over compacted ZSTD Parquet (`256MB` memory limit, `192MB` temp spill cap, `10s` `con.interrupt()` timeout). HC scans require a narrowing filter (`court`, `cnr`, `judge`, `act`, `party`, or `<=5`-year window); DDL District scans require `state`, `court`, `cnr`, or `<=5`-year window.
- **Direct AWS S3 PDF Fetcher (`mcp/nyaya/storage/pdf_fetcher.py`):** `DirectS3PdfFetcher` fetches judgment PDFs directly over anonymous HTTPS from strictly allowlisted buckets (`indian-supreme-court-judgments`, `indian-high-court-judgments` under `data/pdf/year=...`) and official tribunal portals, enforcing `15 MiB` / `500`-page limits, `%PDF-` magic checks, `pypdfium2` text extraction with SCR header stripping & ligature repair (plus opt-in Apache-2.0 OCR when `NYAYA_ENABLE_OCR=true`), and a `256 MiB` zstd disk LRU (`lru.sqlite`).
- **HTTP Middleware (`mcp/nyaya/ratelimit.py`, `mcp/nyaya/security_headers.py`):** Per-IP fixed-window rate limits (`120` reads, `30` MCP `POST`, `20` analytics, `15` chat `POST` req/min/IP; `50k`-key LRU or Redis via `REDIS_URL`, right-most `X-Forwarded-For`, unproxied loopback bypass), `1 MiB` (`1,048,576` B) streaming body-size cap, `X-Request-ID`, and CSP/XFO/COOP/CORP/proxy-aware HSTS headers.

## Ingestion Pipeline (`nyaya-ingest`)

`nyaya-ingest` (available via `uv sync --extra ingest`) builds and verifies SQLite, LanceDB, and Parquet corpus artifacts offline or in `.github/workflows/ingest-refresh.yml`:

```bash
cd mcp
uv sync --extra ingest
uv run nyaya-ingest sync --tier daily --work-dir ./work        # Dattam SC/HC + PRS deltas (or --tier static for Vaquill/DDL/ILDC/CAD)
uv run nyaya-ingest build-sqlite --work-dir ./work --out ./build/nyaya-core.sqlite   # fast: skips loading HC rows (served from Parquet)
uv run nyaya-ingest build-lance-fts --work-dir ./work --out ./build/lancedb
uv run nyaya-ingest compact-parquet --work-dir ./work --out ./build
uv run nyaya-ingest verify-gate --build-dir ./build --strict   # enforces 8 Legal Integrity Gate suites & emits gate-pass.json with SHA-256s
uv run nyaya-ingest publish --build-dir ./build --target all --tag v2026.11.22   # atomic blue/green swap on Railway Bucket + GitHub Releases

# Or run the full pipeline end-to-end (with --fixture and --dry-run for hermetic testing):
uv run nyaya-ingest all --fixture --dry-run
```

## Client Configuration

### Local (`stdio`, Recommended for Private Client Briefs)

From a source clone (works immediately):

```json
{
  "mcpServers": {
    "nyaya": {
      "command": "uv",
      "args": ["run", "--directory", "/path/to/nyaya/mcp", "nyaya-mcp"]
    }
  }
}
```

Or via `uvx` once published from a `v*` tag (`.github/workflows/release-pypi.yml`):

```json
{
  "mcpServers": {
    "nyaya": {
      "command": "uvx",
      "args": ["nyaya-mcp"]
    }
  }
}
```

*(Commercial firms can add `"env": { "NYAYA_COMMERCIAL_SAFE_ONLY": "1" }` or `"args": ["nyaya-mcp", "--commercial-safe-only"]`.)*

### Remote (Streamable HTTP)

```json
{
  "mcpServers": {
    "nyaya": {
      "url": "https://<your-domain>/mcp",
      "transport": "http"
    }
  }
}
```

### opencode / FastMCP Client

```bash
opencode mcp add nyaya -- uvx nyaya-mcp
# or remote HTTP:
opencode mcp add nyaya --transport http --url https://<your-domain>/mcp
```

```python
import asyncio
from fastmcp import Client

async def main():
    async with Client("https://<your-domain>/mcp") as client:
        tools = await client.list_tools()
        result = await client.call_tool("get_provision", {"citation": "Article 21"})
        print(len(tools), result)

asyncio.run(main())
```

## Development & Testing

```bash
cd mcp
uv sync --extra http --extra ingest --dev
uv run ruff check nyaya/ tests/
uv run mypy nyaya/
uv run pytest --cov=nyaya --cov-fail-under=78
uv lock --check
```

Build the hermetic offline test corpus manually:

```bash
cd mcp
uv run python -m tests.fixtures.mini_corpus.builder /tmp/mini_corpus
uv run nyaya-mcp --transport stdio --data-dir /tmp/mini_corpus --no-download --offline-only
```

## Project Structure

```text
mcp/
├── pyproject.toml                  # nyaya-mcp package, [http]/[dotenv]/[ingest]/[dev] extras
├── uv.lock                         # Locked dependencies (verified in CI + security.yml)
├── nyaya/
│   ├── cli.py                      # nyaya-mcp / nyaya entrypoint (--transport stdio|http, sync)
│   ├── mcp_app.py                  # Import-pure create_mcp() factory (zero stdout/env side effects)
│   ├── http_app.py                 # create_http_app() Starlette stack (/health, /mcp, /api/*, /chat, SPA)
│   ├── server.py                   # Compatibility shim delegating to cli.py / http_app.py
│   ├── config.py                   # Runtime Settings & rate-limit configuration
│   ├── exceptions.py               # 9 canonical NyayaError subclasses
│   ├── models.py                   # v2 Pydantic schemas & CorpusStatusResponse
│   ├── citations.py                # Shared Indian legal citation parser & verifier
│   ├── sanitize.py                 # OWASP LLM01 <corpus_text> framing, Tag strip & pub-ban checks
│   ├── ratelimit.py                # Right-most XFF, 50k-key LRU, streaming BodySizeLimitMiddleware
│   ├── security_headers.py         # Pure-ASGI CSP, XFO, COOP/CORP, proxy-aware HSTS
│   ├── rest.py                     # /api/corpus-status, /acts, /judgments, /analytics, /tools, /health-summary
│   ├── storage/                    # sqlite_store, lance_store, duckdb_store, pdf_fetcher, point_in_time, cause_list_store, live_docket, manifest, memory_backend
│   ├── retrieval/                  # query_analyzer, lexical_engine, hybrid, paragraph_bm25, ranking (RRF)
│   ├── ingest/                     # lockfile, state, fetchers/, extract/, builders/, gate, publish, data/
│   ├── tools/                      # 15 v2.1 MCP tool modules + fail-fast registry
│   ├── resources/                  # 8 nyaya:// resources
│   └── prompts/                    # 4 practitioner/student MCP prompts
└── tests/
    ├── fixtures/mini_corpus/       # Synthetic offline SQLite + LanceDB + Parquet + PDF builder
    └── ingest/                     # Lockfile, delta state, PDF/chunker, builder, gate & publish tests
```

## License

Code is **Apache-2.0** (`../LICENSE`). Bundled legal datasets retain upstream licenses and statutory permissions under **Indian Copyright Act, 1957 s. 52(1)(q)** and ***Eastern Book Company v. D.B. Modak*, (2008) 1 SCC 1** (including CC-BY-4.0 core compilations and CC-BY-NC research datasets `DDL 81M` and `ILDC`, filterable via `NYAYA_COMMERCIAL_SAFE_ONLY`). See [`../NOTICE`](../NOTICE) and [`../TRUSTED_SOURCES.md`](../TRUSTED_SOURCES.md).
