"""Load the nyaya MCP corpus tools as LangChain tools.

The agent calls the existing nyaya MCP server over streamable HTTP via
``langchain-mcp-adapters``' ``MultiServerMCPClient``. Each tool invocation
opens a short-lived stateless session, so there's no long-held connection
to manage. Tool execution errors (``CallToolResult.isError=True``) are
returned to the model as ``ToolMessage``s for self-correction rather than
crashing the turn (``handle_tool_errors=True`` is the adapter default).
"""

from __future__ import annotations

import logging
from typing import Any

from .config import Settings, get_settings

log = logging.getLogger("nyaya_chat.tools")


async def load_tools(settings: Settings | None = None) -> list[Any]:
    """Connect to the nyaya MCP server and return the curated tool subset.

    The returned tools are LangChain ``BaseTool`` instances ready to pass to
    ``langgraph.prebuilt.ToolNode`` or ``model.bind_tools``. The connection is
    not held — each tool call makes its own stateless HTTP request, so the
    client object is only used to fetch the tool schemas.
    """
    from langchain_mcp_adapters.client import MultiServerMCPClient

    s = settings or get_settings()
    client = MultiServerMCPClient(
        {"nyaya": {"transport": "streamable_http", "url": s.mcp_url}},
        # Errors returned to the model so it can recover; transport failures
        # still raise.
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
            "tool allowlist references unknown tools (ignored): %s. "
            "Available: %s", missing, sorted(names),
        )
    log.info(
        "loaded %d/%d MCP tools from %s: %s",
        len(curated), len(all_tools), s.mcp_url, [t.name for t in curated],
    )
    return curated
