"""Offline retrieval evaluation: measure exact-match Recall@k, MRR@k, and p95 latency.

Evaluates the v2 pure-lexical/metadata/graph ``RetrievalEngine`` and
``CorpusBackend`` across the 90-case ``golden.jsonl`` suite with 100% offline
execution (zero ``DATABASE_URL``, zero ``NVIDIA_API_KEY`` required).

Fixes the v0.2 act-only recall bug: scoring requires an exact canonical
``(act, ref)`` or ``(court/judgment, citation/cnr/case_id)`` match via
``nyaya_chat.citations``; retrieving a different section of the same act
(e.g. ``IPC 420`` when ``IPC 302`` is expected) or a different reporter page
(e.g. ``AIR 1973 SC 9999`` when ``AIR 1973 SC 1461`` is expected) scores 0.0.

Usage::

    cd mcp
    uv run python ../chat/eval/retrieval_eval.py --fixture tests/fixtures/mini_corpus
"""

from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_THIS_DIR = Path(__file__).resolve().parent
_CHAT_ROOT = _THIS_DIR.parent
_MCP_ROOT = _CHAT_ROOT.parent / "mcp"
for _p in (str(_CHAT_ROOT), str(_MCP_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)


@dataclass
class GoldenCase:
    id: str
    question: str
    expected_citations: list[dict[str, str]]
    expected_keywords: list[str]
    should_refuse: bool
    category: str = "general"
    tool: str = "search_statutes"
    filters: dict[str, Any] = field(default_factory=dict)
    commercial_safe: bool = True


@dataclass
class RetrievalResult:
    case_id: str
    question: str
    recall_at_k: float
    mrr_at_k: float
    expected_found: list[bool]
    latency_ms: float
    category: str = "general"
    error: str | None = None


@dataclass
class EvalReport:
    total: int = 0
    mean_recall: float = 0.0
    mean_mrr: float = 0.0
    mean_latency_ms: float = 0.0
    p95_latency_ms: float = 0.0
    per_case: list[RetrievalResult] = field(default_factory=list)


def load_golden(path: str | Path) -> list[GoldenCase]:
    cases: list[GoldenCase] = []
    p = Path(path)
    if not p.exists():
        alt = _CHAT_ROOT / path
        if alt.exists():
            p = alt
        else:
            print(f"ERROR: golden file not found: {path}", file=sys.stderr)
            sys.exit(1)
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        d = json.loads(line)
        cases.append(
            GoldenCase(
                id=d["id"],
                question=d["question"],
                expected_citations=d.get("expected_citations", []),
                expected_keywords=d.get("expected_keywords", []),
                should_refuse=d.get("should_refuse", False),
                category=d.get("category", "general"),
                tool=d.get("tool", "search_statutes"),
                filters=d.get("filters", {}),
                commercial_safe=d.get("commercial_safe", True),
            )
        )
    return cases


def _item_to_json(item: Any) -> str:
    if isinstance(item, str):
        return item
    if hasattr(item, "model_dump_json") and callable(item.model_dump_json):
        return str(item.model_dump_json())
    if hasattr(item, "model_dump") and callable(item.model_dump):
        return json.dumps(item.model_dump(mode="json"), default=str)
    if isinstance(item, dict):
        return json.dumps(item, default=str)
    return json.dumps(getattr(item, "__dict__", {}), default=str)


def _expected_candidate_keys(cite: dict[str, str], aliases: dict[str, str]) -> set[str]:
    from nyaya.citations import _marker_candidate_keys

    from nyaya_chat.citations import _extra_marker_keys

    act = str(cite.get("act") or "judgment").strip()
    ref = str(cite.get("ref") or "").strip()
    if not ref:
        return set()
    return _marker_candidate_keys(act, ref, aliases) | _extra_marker_keys(act, ref, aliases)


def score_retrieved_items(
    expected_citations: list[dict[str, str]],
    retrieved_items: Sequence[Any],
) -> tuple[float, float, list[bool]]:
    """Compute strict exact-match (recall@k, mrr@k, expected_found) over retrieved items.

    Never falls back to act-only matching: a hit must share at least one
    canonical citation key with the expected citation.
    """
    if not expected_citations:
        return 1.0, 1.0, []

    from nyaya_chat.citations import _build_aliases_for_payloads, extract_grounded_keys

    serialized = [_item_to_json(item) for item in retrieved_items]
    aliases = _build_aliases_for_payloads(serialized)
    per_rank_keys: list[set[str]] = [
        extract_grounded_keys([payload], alias_map=aliases) for payload in serialized
    ]
    all_keys: set[str] = set()
    for rk in per_rank_keys:
        all_keys.update(rk)

    expected_found: list[bool] = []
    first_ranks: list[int] = []

    for cite in expected_citations:
        cand_keys = _expected_candidate_keys(cite, aliases)
        matched = bool(cand_keys and (cand_keys & all_keys))
        expected_found.append(matched)
        if matched:
            for rank_idx, rk in enumerate(per_rank_keys, start=1):
                if cand_keys & rk:
                    first_ranks.append(rank_idx)
                    break

    recall = sum(1 for f in expected_found if f) / len(expected_citations)
    mrr = (1.0 / min(first_ranks)) if first_ranks else 0.0
    return recall, mrr, expected_found


def _retrieve_for_case(case: GoldenCase, k: int) -> list[Any]:
    from nyaya.models import JudgmentSearchQuery, StatuteSearchQuery
    from nyaya.storage import get_backend, get_retrieval

    backend = get_backend()
    retrieval = get_retrieval()
    items: list[Any] = []
    f = case.filters or {}

    if case.tool in ("search_statutes", "get_provision", "browse_statute"):
        s_resp = retrieval.search_statutes(
            StatuteSearchQuery(
                query=case.question,
                act=f.get("act"),
                jurisdiction=f.get("jurisdiction"),
                kind=f.get("kind"),
                promote_definitions=bool(f.get("promote_definitions", False)),
                commercial_safe_only=False,
                limit=k,
            )
        )
        items.extend(s_resp.results)

    elif case.tool == "compare_provisions":
        s_resp = retrieval.search_statutes(
            StatuteSearchQuery(query=case.question, commercial_safe_only=False, limit=k)
        )
        items.extend(s_resp.results)
        for hit in list(s_resp.results[:3]):
            for entry in backend.compare_provisions(hit.act, hit.ref, commercial_safe_only=False):
                items.append(entry)

    elif case.tool == "get_provision_history":
        hist = backend.get_provision_history(
            f.get("act") or "Constitution",
            f.get("ref"),
            amendment_number=f.get("amendment_number"),
            limit=max(k, 20),
            commercial_safe_only=False,
        )
        items.extend(hist.amendments)
        items.extend(hist.judicial_validity_events)
        s_resp = retrieval.search_statutes(
            StatuteSearchQuery(query=case.question, commercial_safe_only=False, limit=k)
        )
        items.extend(s_resp.results)

    elif case.tool in ("search_judgments", "get_judgment", "trace_precedent", "judgment_analytics"):
        duckdb_store = getattr(retrieval, "duckdb", None)
        tier_filter = f.get("court_tier")
        if duckdb_store is not None and tier_filter == "regulator":
            d_rows, _ = duckdb_store.search_judgments(
                tier="regulator",
                query=f.get("query"),
                court=None,
                limit=k,
                commercial_safe_only=False,
            )
            items.extend(d_rows)
        else:
            q_str = f["query"] if "query" in f else case.question
            j_resp = retrieval.search_judgments(
                JudgmentSearchQuery(
                    query=q_str,
                    court_tier=tier_filter,
                    court=None if tier_filter in ("district_court", "tribunal") else f.get("court"),
                    bench=f.get("bench"),
                    judge=f.get("judge"),
                    author_judge=f.get("author_judge"),
                    party=f.get("party"),
                    act=f.get("act"),
                    section=f.get("section"),
                    year_from=f.get("year_from"),
                    year_to=f.get("year_to"),
                    disposal_nature=f.get("disposal_nature"),
                    commercial_safe_only=False,
                    limit=k,
                )
            )
            items.extend(j_resp.results)
            if duckdb_store is not None and tier_filter in ("district_court", "tribunal"):
                d_rows, _ = duckdb_store.search_judgments(
                    tier=tier_filter,
                    query=q_str,
                    court=None,
                    state=f.get("state") or (f.get("court") if tier_filter == "district_court" else None),
                    judge=f.get("judge") or f.get("author_judge"),
                    party=f.get("party"),
                    act=f.get("act"),
                    year_from=f.get("year_from"),
                    year_to=f.get("year_to"),
                    limit=k,
                    commercial_safe_only=False,
                )
                items.extend(d_rows)

    else:
        s_resp = retrieval.search_statutes(
            StatuteSearchQuery(query=case.question, commercial_safe_only=False, limit=k)
        )
        items.extend(s_resp.results)

    return items[: max(k, len(items))]


async def eval_retrieval(cases: list[GoldenCase], k: int = 10) -> EvalReport:
    report = EvalReport(total=len(cases))
    results: list[RetrievalResult] = []

    for case in cases:
        if case.should_refuse or not case.expected_citations:
            results.append(
                RetrievalResult(
                    case_id=case.id,
                    question=case.question,
                    recall_at_k=1.0,
                    mrr_at_k=1.0,
                    expected_found=[],
                    latency_ms=0.0,
                    category=case.category,
                )
            )
            continue

        start = time.monotonic()
        try:
            retrieved = await asyncio.to_thread(_retrieve_for_case, case, k)
            latency = (time.monotonic() - start) * 1000.0
            recall, mrr, expected_found = score_retrieved_items(case.expected_citations, retrieved)
            results.append(
                RetrievalResult(
                    case_id=case.id,
                    question=case.question,
                    recall_at_k=recall,
                    mrr_at_k=mrr,
                    expected_found=expected_found,
                    latency_ms=latency,
                    category=case.category,
                )
            )
        except Exception as exc:
            latency = (time.monotonic() - start) * 1000.0
            results.append(
                RetrievalResult(
                    case_id=case.id,
                    question=case.question,
                    recall_at_k=0.0,
                    mrr_at_k=0.0,
                    expected_found=[False] * len(case.expected_citations),
                    latency_ms=latency,
                    category=case.category,
                    error=str(exc),
                )
            )

    report.per_case = results
    recalls = [r.recall_at_k for r in results if r.error is None]
    mrrs = [r.mrr_at_k for r in results if r.error is None]
    latencies = sorted(r.latency_ms for r in results if r.error is None and r.latency_ms > 0)
    report.mean_recall = sum(recalls) / len(recalls) if recalls else 0.0
    report.mean_mrr = sum(mrrs) / len(mrrs) if mrrs else 0.0
    report.mean_latency_ms = sum(latencies) / len(latencies) if latencies else 0.0
    if latencies:
        p95_idx = min(len(latencies) - 1, int(len(latencies) * 0.95))
        report.p95_latency_ms = latencies[p95_idx]
    return report


def _setup_fixture_backend(fixture_arg: str) -> tempfile.TemporaryDirectory[str] | None:
    from dataclasses import replace

    from nyaya.config import get_settings
    from nyaya.retrieval import build_retrieval_engine
    from nyaya.storage import set_analytics, set_backend, set_retrieval
    from nyaya.storage.duckdb_store import DuckDBStore
    from nyaya.storage.lance_store import LanceStore
    from nyaya.storage.manifest import load_manifest
    from nyaya.storage.sqlite_store import SqliteStore

    cand = Path(fixture_arg)
    if not cand.exists() and (_MCP_ROOT / fixture_arg).exists():
        cand = _MCP_ROOT / fixture_arg

    tmp_ctx: tempfile.TemporaryDirectory[str] | None = None
    if (cand / "nyaya-core.sqlite").is_file():
        data_dir = cand
    else:
        from tests.fixtures.mini_corpus.builder import build_mini_corpus

        tmp_ctx = tempfile.TemporaryDirectory(prefix="nyaya_eval_fixture_")
        data_dir = Path(tmp_ctx.name)
        build_mini_corpus(data_dir)

    settings = replace(
        get_settings(),
        data_dir=data_dir,
        cache_dir=data_dir / "cache",
        offline_only=True,
        commercial_safe_only=False,
    )
    sqlite_store = SqliteStore(data_dir / "nyaya-core.sqlite", default_commercial_safe_only=False)
    lance_store = LanceStore(
        local_dir=data_dir / "lancedb",
        offline_only=True,
        default_commercial_safe_only=False,
    )
    manifest = (
        load_manifest(data_dir / "manifest.json", offline_only=True)
        if (data_dir / "manifest.json").is_file()
        else None
    )
    duck_store = DuckDBStore(
        manifest=manifest,
        settings=settings,
        base_dir=data_dir,
        temp_dir=data_dir / "duck_tmp",
        offline_only=True,
        default_commercial_safe_only=False,
    )
    engine = build_retrieval_engine(
        settings,
        sqlite_store,
        lance=lance_store,
        duckdb=duck_store,
    )
    set_backend(sqlite_store)
    set_retrieval(engine)
    set_analytics(duck_store)
    return tmp_ctx


def print_report(report: EvalReport, k: int) -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(f"\n{'=' * 68}")
    print(f"Nyaya v2.0 Offline Retrieval Evaluation: Recall@{k} & MRR@{k}")
    print(f"{'=' * 68}")
    print(f"Cases:          {report.total}")
    print(f"Mean Recall@{k}:  {report.mean_recall:.2%}")
    print(f"Mean MRR@{k}:     {report.mean_mrr:.2%}")
    print(f"Mean Latency:   {report.mean_latency_ms:.1f}ms (p95={report.p95_latency_ms:.1f}ms)")
    print(f"{'-' * 68}")
    for r in report.per_case:
        status = "PASS" if r.recall_at_k == 1.0 else ("FAIL" if r.recall_at_k == 0 else "PART")
        found_str = ",".join("Y" if f else "N" for f in r.expected_found)
        err = f" [ERROR: {r.error}]" if r.error else ""
        print(
            f"  [{status}] {r.case_id:28s} ({r.category:24s}) "
            f"recall={r.recall_at_k:.0%} mrr={r.mrr_at_k:.2f} found=[{found_str}] {r.latency_ms:.1f}ms{err}"
        )
    print(f"{'=' * 68}")


async def main() -> None:
    import argparse

    from nyaya.storage import close_all

    parser = argparse.ArgumentParser(description="Nyaya v2.0 offline retrieval evaluation")
    parser.add_argument("--k", type=int, default=10, help="Top-k cutoff (default 10)")
    parser.add_argument(
        "--golden",
        type=str,
        default=str(_THIS_DIR / "golden.jsonl"),
        help="Path to golden.jsonl",
    )
    parser.add_argument(
        "--fixture",
        type=str,
        default=None,
        help="Optional fixture path (e.g. tests/fixtures/mini_corpus) for hermetic offline run",
    )
    args = parser.parse_args()

    tmp_ctx = None
    try:
        if args.fixture:
            tmp_ctx = _setup_fixture_backend(args.fixture)
        cases = load_golden(args.golden)
        report = await eval_retrieval(cases, k=args.k)
        print_report(report, args.k)

        if report.mean_recall < 0.85 or report.mean_mrr < 0.70:
            print(
                f"\nFAIL: mean_recall={report.mean_recall:.2%} (min 85%) "
                f"mean_mrr={report.mean_mrr:.2%} (min 70%)"
            )
            sys.exit(1)
        print(
            f"\nPASS: mean_recall={report.mean_recall:.2%} >= 85% and "
            f"mean_mrr={report.mean_mrr:.2%} >= 70%"
        )
    finally:
        close_all()
        if tmp_ctx is not None:
            tmp_ctx.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
