"""Shared helpers for MCP tool implementations."""

from __future__ import annotations

import asyncio
import functools
from typing import Any, Callable, TypeVar

T = TypeVar("T")


def run_sync(func: Callable[..., T]) -> Callable[..., Any]:
    """Decorator: wrap a synchronous DB-bound function so it can be awaited
    from an async tool handler without blocking the event loop.

    The wrapped function becomes a coroutine that runs ``func`` in a worker
    thread via ``asyncio.to_thread``. This keeps the FastMCP event loop
    responsive while Postgres queries are in flight (Supabase RTT is 50-200 ms).

    Usage::

        @run_sync
        def my_tool(query: str) -> SearchResponse:
            return db.search_all(query)
    """

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        return await asyncio.to_thread(func, *args, **kwargs)

    return wrapper