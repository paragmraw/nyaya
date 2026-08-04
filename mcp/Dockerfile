# syntax=docker/dockerfile:1.7

# --- Build stage ------------------------------------------------------------
# Alpine-based build. Compiles psycopg (no musllinux wheel — needs libpq + gcc).
# The semantic extra (fastembed + onnxruntime) is NOT installed: onnxruntime
# has no musllinux wheels (glibc-only). semantic_query is unavailable in
# this image.
# python:3.12-alpine pinned by digest for reproducible builds (amd64).
FROM python:3.12-alpine@sha256:aa679aa4eed6eb56c1dc6ad3f1b98b7d2d788fd961596779d188fdedad97fb38 AS builder

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

# uv pinned by digest for supply-chain integrity.
COPY --from=ghcr.io/astral-sh/uv:0.11.25-alpine@sha256:18a2499e97102ccb684f4d19fe4cdd598feb20582dccb65fe086fcadc7c9b81a /uv /usr/local/bin/uv

COPY mcp/pyproject.toml mcp/uv.lock mcp/README.md ./

# Export pinned requirements from the lockfile and install into system site-packages.
RUN uv export --frozen --no-dev --no-emit-project --no-editable --format requirements-txt > requirements.txt \
    && uv pip install --system --no-cache -r requirements.txt

# --- Runtime stage ----------------------------------------------------------
FROM python:3.12-alpine@sha256:aa679aa4eed6eb56c1dc6ad3f1b98b7d2d788fd961596779d188fdedad97fb38 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=8000

RUN apk add --no-cache libpq ca-certificates \
    && addgroup -S nyaya \
    && adduser -S -G nyaya -u 1000 nyaya

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY --chown=nyaya:nyaya mcp/nyaya/ /app/nyaya/
COPY --chown=nyaya:nyaya mcp/scripts/ /app/scripts/
COPY --chown=nyaya:nyaya mcp/data/ /app/data/
COPY --chown=nyaya:nyaya mcp/pyproject.toml mcp/README.md /app/

USER nyaya

EXPOSE 8000

STOPSIGNAL SIGTERM

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request, sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:${PORT:-8000}/health', timeout=3).status==200 else 1)"

CMD ["python", "-m", "nyaya.server"]