"""Tests for nyaya_chat.agent — graph build + message assembly + supervisor routing."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, ToolMessage


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


def test_system_prompt_instructs_structuring_and_glossing():
    from nyaya_chat.llm import SYSTEM_PROMPT
    assert "##" in SYSTEM_PROMPT
    assert "blockquote" in SYSTEM_PROMPT.lower()
    assert "table" in SYSTEM_PROMPT.lower()
    assert "plain language" in SYSTEM_PROMPT.lower()
    assert "Never use a single #" in SYSTEM_PROMPT


def test_supervisor_prompt_instructs_parallel_delegation():
    from nyaya_chat.llm import SUPERVISOR_PROMPT
    assert "parallel" in SUPERVISOR_PROMPT.lower() or "single" in SUPERVISOR_PROMPT.lower()
    assert "synthesis" in SUPERVISOR_PROMPT.lower() or "delegate" in SUPERVISOR_PROMPT.lower()


@pytest.mark.asyncio
async def test_build_agent_with_tools(fake_model, fake_tools, monkeypatch):
    fake_model.responses = [
        AIMessage(content="", tool_calls=[{"id": "tc1", "name": "get_section", "args": {"query": "IPC 302"}}]),
        AIMessage(content="Done."),
    ]
    from nyaya_chat import agent as agent_mod
    graph, tools = await agent_mod.build_agent()
    assert len(tools) == 2
    # The graph has supervisor/tools/synthesis nodes.
    assert "supervisor" in graph.nodes
    assert "tools" in graph.nodes
    assert "synthesis" in graph.nodes
    assert "agent" not in graph.nodes


@pytest.mark.asyncio
async def test_build_agent_without_tools_degrades(fake_model, monkeypatch):
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
    assert "supervisor" not in graph.nodes


@pytest.mark.asyncio
async def test_agent_supervisor_emits_tool_calls(fake_model, fake_tools):
    """Supervisor emits a tool call, tools node runs, synthesis produces answer."""
    fake_model.responses = [
        # Supervisor: emits a tool call to get_section
        AIMessage(content="", tool_calls=[{
            "id": "tc1", "name": "get_section", "args": {"query": "302"},
        }]),
        # Synthesis: produces final answer
        AIMessage(content="Punishment for murder is death or life [[act: IPC, ref: s. 302]]."),
    ]
    from nyaya_chat.agent import _build_messages, build_agent
    graph, tools = await build_agent()
    msgs = _build_messages("What is IPC 302?", [])
    result = await graph.ainvoke({"messages": msgs})
    out = result["messages"]
    # Final message should be the synthesis answer
    assert any(getattr(m, "content", "").startswith("Punishment for murder") for m in out)
    assert fake_model.calls  # the model was invoked


def test_tool_call_key_normalises_args():
    from nyaya_chat.agent import _tool_call_key
    k1 = _tool_call_key("get_section", {"section_number": 302, "act": "IPC"})
    k2 = _tool_call_key("get_section", {"act": "IPC", "section_number": "302"})
    assert k1 == k2


def test_tool_call_key_different_args_differ():
    from nyaya_chat.agent import _tool_call_key
    k1 = _tool_call_key("get_section", {"section_number": "302"})
    k2 = _tool_call_key("get_section", {"section_number": "303"})
    assert k1 != k2


def test_tool_call_key_different_tools_differ():
    from nyaya_chat.agent import _tool_call_key
    k1 = _tool_call_key("get_section", {"query": "302"})
    k2 = _tool_call_key("get_article", {"query": "302"})
    assert k1 != k2


# ---------------------------------------------------------------------------
# DedupToolNode tests
# ---------------------------------------------------------------------------

class _FakeToolNode:
    """Stand-in for ToolNode that records calls and returns synthetic ToolMessages."""

    def __init__(self, tools):
        self._tools = tools
        self.invoke_count = 0
        self.calls_seen: list[dict] = []

    async def ainvoke(self, state):
        self.invoke_count += 1
        messages = state.get("messages", [])
        last_ai = None
        for m in reversed(messages):
            if isinstance(m, AIMessage) and getattr(m, "tool_calls", None):
                last_ai = m
                break
        if last_ai is None:
            return {"messages": []}
        out = []
        for tc in last_ai.tool_calls:
            self.calls_seen.append(tc)
            out.append(ToolMessage(
                content=f"result({tc['args'].get('query', '')})",
                tool_call_id=tc.get("id", ""),
                name=tc["name"],
            ))
        return {"messages": out}


def _make_fake_tool(name="get_section"):
    from langchain_core.tools import StructuredTool
    async def _arun(query: str = ""):
        return f"result({query})"
    return StructuredTool.from_function(
        lambda q="": q, coroutine=_arun, name=name, description="fake",
    )


@pytest.mark.asyncio
async def test_dedup_skips_duplicate_calls():
    from nyaya_chat.agent import DedupToolNode

    tool = _make_fake_tool("get_section")
    dedup = DedupToolNode([tool])
    fake_node = _FakeToolNode([tool])
    dedup._tool_node = fake_node

    # First call: unique
    state = {"messages": [AIMessage(
        content="", tool_calls=[
            {"id": "tc1", "name": "get_section", "args": {"query": "302"}},
        ]),
    ]}
    result = await dedup(state)
    msgs = result["messages"]
    assert len(msgs) == 1
    assert isinstance(msgs[0], ToolMessage)
    assert "result(302)" in str(msgs[0].content)
    assert len(fake_node.calls_seen) == 1

    # Second call with same args: duplicate, should be skipped
    state2 = {"messages": [AIMessage(
        content="", tool_calls=[
            {"id": "tc2", "name": "get_section", "args": {"query": "302"}},
        ]),
    ]}
    result2 = await dedup(state2)
    msgs2 = result2["messages"]
    assert len(msgs2) == 1
    assert isinstance(msgs2[0], ToolMessage)
    assert "result(302)" in str(msgs2[0].content)
    assert len(fake_node.calls_seen) == 1


@pytest.mark.asyncio
async def test_dedup_passes_unique_calls_through():
    from nyaya_chat.agent import DedupToolNode

    tool = _make_fake_tool("get_section")
    dedup = DedupToolNode([tool])
    fake_node = _FakeToolNode([tool])
    dedup._tool_node = fake_node

    state = {"messages": [AIMessage(
        content="", tool_calls=[
            {"id": "tc1", "name": "get_section", "args": {"query": "302"}},
            {"id": "tc2", "name": "get_section", "args": {"query": "303"}},
        ]),
    ]}
    result = await dedup(state)
    msgs = result["messages"]
    assert len(msgs) == 2
    assert all(isinstance(m, ToolMessage) for m in msgs)
    assert len(fake_node.calls_seen) == 2


@pytest.mark.asyncio
async def test_dedup_mixed_unique_and_duplicate():
    from nyaya_chat.agent import DedupToolNode

    tool = _make_fake_tool("get_section")
    dedup = DedupToolNode([tool])
    fake_node = _FakeToolNode([tool])
    dedup._tool_node = fake_node

    state1 = {"messages": [AIMessage(
        content="", tool_calls=[
            {"id": "tc1", "name": "get_section", "args": {"query": "302"}},
        ]),
    ]}
    await dedup(state1)

    state2 = {"messages": [AIMessage(
        content="", tool_calls=[
            {"id": "tc2", "name": "get_section", "args": {"query": "302"}},
            {"id": "tc3", "name": "get_section", "args": {"query": "304"}},
        ]),
    ]}
    result = await dedup(state2)
    msgs = result["messages"]
    assert len(msgs) == 2
    assert any(tc["args"]["query"] == "304" for tc in fake_node.calls_seen)
    queries_302 = [tc for tc in fake_node.calls_seen if tc["args"].get("query") == "302"]
    assert len(queries_302) == 1
