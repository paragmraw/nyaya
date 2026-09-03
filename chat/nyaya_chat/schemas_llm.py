"""Pydantic schemas for structured LLM output.

These schemas are used with ``ChatNVIDIA.with_structured_output()`` to
enforce structured responses from the model, eliminating fragile free-text
parsing.

- ``Intent``: enum for the guardrail Tier 2 classifier
- ``ToolCallSpec``: a single tool call in a supervisor plan
- ``ToolPlan``: structured output from the supervisor (reasoning + tool calls)
"""

from __future__ import annotations

import enum
from typing import Any

from pydantic import BaseModel, Field


class Intent(enum.StrEnum):
    """Classification of user message intent.

    Inherits from ``str`` so ``with_structured_output`` uses the
    ``guided_choice`` path (most reliable for single-word classification).
    """

    LEGAL = "legal"
    GREETING = "greeting"
    CAPABILITY = "capability"
    THANKS = "thanks"
    OFF_TOPIC = "off_topic"


class ToolCallSpec(BaseModel):
    """A single tool call in a structured tool plan."""

    name: str = Field(
        description="Tool name: semantic_query, get_section, get_article, "
        "get_judgment, cross_reference, or list_acts"
    )
    args: dict[str, Any] = Field(
        description="Arguments for the tool call, matching the tool's schema"
    )


class ToolPlan(BaseModel):
    """Structured output from the supervisor model.

    The model returns its reasoning (which becomes the agent plan shown
    in the frontend) and a list of tool calls to execute in parallel.
    """

    reasoning: str = Field(
        description="Brief reasoning (2-3 sentences) about which legal "
        "sources are needed and why"
    )
    tool_calls: list[ToolCallSpec] = Field(
        description="Tool calls to execute in parallel. Empty list if "
        "no retrieval is needed."
    )
