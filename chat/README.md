# nyaya-chat

LangGraph + FastAPI chat backend (**v2.0.0**) for [nyaya](../README.md). A retrieval-grounded Indian-law assistant powered by `ChatNVIDIA` (`langchain-nvidia-ai-endpoints`) and backed by Nyaya v2.0's pure-lexical/metadata/graph corpus.

## Architecture

```text
SPA  ──POST /chat/turn (SSE)──►  nyaya.http_app (:8000)  ──in-process / streamable HTTP──►  v2 Corpus / /mcp
                                 └─ /chat sub-app (FastAPI, mounted iff NVIDIA_API_KEY is set)
                                     LangGraph agent -> tools -> agent graph
                                     ChatNVIDIA LLM + shared nyaya.citations verifier
```

The chat sub-app is mounted into `nyaya.http_app` at `/chat` whenever `NVIDIA_API_KEY` is set. Cross-cutting middleware (CORS, security headers, request-id, rate limiting, streaming body-size cap) lives on the host Starlette app; `nyaya_chat` owns the agent graph, prompts, guardrails, citation verification, and SSE projection.

- **Agent (`graph/agent.py`):** 2-node LangGraph graph (`START -> agent -> (tools -> agent | END)`) with `DedupToolNode`, optional 1-round gated reflection, and `SYNTHESIS_THINKING=False` / `SYNTHESIS_MAX_TOKENS=6144`. Skips outer `<corpus_text>` double-wrapping whenever tool JSON already carries parameterized `<corpus_text ...>` envelopes, and treats only `storage_unavailable` / `database_unavailable` / `bucket_unavailable` as hard storage failures.
- **8-Tool v2 Allowlist (`tools_layer/spec.py`):** Exposes 8 of the 12 MCP v2 tools to the agent:
  `search_statutes`, `get_provision`, `compare_provisions`, `browse_statute`, `get_provision_history`, `search_judgments`, `get_judgment`, `trace_precedent`.
  Native mode (`tools_layer/native.py`) calls `nyaya.storage` (`get_backend()`, `get_retrieval()`, `get_pdf_fetcher()`) directly; fallback mode calls `MCP_URL` over Streamable HTTP.
- **Shared Citation Verification (`citations.py` -> `nyaya.citations`):** Preserves inline `[[act: X, ref: Y]]` streaming markers while validating against canonical structural keys (`extract_grounded_keys`), covering SC/HC neutral citations (`2024 INSC 123`, `2024:DHC:1234`), 16-char CNRs, AIR/SCC/SCR, CPC `O.7 R.11`, and sub-sections (`s.65B(4)`), and rejecting mismatched page/section hallucinations (e.g., `AIR 1973 SC 9999` vs `AIR 1973 SC 1461`).
- **UI Cleaning (`tools_layer/cleaning.py`):** Strips parameterized `<corpus_text ...>` wrappers only from human-readable `tool_result` SSE summaries; synthesis LLM calls receive full `<corpus_text>` isolation boundaries.
- **Protection:** `POST /chat/*` is rate-limited at `15` req/min/IP by `mcp/nyaya/ratelimit.py`.

## Quickstart (standalone dev)

```bash
cd mcp
uv sync --extra http --dev
uv pip install -e ../chat

# Only NVIDIA_API_KEY is required to mount /chat
NVIDIA_API_KEY=nvapi-... uv run nyaya-mcp --transport http --host 127.0.0.1 --port 8000
# -> http://127.0.0.1:8000/chat/health
# -> http://127.0.0.1:8000/chat/docs
```

## Environment

- `NVIDIA_API_KEY` (required to mount `/chat`; not required for `nyaya-mcp` core tools)
- `MCP_URL` (optional; defaults to `http://localhost:${PORT}/mcp`)
- `NYAYA_COMMERCIAL_SAFE_ONLY` (optional; inherited by `native.py` tool execution)
- `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` (optional tracing)

All LLM parameters (`LLM_MODEL`, `SYNTHESIS_MODEL`, `SYNTHESIS_MAX_TOKENS`, `TURN_BUDGET_S`, `MAX_HISTORY`, `DEFAULT_TOOLS`) live in [`nyaya_chat/config.py`](nyaya_chat/config.py).

## API

### `GET /chat/health`

Returns `{"status": "healthy"|"degraded", "model": "...", "tools_loaded": 8, "reason": ...}` without blocking on graph compilation.

### `POST /chat/turn`

Accepts `{"message": "...", "history": [{"role": "user"|"assistant", "content": "..."}]}` and streams `text/event-stream` SSE events (`meta`, `status`, `plan`, `token`, `reasoning`, `tool_start`, `tool_result`, `citations`, `correction`, `ping`, `error`, `done`).

## Tests & Offline Evaluation

```bash
# Unit tests, lint & strict mypy (from mcp/ environment)
cd mcp
uv run ruff check ../chat/nyaya_chat/ ../chat/tests/ ../chat/eval/
uv run mypy ../chat/nyaya_chat/
NVIDIA_API_KEY=nvapi-test-key-for-ci uv run pytest ../chat/tests/ -v --cov=nyaya_chat --cov-fail-under=84

# 100% offline exact-match retrieval evaluation (zero NVIDIA_API_KEY needed)
uv run python ../chat/eval/retrieval_eval.py --fixture tests/fixtures/mini_corpus
```

Live-server evaluation (requires a running HTTP server with `NVIDIA_API_KEY` set):

```bash
cd chat
NYAYA_EVAL_HOST=http://localhost:8000 uv run pytest -m eval
uv run python -m eval.chat_eval --host http://localhost:8000 --verbose
uv run python -m eval.e2e_eval --golden eval/golden.jsonl --timeout 120
```

`eval/golden.jsonl` contains 90 test cases across Central/State/UT Acts, Constitution Articles/Schedules/Amendments 1–106, IPC/CrPC/IEA ↔ BNS/BNSS/BSA transitions, judicial invalidations, SC & HC precedent lookups, Regulators/Tribunals, DDL District analytics (`commercial_safe=false`), and adversarial/refusal traps.
