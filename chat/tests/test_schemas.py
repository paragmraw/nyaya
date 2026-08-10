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
    h = ChatSubHealthResponse(status="healthy", model="nvidia/nemotron-3-super-120b-a12b", tools_loaded=9)
    assert h.status == "healthy"
    assert h.tools_loaded == 9


def test_structured_citation_minimal():
    from nyaya_chat.schemas import StructuredCitation
    c = StructuredCitation(act="IPC", ref="s. 302")
    assert c.act == "IPC"
    assert c.ref == "s. 302"
    assert c.quote is None


def test_structured_citation_with_quote():
    from nyaya_chat.schemas import StructuredCitation
    c = StructuredCitation(act="BNS", ref="s. 103", quote="Whoever commits murder...")
    assert c.quote == "Whoever commits murder..."


def test_cited_answer_with_citations():
    from nyaya_chat.schemas import CitedAnswer, StructuredCitation
    ca = CitedAnswer(
        answer="Murder is punishable by death or life imprisonment.",
        citations=[StructuredCitation(act="IPC", ref="s. 302")],
        reasoning="IPC 302 defines the punishment for murder.",
    )
    assert ca.answer == "Murder is punishable by death or life imprisonment."
    assert len(ca.citations) == 1
    assert ca.citations[0].act == "IPC"
    assert ca.reasoning == "IPC 302 defines the punishment for murder."


def test_cited_answer_empty_citations():
    from nyaya_chat.schemas import CitedAnswer
    ca = CitedAnswer(answer="No relevant provision found.", citations=[])
    assert ca.citations == []
    assert ca.reasoning == ""  # default


def test_cited_answer_requires_answer():
    from nyaya_chat.schemas import CitedAnswer
    with pytest.raises(Exception):
        CitedAnswer(citations=[])  # missing required answer field
