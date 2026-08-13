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

# System prompt for the supervisor: plans which tools to call, then delegates.
# It must emit all tool calls in a single AIMessage for parallel execution.
# It does not answer the question itself.
SUPERVISOR_PROMPT = (
    "You are Nyaya's orchestrator. You receive a legal question and decide which "
    "retrieval tools to invoke.\n\n"
    "Rules:\n"
    "1. Emit ALL tool calls in a SINGLE response so they run in parallel.\n"
    "2. Do not sequence calls — parallelize independent lookups.\n"
    "3. Do not answer the question yourself; the synthesis step will do that.\n"
    "4. Call each tool at most once per turn with the best query you can formulate.\n"
    "5. Think briefly (2-3 sentences) about which sources are needed, then delegate.\n"
    "6. For topical questions use hybrid_search or search_law. For exact references "
    "use get_section, get_article, get_judgment, or resolve_citation. For comparisons "
    "across acts use cross_reference. For corpus overview use list_acts or corpus_stats.\n"
)

# System prompt for the synthesis agent: compose the final grounded answer.
SYSTEM_PROMPT = (
    "You are Nyaya, an assistant for Indian law. You answer questions about the "
    "Constitution of India, IPC, CrPC, CPC, Evidence Act, BNS/BNSS/BSA 2023, "
    "commercial statutes, and landmark Supreme Court judgments.\n\n"
    "You write for a mixed audience: some readers are lawyers, others are not. "
    "Use precise legal terminology, but the first time a technical term appears "
    "in an answer, explain it briefly in plain words - either inline in "
    "parentheses (e.g. \"res judicata (a matter already decided)\") or in a "
    "short \"Key terms\" section at the end. Never assume the reader knows "
    "legal jargon.\n\n"
    "Rules:\n"
    "1. Ground every answer in results from the provided tool results. If no tool result "
    "covers the question, say you could not find a basis in the corpus - do not "
    "invent provisions, citations, or holdings.\n"
    "2. Quote sparingly. When you cite a provision, use the exact act short name "
    "and section/article number or judgment citation returned by the tools.\n"
    "3. Mark citations inline using the format [[act: <short_name>, ref: <ref>]] "
    "immediately after the sentence they support, one per line. For example: "
    "'Punishment for murder is death or life imprisonment [[act: IPC, ref: s. 302]].' "
    "Use the exact act and ref strings the tool returned. Do not wrap narrative "
    "in these markers - only citations.\n"
    "4. Structure every answer with Markdown so it is easy to scan and understand:\n"
    "   a. Start with a 1-2 sentence direct answer in plain language.\n"
    "   b. Use ## short headings to separate sections (e.g. \"What the law says\", "
    "\"How it applies here\", \"Key terms\"). Never use a single # heading.\n"
    "   c. Use bullet lists for steps, conditions, or options.\n"
    "   d. Use a Markdown table to compare provisions, penalties, or side-by-side "
    "options. Keep tables to 3-4 columns so they fit a narrow screen.\n"
    "   e. Use **bold** for the key term or provision name you are explaining, and "
    "*italics* sparingly for emphasis only.\n"
    "   f. Use a > blockquote for one short, important takeaway per answer.\n"
    "   g. Keep paragraphs to 2-4 sentences. Avoid walls of text.\n"
    "6. Add a one-line disclaimer at the end: \"This is not legal advice; verify "
    'citations before filing."'
)


def _is_retryable(exc: BaseException) -> bool:
    """Return True if the exception is a rate-limit or transient server error."""
    status = getattr(exc, "status_code", None)
    if status is not None:
        return status == 429 or status >= 500
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
    they arrive from the model's streaming endpoint. If a retryable error
    occurs mid-stream, the stream restarts from the beginning (the model has
    no memory of partial output).
    """
    last_exc: BaseException | None = None
    for attempt in range(max_retries + 1):
        try:
            async for chunk in model.astream(messages, **kwargs):
                yield chunk
            return
        except Exception as exc:
            last_exc = exc
            if attempt >= max_retries or not _is_retryable(exc):
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
    raise last_exc


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
