"""Shared helpers for MCP tool implementations."""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Callable
from typing import Any, ParamSpec, TypeVar

P = ParamSpec("P")
T = TypeVar("T")

# Hard cap on user-supplied query strings; prevents DoS via expensive
# ts_headline / embedding operations on huge inputs.
MAX_QUERY_LENGTH = 4096


def validate_query_length(query: str) -> str:
    """Validate that a query string is within the allowed length.

    Raises SearchError if the query exceeds MAX_QUERY_LENGTH.
    """
    from ..exceptions import SearchError

    if len(query) > MAX_QUERY_LENGTH:
        raise SearchError(
            f"Query too long ({len(query)} chars); maximum is {MAX_QUERY_LENGTH}.",
            hint="Shorten the query or use a more specific search term.",
        )
    return query


def run_sync(func: Callable[P, T]) -> Callable[P, Any]:
    """Decorator: run ``func`` in a worker thread so it can be awaited.

    Wraps a synchronous DB-bound function so the FastMCP event loop stays
    responsive while Postgres queries are in flight (Supabase RTT is
    50-200 ms).

    Usage::

        @run_sync
        def my_tool(query: str) -> SearchResponse:
            return db.search_all(query)
    """

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        return await asyncio.to_thread(func, *args, **kwargs)

    return wrapper
