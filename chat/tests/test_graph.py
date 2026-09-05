"""Tests for nyaya_chat.graph — build, message assembly, nodes, routing."""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from nyaya_chat.schemas_llm import ToolCallSpec, ToolPlan


def test_build_messages_assembles_system_history_user():
    from nyaya_chat.graph.supervisor import build_messages
    msgs = build_messages("hello", [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
    ])
    assert len(msgs) == 4  # system + 2 history + new user
    assert msgs[0].content.startswith("You are Nyaya")
    assert msgs[1].content == "q1"
    assert msgs[3].content == "hello"


def test_build_messages_empty_history():
    from nyaya_chat.graph.supervisor import build_messages
    msgs = build_messages("hi", [])
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


def test_supervisor_prompt_lists_allowlisted_tools():
    """The supervisor prompt's tool list is rendered from tools_layer.spec —
    a drift between the prompt and the allowlist is impossible by construction."""
    from nyaya_chat.llm import SUPERVISOR_PROMPT
    from nyaya_chat.tools_layer.spec import TOOL_SPECS
    for spec in TOOL_SPECS:
        assert f"- {spec.name}:" in SUPERVISOR_PROMPT
    assert "parallel" in SUPERVISOR_PROMPT.lower()


def test_supervisor_prompt_has_sequential_rules():
    """SUPERVISOR_PROMPT should have rules numbered 1-7 with no duplicates"""
    import re

    from nyaya_chat.llm import SUPERVISOR_PROMPT
    numbers = [int(m) for m in re.findall(r"(\d+)\.\s", SUPERVISOR_PROMPT)]
    assert numbers == sorted(numbers), f"Rules not in order: {numbers}"
    assert len(numbers) == len(set(numbers)), f"Duplicate rule numbers: {numbers}"


def test_reflection_prompt_constrains_to_semantic_query():
    from nyaya_chat.prompts import REFLECTION_PROMPT
    assert "semantic_query" in REFLECTION_PROMPT
    assert "DIFFERENT" in REFLECTION_PROMPT


@pytest.mark.asyncio
async def test_build_graph_with_tools(fake_model, fake_tools):
    # Set up structured output for the supervisor: returns a ToolPlan
    fake_model._structured_result = ToolPlan(
        reasoning="I need to look up IPC section 302.",
        tool_calls=[ToolCallSpec(name="get_section", args={"act": "IPC", "section": "302"})],
    )
    # Synthesis: produces final answer
    fake_model.responses = [
        AIMessage(content="Punishment for murder [[act: IPC, ref: s. 302]]."),
    ]
    from nyaya_chat import graph as graph_mod
    from nyaya_chat.config import get_settings
    graph, tools = await graph_mod.build_graph(get_settings())
    assert len(tools) == 2
    # The graph has supervisor/tools/synthesis nodes.
    assert "supervisor" in graph.nodes
    assert "tools" in graph.nodes
    assert "synthesis" in graph.nodes
    assert "agent" not in graph.nodes


@pytest.mark.asyncio
async def test_build_graph_without_tools_degrades(fake_model, monkeypatch):
    fake_model.responses = [AIMessage(content="hi")]
    from nyaya_chat import graph as graph_mod
    from nyaya_chat import tools_layer as tools_layer_mod
    from nyaya_chat.config import get_settings
    async def _empty(_=None):
        return []
    monkeypatch.setattr(tools_layer_mod, "load_tools", _empty)
    monkeypatch.setattr(graph_mod, "load_tools", _empty, raising=False)
    graph, tools = await graph_mod.build_graph(get_settings())
    assert tools == []
    assert "degraded_synthesis" in graph.nodes
    assert "supervisor" not in graph.nodes


@pytest.mark.asyncio
async def test_graph_supervisor_emits_tool_calls(fake_model, fake_tools):
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
    from nyaya_chat.config import get_settings
    from nyaya_chat.graph import build_graph
    from nyaya_chat.graph.supervisor import build_messages
    graph, tools = await build_graph(get_settings())
    msgs = build_messages("What is IPC 302?", [])
    result = await graph.ainvoke(
        {"messages": msgs, "rid": "t1"}, {"recursion_limit": 50},
    )
    out = result["messages"]
    # Final message should be the synthesis answer
    assert any(getattr(m, "content", "").startswith("Punishment for murder") for m in out)
    assert fake_model.calls  # the model was invoked


@pytest.mark.asyncio
async def test_graph_emits_semantic_events_end_to_end(fake_model, fake_tools, monkeypatch):
    """A full turn through the compiled graph emits the semantic SSE events —
    plan/status/tool_start/tool_result/token/citations — via the custom stream
    (captured by intercepting the events module's emit)."""
    captured: list[dict] = []
    import nyaya_chat.graph.events as events_mod
    monkeypatch.setattr(events_mod, "emit", lambda payload: captured.append(dict(payload)))

    fake_model._structured_result = ToolPlan(
        reasoning="Looking up IPC 302.",
        tool_calls=[ToolCallSpec(name="get_section", args={"act": "IPC", "section": "302"})],
    )
    fake_model.responses = [
        AIMessage(content="Punishment for murder is death or life [[act: IPC, ref: s. 302]]."),
    ]
    from nyaya_chat.config import get_settings
    from nyaya_chat.graph import build_graph
    from nyaya_chat.graph.supervisor import build_messages
    graph, _ = await build_graph(get_settings())
    msgs = build_messages("What is IPC 302?", [])
    await graph.ainvoke({"messages": msgs, "rid": "ev"}, {"recursion_limit": 50})

    types = [e["type"] for e in captured]
    assert "plan" in types          # supervisor's structured reasoning
    assert "status" in types        # searching/composing transitions
    assert "tool_start" in types    # the model called get_section
    assert "tool_result" in types   # the tool finished
    assert "token" in types         # synthesis streamed tokens
    assert "citations" in types     # parsed from the verified answer
    assert "correction" in types    # the disclaimer was appended post-stream
    # rid echoes on status events
    assert all(e.get("rid") == "ev" for e in captured if e["type"] == "status")


# ---------------------------------------------------------------------------
# Model factory (_make_model) tests
# ---------------------------------------------------------------------------


def test_make_model_passes_phase_params_to_fake(fake_model, settings):
    """Fakes honour the requested temperature/max_tokens (recorded via the
    ``nyaya_fake_model`` protocol) instead of silently ignoring them."""
    from nyaya_chat.graph import _make_model

    m = _make_model(
        settings, model_name="nvidia/fake", max_tokens=512, temperature=0.2,
    )
    assert m is fake_model
    assert fake_model.temperature == 0.2
    assert fake_model.max_completion_tokens == 512


def test_make_model_reuses_cached_base_when_config_matches(monkeypatch, settings):
    """A phase whose configuration equals the cached base model's reuses it
    (no duplicate API client); a phase with different settings constructs one."""
    from nyaya_chat import graph as graph_mod
    from nyaya_chat import llm as llm_mod

    constructed: list[dict] = []

    class FakeChatNVIDIA:
        def __init__(self, **kw):
            # Records only the clients _make_model itself builds; the cached
            # base below is a plain namespace, so it never lands here.
            constructed.append(kw)
            self.model = kw.get("model")
            self.temperature = kw.get("temperature")
            self.max_tokens = kw.get("max_completion_tokens")

    monkeypatch.setattr("langchain_nvidia_ai_endpoints.ChatNVIDIA", FakeChatNVIDIA)
    llm_mod.reset_model_cache()
    base = FakeChatNVIDIA(
        model=settings.llm_model,
        temperature=settings.llm_temperature,
        max_completion_tokens=settings.llm_max_tokens,
    )
    monkeypatch.setattr(llm_mod, "get_model", lambda _=None: base)
    monkeypatch.setattr(graph_mod, "get_model", lambda _=None: base)

    # Synthesis defaults match the base configuration exactly -> reuse.
    constructed.clear()  # forget the base's own construction
    reused = graph_mod._make_model(
        settings, model_name=settings.llm_model, max_tokens=settings.llm_max_tokens,
    )
    assert reused is base
    assert constructed == []  # no second client built

    # Supervisor's short token cap differs -> a new instance is built.
    supervisor = graph_mod._make_model(
        settings, model_name=settings.llm_model,
        max_tokens=settings.supervisor_max_tokens,
        temperature=settings.supervisor_temperature,
    )
    assert supervisor is not reused
    assert len(constructed) == 1
    assert constructed[0]["max_completion_tokens"] == settings.supervisor_max_tokens
    assert constructed[0]["temperature"] == settings.supervisor_temperature


def test_tool_call_key_normalises_args():
    from nyaya_chat.graph.tools_node import _tool_call_key
    k1 = _tool_call_key("get_section", {"section_number": 302, "act": "IPC"})
    k2 = _tool_call_key("get_section", {"act": "IPC", "section_number": "302"})
    assert k1 == k2


def test_tool_call_key_different_args_differ():
    from nyaya_chat.graph.tools_node import _tool_call_key
    k1 = _tool_call_key("get_section", {"section_number": "302"})
    k2 = _tool_call_key("get_section", {"section_number": "303"})
    assert k1 != k2


def test_tool_call_key_different_tools_differ():
    from nyaya_chat.graph.tools_node import _tool_call_key
    k1 = _tool_call_key("get_section", {"query": "302"})
    k2 = _tool_call_key("get_article", {"query": "302"})
    assert k1 != k2


# ---------------------------------------------------------------------------
# Supervisor node: structured plan, recovery, corrective retry
# ---------------------------------------------------------------------------


def _captured_events(monkeypatch) -> list[dict]:
    import nyaya_chat.graph.events as events_mod
    captured: list[dict] = []
    monkeypatch.setattr(events_mod, "emit", lambda payload: captured.append(dict(payload)))
    return captured


@pytest.mark.asyncio
async def test_supervisor_structured_plan_routes_to_tools(fake_model, settings, monkeypatch):
    """A structured ToolPlan becomes an AIMessage with tool_calls; plan text is
    streamed as a plan event; tool starts are emitted."""
    captured = _captured_events(monkeypatch)
    fake_model._structured_result = ToolPlan(
        reasoning="Need IPC 302.",
        tool_calls=[ToolCallSpec(name="get_section", args={"act": "IPC", "section": "302"})],
    )
    from nyaya_chat.graph.supervisor import make_supervisor_node
    node = make_supervisor_node(settings, fake_model, None)
    out = await node({"messages": [HumanMessage(content="q")], "rid": "s1"})
    # messages[0] is the streamed plan text; the tool_calls message is last.
    ai = out["messages"][-1]
    assert isinstance(ai, AIMessage) and ai.tool_calls
    assert ai.tool_calls[0]["name"] == "get_section"
    types = [e["type"] for e in captured]
    assert types[0] == "plan"
    assert "tool_start" in types


@pytest.mark.asyncio
async def test_supervisor_drops_non_allowlisted_calls(fake_model, settings, monkeypatch):
    """Tool calls outside TOOL_NAMES are dropped, not executed."""
    _captured_events(monkeypatch)
    fake_model._structured_result = ToolPlan(
        reasoning="",
        tool_calls=[
            ToolCallSpec(name="get_section", args={"act": "IPC", "section": "302"}),
            ToolCallSpec(name="shell_exec", args={"cmd": "rm -rf /"}),
        ],
    )
    from nyaya_chat.graph.supervisor import make_supervisor_node
    node = make_supervisor_node(settings, fake_model, None)
    out = await node({"messages": [HumanMessage(content="q")], "rid": "s2"})
    ai = out["messages"][0]
    assert [tc["name"] for tc in ai.tool_calls] == ["get_section"]


@pytest.mark.asyncio
async def test_supervisor_recovers_tool_calls_from_free_text(fake_model, settings, monkeypatch):
    """A bind_tools response with calls embedded in prose is recovered."""
    _captured_events(monkeypatch)
    fake_model.responses = [AIMessage(content=json.dumps(
        {"tool_calls": [{"name": "get_section", "args": {"act": "IPC", "section": "302"}}]},
    ))]
    from nyaya_chat.graph.supervisor import make_supervisor_node
    node = make_supervisor_node(settings, fake_model, None)
    out = await node({"messages": [HumanMessage(content="q")], "rid": "s3"})
    ai = out["messages"][0]
    assert isinstance(ai, AIMessage) and ai.tool_calls
    assert ai.tool_calls[0]["name"] == "get_section"


@pytest.mark.asyncio
async def test_supervisor_corrective_retry_on_zero_calls(fake_model, settings, monkeypatch):
    """Zero tool calls triggers ONE corrective retry with the same model."""
    _captured_events(monkeypatch)
    # First response: prose only. Second (post-nudge): structured plan? No —
    # the retry uses the SAME model object, so it returns the next scripted
    # response: bind_tools-style AIMessage with tool_calls.
    fake_model.responses = [
        AIMessage(content="The answer is, in my legal opinion, 42."),
        AIMessage(content="", tool_calls=[
            {"id": "tc_r", "name": "get_section", "args": {"act": "IPC", "section": "302"}},
        ]),
    ]
    from nyaya_chat.graph.supervisor import make_supervisor_node
    node = make_supervisor_node(settings, fake_model, None)
    out = await node({"messages": [HumanMessage(content="q")], "rid": "s4"})
    ai = out["messages"][0]
    assert isinstance(ai, AIMessage) and ai.tool_calls
    assert ai.tool_calls[0]["name"] == "get_section"
    assert len(fake_model.calls) == 2  # original + corrective retry


@pytest.mark.asyncio
async def test_supervisor_routes_raw_response_to_synthesis_after_failed_retry(
    fake_model, settings, monkeypatch,
):
    """When even the corrective retry yields no calls, the raw response is
    forwarded to synthesis instead of dropping the turn."""
    _captured_events(monkeypatch)
    fake_model.responses = [
        AIMessage(content="I cannot answer without tools."),
        AIMessage(content="Still no tools."),
    ]
    from nyaya_chat.graph.supervisor import make_supervisor_node
    node = make_supervisor_node(settings, fake_model, None)
    out = await node({"messages": [HumanMessage(content="q")], "rid": "s5"})
    assert len(out["messages"]) == 1
    assert out["messages"][0].content == "Still no tools."


def test_route_supervisor_tools_vs_synthesis():
    from nyaya_chat.graph.supervisor import route_supervisor
    with_tools = {"messages": [AIMessage(content="", tool_calls=[
        {"id": "t", "name": "get_section", "args": {}}])] }
    assert route_supervisor(with_tools) == "tools"
    without = {"messages": [AIMessage(content="plain")]}
    assert route_supervisor(without) == "synthesis"


def test_state_messages_appends_reflection_prompt_on_round_2(settings):
    """Round >= 1 (synthesis already ran) gets the retrieval-only suffix."""
    from langchain_core.messages import SystemMessage

    from nyaya_chat.graph.supervisor import _state_messages
    base = [SystemMessage(content="sys"), HumanMessage(content="q")]
    msgs = _state_messages({"messages": base, "round": 1})
    assert msgs[0].content.startswith("You are Nyaya")
    assert "REFLECTION ROUND" in msgs[0].content
    fresh = _state_messages({"messages": base})
    assert "REFLECTION ROUND" not in fresh[0].content


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
    from nyaya_chat.graph.tools_node import DedupToolNode

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
    from nyaya_chat.graph.tools_node import DedupToolNode

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
    from nyaya_chat.graph.tools_node import DedupToolNode

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
    from nyaya_chat.graph.tools_node import DedupToolNode

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
    from nyaya_chat.graph.synthesis import make_synthesis_node
    from nyaya_chat.llm import DISCLAIMER

    fake_model.responses = [AIMessage(content="Punishment for murder is death.")]
    node = make_synthesis_node(settings, fake_model, has_tools=False)
    out = await node({"messages": [HumanMessage(content="What is IPC 302?")]})
    content = out["messages"][0].content
    assert content.startswith("Punishment for murder is death.")
    assert content.endswith(f"\n\n*{DISCLAIMER}*")


@pytest.mark.asyncio
async def test_synthesis_node_does_not_duplicate_disclaimer(fake_model, settings):
    """When the model already emitted the disclaimer, it is left as-is."""
    from nyaya_chat.graph.synthesis import make_synthesis_node
    from nyaya_chat.llm import DISCLAIMER

    answer = "Answer.\n\nThis is not legal advice; verify citations before filing."
    fake_model.responses = [AIMessage(content=answer)]
    node = make_synthesis_node(settings, fake_model, has_tools=False)
    out = await node({"messages": [HumanMessage(content="q")]})
    assert out["messages"][0].content == answer
    assert out["messages"][0].content.count(DISCLAIMER) == 1


@pytest.mark.asyncio
async def test_synthesis_node_verifies_and_disclaims_in_one_pass(fake_model, settings, monkeypatch):
    """Verification (strip ungrounded citations) and the disclaimer append both
    happen in the synthesis node, so the returned message is final."""
    from nyaya_chat.graph.synthesis import make_synthesis_node
    from nyaya_chat.llm import DISCLAIMER

    _captured_events(monkeypatch)
    fake_model.responses = [AIMessage(
        content="Grounded [[act: IPC, ref: s. 302]] and ungrounded [[act: GhostAct, ref: 1]]."
    )]
    node = make_synthesis_node(settings, fake_model, has_tools=True)
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


@pytest.mark.asyncio
async def test_synthesis_node_wraps_tool_results_in_corpus_tags(fake_model, settings):
    """Tool results reach the model wrapped in <corpus_text> delimiters."""
    from nyaya_chat.graph.synthesis import make_synthesis_node

    fake_model.responses = [AIMessage(content="Answer.")]
    node = make_synthesis_node(settings, fake_model, has_tools=True)
    state = {
        "messages": [
            HumanMessage(content="q"),
            AIMessage(content="", tool_calls=[
                {"id": "tc1", "name": "get_section", "args": {"act": "IPC", "section_number": "302"}},
            ]),
            ToolMessage(content="raw tool text", tool_call_id="tc1", name="get_section"),
        ],
    }
    await node(state)
    sent = fake_model.calls[-1]
    tool_msgs = [m for m in sent if getattr(m, "name", None) == "get_section"]
    assert "<corpus_text>" in tool_msgs[0].content
    assert "raw tool text" in tool_msgs[0].content


@pytest.mark.asyncio
async def test_synthesis_node_no_tools_path_skips_corpus_wrap(fake_model, settings):
    """The degraded has_tools=False path streams the answer without wrapping."""
    from nyaya_chat.graph.synthesis import make_synthesis_node

    fake_model.responses = [AIMessage(content="Answer.")]
    node = make_synthesis_node(settings, fake_model, has_tools=False)
    await node({"messages": [HumanMessage(content="q")]})
    sent = fake_model.calls[-1]
    # The system prompt legitimately mentions <corpus_text> in its injection
    # defense instructions — no ToolMessage exists here, so nothing is wrapped.
    assert "<corpus_text>" not in "".join(
        m.content if isinstance(m.content, str) else ""
        for m in sent if not isinstance(m, SystemMessage)
    )


@pytest.mark.asyncio
async def test_synthesis_node_increments_round(fake_model, settings):
    from nyaya_chat.graph.synthesis import make_synthesis_node

    fake_model.responses = [AIMessage(content="Answer [[act: IPC, ref: s. 302]].")]
    node = make_synthesis_node(settings, fake_model, has_tools=False)
    out = await node({"messages": [HumanMessage(content="q")], "round": 1})
    assert out["round"] == 2


def _semantic_query_result(n_hits: int, snippet_len: int = 2000) -> str:
    return json.dumps({
        "query": "punishment for murder",
        "total": n_hits * 5, "returned": n_hits, "offset": 0, "limit": n_hits,
        "source": "nyaya", "as_of": "2024-01-01", "fallback_reason": None,
        "results": [
            {"act": f"Act{i}", "ref": f"s. {100 + i}", "title": f"T{i}",
             "snippet": f"HIT{i}-" + "z" * snippet_len,
             "rank": 1.0 - i * 0.01, "citation": None, "kind": "section"}
            for i in range(n_hits)
        ],
    })


@pytest.mark.asyncio
async def test_synthesis_node_prunes_list_type_tool_results(fake_model, settings):
    """A multi-hit semantic_query result is bounded in what reaches the model:
    the top hit's snippet survives in full, later hits are condensed to
    identification fields + a 300-char snippet, and envelope metadata is gone."""
    from nyaya_chat.graph.synthesis import make_synthesis_node

    fake_model.responses = [AIMessage(content="Answer [[act: Act0, ref: s. 100]].")]
    node = make_synthesis_node(settings, fake_model, has_tools=True)
    payload = _semantic_query_result(8)
    state = {
        "messages": [
            HumanMessage(content="What is the punishment for murder?"),
            AIMessage(content="", tool_calls=[
                {"id": "tc1", "name": "semantic_query", "args": {"query": "punishment for murder"}},
            ]),
            ToolMessage(
                content=payload, tool_call_id="tc1", name="semantic_query",
            ),
        ],
    }
    await node(state)

    sent = fake_model.calls[-1]
    tool_msgs = [m for m in sent if getattr(m, "name", None) == "semantic_query"]
    assert len(tool_msgs) == 1
    sent_content = tool_msgs[0].content
    # The ~12K-token worst case is cut down to a third of the original payload.
    assert len(sent_content) < len(payload) / 3
    # Top hit verbatim; tail hits condensed (snippet capped) but identifiable.
    assert "HIT0-" + "z" * 2000 in sent_content
    assert "HIT3-" + "z" * 2000 not in sent_content
    assert '"ref": "s. 103"' in sent_content  # tail hit still citable/fetchable
    assert '"as_of"' not in sent_content  # envelope metadata pruned


@pytest.mark.asyncio
async def test_synthesis_node_prunes_only_list_type_results(fake_model, settings):
    """Full text of a single-document tool (get_section) passes to the model
    UNPRUNED — pruning never touches single-document results."""
    from nyaya_chat.graph.synthesis import make_synthesis_node

    full_text = "SECTION-TEXT:" + "F" * 3000
    fake_model.responses = [AIMessage(content="Answer [[act: IPC, ref: s. 302]].")]
    node = make_synthesis_node(settings, fake_model, has_tools=True)
    payload = json.dumps({"act": "IPC", "ref": "s. 302", "text": full_text})
    state = {
        "messages": [
            HumanMessage(content="What is IPC 302?"),
            AIMessage(content="", tool_calls=[
                {"id": "tc1", "name": "get_section", "args": {"act": "IPC", "section_number": "302"}},
            ]),
            ToolMessage(
                content=payload,
                tool_call_id="tc1", name="get_section",
            ),
        ],
    }
    await node(state)

    sent = fake_model.calls[-1]
    tool_msgs = [m for m in sent if getattr(m, "name", None) == "get_section"]
    assert len(tool_msgs) == 1
    assert full_text in tool_msgs[0].content  # verbatim, unpruned


# ---------------------------------------------------------------------------
# Reflection routing
# ---------------------------------------------------------------------------


def _synthesis_state(answer: str, round_: int) -> dict:
    return {
        "messages": [
            HumanMessage(content="q"),
            AIMessage(content="", tool_calls=[{"id": "t", "name": "get_section", "args": {}}]),
            AIMessage(content=answer),
        ],
        "round": round_,
    }


def test_route_synthesis_ends_with_citations(settings):
    from nyaya_chat.graph.synthesis import route_synthesis
    assert route_synthesis(_synthesis_state(
        "Answer [[act: IPC, ref: s. 302]].", 1), settings) == "end"


def test_route_synthesis_loops_back_without_citations(settings):
    from nyaya_chat.graph.synthesis import route_synthesis
    assert route_synthesis(_synthesis_state("Answer without citations.", 1), settings) == "supervisor"


def test_route_synthesis_loops_back_on_refusal(settings):
    from nyaya_chat.graph.synthesis import route_synthesis
    assert route_synthesis(_synthesis_state(
        "I could not find a basis in the corpus [[act: IPC, ref: s. 302]].", 1),
        settings) == "supervisor"


def test_route_synthesis_respects_max_rounds(settings):
    from nyaya_chat.graph.synthesis import route_synthesis
    state = _synthesis_state("Answer without citations.", settings.max_reflection_rounds)
    assert route_synthesis(state, settings) == "end"


def test_route_synthesis_ends_when_no_tools_were_called(settings):
    from nyaya_chat.graph.synthesis import route_synthesis
    state = {
        "messages": [HumanMessage(content="q"), AIMessage(content="No citations.")],
        "round": 1,
    }
    assert route_synthesis(state, settings) == "end"
