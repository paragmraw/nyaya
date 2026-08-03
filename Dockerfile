# syntax=docker/dockerfile:1.7
#
# Multi-stage Dockerfile for the Nyaya monorepo: builds the Next.js static
# export in a Node stage, then assembles the Python runtime that serves the
# SPA + REST + MCP from one origin. Build context is the repo root.

# ─── Stage 1: build the SPA (static export -> /web/out) ──────────────────────
FROM node:20-alpine AS web-builder
WORKDIR /web
# Install deps first for layer caching
COPY web/package.json web/package-lock.json* ./
RUN npm ci --no-audit --no-fund
# Build the app (next.config.mjs has output: 'export' -> emits static HTML)
COPY web/ .
RUN npm run build
# Output: /web/out/ (static HTML/CSS/JS + assets + logo.png)

# ─── Stage 2: build the Python package (Alpine, no semantic search) ─────────
FROM python:3.12-alpine AS py-builder

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

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

COPY mcp/pyproject.toml mcp/README.md ./

RUN uv pip install --system --no-cache \
        "."

# ─── Stage 3: runtime ────────────────────────────────────────────────────────
FROM python:3.12-alpine AS runtime

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
COPY --chown=nyaya:nyaya mcp/scripts/ /app/scripts/
COPY --chown=nyaya:nyaya mcp/data/ /app/data/
COPY --chown=nyaya:nyaya mcp/pyproject.toml mcp/README.md /app/

# Built SPA from stage 1 — served at / by Starlette StaticFiles
COPY --from=web-builder --chown=nyaya:nyaya /web/out/ /app/web/out/

USER nyaya

EXPOSE 8000

STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:${PORT:-8000}/health', timeout=3).status==200 else 1)"

CMD ["python", "-m", "nyaya.server"]