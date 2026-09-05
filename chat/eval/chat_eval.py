"""Chat quality eval harness — the single merged replacement for the old
``chat_eval.py`` + ``validate_chat.py`` pair (Task 11).

Sends real chat queries to ``POST /chat/turn`` and analyzes the SSE stream:

- SSE protocol (meta, status, plan, token, reasoning, tool_start, tool_result,
  citations, correction, ping, done, error)
- Tool calls (which tools, how many, with what args)
- Citation grounding ([[act: X, ref: Y]] markers, extracted with the backend's
  shared ``nyaya_chat.citations.CITATION_RE``)
- Guardrail fast-path behavior (greetings/capabilities/thanks/off-topic:
  instant canned response, no tools, no reasoning events)
- Refusal handling (out-of-corpus questions must not fabricate citations)
- Markdown structure (headings, disclaimer)
- Latency: REAL incremental SSE reads, so time-to-first-token is measured at
  the moment the first ``token`` event arrives off the wire (the old harness
  faked it as ``latency * 0.3``; that hack is gone)
- Error handling: unified error shape {message, detail, rid}, status events
  carrying a non-blank ``rid``

Usage (from the chat/ directory)::

    python -m eval.chat_eval --host http://localhost:8001
    python -m eval.chat_eval --host http://localhost:8001 --verbose
    python -m eval.chat_eval --host http://localhost:8001 --scenario fact-ipc-302
    python -m eval.chat_eval --test fact-ipc-302          # --test aliases --scenario
    python -m eval.chat_eval --list

The scenarios and checks are the union of the two replaced harnesses:
``validate_chat.py``'s dataset was already a strict superset of the old
``chat_eval.py`` SCENARIOS except one question (the unicode ``§302`` edge
case), which is kept here as ``edge-special-chars-unicode``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Make the nyaya_chat package importable whether this script is run in
# place (``python eval/chat_eval.py``) or from the chat root (``python -m
# eval.chat_eval``). The citation-marker regex is single-sourced there.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# SSE parsing — incremental, so time-to-first-token is real
# ---------------------------------------------------------------------------

def parse_sse_block(block: str) -> tuple[str, Any] | None:
    """Parse one SSE block (the lines between blank lines) into an event.

    Per the SSE spec: multiple ``data:`` lines are joined with a newline and
    only the single leading space after ``data:`` is stripped. Returns
    ``(event_name, payload)`` where payload is JSON when it parses, else
    ``{"raw": data}``.
    """
    block = block.strip()
    if not block:
        return None
    event = "message"
    data_lines: list[str] = []
    for line in block.split("\n"):
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            seg = line[5:]
            data_lines.append(seg[1:] if seg.startswith(" ") else seg)
    if not data_lines:
        return None
    data = "\n".join(data_lines)
    try:
        payload = json.loads(data)
    except json.JSONDecodeError:
        payload = {"raw": data}
    return event, payload


def parse_sse_stream(raw_bytes: bytes) -> list[dict[str, Any]]:
    """Parse raw SSE bytes into ``[{event, data}]`` (used by the pytest
    wrapper and for offline parsing of saved responses)."""
    events: list[dict[str, Any]] = []
    for block in raw_bytes.decode("utf-8").split("\n\n"):
        parsed = parse_sse_block(block)
        if parsed is not None:
            events.append({"event": parsed[0], "data": parsed[1]})
    return events


# ---------------------------------------------------------------------------
# Result models
# ---------------------------------------------------------------------------

@dataclass
class ToolCallInfo:
    name: str
    args: dict[str, Any] | None
    result_summary: str | None


@dataclass
class StreamResult:
    """Everything one scenario produced, in one place."""

    scenario_id: str = ""
    question: str = ""
    category: str = ""
    # SSE events as (event_name, payload, received_at) with received_at =
    # time.monotonic() at the moment the event actually arrived off the wire.
    events: list[tuple[str, Any, float]] = field(default_factory=list)
    answer_text: str = ""
    plan_text: str = ""
    tool_calls: list[ToolCallInfo] = field(default_factory=list)
    citations: list[dict[str, str]] = field(default_factory=list)
    request_id: str = ""
    statuses: list[str] = field(default_factory=list)
    # Contract violations: status events missing a non-blank rid
    status_missing_rid: int = 0
    latency_ms: float = 0
    time_to_first_token_ms: float = 0
    # Error (unified shape: {message, detail, rid})
    error: str | None = None
    error_detail: str = ""
    error_rid: str = ""
    checks: list[tuple[str, bool, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Citation parsing
# ---------------------------------------------------------------------------

def extract_citations(text: str) -> list[dict[str, str]]:
    """Extract [[act: X, ref: Y]] markers using the backend's shared regex
    (``nyaya_chat.citations.CITATION_RE``)."""
    from nyaya_chat.citations import CITATION_RE

    citations: list[dict[str, str]] = []
    seen: set[str] = set()
    for m in CITATION_RE.finditer(text):
        act = m.group(1).strip()
        ref = m.group(2).strip()
        key = f"{act}|{ref}"
        if key not in seen:
            seen.add(key)
            citations.append({"act": act, "ref": ref})
    return citations


# ---------------------------------------------------------------------------
# SSE request — streamed, with real event timestamps
# ---------------------------------------------------------------------------

def stream_chat_turn(
    host: str,
    message: str,
    history: list[dict[str, str]] | None = None,
    timeout: float = 300,
) -> StreamResult:
    """POST /chat/turn and read the SSE stream incrementally.

    ``time_to_first_token_ms`` is the real time from request start until the
    first ``token`` event arrived off the wire — not an estimate.
    ``latency_ms`` covers the full stream.
    """
    payload = json.dumps({"message": message, "history": history or []}).encode("utf-8")
    req = urllib.request.Request(
        f"{host}/chat/turn",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    )
    start = time.monotonic()
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        latency = (time.monotonic() - start) * 1000
        body = e.read().decode("utf-8", errors="replace")
        return StreamResult(latency_ms=latency, error=f"HTTP {e.code}: {body[:200]}")
    except Exception as e:  # noqa: BLE001 - report any transport failure as an error string
        latency = (time.monotonic() - start) * 1000
        return StreamResult(latency_ms=latency, error=str(e))

    events: list[tuple[str, Any, float]] = []
    ttft_ms = 0.0
    block_lines: list[str] = []
    with resp:
        for raw_line in resp:
            line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
            if line:
                block_lines.append(line)
                continue  # blank line terminates an SSE block
            parsed = parse_sse_block("\n".join(block_lines))
            block_lines = []
            if parsed is None:
                continue
            event, data = parsed
            received_at = time.monotonic()
            if event == "token" and ttft_ms == 0:
                ttft_ms = (received_at - start) * 1000
            events.append((event, data, received_at))
    latency = (time.monotonic() - start) * 1000
    return StreamResult(events=events, latency_ms=latency, time_to_first_token_ms=ttft_ms)


# ---------------------------------------------------------------------------
# Result extraction
# ---------------------------------------------------------------------------

def extract_result(stream: StreamResult) -> StreamResult:
    """Fold the raw event stream into answer text, plan, tools, citations."""
    answer_parts: list[str] = []
    plan_parts: list[str] = []
    tools_map: dict[str, ToolCallInfo] = {}

    for event_type, data, _ts in stream.events:
        if event_type == "meta":
            stream.request_id = data.get("request_id", "")
        elif event_type == "status":
            stream.statuses.append(data.get("msg", ""))
            if not data.get("rid"):
                stream.status_missing_rid += 1
        elif event_type == "plan":
            plan_parts.append(data.get("content", ""))
        elif event_type == "token":
            answer_parts.append(data.get("content", ""))
        elif event_type == "citations":
            cites = data.get("citations", [])
            if cites:
                stream.citations = cites
        elif event_type == "correction":
            # The verified answer replaces the raw streamed tokens (matches
            # the frontend, which swaps the message content on correction).
            answer_parts = [data.get("content", "")]
        elif event_type == "error":
            stream.error = data.get("message", "unknown_error")
            stream.error_detail = data.get("detail", "")
            stream.error_rid = data.get("rid", "")
        elif event_type == "tool_start":
            tc_id = data.get("id", "")
            tools_map[tc_id] = ToolCallInfo(
                name=data.get("name", ""), args=data.get("args"), result_summary=None,
            )
        elif event_type == "tool_result":
            tc_id = data.get("id", "")
            tc_summary = data.get("summary", "")
            if tc_id in tools_map:
                tools_map[tc_id].result_summary = tc_summary
            else:
                tools_map[tc_id] = ToolCallInfo(
                    name=data.get("name", ""), args=None, result_summary=tc_summary,
                )

    stream.answer_text = "".join(answer_parts)
    stream.plan_text = "".join(plan_parts)
    stream.tool_calls = list(tools_map.values())
    # If citations weren't set by the citations event, parse from text.
    if not stream.citations:
        stream.citations = extract_citations(stream.answer_text)
    return stream


# ---------------------------------------------------------------------------
# Scenarios — superset of both old suites
# ---------------------------------------------------------------------------

@dataclass
class Scenario:
    id: str
    question: str
    category: str
    expected_behavior: str = ""
    history: list[dict[str, str]] | None = None
    max_latency_ms: float = 180000  # 3 min default


SCENARIOS: list[Scenario] = [
    # ── Greetings (guardrail Tier 1, instant) ──
    Scenario("greeting-hello", "hello", "greeting", "Instant canned greeting, no tools", max_latency_ms=200),
    Scenario("greeting-hi", "hi", "greeting", "Instant canned greeting, no tools", max_latency_ms=200),
    Scenario("greeting-hey", "hey", "greeting", "Instant canned greeting, no tools", max_latency_ms=200),
    Scenario("greeting-good-morning", "good morning!", "greeting", "Instant canned greeting, no tools", max_latency_ms=200),
    Scenario("greeting-greetings", "greetings", "greeting", "Instant canned greeting, no tools", max_latency_ms=200),
    Scenario("greeting-hello-there", "hello there", "greeting", "Instant canned greeting, no tools", max_latency_ms=200),

    # ── Capability questions (guardrail Tier 1, instant) ──
    Scenario("capability-what-can-you-do", "what can you do?", "capability", "Instant canned capability response", max_latency_ms=200),
    Scenario("capability-who-are-you", "who are you?", "capability", "Instant canned capability response", max_latency_ms=200),
    Scenario("capability-what-do-you-know", "what do you know?", "capability", "Instant canned capability response", max_latency_ms=200),
    Scenario("capability-help", "help", "capability", "Instant canned capability response", max_latency_ms=200),

    # ── Thanks (guardrail Tier 1, instant) ──
    Scenario("thanks-thank-you", "thank you", "thanks", "Instant canned thanks response", max_latency_ms=200),
    Scenario("thanks-great", "great!", "thanks", "Instant canned thanks response", max_latency_ms=200),

    # ── Off-topic (guardrail Tier 1, instant) ──
    Scenario("off-topic-weather", "what's the weather in Mumbai?", "off_topic", "Instant canned refusal", max_latency_ms=200),
    Scenario("off-topic-joke", "tell me a joke", "off_topic", "Instant canned refusal", max_latency_ms=200),
    Scenario("off-topic-recipe", "how to make biryani recipe", "off_topic", "Instant canned refusal", max_latency_ms=200),

    # ── Factual lookups (supervisor + tools + synthesis) ──
    Scenario("fact-ipc-302", "What is the punishment for murder under IPC section 302?", "factual_lookup", "Tool call + cited answer"),
    Scenario("fact-art-21", "What does Article 21 of the Constitution guarantee?", "factual_lookup", "Tool call + cited answer", max_latency_ms=300000),
    Scenario("fact-ipc-420", "Explain IPC section 420 on cheating.", "factual_lookup", "Tool call + cited answer"),
    Scenario("fact-art-14", "What is Article 14 of the Constitution about?", "factual_lookup", "Tool call + cited answer", max_latency_ms=300000),

    # ── Semantic search (topical queries) ──
    Scenario("semantic-good-faith", "What is the legal definition of good faith in Indian law?", "semantic_search", "semantic_query + cited answer"),
    Scenario("semantic-dowry", "What are the laws against dowry in India?", "semantic_search", "semantic_query + cited answer"),
    Scenario("semantic-self-defence", "When can a person legally use force in self defence?", "semantic_search", "semantic_query + cited answer"),

    # ── Cross-act comparisons ──
    Scenario("compare-ipc-bns-murder", "Compare the punishment for murder under IPC and the new BNS. What changed?", "comparison", "Multiple tool calls + cited comparison"),
    Scenario("compare-ipc-bns-theft", "How does theft differ between IPC and BNS?", "comparison", "Multiple tool calls + cited comparison", max_latency_ms=300000),

    # ── Refusal (out-of-corpus) ──
    Scenario("refusal-nonexistent", "What does the Indian Space Act of 2050 say about Mars colonies?", "refusal", "Tool call + refusal, no fabricated citations"),
    Scenario("refusal-fake-section", "What does IPC section 99999 say?", "refusal", "Tool call + refusal, no fabricated citations"),

    # ── Judgment lookup ──
    Scenario("judgment-kesavananda", "What was the Kesavananda Bharati case about?", "judgment", "get_judgment + cited answer", max_latency_ms=300000),

    # ── Definition lookup ──
    Scenario("definition-dishonestly", "What is the meaning of 'dishonestly' under the IPC?", "definition", "get_section or semantic_query + cited answer"),

    # ── Multi-turn conversation ──
    Scenario("multi-turn-1", "What does Article 19 of the Constitution guarantee?", "multi_turn", "Tool call + cited answer"),
    Scenario("multi-turn-2", "How does it relate to reasonable restrictions?", "multi_turn", "Tool call + cited answer", history=[
        {"role": "user", "content": "What does Article 19 of the Constitution guarantee?"},
        {"role": "assistant", "content": "Article 19 of the Constitution of India guarantees six fundamental freedoms to all citizens. These include freedom of speech and expression, assembly, association, movement, residence, and profession. Each freedom is subject to reasonable restrictions that can be imposed by law. [[act: Constitution, ref: 19]]"},
    ]),

    # ── Edge cases ──
    Scenario("edge-special-chars", "What is the punishment for section 302 IPC?", "edge_case", "Should handle and call tools"),
    # Kept from the old chat_eval.py: unicode punctuation (section sign,
    # em-dash, quotes) in the question.
    Scenario("edge-special-chars-unicode", "What is the punishment for §302 IPC? (murder -- 'death penalty')",
             "edge_case", "Should handle unicode punctuation and call tools"),
    Scenario("edge-ambiguous", "302", "edge_case", "Should interpret as IPC 302 and call tools"),
    Scenario("edge-very-long", "What is Section 302 of the IPC? " + "Please provide a very detailed explanation. " * 20,
             "edge_case", "Should handle long input and call tools"),
]


# ---------------------------------------------------------------------------
# Quality checks — union of both old suites
# ---------------------------------------------------------------------------

def run_checks(result: StreamResult, scenario: Scenario) -> None:
    """Run quality checks on the result and populate result.checks."""
    # ── Universal checks (all categories) ──
    result.checks.append((
        "no_error", result.error is None,
        f"error={result.error}" if result.error else "OK",
    ))

    has_done = any(event_type == "done" for event_type, _d, _ts in result.events)
    result.checks.append(("has_done_event", has_done, ""))

    has_meta = any(event_type == "meta" for event_type, _d, _ts in result.events)
    result.checks.append((
        "has_meta_event", has_meta,
        f"request_id={result.request_id[:12] if result.request_id else 'missing'}",
    ))

    result.checks.append(("has_status", len(result.statuses) > 0, f"statuses={result.statuses}"))

    # Unified SSE contract: every status event carries a non-blank rid.
    result.checks.append((
        "status_events_have_rid",
        result.status_missing_rid == 0,
        f"missing_rid={result.status_missing_rid}",
    ))

    # Unified error shape: {message, detail, rid}
    error_events = [(t, d) for t, d, _ts in result.events if t == "error"]
    if error_events:
        d = error_events[0][1]
        error_shape_ok = (
            isinstance(d.get("message"), str)
            and isinstance(d.get("detail"), str)
            and bool(d.get("rid"))
        )
        result.checks.append(("error_shape", error_shape_ok, json.dumps(d)[:120]))

    # Latency budget (per-scenario: guardrails 200ms, legal up to 3-5 min)
    result.checks.append((
        "latency_ok", result.latency_ms <= scenario.max_latency_ms,
        f"latency={result.latency_ms:.0f}ms (max={scenario.max_latency_ms:.0f}ms)",
    ))

    result.checks.append(("no_corpus_text_tags", "<corpus_text>" not in result.answer_text, ""))

    # Tool result summaries must be human-readable, not raw Python repr.
    has_repr_leak = any(
        tc.result_summary
        and ("[{'type'" in tc.result_summary[:50] or "'text'" in tc.result_summary[:50])
        for tc in result.tool_calls
    )
    result.checks.append(("no_tool_repr_leak", not has_repr_leak, ""))

    # The model's deliberation must never surface in the answer body — the
    # reasoning trace is where thinking belongs. High-precision markers only,
    # so a legitimate legal phrase can't false-fail the check.
    reasoning_leak_markers = (
        "here's a thinking process",
        "here is a thinking process",
        "analyzing user input",
        "let me think through",
        "drafting the answer",
    )
    leaked_marker = next(
        (m for m in reasoning_leak_markers if m in result.answer_text.lower()), None,
    )
    result.checks.append((
        "no_reasoning_leak",
        leaked_marker is None,
        f"marker={leaked_marker!r}" if leaked_marker else "",
    ))

    # ── Category-specific checks ──
    answer_lower = result.answer_text.lower()
    tools = [t.name for t in result.tool_calls]

    if scenario.category in ("greeting", "capability", "thanks", "off_topic"):
        # Guardrail fast-path: no tools, fast, canned response.
        result.checks.append(("no_tool_calls", len(result.tool_calls) == 0, f"tools={tools}"))
        result.checks.append((
            "has_answer", len(result.answer_text.strip()) > 20,
            f"answer_len={len(result.answer_text)}",
        ))
        result.checks.append(("no_citations", not result.citations, f"citations={result.citations[:3]}"))
        # The guardrail bypasses the pipeline: no reasoning events either.
        has_reasoning = any(t == "reasoning" for t, _d, _ts in result.events)
        result.checks.append(("no_reasoning_events", not has_reasoning, ""))
        # Fast even beyond the strict per-scenario budget (Tier 2 cap).
        result.checks.append(("fast_response", result.latency_ms < 15000, f"latency={result.latency_ms:.0f}ms"))
        if scenario.category == "greeting":
            has_keyword = "hello" in answer_lower or "i'm nyaya" in answer_lower or "indian law" in answer_lower
        elif scenario.category == "capability":
            has_keyword = "i can" in answer_lower or "nyaya" in answer_lower
        elif scenario.category == "thanks":
            has_keyword = "welcome" in answer_lower
        else:
            has_keyword = "indian law" in answer_lower or "can't help" in answer_lower or "cannot help" in answer_lower
        result.checks.append(("response_has_keyword", has_keyword, f"answer_start={answer_lower[:60]}"))

    elif scenario.category == "factual_lookup":
        result.checks.append(("has_tool_calls", len(result.tool_calls) > 0, f"tools={tools}"))
        result.checks.append(("has_citations", len(result.citations) > 0, f"citations={result.citations[:3]}"))
        result.checks.append((
            "has_substantive_answer", len(answer_lower) > 50,
            f"answer_len={len(result.answer_text)}",
        ))
        result.checks.append(("has_markdown_headings", "##" in result.answer_text, ""))
        result.checks.append(("has_disclaimer", "not legal advice" in answer_lower, ""))

    elif scenario.category == "semantic_search":
        result.checks.append(("has_tool_calls", len(result.tool_calls) > 0, f"tools={tools}"))
        result.checks.append(("uses_semantic_query", "semantic_query" in tools, f"tools={tools}"))
        result.checks.append(("has_citations", len(result.citations) > 0, f"citations={result.citations[:3]}"))

    elif scenario.category == "comparison":
        has_comparison_tools = len(result.tool_calls) >= 2 or "cross_reference" in tools
        result.checks.append(("has_comparison_tools", has_comparison_tools, f"tools={tools}"))
        result.checks.append(("mentions_both_acts", "ipc" in answer_lower and "bns" in answer_lower, ""))
        result.checks.append(("has_citations", len(result.citations) > 0, f"citations={result.citations[:3]}"))

    elif scenario.category == "refusal":
        result.checks.append(("no_fabricated_citations", len(result.citations) == 0, f"citations={result.citations}"))
        refusal_indicators = [
            "could not find", "not in the corpus", "not available",
            "could not find a basis", "no basis in the corpus",
            "did not include verifiable", "not in the nyaya corpus",
            "i don't have", "cannot find", "no information",
            "does not exist", "does not contain",
        ]
        result.checks.append((
            "has_refusal_language",
            any(ind in answer_lower for ind in refusal_indicators), "",
        ))

    elif scenario.category == "multi_turn":
        result.checks.append(("has_tool_calls", len(result.tool_calls) > 0, f"tools={tools}"))
        result.checks.append(("has_citations", len(result.citations) > 0, f"citations={result.citations[:3]}"))

    elif scenario.category == "judgment":
        result.checks.append((
            "uses_judgment_tool",
            any(t in ("get_judgment", "semantic_query") for t in tools), f"tools={tools}",
        ))

    elif scenario.category == "definition":
        has_definition_tool = any(t in ("semantic_query", "get_section") for t in tools)
        result.checks.append(("uses_definition_tool", has_definition_tool, f"tools={tools}"))
        result.checks.append(("has_citations", len(result.citations) > 0, f"citations={result.citations[:3]}"))

    elif scenario.category == "edge_case":
        has_response = len(result.answer_text.strip()) > 0 or result.error is not None
        result.checks.append(("has_response_or_error", has_response, ""))


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_scenario(host: str, scenario: Scenario, *, live: bool = True) -> StreamResult:
    """Run one scenario and populate its checks."""
    if live:
        print(f"\n{'-' * 70}")
        print(f"  Scenario: {scenario.id} ({scenario.category})")
        print(f"  Q: {scenario.question[:80]}{'...' if len(scenario.question) > 80 else ''}")
        print(f"  Expected: {scenario.expected_behavior}")

    stream = stream_chat_turn(host, scenario.question, history=scenario.history, timeout=300)
    stream.scenario_id = scenario.id
    stream.question = scenario.question
    stream.category = scenario.category
    extract_result(stream)
    run_checks(stream, scenario)

    passed = sum(1 for _, ok, _ in stream.checks if ok)
    total = len(stream.checks)
    ttft = f" TTFT={stream.time_to_first_token_ms:.0f}ms" if stream.time_to_first_token_ms else ""
    if live:
        print(f"  [{'PASS' if passed == total else 'FAIL'}] Checks: {passed}/{total} | "
              f"Tools: {len(stream.tool_calls)} | Citations: {len(stream.citations)} | "
              f"Latency: {stream.latency_ms:.0f}ms{ttft} | Answer: {len(stream.answer_text)} chars")
        if stream.error:
            print(f"  WARN: {stream.error}")
        for check_name, check_ok, check_detail in stream.checks:
            if not check_ok:
                print(f"    FAIL {check_name}: {check_detail}")
    return stream


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def print_final_report(results: list[StreamResult], verbose: bool = False) -> None:
    """Print the merged final report (superset of both old reports)."""
    print(f"\n\n{'=' * 70}")
    print("CHAT EVAL REPORT")
    print(f"{'=' * 70}")
    print(f"Scenarios: {len(results)}")

    total_checks = sum(len(r.checks) for r in results)
    passed_checks = sum(1 for r in results for _, ok, _ in r.checks if ok)
    print(f"Total checks: {passed_checks}/{total_checks} ({passed_checks / total_checks * 100:.1f}%)")

    # By category
    categories: dict[str, list[StreamResult]] = {}
    for r in results:
        categories.setdefault(r.category, []).append(r)

    print(f"\n{'-' * 70}")
    print("BY CATEGORY:")
    for cat, cat_results in sorted(categories.items()):
        cat_checks = sum(len(r.checks) for r in cat_results)
        cat_passed = sum(1 for r in cat_results for _, ok, _ in r.checks if ok)
        cat_pass_rate = cat_passed / cat_checks * 100 if cat_checks else 0
        cat_latency = sum(r.latency_ms for r in cat_results) / len(cat_results)
        cat_tools = sum(len(r.tool_calls) for r in cat_results) / len(cat_results)
        cat_citations = sum(len(r.citations) for r in cat_results) / len(cat_results)
        icon = "PASS" if cat_pass_rate == 100 else ("WARN" if cat_pass_rate >= 80 else "FAIL")
        print(f"  [{icon}] {cat:20s} checks={cat_passed}/{cat_checks} ({cat_pass_rate:.0f}%) "
              f"avg_latency={cat_latency:.0f}ms avg_tools={cat_tools:.1f} avg_citations={cat_citations:.1f}")

    # Tool usage
    print(f"\n{'-' * 70}")
    print("TOOL USAGE:")
    tool_counts: dict[str, int] = {}
    for r in results:
        for tc in r.tool_calls:
            tool_counts[tc.name] = tool_counts.get(tc.name, 0) + 1
    for tool_name, count in sorted(tool_counts.items(), key=lambda x: -x[1]):
        print(f"  {tool_name:25s} called {count} times")
    no_tool_count = sum(1 for r in results if len(r.tool_calls) == 0)
    print(f"  {'(no tools)':25s} {no_tool_count} scenarios")

    # Citation stats
    print(f"\n{'-' * 70}")
    print("CITATIONS:")
    total_citations = sum(len(r.citations) for r in results)
    with_citations = sum(1 for r in results if len(r.citations) > 0)
    print(f"  Total citations: {total_citations}")
    print(f"  Scenarios with citations: {with_citations}/{len(results)}")

    # Latency + real time-to-first-token
    print(f"\n{'-' * 70}")
    print("LATENCY (ms):")
    latencies = [r.latency_ms for r in results if r.error is None]
    if latencies:
        sorted_l = sorted(latencies)
        print(f"  Min:    {min(latencies):.0f}ms")
        print(f"  Max:    {max(latencies):.0f}ms")
        print(f"  Mean:   {sum(latencies) / len(latencies):.0f}ms")
        print(f"  Median: {sorted_l[len(sorted_l) // 2]:.0f}ms")
        print(f"  P90:    {sorted_l[int(len(sorted_l) * 0.9)]:.0f}ms")

    ttfts = [r.time_to_first_token_ms for r in results
             if r.time_to_first_token_ms > 0 and r.error is None]
    if ttfts:
        print("\n  Time to first token (ms) — measured on the live SSE stream:")
        print(f"    Min:    {min(ttfts):.0f}ms")
        print(f"    Mean:   {sum(ttfts) / len(ttfts):.0f}ms")
        sorted_t = sorted(ttfts)
        print(f"    Median: {sorted_t[len(sorted_t) // 2]:.0f}ms")
        print(f"    P90:    {sorted_t[int(len(sorted_t) * 0.9)]:.0f}ms")

    guardrail_latencies = [r.latency_ms for r in results
                           if r.category in ("greeting", "capability", "thanks", "off_topic")
                           and r.error is None]
    if guardrail_latencies:
        print("\n  Guardrail latency:")
        print(f"    Mean: {sum(guardrail_latencies) / len(guardrail_latencies):.0f}ms")
        print(f"    Max:  {max(guardrail_latencies):.0f}ms")

    # Errors
    errors = [r for r in results if r.error is not None]
    if errors:
        print(f"\n{'-' * 70}")
        print(f"ERRORS ({len(errors)}):")
        for r in errors:
            print(f"  {r.scenario_id:25s} error={r.error}")

    # SSE protocol events
    print(f"\n{'-' * 70}")
    print("SSE PROTOCOL EVENTS:")
    event_counts: dict[str, int] = {}
    for r in results:
        for event_type, _d, _ts in r.events:
            event_counts[event_type] = event_counts.get(event_type, 0) + 1
    for name, count in sorted(event_counts.items(), key=lambda x: -x[1]):
        print(f"  {name:20s} {count} total events")

    # Detailed per-scenario output
    if verbose:
        print(f"\n{'-' * 70}")
        print("DETAILED SCENARIO RESULTS:")
        for r in results:
            print(f"\n  -- {r.scenario_id} ({r.category}) --")
            print(f"  Q: {r.question[:100]}")
            print(f"  Latency: {r.latency_ms:.0f}ms | TTFT: {r.time_to_first_token_ms:.0f}ms")
            print(f"  Request ID: {r.request_id}")
            print(f"  Statuses: {r.statuses}")
            print(f"  Plan: {r.plan_text[:100]}{'...' if len(r.plan_text) > 100 else ''}")
            print(f"  Tools ({len(r.tool_calls)}):")
            for tc in r.tool_calls:
                args_str = json.dumps(tc.args) if tc.args else "(no args)"
                summary_len = len(tc.result_summary) if tc.result_summary else 0
                print(f"    - {tc.name}(args={args_str[:60]}) -> {summary_len} chars result")
            print(f"  Citations ({len(r.citations)}): {r.citations[:5]}")
            print(f"  Answer ({len(r.answer_text)} chars):")
            print(f"    {r.answer_text[:300]}{'...' if len(r.answer_text) > 300 else ''}")
            if r.error:
                print(f"  ERROR: {r.error}")
            failed_checks = [(n, d) for n, ok, d in r.checks if not ok]
            if failed_checks:
                print("  Failed checks:")
                for check_name, check_detail in failed_checks:
                    print(f"    FAIL {check_name}: {check_detail}")

    print(f"\n{'=' * 70}")
    if passed_checks == total_checks:
        print(f"ALL CHECKS PASSED -- {passed_checks}/{total_checks}")
    else:
        failed = total_checks - passed_checks
        print(f"{failed} CHECK(S) FAILED -- {passed_checks}/{total_checks} passed")
    print(f"{'=' * 70}")


def main() -> None:
    # Windows consoles default to cp1252; the report prints box chars,
    # narrow no-break spaces ( ) and other non-ASCII from model output.
    # Without this, print_final_report crashes *after* the run completes and
    # the aggregate is lost (observed on the 2026-09-05 baseline).
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description="Chat quality eval harness (merged)")
    # Default 127.0.0.1, not localhost: on Windows, urllib resolves localhost
    # to ::1 first and the connection stall (server binds IPv4) inflated every
    # measured latency by ~2s — enough to fail every canned-path latency check.
    parser.add_argument("--host", default="http://127.0.0.1:8001", help="Base URL of the nyaya server")
    parser.add_argument("--verbose", action="store_true", help="Print detailed per-scenario output")
    parser.add_argument("--scenario", "--test", dest="scenario", default=None,
                        help="Run a single scenario by ID (alias: --test)")
    parser.add_argument("--list", action="store_true", help="List scenario IDs and exit")
    parser.add_argument("--sleep", type=float, default=5.0,
                        help="Seconds between requests (rate-limit safety; default 5)")
    args = parser.parse_args()

    if args.list:
        for s in SCENARIOS:
            print(f"  {s.id:32s} {s.category:16s} {s.expected_behavior}")
        return

    print(f"Target: {args.host}")
    print(f"Scenarios: {len(SCENARIOS)}")

    try:
        health = urllib.request.urlopen(f"{args.host}/chat/health", timeout=5)
        print(f"Chat health: {json.loads(health.read())}")
    except Exception as e:
        print(f"ERROR: Cannot reach server at {args.host}: {e}")
        sys.exit(1)

    scenarios_to_run = SCENARIOS if not args.scenario else [s for s in SCENARIOS if s.id == args.scenario]
    if not scenarios_to_run:
        print(f"ERROR: no scenario matching {args.scenario!r}. Use --list to see IDs.")
        sys.exit(1)

    results: list[StreamResult] = []
    for scenario in scenarios_to_run:
        results.append(run_scenario(args.host, scenario))
        # Delay between requests to avoid rate limiting (15 chat req/min).
        if len(scenarios_to_run) > 1:
            time.sleep(args.sleep)

    print_final_report(results, verbose=args.verbose)

    total_failed = sum(1 for r in results for _, ok, _ in r.checks if not ok)
    if total_failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
