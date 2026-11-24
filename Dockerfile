# syntax=docker/dockerfile:1.7
#
# Multi-stage Dockerfile for the Nyaya v2.0 monorepo (Option R2, Zero-Supabase):
#   1. web-builder : builds the Next.js static export (/web/out)
#   2. py-builder  : installs pinned mcp + chat Python deps on Debian slim and
#                    pre-installs the DuckDB httpfs extension into /app/duckdb_ext
#   3. data-stage  : bakes /app/data/nyaya-core.sqlite from a verified release URL
#                    (.zst + SHA-256) or falls back offline to mini_corpus fixture
#   4. runtime     : hardened non-root python:3.12-slim-bookworm image serving
#                    SPA + REST + /mcp + optional /chat from one origin (:8000)

# --- Stage 1: build the SPA (static export -> /web/out) -----------------------
# node:20-alpine pinned by linux/amd64 manifest digest.
FROM node:20-alpine@sha256:afdf98210b07b586eb71fa22ba2e432e058e4cd1304d31ed60888755b8c865fb AS web-builder
ENV NODE_ENV=production
# Build-time feature flag embedded in the Next.js static export. Defaults to
# true; runtime mounts /chat whenever NVIDIA_API_KEY is configured.
ARG NEXT_PUBLIC_CHAT_ENABLED=true
ENV NEXT_PUBLIC_CHAT_ENABLED=${NEXT_PUBLIC_CHAT_ENABLED}
WORKDIR /web
# Install deps first for layer caching. Use `npm install` (not `npm ci`) so
# Linux-specific optional bindings are resolved cleanly.
COPY web/package.json web/package-lock.json ./
RUN npm install --no-audit --no-fund --include=dev
COPY web/ .
RUN npm run build
# Output: /web/out/

# --- Stage 2: build Python site-packages & DuckDB extensions (Debian slim) ----
# python:3.12-slim-bookworm (3.12.16) pinned by linux/amd64 manifest digest so
# duckdb, lancedb, pypdfium2, pyarrow, and zstandard install via manylinux
# glibc wheels with zero compilers or libpq/postgresql-dev.
FROM python:3.12-slim-bookworm@sha256:0f086103b026048cc50cb5da25ec435c023c5241a83fc44daea593377abc61c4 AS py-builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /build

# uv and uvx pinned by linux/amd64 manifest digest for supply-chain integrity.
COPY --from=ghcr.io/astral-sh/uv:0.11.25-debian-slim@sha256:672c2cac384d695fc9395d14ded6f986dfa0e6bde1f0846bed0aa461a757cffd /usr/local/bin/uv /usr/local/bin/uvx /usr/local/bin/

COPY mcp/pyproject.toml mcp/uv.lock mcp/README.md ./

# Export pinned runtime requirements (including [http] for uvicorn + redis)
# from mcp/uv.lock. --locked fails loudly if the lockfile is stale.
RUN uv export --locked --no-dev --extra http --no-emit-project --no-editable --format requirements-txt > requirements.txt \
    && uv pip install --system --no-cache -r requirements.txt

# Install the chat sub-app (chat/nyaya_chat) constrained by mcp's exported
# pins (-c ../requirements.txt) so any shared-dependency drift fails loudly.
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

# Pre-install DuckDB httpfs extension into /app/duckdb_ext so the runtime
# container works under `read_only: true` with autoinstall disabled.
RUN mkdir -p /app/duckdb_ext \
    && python -c "import duckdb; con = duckdb.connect(); con.execute(\"SET extension_directory='/app/duckdb_ext'; INSTALL httpfs;\"); con.close()"

# Install nyaya-mcp package & console scripts (`nyaya-mcp`, `nyaya`, `nyaya-ingest`).
# Installed after dependency & httpfs layers to preserve Docker layer caching.
COPY mcp/nyaya/ ./nyaya/
RUN uv pip install --system --no-cache --no-deps .

# --- Stage 2b: dual-mode SQLite core baker (prod artifact OR CI mini_corpus) --
FROM py-builder AS data-stage

ARG NYAYA_CORE_SQLITE_URL=""
ARG NYAYA_CORE_SQLITE_SHA256=""

COPY mcp/tests/ ./tests/

RUN mkdir -p /app/data \
    && if [ -n "$NYAYA_CORE_SQLITE_URL" ]; then \
        if [ -z "$NYAYA_CORE_SQLITE_SHA256" ]; then \
            echo "ERROR: NYAYA_CORE_SQLITE_SHA256 must be set when NYAYA_CORE_SQLITE_URL is non-empty" >&2; \
            exit 1; \
        fi; \
        rm -rf /app/data/* \
        && apt-get update \
        && apt-get install -y --no-install-recommends curl ca-certificates zstd \
        && rm -rf /var/lib/apt/lists/* \
        && curl -fsSL "$NYAYA_CORE_SQLITE_URL" -o /tmp/nyaya-core.sqlite.zst \
        && echo "${NYAYA_CORE_SQLITE_SHA256}  /tmp/nyaya-core.sqlite.zst" | sha256sum -c - \
        && zstd -d --rm /tmp/nyaya-core.sqlite.zst -o /app/data/nyaya-core.sqlite \
        && find /app/data -mindepth 1 ! -name 'nyaya-core.sqlite' -exec rm -rf {} + \
        && test -f /app/data/nyaya-core.sqlite; \
    else \
        PYTHONPATH=/build python -m tests.fixtures.mini_corpus.builder /app/data; \
    fi

# --- Stage 3: hardened non-root runtime -------------------------------------
FROM python:3.12-slim-bookworm@sha256:0f086103b026048cc50cb5da25ec435c023c5241a83fc44daea593377abc61c4 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    NYAYA_DATA_DIR=/app/data \
    NYAYA_CACHE_DIR=/tmp/nyaya-cache \
    NYAYA_DUCKDB_EXT_DIR=/app/duckdb_ext \
    PORT=8000

# Apply latest Debian security patches (Trivy gate fails on fixable HIGH/CRITICAL
# OS CVEs) and create non-root user `nyaya` (UID/GID 1000) + writable cache
# mountpoints (owned by nyaya so Docker named volumes inherit UID 1000 ownership).
RUN apt-get update \
    && apt-get upgrade -y \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && groupadd --gid 1000 nyaya \
    && useradd --uid 1000 --gid 1000 --create-home --shell /usr/sbin/nologin nyaya \
    && mkdir -p /app /var/cache/nyaya /tmp/nyaya-cache \
    && chown -R nyaya:nyaya /app /var/cache/nyaya /tmp/nyaya-cache

WORKDIR /app

# Python packages and console scripts from py-builder; strip build-only uv/uvx.
COPY --from=py-builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=py-builder /usr/local/bin /usr/local/bin
RUN rm -f /usr/local/bin/uv /usr/local/bin/uvx

# Pre-installed DuckDB httpfs extension and baked SQLite core corpus.
COPY --from=py-builder --chown=nyaya:nyaya /app/duckdb_ext /app/duckdb_ext
COPY --from=data-stage --chown=nyaya:nyaya /app/data /app/data

# Apache-2.0 NOTICE, README, and built SPA (`nyaya` and `nyaya_chat` live in site-packages).
COPY --chown=nyaya:nyaya mcp/README.md NOTICE /app/
COPY --from=web-builder --chown=nyaya:nyaya /web/out/ /app/web/out/

USER nyaya

EXPOSE 8000

STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import os, urllib.request, sys; port = os.environ.get('PORT', '8000'); sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=3).status == 200 else 1)"

CMD ["python", "-m", "nyaya.cli", "--transport", "http", "--host", "0.0.0.0"]
