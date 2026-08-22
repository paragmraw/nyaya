"""Structured logging + optional Langfuse LLM tracing.

Structured JSON logging via ``structlog`` provides machine-parseable log
lines with ``request_id``, ``turn_id``, ``phase``, ``latency_ms``,
``tool_name``, and ``token_count`` fields.

Langfuse integration (optional, enabled when ``LANGFUSE_PUBLIC_KEY`` +
``LANGFUSE_SECRET_KEY`` + ``LANGFUSE_HOST`` are set) traces every
supervisor/synthesis call with input/output/tokens/latency. The Langfuse
CallbackHandler integrates with LangChain automatically. When Langfuse is
not configured, tracing is a no-op.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

log = logging.getLogger("nyaya_chat.observability")


# ---------------------------------------------------------------------------
# Structured logging configuration
# ---------------------------------------------------------------------------

_structlog_configured = False


def configure_structlog(level: str = "INFO") -> None:
    """Configure structlog for JSON output to stdout.

    Safe to call multiple times; only configures once.
    """
    global _structlog_configured
    if _structlog_configured:
        return

    try:
        import structlog
    except ImportError:
        log.info("structlog not installed; using standard logging")
        logging.basicConfig(level=level, stream=sys.stdout)
        _structlog_configured = True
        return

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level.upper(), logging.INFO)),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )
    _structlog_configured = True


# ---------------------------------------------------------------------------
# Langfuse integration (optional)
# ---------------------------------------------------------------------------

_langfuse_handler: Any = None
_langfuse_enabled = False


def get_langfuse_handler() -> Any | None:
    """Return a Langfuse CallbackHandler if configured, else None.

    The handler is a singleton — created once on first call. When Langfuse
    env vars are not set, returns None (tracing disabled).
    """
    global _langfuse_handler, _langfuse_enabled

    if _langfuse_enabled:
        return _langfuse_handler

    pk = os.environ.get("LANGFUSE_PUBLIC_KEY")
    sk = os.environ.get("LANGFUSE_SECRET_KEY")
    host = os.environ.get("LANGFUSE_HOST")

    if not (pk and sk and host):
        _langfuse_enabled = True
        _langfuse_handler = None
        return None

    try:
        from langfuse.callback import CallbackHandler
        _langfuse_handler = CallbackHandler(
            public_key=pk,
            secret_key=sk,
            host=host,
        )
        _langfuse_enabled = True
        log.info("Langfuse tracing enabled: host=%s", host)
        return _langfuse_handler
    except ImportError:
        log.info("langfuse package not installed; tracing disabled")
        _langfuse_enabled = True
        _langfuse_handler = None
        return None
    except Exception as exc:
        log.warning("Langfuse initialization failed: %s", exc)
        _langfuse_enabled = True
        _langfuse_handler = None
        return None


def get_langfuse_callbacks() -> list[Any]:
    """Return a list of callbacks for LangChain model invocation.

    Returns an empty list if Langfuse is not configured.
    """
    handler = get_langfuse_handler()
    return [handler] if handler else []


def reset_observability() -> None:
    """Reset observability state (for tests)."""
    global _structlog_configured, _langfuse_handler, _langfuse_enabled
    _structlog_configured = False
    _langfuse_handler = None
    _langfuse_enabled = False
