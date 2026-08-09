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

_model_instance = None
_model_initialised = False

# System prompt: grounds every answer in retrieved provisions and forces a
# stable citation format the frontend can render. The model is told the tool
# surface is its only source of truth for the law.
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


def get_model(settings: Settings | None = None):
    """Return a cached ``ChatNVIDIA`` instance.

    The ``settings`` arg is read on the first call only (and used to configure
    the model). Subsequent calls ignore it and return the cached instance.
    Tests should call ``reset_model_cache()`` to start fresh.
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
