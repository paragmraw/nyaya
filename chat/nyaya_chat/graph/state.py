"""Graph state for the supervisor → parallel-tools → synthesis chat graph.

The compiled graph is built once and shared across all requests, so any
per-request memory (tool-call dedup, round counters, phase timings) must
live in state, never on node instances.
"""

from __future__ import annotations

from typing import Annotated, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class ChatState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], add_messages]
    # Request id, echoed on status/error SSE events. Carried in state so
    # every node's emitters share the caller's rid.
    rid: str
    # 1-based synthesis-round counter (incremented by the synthesis node);
    # drives the reflection routing cap (settings.max_reflection_rounds) and
    # tells the supervisor it is on a reflection round (round >= 1).
    round: int
    # Per-request tool-call dedup state used by the tools node (see
    # graph/tools_node.py).
    dedup_seen: dict[str, str]
    dedup_results: dict[str, str]
    # Per-phase wall-clock timings, accumulated across rounds (ms). Logged by
    # the streamer's per-turn completion line; not part of the SSE contract.
    phase_ms: dict[str, float]
    # Last usage_metadata seen on a model chunk (cumulative per model call —
    # the final synthesis round's totals). Consumed by the per-turn log.
    usage: dict[str, int]
