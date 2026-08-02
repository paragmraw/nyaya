"""Server-side exceptions that map onto MCP error responses."""

from __future__ import annotations


class NotFound(Exception):
    """A requested act/section/article/judgment is not in the corpus."""