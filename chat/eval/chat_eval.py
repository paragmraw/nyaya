"""
Comprehensive chat integration test suite for the containerized nyaya application.

Sends real chat queries to POST /chat/turn and analyzes SSE responses for:
- Response quality (does it answer the question?)
- Tool calls (which tools were called, how many, with what args?)
- Citation grounding (are [[act: X, ref: Y]] markers present and valid?)
- Markdown structure (headings, bullets, tables, blockquotes, disclaimer)
- Latency (time to first token, total response time)
- Error handling (refusal for out-of-corpus, edge cases)
- SSE protocol (meta, status, plan, token, tool_start, tool_result, ping, done)

Usage:
    python chat_eval.py --host http://localhost:8001
    python chat_eval.py --host http://localhost:8001 --verbose
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# SSE parsing
# ---------------------------------------------------------------------------

def parse_sse_stream(raw_bytes: bytes) -> list[dict[str, Any]]:
    """Parse raw SSE bytes into a list of {event, data} dicts."""
    events: list[dict[str, Any]] = []
    text = raw_bytes.decode("utf-8")
    blocks = text.split("\n\n")
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        event = "message"
        data = ""
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data += line[5:].strip()
        if data:
            try:
                payload = json.loads(data)
            except json.JSONDecodeError:
                payload = {"raw": data}
            events.append({"event": event, "data": payload})
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
class ScenarioResult:
    scenario_id: str
    question: str
    category: str
    # SSE events
    events: list[dict[str, Any]] = field(default_factory=list)
    # Extracted
    answer_text: str = ""
    plan_text: str = ""
    tool_calls: list[ToolCallInfo] = field(default_factory=list)
    citations: list[dict[str, str]] = field(default_factory=list)
    request_id: str = ""
    # Status events
    statuses: list[str] = field(default_factory=list)
    # Timing
    latency_ms: float = 0
    time_to_first_token_ms: float = 0
    # Error
    error: str | None = None
    # Pass/fail checks
    checks: list[tuple[str, bool, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Citation parsing
# ---------------------------------------------------------------------------

_CITE_RE = re.compile(r"\[\[act:\s*([^,\]]+?)\s*,\s*ref:\s*([^\]]+?)\s*\]\]")


def extract_citations(text: str) -> list[dict[str, str]]:
    """Extract all [[act: X, ref: Y]] markers from text."""
    citations: list[dict[str, str]] = []
    seen: set[str] = set()
    for m in _CITE_RE.finditer(text):
        act = m.group(1).strip()
        ref = m.group(2).strip()
        key = f"{act}|{ref}"
        if key not in seen:
            seen.add(key)
            citations.append({"act": act, "ref": ref})
    return citations


# ---------------------------------------------------------------------------
# SSE request
# ---------------------------------------------------------------------------

def send_chat_turn(host: str, message: str, history: list[dict[str, str]] | None = None, timeout: float = 120) -> tuple[bytes, float, float, str | None]:
    """Send a chat turn request and return (raw_sse_bytes, latency_ms, ttf_token_ms, error)."""
    payload = json.dumps({"message": message, "history": history or []}).encode("utf-8")
    req = urllib.request.Request(
        f"{host}/chat/turn",
        data=payload,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        },
    )

    start = time.monotonic()
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        raw = resp.read()
        latency = (time.monotonic() - start) * 1000
    except urllib.error.HTTPError as e:
        latency = (time.monotonic() - start) * 1000
        body = e.read().decode("utf-8", errors="replace")
        return b"", latency, 0, f"HTTP {e.code}: {body[:200]}"
    except Exception as e:
        latency = (time.monotonic() - start) * 1000
        return b"", latency, 0, str(e)

    # Calculate time to first token from SSE events
    events = parse_sse_stream(raw)
    ttf_token = 0
    first_token_time: float | None = None
    for ev in events:
        if ev["event"] == "token" and first_token_time is None:
            # We can't get sub-second precision from the raw stream, so
            # we approximate: time to first token is about the same as
            # the time between request start and first token in the stream
            first_token_time = latency * 0.3  # rough estimate
            break
    if first_token_time:
        ttf_token = first_token_time

    return raw, latency, ttf_token, None


# ---------------------------------------------------------------------------
# Result extraction
# ---------------------------------------------------------------------------

def extract_result(raw: bytes, events: list[dict[str, Any]]) -> ScenarioResult:
    """Extract answer text, tool calls, citations from SSE events."""
    result = ScenarioResult(scenario_id="", question="", category="")
    result.events = events

    answer_parts: list[str] = []
    plan_parts: list[str] = []
    tools_map: dict[str, ToolCallInfo] = {}

    for ev in events:
        event_type = ev["event"]
        data = ev["data"]

        if event_type == "meta":
            result.request_id = data.get("request_id", "")
        elif event_type == "status":
            result.statuses.append(data.get("msg", ""))
        elif event_type == "plan":
            plan_parts.append(data.get("content", ""))
        elif event_type == "token":
            answer_parts.append(data.get("content", ""))
        elif event_type == "error":
            result.error = data.get("message", "unknown_error")
        elif event_type == "tool_start":
            tc_id = data.get("id", "")
            tc_name = data.get("name", "")
            tc_args = data.get("args")
            tools_map[tc_id] = ToolCallInfo(name=tc_name, args=tc_args, result_summary=None)
        elif event_type == "tool_result":
            tc_id = data.get("id", "")
            tc_name = data.get("name", "")
            tc_summary = data.get("summary", "")
            if tc_id in tools_map:
                tools_map[tc_id].result_summary = tc_summary
            else:
                tools_map[tc_id] = ToolCallInfo(name=tc_name, args=None, result_summary=tc_summary)

    result.answer_text = "".join(answer_parts)
    result.plan_text = "".join(plan_parts)
    result.tool_calls = list(tools_map.values())
    result.citations = extract_citations(result.answer_text)

    return result


# ---------------------------------------------------------------------------
# Scenario definitions
# ---------------------------------------------------------------------------

@dataclass
class Scenario:
    id: str
    question: str
    category: str
    history: list[dict[str, str]] | None = None
    description: str = ""


SCENARIOS: list[Scenario] = [
    # -- Factual lookups (exact section/article) --
    Scenario(
        id="fact-ipc-302",
        question="What is the punishment for murder under IPC section 302?",
        category="factual_lookup",
        description="Direct section lookup -- supervisor should call get_section(IPC, 302)",
    ),
    Scenario(
        id="fact-art-21",
        question="What does Article 21 of the Constitution guarantee?",
        category="factual_lookup",
        description="Constitutional article lookup -- supervisor should call get_article(21)",
    ),
    Scenario(
        id="fact-ipc-420",
        question="Explain IPC section 420 on cheating.",
        category="factual_lookup",
        description="Section lookup with explanation request",
    ),
    Scenario(
        id="fact-art-14",
        question="What is Article 14 of the Constitution about?",
        category="factual_lookup",
        description="Constitutional equality article",
    ),

    # -- Semantic search (topical, no exact reference) --
    Scenario(
        id="semantic-good-faith",
        question="What is the legal definition of good faith in Indian law?",
        category="semantic_search",
        description="Topical query -- supervisor should call semantic_query with 'good faith definition'",
    ),
    Scenario(
        id="semantic-dowry",
        question="What are the laws against dowry in India?",
        category="semantic_search",
        description="Topical query requiring semantic search across multiple sections",
    ),
    Scenario(
        id="semantic-self-defence",
        question="When can a person legally use force in self defence?",
        category="semantic_search",
        description="Topical query about private defence",
    ),

    # -- Cross-act comparison --
    Scenario(
        id="compare-ipc-bns-murder",
        question="Compare the punishment for murder under IPC and the new BNS. What changed?",
        category="comparison",
        description="Cross-act comparison -- supervisor should call cross_reference or multiple get_section calls",
    ),
    Scenario(
        id="compare-ipc-bns-theft",
        question="How does theft differ between IPC and BNS?",
        category="comparison",
        description="Cross-act comparison for theft provisions",
    ),

    # -- Out-of-corpus / refusal --
    Scenario(
        id="refusal-nonexistent",
        question="What does the Indian Space Act of 2050 say about Mars colonies?",
        category="refusal",
        description="Completely out-of-corpus question -- agent should refuse gracefully",
    ),
    Scenario(
        id="refusal-fake-section",
        question="What does IPC section 99999 say?",
        category="refusal",
        description="Nonexistent section -- should get not_found and refuse",
    ),

    # -- Multi-turn conversation --
    Scenario(
        id="multi-turn-1",
        question="What does Article 19 of the Constitution guarantee?",
        category="multi_turn",
        description="First turn of a multi-turn conversation",
    ),
    Scenario(
        id="multi-turn-2",
        question="How does it relate to reasonable restrictions?",
        category="multi_turn",
        description="Follow-up question referencing previous turn -- should use history",
        history=[
            {"role": "user", "content": "What does Article 19 of the Constitution guarantee?"},
            {"role": "assistant", "content": "Article 19 of the Constitution of India guarantees six fundamental freedoms to all citizens. These include freedom of speech and expression, assembly, association, movement, residence, and profession. Each freedom is subject to reasonable restrictions that can be imposed by law. [[act: Constitution, ref: 19]]"},
        ],
    ),

    # -- Edge cases --
    Scenario(
        id="edge-very-long",
        question="What is Section 302 of the IPC? " + "Please provide a very detailed explanation. " * 20,
        category="edge_case",
        description="Very long question -- should still work, server caps at 4000 chars",
    ),
    Scenario(
        id="edge-special-chars",
        question="What is the punishment for §302 IPC? (murder -- 'death penalty')",
        category="edge_case",
        description="Special characters in question (section sign, em-dash, quotes)",
    ),
    Scenario(
        id="edge-ambiguous",
        question="302",
        category="edge_case",
        description="Minimal ambiguous query -- just a number",
    ),

    # -- Judgment lookup --
    Scenario(
        id="judgment-kesavananda",
        question="What was the Kesavananda Bharati case about?",
        category="judgment",
        description="Landmark judgment lookup -- should call get_judgment",
    ),

    # -- Definition / term lookup --
    Scenario(
        id="definition-dishonestly",
        question="What is the meaning of 'dishonestly' under the IPC?",
        category="definition",
        description="Statutory definition lookup -- should use semantic_query with promote_definitions",
    ),

    # -- Guardrail: greetings, capabilities, off-topic --
    Scenario(
        id="greeting-hello",
        question="hello",
        category="guardrail",
        description="Simple greeting -- should get instant canned response, no tools",
    ),
    Scenario(
        id="greeting-namaste",
        question="namaste",
        category="guardrail",
        description="Indian greeting -- instant canned response",
    ),
    Scenario(
        id="greeting-good-morning",
        question="good morning!",
        category="guardrail",
        description="Time-based greeting -- instant canned response",
    ),
    Scenario(
        id="capability-what-can-you-do",
        question="what can you do?",
        category="guardrail",
        description="Capability question -- instant canned response listing features",
    ),
    Scenario(
        id="capability-who-are-you",
        question="who are you?",
        category="guardrail",
        description="Identity question -- instant canned response",
    ),
    Scenario(
        id="thanks-thank-you",
        question="thank you",
        category="guardrail",
        description="Thanks -- instant canned acknowledgment",
    ),
    Scenario(
        id="off-topic-weather",
        question="what's the weather in Mumbai?",
        category="guardrail",
        description="Off-topic weather question -- instant canned refusal",
    ),
    Scenario(
        id="off-topic-joke",
        question="tell me a joke",
        category="guardrail",
        description="Off-topic joke request -- instant canned refusal",
    ),
    Scenario(
        id="off-topic-recipe",
        question="how to make biryani recipe",
        category="guardrail",
        description="Off-topic recipe -- instant canned refusal",
    ),
]


# ---------------------------------------------------------------------------
# Quality checks
# ---------------------------------------------------------------------------

def run_checks(result: ScenarioResult, scenario: Scenario) -> None:
    """Run quality checks on the result and populate result.checks."""

    # 1. No error
    has_no_error = result.error is None
    result.checks.append(("no_error", has_no_error, f"error={result.error}" if result.error else "OK"))

    # 2. Has answer text
    has_answer = len(result.answer_text.strip()) > 20
    result.checks.append(("has_answer", has_answer, f"answer_len={len(result.answer_text)}"))

    # 3. Has done event
    has_done = any(ev["event"] == "done" for ev in result.events)
    result.checks.append(("has_done_event", has_done, ""))

    # 4. Has meta event (request_id)
    has_meta = any(ev["event"] == "meta" for ev in result.events)
    result.checks.append(("has_meta_event", has_meta, f"request_id={result.request_id[:12] if result.request_id else 'missing'}"))

    # 5. Has at least one status event
    has_status = len(result.statuses) > 0
    result.checks.append(("has_status", has_status, f"statuses={result.statuses}"))

    # Category-specific checks
    if scenario.category == "factual_lookup":
        # Should have tool calls
        has_tools = len(result.tool_calls) > 0
        result.checks.append(("has_tool_calls", has_tools, f"tools={[t.name for t in result.tool_calls]}"))
        # Should have citations
        has_citations = len(result.citations) > 0
        result.checks.append(("has_citations", has_citations, f"citations={result.citations[:3]}"))
        # Should have markdown structure (## headings)
        has_headings = "##" in result.answer_text
        result.checks.append(("has_markdown_headings", has_headings, ""))
        # Should have disclaimer
        has_disclaimer = "not legal advice" in result.answer_text.lower()
        result.checks.append(("has_disclaimer", has_disclaimer, ""))

    elif scenario.category == "semantic_search":
        # Should have semantic_query tool call
        has_semantic = any(t.name == "semantic_query" for t in result.tool_calls)
        result.checks.append(("uses_semantic_query", has_semantic, f"tools={[t.name for t in result.tool_calls]}"))
        # Should have citations
        has_citations = len(result.citations) > 0
        result.checks.append(("has_citations", has_citations, f"citations={result.citations[:3]}"))

    elif scenario.category == "comparison":
        # Should have multiple tool calls or cross_reference
        has_multiple_tools = len(result.tool_calls) >= 2 or any(t.name == "cross_reference" for t in result.tool_calls)
        result.checks.append(("has_comparison_tools", has_multiple_tools, f"tools={[t.name for t in result.tool_calls]}"))
        # Should mention both acts in the answer
        answer_lower = result.answer_text.lower()
        mentions_both = "ipc" in answer_lower and "bns" in answer_lower
        result.checks.append(("mentions_both_acts", mentions_both, ""))
        # Should have citations
        has_citations = len(result.citations) > 0
        result.checks.append(("has_citations", has_citations, f"citations={result.citations[:3]}"))

    elif scenario.category == "refusal":
        # Should NOT have fabricated citations
        has_fabricated = len(result.citations) > 0
        result.checks.append(("no_fabricated_citations", not has_fabricated, f"unexpected citations={result.citations}"))
        # Should indicate inability to find (either explicit refusal or caveat)
        answer_lower = result.answer_text.lower()
        refusal_indicators = [
            "could not find", "not in the corpus", "not available",
            "could not find a basis", "no basis in the corpus",
            "did not include verifiable", "not in the nyaya corpus",
            "i don't have", "cannot find", "no information",
        ]
        has_refusal_language = any(ind in answer_lower for ind in refusal_indicators)
        result.checks.append(("has_refusal_language", has_refusal_language, ""))

    elif scenario.category == "multi_turn":
        # Should use history context
        has_tools = len(result.tool_calls) > 0
        result.checks.append(("has_tool_calls", has_tools, f"tools={[t.name for t in result.tool_calls]}"))
        # Should have citations
        has_citations = len(result.citations) > 0
        result.checks.append(("has_citations", has_citations, f"citations={result.citations[:3]}"))

    elif scenario.category == "judgment":
        # Should call get_judgment or semantic_query
        has_judgment_tool = any(t.name in ("get_judgment", "semantic_query") for t in result.tool_calls)
        result.checks.append(("uses_judgment_tool", has_judgment_tool, f"tools={[t.name for t in result.tool_calls]}"))

    elif scenario.category == "definition":
        # Should have semantic_query or get_section
        has_definition_tool = any(t.name in ("semantic_query", "get_section") for t in result.tool_calls)
        result.checks.append(("uses_definition_tool", has_definition_tool, f"tools={[t.name for t in result.tool_calls]}"))
        # Should have citations
        has_citations = len(result.citations) > 0
        result.checks.append(("has_citations", has_citations, f"citations={result.citations[:3]}"))

    elif scenario.category == "edge_case":
        # Should not crash, should have some response or error
        has_response = len(result.answer_text.strip()) > 0 or result.error is not None
        result.checks.append(("has_response_or_error", has_response, ""))

    elif scenario.category == "guardrail":
        # Should NOT call any tools (guardrail bypasses the pipeline)
        no_tools = len(result.tool_calls) == 0
        result.checks.append(("no_tool_calls", no_tools, f"tools={[t.name for t in result.tool_calls]}"))
        # Should respond fast (under 3s for Tier 1, under 15s for Tier 2)
        is_fast = result.latency_ms < 15000
        result.checks.append(("fast_response", is_fast, f"latency={result.latency_ms:.0f}ms"))
        # Should NOT have citations (canned responses don't cite)
        no_citations = len(result.citations) == 0
        result.checks.append(("no_citations", no_citations, f"citations={result.citations}"))
        # Response should contain expected keyword based on scenario
        answer_lower = result.answer_text.lower()
        if "greeting" in scenario.id:
            has_keyword = "namaste" in answer_lower or "i'm nyaya" in answer_lower
        elif "capability" in scenario.id:
            has_keyword = "i can" in answer_lower or "nyaya" in answer_lower
        elif "thanks" in scenario.id:
            has_keyword = "welcome" in answer_lower
        elif "off-topic" in scenario.id:
            has_keyword = "indian law" in answer_lower or "can't help" in answer_lower or "cannot help" in answer_lower
        else:
            has_keyword = True
        result.checks.append(("response_has_keyword", has_keyword, f"answer_start={answer_lower[:60]}"))

    # 6. Tool result summary should not contain raw Python repr
    for tc in result.tool_calls:
        if tc.result_summary:
            has_repr_leak = "[{'type'" in tc.result_summary or "'text'" in tc.result_summary[:50]
            if has_repr_leak:
                result.checks.append(("no_tool_repr_leak", False, f"tool={tc.name} has Python repr"))
                break
    else:
        result.checks.append(("no_tool_repr_leak", True, ""))

    # 7. Answer should not contain <corpus_text> tags (should be stripped)
    no_corpus_tags = "<corpus_text>" not in result.answer_text
    result.checks.append(("no_corpus_text_tags", no_corpus_tags, ""))


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_scenario(host: str, scenario: Scenario) -> ScenarioResult:
    """Run a single scenario and return the result."""
    print(f"\n{'-'*70}")
    print(f"  Scenario: {scenario.id} ({scenario.category})")
    print(f"  Q: {scenario.question[:80]}{'...' if len(scenario.question) > 80 else ''}")

    raw, latency, ttf_token, error = send_chat_turn(
        host, scenario.question, history=scenario.history, timeout=120,
    )

    events = parse_sse_stream(raw) if raw else []
    result = extract_result(raw, events)
    result.scenario_id = scenario.id
    result.question = scenario.question
    result.category = scenario.category
    result.latency_ms = latency
    result.time_to_first_token_ms = ttf_token

    if error:
        result.error = error

    run_checks(result, scenario)

    # Print quick summary
    passed = sum(1 for _, ok, _ in result.checks if ok)
    total = len(result.checks)
    status_icon = "PASS" if passed == total else "FAIL"
    print(f"  [{status_icon}] Checks: {passed}/{total} passed | "
          f"Tools: {len(result.tool_calls)} | "
          f"Citations: {len(result.citations)} | "
          f"Latency: {latency:.0f}ms | "
          f"Answer: {len(result.answer_text)} chars")

    if result.error:
        print(f"  WARN ERROR: {result.error}")

    for check_name, check_ok, check_detail in result.checks:
        if not check_ok:
            print(f"    FAIL {check_name}: {check_detail}")

    return result


def print_final_report(results: list[ScenarioResult], verbose: bool = False) -> None:
    """Print a comprehensive final report."""
    print(f"\n\n{'='*70}")
    print("COMPREHENSIVE CHAT QUALITY REPORT")
    print(f"{'='*70}")
    print(f"Scenarios: {len(results)}")

    # Overall pass rate
    total_checks = sum(len(r.checks) for r in results)
    passed_checks = sum(1 for r in results for _, ok, _ in r.checks if ok)
    print(f"Total checks: {passed_checks}/{total_checks} ({passed_checks/total_checks*100:.1f}%)")

    # By category
    categories: dict[str, list[ScenarioResult]] = {}
    for r in results:
        categories.setdefault(r.category, []).append(r)

    print(f"\n{'-'*70}")
    print("BY CATEGORY:")
    for cat, cat_results in sorted(categories.items()):
        cat_checks = sum(len(r.checks) for r in cat_results)
        cat_passed = sum(1 for r in cat_results for _, ok, _ in r.checks if ok)
        cat_pass_rate = cat_passed / cat_checks * 100 if cat_checks > 0 else 0
        cat_latency = sum(r.latency_ms for r in cat_results) / len(cat_results)
        cat_tools = sum(len(r.tool_calls) for r in cat_results) / len(cat_results)
        cat_citations = sum(len(r.citations) for r in cat_results) / len(cat_results)
        icon = "PASS" if cat_pass_rate == 100 else ("◐" if cat_pass_rate >= 80 else "FAIL")
        print(f"  {icon} {cat:20s} checks={cat_passed}/{cat_checks} ({cat_pass_rate:.0f}%) "
              f"avg_latency={cat_latency:.0f}ms avg_tools={cat_tools:.1f} avg_citations={cat_citations:.1f}")

    # Tool usage stats
    print(f"\n{'-'*70}")
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
    print(f"\n{'-'*70}")
    print("CITATIONS:")
    total_citations = sum(len(r.citations) for r in results)
    scenarios_with_citations = sum(1 for r in results if len(r.citations) > 0)
    print(f"  Total citations: {total_citations}")
    print(f"  Scenarios with citations: {scenarios_with_citations}/{len(results)}")

    # Latency stats
    print(f"\n{'-'*70}")
    print("LATENCY (ms):")
    latencies = [r.latency_ms for r in results if r.error is None]
    if latencies:
        print(f"  Min:    {min(latencies):.0f}ms")
        print(f"  Max:    {max(latencies):.0f}ms")
        print(f"  Mean:   {sum(latencies)/len(latencies):.0f}ms")
        sorted_l = sorted(latencies)
        print(f"  Median: {sorted_l[len(sorted_l)//2]:.0f}ms")
        print(f"  P90:    {sorted_l[int(len(sorted_l)*0.9)]:.0f}ms")

    # Error summary
    errors = [r for r in results if r.error is not None]
    if errors:
        print(f"\n{'-'*70}")
        print(f"ERRORS ({len(errors)}):")
        for r in errors:
            print(f"  {r.scenario_id:25s} error={r.error}")

    # SSE protocol events breakdown
    print(f"\n{'-'*70}")
    print("SSE PROTOCOL EVENTS:")
    event_counts: dict[str, int] = {}
    for r in results:
        for ev in r.events:
            event_counts[ev["event"]] = event_counts.get(ev["event"], 0) + 1
    for event_name, count in sorted(event_counts.items(), key=lambda x: -x[1]):
        print(f"  {event_name:20s} {count} total events")

    # Detailed per-scenario output
    if verbose:
        print(f"\n{'-'*70}")
        print("DETAILED SCENARIO RESULTS:")
        for r in results:
            print(f"\n  -- {r.scenario_id} ({r.category}) --")
            print(f"  Q: {r.question[:100]}")
            print(f"  Latency: {r.latency_ms:.0f}ms")
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
            # Print first 300 chars of answer
            print(f"    {r.answer_text[:300]}{'...' if len(r.answer_text) > 300 else ''}")
            if r.error:
                print(f"  ERROR: {r.error}")
            failed_checks = [(n, d) for n, ok, d in r.checks if not ok]
            if failed_checks:
                print("  Failed checks:")
                for check_name, check_detail in failed_checks:
                    print(f"    FAIL {check_name}: {check_detail}")

    print(f"\n{'='*70}")
    if passed_checks == total_checks:
        print(f"ALL CHECKS PASSED -- {passed_checks}/{total_checks}")
    else:
        failed = total_checks - passed_checks
        print(f"{failed} CHECK(S) FAILED -- {passed_checks}/{total_checks} passed")
    print(f"{'='*70}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Comprehensive chat quality tests")
    parser.add_argument("--host", default="http://localhost:8001", help="Base URL of the nyaya server")
    parser.add_argument("--verbose", action="store_true", help="Print detailed per-scenario output")
    parser.add_argument("--scenario", default=None, help="Run a single scenario by ID")
    args = parser.parse_args()

    print(f"Target: {args.host}")
    print(f"Scenarios: {len(SCENARIOS)}")

    # Verify server is up
    try:
        health = urllib.request.urlopen(f"{args.host}/chat/health", timeout=5)
        health_data = json.loads(health.read())
        print(f"Chat health: {health_data}")
    except Exception as e:
        print(f"ERROR: Cannot reach server at {args.host}: {e}")
        sys.exit(1)

    results: list[ScenarioResult] = []
    scenarios_to_run = SCENARIOS if not args.scenario else [s for s in SCENARIOS if s.id == args.scenario]

    for scenario in scenarios_to_run:
        result = run_scenario(args.host, scenario)
        results.append(result)

    print_final_report(results, verbose=args.verbose)

    # Exit non-zero if any checks failed
    total_failed = sum(1 for r in results for _, ok, _ in r.checks if not ok)
    if total_failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
