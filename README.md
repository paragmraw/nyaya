# nyaya

An open-source Indian legal research platform and [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) server. Search Indian statutes, constitutional amendments, criminal-code transitions, tribunal/regulatory instruments, and Supreme Court & High Court judgments with citation verification, without running an external database or needing API keys for local use.

**Live Explorer:** [nyaya.parag.tech](https://nyaya.parag.tech) · **Remote MCP:** `https://nyaya.parag.tech/mcp` · **Docs:** [MCP Server](mcp/README.md) · [Web Frontend](web/README.md) · [Chat Assistant](chat/README.md) · [Trusted Sources](TRUSTED_SOURCES.md) · [Security](SECURITY.md)

---

## What is this project about?

**Nyaya** (`nyaya-mcp` v2.0) gives legal practitioners, researchers, and AI assistants deterministic, citation-grounded access to primary Indian law without external vector databases or embedding APIs.

- **Comprehensive Indian legal corpus:**
  - **Constitution & Statutes:** Constitution of India (Amendments 1–106), 1,569 Central Acts, 8,544 State & UT Acts across 36 States/UTs (40,353 indexed provisions; 37 legislation jurisdictions in total), **BNS / BNSS / BSA 2023 ↔ IPC / CrPC / IEA** concordance with substantive diffs, and a **Judicial Invalidations** registry (e.g., IT Act s. 66A, Electoral Bonds).
  - **Judiciary:** 38,000+ Supreme Court judgments (`38,292`) and 20.7M+ High Court judgments and orders (`20,696,054` across 25 High Courts), plus on-demand PDF text extraction from AWS Open Data.
  - **Regulators & Tribunals:** 12 financial and sectoral regulators (12,152 instruments) and 15 tribunals (813,168 orders).
  - **Citation verification & parsing:** Priority-ordered Indian citation parser (`INSC`, High Court neutral citations, `AIR`, `SCC`, `SCR`, `CNR`, CPC Orders/Rules, Constitution Articles/Schedules, and chained statute sections) that validates claims against the corpus before surfacing results.
- **Deterministic & database-free:** Uses pure-lexical search (SQLite FTS5 + LanceDB native Rust BM25 + DuckDB over ZSTD Parquet) rather than opaque vector embeddings, so exact section numbers, case citations, and statutory phrases always match reliably.
- **12 MCP tools, 8 resources, and 4 prompts:** Exposes structured legal lookup, statute comparison, precedent tracing, docket analytics, and draft citation auditing over `stdio` or HTTP.

---

## Who is this for?

- **Lawyers, legal researchers & students:** Compare repealed IPC/CrPC/IEA provisions against BNS/BNSS/BSA 2023, check whether a provision has been judicially struck down, trace Supreme Court precedent chains, and audit legal drafts for hallucinated citations.
- **AI & MCP users (Claude Desktop, Cursor, IDEs):** Connect Nyaya directly as an MCP tool server in seconds via `uvx nyaya-mcp` (zero API keys, zero setup) or via the hosted `/mcp` endpoint.
- **Developers & legal-tech builders:** Self-host a single hardened container serving the web explorer, `/api/*` JSON endpoints, `/mcp` Streamable HTTP endpoint, and an optional `/chat` assistant, with `NYAYA_COMMERCIAL_SAFE_ONLY=1` to strictly exclude non-commercial research datasets (`DDL`, `ILDC`).

---

## How to use it?

### 1. Connect to an AI client (MCP via `uvx`, zero API keys)

Requires Python 3.11.5+ and [`uv`](https://docs.astral.sh/uv/). Run directly from your terminal:

```bash
uvx nyaya-mcp
```

Or add it to your MCP client configuration (Claude Desktop, Cursor, etc.):

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

Common CLI options:

- `--commercial-safe-only` (or `NYAYA_COMMERCIAL_SAFE_ONLY=1`): exclude non-commercial datasets (`DDL` 81M District Courts, `ILDC`)
- `nyaya-mcp sync`: pre-download and verify the local SQLite `core` pack from `NYAYA_MANIFEST_URL`
- `--offline-only --no-download --data-dir /path/to/data`: run air-gapped using local packs only
- `nyaya-mcp --transport http --host 0.0.0.0 --port 8000`: start the HTTP server (MCP + REST + Web UI)

### 2. Run locally with Docker

Runs the full stack (Web UI at `/`, REST API at `/api/*`, MCP at `/mcp`, and optional `/chat` when `NVIDIA_API_KEY` is set) inside a read-only container with a 1 GB memory limit and zero required secrets:

```bash
cp .env.example .env   # optional; works out of the box with zero secrets
docker compose up -d --build
```

Then open `http://localhost:8000` in your browser, or run `stdio` mode inside a hardened container:

```bash
docker build -t nyaya-mcp .
docker run --rm -i --read-only --tmpfs /tmp:size=64m -w /tmp nyaya-mcp nyaya-mcp --offline-only --no-download
```

### 3. Hosted access, local development & deployment

- **Hosted instance:** Browse the corpus explorer at [nyaya.parag.tech](https://nyaya.parag.tech) or point HTTP-capable MCP clients to `https://nyaya.parag.tech/mcp`.
- **Local development & tests:** See [`CONTRIBUTING.md`](CONTRIBUTING.md) and each component's README ([`mcp/README.md`](mcp/README.md), [`web/README.md`](web/README.md), [`chat/README.md`](chat/README.md)).
- **Cloud deployment & ingestion:** Infrastructure is declared in [`.railway/railway.ts`](.railway/railway.ts) and corpus build/publish commands (`nyaya-ingest`) are documented in [`mcp/README.md`](mcp/README.md).

---

## How it is built & architecture

Nyaya is packaged as a single Python ASGI server (`mcp/`) that serves the MCP protocol, thin REST endpoints, the pre-built Next.js static frontend (`web/out/`), and an optional LangGraph chat router (`chat/`):

```text
  MCP Clients (stdio / HTTP) ──┐
  Web Browser (/)            ──┼──▶ Starlette ASGI Server (mcp/)
  REST & Optional /chat      ──┘       ├── /mcp          (12 tools · 8 resources · 4 prompts)
                                       ├── /api/*        (REST endpoints for web & clients)
                                       ├── /chat/*       (Optional LangGraph + NVIDIA NIM)
                                       └── /             (Next.js 16 static export)
                                              │
                 ┌────────────────────────────┼────────────────────────────┐
                 ▼                            ▼                            ▼
       Baked SQLite Core          S3-Compatible Object Storage     AWS Open Data PDFs
    (nyaya-core.sqlite, FTS5)     (LanceDB BM25 + ZSTD Parquet)    (Anonymous HTTPS S3)
    • Statutes & Constitution     • Full-text BM25 chunks          • SC & HC judgment PDFs
    • Concordance & Invalidations • 20.7M+ HC & Tribunal analytics • pypdfium2 extraction
    • SC metadata & citations     • DuckDB httpfs (256 MB / 10s)   • 256 MB zstd disk LRU
```

### Repository layout

| Component | Version | Role & Stack | Documentation |
|---|---|---|---|
| [`mcp/`](mcp/) | `2.0.0` | Core `nyaya-mcp` Python package: 12 MCP tools, 8 resources, 4 prompts, REST `/api/*`, SQLite/LanceDB/DuckDB/PDF storage layers, security/rate-limit middleware, and `nyaya-ingest` CLI. | [`mcp/README.md`](mcp/README.md) |
| [`web/`](web/) | `2.0.0` | Next.js 16 + React 19 static SPA (`web/out/`) providing the Home, Corpus Explorer, Citation Anatomy, and Architecture pages plus FAQ and legal/info pages (privacy, terms, disclaimer, accessibility, contact). | [`web/README.md`](web/README.md) |
| [`chat/`](chat/) | `2.0.0` | Optional FastAPI + LangGraph assistant (`nyaya-chat`) using an 8-tool allowlist, shared citation verification, and `ChatNVIDIA` (mounted at `/chat` only when `NVIDIA_API_KEY` is set). | [`chat/README.md`](chat/README.md) |

---

## Licensing, Sources & Security

- **Code license:** Apache-2.0; see [`LICENSE`](LICENSE) and [`NOTICE`](NOTICE).
- **Legal corpus & exclusions:** Primary Indian statutes and judgments are reproduced under **Indian Copyright Act, 1957 s. 52(1)(q)** and ***Eastern Book Company v. D.B. Modak*, (2008) 1 SCC 1** (commercial reporter headnotes and s. 228A / BNS s. 72 / POCSO protected orders are excluded). See [`TRUSTED_SOURCES.md`](TRUSTED_SOURCES.md).
- **Non-commercial datasets:** Research packs can include `DDL` 81M District Courts (`CC-BY-NC-SA-4.0`) and `ILDC` (`CC-BY-NC-4.0`) tagged `commercial_safe=false`. Commercial deployments should set `NYAYA_COMMERCIAL_SAFE_ONLY=1`.
- **Security & prompt-injection isolation:** All retrieved legal text is framed as untrusted data (`<corpus_text ... trust="untrusted_primary_law">`, OWASP LLM01). Rate limits, streaming body caps, security headers, and vulnerability reporting are documented in [`SECURITY.md`](SECURITY.md).
- **Contributing & releases:** See [`CONTRIBUTING.md`](CONTRIBUTING.md), [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md), and [`CHANGELOG.md`](CHANGELOG.md).
