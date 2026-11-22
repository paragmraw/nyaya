# nyaya-mcp

An [MCP](https://modelcontextprotocol.io) server for Indian law (**v2.0.0**, PyPI distribution `nyaya-mcp`). Runs locally over `stdio` via `uvx nyaya-mcp` with zero API keys, or as a stateless Railway HTTP server combining a baked SQLite FTS5 core, LanceDB native Rust BM25 FTS, DuckDB compacted ZSTD Parquet, and direct AWS Open Data PDF extraction (`pypdfium2`).

> **Not legal advice.** `nyaya-mcp` is a legal research and citation-auditing tool. Always verify primary texts against the official Gazette and check subsequent judicial treatment before filing.

## MCP v2.0 Surface (12 Tools · 8 Resources · 4 Prompts)

### 12 Tools

All 12 tools are read-only (`readOnlyHint=True`, `destructiveHint=False`, `idempotentHint=True`), enforce `commercial_safe_only`, attach `SourceProvenance`, and wrap primary text in `<corpus_text ... trust="untrusted_primary_law">`:

| Tool | Scope (`openWorldHint`) | Purpose |
|---|---|---|
| `search_statutes` | `True` (SQLite + Bucket LanceDB) | Lexical BM25 / exact-phrase search across Central Acts, Constitution, 36 State/UT Acts, and 12 Regulators |
| `get_provision` | `True` | Verbatim section/article/schedule/order-rule + `StatutoryValidity` envelope + top cross-links |
| `browse_statute` | `True` | Search statute/regulator catalog, browse Act TOC, or paginate provisions |
| `compare_provisions` | `False` (local SQLite core) | IPC↔BNS, CrPC↔BNSS, IEA↔BSA concordance, clause diffs, punishment/procedural deltas, BNSS s. 531 savings |
| `cross_reference` | `False` (local SQLite core) | Bidirectional statutory and judicial citation cross-references |
| `get_provision_history` | `False` (local SQLite core) | Constitutional Amendments 1–106, statutory amendments, and judicial invalidation timelines |
| `search_judgments` | `True` | Multi-facet search across SC (35k+), High Courts (17.77M+), 15 Tribunals, and DDL District Courts (81M) with `include_equivalent_provisions` and `binding_for_court` |
| `get_judgment` | `True` | Windowed judgment reader (`overview`, `paragraphs`, `pages`, `search_within`, `docket`) with direct AWS S3 `pypdfium2` extraction |
| `trace_precedent` | `False` (local SQLite core) | Citation graph (`cited_by` / `cites` / `both`), larger-bench filters, and quoting excerpts; always returns `precedent_status="unverified_automated"` |
| `judgment_analytics` | `True` (DuckDB Parquet) | Closed-schema partition-guarded aggregations over SC, 17.77M HC, 81M DDL District, Tribunals, and Regulators |
| `verify_citations` | `True` | Deterministic brief/citation audit (`VERIFIED`, `VERIFIED_REPEALED`, `VERIFIED_STRUCK_DOWN_OR_READ_DOWN`, `NOT_FOUND_IN_CORPUS`, `QUOTE_VERIFIED`, `QUOTE_MISMATCH`, etc.) |
| `corpus_status` | `False` (local SQLite core) | Unified coverage matrix, backend health, and `commercial_safe` licensing metadata |

### 8 `nyaya://` Resources

- `nyaya://corpus/status`
- `nyaya://statutes`
- `nyaya://statute/{act}`
- `nyaya://provision/{act}/{ref}`
- `nyaya://amendment/{act}/{number}`
- `nyaya://judgment/{identifier}`
- `nyaya://judgment/{identifier}/paragraphs/{start}-{end}` *(capped at 30 paragraphs / 25 KB)*
- `nyaya://courts` *(Supreme Court, 25 High Courts & 45 Benches, 15 Tribunals, 12 Regulators, DDL state codes)*

### 4 MCP Prompts

- `audit_legal_draft` — Filing-readiness citation and statutory-validity audit table
- `transition_criminal_code` — IPC/CrPC/IEA to BNS/BNSS/BSA transition matrix with BNSS s. 531 savings check
- `precedent_research_memo` — Binding SC vs binding home HC vs persuasive precedent memo
- `irac_case_brief` — Paragraph-pinned IRAC case brief with subsequent citations

## Quickstart

### Local `stdio` (zero configuration)

```bash
# Zero secrets needed — automatically fetches/verifies public core pack on first run
uvx nyaya-mcp

# Restrict to commercial-safe datasets only (excludes CC-BY-NC DDL 81M & ILDC)
NYAYA_COMMERCIAL_SAFE_ONLY=1 uvx nyaya-mcp --commercial-safe-only

# Pre-download packs for offline laptop use
uvx nyaya-mcp sync --pack core
uvx nyaya-mcp --offline-only --no-download
```

### Local HTTP Server (from source)

```bash
cd mcp
uv sync --extra http --dev
uv run nyaya-mcp --transport http --host 127.0.0.1 --port 8000
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
| `/api/corpus-status` | `GET` | Full `CorpusStatusResponse` (`?court=&commercial_safe_only=`) |
| `/api/acts` | `GET` | Paginated statute & regulator catalog (`?jurisdiction=&category=&q=&limit=50&offset=0&commercial_safe_only=`) |
| `/api/judgments` | `GET` | Multi-tier judgment search (`?tier=sc\|hc\|tribunal\|district\|all&court=&year_from=&year_to=&q=&limit=50&offset=0&commercial_safe_only=`) |
| `/api/analytics` | `GET` | Closed-schema DuckDB Parquet aggregations (`?dataset=sc\|hc\|district\|tribunal\|regulator\|all&group_by=year\|disposal_nature\|court\|bench\|act\|case_type\|judge\|state\|district\|regulator&court=&state=&judge=&act=&year_from=&year_to=&top_n=25&commercial_safe_only=`, e.g. `/api/analytics?dataset=sc&group_by=disposal_nature`) |
| `/api/tools` | `GET` | Introspected MCP surface (`total: 12`, `resources_count: 8`, `prompts_count: 4`) |
| `/api/health-summary` | `GET` | Always-200 health + corpus summary for frontend SWR hydration |

## Ingestion Pipeline (`nyaya-ingest`)

`nyaya-ingest` (available via `uv sync --extra ingest`) builds and verifies Option R2 corpus artifacts offline or in `.github/workflows/ingest-refresh.yml`:

```bash
cd mcp
uv sync --extra ingest
uv run nyaya-ingest sync --tier daily --work-dir ./work        # Dattam SC/HC + PRS deltas (or --tier static for Vaquill/DDL/ILDC/CAD)
uv run nyaya-ingest build-sqlite --work-dir ./work --out ./build/nyaya-core.sqlite
uv run nyaya-ingest build-lance-fts --work-dir ./work --out ./build/lancedb
uv run nyaya-ingest compact-parquet --work-dir ./work --out ./build/parquet
uv run nyaya-ingest verify-gate --build-dir ./build --strict   # emits gate-pass.json binding exact SHA-256s
uv run nyaya-ingest publish --build-dir ./build --target all   # atomic blue/green swap on Railway Bucket + HF/GH

# Or run the full pipeline end-to-end (with --fixture and --dry-run for hermetic testing):
uv run nyaya-ingest all --fixture --dry-run
```

## Client Configuration

### Local (`uvx` over `stdio` — Recommended for Private Client Briefs)

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
│   ├── config.py                   # Option R2 Settings & rate-limit configuration
│   ├── exceptions.py               # 9 canonical NyayaError subclasses
│   ├── models.py                   # v2 Pydantic schemas & CorpusStatusResponse
│   ├── citations.py                # Shared Indian legal citation parser & verifier
│   ├── sanitize.py                 # OWASP LLM01 <corpus_text> framing, Tag strip & pub-ban checks
│   ├── ratelimit.py                # Right-most XFF, 50k-key LRU, streaming BodySizeLimitMiddleware
│   ├── security_headers.py         # Pure-ASGI CSP, XFO, COOP/CORP, proxy-aware HSTS
│   ├── rest.py                     # /api/corpus-status, /acts, /judgments, /analytics, /tools, /health-summary
│   ├── storage/                    # sqlite_store, lance_store, duckdb_store, pdf_fetcher, manifest, memory_backend
│   ├── retrieval/                  # query_analyzer, lexical_engine, paragraph_bm25, ranking (RRF)
│   ├── ingest/                     # lockfile, state, fetchers/, extract/, builders/, gate, publish, data/
│   ├── tools/                      # 12 v2 MCP tool modules + fail-fast registry
│   ├── resources/                  # 8 nyaya:// resources
│   └── prompts/                    # 4 practitioner/student MCP prompts
└── tests/
    ├── fixtures/mini_corpus/       # Synthetic offline SQLite + LanceDB + Parquet + PDF builder
    └── ingest/                     # Lockfile, delta state, PDF/chunker, builder, gate & publish tests
```

## License

Code is **Apache-2.0** (`../LICENSE`). Bundled legal datasets retain upstream licenses and statutory permissions under **Indian Copyright Act, 1957 s. 52(1)(q)** and ***Eastern Book Company v. D.B. Modak*, (2008) 1 SCC 1** (including CC-BY-4.0 core compilations and CC-BY-NC research datasets `DDL 81M` and `ILDC`, filterable via `NYAYA_COMMERCIAL_SAFE_ONLY`). See [`../NOTICE`](../NOTICE) and [`../TRUSTED_SOURCES.md`](../TRUSTED_SOURCES.md).
