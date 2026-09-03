# syntax=docker/dockerfile:1.7
#
# Multi-stage Dockerfile for the Nyaya monorepo: builds the Next.js static
# export in a Node stage, then assembles the Python runtime that serves the
# SPA + REST + MCP from one origin. Build context is the repo root.

# --- Stage 1: build the SPA (static export -> /web/out) -----------------------
# node:20-alpine pinned by digest for reproducible builds (amd64).
FROM node:20-alpine@sha256:afdf98210b07b586eb71fa22ba2e432e058e4cd1304d31ed60888755b8c865fb AS web-builder
ENV NODE_ENV=production
# Feature flag for chat UI (embedded at build time for static export).
# Defaults to true so the production image ships with the chat panel live
# (the server mounts /chat whenever NVIDIA_API_KEY is set); override the
# build arg to build a chat-less image.
ARG NEXT_PUBLIC_CHAT_ENABLED=true
ENV NEXT_PUBLIC_CHAT_ENABLED=${NEXT_PUBLIC_CHAT_ENABLED}
WORKDIR /web
# Install deps first for layer caching (lockfile is committed for reproducibility).
# Use `npm install` (not `npm ci`) so platform-specific optional deps
# (e.g. @unrs/resolver-binding-linux-*) are reconciled — the committed
# lockfile was generated on Windows and lacks Linux-only entries.
COPY web/package.json web/package-lock.json ./
RUN npm install --no-audit --no-fund --include=dev
COPY web/ .
RUN npm run build
# Output: /web/out/ (static HTML/CSS/JS + assets + logo.svg)

# --- Stage 2: build the Python package (Alpine, no semantic search) ----------
# python:3.12-alpine pinned by digest for reproducible builds (amd64).
FROM python:3.12-alpine@sha256:aa679aa4eed6eb56c1dc6ad3f1b98b7d2d788fd961596779d188fdedad97fb38 AS py-builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CFLAGS="-Os -fomit-frame-pointer" \
    CPPFLAGS="-Os -fomit-frame-pointer"

WORKDIR /build

RUN apk add --no-cache \
        build-base \
        postgresql-dev \
        curl

# uv and uvx pinned by digest for supply-chain integrity.
COPY --from=ghcr.io/astral-sh/uv:0.11.25-alpine@sha256:18a2499e97102ccb684f4d19fe4cdd598feb20582dccb65fe086fcadc7c9b81a /usr/local/bin/uv /usr/local/bin/uvx /usr/local/bin/

COPY mcp/pyproject.toml mcp/uv.lock mcp/README.md ./

# Export pinned requirements from the lockfile and install into system site-packages.
# --locked (not --frozen): a stale lock must be a hard error here, not silently
# ignored — --frozen only *skips* the freshness check.
RUN uv export --locked --no-dev --no-emit-project --no-editable --format requirements-txt > requirements.txt \
    && uv pip install --system --no-cache -r requirements.txt

# Install the chat sub-app package (chat/nyaya_chat) and its dependencies
# (fastapi, langgraph, langchain-mcp-adapters, langchain-nvidia-ai-endpoints)
# into site-packages so the host nyaya server can ``import nyaya_chat`` at
# runtime. Non-editable so the code lives in site-packages (the builder's
# /build is discarded).
#
# Dependencies are pinned by exporting chat/uv.lock (committed) rather than
# letting uv resolve fresh against pyproject.toml — and chat's install is
# *constrained by* mcp's exported requirements (-c requirements.txt) so any
# shared-pin disagreement between the two locks fails this build loudly
# instead of silently re-pinning what mcp installed. The [tool.uv]
# constraint-dependencies blocks in both pyprojects keep the locks aligned;
# see the comments there. The langfuse telemetry extra is behind a build ARG
# (default off) so the base image doesn't pull the langfuse SDK stack; opt in
# with --build-arg CHAT_LANGFUSE=true.
ARG CHAT_LANGFUSE=false
COPY chat/pyproject.toml chat/uv.lock chat/README.md ./chat/
COPY chat/nyaya_chat/ ./chat/nyaya_chat/
RUN cd chat \
    && if [ "$CHAT_LANGFUSE" = "true" ]; then \
        uv export --locked --no-dev --extra langfuse --no-emit-project --no-editable --format requirements-txt > /tmp/chat-requirements.txt; \
    else \
        uv export --locked --no-dev --no-emit-project --no-editable --format requirements-txt > /tmp/chat-requirements.txt; \
    fi \
    && uv pip install --system --no-cache -r /tmp/chat-requirements.txt -c ../requirements.txt \
    && uv pip install --system --no-cache --no-deps .

# --- Stage 3: runtime -------------------------------------------------------
# python:3.12-alpine pinned by digest for reproducible builds (amd64).
FROM python:3.12-alpine@sha256:aa679aa4eed6eb56c1dc6ad3f1b98b7d2d788fd961596779d188fdedad97fb38 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

RUN apk add --no-cache libpq ca-certificates \
    && addgroup -S nyaya \
    && adduser -S -G nyaya -u 1000 nyaya

WORKDIR /app

# Python packages and entrypoints from the builder
COPY --from=py-builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=py-builder /usr/local/bin /usr/local/bin

# Application code (server.py mounts StaticFiles at web/out/)
COPY --chown=nyaya:nyaya mcp/nyaya/ /app/nyaya/
COPY --chown=nyaya:nyaya mcp/pyproject.toml mcp/README.md /app/

# Built SPA from stage 1 — served at / by Starlette StaticFiles
COPY --from=web-builder --chown=nyaya:nyaya /web/out/ /app/web/out/

USER nyaya

EXPOSE 8000

STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:${PORT:-8000}/health', timeout=3).status==200 else 1)"

CMD ["python", "-m", "nyaya.server"]
