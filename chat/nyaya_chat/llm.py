"""NVIDIA Nemotron chat model singleton + retry wrapper.

The model is configured from ``Settings`` and lazily instantiated on first
use so importing this module is cheap (and so tests can monkeypatch
``get_model`` before any call). We use ``ChatNVIDIA`` from
``langchain-nvidia-ai-endpoints`` which reads ``NVIDIA_API_KEY`` from the
environment and supports native streaming + tool calling.

``ainvoke_with_retry`` wraps model invocations with exponential backoff for
rate-limit (429) and transient (5xx) errors.
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import AsyncIterator
from typing import Any

from .config import Settings, get_settings

log = logging.getLogger("nyaya_chat.llm")

_model_instance: Any = None
_model_initialised = False

# Prompt constants moved to ``prompts.py`` (single source of truth: the
# supervisor prompt's tool list is rendered from ``tools_layer.spec`` there).
# Re-exported here for backward compatibility with existing importers.
from .prompts import DISCLAIMER, SUPERVISOR_PROMPT, SYSTEM_PROMPT  # noqa: E402,F401


def _is_retryable(exc: BaseException) -> bool:
    """Classify an exception as retryable (rate limit / transient) or not.

    Classification, most to least specific:

    1. A numeric ``status_code`` attribute (LangChain / openai-style API
       errors): retry on 429 (rate limit) and 5xx (transient server).
    2. A ``response.status_code`` pair (httpx / requests-style
       ``HTTPStatusError``): same rule.
    3. Exception type: timeouts and transport failures are transient by
       definition (``TimeoutError`` — which ``asyncio.TimeoutError`` aliases
       on Python >= 3.11 — and httpx ``TransportError`` subclasses such as
       ``ConnectError``).
    4. Last resort, for exception types that carry no structured status:
       substring match on the message — some hosted endpoints stringify
       429s into the error text.
    """
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status == 429 or status >= 500
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        return status == 429 or status >= 500
    if isinstance(exc, TimeoutError):
        return True
    try:
        import httpx
    except ImportError:  # pragma: no cover - httpx ships with the LLM stack
        httpx = None  # type: ignore[assignment]
    if httpx is not None and isinstance(exc, httpx.TransportError):
        return True
    msg = str(exc).lower()
    return "rate limit" in msg or "429" in msg or "timeout" in msg or "overloaded" in msg


async def ainvoke_with_retry(
    model: Any,
    messages: Any,
    *,
    max_retries: int = 4,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    **kwargs: Any,
) -> Any:
    """Invoke ``model.ainvoke`` with exponential backoff on retryable errors.

    Retries on HTTP 429 (rate limit) and 5xx (transient server) errors.
    Uses full jitter: delay = random.uniform(0, min(max_delay, base_delay * 2**attempt)).
    """
    last_exc: BaseException | None = None
    for attempt in range(max_retries + 1):
        try:
            return await model.ainvoke(messages, **kwargs)
        except Exception as exc:
            last_exc = exc
            if attempt >= max_retries or not _is_retryable(exc):
                raise
            delay = min(max_delay, base_delay * (2**attempt))
            delay = random.uniform(0, delay)
            log.warning(
                "retryable error (attempt %d/%d), backing off %.1fs: %s",
                attempt + 1, max_retries, delay, exc,
            )
            await asyncio.sleep(delay)
    assert last_exc is not None  # pragma: no cover
    raise last_exc  # pragma: no cover


async def astream_with_retry(
    model: Any,
    messages: Any,
    *,
    max_retries: int = 4,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    **kwargs: Any,
) -> AsyncIterator[Any]:
    """Stream model tokens with exponential backoff on retryable errors.

    Like :func:`ainvoke_with_retry` but yields ``AIMessageChunk`` objects as
    they arrive from the model's streaming endpoint. Retries only if NO chunk
    has been yielded yet: once a chunk has reached the caller it cannot be
    retracted, so restarting the stream would duplicate output. A retryable
    error mid-stream therefore propagates to the caller (surfaced as a stream
    error) instead of silently doubling the answer.
    """
    last_exc: BaseException | None = None
    yielded = False
    for attempt in range(max_retries + 1):
        try:
            async for chunk in model.astream(messages, **kwargs):
                yielded = True
                yield chunk
            return
        except Exception as exc:
            last_exc = exc
            if attempt >= max_retries or not _is_retryable(exc) or yielded:
                raise
            delay = min(max_delay, base_delay * (2**attempt))
            delay = random.uniform(0, delay)
            log.warning(
                "retryable stream error (attempt %d/%d), backing off %.1fs: %s",
                attempt + 1, max_retries, delay, exc,
            )
            await asyncio.sleep(delay)
    assert last_exc is not None  # pragma: no cover
    raise last_exc  # pragma: no cover


def get_model(settings: Settings | None = None):
    """Return a cached ``ChatNVIDIA`` instance.

    The ``settings`` arg is read on the first call only. Tests should call
    ``reset_model_cache()`` to start fresh.
    """
    global _model_instance, _model_initialised
    if _model_initialised:
        return _model_instance

    from langchain_nvidia_ai_endpoints import ChatNVIDIA

    s = settings or get_settings()
    log.info(
        "initialising ChatNVIDIA model=%s temp=%s max_tokens=%s",
        s.llm_model, s.llm_temperature, s.llm_max_tokens,
    )
    _model_instance = ChatNVIDIA(
        model=s.llm_model,
        temperature=s.llm_temperature,
        max_completion_tokens=s.llm_max_tokens,
        timeout=s.llm_timeout_s,
        api_key=s.nvidia_api_key.get_secret_value(),
    )
    _model_initialised = True
    return _model_instance


def reset_model_cache() -> None:
    """Clear the model cache. Intended for tests."""
    global _model_instance, _model_initialised
    _model_instance = None
    _model_initialised = False
