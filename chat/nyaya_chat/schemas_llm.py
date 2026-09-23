"""Pydantic schemas for structured LLM output.

These schemas are used with ``ChatNVIDIA.with_structured_output()`` to
enforce structured responses from the model, eliminating fragile free-text
parsing.

- ``Intent``: enum for the guardrail Tier 2 classifier
"""

from __future__ import annotations

import enum


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
