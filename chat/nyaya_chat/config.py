"""Runtime configuration for nyaya-chat.

Environment variables read:

``NVIDIA_API_KEY`` (required)
    NVIDIA API Catalog key (``nvapi-...``). Used to call Nemotron models on
    ``integrate.api.nvidia.com``.

``MCP_URL`` (optional)
    Override the MCP server URL. Defaults to a PORT-derived localhost URL.

``LANGFUSE_PUBLIC_KEY``, ``LANGFUSE_SECRET_KEY``, ``LANGFUSE_HOST`` (all optional)
    When all three are set, LLM calls are traced to Langfuse for observability.

Everything else is a Python constant in this module. Edit this file to tune
the LLM models, temperatures, token caps, message limits, tool allowlist,
or the chat log level.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from pydantic import SecretStr

# ---------------------------------------------------------------------------
# Tunable constants (edit here; no env vars).
# ---------------------------------------------------------------------------

# URL of the nyaya MCP server the agent calls for corpus retrieval. When
# mounted in the same process as the MCP server (the default deploy), this
# points at the same origin; for local dev it's http://localhost:8000/mcp.
# At runtime, get_settings() derives the port from the PORT env var (set by
# Railway to e.g. 8080) so the self-call matches the actual listening port.
# An explicit MCP_URL env var overrides the PORT-derived default (useful for
# split-process local dev where the MCP server runs separately).
MCP_URL = "http://localhost:8000/mcp"

_LIGHTNING = "nvidia/nemotron-3.5-lightning-30b-a3b"

# LLM model ids.
LLM_MODEL = _LIGHTNING            # fallback for degraded mode (no tools)
SYNTHESIS_MODEL = _LIGHTNING      # composes the final grounded answer

# LLM tuning.
LLM_TEMPERATURE = 0.1
LLM_MAX_TOKENS = 2048             # cap per turn in degraded mode
LLM_TIMEOUT_S = 60.0              # per-invoke timeout for the NVIDIA API
LLM_MAX_RETRIES = 4               # retries on 429/5xx with exponential backoff

# The agent's single completion cap (thinking is off, so the budget belongs
# entirely to the answer).
SYNTHESIS_MAX_TOKENS = 6144

# Thinking mode for the agent model. Off by default: reasoning tokens share
# the completion budget with the answer and delay the first answer word by
# thousands of tokens (observed live: ~3k+ thinking tokens before the first
# answer word). Flip to True only if citation quality regresses in eval.
SYNTHESIS_THINKING = False

# Reflection loop: when the agent's answer is ungrounded (no citations and
# tools were called) and the turn budget allows, the agent may run one more
# retrieval round. This cap counts reflection rounds; total answer legs per
# turn = value + 1.
MAX_REFLECTION_ROUNDS = 1

# A reflection round is only started when at least this many seconds remain
# before the turn deadline — a reflection that could not finish inside the
# budget is worse than an uncited answer.
REFLECTION_DEADLINE_S = 12.0

# Citation verification: after synthesis, the agent programatically parses
# [[act: X, ref: Y]] markers and checks each against the tool results. Ungrounded
# citations are stripped. If zero remain and tools were called, a caveat is
# appended.
CITATION_VERIFICATION = True

# SSE keepalive: emit a ping event every N seconds to prevent proxy timeouts.
SSE_KEEPALIVE_INTERVAL_S = 15.0

# Turn wall-clock budget: nodes check the deadline between phases and fail the
# turn with a ``timeout`` error event instead of grinding through retry loops
# (each LLM call is up to LLM_TIMEOUT_S x LLM_MAX_RETRIES). Sized for the
# TTFT <4s / total <20s targets with headroom for one gated reflection.
TURN_BUDGET_S = 60.0

# Guardrail: intent classification before the agent pipeline.
# Tier 1 is regex-based (instant); Tier 2 is an LLM call (only if Tier 1
# is uncertain). Set to False to bypass the guardrail entirely (all messages
# go through the normal agent -> tools -> agent pipeline).
GUARDRAIL_ENABLED = True

# Tier 2 classifier: uses with_structured_output(Intent enum) for reliable
# classification. These settings control the dedicated classifier model.
GUARDRAIL_CLASSIFIER_MAX_TOKENS = 32
GUARDRAIL_CLASSIFIER_TIMEOUT_S = 10.0

# Message constraints.
MAX_HISTORY = 8                   # max prior (role, content) turns the client may send

# Tool-result truncation: cleaned tool content that is fed back to the
# model (dedup cache, synthesis prompt) or rendered in the UI summary is
# capped at this many characters. Single source of truth for the cap
# (tool_content.py); the per-message input cap lives in schemas.py
# (``ChatRequest.message``'s ``Field(max_length=4000)``).
MAX_TOOL_CHARS = 8000

# Chat logger level.
LOG_LEVEL = "INFO"


# ---------------------------------------------------------------------------
# Module-level attribute fallback (PEP 562)
# ---------------------------------------------------------------------------

def __getattr__(name: str):  # noqa: ANN202 - PEP 562 signature
    """Resolve ``DEFAULT_TOOLS`` from the tool spec at access time.

    The curated default tool set (the 6 most useful of the MCP server's 16
    for grounded Q&A — resolve_citation was folded into
    get_section/get_article, corpus_stats dropped, hybrid_search removed in
    the v0.2 consolidation) is defined ONCE in ``tools_layer/spec.py``; it
    cannot be imported at module level here because the ``tools_layer``
    package imports this module. Imported lazily, both directions resolve.
    """
    if name == "DEFAULT_TOOLS":
        from .tools_layer.spec import DEFAULT_TOOLS as _tools
        return _tools
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def _redact(secret: str | None) -> str | None:
    if not secret:
        return None
    if len(secret) <= 8:
        return "***"
    return f"{secret[:5]}…{secret[-3:]}"


@dataclass(frozen=True)
class Settings:
    nvidia_api_key: SecretStr
    mcp_url: str = MCP_URL
    llm_model: str = LLM_MODEL
    synthesis_model: str = SYNTHESIS_MODEL
    llm_temperature: float = LLM_TEMPERATURE
    llm_max_tokens: int = LLM_MAX_TOKENS
    llm_timeout_s: float = LLM_TIMEOUT_S
    llm_max_retries: int = LLM_MAX_RETRIES
    max_history: int = MAX_HISTORY
    log_level: str = LOG_LEVEL
    synthesis_max_tokens: int = SYNTHESIS_MAX_TOKENS
    synthesis_thinking: bool = SYNTHESIS_THINKING
    max_reflection_rounds: int = MAX_REFLECTION_ROUNDS
    reflection_deadline_s: float = REFLECTION_DEADLINE_S
    citation_verification: bool = CITATION_VERIFICATION
    sse_keepalive_interval_s: float = SSE_KEEPALIVE_INTERVAL_S
    turn_budget_s: float = TURN_BUDGET_S
    guardrail_enabled: bool = GUARDRAIL_ENABLED
    guardrail_classifier_max_tokens: int = GUARDRAIL_CLASSIFIER_MAX_TOKENS
    guardrail_classifier_timeout_s: float = GUARDRAIL_CLASSIFIER_TIMEOUT_S

    @property
    def tool_allowlist(self) -> tuple[str, ...]:
        """The curated default tool set exposed to the agent (spec.py is the
        single source of truth)."""
        from .tools_layer.spec import DEFAULT_TOOLS
        return DEFAULT_TOOLS

    @property
    def tools(self) -> str:
        """Comma-join of the allowlist (kept for log-dict compatibility)."""
        return ",".join(self.tool_allowlist)

    def as_log_dict(self) -> dict[str, object]:
        return {
            "mcp_url": self.mcp_url,
            "llm_model": self.llm_model,
            "synthesis_model": self.synthesis_model,
            "llm_temperature": self.llm_temperature,
            "llm_max_tokens": self.llm_max_tokens,
            "llm_timeout_s": self.llm_timeout_s,
            "llm_max_retries": self.llm_max_retries,
            "max_history": self.max_history,
            "nvidia_api_key": _redact(self.nvidia_api_key.get_secret_value()),
            "tools": list(self.tool_allowlist),
            "synthesis_max_tokens": self.synthesis_max_tokens,
            "synthesis_thinking": self.synthesis_thinking,
            "max_reflection_rounds": self.max_reflection_rounds,
            "reflection_deadline_s": self.reflection_deadline_s,
            "citation_verification": self.citation_verification,
            "sse_keepalive_interval_s": self.sse_keepalive_interval_s,
            "turn_budget_s": self.turn_budget_s,
            "guardrail_enabled": self.guardrail_enabled,
            "log_level": self.log_level,
        }


def _required_env(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(
            f"Required environment variable {name!r} is not set. "
            "Copy .env.example to .env and fill in the values."
        )
    return val


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide Settings instance.

    Raises RuntimeError if ``NVIDIA_API_KEY`` is missing.
    Tests should call ``reset_settings_cache()`` after monkeypatching env.
    """
    port = os.environ.get("PORT", "8000")
    default_mcp_url = f"http://localhost:{port}/mcp"
    mcp_url = os.environ.get("MCP_URL", default_mcp_url)
    return Settings(
        nvidia_api_key=SecretStr(_required_env("NVIDIA_API_KEY")),
        mcp_url=mcp_url,
    )


def reset_settings_cache() -> None:
    """Clear the settings cache. Intended for tests."""
    get_settings.cache_clear()
