# nyaya-web

Next.js 16 + React 19 static-export frontend (**v2.0.0**) for [nyaya](../README.md). Builds into static HTML/CSS/JS (`web/out/`) served from the same Starlette origin (`mcp/nyaya/http_app.py`) as the MCP server, `/api/*` REST routes, and optional `/chat` assistant.

## Routes

| Route | Purpose | Data Source |
|---|---|---|
| `/` | Home workspace: capabilities summary, live corpus counters, `uvx`/HTTP MCP config generator, and optional `/chat` panel | `/api/corpus-status`, `/chat/health`, `/chat/turn` (SSE) |
| `/corpus/` | Open-India-Law corpus explorer with category/status/commercial-safe filters, deep-link highlighting (`?act=...&ref=...`), and licensing notice | `/api/corpus-status`, `/api/acts`, build-time `corpus-stats.json` |
| `/citations/` | Interactive citation anatomy (`nyaya/citations.py`), supported Indian citation formats, 4-stage verification pipeline, and audit status reference | Static documentation |
| `/architecture/` | Option R2 architecture overview, interactive pipeline stages, runtime/governance stack, MCP config, and live tool/resource/prompt surface | `/api/tools` (with `MCP_TOOLS_FALLBACK`) |

## Tech Stack

- **Framework:** Next.js `^16.4.6` (`output: "export"`, `trailingSlash: true`, `images.unoptimized: true`, `productionBrowserSourceMaps: false`), React `^19.0.0`, TypeScript `5.9.3`
- **Data Fetching & Rendering:** `swr` `2.5.1` (`src/lib/swr.ts`, `src/lib/api.ts`), `react-markdown` `^10.1.0` + `remark-gfm` `^4.0.1`
- **SEO & Assets:** `next-sitemap` `^4.2.3` (`next-sitemap.config.cjs`), `scripts/generate-corpus-stats.ts`, `scripts/generate-og.ts`
- **Quality & Testing:** ESLint 9 flat config (`eslint.config.mjs` with `eslint-config-next`, `@typescript-eslint`, `react-hooks`, `jsx-a11y`), `tsx` + `node:test` (`tests/*.test.ts`)

## Quickstart (Local Development)

Run the Python HTTP backend on `:8000` (from `mcp/`) and the Next.js dev server on `:3000`:

```bash
# Terminal 1: start the backend (from mcp/)
cd mcp
uv sync --extra http --dev
uv run nyaya-mcp --transport http --host 127.0.0.1 --port 8000

# Terminal 2: start the frontend dev server (from web/)
cd web
npm install
npm run dev
```

During `npm run dev`, `next.config.mjs` proxies `/api/:path*`, `/mcp`, and `/chat/:path*` to `http://localhost:8000/...`. In production builds (`output: "export"`), Starlette serves `web/out/` directly from the same origin.

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `NEXT_PUBLIC_CHAT_ENABLED` | unset (`false` in `page.tsx`; `true` in `Dockerfile` build arg) | Build-time feature flag baked into the static export to enable/disable the `/chat` panel on `/`. Even when `"true"`, the backend mounts `/chat` only if `NVIDIA_API_KEY` is set. |

## Build-Time Data & Asset Generators

- `npm run generate:corpus-stats` runs `tsx scripts/generate-corpus-stats.ts`, reading `BASELINE_COUNTS` & `CURATED` (`src/lib/data.ts`) and `API_VERSION` (`src/lib/site.ts`) to emit synchronized snapshots at `src/data/corpus-stats.json` and `public/corpus-stats.json`. Pass `--from-manifest <path>` to override counts from a live corpus-status JSON file.
- `npx tsx scripts/generate-og.ts` deterministically regenerates `public/og-default.png` (`1200x630`).
- `next-sitemap.config.cjs` emits `sitemap.xml` and `robots.txt` into `out/` during `npm run build`. Keep `siteUrl` in `next-sitemap.config.cjs` synchronized with `SITE` in `src/lib/site.ts`.

## Scripts, Linting & Testing

```bash
cd web
npx eslint .      # ESLint 9 flat config (also `npm run lint`)
npm test          # Runs generate:corpus-stats + 17 node:test suites via tsx
npm run build     # Runs generate:corpus-stats + next build + next-sitemap -> out/
```

## Project Structure

```text
web/
├── public/                     # Static icons, og-default.png, generated corpus-stats.json
├── scripts/
│   ├── generate-corpus-stats.ts # Emits src/data/corpus-stats.json + public/corpus-stats.json
│   └── generate-og.ts          # Generates 1200x630 PNG OG image
├── src/
│   ├── app/                    # App Router pages (/, /corpus/, /citations/, /architecture/)
│   ├── components/             # UI, chat, MCP config/tools, corpus table, and JSON-LD components
│   ├── data/corpus-stats.json  # Build-time CorpusStatusResponse snapshot
│   └── lib/                    # API client, SWR hooks, curated metadata, schemas, chat state
├── tests/                      # 17 node:test unit & structural validation suites
├── eslint.config.mjs           # ESLint 9 flat configuration
├── next.config.mjs             # Static export + dev-only proxy rewrites
└── next-sitemap.config.cjs     # Sitemap & robots.txt configuration
```
