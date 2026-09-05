"""MCP-over-HTTP fallback loader for the chat tools.

Used when the native path (direct import of ``nyaya.db``) is unavailable —
e.g. a standalone chat deployment without the mcp package installed. The
tools are filtered to ``tools_layer.spec.TOOL_SPECS`` names so the model sees
the SAME interface as the native path; result cleaning happens in the graph's
tools node (``tools_layer.cleaning``), shared by both paths.
"""

from __future__ import annotations

import logging

from ..config import Settings
from .spec import TOOL_SPECS

log = logging.getLogger("nyaya_chat.tools_layer.mcp_fallback")


async def load_mcp_tools(settings: Settings) -> list:
    """Connect to the nyaya MCP server over streamable HTTP and return tools."""
    from langchain_mcp_adapters.client import MultiServerMCPClient

    client = MultiServerMCPClient(
        {"nyaya": {"transport": "streamable_http", "url": settings.mcp_url}},
        handle_tool_errors=True,
    )
    all_tools = await client.get_tools()
    names = {t.name for t in all_tools}
    if not all_tools:
        log.warning("MCP server at %s returned no tools", settings.mcp_url)
        return []

    allow = {s.name for s in TOOL_SPECS}
    curated = [t for t in all_tools if t.name in allow]
    missing = sorted(allow - names)
    if missing:
        log.warning(
            "tool allowlist references unknown tools (ignored): %s. Available: %s",
            missing, sorted(names),
        )
    log.info(
        "loaded %d/%d MCP tools from %s: %s",
        len(curated), len(all_tools), settings.mcp_url, [t.name for t in curated],
    )
    return curated
