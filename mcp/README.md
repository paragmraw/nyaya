# nyaya

An [MCP](https://modelcontextprotocol.io) server for Indian law. Exposes the Constitution of India, IPC, CrPC, CPC, Evidence Act, the 2023 Sanhitas (BNS/BNSS/BSA), major commercial statutes, and landmark Supreme Court judgments as MCP tools and resources — so any MCP-compatible client (Claude, opencode, Cursor, ChatGPT, Gemini) can answer questions grounded in primary legal sources.

> **Not legal advice.** nyaya is a research and developer tool. Statutory text is sourced from public-domain and openly-licensed repositories; always verify against the official Gazette for any legal proceeding.

## What it gives you

- **16 tools**: `semantic_query` (embedding retrieval + reranking), `get_section`, `get_article`, `get_judgment`, `list_acts` / `list_chapters` / `list_sections` / `list_articles` / `list_judgments`, `cross_reference` (bidirectional), `list_schedules` / `get_schedule` / `list_amendments` / `get_amendment`, `get_amendments_for_article`, `corpus_stats`
- **11 resources**: `corpus://`, `acts://`, `schedules://`, `amendments://`, `judgments://`, `act://{name}`, `section://{act}/{num}`, `article://{num}`, `judgment://{slug}`, `amendment://{num}`, `schedule://{num}`
- **Semantic search** via pgvector + NVIDIA `nemotron-3-embed-1b` (2048-d, 32k context, 34 Indic languages) and `llama-nemotron-rerank-vl-1b-v2` reranker — works on any platform including Alpine (API-based, no native wheels needed)
- **Provenance on every result**: source, license, and `as_of` date (derived from the `acts` table, not hardcoded)
- **Input normalization**: act names and section numbers are case-insensitive, whitespace-trimmed, and alias-resolved (`ipc` → `IPC`)
- **Fuzzy judgment lookup**: `get_judgment` matches by exact citation, exact title, or fuzzy title substring (≥ 8 chars to avoid false matches)
- **Structured errors**: all `NotFound` responses return `is_error=true` with a machine-readable `structured_content={"error": {"code": "not_found", "message": "...", "kind": "section|article|act|judgment|schedule|amendment", "hint": "..."}}` so LLM clients can branch programmatically. `EmbeddingUnavailable` is distinct from "no matches".
- **Text projection controls on the list tools**: `list_sections` / `list_articles` / `list_judgments` return short text snippets by default (300 chars, tunable via `snippet_chars`); pass `include_text=true` for full text (large responses — prefer `get_section`/`get_article`/`get_judgment` for a single document). The REST equivalents expose the same choice: `include_text=false` by default, `?full=1` for full text.

## Corpus and sources

| Corpus | Source | License |
|---|---|---|
| Constitution (Articles 1–395) | `Vikhram-S/IndianConstitution` PyPI | Apache-2.0 |
| Constitution Schedules | constitutionofindia.net (CLPR) | Public domain (government edicts) |
| Constitution Amendments | Inline data (public record) | Public domain |
| IPC, IEA (Evidence Act), CPC | `civictech-India/Indian-Law-Penal-Code-Json` (GitHub) | Public domain (government edicts) |
| CrPC, Companies, GST (IGST+CGST), IT Act, Arbitration, Consumer Protection | `mratanusarkar/Indian-Laws` (HuggingFace) | Public domain (government edicts) |
| BNS, BNSS, BSA (2023) | PRS PDFs | CC BY 4.0 |
| Landmark SC judgments | indiankanoon.org (live fetch) | Public domain (government edicts) |
| IPC↔BNS cross-reference map | Inline data (PRS comparison docs) | — |

## Quickstart (local)

### Prerequisites

- Python 3.11+
- A Supabase project (free tier is fine) with the `pgvector` extension enabled
- An NVIDIA API key (get one at [build.nvidia.com](https://build.nvidia.com))

### 1. Set up Supabase

1. Create a project at [supabase.com](https://supabase.com).
2. Enable pgvector: **Database → Extensions → enable `vector`**.
3. The schema is applied by the hydration notebook (cell 3) — no separate `schema.sql` to run.

### 2. Configure environment

From the repo root:

```bash
cp .env.example .env
# Edit .env:
#   DATABASE_URL   — the Supabase/Postgres connection string
#   NVIDIA_API_KEY — your NVIDIA API Catalog key (required for embeddings + reranking)

# Then opt in to loading it: the server only reads .env when explicitly told to
# (so tests and CI that export real env vars are never surprised by a stray file).
export NYAYA_DOTENV=1
```

`.env` loading requires the optional `dotenv` extra (`pip install -e ".[dotenv]"`);
without it the flag is a silent no-op. Variables already set in the environment
always win — `load_dotenv()` does not override them. Tests and CI leave
`NYAYA_DOTENV` unset and pass variables directly.

### 3. Install and run

From the `mcp/` directory:

```bash
pip install -e .

# Hydrate Supabase (one-time) — open and run the notebook:
#   mcp/notebooks/hydrate.ipynb
# The notebook is self-contained: installs its own deps in cell 1, fetches all
# data sources live, embeds via the NVIDIA API, and writes to Postgres.

# Start the server
nyaya
# → Uvicorn running on http://0.0.0.0:8000
# → MCP endpoint at http://localhost:8000/mcp
# → Health check at http://localhost:8000/health
```

### 4. Hydrate the corpus (notebook)

`mcp/notebooks/hydrate.ipynb` is the single hydration path. Open it and run all cells. It:

1. Installs dependencies (cell 1).
2. Applies the schema (drops old tables, creates `acts` + `documents` + `cross_refs` with `vector(2048)`).
3. Fetches the Constitution, bare acts, Sanhitas, judgments, and cross-references — all live from their sources.
4. Embeds all documents via `nvidia/nemotron-3-embed-1b` (2048-d).
5. Writes to Postgres (idempotent upserts).
6. Runs sanity checks.

The notebook is **idempotent** — re-running it rebuilds the corpus from scratch. Run it whenever the corpus or embedding model changes.

## Deploy to Railway

nyaya ships with a Dockerfile (repo root) and a [Railway IaC](https://docs.railway.com/infrastructure-as-code) config at `.railway/railway.ts`. The build context is the repo root.

### 1. Create the project

```bash
railway login
railway link   # or `railway init` for a fresh project
railway up     # first deploy
```

### 2. Set environment variables

| Variable | Value |
|---|---|
| `DATABASE_URL` | Supabase/Postgres connection string |
| `NVIDIA_API_KEY` | NVIDIA API Catalog key (required for embeddings + reranking) |

`PORT` is set automatically by Railway (defaults to `8000`). Both secrets are `preserve()`d in `.railway/railway.ts`, so `railway config apply` never touches their values — but any variable you set in the Railway Variables tab must also appear there (or be added to the IaC file) before applying, since an apply can delete live variables absent from the file.

### Changing the deploy config

`.railway/railway.ts` is the source of truth for service settings (source branch, healthcheck, replicas, variables). It needs its TypeScript SDK installed locally — run `npm install` inside `.railway/` once. Edit the file, review with `railway config plan`, apply with `railway config apply --yes`. Code deploys ride on autodeploy from `main` (or a manual `railway up`). Railway's legacy `railway.toml`/`railway.json` Config-as-Code is deprecated (unread after 2026-12-01) and not used here.

### 3. Apply the schema migration (before/at first deploy)

The server expects two **additive** statements that may not exist on a database provisioned before the `ref_num` change:

```sql
alter table documents add column if not exists ref_num int
    generated always as (coalesce(nullif(regexp_replace(ref, '[^0-9].*$', ''), '')::int, 0)) stored;
create index if not exists documents_act_ref_num_idx on documents (act_id, ref_num);
```

Apply these to the deployed database (they are the only safe-to-apply block, at the bottom of `schema.sql`) **before deploying the image** — until they exist, `list_sections` (ORDER BY `ref_num`) fails with `UndefinedColumn` and the flagship list tool 500s. Everything else degrades fine.

> **Never run `psql -f schema.sql` against a populated database** — the file starts with `DROP ... CASCADE` statements and will destroy the corpus. See the warning header at the top of `schema.sql`.

### 4. Run ingestion (one-time)

Ingestion writes to Supabase, so you can run it from your local machine (with the same `.env`) — the Railway deployment reads from the same database. Run the notebook `mcp/notebooks/hydrate.ipynb` locally once, and the deployed server immediately serves the data.

### Health check

Railway polls `GET /health` (declared in `.railway/railway.ts`). The endpoint returns:

```json
{
  "status": "healthy",
  "service": "nyaya",
  "version": "0.2.0",
  "counts": {"section": 3257, "article": 464, "judgment": 5, "amendment": 106, "schedule": 12, "acts": 14, "cross_refs": 597}
}
```

## Image variants

The Dockerfile builds an **Alpine** image (~270 MB). Semantic search works on Alpine because embeddings + reranking are API-based (NVIDIA API), not local — no `onnxruntime` native wheels needed.

## Client configuration

### Claude Desktop

Add to `claude_desktop_config.json` (under Settings → Developer):

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

### Cursor

**Settings → MCP → Add server**:
- URL: `https://<your-domain>/mcp`
- Transport: Streamable HTTP

### opencode

```bash
opencode mcp add nyaya --transport http --url https://<your-domain>/mcp
```

### Programmatic (FastMCP client)

```python
import asyncio
from fastmcp import Client

async def main():
    async with Client("https://<your-domain>/mcp") as client:
        tools = await client.list_tools()
        result = await client.call_tool("get_article", {"article": "21"})
        print(result)

asyncio.run(main())
```

## Development

From the `mcp/` directory:

```bash
pip install -e ".[dev]"
pytest                          # unit tests (no DB needed)
pytest -m integration           # server boot + /health tests
pytest --cov=nyaya              # with coverage
ruff check .                    # lint
```

The test suite is fully offline: `tests/conftest.py` only pins the required env vars (`DATABASE_URL`, `NVIDIA_API_KEY`) to dummy values so `get_settings()` builds; the DB-layer fakes live next to the tests that use them (e.g. the `_FakeConn` pool fake in `tests/test_db.py`, the fake `db` functions in `tests/test_rest.py`). `pytest` runs with no Supabase and no network. Tests that boot the real ASGI app are marked `@pytest.mark.integration` and still run offline against fakes.

## Project structure

```
nyaya/                          # repo root
├── .railway/                   # Railway IaC (railway.ts deploy config)
├── docker-compose.yml          # local container run (build context = repo root)
├── .env.example                # copy to .env and fill in
├── Dockerfile                  # Alpine image (~270 MB)
├── README.md                   # repo overview (this file)
├── LICENSE                     # Apache-2.0
└── mcp/                        # the MCP server component
    ├── pyproject.toml          # package metadata + deps
    ├── README.md               # detailed setup + deploy docs (this file)
    ├── nyaya/                  # the Python package
    │   ├── server.py           # FastMCP app + /health
    │   ├── config.py           # env-var settings
    │   ├── db.py               # Postgres read layer (psycopg + pgvector)
    │   ├── embeddings.py       # NVIDIA API embed + rerank services
    │   ├── models.py           # Pydantic models (structured output)
    │   ├── exceptions.py       # NotFound etc.
    │   ├── tools/              # 16 MCP tools across 9 register modules
    │   │   ├── _error.py       # @structured_errors decorator
    │   │   └── _util.py        # run_sync, query validation
    │   └── resources/          # 11 MCP resources + templates
    ├── notebooks/
    │   └── hydrate.ipynb       # end-to-end hydration (fetch + embed + write)
    └── tests/                  # offline unit + integration tests
```

## License

Apache-2.0. The bundled legal text retains the license of its source (see the `source` and `source_license` fields on every result). Statutory text and judgments are public domain under Section 52(1)(q) of the Copyright Act 1957. PRS-sourced text is CC BY 4.0 (attribution required). The `indianconstitution` package is Apache-2.0.