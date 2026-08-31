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


class ChatSubHealthResponse(BaseModel):
    """Health payload for the chat sub-app (served at GET /chat/health).

    ``reason`` explains a ``degraded`` status (agent still initializing, or
    no corpus tools loaded); ``None`` when healthy. Optional — the field the
    frontend actually consumes is ``model`` (the model badge in
    ``ChatPanel.tsx``), which is present in every response.
    """
    status: Literal["healthy", "degraded"]
    model: str
    tools_loaded: int
    reason: str | None = None
