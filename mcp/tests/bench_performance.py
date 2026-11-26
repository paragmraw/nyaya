"""Warm performance & throughput benchmarks for Nyaya MCP v2.1.

Measures warm p50 / p95 / p99 latencies (ms) and throughput across:
1. `search_statutes` (SQLite FTS5 + Lance)
2. `get_provision` (standard vs `as_of` point-in-time reconstruction)
3. `get_provisions_batch` (25 provisions in 1 call vs 25 serial `get_provision` calls)
4. `get_judgments_batch` (10 judgments in 1 call vs 10 serial `get_judgment` calls)
5. `search_judgments` (pure lexical vs opt-in hybrid mode latency overhead)
6. `trace_precedent` (citation graph traversal)
7. `verify_citations` (large ~35 KB legal brief with quote checks)
8. `get_cause_list` (court/date/party filter)
9. `citation_graph.build_citation_edges_for_corpus` throughput (docs/sec, edges/sec)

Runnable standalone or importable::

    cd mcp
    uv run python tests/bench_performance.py
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_MCP_ROOT = Path(__file__).resolve().parent.parent
if str(_MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(_MCP_ROOT))

from nyaya import storage  # noqa: E402
from nyaya.config import Settings  # noqa: E402
from nyaya.ingest.builders.citation_graph import build_citation_edges_for_corpus  # noqa: E402
from nyaya.ingest.builders.lance_fts import build_lance_fts_tables  # noqa: E402
from nyaya.ingest.builders.sqlite_core import (  # noqa: E402
    IngestInputBundle,
    build_sqlite_core,
    create_mini_ingest_input,
)
from nyaya.models import JudgmentSearchQuery, QuoteCheckInput, StatuteSearchQuery  # noqa: E402
from nyaya.retrieval.hybrid import HashedNgramEmbeddingProvider  # noqa: E402
from nyaya.retrieval.lexical_engine import build_retrieval_engine  # noqa: E402
from nyaya.storage.lance_store import LanceStore  # noqa: E402
from nyaya.storage.sqlite_store import SqliteStore  # noqa: E402
from nyaya.tools.get_cause_list import get_cause_list  # noqa: E402
from nyaya.tools.get_judgment import get_judgment  # noqa: E402
from nyaya.tools.get_judgments_batch import get_judgments_batch  # noqa: E402
from nyaya.tools.get_provision import get_provision  # noqa: E402
from nyaya.tools.get_provisions_batch import get_provisions_batch  # noqa: E402
from nyaya.tools.trace_precedent import trace_precedent  # noqa: E402
from nyaya.tools.verify_citations import verify_citations  # noqa: E402


@dataclass(frozen=True)
class LatencyStats:
    name: str
    iterations: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    mean_ms: float
    ops_per_sec: float


@dataclass
class BenchmarkReport:
    latencies: list[LatencyStats] = field(default_factory=list)
    provisions_batch_speedup: float = 1.0
    judgments_batch_speedup: float = 1.0
    hybrid_overhead_ms: float = 0.0
    citation_graph_docs_per_sec: float = 0.0
    citation_graph_edges_per_sec: float = 0.0
    citation_graph_docs: int = 0
    citation_graph_edges: int = 0
    brief_bytes: int = 0
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


def _percentile(sorted_vals: list[float], pct: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    rank = (pct / 100.0) * (len(sorted_vals) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = rank - lo
    return sorted_vals[lo] + frac * (sorted_vals[hi] - sorted_vals[lo])


def _bench_sync(
    name: str,
    fn: Callable[[], Any],
    *,
    warmup: int = 5,
    iterations: int = 30,
) -> LatencyStats:
    for _ in range(warmup):
        fn()
    samples: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    samples.sort()
    mean_ms = sum(samples) / len(samples)
    ops_sec = (1000.0 / mean_ms) if mean_ms > 0 else 0.0
    return LatencyStats(
        name=name,
        iterations=iterations,
        p50_ms=_percentile(samples, 50.0),
        p95_ms=_percentile(samples, 95.0),
        p99_ms=_percentile(samples, 99.0),
        mean_ms=mean_ms,
        ops_per_sec=ops_sec,
    )


async def _bench_async(
    name: str,
    fn: Callable[[], Any],
    *,
    warmup: int = 5,
    iterations: int = 30,
) -> LatencyStats:
    for _ in range(warmup):
        await fn()
    samples: list[float] = []
    for _ in range(iterations):
        t0 = time.perf_counter()
        await fn()
        samples.append((time.perf_counter() - t0) * 1000.0)
    samples.sort()
    mean_ms = sum(samples) / len(samples)
    ops_sec = (1000.0 / mean_ms) if mean_ms > 0 else 0.0
    return LatencyStats(
        name=name,
        iterations=iterations,
        p50_ms=_percentile(samples, 50.0),
        p95_ms=_percentile(samples, 95.0),
        p99_ms=_percentile(samples, 99.0),
        mean_ms=mean_ms,
        ops_per_sec=ops_sec,
    )


def _make_settings(data_dir: Path, *, hybrid_search: bool) -> Settings:
    return Settings(
        data_dir=data_dir,
        cache_dir=data_dir / "cache",
        bucket_endpoint=None,
        bucket_name=None,
        bucket_access_key_id=None,
        bucket_secret_access_key=None,
        bucket_region="auto",
        bucket_url_style="vhost",
        manifest_url=None,
        commercial_safe_only=False,
        offline_only=True,
        duckdb_ext_dir=None,
        log_level="WARNING",
        port=8000,
        redis_url=None,
        trusted_proxy_hops=1,
        hybrid_search=hybrid_search,
    )


def _build_bench_bundle() -> tuple[IngestInputBundle, list[str]]:
    bundle = create_mini_ingest_input()

    # Create 10 local offline judgments (no S3 bucket) so get_judgment / get_judgments_batch
    # exercises pure SQLite/Lance offline paths without remote S3 latency/errors.
    offline_judgment_ids: list[str] = []
    for idx in range(1, 11):
        cid = f"hc:2024:bench:{idx}"
        nc = f"2024:DHC:{2000 + idx}"
        offline_judgment_ids.append(nc)
        bundle.hc_web_rows.append(
            {
                "case_id": cid,
                "court_code": "7_26",
                "court_name": "High Court of Delhi",
                "bench": "Principal",
                "cnr": f"DLHC0109{idx:04d}2024",
                "neutral_citation": nc,
                "title": f"Benchmark Party {idx} v. State NCT of Delhi",
                "petitioner": f"Benchmark Party {idx}",
                "respondent": "State NCT of Delhi",
                "judge": "Vibhu Bakhru",
                "author_judge": "Vibhu Bakhru",
                "bench_strength": 2,
                "decision_date": "2024-06-15",
                "year": 2024,
                "order_number": idx,
                "disposal_nature": "Allowed",
                "case_type": "W.P.(C)",
                "act": "Constitution",
                "acts_cited": ["Constitution", "CPC"],
                "is_reportable": True,
                "pdf_s3_bucket": None,
                "pdf_s3_key": None,
                "source_url": "https://indian-high-court-judgments.s3.ap-south-1.amazonaws.com",
                "text": (
                    f"1. Petition under Article 21 and Order 7 Rule 11 CPC relying on 2017 INSC 801 "
                    f"and AIR 1973 SC 1461.\n2. Writ petition #{idx} disposed of."
                ),
                "source_id": "dattam_hc_judgments",
                "commercial_safe": True,
            }
        )

    return bundle, offline_judgment_ids


def _build_35kb_legal_brief() -> tuple[str, list[QuoteCheckInput | dict[str, str]]]:
    paragraphs = [
        (
            "1. The petitioner invokes Article 21 of the Constitution of India and relies on "
            "Justice K.S. Puttaswamy (Retd.) v. Union of India, 2017 INSC 801 ((2017) 10 SCC 1), "
            "affirming Kesavananda Bharati v. State of Kerala, AIR 1973 SC 1461 ((1973) 4 SCC 225), "
            "and Shreya Singhal v. Union of India, 2015 INSC 254 ((2015) 5 SCC 1), which struck down "
            "Section 66A of the Information Technology Act, 2000."
        ),
        (
            "2. On criminal transition, offences prior to 1 July 2024 under Section 302 IPC, "
            "Section 420 IPC, and Section 438 CrPC continue under Section 531 BNSS, while subsequent "
            "causes attract Section 103 BNS, Section 318(4) BNS, and Section 482 BNSS, alongside "
            "Section 63 BSA replacing Section 65B EvidenceAct."
        ),
        (
            "3. In civil jurisdiction, plaint rejection is governed by Order 7 Rule 11 CPC and "
            "temporary injunctions by Order 39 Rule 1 CPC, as applied in 2024:DHC:1234 and "
            "2024:BHC-OS:567. Under the Income-tax Act, 1961 (ITAct1961 s.139 and s.143), "
            "corresponding provisions under ITAct2025 s.263 and s.270 preserve transitional continuity."
        ),
    ]
    filler_block = (
        "4. Detailed submission on constitutional supremacy, statutory interpretation, procedural fairness, "
        "natural justice, proportionality doctrine, and evidentiary certification under Article 14, "
        "Article 19, Article 21, and Article 32 of the Constitution of India. "
    )
    chunks: list[str] = [*paragraphs]
    while len("\n\n".join(chunks).encode("utf-8")) < 35_000:
        chunks.append(filler_block * 3)
    brief = "\n\n".join(chunks)

    quote_checks: list[QuoteCheckInput | dict[str, str]] = [
        QuoteCheckInput(
            citation="Article 21",
            quote="No person shall be deprived of his life or personal liberty except according to procedure established by law.",
        ),
        QuoteCheckInput(
            citation="IPC s.302",
            quote="Whoever commits murder shall be punished with death, or imprisonment for life, and shall also be liable to fine.",
        ),
        QuoteCheckInput(
            citation="CPC Order 7 Rule 11",
            quote="The plaint shall be rejected in the following cases",
        ),
    ]
    return brief, quote_checks


async def _run_benchmarks_async() -> BenchmarkReport:
    report = BenchmarkReport()

    with tempfile.TemporaryDirectory(prefix="nyaya_bench_") as tmp:
        root = Path(tmp)
        bundle, offline_j_ids = _build_bench_bundle()
        sqlite_path = root / "nyaya-core.sqlite"
        lance_dir = root / "lancedb"
        embedder = HashedNgramEmbeddingProvider(dim=256)

        build_sqlite_core(sqlite_path, input_data=bundle)
        build_lance_fts_tables(lance_dir, input_data=bundle, embedding_provider=embedder)

        backend = SqliteStore(sqlite_path)
        lance = LanceStore(local_dir=lance_dir, offline_only=True, embedding_provider=embedder)
        lex_engine = build_retrieval_engine(
            _make_settings(root, hybrid_search=False), backend, lance=lance
        )
        hyb_engine = build_retrieval_engine(
            _make_settings(root, hybrid_search=True), backend, lance=lance, embedder=embedder
        )

        storage.set_backend(backend)
        storage.set_retrieval(lex_engine)
        try:
            # 1. search_statutes (SQLite FTS5 + Lance)
            s_stat = _bench_sync(
                "1. search_statutes (SQLite+Lance)",
                lambda: lex_engine.search_statutes(
                    StatuteSearchQuery(query="punishment for murder procedural fairness", limit=10)
                ),
            )
            report.latencies.append(s_stat)

            # 2. get_provision (standard vs as_of)
            gp_std = _bench_sync(
                "2a. get_provision (standard)",
                lambda: get_provision(citation="IPC s.302"),
            )
            gp_pit = _bench_sync(
                "2b. get_provision (as_of)",
                lambda: get_provision(citation="IPC s.302", as_of="2024-06-30"),
            )
            report.latencies.extend([gp_std, gp_pit])

            # 3. get_provisions_batch (25 provisions in 1 call vs 25 serial calls)
            prov_25 = [
                "Constitution art.14", "Constitution art.19", "Constitution art.21",
                "Constitution art.21A", "Constitution art.32", "IPC s.302", "IPC s.420",
                "IPC s.375", "IPC s.377", "IPC s.497", "BNS s.103", "BNS s.318(4)",
                "CrPC s.438", "BNSS s.482", "BNSS s.531", "EvidenceAct s.65B", "BSA s.63",
                "ITAct s.66A", "ITAct1961 s.139", "ITAct2025 s.263", "ITAct1961 s.143",
                "ITAct2025 s.270", "CPC Order 1 Rule 1", "CPC Order 7 Rule 11", "CPC Order 39 Rule 1",
            ]
            gp_serial = _bench_sync(
                "3a. get_provision x25 (serial)",
                lambda: [get_provision(citation=c, as_of="2024-06-30") for c in prov_25],
                warmup=2,
                iterations=15,
            )
            gp_batch = _bench_sync(
                "3b. get_provisions_batch (25)",
                lambda: get_provisions_batch(citations=prov_25, as_of="2024-06-30"),
                warmup=2,
                iterations=15,
            )
            report.latencies.extend([gp_serial, gp_batch])
            report.provisions_batch_speedup = (
                gp_serial.mean_ms / gp_batch.mean_ms if gp_batch.mean_ms > 0 else 1.0
            )

            # 4. get_judgments_batch (10 judgments in 1 call vs 10 serial calls)
            gj_serial = await _bench_async(
                "4a. get_judgment x10 (serial)",
                lambda: _serial_get_judgments(offline_j_ids),
                warmup=2,
                iterations=15,
            )
            gj_batch = await _bench_async(
                "4b. get_judgments_batch (10)",
                lambda: get_judgments_batch(identifiers=offline_j_ids, view="overview"),
                warmup=2,
                iterations=15,
            )
            report.latencies.extend([gj_serial, gj_batch])
            report.judgments_batch_speedup = (
                gj_serial.mean_ms / gj_batch.mean_ms if gj_batch.mean_ms > 0 else 1.0
            )

            # 5. search_judgments (lexical vs opt-in hybrid)
            sj_lex = _bench_sync(
                "5a. search_judgments (lexical)",
                lambda: lex_engine.search_judgments(
                    JudgmentSearchQuery(query="anticipatory bail personal liberty", court_tier="all", limit=10)
                ),
            )
            sj_hyb = _bench_sync(
                "5b. search_judgments (hybrid RRF)",
                lambda: hyb_engine.search_judgments(
                    JudgmentSearchQuery(query="anticipatory bail personal liberty", court_tier="all", limit=10)
                ),
            )
            report.latencies.extend([sj_lex, sj_hyb])
            report.hybrid_overhead_ms = sj_hyb.p50_ms - sj_lex.p50_ms

            # 6. trace_precedent (citation graph traversal)
            tp_stat = _bench_sync(
                "6. trace_precedent",
                lambda: trace_precedent(identifier="2017 INSC 801", direction="both", limit=15),
            )
            report.latencies.append(tp_stat)

            # 7. verify_citations (~35 KB brief with quote checks)
            brief, quote_checks = _build_35kb_legal_brief()
            report.brief_bytes = len(brief.encode("utf-8"))
            vc_stat = await _bench_async(
                "7. verify_citations (~35KB)",
                lambda: verify_citations(
                    text=brief,
                    verify_quotes=quote_checks,
                    commercial_safe_only=False,
                ),
                warmup=2,
                iterations=12,
            )
            report.latencies.append(vc_stat)

            # 8. get_cause_list (court/date/party filter)
            cl_stat = _bench_sync(
                "8. get_cause_list",
                lambda: get_cause_list(court="Supreme Court of India", date="2026-11-26"),
            )
            report.latencies.append(cl_stat)

            # 9. citation_graph.build_citation_edges_for_corpus throughput
            synth_docs = [
                {
                    "case_id": f"sc:synth:{i}",
                    "year": 2024,
                    "bench_strength": 2,
                    "text": (
                        "1. Applying 2017 INSC 801 and AIR 1973 SC 1461.\n"
                        "2. Discussing 2015 INSC 254, 2024:DHC:1234, Section 302 IPC, and Article 21."
                    ),
                }
                for i in range(200)
            ]
            t0 = time.perf_counter()
            built_edges = build_citation_edges_for_corpus(
                bundle.sc_judgments + synth_docs,
                bundle.hc_web_rows,
                provisions=[
                    {"doc_id": "ipc-1860:302", "act_short": "IPC", "ref": "302"},
                    {"doc_id": "constitution:art:21", "act_short": "Constitution", "ref": "21"},
                ],
            )
            elapsed_s = max(time.perf_counter() - t0, 1e-6)
            total_docs = len(bundle.sc_judgments) + len(synth_docs) + len(bundle.hc_web_rows)
            report.citation_graph_docs = total_docs
            report.citation_graph_edges = len(built_edges)
            report.citation_graph_docs_per_sec = total_docs / elapsed_s
            report.citation_graph_edges_per_sec = len(built_edges) / elapsed_s

            # Threshold assertions (warm p95 < 100ms for SQLite/FTS/point-in-time/batch/cause-list/trace)
            for st in (s_stat, gp_std, gp_pit, gp_batch, gj_batch, sj_lex, tp_stat, cl_stat):
                if st.p95_ms >= 100.0:
                    report.failures.append(f"{st.name} p95={st.p95_ms:.2f}ms >= 100ms threshold")
            if sj_hyb.p95_ms >= 150.0:
                report.failures.append(f"{sj_hyb.name} p95={sj_hyb.p95_ms:.2f}ms >= 150ms threshold")
            if vc_stat.p95_ms >= 500.0:
                report.failures.append(f"{vc_stat.name} p95={vc_stat.p95_ms:.2f}ms >= 500ms threshold")
            if report.citation_graph_docs_per_sec < 100.0:
                report.failures.append(
                    f"citation_graph throughput={report.citation_graph_docs_per_sec:.1f} docs/s < 100 docs/s"
                )

            return report
        finally:
            storage.set_retrieval(None)
            storage.set_backend(None)
            lance.close()
            backend.close()


async def _serial_get_judgments(identifiers: list[str]) -> list[Any]:
    out = []
    for ident in identifiers:
        out.append(await get_judgment(identifier=ident, view="overview"))
    return out


def run_benchmarks() -> BenchmarkReport:
    """Synchronous entry point running all Suite 4 benchmarks."""
    return asyncio.run(_run_benchmarks_async())


def format_benchmark_report(report: BenchmarkReport) -> str:
    lines = [
        "==========================================================================================",
        "Nyaya v2.1 Warm Performance & Throughput Benchmarks",
        "==========================================================================================",
        f"{'Benchmark':<34} | {'N':>3} | {'p50 ms':>8} | {'p95 ms':>8} | {'p99 ms':>8} | {'ops/s':>9}",
        "-" * 90,
    ]
    for st in report.latencies:
        lines.append(
            f"{st.name:<34} | {st.iterations:>3} | {st.p50_ms:>8.2f} | {st.p95_ms:>8.2f} | "
            f"{st.p99_ms:>8.2f} | {st.ops_per_sec:>9.1f}"
        )
    lines.extend(
        [
            "-" * 90,
            f"get_provisions_batch (25 items) speedup vs 25x serial: {report.provisions_batch_speedup:.2f}x",
            f"get_judgments_batch  (10 items) speedup vs 10x serial: {report.judgments_batch_speedup:.2f}x",
            f"search_judgments hybrid vs lexical p50 overhead:       {report.hybrid_overhead_ms:+.2f} ms",
            f"verify_citations brief size:                           {report.brief_bytes} bytes",
            (
                f"citation_graph.build_citation_edges_for_corpus:        "
                f"{report.citation_graph_docs} docs -> {report.citation_graph_edges} edges "
                f"({report.citation_graph_docs_per_sec:.1f} docs/s, {report.citation_graph_edges_per_sec:.1f} edges/s)"
            ),
            "=" * 90,
        ]
    )
    if report.failures:
        lines.append("FAILURES:")
        for f in report.failures:
            lines.append(f"  - {f}")
    else:
        lines.append("PASS: All latency (<100ms core / <500ms 35KB brief) and throughput thresholds satisfied.")
    return "\n".join(lines)


def main() -> int:
    report = run_benchmarks()
    print(format_benchmark_report(report))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
