"""Runtime configuration for nyaya-chat.

Only one environment variable is read:

``NVIDIA_API_KEY`` (required)
    NVIDIA API Catalog key (``nvapi-...``). Used to call Nemotron models on
    ``integrate.api.nvidia.com``.

Everything else is a Python constant in this module. Edit this file to tune
the MCP server URL, LLM models, temperatures, token caps, message limits,
tool allowlist, or the chat log level.
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
SUPERVISOR_MODEL = _LIGHTNING     # plans tool calls, short output
SYNTHESIS_MODEL = _LIGHTNING      # composes the final grounded answer

# LLM tuning.
LLM_TEMPERATURE = 0.1
LLM_MAX_TOKENS = 2048             # cap per turn in degraded mode
LLM_TIMEOUT_S = 60.0              # per-invoke timeout for the NVIDIA API
LLM_MAX_RETRIES = 4               # retries on 429/5xx with exponential backoff

# Per-phase token caps.
SUPERVISOR_MAX_TOKENS = 512       # supervisor plans and delegates; short output
SYNTHESIS_MAX_TOKENS = 4096       # synthesis composes the final answer

# Message constraints.
MAX_MESSAGE_CHARS = 4000          # max length of a single user message
MAX_HISTORY = 8                   # max prior (role, content) turns the client may send

# Chat logger level.
LOG_LEVEL = "INFO"

# Curated default tool set. The nyaya MCP server exposes 16 tools; we expose
# the 7 most useful for grounded Q&A. resolve_citation is folded into
# get_section/get_article (they accept citation strings). corpus_stats is
# dropped (list_acts gives the same discovery signal).
DEFAULT_TOOLS: tuple[str, ...] = (
    "semantic_query",
    "get_section",
    "get_article",
    "get_judgment",
    "cross_reference",
    "list_acts",
    "hybrid_search",
)


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
    supervisor_model: str = SUPERVISOR_MODEL
    synthesis_model: str = SYNTHESIS_MODEL
    llm_temperature: float = LLM_TEMPERATURE
    llm_max_tokens: int = LLM_MAX_TOKENS
    llm_timeout_s: float = LLM_TIMEOUT_S
    llm_max_retries: int = LLM_MAX_RETRIES
    max_message_chars: int = MAX_MESSAGE_CHARS
    max_history: int = MAX_HISTORY
    log_level: str = LOG_LEVEL
    supervisor_max_tokens: int = SUPERVISOR_MAX_TOKENS
    synthesis_max_tokens: int = SYNTHESIS_MAX_TOKENS

    @property
    def tool_allowlist(self) -> tuple[str, ...]:
        """The curated default tool set exposed to the agent."""
        return DEFAULT_TOOLS

    @property
    def tools(self) -> str:
        """Comma-join of the allowlist (kept for log-dict compatibility)."""
        return ",".join(self.tool_allowlist)

    def as_log_dict(self) -> dict[str, object]:
        return {
            "mcp_url": self.mcp_url,
            "llm_model": self.llm_model,
            "supervisor_model": self.supervisor_model,
            "synthesis_model": self.synthesis_model,
            "llm_temperature": self.llm_temperature,
            "llm_max_tokens": self.llm_max_tokens,
            "llm_timeout_s": self.llm_timeout_s,
            "llm_max_retries": self.llm_max_retries,
            "max_message_chars": self.max_message_chars,
            "max_history": self.max_history,
            "nvidia_api_key": _redact(self.nvidia_api_key.get_secret_value()),
            "tools": list(self.tool_allowlist),
            "supervisor_max_tokens": self.supervisor_max_tokens,
            "synthesis_max_tokens": self.synthesis_max_tokens,
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
