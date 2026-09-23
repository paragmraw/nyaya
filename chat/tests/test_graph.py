"""Tests for nyaya_chat.graph — build, message assembly, nodes, routing.

Phase 1 pipeline collapse: the two-model supervisor+synthesis pipeline is
replaced by ONE streaming, tool-bound agent node in a 2-node graph
(``START → agent → (tools → agent | END)``). The agent node buffers its
first leg (tool-call decision); post-tools legs stream answer tokens live.
"""

from __future__ import annotations

import json
import time

import pytest
from conftest import FakeChatModel
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage


def test_build_messages_assembles_system_history_user():
    from nyaya_chat.graph.agent import build_messages
    msgs = build_messages("hello", [
        {"role": "user", "content": "q1"},
        {"role": "assistant", "content": "a1"},
    ])
    assert len(msgs) == 4  # system + 2 history + new user
    assert msgs[0].content.startswith("You are Nyaya")
    assert msgs[1].content == "q1"
    assert msgs[3].content == "hello"


def test_build_messages_empty_history():
    from nyaya_chat.graph.agent import build_messages
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


def test_system_prompt_forbids_process_narration():
    """The degraded-path prompt must tell the model to keep its planning out of
    the answer body — the answer shows the final answer only."""
    from nyaya_chat.llm import SYSTEM_PROMPT
    assert "final answer ONLY" in SYSTEM_PROMPT
    assert "thinking process" in SYSTEM_PROMPT


def test_agent_prompt_lists_allowlisted_tools():
    """The agent prompt's tool list is rendered from tools_layer.spec —
    a drift between the prompt and the allowlist is impossible by construction."""
    from nyaya_chat.llm import AGENT_PROMPT
    from nyaya_chat.tools_layer.spec import TOOL_SPECS
    for spec in TOOL_SPECS:
        assert f"- {spec.name}:" in AGENT_PROMPT
    assert "parallel" in AGENT_PROMPT.lower()


def test_agent_prompt_has_sequential_rules():
    """AGENT_PROMPT should have rules numbered with no duplicates or gaps."""
    import re

    from nyaya_chat.llm import AGENT_PROMPT
    numbers = [int(m) for m in re.findall(r"(\d+)\.\s", AGENT_PROMPT)]
    assert numbers == sorted(numbers), f"Rules not in order: {numbers}"
    assert len(numbers) == len(set(numbers)), f"Duplicate rule numbers: {numbers}"


def test_agent_prompt_keeps_must_call_tools_rule():
    """Merging the prompts must not weaken the MUST-call-tools rule — the
    guard against the agent answering from memory (kills has_tool_calls)."""
    from nyaya_chat.llm import AGENT_PROMPT
    assert "MUST call at least one tool" in AGENT_PROMPT


def test_agent_prompt_demands_parallel_comparisons():
    """Known eval FAIL target: cross-act comparisons must fetch BOTH acts in
    one response so the comparison answer is grounded on the first pass."""
    from nyaya_chat.llm import AGENT_PROMPT
    assert "parallel lookups for BOTH acts" in AGENT_PROMPT


def test_agent_prompt_keeps_followup_different_query_rule():
    from nyaya_chat.llm import AGENT_PROMPT
    assert "DIFFERENT query" in AGENT_PROMPT


def test_reflection_prompt_constrains_to_semantic_query():
    from nyaya_chat.prompts import REFLECTION_PROMPT
    assert "semantic_query" in REFLECTION_PROMPT
    assert "DIFFERENT" in REFLECTION_PROMPT


def test_state_messages_appends_reflection_prompt_on_round_2(settings):
    """Round >= 1 (an answer leg already ran) gets the retrieval-only suffix."""
    from langchain_core.messages import SystemMessage

    from nyaya_chat.graph.agent import _state_messages
    base = [SystemMessage(content="sys"), HumanMessage(content="q")]
    msgs = _state_messages({"messages": base, "round": 1})
    assert msgs[0].content.startswith("You are Nyaya")
    assert "REFLECTION ROUND" in msgs[0].content
    fresh = _state_messages({"messages": base})
    assert "REFLECTION ROUND" not in fresh[0].content


# ---------------------------------------------------------------------------
# Graph assembly
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_build_graph_with_tools(fake_model, fake_tools):
    """The tool graph is agent+tools — the supervisor and synthesis nodes are
    gone (pipeline collapse)."""
    fake_model.responses = [
        AIMessage(content="", tool_calls=[
            {"id": "tc1", "name": "get_section", "args": {"act": "IPC", "section_number": "302"}},
        ]),
        AIMessage(content="Punishment for murder [[act: IPC, ref: s. 302]]."),
    ]
    from nyaya_chat import graph as graph_mod
    from nyaya_chat.config import get_settings
    graph, tools = await graph_mod.build_graph(get_settings())
    assert len(tools) == 2
    assert "agent" in graph.nodes
    assert "tools" in graph.nodes
    assert "supervisor" not in graph.nodes
    assert "synthesis" not in graph.nodes
    assert "degraded_synthesis" not in graph.nodes


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
    assert "agent" not in graph.nodes
    assert "supervisor" not in graph.nodes


@pytest.mark.asyncio
async def test_graph_agent_full_turn(fake_model, fake_tools):
    """Full turn: agent leg-1 emits tool calls, tools run, answer leg streams
    the grounded answer, and the graph ends (cited answer, round budget)."""
    fake_model.responses = [
        AIMessage(content="", tool_calls=[
            {"id": "tc1", "name": "get_section", "args": {"act": "IPC", "section_number": "302"}},
        ]),
        AIMessage(content="Punishment for murder is death or life [[act: IPC, ref: s. 302]]."),
    ]
    from nyaya_chat.config import get_settings
    from nyaya_chat.graph import build_graph
    from nyaya_chat.graph.agent import build_messages
    graph, _ = await build_graph(get_settings())
    msgs = build_messages("What is IPC 302?", [])
    result = await graph.ainvoke(
        {"messages": msgs, "rid": "t1"}, {"recursion_limit": 50},
    )
    out = result["messages"]
    # Final message should be the agent's answer
    assert any(getattr(m, "content", "").startswith("Punishment for murder") for m in out)
    assert fake_model.calls  # the model was invoked


@pytest.mark.asyncio
async def test_graph_reflection_round_reruns_agent(fake_model, fake_tools, monkeypatch):
    """An uncited answer after tools routes the agent back in for one gated
    reflection round; the cited reflection answer ends the turn."""
    captured = _captured_events(monkeypatch)
    fake_model.responses = [
        AIMessage(content="", tool_calls=[
            {"id": "tc1", "name": "get_section", "args": {"act": "IPC", "section_number": "302"}},
        ]),
        AIMessage(content="An answer without any citations."),
        AIMessage(content="Better answer [[act: IPC, ref: s. 302]]."),
    ]
    from nyaya_chat.config import get_settings
    from nyaya_chat.graph import build_graph
    from nyaya_chat.graph.agent import build_messages
    graph, _ = await build_graph(get_settings())
    result = await graph.ainvoke(
        {"messages": build_messages("What is IPC 302?", []), "rid": "t2"},
        {"recursion_limit": 50},
    )
    assert len(fake_model.calls) == 3  # leg-1 + answer leg + reflection leg
    assert any(
        getattr(m, "content", "").startswith("Better answer [[act: IPC, ref: s. 302]].")
        for m in result["messages"]
    )
    # The reflection re-entry streams its answer (citations event emitted).
    assert any(e["type"] == "citations" for e in captured)


@pytest.mark.asyncio
async def test_graph_emits_semantic_events_end_to_end(fake_model, fake_tools, monkeypatch):
    """A full turn through the compiled graph emits the semantic SSE events —
    plan/status/tool_start/tool_result/token/citations — via the custom stream
    (captured by intercepting the events module's emit)."""
    captured: list[dict] = []
    import nyaya_chat.graph.events as events_mod
    monkeypatch.setattr(events_mod, "emit", lambda payload: captured.append(dict(payload)))

    fake_model.responses = [
        AIMessage(content="Looking up IPC 302.", tool_calls=[
            {"id": "tc1", "name": "get_section", "args": {"act": "IPC", "section_number": "302"}},
        ]),
        AIMessage(content="Punishment for murder is death or life [[act: IPC, ref: s. 302]]."),
    ]
    from nyaya_chat.config import get_settings
    from nyaya_chat.graph import build_graph
    from nyaya_chat.graph.agent import build_messages
    graph, _ = await build_graph(get_settings())
    msgs = build_messages("What is IPC 302?", [])
    await graph.ainvoke({"messages": msgs, "rid": "ev"}, {"recursion_limit": 50})

    types = [e["type"] for e in captured]
    assert "plan" in types          # leg-1 buffered preamble text
    assert "status" in types        # analyzing/searching/composing transitions
    assert "tool_start" in types    # the agent called get_section
    assert "tool_result" in types   # the tool finished
    assert "token" in types         # the answer leg streamed tokens
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

    # Degraded-mode defaults match the base configuration exactly -> reuse.
    constructed.clear()  # forget the base's own construction
    reused = graph_mod._make_model(
        settings, model_name=settings.llm_model, max_tokens=settings.llm_max_tokens,
    )
    assert reused is base
    assert constructed == []  # no second client built

    # The agent's larger answer budget differs -> a new instance is built.
    agent = graph_mod._make_model(
        settings, model_name=settings.synthesis_model,
        max_tokens=settings.synthesis_max_tokens,
    )
    assert agent is not reused
    assert len(constructed) == 1
    assert constructed[0]["max_completion_tokens"] == settings.synthesis_max_tokens


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
# Agent node: leg-1 buffering, tool-call legs, answer legs
# ---------------------------------------------------------------------------


def _captured_events(monkeypatch) -> list[dict]:
    import nyaya_chat.graph.events as events_mod
    captured: list[dict] = []
    monkeypatch.setattr(events_mod, "emit", lambda payload: captured.append(dict(payload)))
    return captured


def _tool_leg_state() -> dict:
    """Post-tools state: the last message is a ToolMessage (live-answer leg)."""
    return {
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
        "rid": "a-leg",
    }


@pytest.mark.asyncio
async def test_agent_leg1_tool_calls_buffer_to_plan_and_tool_start(
    fake_model, settings, monkeypatch,
):
    """Leg-1 with tool calls: the buffered preamble text is streamed as a
    ``plan`` event (never as answer tokens), tool starts are emitted, the
    returned message carries the tool_calls, and the round counter is
    untouched (it counts answer legs)."""
    captured = _captured_events(monkeypatch)
    fake_model.responses = [AIMessage(
        content="Looking up IPC 302.",
        tool_calls=[{"id": "tc1", "name": "get_section", "args": {"act": "IPC", "section_number": "302"}}],
    )]
    from nyaya_chat.graph.agent import make_agent_node
    node = make_agent_node(settings, fake_model, has_tools=True)
    out = await node({"messages": [HumanMessage(content="q")], "rid": "a1"})
    ai = out["messages"][0]
    assert isinstance(ai, AIMessage) and ai.tool_calls
    assert ai.tool_calls[0]["name"] == "get_section"
    assert ai.content == "Looking up IPC 302."
    types = [e["type"] for e in captured]
    assert types[0] == "status" and captured[0]["msg"] == "analyzing"
    assert "plan" in types
    assert "tool_start" in types
    assert "token" not in types  # leg-1 text is buffered, not streamed as answer
    assert "round" not in out  # tool-call leg does not advance the round


@pytest.mark.asyncio
async def test_agent_drops_non_allowlisted_calls(fake_model, settings, monkeypatch):
    """Tool calls outside TOOL_NAMES are dropped, not executed."""
    _captured_events(monkeypatch)
    fake_model.responses = [AIMessage(content="", tool_calls=[
        {"id": "tc1", "name": "get_section", "args": {"act": "IPC", "section_number": "302"}},
        {"id": "tc2", "name": "shell_exec", "args": {"cmd": "rm -rf /"}},
    ])]
    from nyaya_chat.graph.agent import make_agent_node
    node = make_agent_node(settings, fake_model, has_tools=True)
    out = await node({"messages": [HumanMessage(content="q")], "rid": "a2"})
    ai = out["messages"][0]
    assert isinstance(ai, AIMessage)
    assert [tc["name"] for tc in ai.tool_calls] == ["get_section"]


@pytest.mark.asyncio
async def test_agent_leg1_direct_answer_replays_buffered_tokens(
    fake_model, settings, monkeypatch,
):
    """Leg-1 with NO tool calls (the model answered directly): the buffered
    stream is replayed as token events after a ``composing`` status — the
    client sees a normal answer stream — and the turn can end."""
    captured = _captured_events(monkeypatch)
    fake_model.responses = [AIMessage(content="Answer without tools.")]
    from nyaya_chat.graph.agent import make_agent_node
    node = make_agent_node(settings, fake_model, has_tools=True)
    out = await node({"messages": [HumanMessage(content="q")], "rid": "a3"})
    tokens = "".join(e["content"] for e in captured if e["type"] == "token")
    assert tokens == "Answer without tools."
    assert "tool_start" not in [e["type"] for e in captured]
    composing = [e for e in captured if e["type"] == "status" and e.get("msg") == "composing"]
    assert composing  # the client's phase moved to composing before the replay
    assert out["round"] == 1  # answer leg advanced the round


@pytest.mark.asyncio
async def test_agent_answer_leg_streams_and_verifies(fake_model, settings, monkeypatch):
    """Post-tools leg: tokens stream live; verification (strip ungrounded
    citations) and the disclaimer append both happen in the agent node, so the
    returned message is final."""
    from nyaya_chat.llm import DISCLAIMER

    captured = _captured_events(monkeypatch)
    fake_model.responses = [AIMessage(
        content="Grounded [[act: IPC, ref: s. 302]] and ungrounded [[act: GhostAct, ref: 1]]."
    )]
    from nyaya_chat.graph.agent import make_agent_node
    node = make_agent_node(settings, fake_model, has_tools=True)
    out = await node(_tool_leg_state())
    content = out["messages"][0].content
    assert "[[act: IPC, ref: s. 302]]" in content
    assert "GhostAct" not in content
    assert content.endswith(f"\n\n*{DISCLAIMER}*")
    assert out["round"] == 1
    types = [e["type"] for e in captured]
    assert types[0] == "status" and captured[0]["msg"] == "composing"
    assert "token" in types
    assert "citations" in types
    assert "correction" in types  # verified text differs from streamed text


# ---------------------------------------------------------------------------
# Tool-result handling (pruning, corpus wrapping) — moved from synthesis
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_node_wraps_tool_results_in_corpus_tags(fake_model, settings):
    """Tool results reach the model wrapped in <corpus_text> delimiters."""
    from nyaya_chat.graph.agent import make_agent_node

    fake_model.responses = [AIMessage(content="Answer.")]
    node = make_agent_node(settings, fake_model, has_tools=True)
    await node(_tool_leg_state())
    sent = fake_model.calls[-1]
    tool_msgs = [m for m in sent if getattr(m, "name", None) == "get_section"]
    assert "<corpus_text>" in tool_msgs[0].content
    assert 'kind": "section"' in tool_msgs[0].content or "..." in tool_msgs[0].content


@pytest.mark.asyncio
async def test_agent_node_no_tools_path_skips_corpus_wrap(fake_model, settings):
    """The degraded has_tools=False path streams the answer without wrapping."""
    from nyaya_chat.graph.agent import make_agent_node

    fake_model.responses = [AIMessage(content="Answer.")]
    node = make_agent_node(settings, fake_model, has_tools=False)
    await node({"messages": [HumanMessage(content="q")]})
    sent = fake_model.calls[-1]
    # The system prompt legitimately mentions <corpus_text> in its injection
    # defense instructions — no ToolMessage exists here, so nothing is wrapped.
    assert "<corpus_text>" not in "".join(
        m.content if isinstance(m.content, str) else ""
        for m in sent if not isinstance(m, SystemMessage)
    )


@pytest.mark.asyncio
async def test_agent_node_increments_round(fake_model, settings):
    from nyaya_chat.graph.agent import make_agent_node

    fake_model.responses = [AIMessage(content="Answer [[act: IPC, ref: s. 302]].")]
    node = make_agent_node(settings, fake_model, has_tools=False)
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
async def test_agent_node_prunes_list_type_tool_results(fake_model, settings):
    """A multi-hit semantic_query result is bounded in what reaches the model:
    the top hit's snippet survives in full, later hits are condensed to
    identification fields + a 300-char snippet, and envelope metadata is gone."""
    from nyaya_chat.graph.agent import make_agent_node

    fake_model.responses = [AIMessage(content="Answer [[act: Act0, ref: s. 100]].")]
    node = make_agent_node(settings, fake_model, has_tools=True)
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
async def test_agent_node_prunes_only_list_type_results(fake_model, settings):
    """Full text of a single-document tool (get_section) passes to the model
    UNPRUNED — pruning never touches single-document results."""
    from nyaya_chat.graph.agent import make_agent_node

    full_text = "SECTION-TEXT:" + "F" * 3000
    fake_model.responses = [AIMessage(content="Answer [[act: IPC, ref: s. 302]].")]
    node = make_agent_node(settings, fake_model, has_tools=True)
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
# Routing: route_agent (reflection gate)
# ---------------------------------------------------------------------------


def _answer_state(answer: str, round_: int, *, deadline: float | None = None) -> dict:
    return {
        "messages": [
            HumanMessage(content="q"),
            AIMessage(content="", tool_calls=[{"id": "t", "name": "get_section", "args": {}}]),
            AIMessage(content=answer),
        ],
        "round": round_,
        "deadline": deadline,
    }


def test_route_agent_tools_when_last_has_tool_calls(settings):
    from nyaya_chat.graph.agent import route_agent
    with_tools = {"messages": [AIMessage(content="", tool_calls=[
        {"id": "t", "name": "get_section", "args": {}}])] }
    assert route_agent(with_tools, settings) == "tools"


def test_route_agent_reflects_on_uncited_answer_within_budget(settings):
    from nyaya_chat.graph.agent import route_agent
    # Round 1 <= max_reflection_rounds(1): one gated reflection is allowed.
    assert route_agent(_answer_state("Answer without citations.", 1), settings) == "agent"


def test_route_agent_ends_with_citations(settings):
    from nyaya_chat.graph.agent import route_agent
    assert route_agent(_answer_state(
        "Answer [[act: IPC, ref: s. 302]].", 1), settings) == "end"


def test_route_agent_respects_max_rounds(settings):
    """Round 2 (> max_reflection_rounds=1) never reflects again — total answer
    legs per turn = max + 1."""
    from nyaya_chat.graph.agent import route_agent
    state = _answer_state("Answer without citations.", settings.max_reflection_rounds + 1)
    assert route_agent(state, settings) == "end"


def test_route_agent_ends_when_no_tools_were_called(settings):
    from nyaya_chat.graph.agent import route_agent
    state = {
        "messages": [HumanMessage(content="q"), AIMessage(content="No citations.")],
        "round": 1,
    }
    assert route_agent(state, settings) == "end"


def test_route_agent_deadline_gate_blocks_late_reflection(settings):
    """A reflection round may only start when REFLECTION_DEADLINE_S remains —
    a reflection that could not finish inside the budget is worse than an
    uncited answer."""
    from nyaya_chat.graph.agent import route_agent
    state = _answer_state(
        "Answer without citations.", 1,
        deadline=time.monotonic() + settings.reflection_deadline_s - 1.0,
    )
    assert route_agent(state, settings) == "end"
    state = _answer_state(
        "Answer without citations.", 1,
        deadline=time.monotonic() + settings.reflection_deadline_s + 10.0,
    )
    assert route_agent(state, settings) == "agent"


# ---------------------------------------------------------------------------
# Recovery hardening: deadline, empty stream, DB-error short-circuit
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_deadline_exceeded_raises_turn_error(fake_model, settings):
    """A blown wall-clock budget fails the turn with a 'timeout' TurnError
    before any model call is made."""
    from nyaya_chat.errors import TurnError
    from nyaya_chat.graph.agent import make_agent_node
    node = make_agent_node(settings, fake_model, has_tools=True)
    with pytest.raises(TurnError) as exc_info:
        await node({
            "messages": [HumanMessage(content="q")],
            "rid": "a4",
            "deadline": time.monotonic() - 1.0,  # already spent
        })
    assert exc_info.value.code == "timeout"
    assert fake_model.calls == []  # no model call was attempted


class _EmptyStreamModel:
    """A model stand-in whose astream yields NOTHING (dead stream)."""

    def __init__(self):
        self.calls: list = []

    async def astream(self, messages, **kw):
        self.calls.append(messages)
        return
        yield  # pragma: no cover - makes this an async generator


@pytest.mark.asyncio
async def test_agent_empty_stream_raises_turn_error(settings):
    """An empty agent stream is a failed turn, not a silent success."""
    from nyaya_chat.errors import TurnError
    from nyaya_chat.graph.agent import make_agent_node
    node = make_agent_node(settings, _EmptyStreamModel(), has_tools=False)
    with pytest.raises(TurnError) as exc_info:
        await node({"messages": [HumanMessage(content="q")], "rid": "a5"})
    assert exc_info.value.code == "empty_response"


def _db_error_content() -> str:
    return json.dumps({"error": {
        "code": "database_unavailable",
        "message": "connection refused",
        "kind": "db",
        "hint": "retry later",
    }})


@pytest.mark.asyncio
async def test_agent_all_db_error_results_short_circuits(settings):
    """When EVERY tool result is native error JSON, the agent fails fast with
    'retrieval_unavailable' instead of synthesizing from nothing."""
    from nyaya_chat.errors import TurnError
    from nyaya_chat.graph.agent import make_agent_node
    model = FakeChatModel(responses=[AIMessage(content="made up answer")])
    node = make_agent_node(settings, model, has_tools=True)
    state = {
        "messages": [
            HumanMessage(content="q"),
            AIMessage(content="", tool_calls=[
                {"id": "tc1", "name": "get_section", "args": {}},
                {"id": "tc2", "name": "semantic_query", "args": {}},
            ]),
            ToolMessage(content=_db_error_content(), tool_call_id="tc1", name="get_section"),
            ToolMessage(content=_db_error_content(), tool_call_id="tc2", name="semantic_query"),
        ],
        "rid": "a6",
    }
    with pytest.raises(TurnError) as exc_info:
        await node(state)
    assert exc_info.value.code == "retrieval_unavailable"
    assert model.calls == []  # the model was never consulted


@pytest.mark.asyncio
async def test_agent_mixed_results_do_not_short_circuit(fake_model, settings):
    """One real result alongside an error JSON still answers — the
    short-circuit fires only when EVERY tool errored."""
    fake_model.responses = [AIMessage(content="Answer [[act: IPC, ref: s. 302]].")]
    from nyaya_chat.graph.agent import make_agent_node
    node = make_agent_node(settings, fake_model, has_tools=True)
    state = {
        "messages": [
            HumanMessage(content="q"),
            AIMessage(content="", tool_calls=[
                {"id": "tc1", "name": "get_section", "args": {"act": "IPC", "section_number": "302"}},
                {"id": "tc2", "name": "semantic_query", "args": {}},
            ]),
            ToolMessage(
                content='{"act": "IPC", "ref": "s. 302", "text": "real text"}',
                tool_call_id="tc1", name="get_section",
            ),
            ToolMessage(content=_db_error_content(), tool_call_id="tc2", name="semantic_query"),
        ],
        "rid": "a7",
    }
    out = await node(state)  # no exception
    assert out["messages"][0].content.startswith("Answer")


def test_agent_not_found_error_json_is_not_a_db_error():
    """A ``not_found`` error JSON (e.g. get_section on a nonexistent section)
    is a legitimate result the agent must turn into a refusal — it must NOT
    count as a database error for the short-circuit."""
    from nyaya_chat.graph.agent import _is_db_error_json
    not_found = json.dumps({"error": {
        "code": "not_found",
        "message": "no section 99999 in IPC",
        "kind": "not_found",
        "hint": "check the section number",
    }})
    assert not _is_db_error_json(not_found)
    assert _is_db_error_json(_db_error_content())  # database_unavailable still matches
    assert _is_db_error_json(json.dumps({"error": {"code": "embedding_unavailable"}}))


@pytest.mark.asyncio
async def test_agent_not_found_only_results_still_answer(fake_model, settings):
    """All tool results being not_found errors still reaches the agent model
    (regression: the short-circuit used to hard-fail such turns with
    retrieval_unavailable, killing valid refusal answers)."""
    fake_model.responses = [AIMessage(
        content="I could not find IPC section 99999; no such provision exists.",
    )]
    from nyaya_chat.graph.agent import make_agent_node
    node = make_agent_node(settings, fake_model, has_tools=True)
    state = {
        "messages": [
            HumanMessage(content="What does IPC section 99999 say?"),
            AIMessage(content="", tool_calls=[
                {"id": "tc1", "name": "get_section", "args": {"act": "IPC", "section_number": "99999"}},
            ]),
            ToolMessage(
                content=json.dumps({"error": {
                    "code": "not_found",
                    "message": "no section 99999 in IPC",
                    "kind": "not_found",
                    "hint": "check the section number",
                }}),
                tool_call_id="tc1", name="get_section",
            ),
        ],
        "rid": "a8",
    }
    out = await node(state)  # no exception — the agent composes the refusal
    assert fake_model.calls  # the model was consulted
    assert "99999" in out["messages"][0].content


# ---------------------------------------------------------------------------
# Reasoning-leak guard + keep-better reflection (moved to agent.py)
# ---------------------------------------------------------------------------


class _FakeStreamingModel:
    """astream fake that yields scripted AIMessageChunks in order.

    The agent node only calls ``astream`` (via ``astream_with_retry``);
    no other model methods are needed for the leak/keep-better tests.
    """

    nyaya_fake_model = True

    def __init__(self, chunks: list):
        self._chunks = chunks
        self.calls: list = []

    def with_generation_params(self, *, temperature=None, max_tokens=None):
        return self

    async def astream(self, messages, **kw):
        self.calls.append(messages)
        for c in self._chunks:
            yield c


_LEAK_REASONING = (
    "Here's a thinking process:\n\n1.  **Analyze User Input:** The user is asking"
    " for the difference between murder and culpable homicide under Indian law.\n"
    "2.  **Recall the corpus:** IPC sections 299-302 define the two offences; "
    "culpable homicide is the genus and murder the species, distinguished by "
    "intention, knowledge, and the degree of likelihood of death.\n"
)  # a single >200-char deliberation passage


def _leak_state() -> dict:
    return _tool_leg_state()


@pytest.mark.asyncio
async def test_agent_hides_reasoning_duplicate_from_answer_stream(settings, monkeypatch):
    """The NVIDIA API re-sent the entire accumulated reasoning_content as one
    giant content chunk (observed live: an 8.6K-char chunk byte-identical to
    the reasoning buffer). That deliberation must reach the reasoning trace,
    never the answer tokens or the final message."""
    from langchain_core.messages import AIMessageChunk

    from nyaya_chat.graph.agent import make_agent_node

    captured = _captured_events(monkeypatch)
    answer = "Murder requires intention [[act: IPC, ref: s. 302]]."
    model = _FakeStreamingModel([
        AIMessageChunk(content="", additional_kwargs={"reasoning_content": _LEAK_REASONING}),
        AIMessageChunk(content=_LEAK_REASONING),  # the flush: byte-identical re-send
        AIMessageChunk(content=answer),
    ])
    node = make_agent_node(settings, model, has_tools=True)
    out = await node(_leak_state())

    tokens = "".join(e["content"] for e in captured if e["type"] == "token")
    assert tokens == answer  # the leak never reaches the answer body
    reasoning_emitted = "".join(e["content"] for e in captured if e["type"] == "reasoning")
    assert _LEAK_REASONING in reasoning_emitted  # it lands in the trace instead
    final = out["messages"][0].content
    assert "thinking process" not in final
    assert "[[act: IPC, ref: s. 302]]" in final


@pytest.mark.asyncio
async def test_agent_dedupes_reasoning_buffer_flush(settings, monkeypatch):
    """The API re-sends the accumulated reasoning buffer as one giant delta
    (observed live: 4,021- and 7,920-char reasoning deltas byte-identical to
    the buffer). The trace must not duplicate it, and the content stream of
    the SAME chunk must still be processed."""
    from langchain_core.messages import AIMessageChunk

    from nyaya_chat.graph.agent import make_agent_node

    captured = _captured_events(monkeypatch)
    answer = "Murder requires intention [[act: IPC, ref: s. 302]]."
    model = _FakeStreamingModel([
        AIMessageChunk(content="", additional_kwargs={"reasoning_content": _LEAK_REASONING}),
        # flush: byte-identical re-send of the buffer, WITH content on the
        # same chunk — the content must not be lost
        AIMessageChunk(content=answer, additional_kwargs={"reasoning_content": _LEAK_REASONING}),
        AIMessageChunk(content=" Section 300 defines murder."),
    ])
    node = make_agent_node(settings, model, has_tools=True)
    out = await node(_leak_state())

    reasoning_emitted = "".join(e["content"] for e in captured if e["type"] == "reasoning")
    assert reasoning_emitted.count("Here's a thinking process") == 1  # no duplicate flush
    tokens = "".join(e["content"] for e in captured if e["type"] == "token")
    assert answer + " Section 300 defines murder." == tokens  # content survived
    assert "[[act: IPC, ref: s. 302]]" in out["messages"][0].content


@pytest.mark.asyncio
async def test_agent_leak_only_stream_still_completes(settings, monkeypatch):
    """When the flush is the ONLY content (the model never wrote an answer
    before truncation), the turn still completes: the verified note + disclaimer
    text is emitted as a correction — no crash."""
    from langchain_core.messages import AIMessageChunk

    from nyaya_chat.graph.agent import make_agent_node

    captured = _captured_events(monkeypatch)
    model = _FakeStreamingModel([
        AIMessageChunk(content="", additional_kwargs={"reasoning_content": _LEAK_REASONING}),
        AIMessageChunk(content=_LEAK_REASONING),
    ])
    node = make_agent_node(settings, model, has_tools=True)
    out = await node(_leak_state())
    final = out["messages"][0].content
    assert "thinking process" not in final
    corrections = [e["content"] for e in captured if e["type"] == "correction"]
    assert corrections  # the frontend is told to replace the (empty) stream


@pytest.mark.asyncio
async def test_agent_keeps_cited_answer_over_uncited_resynthesis(fake_model, settings, monkeypatch):
    """Keep-better across reflection rounds: when the reflection leg
    re-synthesizes on the SAME tool results and its answer has no citations
    while the previous round's answer did, the previous answer is kept and a
    correction restores it — the observed regression was a degenerate 63-char
    reflection answer replacing a good round-1 answer."""
    from nyaya_chat.graph.agent import make_agent_node

    captured = _captured_events(monkeypatch)
    prev = "Culpable homicide is the genus; murder the species [[act: IPC, ref: s. 300]]."
    fake_model.responses = ["I could not find anything about that."]
    node = make_agent_node(settings, fake_model, has_tools=True)
    state = {
        "messages": [
            HumanMessage(content="difference between murder and culpable homicide?"),
            AIMessage(content=prev),  # round-1 final answer
            AIMessage(content="", tool_calls=[
                {"id": "tc2", "name": "semantic_query", "args": {"query": "murder vs culpable homicide"}},
            ]),
            ToolMessage(content=json.dumps({"results": []}), tool_call_id="tc2", name="semantic_query"),
        ],
        "round": 1,
        "last_answer": prev,
        "last_answer_cited": True,
        "rid": "keep1",
    }
    out = await node(state)
    assert out["messages"] == []  # the junk answer is NOT added to history
    assert out["round"] == 2
    assert out["last_answer"] == prev  # the kept answer stays authoritative
    corrections = [e["content"] for e in captured if e["type"] == "correction"]
    assert corrections[-1] == prev  # the frontend is restored to the good answer


@pytest.mark.asyncio
async def test_agent_logs_first_token_latency(settings, monkeypatch, caplog):
    """The agent's answer stream must log its time-to-first-token — the
    overhaul's headline metric — mirroring the agent_ms phase log."""
    import logging as _logging

    from langchain_core.messages import AIMessageChunk

    from nyaya_chat.graph.agent import make_agent_node

    _captured_events(monkeypatch)
    model = _FakeStreamingModel([
        AIMessageChunk(content="Murder requires intention"),
        AIMessageChunk(content=" [[act: IPC, ref: s. 302]]."),
    ])
    node = make_agent_node(settings, model, has_tools=True)
    with caplog.at_level(_logging.INFO, logger="nyaya_chat.graph.agent"):
        await node(_leak_state())
    assert any("first token" in rec.message for rec in caplog.records), \
        [r.message for r in caplog.records]
