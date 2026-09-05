"""Tests for the reflection loop and corpus text delimiter wrapping."""

from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from nyaya_chat.citations import CITATION_RE
from nyaya_chat.graph.synthesis import (
    _has_refusal,
    _has_tool_calls,
    _wrap_tool_results_in_corpus_tags,
)


def _has_citations(text: str) -> bool:
    return bool(CITATION_RE.search(text))


def test_has_citations_true():
    assert _has_citations("Murder [[act: IPC, ref: s. 302]] is punishable.")


def test_has_citations_false():
    assert not _has_citations("No citations here.")


def test_has_citations_empty():
    assert not _has_citations("")


def test_has_refusal_true():
    assert _has_refusal("I could not find a basis in the corpus.")
    assert _has_refusal("No tool result covers this question.")
    assert _has_refusal("This provision is not in the corpus.")


def test_has_refusal_false():
    assert not _has_refusal("The punishment for murder is death.")
    assert not _has_refusal("")


def test_had_tool_calls_true():
    msgs = [
        HumanMessage(content="q"),
        AIMessage(content="", tool_calls=[{"id": "tc1", "name": "get_section", "args": {}}]),
        ToolMessage(content="result", tool_call_id="tc1", name="get_section"),
    ]
    assert _has_tool_calls(msgs)


def test_had_tool_calls_false():
    msgs = [
        HumanMessage(content="q"),
        AIMessage(content="answer"),
    ]
    assert not _has_tool_calls(msgs)


def test_had_tool_calls_empty():
    assert not _has_tool_calls([])


def test_wrap_tool_results_in_corpus_tags():
    msgs = [
        SystemMessage(content="system"),
        HumanMessage(content="q"),
        AIMessage(content="", tool_calls=[{"id": "tc1", "name": "get_section", "args": {}}]),
        ToolMessage(content='{"act": "IPC", "ref": "302"}', tool_call_id="tc1", name="get_section"),
    ]
    wrapped = _wrap_tool_results_in_corpus_tags(msgs)
    # Non-ToolMessages should be unchanged
    assert wrapped[0] == msgs[0]
    assert wrapped[1] == msgs[1]
    assert wrapped[2] == msgs[2]
    # ToolMessage should be wrapped
    assert isinstance(wrapped[3], ToolMessage)
    assert "<corpus_text>" in wrapped[3].content
    assert "</corpus_text>" in wrapped[3].content
    assert '{"act": "IPC", "ref": "302"}' in wrapped[3].content


def test_wrap_tool_results_no_tool_messages():
    msgs = [
        SystemMessage(content="system"),
        HumanMessage(content="q"),
        AIMessage(content="answer"),
    ]
    wrapped = _wrap_tool_results_in_corpus_tags(msgs)
    assert wrapped == msgs


def test_system_prompt_has_corpus_text_instruction():
    from nyaya_chat.llm import SYSTEM_PROMPT
    assert "corpus_text" in SYSTEM_PROMPT
    assert "data" in SYSTEM_PROMPT.lower()
    assert "instructions" in SYSTEM_PROMPT.lower()


def test_system_prompt_rule_numbering_fixed():
    # Rules should be numbered 1, 2, 3, 4, 5 (no gap)
    import re

    from nyaya_chat.llm import SYSTEM_PROMPT
    numbers = re.findall(r"(\d+)\. ", SYSTEM_PROMPT)
    # Convert to ints
    nums = [int(n) for n in numbers]
    # Check that 5 appears (the disclaimer rule, previously numbered 6)
    assert 5 in nums
    assert 6 not in nums


def test_supervisor_prompt_has_followup_rule():
    from nyaya_chat.llm import SUPERVISOR_PROMPT
    assert "follow-up" in SUPERVISOR_PROMPT.lower() or "different query" in SUPERVISOR_PROMPT.lower()
