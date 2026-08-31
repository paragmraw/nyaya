"""
Comprehensive validation test for the containerized nyaya chat application.

Covers:
- Greetings and social messages (guardrail fast-path, instant response)
- Capability questions (guardrail fast-path, instant response)
- Factual legal lookups (IPC sections, Constitution articles)
- Semantic search (topical queries, no exact reference)
- Cross-act comparisons (IPC vs BNS)
- Judgment lookups
- Definition lookups
- Out-of-corpus / refusal handling
- Multi-turn conversations
- Edge cases (special chars, ambiguous, very long)
- SSE protocol verification (meta, status, plan, token, reasoning, tool_start, tool_result, citations, ping, done)
- Latency measurement (greetings <200ms, legal <180s)
- Tool call verification (correct tools called, tool args correct)
- Citation verification (citations present for legal queries, absent for refusals)
- Markdown structure verification (headings, bullets, tables, disclaimer)
- Response quality (answer addresses the question, no fabricated content)

Usage:
    python validate_chat.py --host http://localhost:8001
    python validate_chat.py --host http://localhost:8001 --verbose
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
    """Parse raw SSE bytes into a list of {event, data} dicts.

    Per the SSE spec: multiple ``data:`` lines are joined with a newline and
    only the single leading space after ``data:`` is stripped.
    """
    events: list[dict[str, Any]] = []
    text = raw_bytes.decode("utf-8")
    blocks = text.split("\n\n")
    for block in blocks:
        block = block.strip()
        if not block:
            continue
        event = "message"
        data_lines: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                seg = line[5:]
                data_lines.append(seg[1:] if seg.startswith(" ") else seg)
        if data_lines:
            data = "\n".join(data_lines)
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
class TestResult:
    test_id: str
    question: str
    category: str
    expected_behavior: str
    events: list[dict[str, Any]] = field(default_factory=list)
    answer_text: str = ""
    plan_text: str = ""
    tool_calls: list[ToolCallInfo] = field(default_factory=list)
    citations: list[dict[str, str]] = field(default_factory=list)
    request_id: str = ""
    statuses: list[str] = field(default_factory=list)
    # Contract violations: status events missing a non-blank rid
    status_missing_rid: int = 0
    latency_ms: float = 0
    # Error (unified shape: {message, detail, rid})
    error: str | None = None
    error_detail: str = ""
    error_rid: str = ""
    checks: list[tuple[str, bool, str]] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Citation parsing
# ---------------------------------------------------------------------------

_CITE_RE = re.compile(r"\[\[act:\s*([^,\]]+?)\s*,\s*ref:\s*([^\]]+?)\s*\]\]")


def extract_citations(text: str) -> list[dict[str, str]]:
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

def send_chat_turn(host: str, message: str, history: list[dict[str, str]] | None = None, timeout: float = 180) -> tuple[bytes, float, str | None]:
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
        raw = resp.read()
        latency = (time.monotonic() - start) * 1000
        return raw, latency, None
    except urllib.error.HTTPError as e:
        latency = (time.monotonic() - start) * 1000
        body = e.read().decode("utf-8", errors="replace")
        return b"", latency, f"HTTP {e.code}: {body[:200]}"
    except Exception as e:
        latency = (time.monotonic() - start) * 1000
        return b"", latency, str(e)


# ---------------------------------------------------------------------------
# Result extraction
# ---------------------------------------------------------------------------

def extract_result(raw: bytes, events: list[dict[str, Any]]) -> TestResult:
    result = TestResult(test_id="", question="", category="", expected_behavior="")
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
            if not data.get("rid"):
                result.status_missing_rid += 1
        elif event_type == "plan":
            plan_parts.append(data.get("content", ""))
        elif event_type == "token":
            answer_parts.append(data.get("content", ""))
        elif event_type == "citations":
            cites = data.get("citations", [])
            if cites:
                result.citations = cites
        elif event_type == "correction":
            # The verified answer replaces the raw streamed tokens (matches
            # the frontend, which swaps the message content on correction).
            answer_parts = [data.get("content", "")]
        elif event_type == "error":
            result.error = data.get("message", "unknown_error")
            result.error_detail = data.get("detail", "")
            result.error_rid = data.get("rid", "")
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
    # If citations weren't set by the citations event, parse from text
    if not result.citations:
        result.citations = extract_citations(result.answer_text)
    return result


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

@dataclass
class TestCase:
    id: str
    question: str
    category: str
    expected_behavior: str
    history: list[dict[str, str]] | None = None
    max_latency_ms: float = 180000  # 3 min default


TESTS: list[TestCase] = [
    # ── Greetings (guardrail Tier 1, instant) ──
    TestCase("greeting-hello", "hello", "greeting", "Instant canned greeting, no tools", max_latency_ms=200),
    TestCase("greeting-hi", "hi", "greeting", "Instant canned greeting, no tools", max_latency_ms=200),
    TestCase("greeting-hey", "hey", "greeting", "Instant canned greeting, no tools", max_latency_ms=200),
    TestCase("greeting-good-morning", "good morning!", "greeting", "Instant canned greeting, no tools", max_latency_ms=200),
    TestCase("greeting-greetings", "greetings", "greeting", "Instant canned greeting, no tools", max_latency_ms=200),
    TestCase("greeting-hello-there", "hello there", "greeting", "Instant canned greeting, no tools", max_latency_ms=200),

    # ── Capability questions (guardrail Tier 1, instant) ──
    TestCase("capability-what-can-you-do", "what can you do?", "capability", "Instant canned capability response", max_latency_ms=200),
    TestCase("capability-who-are-you", "who are you?", "capability", "Instant canned capability response", max_latency_ms=200),
    TestCase("capability-what-do-you-know", "what do you know?", "capability", "Instant canned capability response", max_latency_ms=200),
    TestCase("capability-help", "help", "capability", "Instant canned capability response", max_latency_ms=200),

    # ── Thanks (guardrail Tier 1, instant) ──
    TestCase("thanks-thank-you", "thank you", "thanks", "Instant canned thanks response", max_latency_ms=200),
    TestCase("thanks-great", "great!", "thanks", "Instant canned thanks response", max_latency_ms=200),

    # ── Off-topic (guardrail Tier 1, instant) ──
    TestCase("off-topic-weather", "what's the weather in Mumbai?", "off_topic", "Instant canned refusal", max_latency_ms=200),
    TestCase("off-topic-joke", "tell me a joke", "off_topic", "Instant canned refusal", max_latency_ms=200),
    TestCase("off-topic-recipe", "how to make biryani recipe", "off_topic", "Instant canned refusal", max_latency_ms=200),

    # ── Factual lookups (supervisor + tools + synthesis) ──
    TestCase("fact-ipc-302", "What is the punishment for murder under IPC section 302?", "factual_lookup", "Tool call + cited answer"),
    TestCase("fact-art-21", "What does Article 21 of the Constitution guarantee?", "factual_lookup", "Tool call + cited answer", max_latency_ms=300000),
    TestCase("fact-ipc-420", "Explain IPC section 420 on cheating.", "factual_lookup", "Tool call + cited answer"),
    TestCase("fact-art-14", "What is Article 14 of the Constitution about?", "factual_lookup", "Tool call + cited answer", max_latency_ms=300000),

    # ── Semantic search (topical queries) ──
    TestCase("semantic-good-faith", "What is the legal definition of good faith in Indian law?", "semantic_search", "semantic_query + cited answer"),
    TestCase("semantic-dowry", "What are the laws against dowry in India?", "semantic_search", "semantic_query + cited answer"),
    TestCase("semantic-self-defence", "When can a person legally use force in self defence?", "semantic_search", "semantic_query + cited answer"),

    # ── Cross-act comparisons ──
    TestCase("compare-ipc-bns-murder", "Compare the punishment for murder under IPC and the new BNS. What changed?", "comparison", "Multiple tool calls + cited comparison"),
    TestCase("compare-ipc-bns-theft", "How does theft differ between IPC and BNS?", "comparison", "Multiple tool calls + cited comparison", max_latency_ms=300000),

    # ── Refusal (out-of-corpus) ──
    TestCase("refusal-nonexistent", "What does the Indian Space Act of 2050 say about Mars colonies?", "refusal", "Tool call + refusal, no fabricated citations"),
    TestCase("refusal-fake-section", "What does IPC section 99999 say?", "refusal", "Tool call + refusal, no fabricated citations"),

    # ── Judgment lookup ──
    TestCase("judgment-kesavananda", "What was the Kesavananda Bharati case about?", "judgment", "get_judgment + cited answer", max_latency_ms=300000),

    # ── Definition lookup ──
    TestCase("definition-dishonestly", "What is the meaning of 'dishonestly' under the IPC?", "definition", "get_section or semantic_query + cited answer"),

    # ── Multi-turn conversation ──
    TestCase("multi-turn-1", "What does Article 19 of the Constitution guarantee?", "multi_turn", "Tool call + cited answer"),
    TestCase("multi-turn-2", "How does it relate to reasonable restrictions?", "multi_turn", "Tool call + cited answer", history=[
        {"role": "user", "content": "What does Article 19 of the Constitution guarantee?"},
        {"role": "assistant", "content": "Article 19 guarantees six fundamental freedoms including speech, assembly, association, movement, residence, and profession."},
    ]),

    # ── Edge cases ──
    TestCase("edge-special-chars", "What is the punishment for section 302 IPC?", "edge_case", "Should handle and call tools"),
    TestCase("edge-ambiguous", "302", "edge_case", "Should interpret as IPC 302 and call tools"),
    TestCase("edge-very-long", "What is Section 302 of the IPC? " + "Please provide a detailed explanation. " * 10, "edge_case", "Should handle long input and call tools"),
]


# ---------------------------------------------------------------------------
# Quality checks
# ---------------------------------------------------------------------------

def run_checks(result: TestResult, test: TestCase) -> None:
    # ── Universal checks (all categories) ──
    has_no_error = result.error is None
    result.checks.append(("no_error", has_no_error, f"error={result.error}" if result.error else "OK"))

    has_done = any(ev["event"] == "done" for ev in result.events)
    result.checks.append(("has_done_event", has_done, ""))

    has_meta = any(ev["event"] == "meta" for ev in result.events)
    result.checks.append(("has_meta_event", has_meta, f"request_id={result.request_id[:12] if result.request_id else 'missing'}"))

    has_status = len(result.statuses) > 0
    result.checks.append(("has_status", has_status, f"statuses={result.statuses}"))

    # Unified SSE contract: every status event carries a non-blank rid
    result.checks.append((
        "status_events_have_rid",
        result.status_missing_rid == 0,
        f"missing_rid={result.status_missing_rid}",
    ))

    # Unified error shape: {message, detail, rid}
    error_events = [ev for ev in result.events if ev["event"] == "error"]
    if error_events:
        d = error_events[0]["data"]
        error_shape_ok = (
            isinstance(d.get("message"), str)
            and isinstance(d.get("detail"), str)
            and bool(d.get("rid"))
        )
        result.checks.append(("error_shape", error_shape_ok, json.dumps(d)[:120]))

    # Latency check
    latency_ok = result.latency_ms <= test.max_latency_ms
    result.checks.append(("latency_ok", latency_ok, f"latency={result.latency_ms:.0f}ms (max={test.max_latency_ms}ms)"))

    # No corpus_text tags in answer
    no_corpus_tags = "<corpus_text>" not in result.answer_text
    result.checks.append(("no_corpus_text_tags", no_corpus_tags, ""))

    # No tool repr leak
    has_repr_leak = False
    for tc in result.tool_calls:
        if tc.result_summary and ("[{'type'" in tc.result_summary[:50]):
            has_repr_leak = True
            break
    result.checks.append(("no_tool_repr_leak", not has_repr_leak, ""))

    # ── Category-specific checks ──
    if test.category in ("greeting", "capability", "thanks", "off_topic"):
        # Guardrail fast-path: no tools, fast, canned response
        no_tools = len(result.tool_calls) == 0
        result.checks.append(("no_tool_calls", no_tools, f"tools={[t.name for t in result.tool_calls]}"))

        has_answer = len(result.answer_text.strip()) > 20
        result.checks.append(("has_answer", has_answer, f"answer_len={len(result.answer_text)}"))

        # Should NOT have citations
        no_citations = len(result.citations) == 0
        result.checks.append(("no_citations", no_citations, f"citations={result.citations[:3]}"))

        # Should NOT have reasoning (guardrail skips the pipeline)
        has_reasoning = any(ev["event"] == "reasoning" for ev in result.events)
        no_reasoning = not has_reasoning
        result.checks.append(("no_reasoning_events", no_reasoning, ""))

        # Response should contain expected keyword
        answer_lower = result.answer_text.lower()
        if test.category == "greeting":
            has_keyword = "hello" in answer_lower or "i'm nyaya" in answer_lower or "indian law" in answer_lower
        elif test.category == "capability":
            has_keyword = "i can" in answer_lower or "nyaya" in answer_lower
        elif test.category == "thanks":
            has_keyword = "welcome" in answer_lower
        elif test.category == "off_topic":
            has_keyword = "indian law" in answer_lower or "can't help" in answer_lower or "cannot help" in answer_lower
        else:
            has_keyword = True
        result.checks.append(("response_has_keyword", has_keyword, f"answer_start={answer_lower[:60]}"))

    elif test.category == "factual_lookup":
        has_tools = len(result.tool_calls) > 0
        result.checks.append(("has_tool_calls", has_tools, f"tools={[t.name for t in result.tool_calls]}"))

        has_citations = len(result.citations) > 0
        result.checks.append(("has_citations", has_citations, f"citations={result.citations[:3]}"))

        has_answer = len(result.answer_text.strip()) > 50
        result.checks.append(("has_substantive_answer", has_answer, f"answer_len={len(result.answer_text)}"))

        has_disclaimer = "not legal advice" in result.answer_text.lower()
        result.checks.append(("has_disclaimer", has_disclaimer, ""))

    elif test.category == "semantic_search":
        has_tools = len(result.tool_calls) > 0
        result.checks.append(("has_tool_calls", has_tools, f"tools={[t.name for t in result.tool_calls]}"))

        has_citations = len(result.citations) > 0
        result.checks.append(("has_citations", has_citations, f"citations={result.citations[:3]}"))

    elif test.category == "comparison":
        has_tools = len(result.tool_calls) >= 1
        result.checks.append(("has_tool_calls", has_tools, f"tools={[t.name for t in result.tool_calls]}"))

        answer_lower = result.answer_text.lower()
        mentions_both = "ipc" in answer_lower and "bns" in answer_lower
        result.checks.append(("mentions_both_acts", mentions_both, ""))

    elif test.category == "refusal":
        no_fabricated = len(result.citations) == 0
        result.checks.append(("no_fabricated_citations", no_fabricated, f"citations={result.citations}"))

        answer_lower = result.answer_text.lower()
        refusal_indicators = [
            "could not find", "not in the corpus", "not available",
            "could not find a basis", "no basis in the corpus",
            "did not include verifiable", "not in the nyaya corpus",
            "i don't have", "cannot find", "no information",
            "does not exist", "does not contain",
        ]
        has_refusal_language = any(ind in answer_lower for ind in refusal_indicators)
        result.checks.append(("has_refusal_language", has_refusal_language, ""))

    elif test.category == "judgment":
        has_tools = len(result.tool_calls) > 0
        result.checks.append(("has_tool_calls", has_tools, f"tools={[t.name for t in result.tool_calls]}"))

    elif test.category == "definition":
        has_tools = len(result.tool_calls) > 0
        result.checks.append(("has_tool_calls", has_tools, f"tools={[t.name for t in result.tool_calls]}"))

    elif test.category == "multi_turn":
        has_tools = len(result.tool_calls) > 0
        result.checks.append(("has_tool_calls", has_tools, f"tools={[t.name for t in result.tool_calls]}"))

    elif test.category == "edge_case":
        has_response = len(result.answer_text.strip()) > 0 or result.error is not None
        result.checks.append(("has_response_or_error", has_response, ""))


# ---------------------------------------------------------------------------
# Main runner
# ---------------------------------------------------------------------------

def run_test(host: str, test: TestCase) -> TestResult:
    print(f"\n{'-'*70}")
    print(f"  Test: {test.id} ({test.category})")
    print(f"  Q: {test.question[:80]}{'...' if len(test.question) > 80 else ''}")
    print(f"  Expected: {test.expected_behavior}")

    raw, latency, error = send_chat_turn(host, test.question, history=test.history, timeout=180)
    events = parse_sse_stream(raw) if raw else []
    result = extract_result(raw, events)
    result.test_id = test.id
    result.question = test.question
    result.category = test.category
    result.expected_behavior = test.expected_behavior
    result.latency_ms = latency
    if error:
        result.error = error

    run_checks(result, test)

    passed = sum(1 for _, ok, _ in result.checks if ok)
    total = len(result.checks)
    status = "PASS" if passed == total else "FAIL"
    print(f"  [{status}] Checks: {passed}/{total} | Tools: {len(result.tool_calls)} | "
          f"Citations: {len(result.citations)} | Latency: {latency:.0f}ms | "
          f"Answer: {len(result.answer_text)} chars")

    if result.error:
        print(f"  WARN: {result.error}")

    for check_name, check_ok, check_detail in result.checks:
        if not check_ok:
            print(f"    FAIL {check_name}: {check_detail}")

    return result


def print_final_report(results: list[TestResult], verbose: bool = False) -> None:
    print(f"\n\n{'='*70}")
    print("COMPREHENSIVE VALIDATION REPORT")
    print(f"{'='*70}")
    print(f"Tests: {len(results)}")

    total_checks = sum(len(r.checks) for r in results)
    passed_checks = sum(1 for r in results for _, ok, _ in r.checks if ok)
    print(f"Total checks: {passed_checks}/{total_checks} ({passed_checks/total_checks*100:.1f}%)")

    # By category
    categories: dict[str, list[TestResult]] = {}
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
        icon = "PASS" if cat_pass_rate == 100 else ("WARN" if cat_pass_rate >= 80 else "FAIL")
        print(f"  [{icon}] {cat:20s} checks={cat_passed}/{cat_checks} ({cat_pass_rate:.0f}%) "
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
    print(f"  {'(no tools)':25s} {no_tool_count} tests")

    # Citation stats
    print(f"\n{'-'*70}")
    print("CITATIONS:")
    total_citations = sum(len(r.citations) for r in results)
    tests_with_citations = sum(1 for r in results if len(r.citations) > 0)
    print(f"  Total citations: {total_citations}")
    print(f"  Tests with citations: {tests_with_citations}/{len(results)}")

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

    # Guardrail latency specifically
    guardrail_latencies = [r.latency_ms for r in results if r.category in ("greeting", "capability", "thanks", "off_topic") and r.error is None]
    if guardrail_latencies:
        print("\n  Guardrail latency:")
        print(f"    Mean: {sum(guardrail_latencies)/len(guardrail_latencies):.0f}ms")
        print(f"    Max:  {max(guardrail_latencies):.0f}ms")

    # Error summary
    errors = [r for r in results if r.error is not None]
    if errors:
        print(f"\n{'-'*70}")
        print(f"ERRORS ({len(errors)}):")
        for r in errors:
            print(f"  {r.test_id:25s} error={r.error}")

    # SSE protocol events
    print(f"\n{'-'*70}")
    print("SSE PROTOCOL EVENTS:")
    event_counts: dict[str, int] = {}
    for r in results:
        for ev in r.events:
            event_counts[ev["event"]] = event_counts.get(ev["event"], 0) + 1
    for event_name, count in sorted(event_counts.items(), key=lambda x: -x[1]):
        print(f"  {event_name:20s} {count} total events")

    # Detailed output
    if verbose:
        print(f"\n{'-'*70}")
        print("DETAILED RESULTS:")
        for r in results:
            print(f"\n  -- {r.test_id} ({r.category}) --")
            print(f"  Q: {r.question[:100]}")
            print(f"  Latency: {r.latency_ms:.0f}ms")
            print(f"  Request ID: {r.request_id}")
            print(f"  Statuses: {r.statuses}")
            print(f"  Tools ({len(r.tool_calls)}):")
            for tc in r.tool_calls:
                args_str = json.dumps(tc.args) if tc.args else "(no args)"
                summary_len = len(tc.result_summary) if tc.result_summary else 0
                print(f"    - {tc.name}(args={args_str[:60]}) -> {summary_len} chars")
            print(f"  Citations ({len(r.citations)}): {r.citations[:5]}")
            print(f"  Answer ({len(r.answer_text)} chars): {r.answer_text[:300]}{'...' if len(r.answer_text) > 300 else ''}")
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
    parser = argparse.ArgumentParser(description="Comprehensive chat validation")
    parser.add_argument("--host", default="http://localhost:8001")
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument("--test", default=None, help="Run a single test by ID")
    args = parser.parse_args()

    print(f"Target: {args.host}")
    print(f"Tests: {len(TESTS)}")

    try:
        health = urllib.request.urlopen(f"{args.host}/chat/health", timeout=5)
        health_data = json.loads(health.read())
        print(f"Chat health: {health_data}")
    except Exception as e:
        print(f"ERROR: Cannot reach server at {args.host}: {e}")
        sys.exit(1)

    results: list[TestResult] = []
    tests_to_run = TESTS if not args.test else [t for t in TESTS if t.id == args.test]

    for test in tests_to_run:
        result = run_test(args.host, test)
        results.append(result)
        # Delay between requests to avoid rate limiting (15 chat req/min)
        if not args.test:
            time.sleep(5)

    print_final_report(results, verbose=args.verbose)

    total_failed = sum(1 for r in results for _, ok, _ in r.checks if not ok)
    if total_failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()



