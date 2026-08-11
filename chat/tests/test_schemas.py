"""Tests for nyaya_chat.schemas — request validation."""

from __future__ import annotations

import pytest


def test_chat_request_minimal():
    from nyaya_chat.schemas import ChatRequest
    r = ChatRequest(message="What is Section 302 IPC?")
    assert r.message == "What is Section 302 IPC?"
    assert r.history == []


def test_chat_request_with_history():
    from nyaya_chat.schemas import ChatRequest, HistoryTurn
    r = ChatRequest(
        message="and BNS equivalent?",
        history=[HistoryTurn(role="user", content="q1"), HistoryTurn(role="assistant", content="a1")],
    )
    assert len(r.history) == 2
    assert r.history[0].role == "user"


def test_blank_message_rejected():
    from nyaya_chat.schemas import ChatRequest
    with pytest.raises(Exception):
        ChatRequest(message="   ")


def test_empty_message_rejected():
    from nyaya_chat.schemas import ChatRequest
    with pytest.raises(Exception):
        ChatRequest(message="")


def test_message_too_long_rejected():
    from nyaya_chat.schemas import ChatRequest
    with pytest.raises(Exception):
        ChatRequest(message="x" * 4001)


def test_invalid_history_role_rejected():
    from nyaya_chat.schemas import HistoryTurn
    with pytest.raises(Exception):
        HistoryTurn(role="tool", content="x")  # type: ignore[arg-type]


def test_health_response():
    from nyaya_chat.schemas import ChatSubHealthResponse
    h = ChatSubHealthResponse(status="healthy", model="nvidia/nemotron-3.5-lightning-30b-a3b", tools_loaded=9)
    assert h.status == "healthy"
    assert h.tools_loaded == 9
