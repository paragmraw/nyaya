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


def test_scenario_has_ttft_budget_defaulting_to_phase0_soft_cap():
    mod = _load()
    s = mod.Scenario("x", "q", "factual_lookup")
    assert s.max_ttft_ms == 60000.0


def test_legal_scenario_latency_caps_are_softened_for_phase0():
    mod = _load()
    legal = [s for s in mod.SCENARIOS if s.category not in
             ("greeting", "capability", "thanks", "off_topic")]
    assert legal, "expected legal scenarios"
    assert all(s.max_latency_ms <= 60000.0 for s in legal), \
        [(s.id, s.max_latency_ms) for s in legal if s.max_latency_ms > 60000]
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


def test_latency_and_ttft_are_soft_in_phase0():
    """Phase 0 records the baseline without red-gating latency: over-budget
    latency/TTFT must be reportable warnings, NOT gating failures."""
    mod = _load()
    r, _ = _result(mod, latency_ms=120000.0, ttft_ms=120000.0)
    gating = mod.gating_failures(r)
    assert [f for f in gating if f[0] in ("latency_ok", "ttft_ok")] == []
    # But they are still recorded as checks so the report can warn.
    assert any(name == "latency_ok" and not ok for name, ok, _ in r.checks)
    assert any(name == "ttft_ok" and not ok for name, ok, _ in r.checks)


def test_quality_failures_still_gate():
    mod = _load()
    r, scenario = _result(mod)
    # Factual lookup with no tool calls must be a gating failure.
    r.tool_calls = []
    r.checks = []
    mod.run_checks(r, scenario)
    gating = mod.gating_failures(r)
    assert any(f[0] == "has_tool_calls" for f in gating)