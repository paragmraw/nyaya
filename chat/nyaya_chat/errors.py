"""Turn-level exceptions with a user-facing SSE error code.

Nodes raise :class:`TurnError` for failures that are *expected* outcomes of a
turn — an empty synthesis stream, a dead corpus, a blown time budget — so the
SSE layer can emit a specific, human-explainable ``error`` event (with the
code the frontend's humanizer maps or gracefully falls back on) instead of
the generic ``agent_error``/“internal server error” bookend.
"""

from __future__ import annotations


class TurnError(Exception):
    """A turn failed with a specific, user-facing error code + detail.

    ``code`` is the SSE ``error.message`` (a stable machine code the
    frontend's humanizer maps to copy); ``detail`` is the log-facing
    explanation. Both ride on the exception so ``streaming.py`` can project
    them without string parsing.
    """

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail
