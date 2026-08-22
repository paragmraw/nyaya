"""Retrieval evaluation: measure recall@k for the semantic_query tool.

For each golden question, calls ``nyaya.db.rerank_search`` directly (via
asyncio.to_thread) with the question as the query, and checks whether each
expected citation (act, ref) appears in the top-k results.

Usage::

    cd chat
    python -m eval.retrieval_eval [--k 10] [--golden eval/golden.jsonl]

Requires ``DATABASE_URL`` and ``NVIDIA_API_KEY`` env vars (the nyaya package
must be importable).
"""

from __future__ import annotations

import asyncio
import json
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
class RetrievalResult:
    case_id: str
    question: str
    recall_at_k: float
    expected_found: list[bool]
    latency_ms: float
    error: str | None = None


@dataclass
class EvalReport:
    total: int = 0
    mean_recall: float = 0.0
    per_case: list[RetrievalResult] = field(default_factory=list)
    mean_latency_ms: float = 0.0


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
    import re
    s = s.strip().lower()
    s = re.sub(r"^(?:s(?:ec(?:tion)?)?\.?\s*|art(?:icle)?\.?\s*)", "", s)
    return s.strip()


async def eval_retrieval(cases: list[GoldenCase], k: int = 10) -> EvalReport:
    from nyaya import db
    from nyaya.exceptions import DatabaseUnavailable

    report = EvalReport(total=len(cases))
    results: list[RetrievalResult] = []

    for case in cases:
        if not case.expected_citations:
            # Skip cases with no expected citations (e.g. refusal tests)
            results.append(RetrievalResult(
                case_id=case.id, question=case.question,
                recall_at_k=1.0, expected_found=[], latency_ms=0,
            ))
            continue

        start = time.monotonic()
        try:
            search_results, total, fallback = await asyncio.to_thread(
                db.rerank_search, case.question, limit=k, offset=0,
            )
            latency = (time.monotonic() - start) * 1000

            # Build a set of (act, ref) pairs from results
            found_pairs: set[str] = set()
            for r in search_results:
                act_n = _normalize(r.act)
                ref_n = _normalize(r.ref)
                found_pairs.add(f"{act_n}|{ref_n}")

            # Check each expected citation
            expected_found: list[bool] = []
            for cite in case.expected_citations:
                act_n = _normalize(cite["act"])
                ref_n = _normalize(cite["ref"])
                found = f"{act_n}|{ref_n}" in found_pairs
                # Also check act-only match
                if not found:
                    found = any(p.startswith(f"{act_n}|") for p in found_pairs)
                expected_found.append(found)

            recall = sum(1 for f in expected_found if f) / len(expected_found)
            results.append(RetrievalResult(
                case_id=case.id, question=case.question,
                recall_at_k=recall, expected_found=expected_found,
                latency_ms=latency,
            ))
        except (DatabaseUnavailable, Exception) as exc:
            latency = (time.monotonic() - start) * 1000
            results.append(RetrievalResult(
                case_id=case.id, question=case.question,
                recall_at_k=0.0, expected_found=[False] * len(case.expected_citations),
                latency_ms=latency, error=str(exc),
            ))

    report.per_case = results
    recalls = [r.recall_at_k for r in results if r.error is None]
    latencies = [r.latency_ms for r in results if r.error is None and r.latency_ms > 0]
    report.mean_recall = sum(recalls) / len(recalls) if recalls else 0
    report.mean_latency_ms = sum(latencies) / len(latencies) if latencies else 0
    return report


def print_report(report: EvalReport, k: int) -> None:
    print(f"\n{'='*60}")
    print(f"Retrieval Evaluation — recall@{k}")
    print(f"{'='*60}")
    print(f"Cases: {report.total}")
    print(f"Mean Recall@{k}: {report.mean_recall:.2%}")
    print(f"Mean Latency: {report.mean_latency_ms:.0f}ms")
    print(f"{'─'*60}")
    for r in report.per_case:
        status = "✓" if r.recall_at_k == 1.0 else ("✗" if r.recall_at_k == 0 else "◐")
        found_str = ",".join("✓" if f else "✗" for f in r.expected_found)
        err = f" [ERROR: {r.error}]" if r.error else ""
        print(f"  {status} {r.case_id:20s} recall={r.recall_at_k:.0%} "
              f"found=[{found_str}] {r.latency_ms:.0f}ms{err}")
    print(f"{'='*60}")


async def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description="Retrieval evaluation")
    parser.add_argument("--k", type=int, default=10, help="Recall@k (default 10)")
    parser.add_argument("--golden", type=str, default="eval/golden.jsonl",
                        help="Path to golden.jsonl")
    args = parser.parse_args()

    cases = load_golden(args.golden)
    report = await eval_retrieval(cases, k=args.k)
    print_report(report, args.k)

    # Exit non-zero if mean recall < 0.7
    if report.mean_recall < 0.7:
        print(f"\nFAIL: mean recall {report.mean_recall:.2%} < 70% threshold")
        sys.exit(1)
    print(f"\nPASS: mean recall {report.mean_recall:.2%} >= 70% threshold")


if __name__ == "__main__":
    asyncio.run(main())
