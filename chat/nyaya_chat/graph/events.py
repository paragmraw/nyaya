"""Typed SSE event emitters for graph nodes (LangGraph custom stream mode).

Nodes emit the semantic events of the SSE contract — ``status``, ``plan``,
``token``, ``reasoning``, ``tool_start``, ``tool_result``, ``citations``,
``correction``, ``usage`` — through LangGraph's ``get_stream_writer()``
custom stream. ``streaming.py`` consumes ``stream_mode="custom"`` and
projects each emitted dict onto the wire as ``event: <type>`` with the
remaining keys as the data payload.

This replaces the old inference layer, which reconstructed phase
transitions from node-name-keyed ``updates`` state and routed raw token
chunks by node-name string comparison — the coupling that broke whenever a
node was renamed.

``writer()`` falls back to a no-op outside a runnable context
(``get_stream_writer`` raises there — direct node invocations in tests emit
nothing, which is exactly right).
"""

from __future__ import annotations

import logging
import time
from typing import Any

log = logging.getLogger("nyaya_chat.graph.events")


def writer() -> Any:
    """Return the LangGraph stream writer, or a no-op outside a runnable."""
    try:
        from langgraph.config import get_stream_writer
        return get_stream_writer()
    except RuntimeError:
        return lambda _payload: None


def emit(payload: dict[str, Any]) -> None:
    """Emit one event dict onto the custom stream."""
    try:
        writer()(payload)
    except Exception:  # pragma: no cover - emission must never break a turn
        log.debug("event emission failed", exc_info=True)


def status(msg: str, rid: str) -> None:
    """Phase transition: analyzing/searching/composing."""
    emit({"type": "status", "msg": msg, "rid": rid})


def plan(content: str) -> None:
    """Supervisor plan text (the reasoning of a structured ToolPlan)."""
    emit({"type": "plan", "content": content})


def token(content: str) -> None:
    """Synthesis answer token delta."""
    emit({"type": "token", "content": content})


def reasoning(content: str) -> None:
    """Reasoning-content delta (Nemotron thinking output)."""
    emit({"type": "reasoning", "content": content})


def tool_start(tc_id: str, name: str, args: dict[str, Any]) -> None:
    """The model called a tool (args echo capped by the caller)."""
    emit({"type": "tool_start", "id": tc_id, "name": name, "args": args})


def tool_result(tc_id: str, name: str, summary: str) -> None:
    """A tool finished; ``summary`` is the UI panel text (pre-capped)."""
    emit({"type": "tool_result", "id": tc_id, "name": name, "summary": summary})


def citations(cites: list[dict[str, str]]) -> None:
    """Grounded citations parsed from the VERIFIED answer."""
    emit({"type": "citations", "citations": cites})


def correction(content: str) -> None:
    """The verified answer, ONLY when it differs from the streamed tokens."""
    emit({"type": "correction", "content": content})


def usage(usage_metadata: dict[str, Any]) -> None:
    """Token-usage totals from the final model call (per-turn log only)."""
    emit({"type": "usage", "usage": usage_metadata})


# ---------------------------------------------------------------------------
# Phase timing (observability, not SSE)
# ---------------------------------------------------------------------------

def timed_phase(state: dict[str, Any], phase: str, t0: float) -> float:
    """Record one phase's duration (ms) in ``state['phase_ms']``.

    Call at the END of a phase with the ``time.monotonic()`` value captured
    at its start::

        t0 = time.monotonic()
        ... work ...
        timed_phase(state, "supervisor_ms", t0)

    Rounds accumulate (summed per key); the streamer includes the totals in
    the per-turn completion log line. Returns the elapsed ms.
    """
    elapsed_ms = (time.monotonic() - t0) * 1000.0
    rounds = state.setdefault("phase_ms", {})
    rounds[phase] = rounds.get(phase, 0.0) + elapsed_ms
    return elapsed_ms
