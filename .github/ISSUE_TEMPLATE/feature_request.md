---
name: Feature request
about: Suggest an idea for this project
title: '[FEATURE] '
labels: enhancement
assignees: ''
---

## Is your feature request related to a problem? Please describe.
A clear and concise description of what the problem is. Ex. I'm always frustrated when [...]

## Describe the solution you'd like
A clear and concise description of what you want to happen.

## Describe alternatives you've considered
A clear and concise description of any alternative solutions or features you've considered.

## Component
Which component(s) would this affect?
- [ ] `mcp/` (MCP v2.0 server: 12 tools, 8 `nyaya://` resources, 4 prompts, REST `/api/*`, Option R2 storage/retrieval)
- [ ] `ingest/` (`mcp/nyaya/ingest/`: `nyaya-ingest` CLI, `sources.lock.yaml`, reference YAML data, `verify-gate`)
- [ ] `chat/` (Optional LangGraph + FastAPI `/chat` assistant, shared `nyaya.citations` verifier, `retrieval_eval.py`)
- [ ] `web/` (Next.js 16 static export SPA)
- [ ] `docs` (`README.md`, `TRUSTED_SOURCES.md`, `SECURITY.md`, `NOTICE`)
- [ ] `infra` (`Dockerfile`, `docker-compose.yml`, `.railway/railway.ts`, `.github/workflows/`)
- [ ] other

## Additional context
Add any other context or screenshots about the feature request here.