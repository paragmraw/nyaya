## Description
Briefly describe the changes in this PR.

## Type of change
- [ ] Bug fix (non-breaking change which fixes an issue)
- [ ] New feature (non-breaking change which adds functionality)
- [ ] Breaking change (fix or feature that would cause existing functionality to not work as expected)
- [ ] Documentation update
- [ ] Refactoring (no functional changes)
- [ ] Performance improvement
- [ ] Test addition/update
- [ ] CI/CD / Infra change

## Component(s) affected
- [ ] `mcp/` (MCP v2.0 server, storage/retrieval, REST `/api/*`, SQLite/LanceDB/DuckDB/S3)
- [ ] `ingest/` (`mcp/nyaya/ingest/` pipeline, reference YAML tables, `verify-gate`)
- [ ] `chat/` (Optional LangGraph + FastAPI `/chat` sub-app, shared citation verifier, evals)
- [ ] `web/` (Next.js 16 static export SPA)
- [ ] `docs` (`README.md`, `CHANGELOG.md`, `CONTRIBUTING.md`, `TRUSTED_SOURCES.md`, `SECURITY.md`, `NOTICE`)
- [ ] `infra` (`Dockerfile`, `docker-compose.yml`, `.railway/railway.ts`, `.github/workflows/`)
- [ ] other

## Testing
Describe the tests you ran to verify your changes:
- [ ] MCP unit/contract tests pass (`cd mcp && uv run pytest --cov=nyaya --cov-fail-under=78`)
- [ ] Chat unit tests pass (`cd mcp && NVIDIA_API_KEY=nvapi-test-key-for-ci uv run pytest ../chat/tests/ --cov=nyaya_chat --cov-fail-under=84`)
- [ ] Offline retrieval eval passes (`cd mcp && uv run python ../chat/eval/retrieval_eval.py --fixture tests/fixtures/mini_corpus`)
- [ ] Python lint & strict types pass (`cd mcp && uv run ruff check nyaya/ tests/ ../chat/nyaya_chat/ ../chat/tests/ ../chat/eval/ && uv run mypy nyaya/ ../chat/nyaya_chat/`)
- [ ] Lockfiles are in sync (`cd mcp && uv lock --check` and `cd chat && uv lock --check`)
- [ ] Web lint, tests & static export build pass (`cd web && npx eslint . && npm test && npm run build`)
- [ ] Manual / Docker testing performed (describe below)

## Checklist
- [ ] My code follows the project's style guidelines (`CONTRIBUTING.md`)
- [ ] No runtime `stdout` output is emitted in `nyaya` code paths (`stdio` JSON-RPC safety)
- [ ] Primary legal text fields remain wrapped via `frame_corpus_text()` (`<corpus_text ... trust="untrusted_primary_law">`)
- [ ] No copyleft dependencies or unlicensed sources were introduced (`TRUSTED_SOURCES.md`)
- [ ] I have performed a self-review of my code
- [ ] I have made corresponding changes to the documentation
- [ ] I have added tests that prove my fix is effective or that my feature works
- [ ] New and existing tests pass locally with my changes

## Related issues
Closes #(issue number)

## Screenshots (if applicable)
Add screenshots for UI changes.

## Additional notes
Any additional information, configuration, or deployment notes.