"""Tests for nyaya_chat.agent — graph build + message assembly + ReAct routing."""

from __future__ import annotations

import pytest


def test_build_messages_assembles_system_history_user():
    from nyaya_chat.agent import _build_messages
    msgs = _build_messages("hello", [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
    ])
    assert len(msgs) == 4  # system + 2 history + new user
    assert msgs[0].content.startswith("You are Nyaya")
    assert msgs[1].content == "q1"
    assert msgs[3].content == "hello"


def test_build_messages_empty_history():
    from nyaya_chat.agent import _build_messages
    msgs = _build_messages("hi", [])
    assert len(msgs) == 2  # system + user


def test_system_prompt_mentions_citation_format():
    from nyaya_chat.llm import SYSTEM_PROMPT
    assert "[[act:" in SYSTEM_PROMPT
    assert "not legal advice" in SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_build_agent_with_tools(fake_model, fake_tools, monkeypatch):
    from langchain_core.messages import AIMessage
    fake_model.responses = [
        AIMessage(content="", tool_calls=[{"id": "tc1", "name": "get_section", "args": {"query": "IPC 302"}}]),
        AIMessage(content="Done."),
    ]
    from nyaya_chat import agent as agent_mod
    # load_tools already patched via fake_tools fixture.
    graph, tools = await agent_mod.build_agent()
    assert len(tools) == 2
    # The graph is a compiled StateGraph; check it has the expected nodes.
    assert "agent" in graph.nodes
    assert "tools" in graph.nodes


@pytest.mark.asyncio
async def test_build_agent_without_tools_degrades(fake_model, monkeypatch):
    from langchain_core.messages import AIMessage
    fake_model.responses = [AIMessage(content="hi")]
    from nyaya_chat import agent as agent_mod
    from nyaya_chat import tools as tools_mod
    async def _empty(_=None):
        return []
    monkeypatch.setattr(tools_mod, "load_tools", _empty)
    monkeypatch.setattr(agent_mod, "load_tools", _empty, raising=False)
    graph, tools = await agent_mod.build_agent()
    assert tools == []
    assert "agent" in graph.nodes
    assert "tools" not in graph.nodes


@pytest.mark.asyncio
async def test_agent_runs_react_loop(fake_model, fake_tools):
    """End-to-end-ish: model calls a tool, then answers; the graph produces
    a final AIMessage via ainvoke."""
    from langchain_core.messages import AIMessage
    fake_model.responses = [
        AIMessage(content="", tool_calls=[{"id": "tc1", "name": "get_section", "args": {"query": "302"}}]),
        AIMessage(content="Punishment for murder is death or life [[act: IPC, ref: s. 302]]."),
    ]
    from nyaya_chat.agent import _build_messages, build_agent
    graph, tools = await build_agent()
    msgs = _build_messages("What is IPC 302?", [])
    result = await graph.ainvoke({"messages": msgs})
    out = result["messages"]
    # final message is the model's answer
    assert any(getattr(m, "content", "").startswith("Punishment for murder") for m in out)
    assert fake_model.calls  # the model was invoked


@pytest.mark.asyncio
async def test_agent_has_synthesis_node(fake_model, fake_tools):
    """The compiled graph should have a 'synthesis' node."""
    from nyaya_chat import agent as agent_mod
    graph, _tools = await agent_mod.build_agent()
    assert "synthesis" in graph.nodes


@pytest.mark.asyncio
async def test_synthesis_node_produces_cited_answer(fake_model, fake_tools):
    """The synthesis node should call with_structured_output and return a CitedAnswer."""
    from langchain_core.messages import AIMessage
    from nyaya_chat.schemas import CitedAnswer, StructuredCitation
    fake_model.responses = [
        AIMessage(content="Punishment for murder is death or life imprisonment."),
    ]
    fake_model._structured_result = CitedAnswer(
        answer="Punishment for murder is death or life imprisonment.",
        citations=[StructuredCitation(act="IPC", ref="s. 302")],
        reasoning="IPC 302 defines murder punishment.",
    )
    from nyaya_chat.agent import _build_messages, build_agent
    graph, _tools = await build_agent()
    msgs = _build_messages("What is IPC 302?", [])
    result = await graph.ainvoke({"messages": msgs})
    assert "cited_answer" in result
    assert result["cited_answer"] is not None
    assert isinstance(result["cited_answer"], CitedAnswer)
    assert len(result["cited_answer"].citations) == 1
    assert result["cited_answer"].citations[0].act == "IPC"


@pytest.mark.asyncio
async def test_synthesis_fallback_on_none(fake_model, fake_tools):
    """If structured output returns None, cited_answer should be None (graceful fallback)."""
    from langchain_core.messages import AIMessage
    fake_model.responses = [
        AIMessage(content="Some answer without citations."),
    ]
    fake_model._structured_result = None
    from nyaya_chat.agent import _build_messages, build_agent
    graph, _tools = await build_agent()
    msgs = _build_messages("What is IPC 302?", [])
    result = await graph.ainvoke({"messages": msgs})
    assert result.get("cited_answer") is None
    # The raw answer should still be in messages
    assert any(getattr(m, "content", "") == "Some answer without citations." for m in result["messages"])


@pytest.mark.asyncio
async def test_synthesis_fallback_on_exception(fake_model, fake_tools, monkeypatch):
    """If structured output raises, cited_answer should be None (graceful fallback)."""
    from langchain_core.messages import AIMessage
    fake_model.responses = [
        AIMessage(content="Some answer."),
    ]
    # Make with_structured_output's runnable raise on ainvoke
    class _BoomRunnable:
        def invoke(self, messages, **kw):
            raise RuntimeError("structured output failed")
        async def ainvoke(self, messages, **kw):
            raise RuntimeError("structured output failed")
    fake_model.with_structured_output = lambda schema, **kw: _BoomRunnable()
    from nyaya_chat import agent as agent_mod
    # Re-patch get_base_model since we overrode with_structured_output on fake_model
    monkeypatch.setattr(agent_mod, "get_base_model", lambda _=None: fake_model, raising=False)
    from nyaya_chat.agent import _build_messages, build_agent
    graph, _tools = await build_agent()
    msgs = _build_messages("What is IPC 302?", [])
    result = await graph.ainvoke({"messages": msgs})
    assert result.get("cited_answer") is None
