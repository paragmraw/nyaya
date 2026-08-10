"""NVIDIA Nemotron chat model singleton.

The model is configured from ``Settings`` and lazily instantiated on first
use so importing this module is cheap (and so tests can monkeypatch
``get_model`` before any call). We use ``ChatNVIDIA`` from
``langchain-nvidia-ai-endpoints`` which reads ``NVIDIA_API_KEY`` from the
environment and supports native streaming + tool calling.
"""

from __future__ import annotations

import logging

from .config import Settings, get_settings

log = logging.getLogger("nyaya_chat.llm")

_base_model_instance = None
_base_model_initialised = False
_model_instance = None
_model_initialised = False

# System prompt: grounds every answer in retrieved provisions and forces a
# stable citation format the frontend can render. The model is told the tool
# surface is its only source of truth for the law.
#
# NOTE: The [[act: …, ref: …]] inline citation format is now a **fallback**.
# The primary citation path is the structured-output synthesis node (see
# ``agent.py``) which uses ``with_structured_output(CitedAnswer)`` to guarantee
# citation shape. This prompt convention survives for graceful degradation
# when structured output is unavailable or fails.
SYSTEM_PROMPT = (
    "You are Nyaya, an assistant for Indian law. You answer questions about the "
    "Constitution of India, IPC, CrPC, CPC, Evidence Act, BNS/BNSS/BSA 2023, "
    "commercial statutes, and landmark Supreme Court judgments.\n\n"
    "Rules:\n"
    "1. Ground every answer in results from the provided tools. If no tool result "
    "covers the question, say you could not find a basis in the corpus - do not "
    "invent provisions, citations, or holdings.\n"
    "2. Quote sparingly. When you cite a provision, use the exact act short name "
    "and section/article number or judgment citation returned by the tools.\n"
    "3. Mark citations inline using the format [[act: <short_name>, ref: <ref>]] "
    "immediately after the sentence they support, one per line. For example: "
    "'Punishment for murder is death or life imprisonment [[act: IPC, ref: s. 302]].' "
    "Use the exact act and ref strings the tool returned. Do not wrap narrative "
    "in these markers - only citations.\n"
    "4. Prefer hybrid_search for topical questions, get_section / get_article "
    "for exact references, and get_judgment for cases. Call tools before answering; "
    "never answer from memory.\n"
    "5. Keep answers concise and practical for a lawyer. Add a one-line disclaimer "
    'at the end: "This is not legal advice; verify citations before filing."'
)

# Synthesis prompt: used by the structured-output synthesis node that runs
# after the ReAct retrieval loop. It transforms the draft answer + retrieved
# tool results into a schema-guaranteed CitedAnswer object.
SYNTHESIS_PROMPT = (
    "You are a legal citation extractor. Given a draft answer and the retrieved "
    "provisions from tool calls, produce the final answer with precise citations.\n\n"
    "Rules:\n"
    "1. Only cite provisions that appear in the retrieved tool results. Do not "
    "invent citations or reference provisions not returned by the tools.\n"
    "2. Each citation must use the exact act short name and section/article "
    "number as returned by the tools.\n"
    "3. The answer field should be clean final text without [[act:...]] markers.\n"
    "4. The reasoning field should briefly explain why these provisions apply.\n"
    "5. If the draft answer has no citations, return an empty citations list.\n"
    "6. Include the disclaimer: 'This is not legal advice; verify citations "
    "before filing.'"
)


def get_base_model(settings: Settings | None = None):
    """Return a cached base ``ChatNVIDIA`` instance (no thinking mode applied).

    The base model is used for the structured-output synthesis step
    (``with_structured_output``) where thinking mode is not needed. The
    ``settings`` arg is read on the first call only. Tests should call
    ``reset_model_cache()`` to start fresh.
    """
    global _base_model_instance, _base_model_initialised
    if _base_model_initialised:
        return _base_model_instance
    from langchain_nvidia_ai_endpoints import ChatNVIDIA

    s = settings or get_settings()
    log.info(
        "initialising base ChatNVIDIA model=%s temp=%s max_tokens=%s",
        s.llm_model, s.llm_temperature, s.llm_max_tokens,
    )
    _base_model_instance = ChatNVIDIA(
        model=s.llm_model,
        temperature=s.llm_temperature,
        max_completion_tokens=s.llm_max_tokens,
        timeout=s.llm_timeout_s,
        api_key=s.nvidia_api_key.get_secret_value(),
    )
    _base_model_initialised = True
    return _base_model_instance


def get_model(settings: Settings | None = None):
    """Return a cached model with thinking mode applied (for ReAct agent).

    If ``Settings.llm_enable_thinking`` is ``True``, the base model is
    wrapped with ``.with_thinking_mode(enabled=True)`` so the model emits
    ``reasoning_content`` via ``additional_kwargs`` during streaming. The
    ``settings`` arg is read on the first call only. Tests should call
    ``reset_model_cache()`` to start fresh.
    """
    global _model_instance, _model_initialised
    if _model_initialised:
        return _model_instance

    s = settings or get_settings()
    base = get_base_model(s)

    if s.llm_enable_thinking:
        # Nemotron uses param-based thinking (chat_template_kwargs.enable_thinking).
        # Reasoning content surfaces in additional_kwargs["reasoning_content"].
        _model_instance = base.with_thinking_mode(enabled=True)
        log.info("thinking mode enabled for model=%s", s.llm_model)
    else:
        _model_instance = base
        log.info("thinking mode disabled for model=%s", s.llm_model)

    _model_initialised = True
    return _model_instance


def reset_model_cache() -> None:
    """Clear both the base and thinking model caches. Intended for tests."""
    global _base_model_instance, _base_model_initialised, _model_instance, _model_initialised
    _base_model_instance = None
    _base_model_initialised = False
    _model_instance = None
    _model_initialised = False
