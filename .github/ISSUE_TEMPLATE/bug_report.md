---
name: Bug report
about: Create a report to help us improve Nyaya MCP v2.0
title: '[BUG] '
labels: bug
assignees: ''
---

## Describe the bug
A clear and concise description of what the bug is.

## To Reproduce
Steps to reproduce the behavior (e.g. MCP tool call, `nyaya-ingest` command, REST query, or UI interaction):
1. Run or call '...'
2. Pass arguments / visit '...'
3. See error

## Expected behavior
A clear and concise description of what you expected to happen.

## Logs / Tool Output / Screenshots
If applicable, paste the structured error response (`code`, `kind`, `hint`), JSON-RPC payload, or screenshot.

## Environment
- OS: [e.g. Ubuntu 24.04, Windows 11, macOS 15]
- Python version: [e.g. 3.12.16]
- Node version: [e.g. 20.18.0] (if `web/`-related)
- Nyaya version: [e.g. `nyaya-mcp 2.0.0`]
- Transport / Deployment: [`uvx nyaya-mcp` (`stdio`) / Local HTTP (`--transport http`) / Docker Compose (`read_only: true`) / Railway (`SQLite/LanceDB/DuckDB/S3`) / Hermetic `mini_corpus` fixture]
- Storage Tiers Active: [`nyaya-core.sqlite` only / Railway Storage Bucket (`LanceDB BM25` + `DuckDB Parquet`) / Direct AWS S3 PDFs / `--offline-only`]
- Commercial-Safe Mode: [`NYAYA_COMMERCIAL_SAFE_ONLY=false` (default) / `true`]
- Component: [`mcp/` / `ingest/` (`nyaya-ingest`) / `chat/` / `web/` / `all`]

## Additional context
Add any other context about the problem here.