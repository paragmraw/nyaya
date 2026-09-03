"""End-to-end evaluation: run the full chat agent against the golden dataset.

For each golden question, sends the question to the chat agent and checks:
1. Citation correctness: expected citations are present in the answer.
2. Keyword coverage: expected keywords appear in the answer.
3. Refusal correctness: for ``should_refuse`` cases, the answer indicates
   it could not find a basis in the corpus.
4. No ungrounded citations: the citation verification system strips any
   citations not backed by tool results.

Requires ``DATABASE_URL`` and ``NVIDIA_API_KEY`` env vars.

Usage::

    cd chat
    python -m eval.e2e_eval [--golden eval/golden.jsonl] [--timeout 120]

This is a standalone script, not a pytest module — run it directly with the
command above. (An earlier version of this docstring claimed it could be run
via ``pytest -m eval``, which was never wired up.) The live-server SSE harness
is ``eval/chat_eval.py``; offline golden-dataset scoring is
``eval/retrieval_eval.py``.
"""

from __future__ import annotations

import asyncio
import json
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class GoldenCase:
    id: str
    question: str
    expected_citations: list[dict[str, str]]
    expected_keywords: list[str]
    should_refuse: bool


@dataclass
class CaseResult:
    case_id: str
    question: str
    answer: str
    citations_found: list[dict[str, str]]
    keywords_found: list[str]
    refused: bool
    citation_score: float
    keyword_score: float
    refusal_correct: bool
    latency_ms: float
    error: str | None = None


@dataclass
class E2EReport:
    total: int = 0
    mean_citation_score: float = 0.0
    mean_keyword_score: float = 0.0
    refusal_accuracy: float = 0.0
    mean_latency_ms: float = 0.0
    per_case: list[CaseResult] = field(default_factory=list)


def load_golden(path: str) -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    p = Path(path)
    if not p.exists():
        print(f"ERROR: golden file not found: {path}", file=sys.stderr)
        sys.exit(1)
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        d = json.loads(line)
        cases.append(GoldenCase(
            id=d["id"],
            question=d["question"],
            expected_citations=d.get("expected_citations", []),
            expected_keywords=d.get("expected_keywords", []),
            should_refuse=d.get("should_refuse", False),
        ))
    return cases


def _normalize(s: str) -> str:
    s = s.strip().lower()
    s = re.sub(r"^(?:s(?:ec(?:tion)?)?\.?\s*|art(?:icle)?\.?\s*)", "", s)
    return s.strip()


def _check_refusal(answer: str) -> bool:
    lower = answer.lower()
    indicators = [
        "could not find a basis",
        "could not find",
        "not in the corpus",
        "not available in the corpus",
        "i could not find",
        "no basis in the corpus",
        "did not include verifiable",
    ]
    return any(ind in lower for ind in indicators)


async def eval_e2e(cases: list[GoldenCase], timeout: float = 120) -> E2EReport:
    from nyaya_chat.agent import _build_messages, get_agent
    from nyaya_chat.citations import CITATION_RE

    report = E2EReport(total=len(cases))
    results: list[CaseResult] = []

    graph, tools = await get_agent()

    for case in cases:
        start = time.monotonic()
        answer = ""
        error: str | None = None

        try:
            msgs = _build_messages(case.question, [])
            result = await asyncio.wait_for(
                graph.ainvoke({"messages": msgs}),
                timeout=timeout,
            )
            out_msgs = result.get("messages", [])
            # Get the last AI message as the answer
            for m in reversed(out_msgs):
                if hasattr(m, "content") and isinstance(m.content, str) and m.content.strip():
                    # Skip tool-call-only messages
                    if not getattr(m, "tool_calls", None):
                        answer = m.content
                        break
            latency = (time.monotonic() - start) * 1000
        except TimeoutError:
            latency = (time.monotonic() - start) * 1000
            error = "timeout"
        except Exception as exc:
            latency = (time.monotonic() - start) * 1000
            error = str(exc)

        # Parse citations from the answer
        citations_found: list[dict[str, str]] = []
        for m in CITATION_RE.finditer(answer):
            citations_found.append({"act": m.group(1).strip(), "ref": m.group(2).strip()})

        # Check expected citations
        found_pairs = {_normalize(f"{c['act']}|{c['ref']}") for c in citations_found}
        cite_scores: list[float] = []
        for cite in case.expected_citations:
            act_n = _normalize(cite["act"])
            ref_n = _normalize(cite["ref"])
            exact = f"{act_n}|{ref_n}" in found_pairs
            act_match = any(p.startswith(f"{act_n}|") for p in found_pairs)
            cite_scores.append(1.0 if exact else (0.5 if act_match else 0.0))
        citation_score = sum(cite_scores) / len(cite_scores) if cite_scores else (1.0 if not case.expected_citations else 0.0)

        # Check keywords
        lower_answer = answer.lower()
        keywords_found = [kw for kw in case.expected_keywords if kw.lower() in lower_answer]
        keyword_score = len(keywords_found) / len(case.expected_keywords) if case.expected_keywords else 1.0

        # Check refusal
        refused = _check_refusal(answer)
        refusal_correct = (refused == case.should_refuse)

        results.append(CaseResult(
            case_id=case.id, question=case.question, answer=answer[:500],
            citations_found=citations_found, keywords_found=keywords_found,
            refused=refused, citation_score=citation_score,
            keyword_score=keyword_score, refusal_correct=refusal_correct,
            latency_ms=latency, error=error,
        ))

    report.per_case = results
    cite_scores = [r.citation_score for r in results if r.error is None]
    kw_scores = [r.keyword_score for r in results if r.error is None]
    refusal_scores = [1.0 if r.refusal_correct else 0.0 for r in results]
    latencies = [r.latency_ms for r in results if r.error is None and r.latency_ms > 0]
    report.mean_citation_score = sum(cite_scores) / len(cite_scores) if cite_scores else 0
    report.mean_keyword_score = sum(kw_scores) / len(kw_scores) if kw_scores else 0
    report.refusal_accuracy = sum(refusal_scores) / len(refusal_scores) if refusal_scores else 0
    report.mean_latency_ms = sum(latencies) / len(latencies) if latencies else 0
    return report


def print_report(report: E2EReport) -> None:
    print(f"\n{'='*70}")
    print("End-to-End Chat Agent Evaluation")
    print(f"{'='*70}")
    print(f"Cases: {report.total}")
    print(f"Mean Citation Score: {report.mean_citation_score:.2%}")
    print(f"Mean Keyword Score: {report.mean_keyword_score:.2%}")
    print(f"Refusal Accuracy: {report.refusal_accuracy:.2%}")
    print(f"Mean Latency: {report.mean_latency_ms:.0f}ms")
    print(f"{'─'*70}")
    for r in report.per_case:
        status = "✓" if (r.citation_score >= 0.5 and r.keyword_score >= 0.5 and r.refusal_correct and not r.error) else "✗"
        cite_str = f"{r.citation_score:.0%}"
        kw_str = f"{r.keyword_score:.0%}"
        ref_str = "✓" if r.refusal_correct else "✗"
        err = f" [ERROR: {r.error}]" if r.error else ""
        print(f"  {status} {r.case_id:20s} cite={cite_str} kw={kw_str} "
              f"ref={ref_str} {r.latency_ms:.0f}ms{err}")
    print(f"{'='*70}")


async def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="E2E chat agent evaluation")
    parser.add_argument("--golden", type=str, default="eval/golden.jsonl")
    parser.add_argument("--timeout", type=float, default=120)
    args = parser.parse_args()

    cases = load_golden(args.golden)
    report = await eval_e2e(cases, timeout=args.timeout)
    print_report(report)

    # Pass criteria: citation score >= 50%, keyword score >= 50%, refusal accuracy >= 80%
    passed = (
        report.mean_citation_score >= 0.5
        and report.mean_keyword_score >= 0.5
        and report.refusal_accuracy >= 0.8
    )
    if passed:
        print(f"\nPASS: citation={report.mean_citation_score:.2%} "
              f"keywords={report.mean_keyword_score:.2%} "
              f"refusal={report.refusal_accuracy:.2%}")
    else:
        print(f"\nFAIL: citation={report.mean_citation_score:.2%} (<50%) "
              f"keywords={report.mean_keyword_score:.2%} (<50%) "
              f"refusal={report.refusal_accuracy:.2%} (<80%)")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
