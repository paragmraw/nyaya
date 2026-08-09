"""Runtime configuration for nyaya-chat, loaded from environment variables.

Environment variables
---------------------
``NVIDIA_API_KEY`` (required)
    NVIDIA API Catalog key (``nvapi-...``). Used to call Nemotron models on
    ``integrate.api.nvidia.com``.
``NYAYA_MCP_URL`` (optional, default ``http://localhost:8000/mcp``)
    URL of the nyaya MCP server the agent calls for corpus retrieval. When
    mounted in the same process as the MCP server (the default deploy), this
    points at the same origin; for local dev it's ``http://localhost:8000/mcp``.
``CHAT_LLM_MODEL`` (optional)
    NVIDIA model id. Default: ``nvidia/nemotron-3-ultra-550b-a55b``.
``CHAT_LLM_TEMPERATURE`` (optional, default 0.1)
    Sampling temperature for the chat model.
``CHAT_LLM_MAX_TOKENS`` (optional, default 2048)
    Cap on generated tokens per turn.
``CHAT_LLM_TIMEOUT_S`` (optional, default 60)
    Per-invoke timeout for the NVIDIA API.
``CHAT_MAX_MESSAGE_CHARS`` (optional, default 4000)
    Maximum length of a single user message.
``CHAT_MAX_HISTORY`` (optional, default 8)
    Maximum number of prior (role, content) turns the client may send for
    context. Older turns are dropped server-side.
``CHAT_TOOLS`` (optional, comma-separated)
    Allowlist of MCP tool names to expose to the agent. Empty/unset exposes
    the curated default set (see ``DEFAULT_TOOLS``).
``NYAYA_CHAT_LOG_LEVEL`` (optional, default INFO)
    Logging level for the chat logger.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Load .env from the repo root so `cd chat && python -m nyaya_chat.server`
# picks up the same vars as the mcp server.
_REPO_ROOT = Path(__file__).resolve().parents[2]
load_dotenv(_REPO_ROOT / ".env")

# Curated default tool set. The nyaya MCP server exposes 17 tools; we expose
# the ones most useful for grounded Q&A. Override via CHAT_TOOLS (comma-sep).
DEFAULT_TOOLS: tuple[str, ...] = (
    "hybrid_search",
    "search_law",
    "get_section",
    "get_article",
    "get_judgment",
    "cross_reference",
    "resolve_citation",
    "list_acts",
    "corpus_stats",
)


def _redact(secret: str | None) -> str | None:
    if not secret:
        return None
    if len(secret) <= 8:
        return "***"
    return f"{secret[:5]}…{secret[-3:]}"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="", env_file=None, extra="ignore")

    nvidia_api_key: SecretStr = Field(..., alias="NVIDIA_API_KEY")
    mcp_url: str = Field(default="http://localhost:8000/mcp", alias="NYAYA_MCP_URL")
    llm_model: str = Field(default="nvidia/nemotron-3-ultra-550b-a55b", alias="CHAT_LLM_MODEL")
    llm_temperature: float = Field(default=0.1, alias="CHAT_LLM_TEMPERATURE")
    llm_max_tokens: int = Field(default=2048, alias="CHAT_LLM_MAX_TOKENS")
    llm_timeout_s: float = Field(default=60.0, alias="CHAT_LLM_TIMEOUT_S")
    max_message_chars: int = Field(default=4000, alias="CHAT_MAX_MESSAGE_CHARS")
    max_history: int = Field(default=8, alias="CHAT_MAX_HISTORY")
    log_level: str = Field(default="INFO", alias="NYAYA_CHAT_LOG_LEVEL")

    tools: str = Field(default="", alias="CHAT_TOOLS")

    @field_validator("mcp_url")
    @classmethod
    def _validate_mcp_url(cls, v: str) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError("NYAYA_MCP_URL must start with http:// or https://")
        return v.rstrip("/")

    @property
    def tool_allowlist(self) -> tuple[str, ...]:
        """Parse the CHAT_TOOLS env var into a tool allowlist.

        Empty/unset falls back to the curated default set. Otherwise the value
        is split on commas and trimmed.
        """
        raw = self.tools.strip()
        if not raw:
            return DEFAULT_TOOLS
        parts = tuple(t.strip() for t in raw.split(",") if t.strip())
        return parts or DEFAULT_TOOLS

    def as_log_dict(self) -> dict[str, object]:
        return {
            "mcp_url": self.mcp_url,
            "llm_model": self.llm_model,
            "llm_temperature": self.llm_temperature,
            "llm_max_tokens": self.llm_max_tokens,
            "llm_timeout_s": self.llm_timeout_s,
            "max_message_chars": self.max_message_chars,
            "max_history": self.max_history,
            "nvidia_api_key": _redact(self.nvidia_api_key.get_secret_value()),
            "tools": list(self.tool_allowlist),
            "log_level": self.log_level,
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide Settings instance.

    Raises pydantic ValidationError if required env vars are missing.
    Tests should call ``get_settings.cache_clear()`` after monkeypatching env.
    """
    return Settings()  # type: ignore[call-arg]


def reset_settings_cache() -> None:
    """Clear the settings cache. Intended for tests."""
    get_settings.cache_clear()
