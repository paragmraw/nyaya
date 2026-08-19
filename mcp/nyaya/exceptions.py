"""Server-side exceptions that map onto MCP error responses.

The hierarchy is intentionally shallow but structured: every exception carries
a short ``code`` string so MCP clients (LLMs) can branch programmatically
instead of parsing the human-readable ``message``. ``NotFound`` additionally
carries an optional ``hint`` to suggest the next action (e.g. "Try
``semantic_query`` with a topical query").
"""

from __future__ import annotations


class NyayaError(Exception):
    """Base class for all nyaya-raised exceptions.

    Attributes:
        code: Stable, machine-readable error code (e.g. ``"not_found"``,
            ``"database_unavailable"``). Clients branch on this rather than
            parsing ``message``.
        hint: Optional suggested next action for the caller/LLM.
    """

    code: str = "nyaya_error"
    hint: str | None = None

    def __init__(self, message: str = "", *, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        if hint is not None:
            self.hint = hint


class NotFound(NyayaError):
    """A requested act/section/article/judgment is not in the corpus.

    ``kind`` distinguishes what was missing (act vs section vs article vs
    judgment vs schedule vs amendment vs chapter) so callers can choose a
    tailored fallback (e.g. suggest ``list_acts`` for a missing act, but
    ``semantic_query`` for a missing section).
    """

    code = "not_found"

    def __init__(
        self,
        message: str = "",
        *,
        kind: str = "unknown",
        hint: str | None = None,
    ) -> None:
        super().__init__(message, hint=hint)
        self.kind = kind


class DatabaseUnavailable(NyayaError):
    """The Postgres/Supabase backing store could not be reached or timed out."""

    code = "database_unavailable"


class SearchError(NyayaError):
    """A full-text or semantic search query failed at the DB/embedding layer."""

    code = "search_error"


class ConfigurationError(NyayaError):
    """A required environment variable or setting is missing or invalid."""

    code = "configuration_error"


class EmbeddingUnavailable(NyayaError):
    """The semantic-search model is not installed or failed to load.

    Distinct from ``SearchError`` so ``semantic_query`` can surface a precise
    "this build does not include semantic search" message instead of an empty
    result that looks like "no matches".
    """

    code = "embedding_unavailable"
