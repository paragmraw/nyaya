"""Tool loading for the chat agent.

The primary path is **native tools** (direct Python imports of ``nyaya.db``
wrapped as LangChain ``StructuredTool`` objects). If native tools fail to
load (e.g. the ``nyaya`` package is not importable in a standalone chat
deployment), we fall back to the MCP-over-HTTP client via
``langchain-mcp-adapters``. Both paths derive their interface from
``tools_layer.spec``.
"""

from __future__ import annotations

import logging

from ..config import Settings, get_settings

log = logging.getLogger("nyaya_chat.tools_layer")


async def load_tools(settings: Settings | None = None) -> list:
    """Load the curated tool set for the chat agent.

    Tries native (direct-import) tools first. Falls back to MCP-over-HTTP
    if the nyaya package is not available. Returns an empty list if neither
    path yields tools (the caller should degrade gracefully).
    """
    s = settings or get_settings()

    # ── Primary: native tools (direct Python import) ──
    try:
        from .native import load_native_tools
        tools = await load_native_tools(s)
        if tools:
            return tools
        log.warning("native tools returned empty; falling back to MCP client")
    except ImportError as exc:
        log.info("native tools unavailable (%s); falling back to MCP client", exc)
    except Exception as exc:
        log.warning("native tools failed to load (%s); falling back to MCP client", exc)

    # ── Fallback: MCP-over-HTTP client ──
    from .mcp_fallback import load_mcp_tools
    return await load_mcp_tools(s)
