"""Pydantic models for the chat HTTP API."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class HistoryTurn(BaseModel):
    """A single prior turn supplied by the client for context.

    ``role`` is ``user`` or ``assistant`` (tool messages are server-internal).
    """

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=8000)


class ChatRequest(BaseModel):
    """Body for ``POST /chat``.

    ``message`` is the new user turn. ``history`` is an optional list of prior
    turns (capped by ``Settings.max_history`` server-side). We do not persist
    anything; the client is the source of truth for conversation history.
    """

    message: str = Field(min_length=1, max_length=4000, description="The new user message.")
    history: list[HistoryTurn] = Field(default_factory=list)

    @field_validator("message")
    @classmethod
    def _non_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("message must not be blank")
        return v


class StructuredCitation(BaseModel):
    """A single citation extracted by the structured-output synthesis step.

    Emitted as part of a ``CitedAnswer`` when Nemotron's structured-output
    capability is used to guarantee citation shape.
    """

    act: str = Field(description="Act short name, e.g. IPC, BNS, Constitution")
    ref: str = Field(description="Section/article number, e.g. s. 302, Art. 21")
    quote: str | None = Field(
        default=None,
        description="Optional short quote from the provision",
    )


class CitedAnswer(BaseModel):
    """Schema-enforced answer returned by ``with_structured_output``.

    The synthesis node calls
    ``get_base_model().with_structured_output(CitedAnswer)`` to transform the
    ReAct loop's raw answer + retrieved tool results into this object. The
    ``citations`` list is guaranteed to match the schema — no regex parsing
    of inline ``[[act:…]]`` markers needed.
    """

    answer: str = Field(description="The grounded answer text, without [[act:...]] markers")
    citations: list[StructuredCitation] = Field(
        description="Every citation referenced in the answer"
    )
    reasoning: str = Field(
        default="",
        description="Brief reasoning trace for why these provisions apply",
    )


class ChatSubHealthResponse(BaseModel):
    """Health payload for the chat sub-app (served at GET /chat/health)."""
    status: Literal["healthy", "degraded"]
    model: str
    tools_loaded: int
