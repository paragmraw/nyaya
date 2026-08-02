# syntax=docker/dockerfile:1.7

# --- Build stage ------------------------------------------------------------
# Alpine-based build. Compiles psycopg (no musllinux wheel — needs libpq + gcc).
# The semantic extra (fastembed + onnxruntime) is NOT installed: onnxruntime has
# no musllinux wheels (glibc-only). semantic_query is unavailable in this image.
FROM python:3.12-alpine AS builder

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

# --- Runtime stage ----------------------------------------------------------
FROM python:3.12-alpine AS runtime

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