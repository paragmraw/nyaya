"""Offline unit tests for the eval harness itself (``eval/chat_eval.py``).

Unlike ``test_chat_eval.py`` (live-server, ``eval`` marker), these run in the
default hermetic pytest pass: they import the harness module directly and
exercise its pure pieces — the Scenario latency/TTFT fields and the
soft-vs-gating check accounting.
"""

from __future__ import annotations

import importlib.util
import os
import sys

_HARNESS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eval", "chat_eval.py",
)


def _load():
    spec = importlib.util.spec_from_file_location("nyaya_chat_eval_harness", _HARNESS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec: @dataclass resolves annotations via sys.modules
    # under Python 3.13 and fails otherwise.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _result(mod, *, latency_ms=1000.0, ttft_ms=500.0, error=None):
    """A StreamResult with checks already populated by run_checks."""
    scenario = mod.Scenario("t", "test question", "factual_lookup")
    r = mod.StreamResult(scenario_id="t", latency_ms=latency_ms,
                         time_to_first_token_ms=ttft_ms, error=error)
    mod.run_checks(r, scenario)
    return r, scenario


def test_scenario_has_hard_ttft_budget():
    """The default scenario TTFT gating budget (<4s) is enforced as a hard cap."""
    mod = _load()
    s = mod.Scenario("x", "q", "factual_lookup")
    assert s.max_ttft_ms == 4000.0
    assert s.max_latency_ms == 20000.0


def test_legal_scenario_latency_caps_meet_the_plan_budget():
    mod = _load()
    legal = [s for s in mod.SCENARIOS if s.category not in
             ("greeting", "capability", "thanks", "off_topic")]
    assert legal, "expected legal scenarios"
    assert all(s.max_latency_ms <= 20000.0 for s in legal), \
        [(s.id, s.max_latency_ms) for s in legal if s.max_latency_ms > 20000]
    # Guardrail fast paths keep their strict 200ms budget.
    canned = [s for s in mod.SCENARIOS if s.category in
              ("greeting", "capability", "thanks", "off_topic")]
    assert all(s.max_latency_ms == 200 for s in canned)


def test_run_checks_appends_ttft_check():
    mod = _load()
    r, _ = _result(mod, ttft_ms=500.0)
    names = [name for name, _, _ in r.checks]
    assert "ttft_ok" in names


def test_ttft_check_fails_over_budget():
    mod = _load()
    r, _ = _result(mod, ttft_ms=120000.0)
    check = next(c for c in r.checks if c[0] == "ttft_ok")
    assert check[1] is False, check


def test_no_checks_are_soft():
    """All checks are gating — over-budget latency and TTFT are hard
    failures in live evaluation runs."""
    mod = _load()
    assert mod.SOFT_CHECKS == frozenset()
    r, _ = _result(mod, latency_ms=120000.0, ttft_ms=120000.0)
    gating = mod.gating_failures(r)
    assert [f[0] for f in gating if f[0] in ("latency_ok", "ttft_ok")] == \
        ["latency_ok", "ttft_ok"]


def test_canned_caps_relaxed_for_remote_host_only():
    """The 200ms canned cap is server compute; a remote host adds network RTT
    (remote canned requests can see up to ~1.1s from network latency alone), so
    live runs against remote hosts relax canned caps while local runs keep 200ms strict."""
    mod = _load()
    canned_ids = [s.id for s in mod.SCENARIOS if s.category in mod.CANNED_CATEGORIES]
    assert canned_ids, "expected canned scenarios"

    remote = mod.apply_host_latency_overrides(mod.SCENARIOS, "https://nyaya.parag.tech")
    for s in remote:
        if s.category in mod.CANNED_CATEGORIES:
            assert s.max_latency_ms == mod.CANNED_LIVE_LATENCY_MS, s.id
        else:
            assert s.max_latency_ms <= 20000.0, s.id
    # Original SCENARIOS untouched (pure function).
    for s in mod.SCENARIOS:
        if s.category in mod.CANNED_CATEGORIES:
            assert s.max_latency_ms == 200, s.id

    local = mod.apply_host_latency_overrides(mod.SCENARIOS, "http://127.0.0.1:8001")
    assert local is mod.SCENARIOS
    for s in local:
        if s.category in mod.CANNED_CATEGORIES:
            assert s.max_latency_ms == 200, s.id


def test_quality_failures_still_gate():
    mod = _load()
    r, scenario = _result(mod)
    # Factual lookup with no tool calls must be a gating failure.
    r.tool_calls = []
    r.checks = []
    mod.run_checks(r, scenario)
    gating = mod.gating_failures(r)
    assert any(f[0] == "has_tool_calls" for f in gating)


def _load_retrieval_eval():
    ret_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "eval",
        "retrieval_eval.py",
    )
    spec = importlib.util.spec_from_file_location("nyaya_retrieval_eval", ret_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_retrieval_eval_rejects_wrong_section_of_same_act():
    """Regression for v0.2 act-only recall bug: retrieving IPC s. 420 when
    expected is IPC s. 302 must score 0.0 recall and 0.0 MRR."""
    ret = _load_retrieval_eval()
    expected = [{"act": "IPC", "ref": "302"}]
    wrong_hit = [{"act": "IPC", "ref": "420"}]
    recall, mrr, found = ret.score_retrieved_items(expected, wrong_hit)
    assert recall == 0.0
    assert mrr == 0.0
    assert found == [False]

    # Hallucinated AIR page in same year/court also rejected
    expected_j = [{"act": "SC", "ref": "AIR 1973 SC 1461"}]
    wrong_j = [{"reporter_citations": ["AIR 1973 SC 9999"]}]
    j_recall, j_mrr, j_found = ret.score_retrieved_items(expected_j, wrong_j)
    assert j_recall == 0.0
    assert j_mrr == 0.0
    assert j_found == [False]

    # Exact hit at rank 2 -> recall 1.0, MRR 0.5
    hits = [{"act": "IPC", "ref": "420"}, {"act": "IPC", "ref": "s. 302"}]
    r_ok, mrr_ok, f_ok = ret.score_retrieved_items(expected, hits)
    assert r_ok == 1.0
    assert mrr_ok == 0.5
    assert f_ok == [True]


def test_golden_dataset_has_90_v2_cases():
    ret = _load_retrieval_eval()
    golden_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "eval",
        "golden.jsonl",
    )
    cases = ret.load_golden(golden_path)
    assert len(cases) >= 90
    categories = {c.category for c in cases}
    for required_cat in (
        "central_act",
        "constitution",
        "concordance",
        "invalidation",
        "sc_judgment",
        "hc_judgment",
        "state_regulator_tribunal",
        "district_court",
        "refusal",
    ):
        assert required_cat in categories, f"missing golden category {required_cat!r}"


def test_chat_eval_uses_v2_tool_names_and_scenarios():
    mod = _load()
    scenario_ids = {s.id for s in mod.SCENARIOS}
    assert "hc-precedent-dhc" in scenario_ids
    assert "struck-down-it-66a" in scenario_ids
    assert "verify-citations-kesavananda" in scenario_ids

    # Semantic search check must accept v2 search_statutes / search_judgments
    scen = next(s for s in mod.SCENARIOS if s.category == "semantic_search")
    r = mod.StreamResult(
        scenario_id=scen.id,
        latency_ms=800.0,
        time_to_first_token_ms=300.0,
        answer_text="## Answer\nGood faith is defined in BNS [[act: BNS, ref: 2]].\n\n*This is not legal advice; verify citations before filing.*",
        tool_calls=[mod.ToolCallInfo(name="search_statutes", args={"query": "good faith"}, result_summary="Found 1")],
        citations=[{"act": "BNS", "ref": "2"}],
        statuses=["analyzing", "searching", "composing"],
        request_id="rid1",
        events=[("meta", {"request_id": "rid1"}, 0.1), ("done", {}, 0.8)],
    )
    mod.run_checks(r, scen)
    assert not mod.gating_failures(r)
