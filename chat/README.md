# nyaya-chat

LangGraph + FastAPI chat backend (**v2.0.0**, Python `>=3.11`) for [nyaya](../README.md). A retrieval-grounded Indian-law assistant powered by `ChatNVIDIA` (`langchain-nvidia-ai-endpoints`, default model `nvidia/nemotron-3.5-lightning-30b-a3b`) and backed by Nyaya v2.0's pure-lexical/metadata/graph corpus.

## Architecture

```text
SPA  ──POST /chat/turn (SSE)──►  nyaya.http_app (:8000)  ──in-process / streamable HTTP──►  v2 Corpus / /mcp
                                 └─ /chat sub-app (FastAPI, mounted iff NVIDIA_API_KEY is set)
                                     Intent guardrail (Tier 1 regex -> Tier 2 LLM)
                                     LangGraph agent -> tools -> agent graph
                                     ChatNVIDIA LLM + shared nyaya.citations verifier
```

The chat sub-app is mounted into `nyaya.http_app` at `/chat` whenever `NVIDIA_API_KEY` is set. Cross-cutting middleware (CORS, security headers, request-id, rate limiting, streaming body-size cap) lives on the host Starlette app; `nyaya_chat` owns the intent guardrail, agent graph, prompts, citation verification, and SSE projection.

- **Intent Guardrail (`guardrail.py`):** Two-tier pre-agent classifier (Tier 1 instant regex/keyword patterns; Tier 2 Nemotron structured output with `max_tokens=32`, `5s` timeout, failing open to `LEGAL`). Non-legal turns (greetings, capabilities, thanks, off-topic) stream canned SSE responses without invoking tools.
- **Agent (`graph/agent.py`):** 2-node LangGraph graph (`START -> agent -> (tools -> agent | END)`) with `DedupToolNode`, optional 1-round gated reflection (`MAX_REFLECTION_ROUNDS=1`, `REFLECTION_DEADLINE_S=12.0`), `TURN_BUDGET_S=60.0`, and `SYNTHESIS_THINKING=False` / `SYNTHESIS_MAX_TOKENS=6144`. Skips outer `<corpus_text>` double-wrapping whenever tool JSON already carries parameterized `<corpus_text ...>` envelopes, and treats only `storage_unavailable` / `database_unavailable` as hard storage failures.
- **8-Tool v2 Allowlist (`tools_layer/spec.py`):** Exposes 8 of the 12 MCP v2 tools to the agent:
  `search_statutes`, `get_provision`, `compare_provisions`, `browse_statute`, `get_provision_history`, `search_judgments`, `get_judgment`, `trace_precedent`.
  Native mode (`tools_layer/native.py`) calls `nyaya.tools.*` functions directly in-process; fallback mode (`tools_layer/mcp_fallback.py`) calls `MCP_URL` over Streamable HTTP.
- **Shared Citation Verification (`citations.py` -> `nyaya.citations`):** Preserves inline `[[act: X, ref: Y]]` streaming markers while validating against canonical structural keys (`extract_grounded_keys`), covering SC/HC neutral citations (`2024 INSC 123`, `2024:DHC:1234`), 16-char CNRs, AIR/SCC/SCR, CPC `O.7 R.11`, and sub-sections (`s.65B(4)`), and rejecting mismatched page/section hallucinations (e.g., `AIR 1973 SC 9999` vs `AIR 1973 SC 1461`).
- **UI Cleaning (`tools_layer/cleaning.py`):** Strips parameterized `<corpus_text ...>` wrappers only from human-readable `tool_result` SSE summaries; synthesis LLM calls receive full `<corpus_text>` isolation boundaries.
- **Protection:** `POST /chat/*` is rate-limited at `15` req/min/IP by `mcp/nyaya/ratelimit.py`.

## Quickstart (standalone dev)

Install `nyaya-chat` into the `mcp/` environment using the locked export + `--no-deps` flow (matching `.github/workflows/ci.yml` and `Dockerfile` so shared pins such as `starlette` and `mcp` align with `mcp/uv.lock`; `fastapi` is a chat-only dependency and is not constrained by `mcp/uv.lock`):

```bash
cd mcp
uv sync --extra http --dev
uv export --project ../chat --locked --no-dev --no-emit-project --no-editable --format requirements-txt > /tmp/chat-requirements.txt
uv pip install -r /tmp/chat-requirements.txt
uv pip install --no-deps -e ../chat
rm -f /tmp/chat-requirements.txt

# Only NVIDIA_API_KEY is required to mount /chat
NVIDIA_API_KEY=nvapi-... uv run nyaya-mcp --transport http --host 127.0.0.1 --port 8000
# -> http://127.0.0.1:8000/chat/
# -> http://127.0.0.1:8000/chat/health
# -> http://127.0.0.1:8000/chat/docs
```

*(To enable optional Langfuse tracing, also install the `langfuse` extra: `uv pip install -e "../chat[langfuse]"` or `uv sync --extra langfuse` in `chat/`.)*

## Environment

- `NVIDIA_API_KEY` (required to mount `/chat`; not required for `nyaya-mcp` core tools)
- `MCP_URL` (optional; defaults to `http://localhost:${PORT}/mcp`, used only in fallback mode when `nyaya` is not importable)
- `NYAYA_COMMERCIAL_SAFE_ONLY` (optional; inherited by `native.py` tool execution)
- `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST` (optional tracing; all three required when enabling Langfuse)

All LLM parameters (`LLM_MODEL`, `SYNTHESIS_MODEL`, `SYNTHESIS_MAX_TOKENS`, `TURN_BUDGET_S`, `MAX_HISTORY`, `DEFAULT_TOOLS`) live in [`nyaya_chat/config.py`](nyaya_chat/config.py).

## API

### `GET /chat/` & `GET /chat/openapi.json`

Returns the sub-app service info payload (`{"service": "nyaya-chat", "version": "2.0.0", "turn": "POST /chat/turn", "health": "GET /chat/health"}`) and OpenAPI schema.

### `GET /chat/health`

Returns `{"status": "healthy"|"degraded", "model": "...", "tools_loaded": 8, "reason": ...}` without blocking on graph compilation.

### `POST /chat/turn`

Accepts `{"message": "...", "history": [{"role": "user"|"assistant", "content": "..."}]}` (`message`: 1–4,000 chars non-blank; `history[].content`: 1–8,000 chars, server retains the last `MAX_HISTORY=8` turns) and streams `text/event-stream` SSE events (`meta` with `request_id`, `status`/`error` with `rid`, `plan`, `token`, `reasoning`, `tool_start`, `tool_result`, `citations`, `correction`, `ping`, `done`).

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

Live-server & end-to-end evaluation (requires `NVIDIA_API_KEY`, plus a running HTTP server on `NYAYA_EVAL_HOST` for `pytest -m eval` / `eval.chat_eval`):

```bash
# Live SSE harness via pytest marker (runs tests/test_chat_eval.py -> eval/chat_eval.py)
cd chat
NYAYA_EVAL_HOST=http://localhost:8000 uv run pytest -m eval
uv run python -m eval.chat_eval --host http://localhost:8000 --verbose

# Standalone golden-dataset E2E agent eval (run from mcp/ where nyaya + nyaya_chat are installed, or with MCP_URL set)
cd mcp
NVIDIA_API_KEY=nvapi-... uv run python ../chat/eval/e2e_eval.py --golden ../chat/eval/golden.jsonl --timeout 120
```

`eval/golden.jsonl` contains 90 test cases across Central/State/UT Acts, Constitution Articles/Schedules/Amendments 1–106, IPC/CrPC/IEA ↔ BNS/BNSS/BSA transitions, judicial invalidations, SC & HC precedent lookups, Regulators/Tribunals, DDL District analytics (`commercial_safe=false`), and adversarial/refusal traps.
