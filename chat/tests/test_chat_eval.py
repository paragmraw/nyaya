"""Pytest wrapper for the merged SSE eval harness (``eval/chat_eval.py``).

These are LIVE-server integration tests under the ``eval`` pytest marker:
they need a running nyaya stack (Postgres + NVIDIA API key + the server up).
Set ``NYAYA_EVAL_HOST`` to opt in::

    cd chat
    NYAYA_EVAL_HOST=http://localhost:8001 uv run pytest -m eval

Without ``NYAYA_EVAL_HOST`` every test here skips, so the default offline
pytest run stays hermetic (``--strict-markers`` is satisfied: ``eval`` is
registered in pyproject.toml).
"""

from __future__ import annotations

import importlib.util
import os

import pytest

_EVAL_HOST = os.environ.get("NYAYA_EVAL_HOST", "").rstrip("/")
_HARNESS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "eval", "chat_eval.py",
)

pytestmark = [
    pytest.mark.eval,
    # No live host provided -> nothing to evaluate; skip rather than fail.
    pytest.mark.skipif(
        not _EVAL_HOST,
        reason="set NYAYA_EVAL_HOST=http://host:port to run the live chat eval",
    ),
]


def _load():
    """Import the harness module (eval/ has no package __init__)."""
    if not os.path.exists(_HARNESS_PATH):  # pragma: no cover - repo layout guard
        pytest.skip("eval/chat_eval.py not found")
    spec = importlib.util.spec_from_file_location("nyaya_chat_eval_harness", _HARNESS_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(scenario_id: str):
    mod = _load()
    scenario = next(s for s in mod.SCENARIOS if s.id == scenario_id)
    result = mod.run_scenario(_EVAL_HOST, scenario, live=False)
    failed = [(name, detail) for name, ok, detail in result.checks if not ok]
    return result, failed


def test_guardrail_scenarios_pass():
    """Guardrail fast-path: instant canned response, no tools, no citations."""
    for scenario_id in (
        "greeting-hello", "capability-what-can-you-do",
        "thanks-thank-you", "off-topic-weather",
    ):
        result, failed = _run_checked(scenario_id)
        assert not failed, f"{scenario_id}: {failed}"


def _run_checked(scenario_id: str):
    result, failed = _run(scenario_id)
    return result, failed


def test_factual_lookup_scenario_passes():
    result, failed = _run_checked("fact-ipc-302")
    assert not failed, failed
    assert result.tool_calls, "factual lookup must call at least one tool"
    assert result.citations, "factual lookup must produce citations"


def test_real_time_to_first_token_is_measured():
    """The merged harness must measure TTFT off the live stream, not fake it
    as ``latency * 0.3`` like the pre-merge chat_eval.py did."""
    result, _failed = _run_checked("greeting-hello")
    # Guardrails stream instantly; only assert the field is populated when
    # tokens were actually received (a zero TTFT with tokens means the
    # incremental read is broken).
    if result.answer_text:
        assert result.time_to_first_token_ms > 0
