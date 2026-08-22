"""Load the nyaya corpus tools as LangChain tools.

The primary path is **native tools** (direct Python imports of ``nyaya.db``
functions wrapped as LangChain ``StructuredTool`` objects). This eliminates
the HTTP loopback overhead of the MCP transport when the chat agent runs in
the same process as the MCP server.

If native tools fail to load (e.g. the ``nyaya`` package is not importable
in a standalone chat deployment), we fall back to the MCP-over-HTTP client
via ``langchain-mcp-adapters``.
"""

from __future__ import annotations

import logging
from typing import Any

from .config import Settings, get_settings

log = logging.getLogger("nyaya_chat.tools")


async def load_tools(settings: Settings | None = None) -> list[Any]:
    """Load the curated tool set for the chat agent.

    Tries native (direct-import) tools first. Falls back to MCP-over-HTTP
    if the nyaya package is not available. Returns an empty list if neither
    path yields tools (the caller should degrade gracefully).
    """
    s = settings or get_settings()

    # ── Primary: native tools (direct Python import) ──
    try:
        from .native_tools import load_native_tools
        tools = await load_native_tools(s)
        if tools:
            return tools
        log.warning("native tools returned empty; falling back to MCP client")
    except ImportError as exc:
        log.info("native tools unavailable (%s); falling back to MCP client", exc)
    except Exception as exc:
        log.warning("native tools failed to load (%s); falling back to MCP client", exc)

    # ── Fallback: MCP-over-HTTP client ──
    return await _load_mcp_tools(s)


async def _load_mcp_tools(s: Settings) -> list[Any]:
    """Connect to the nyaya MCP server over streamable HTTP and return tools."""
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(
        {"nyaya": {"transport": "streamable_http", "url": s.mcp_url}},
        handle_tool_errors=True,
    )
    all_tools = await client.get_tools()
    names = {t.name for t in all_tools}
    if not all_tools:
        log.warning("MCP server at %s returned no tools", s.mcp_url)
        return []

    allow = set(s.tool_allowlist)
    curated = [t for t in all_tools if t.name in allow]
    missing = sorted(allow - names)
    if missing:
        log.warning(
            "tool allowlist references unknown tools (ignored): %s. Available: %s",
            missing, sorted(names),
        )
    log.info(
        "loaded %d/%d MCP tools from %s: %s",
        len(curated), len(all_tools), s.mcp_url, [t.name for t in curated],
    )
    return curated
