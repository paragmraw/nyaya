# nyaya

An [MCP](https://modelcontextprotocol.io) server for Indian law. Exposes the Constitution of India, IPC, CrPC, CPC, Evidence Act, the 2023 Sanhitas (BNS/BNSS/BSA), major commercial statutes, and landmark Supreme Court judgments as MCP tools and resources — so any MCP-compatible client (Claude, opencode, Cursor, ChatGPT, Gemini) can answer questions grounded in primary legal sources.

> **Not legal advice.** nyaya is a research and developer tool. Statutory text is sourced from public-domain and openly-licensed repositories; always verify against the official Gazette for any legal proceeding.

## What it gives you

- **24 tools**: `search_law`, `get_section`, `get_article`, `list_acts` / `list_chapters` / `list_sections` / `list_articles` / `list_judgments`, `cross_reference` (bidirectional), `semantic_query`, `get_judgment`, `search_judgments`, `get_sections_by_range`, `get_chapter`, `list_schedules` / `get_schedule` / `list_amendments` / `get_amendment` / `get_amendments_for_article`, `get_definition`, `corpus_stats`, `hybrid_search`, `resolve_citation`, `search_by_kind`
- **11 resources**: `corpus://`, `acts://`, `schedules://`, `amendments://`, `judgments://`, `act://{name}`, `section://{act}/{num}`, `article://{num}`, `judgment://{slug}`, `amendment://{num}`, `schedule://{num}`
- **Full-text search** via Postgres `tsvector` + GIN indexes, with true total counts and `offset` pagination
- **Semantic search** via `pgvector` + local `fastembed` embeddings (BAAI/bge-large-en-v1.5, 1024-d) — available in local dev and the slim-based image; **disabled in the Alpine image** (onnxruntime has no musllinux wheels — see [Image variants](#image-variants)). CUDAExecutionProvider is used on NVIDIA GPUs with automatic CPU fallback.
- **Provenance on every result**: source, license, and `as_of` date (derived from the `acts` table, not hardcoded)
- **Input normalization**: act names and section numbers are case-insensitive, whitespace-trimmed, and alias-resolved (`ipc` → `IPC`)
- **Structured errors**: `NotFound` with `kind` (act/section/article/judgment/schedule/amendment) and `hint`; `EmbeddingUnavailable` distinct from "no matches"

## Corpus and sources

| Corpus | Source | License |
|---|---|---|
| Constitution (Articles 1–395) | `Vikhram-S/IndianConstitution` | Apache-2.0 |
| Constitution Schedules + Amendments | PRS PDFs / official MoLJ PDF | CC BY 4.0 / public domain |
| IPC, CrPC, CPC, Evidence Act, Companies, GST, IT, Arbitration, Consumer Protection | `mratanusarkar/Indian-Laws` (HuggingFace) | Public domain (government edicts) |
| BNS, BNSS, BSA (2023) | PRS PDFs | CC BY 4.0 |
| Landmark SC judgments | Curated from indiankanoon.org browse view | Public domain |
| IPC↔BNS cross-reference map | PRS comparison docs | — |

Government sites (`indiacode.nic.in`, `legislative.gov.in`, `main.sci.gov.in`) are used only as reference for spot-checks — they're unreliable for automated access.

## Quickstart (local)

### Prerequisites

- Python 3.11+
- A Supabase project (free tier is fine) with the `pgvector` extension enabled

### 1. Set up Supabase

1. Create a project at [supabase.com](https://supabase.com).
2. Enable pgvector: **Database → Extensions → enable `vector`**.
3. Apply the schema (run from the `mcp/` directory):
   ```bash
   psql "postgresql://postgres:<password>@db.<project>.supabase.co:5432/postgres" -f scripts/schema.sql
   ```
   Or paste the contents of `scripts/schema.sql` into the Supabase SQL editor.

### 2. Configure environment

From the repo root:

```bash
cp .env.example .env
# Edit .env:
#   DATABASE_URL  — the Supabase/Postgres connection string
```

### 3. Install and run

From the `mcp/` directory:

```bash
# Install with semantic + dev extras
pip install -e ".[semantic,dev]"

# Hydrate Supabase (one-time) — see "Hydrate via notebook" below for the
# recommended interactive path, or use the CLI:
nyaya-ingest all

# Start the server
nyaya
# → Uvicorn running on http://0.0.0.0:8000
# → MCP endpoint at http://localhost:8000/mcp
# → Health check at http://localhost:8000/health
```

### 4. Hydrate the corpus (notebook — recommended)

`mcp/notebooks/hydrate.ipynb` is the primary hydration path. Open it from the
`mcp/` directory (so `data/manual/...` and `scripts/schema.sql` relative paths
resolve) and run all cells. It:

1. Applies the schema (idempotent), confirming the embedding columns are `vector(1024)`.
2. Ingests the Constitution, bare acts (HuggingFace), Sanhitas (PRS PDFs), judgments, and cross-references — reusing the same `nyaya.scripts.ingest_*` functions as the CLI.
3. Builds **enriched** pgvector embeddings (sections/articles/judgments get an `act | ref | title` prefix before the text) with `BAAI/bge-large-en-v1.5`, using `CUDAExecutionProvider` on NVIDIA GPUs with automatic `CPUExecutionProvider` fallback. Progress bars via `tqdm`.
4. Runs sanity checks: counts match, `vector_dims(embedding) = 1024`, a semantic query (`right to privacy` → Puttaswamy), and an FTS query (`murder` → IPC/BNS).

The notebook is idempotent — every ingest step upserts on conflict, and `cross_refs`
has a unique constraint so re-runs don't accumulate duplicates. Run it whenever the
corpus changes.

### 5. Hydrate via CLI (non-interactive alternative)

The `nyaya-ingest` CLI wraps the same ingestion functions and is useful for
scripts/CI. The ingestion scripts pull from HuggingFace, PRS, and the bundled
Constitution JSON. Run them in order:

```bash
nyaya-ingest schema           # apply schema.sql (idempotent)
nyaya-ingest constitution     # Articles 1–395 + schedules + amendments
nyaya-ingest civictech        # IPC, IEA, CPC from civictech JSON (full text)
nyaya-ingest bare-acts        # CrPC + commercial statutes from HuggingFace
nyaya-ingest sanhitas         # BNS, BNSS, BSA from PRS PDFs
nyaya-ingest judgments        # landmark SC judgments from data/manual/judgments.yaml
nyaya-ingest cross-refs       # IPC↔BNS mapping + inline references
nyaya-ingest embeddings       # pgvector embeddings (~1.3GB model download on first run)
nyaya-ingest counts           # show row counts
```

`nyaya-ingest all` runs everything in order. The CLI now embeds **enriched**
text (act/ref/title prefix, matching the notebook) so CLI and notebook
hydrations produce identical-quality vectors.

> **Manual data**: `data/manual/judgments.yaml` ships with full verbatim text for 5 landmark judgments (Kesavananda Bharati, Maneka Gandhi, K.S. Puttaswamy, Shah Bano, Navtej Singh Johar) pasted from [indiankanoon.org](https://indiankanoon.org) (free browse, no API needed). Extend it with more cases as needed. Same for `data/manual/ipc_bns_map.yaml` (the starter map now covers ~160 sections — extend it) and `data/manual/schedules/*.md` (all 12 schedules filled from the official Constitution PDF).

## Deploy to Railway

nyaya ships with a Dockerfile (under `mcp/`) and a root `railway.toml` configured for Railway. The build context is the repo root; the Dockerfile's `COPY` paths are prefixed with `mcp/`.

### 1. Create the project

From the repo root:

```bash
railway init
railway up
```

Or connect the GitHub repo via the Railway dashboard for automatic deploys.

### 2. Set environment variables

In the Railway dashboard → **Variables**, set:

| Variable | Value |
|---|---|
| `DATABASE_URL` | Supabase/Postgres connection string |

`PORT` is set automatically by Railway (defaults to `8000`).

### 3. Run ingestion (one-time)

Ingestion writes to Supabase, so you can run it from your local machine (with the same `.env`) — the Railway deployment reads from the same database. From the `mcp/` directory, run `nyaya-ingest all` locally once, and the deployed server immediately serves the data.

### Health check

Railway polls `GET /health` (configured in `railway.toml`). The endpoint returns:

```json
{
  "status": "healthy",
  "service": "nyaya",
  "version": "0.1.0",
  "counts": {"acts": 12, "sections": 511, "articles": 395, "judgments": 5, ...}
}
```

## Image variants

The Dockerfile (`mcp/Dockerfile`) builds an **Alpine** image — the smallest option.

| Variant | Base | Size | Semantic search | When to use |
|---|---|---|---|---|
| **Alpine** (default) | `python:3.12-alpine` | **~269 MB** | No | Railway, size-constrained hosts. `search_law` (FTS) and all other tools work. |

### Why semantic search is disabled on Alpine

`semantic_query` depends on `fastembed`, which depends on `onnxruntime`. Onnxruntime publishes only `manylinux` (glibc) wheels — there are no `musllinux` wheels, so it cannot run on Alpine. The Alpine Dockerfile does not install the semantic extra; the `semantic_query` tool stays registered (so `tools/list` advertises it) but returns an empty result when called. The other 23 tools are unaffected — `search_law` (Postgres FTS) handles the keyword-search use case.

### Claude Desktop

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

## API reference

### Tools

| Tool | Args | Returns |
|---|---|---|
| `search_law` | `query: str`, `act: str?`, `limit: int=10`, `offset: int=0` | FTS hits with true total + pagination (global offset across corpora) |
| `get_section` | `act: str`, `section: str` | Full section text + provenance |
| `get_article` | `article: str` | Constitution article + provenance |
| `list_acts` | — | All acts in the corpus |
| `list_chapters` | `act: str` | Chapters of an act |
| `list_sections` | `act: str`, `chapter: int?`, `limit: int=100`, `offset: int=0` | Sections of an act (paginated, with total) |
| `list_articles` | `part: str?`, `limit: int=100`, `offset: int=0` | Constitution articles by Part (paginated, with total) |
| `list_judgments` | `limit: int=50`, `offset: int=0` | All landmark judgments (paginated, with total) |
| `cross_reference` | `act: str`, `section: str`, `direction: str="both"` | Bidirectional cross-refs (from+to) |
| `semantic_query` | `query: str`, `act: str?`, `limit: int=5` | Embedding-NN hits (raises `EmbeddingUnavailable` if disabled) |
| `get_judgment` | `case_slug: str` | Full judgment by citation or slug |
| `search_judgments` | `query: str`, `court: str?`, `date_from: str?`, `date_to: str?`, `limit: int=10`, `offset: int=0` | FTS over judgments (validates ISO dates) |
| `get_sections_by_range` | `act: str`, `start: str`, `end: str`, `limit: int=500` | Sections in a numeric range |
| `get_chapter` | `act: str`, `chapter: int` | Chapter + all its sections |
| `list_schedules` | — | All 12 Constitution schedules |
| `get_schedule` | `number: int` | A single schedule |
| `list_amendments` | `year_from: int?`, `year_to: int?` | Constitutional amendments |
| `get_amendment` | `number: int` | A single amendment |
| `get_amendments_for_article` | `article: str` | Amendments that affected an article |
| `get_definition` | `term: str`, `act: str?`, `limit: int=10` | Statutory definitions (targets definition-titled sections) |
| `corpus_stats` | — | Corpus counts + as_of date |
| `hybrid_search` | `query: str`, `act: str?`, `limit: int=10`, `offset: int=0` | RRF-fused FTS + semantic results (falls back to FTS) |
| `resolve_citation` | `citation: str` | Parses 'IPC s.302'/'Art.21'/'AIR 1973 SC 1461' and fetches the provision |
| `search_by_kind` | `query: str`, `kind: str="section"`, `act: str?`, `limit: int=10`, `offset: int=0` | FTS filtered to section/article/judgment |

### Resources

| URI | Description |
|---|---|
| `corpus://` | Corpus overview + counts (as_of derived from DB) |
| `acts://` | List of all acts |
| `schedules://` | Constitution schedules |
| `amendments://` | Constitution amendments |
| `judgments://` | List of all landmark judgments |
| `act://{short_name}` | Act metadata + table of contents |
| `section://{act}/{number}` | Full section |
| `article://{number}` | Constitution article |
| `judgment://{case_slug}` | Landmark judgment |
| `amendment://{number}` | A single amendment |
| `schedule://{number}` | A single schedule |

## Development

From the `mcp/` directory:

```bash
pip install -e ".[semantic,dev]"
pytest                          # unit tests (no DB needed)
pytest -m integration           # server boot + /health tests
pytest --cov=nyaya              # with coverage
ruff check .                    # lint
```

The test suite is fully offline: `tests/conftest.py` stubs the DB layer with canned data, so `pytest` runs with no Supabase and no network. Integration tests that boot the ASGI app are marked `@pytest.mark.integration`.

## Project structure

```
nyaya/                          # repo root
├── railway.toml                # Railway deploy config (points to mcp/Dockerfile)
├── docker-compose.yml          # local container run (build context = repo root)
├── .env.example                # copy to .env and fill in
├── .dockerignore               # excludes tests, venvs, caches from the image
├── README.md                   # repo overview
├── LICENSE                     # Apache-2.0
└── mcp/                        # the MCP server component
    ├── Dockerfile              # Alpine image (~269 MB, no semantic search)
    ├── pyproject.toml          # package metadata + deps
    ├── README.md               # detailed setup + deploy docs (this file)
    ├── nyaya/                  # the Python package
    │   ├── server.py           # FastMCP app + /health
    │   ├── config.py           # env-var settings
    │   ├── db.py               # Postgres read layer (psycopg + pgvector)
    │   ├── embeddings.py       # fastembed query embedding
    │   ├── models.py           # Pydantic models (structured output)
    │   ├── exceptions.py       # NotFound etc.
    │   ├── tools/              # 24 MCP tools across 17 modules
    │   └── resources/          # 11 MCP resources + templates
    ├── scripts/
    │   └── schema.sql          # Supabase DDL (idempotent, vector(1024) embeddings)
    ├── notebooks/
    │   └── hydrate.ipynb       # end-to-end hydration (ingest + embeddings + sanity)
    ├── data/manual/            # curated YAML + schedule text
    └── tests/                  # offline unit + integration tests
```

## License

Apache-2.0. The bundled legal text retains the license of its source (see the `source` and `source_license` fields on every result). Statutory text and judgments are public domain under Section 52(1)(q) of the Copyright Act 1957. PRS-sourced text is CC BY 4.0 (attribution required). The `indianconstitution` package is Apache-2.0.