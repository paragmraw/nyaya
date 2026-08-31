"""Tests for nyaya_chat.agent — graph build + message assembly + supervisor routing."""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, ToolMessage

from nyaya_chat.schemas_llm import ToolCallSpec, ToolPlan


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


def test_supervisor_prompt_instructs_structured_plan():
    from nyaya_chat.llm import SUPERVISOR_PROMPT
    # Check for key structured output concepts
    assert "tool" in SUPERVISOR_PROMPT.lower()
    assert "parallel" in SUPERVISOR_PROMPT.lower()


@pytest.mark.asyncio
async def test_build_agent_with_tools(fake_model, fake_tools, monkeypatch):
    # Set up structured output for the supervisor: returns a ToolPlan
    fake_model._structured_result = ToolPlan(
        reasoning="I need to look up IPC section 302.",
        tool_calls=[ToolCallSpec(name="get_section", args={"act": "IPC", "section": "302"})],
    )
    # Synthesis: produces final answer
    fake_model.responses = [
        AIMessage(content="Punishment for murder [[act: IPC, ref: s. 302]]."),
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
    """Supervisor returns a ToolPlan, tools node runs, synthesis produces answer."""
    # Supervisor: structured ToolPlan with a tool call
    fake_model._structured_result = ToolPlan(
        reasoning="I need to look up IPC 302.",
        tool_calls=[ToolCallSpec(name="get_section", args={"act": "IPC", "section": "302"})],
    )
    # Synthesis: produces final answer WITH citation (so reflection doesn't loop)
    fake_model.responses = [
        AIMessage(content="Punishment for murder is death or life [[act: IPC, ref: s. 302]]."),
    ]
    from nyaya_chat.agent import _build_messages, build_agent
    graph, tools = await build_agent()
    msgs = _build_messages("What is IPC 302?", [])
    result = await graph.ainvoke({"messages": msgs}, {"recursion_limit": 50})
    out = result["messages"]
    # Final message should be the synthesis answer
    assert any(getattr(m, "content", "").startswith("Punishment for murder") for m in out)
    assert fake_model.calls  # the model was invoked


def test_parse_text_tool_calls_bracket_format():
    """Parse [[tool_calls]] JSON array [[/tool_calls]] format."""
    from nyaya_chat.agent import _parse_text_tool_calls
    content = '[[tool_calls]]\n[\n {\n  "name": "get_section",\n  "arguments": {"act": "IPC", "section": "302"}\n }\n]\n[[/tool_calls]]'
    calls = _parse_text_tool_calls(content)
    assert len(calls) == 1
    assert calls[0]["name"] == "get_section"
    assert calls[0]["args"]["act"] == "IPC"
    assert calls[0]["args"]["section"] == "302"


def test_parse_text_tool_calls_bare_json():
    """Parse bare JSON object format."""
    from nyaya_chat.agent import _parse_text_tool_calls
    content = '{"name": "get_section", "arguments": {"act": "IPC", "section": "302"}}'
    calls = _parse_text_tool_calls(content)
    assert len(calls) == 1
    assert calls[0]["name"] == "get_section"


def test_parse_text_tool_calls_json_array():
    """Parse bare JSON array format."""
    from nyaya_chat.agent import _parse_text_tool_calls
    content = '[{"name": "get_section", "arguments": {"act": "IPC", "section": "302"}}]'
    calls = _parse_text_tool_calls(content)
    assert len(calls) == 1
    assert calls[0]["name"] == "get_section"


def test_parse_text_tool_calls_no_tool_calls():
    """Return empty list when no tool calls are found."""
    from nyaya_chat.agent import _parse_text_tool_calls
    assert _parse_text_tool_calls("This is a plain text answer.") == []
    assert _parse_text_tool_calls("") == []
    assert _parse_text_tool_calls(None) == []


def test_parse_text_tool_calls_multiple():
    """Parse multiple tool calls from [[tool_calls]] format."""
    from nyaya_chat.agent import _parse_text_tool_calls
    content = '[[tool_calls]]\n[\n {"name": "get_section", "arguments": {"act": "IPC", "section": "302"}},\n {"name": "get_section", "arguments": {"act": "BNS", "section": "103"}}\n]\n[[/tool_calls]]'
    calls = _parse_text_tool_calls(content)
    assert len(calls) == 2
    assert calls[0]["name"] == "get_section"
    assert calls[1]["args"]["act"] == "BNS"


def test_parse_text_xml_tag_wrapped_json():
    """XML-tag wrapped JSON: <toolname>{...json...}</toolname>"""
    from nyaya_chat.agent import _parse_text_tool_calls
    content = '<semantic_query>{"query": "dowry prohibition India laws", "limit": 10}</semantic_query>'
    calls = _parse_text_tool_calls(content)
    assert len(calls) == 1
    assert calls[0]["name"] == "semantic_query"
    assert calls[0]["args"]["query"] == "dowry prohibition India laws"
    assert calls[0]["args"]["limit"] == 10


def test_parse_text_xml_tag_multiple():
    """Multiple XML tags in one response"""
    from nyaya_chat.agent import _parse_text_tool_calls
    content = '<semantic_query>{"query": "IPC 302"}</semantic_query>\n<get_section>{"act": "IPC", "section": "302"}</get_section>'
    calls = _parse_text_tool_calls(content)
    assert len(calls) == 2
    assert calls[0]["name"] == "semantic_query"
    assert calls[1]["name"] == "get_section"


def test_parse_text_bracket_pipe_format():
    """Bracket-pipe: [[tool tool call|tool_name: "...", tool_args: {...}]]"""
    from nyaya_chat.agent import _parse_text_tool_calls
    content = '[[semantic_query tool call|tool_name: "semantic_query", tool_args: {"query": "dowry prohibition India laws", "limit": 10}]]'
    calls = _parse_text_tool_calls(content)
    assert len(calls) == 1
    assert calls[0]["name"] == "semantic_query"
    assert calls[0]["args"]["query"] == "dowry prohibition India laws"


def test_parse_text_bracket_pipe_nested_json():
    """Bracket-pipe with nested JSON objects in tool_args"""
    from nyaya_chat.agent import _parse_text_tool_calls
    content = '[[semantic_query tool call|tool_name: "semantic_query", tool_args: {"query": "test", "filter": {"act": "IPC", "kind": "section"}}}]]'
    calls = _parse_text_tool_calls(content)
    assert len(calls) == 1
    assert calls[0]["args"]["filter"]["act"] == "IPC"


def test_parse_text_attribute_style():
    """Attribute-style: [[tool_name key="value" key2="value2"]]"""
    from nyaya_chat.agent import _parse_text_tool_calls
    content = '[[get_section act="IPC" section="24"]]'
    calls = _parse_text_tool_calls(content)
    assert len(calls) == 1
    assert calls[0]["name"] == "get_section"
    assert calls[0]["args"]["act"] == "IPC"
    assert calls[0]["args"]["section"] == "24"


def test_supervisor_prompt_has_sequential_rules():
    """SUPERVISOR_PROMPT should have rules numbered 1-7 with no duplicates"""
    import re

    from nyaya_chat.llm import SUPERVISOR_PROMPT
    numbers = [int(m) for m in re.findall(r"(\d+)\.\s", SUPERVISOR_PROMPT)]
    assert numbers == sorted(numbers), f"Rules not in order: {numbers}"
    assert len(numbers) == len(set(numbers)), f"Duplicate rule numbers: {numbers}"


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
    """Within ONE request (state carried across rounds), a repeated call is skipped.

    The node is stateless; LangGraph merges the returned ``dedup_seen`` /
    ``dedup_results`` into state, so a later round of the same request sees
    the first round's dedup memory. Here we simulate that by seeding the
    second invoke with the keys the first one returned.
    """
    from nyaya_chat.agent import DedupToolNode

    tool = _make_fake_tool("get_section")
    dedup = DedupToolNode([tool])
    fake_node = _FakeToolNode([tool])
    dedup._tool_node = fake_node

    # First round: unique
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

    # Second round of the SAME request (seeded state): same (name+args) call
    # is a duplicate and must be skipped, reusing the first round's result.
    state2 = {
        "messages": [AIMessage(
            content="", tool_calls=[
                {"id": "tc2", "name": "get_section", "args": {"query": "302"}},
            ]),
        ],
        "dedup_seen": result["dedup_seen"],
        "dedup_results": result["dedup_results"],
    }
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
    """Within one request: a repeated call is skipped, a new one executes."""
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
    result1 = await dedup(state1)

    # Second round of the SAME request (seeded state): "302" is a duplicate,
    # "304" is new and must execute.
    state2 = {
        "messages": [AIMessage(
            content="", tool_calls=[
                {"id": "tc2", "name": "get_section", "args": {"query": "302"}},
                {"id": "tc3", "name": "get_section", "args": {"query": "304"}},
            ]),
        ],
        "dedup_seen": result1["dedup_seen"],
        "dedup_results": result1["dedup_results"],
    }
    result = await dedup(state2)
    msgs = result["messages"]
    assert len(msgs) == 2
    assert any(tc["args"]["query"] == "304" for tc in fake_node.calls_seen)
    queries_302 = [tc for tc in fake_node.calls_seen if tc["args"].get("query") == "302"]
    assert len(queries_302) == 1


@pytest.mark.asyncio
async def test_dedup_state_does_not_leak_across_requests():
    """Regression: dedup memory is per-request, not node instance state.

    The compiled graph (and therefore this node instance) is shared across
    all requests, so a tool call seen in a previous request must NOT be
    treated as a duplicate in a later one — each request starts with fresh
    state and must get fresh tool results.
    """
    from nyaya_chat.agent import DedupToolNode

    tool = _make_fake_tool("get_section")
    dedup = DedupToolNode([tool])
    fake_node = _FakeToolNode([tool])
    dedup._tool_node = fake_node

    call_302 = AIMessage(content="", tool_calls=[
        {"id": "tc1", "name": "get_section", "args": {"query": "302"}},
    ])

    # Request 1: the call executes.
    result1 = await dedup({"messages": [call_302]})
    assert len(fake_node.calls_seen) == 1
    assert "result(302)" in str(result1["messages"][0].content)

    # Request 2: FRESH state (as every new request gets), same (name+args).
    # Must execute again — not skipped, no stale cached result.
    result2 = await dedup({"messages": [AIMessage(
        content="", tool_calls=[
            {"id": "tc2", "name": "get_section", "args": {"query": "302"}},
        ]),
    ]})
    assert len(fake_node.calls_seen) == 2
    msgs2 = result2["messages"]
    assert len(msgs2) == 1
    assert "result(302)" in str(msgs2[0].content)
    assert "(duplicate call skipped)" not in str(msgs2[0].content)

    # Within ONE invocation, a repeated (name+args) call is still deduped:
    # only one of the two identical calls reaches the underlying ToolNode.
    result3 = await dedup({"messages": [AIMessage(
        content="", tool_calls=[
            {"id": "tc3", "name": "get_section", "args": {"query": "302"}},
            {"id": "tc4", "name": "get_section", "args": {"query": "302"}},
        ]),
    ]})
    assert len(fake_node.calls_seen) == 3  # only tc3 executed
    msgs3 = result3["messages"]
    assert len(msgs3) == 2
    assert all("result(302)" in str(m.content) for m in msgs3)


# ---------------------------------------------------------------------------
# Synthesis node: the single authoritative verification + disclaimer pass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_synthesis_node_appends_disclaimer_when_missing(fake_model, settings):
    """The verified message carries the disclaimer — it is NOT appended
    post-stream by the SSE layer."""
    from langchain_core.messages import HumanMessage

    from nyaya_chat.agent import _make_synthesis_node
    from nyaya_chat.llm import DISCLAIMER

    fake_model.responses = [AIMessage(content="Punishment for murder is death.")]
    node = _make_synthesis_node(fake_model, settings, has_tools=False)
    out = await node({"messages": [HumanMessage(content="What is IPC 302?")]})
    content = out["messages"][0].content
    assert content.startswith("Punishment for murder is death.")
    assert content.endswith(f"\n\n*{DISCLAIMER}*")


@pytest.mark.asyncio
async def test_synthesis_node_does_not_duplicate_disclaimer(fake_model, settings):
    """When the model already emitted the disclaimer, it is left as-is."""
    from langchain_core.messages import HumanMessage

    from nyaya_chat.agent import _make_synthesis_node
    from nyaya_chat.llm import DISCLAIMER

    answer = "Answer.\n\nThis is not legal advice; verify citations before filing."
    fake_model.responses = [AIMessage(content=answer)]
    node = _make_synthesis_node(fake_model, settings, has_tools=False)
    out = await node({"messages": [HumanMessage(content="q")]})
    assert out["messages"][0].content == answer
    assert out["messages"][0].content.count(DISCLAIMER) == 1


@pytest.mark.asyncio
async def test_synthesis_node_verifies_and_disclaims_in_one_pass(fake_model, settings):
    """Verification (strip ungrounded citations) and the disclaimer append both
    happen in the synthesis node, so the returned message is final."""
    from langchain_core.messages import HumanMessage, ToolMessage

    from nyaya_chat.agent import _make_synthesis_node
    from nyaya_chat.llm import DISCLAIMER

    fake_model.responses = [AIMessage(
        content="Grounded [[act: IPC, ref: s. 302]] and ungrounded [[act: GhostAct, ref: 1]]."
    )]
    node = _make_synthesis_node(fake_model, settings, has_tools=True)
    state = {
        "messages": [
            HumanMessage(content="What is IPC 302?"),
            AIMessage(content="", tool_calls=[
                {"id": "tc1", "name": "get_section", "args": {"act": "IPC", "section_number": "302"}},
            ]),
            ToolMessage(
                content='{"act": "IPC", "ref": "s. 302", "kind": "section", "text": "..."}',
                tool_call_id="tc1", name="get_section",
            ),
        ],
    }
    out = await node(state)
    content = out["messages"][0].content
    assert "[[act: IPC, ref: s. 302]]" in content
    assert "GhostAct" not in content
    assert content.endswith(f"\n\n*{DISCLAIMER}*")
