# nyaya-chat

LangGraph + FastAPI chat backend for [nyaya](../README.md). A retrieval-grounded
Indian-law assistant: a ReAct agent over the nyaya MCP corpus, powered by an
NVIDIA Nemotron chat model, streamed to clients over Server-Sent Events.

## Architecture

```
SPA  ──POST /chat/turn (SSE)──►  nyaya Starlette app :8000  ──streamable HTTP──►  /mcp (same process)
                                └─ /chat sub-app (FastAPI)
                                    LangGraph agent
                                    ChatNVIDIA Nemotron
```

The chat backend is a **FastAPI sub-app** mounted into the existing nyaya
MCP/Starlette server at `/chat` (see `mcp/nyaya/server.py`). The SPA, REST
API, MCP endpoint, and chat share one origin, one healthcheck, and one
Railway service. The sub-app owns only chat-specific concerns (the agent,
the LLM, the SSE encoder); cross-cutting middleware (CORS, security headers,
request-id, rate limiting, body-size cap) is provided by the host.

- **Agent**: a single-node async ReAct loop (`agent ↔ tools`) compiled with no
  checkpointer — conversation state is per-request only; we do not persist
  anything. The model emits citations inline as `[[act: X, ref: Y]]` markers,
  which the frontend parses into chips.
- **Tools**: the agent calls the nyaya MCP server over streamable HTTP via
  `langchain-mcp-adapters`. In the same-process deploy, `NYAYA_MCP_URL`
  points at the same origin's `/mcp`.
- **LLM**: `ChatNVIDIA(model="nvidia/nemotron-3-super-120b-a12b")`, reads
  `NVIDIA_API_KEY` from the environment. Thinking mode is on by default so
  reasoning tokens stream as a separate `event: reasoning` SSE channel.
- **Streaming**: LangGraph v2 `messages` stream mode -> typed SSE events
  (`token`, `reasoning`, `tool_start`, `tool_result`, `status`, `done`,
  `error`).
- **Protection**: a tighter per-IP rate limit on `POST /chat/*`
  (`NYAYA_RATE_CHAT_PER_MIN`, default 15/min) applied by the host's
  `RateLimitMiddleware`. No auth (same model as the MCP server - behind a
  reverse proxy).

## Quickstart (standalone dev)

```bash
cd chat
uv venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
uv pip install -e ".[dev]"

# .env at the repo root (shared with mcp/) must set NVIDIA_API_KEY and DATABASE_URL.
# Start the nyaya MCP server first (provides the corpus):
cd ../mcp && uv pip install -e ../chat && nyaya &

# The chat sub-app is mounted automatically when NVIDIA_API_KEY is set.
# Set NYAYA_MCP_URL so the agent reaches the MCP server:
export NYAYA_MCP_URL=http://localhost:8000/mcp
# -> http://localhost:8000/chat  (Swagger UI at /chat/docs)
```

## Environment

See `.env.example`. Required: `NVIDIA_API_KEY`. Optional: `NYAYA_MCP_URL`,
`CHAT_LLM_MODEL`, `CHAT_TOOLS`.

## API

### `GET /chat/health`
```json
{"status":"healthy","model":"nvidia/nemotron-3-super-120b-a12b","tools_loaded":9}
```

### `POST /chat/turn`
**Body**: `{"message": "...", "history": [{"role":"user","content":"..."}, ...]}`
**Response**: `text/event-stream` of typed SSE events:

| event | data | meaning |
|---|---|---|
| `token` | `{"content": "..."}` | LLM token delta |
| `reasoning` | `{"content": "..."}` | reasoning_content delta (thinking mode) |
| `tool_start` | `{"id","name","args"}` | the model called a tool |
| `tool_result` | `{"id","name","summary"}` | a tool returned |
| `status` | `{"msg": "thinking"}` | progress |
| `error` | `{"message", "detail"}` | a node failed |
| `done` | `{}` | stream complete |

## Tests

```bash
cd chat && pytest        # unit tests (no live NVIDIA/MCP calls)
ruff check . && mypy nyaya_chat
```