# nyaya-chat

LangGraph + FastAPI chat backend for [nyaya](../README.md). A retrieval-grounded
Indian-law assistant: a supervisor-synthesis agent over the nyaya MCP corpus,
powered by an NVIDIA Nemotron chat model, streamed to clients over
Server-Sent Events.

## Architecture

```
SPA  ──POST /chat/turn (SSE)──►  nyaya Starlette app :8000  ──streamable HTTP──►  /mcp (same process)
                                 └─ /chat sub-app (FastAPI)
                                     LangGraph supervisor-synthesis agent
                                     ChatNVIDIA Nemotron
```

The chat backend is a **FastAPI sub-app** mounted into the existing nyaya
MCP/Starlette server at `/chat` (see `mcp/nyaya/server.py`). The SPA, REST
API, MCP endpoint, and chat share one origin, one healthcheck, and one
Railway service. The sub-app owns only chat-specific concerns (the agent,
the LLM, the SSE encoder); cross-cutting middleware (CORS, security headers,
request-id, rate limiting, body-size cap) is provided by the host.

- **Agent**: a two-phase LangGraph supervisor-synthesis graph (see
  `agent.py`). The **supervisor** node receives the user question, briefly
  reasons about which MCP tools to call, and emits ALL tool calls in a
  single `AIMessage` for parallel execution — it does not answer the
  question itself. The **tools** node (`DedupToolNode`) runs all tool calls
  concurrently and deduplicates repeated (name+args) calls. The **synthesis**
  node receives all tool results as `ToolMessage`s and composes the final
  grounded answer with inline `[[act: X, ref: Y]]` citation markers, which
  the frontend parses into chips. No checkpointer is used — conversation
  state is per-request only; we do not persist anything.
- **Tools**: the agent calls the nyaya MCP server over streamable HTTP via
  `langchain-mcp-adapters`' `MultiServerMCPClient`. In the same-process
  deploy, `MCP_URL` in `chat/nyaya_chat/config.py` points at the same
  origin's `/mcp`. A curated 9-tool allowlist (`DEFAULT_TOOLS` in
  `config.py`) is exposed to the agent; the full 24-tool surface is
  available to direct MCP clients.
- **LLM**: `ChatNVIDIA`, reads `NVIDIA_API_KEY` from the environment. The
  default model is `nvidia/nemotron-3.5-lightning-30b-a3b` (see `config.py`).
  The supervisor and synthesis phases each get their own model instance with
  distinct token caps (`SUPERVISOR_MAX_TOKENS=512`, `SYNTHESIS_MAX_TOKENS=4096`).
- **Streaming**: LangGraph v2 dual stream mode (`["messages", "updates"]`)
  -> typed SSE events (`status`, `plan`, `token`, `reasoning`, `tool_start`,
  `tool_result`, `error`, `done`). Supervisor node content is routed to
  `plan` events so it doesn't mix with the synthesis answer (`token` events).
  Phase transitions emit `status` events (`analyzing` -> `searching` ->
  `composing`).
- **Retry**: model invocations (supervisor `ainvoke`, synthesis `astream`)
  wrap with exponential backoff + full jitter on HTTP 429 and 5xx errors
  (`LLM_MAX_RETRIES=4`).
- **Protection**: a tighter per-IP rate limit on `POST /chat/*`
  (`RATE_CHAT_PER_MIN`, default 15/min, hardcoded in `mcp/nyaya/config.py`)
  applied by the host's `RateLimitMiddleware`. No auth (same model as the
  MCP server - behind a reverse proxy).

## Quickstart (standalone dev)

```bash
cd chat
uv venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
uv pip install -e ".[dev]"

# .env at the repo root (shared with mcp/) must set NVIDIA_API_KEY and DATABASE_URL.
# Start the nyaya MCP server first (provides the corpus):
cd ../mcp && uv pip install -e ../chat && nyaya &
# The chat sub-app is mounted automatically when NVIDIA_API_KEY is set.
# The agent calls the MCP server at http://localhost:8000/mcp (MCP_URL in
# chat/nyaya_chat/config.py).
# -> http://localhost:8000/chat  (Swagger UI at /chat/docs)
```

## Environment

Only `NVIDIA_API_KEY` is required (env var). All other tuning (MCP URL, LLM
models, temperatures, token caps, message limits, tool allowlist, log level)
lives as Python constants in `chat/nyaya_chat/config.py`.

## API

### `GET /chat/health`
```json
{"status":"healthy","model":"nvidia/nemotron-3.5-lightning-30b-a3b","tools_loaded":9}
```

### `POST /chat/turn`
**Body**: `{"message": "...", "history": [{"role":"user","content":"..."}, ...]}`
**Response**: `text/event-stream` of typed SSE events:

| event | data | meaning |
|---|---|---|
| `status` | `{"msg": "analyzing"\|"searching"\|"composing"}` | phase transition (supervisor -> tools -> synthesis) |
| `plan` | `{"content": "..."}` | supervisor plan text (routed separately from the answer) |
| `token` | `{"content": "..."}` | synthesis LLM token delta (the final answer) |
| `reasoning` | `{"content": "..."}` | reasoning_content delta (forward-compat for reasoning-capable models) |
| `tool_start` | `{"id","name","args"}` | the model called a tool |
| `tool_result` | `{"id","name","summary"}` | a tool returned |
| `error` | `{"message", "detail"}` | a node failed |
| `done` | `{}` | stream complete |

## Tests

```bash
cd chat && pytest        # unit tests (no live NVIDIA/MCP calls)
ruff check . && mypy nyaya_chat
```