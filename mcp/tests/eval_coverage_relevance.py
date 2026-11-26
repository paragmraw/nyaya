"""New-pillar relevance evaluation & Lexical-vs-Hybrid comparison for Nyaya MCP v2.1.

Evaluates Recall@10, MRR@10, and exact accuracy across:
1. Consumer Commission (NCDRC) & NCLAT judgment search
2. CPC First Schedule (Order 7 Rule 11, Order 39 Rule 1, etc.)
3. Income-tax Act 1961 <-> 2025 comparison + BNSS offence_classification
4. All 25 High Court neutral citations (YYYY:DHC:N, YYYY:BHC-OS:N, YYYY:MHC:N, etc.)
5. Point-in-time `as_of` accuracy across historical dates
6. Scaled citation graph `trace_precedent` recall
7. CAD debates & cause-list lookup recall
8. Lexical-only vs Opt-in Hybrid (Semantic + BM25 RRF) on exact-citation & conceptual queries

Runnable standalone or importable::

    cd mcp
    uv run python tests/eval_coverage_relevance.py
"""

from __future__ import annotations

import math
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_MCP_ROOT = Path(__file__).resolve().parent.parent
if str(_MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(_MCP_ROOT))

from nyaya import storage  # noqa: E402
from nyaya.config import Settings  # noqa: E402
from nyaya.ingest.builders.lance_fts import build_lance_fts_tables  # noqa: E402
from nyaya.ingest.builders.sqlite_core import (  # noqa: E402
    IngestInputBundle,
    build_sqlite_core,
    create_mini_ingest_input,
)
from nyaya.models import (  # noqa: E402
    JudgmentSearchQuery,
    OffenceClassification,
    PointInTimeMeta,
    StatuteSearchQuery,
)
from nyaya.retrieval.hybrid import HashedNgramEmbeddingProvider  # noqa: E402
from nyaya.retrieval.lexical_engine import LexicalRetrievalEngine, build_retrieval_engine  # noqa: E402
from nyaya.storage.lance_store import LanceStore  # noqa: E402
from nyaya.storage.sqlite_store import SqliteStore  # noqa: E402
from nyaya.tools.compare_provisions import compare_provisions  # noqa: E402
from nyaya.tools.get_cause_list import get_cause_list  # noqa: E402
from nyaya.tools.get_provision import get_provision  # noqa: E402
from nyaya.tools.trace_precedent import trace_precedent  # noqa: E402

# All 25 High Courts of India with Dattam court codes and canonical neutral citations
ALL_25_HC_SAMPLES: tuple[tuple[str, str, str, str, str], ...] = (
    ("hc:2024:ahc:107", "9_13", "High Court of Judicature at Allahabad", "2024:AHC:107", "Allahabad property mutation writ"),
    ("hc:2024:aphc:109", "28_2", "High Court of Andhra Pradesh", "2024:APHC:109", "Andhra Pradesh service seniority dispute"),
    ("hc:2024:bhc:567", "27_1", "High Court of Bombay", "2024:BHC-OS:567", "Bombay Original Side CPC Order VII Rule 11"),
    ("hc:2024:cal:126", "19_16", "High Court at Calcutta", "2024:CAL:126", "Calcutta arbitration award challenge"),
    ("hc:2024:cghc:110", "22_18", "High Court of Chhattisgarh", "2024:CGHC:110", "Chhattisgarh mining royalty appeal"),
    ("hc:2024:dhc:1234", "7_26", "High Court of Delhi", "2024:DHC:1234", "Delhi HC anticipatory bail Section 438 CrPC"),
    ("hc:2024:gau:111", "18_6", "Gauhati High Court", "2024:GAU:111", "Gauhati forest conservation writ"),
    ("hc:2024:guj:112", "24_17", "High Court of Gujarat", "2024:GUJ:112", "Gujarat GST input tax credit"),
    ("hc:2024:hphc:113", "2_5", "High Court of Himachal Pradesh", "2024:HPHC:113", "Himachal Pradesh hydro project contract"),
    ("hc:2024:jkhc:114", "1_12", "High Court of Jammu & Kashmir and Ladakh", "2024:JKL:114", "Jammu Kashmir preventive detention habeas corpus"),
    ("hc:2024:jhc:115", "20_7", "High Court of Jharkhand", "2024:JHHC:115", "Jharkhand land acquisition compensation"),
    ("hc:2024:kar:105", "29_3", "High Court of Karnataka", "2024:KHC:105", "Karnataka trademark infringement injunction"),
    ("hc:2024:ker:131", "32_4", "High Court of Kerala", "2024:KER:131", "Kerala cooperative societies election"),
    ("hc:2024:mphc:116", "23_23", "High Court of Madhya Pradesh", "2024:MPHC:116", "Madhya Pradesh municipal tax assessment"),
    ("hc:2024:mad:104", "33_10", "High Court of Madras", "2024:MHC:104", "Madras cheque bounce Section 138 Negotiable Instruments"),
    ("hc:2024:man:129", "14_25", "High Court of Manipur", "2024:MAN:129", "Manipur public employment reservation"),
    ("hc:2024:meg:128", "17_21", "High Court of Meghalaya", "2024:MEG:128", "Meghalaya customs transit dispute"),
    ("hc:2024:ori:119", "21_11", "High Court of Orissa", "2024:OHC:119", "Orissa industrial tribunal reinstatement"),
    ("hc:2024:pat:127", "10_8", "High Court of Patna", "2024:PAT:127", "Patna excise confiscation release"),
    ("hc:2024:phc:120", "3_22", "High Court of Punjab and Haryana", "2024:PHHC:120", "Punjab Haryana spousal maintenance alimony"),
    ("hc:2024:raj:121", "8_9", "High Court of Rajasthan", "2024:RJ-JD:121", "Rajasthan defamation libel criminal complaint"),
    ("hc:2024:sik:123", "11_24", "High Court of Sikkim", "2024:SHC:123", "Sikkim environmental clearance petition"),
    ("hc:2024:tshc:124", "36_29", "High Court for the State of Telangana", "2024:THC:124", "Telangana electronic evidence digital record admissibility"),
    ("hc:2024:tri:130", "16_20", "High Court of Tripura", "2024:TRI:130", "Tripura pension arrears writ"),
    ("hc:2024:ukhc:125", "5_15", "High Court of Uttarakhand", "2024:UHC:125", "Uttarakhand dowry cruelty criminal revision"),
)


@dataclass(frozen=True)
class PillarMetrics:
    pillar: str
    cases: int
    passed: int
    mean_recall: float
    mean_mrr: float
    mean_latency_ms: float


@dataclass(frozen=True)
class HybridComparisonSummary:
    suite_name: str
    cases: int
    lexical_recall: float
    lexical_mrr: float
    hybrid_recall: float
    hybrid_mrr: float
    recall_lift_pp: float
    mrr_lift: float


@dataclass
class CoverageEvalReport:
    pillar_metrics: list[PillarMetrics] = field(default_factory=list)
    hybrid_comparisons: list[HybridComparisonSummary] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


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


def _build_eval_bundle() -> IngestInputBundle:
    bundle = create_mini_ingest_input()

    existing_hc_ids = {str(r["case_id"]) for r in bundle.hc_web_rows}
    for idx, (case_id, court_code, court_name, nc, summary) in enumerate(ALL_25_HC_SAMPLES, start=1):
        if case_id in existing_hc_ids:
            continue
        # Inject cross-citations across judgments so citation_graph generates scaled edges
        extra_cite = " Relied on 2017 INSC 801 and AIR 1973 SC 1461." if idx % 2 == 0 else " Cited 2024:DHC:1234 and Section 302 IPC."
        bundle.hc_web_rows.append(
            {
                "case_id": case_id,
                "court_code": court_code,
                "court_name": court_name,
                "bench": "Principal",
                "cnr": f"HCIN{idx:04d}00002024",
                "neutral_citation": nc,
                "title": f"Petitioner {idx} v. Respondent {idx} ({nc})",
                "petitioner": f"Petitioner {idx}",
                "respondent": f"Respondent {idx}",
                "judge": f"Justice Judge {idx}",
                "author_judge": f"Justice Judge {idx}",
                "bench_strength": 2 if idx % 3 == 0 else 1,
                "decision_date": f"2024-{(idx % 12) + 1:02d}-15",
                "year": 2024,
                "order_number": idx,
                "disposal_nature": "Allowed" if idx % 2 == 0 else "Disposed of",
                "case_type": "W.P.(C)",
                "act": "Constitution",
                "acts_cited": ["Constitution"],
                "is_reportable": True,
                "source_url": "https://indian-high-court-judgments.s3.ap-south-1.amazonaws.com",
                "text": f"1. {summary}.{extra_cite}",
                "source_id": "dattam_hc_judgments",
                "commercial_safe": True,
            }
        )

    # Ensure rich NCDRC consumer commission + NCLAT + NGT records in tribunal_rows
    existing_trib_ids = {str(r["case_id"]) for r in bundle.tribunal_rows}
    extra_tribunals = [
        {
            "case_id": "trib:ncdrc:2024:22",
            "forum_code": "NCDRC",
            "court_code": "NCDRC",
            "court_name": "National Consumer Disputes Redressal Commission",
            "title": "Homebuyers Welfare Association v. Horizon Builders Pvt Ltd",
            "neutral_citation": "2024 NCDRC 22",
            "judge": "Justice A.P. Sahi",
            "author_judge": "Justice A.P. Sahi",
            "decision_date": "2024-07-12",
            "year": 2024,
            "disposal_nature": "Allowed",
            "case_type": "Consumer Complaint",
            "acts_cited": ["ConsumerProtection"],
            "text": (
                "1. NCDRC directed refund with 9% interest for inordinate delay in handing over "
                "residential apartment possession amounting to deficiency in service under Consumer Protection Act."
            ),
            "commercial_safe": True,
        },
        {
            "case_id": "trib:ncdrc:2024:99",
            "forum_code": "NCDRC",
            "court_code": "NCDRC",
            "court_name": "National Consumer Disputes Redressal Commission",
            "title": "Asha Verma v. Apex Housing Developers",
            "neutral_citation": "2024 NCDRC 99",
            "judge": "Justice Ram Surat Ram Maurya",
            "author_judge": "Justice Ram Surat Ram Maurya",
            "decision_date": "2024-09-05",
            "year": 2024,
            "disposal_nature": "Allowed",
            "case_type": "First Appeal",
            "acts_cited": ["ConsumerProtection"],
            "text": (
                "1. Forfeiture of earnest money without completion certificate constitutes unfair trade "
                "practice and consumer compensation is awarded."
            ),
            "commercial_safe": True,
        },
        {
            "case_id": "trib:ngt:2024:15",
            "forum_code": "NGT",
            "court_code": "NGT",
            "court_name": "National Green Tribunal",
            "title": "Green Citizens Forum v. State Pollution Control Board",
            "neutral_citation": "2024 NGT 15",
            "judge": "Justice Prakash Shrivastava",
            "author_judge": "Justice Prakash Shrivastava",
            "decision_date": "2024-06-18",
            "year": 2024,
            "disposal_nature": "Disposed of",
            "case_type": "Original Application",
            "acts_cited": ["EnvironmentAct"],
            "text": (
                "1. National Green Tribunal imposed environmental compensation for industrial effluent "
                "pollution and forest buffer encroachment."
            ),
            "commercial_safe": True,
        },
    ]
    for tr in extra_tribunals:
        if tr["case_id"] not in existing_trib_ids:
            bundle.tribunal_rows.append(tr)

    # Enrich NCLAT mini record text if minimal
    for tr in bundle.tribunal_rows:
        if tr["case_id"] == "trib:nclat:2024:10" and not tr.get("text"):
            tr["neutral_citation"] = "2024 NCLAT 10"
            tr["text"] = (
                "1. Resolution plan approved under Section 31 of the Insolvency and Bankruptcy Code; "
                "Committee of Creditors commercial wisdom upheld in CIRP appeal."
            )

    # Extra SC judgment referencing earlier SC judgments & IPC/BNS for scaled citation graph
    existing_sc_ids = {str(r["case_id"]) for r in bundle.sc_judgments}
    if "sc:2024:curative" not in existing_sc_ids:
        bundle.sc_judgments.append(
            {
                "case_id": "sc:2024:curative",
                "court_code": "SCI",
                "court_name": "Supreme Court of India",
                "bench": "Constitution Bench",
                "neutral_citation": "2024 INSC 301",
                "scc_citation": "(2024) 6 SCC 1",
                "title": "Constitution Bench Review v. Union of India",
                "petitioner": "Constitution Bench Review",
                "respondent": "Union of India",
                "judge_coram": "D.Y. Chandrachud; Sanjiv Khanna; B.R. Gavai",
                "author_judge": "D.Y. Chandrachud",
                "bench_strength": 7,
                "decision_date": "2024-04-10",
                "year": 2024,
                "disposal_nature": "Allowed",
                "case_type": "Curative Petition",
                "acts_cited": ["Constitution", "IPC", "BNS"],
                "text": (
                    "1. Applying Kesavananda Bharati (AIR 1973 SC 1461) and Justice K.S. Puttaswamy (2017 INSC 801).\n"
                    "2. Also affirming Shreya Singhal (2015 INSC 254) on Article 19(1)(a) and Section 66A ITAct."
                ),
                "source_id": "dattam_sc_judgments",
                "commercial_safe": True,
            }
        )

    return bundle


def _score_ids(expected_ids: set[str], retrieved_ids: list[str]) -> tuple[float, float]:
    if not expected_ids:
        return 1.0, 1.0
    hits = [rid for rid in retrieved_ids[:10] if rid in expected_ids]
    recall = 1.0 if hits else 0.0
    mrr = 0.0
    for idx, rid in enumerate(retrieved_ids[:10], start=1):
        if rid in expected_ids:
            mrr = 1.0 / idx
            break
    return recall, mrr


def _mean(vals: list[float]) -> float:
    return (sum(vals) / len(vals)) if vals else 0.0


def run_coverage_relevance_eval() -> CoverageEvalReport:
    """Build an offline pillar-complete corpus and run all Suite 3 evaluations."""
    report = CoverageEvalReport()

    with tempfile.TemporaryDirectory(prefix="nyaya_eval_cov_") as tmp:
        root = Path(tmp)
        bundle = _build_eval_bundle()
        sqlite_path = root / "nyaya-core.sqlite"
        lance_dir = root / "lancedb"
        embedder = HashedNgramEmbeddingProvider(dim=256)

        build_sqlite_core(sqlite_path, input_data=bundle)
        build_lance_fts_tables(lance_dir, input_data=bundle, embedding_provider=embedder)

        backend = SqliteStore(sqlite_path)
        lance = LanceStore(local_dir=lance_dir, offline_only=True, embedding_provider=embedder)
        lex_engine: LexicalRetrievalEngine = build_retrieval_engine(
            _make_settings(root, hybrid_search=False), backend, lance=lance
        )
        hyb_engine: LexicalRetrievalEngine = build_retrieval_engine(
            _make_settings(root, hybrid_search=True), backend, lance=lance, embedder=embedder
        )

        storage.set_backend(backend)
        storage.set_retrieval(lex_engine)
        try:
            # 1. Consumer Commission (NCDRC) & NCLAT judgment search
            trib_cases: list[tuple[str, JudgmentSearchQuery, set[str]]] = [
                ("ncdrc-homebuyer-refund", JudgmentSearchQuery(query="residential apartment possession refund deficiency in service", court_tier="tribunal", court="NCDRC", limit=10), {"trib:ncdrc:2024:22"}),
                ("ncdrc-unfair-trade", JudgmentSearchQuery(query="forfeiture earnest money unfair trade practice", court_tier="tribunal", court="NCDRC", limit=10), {"trib:ncdrc:2024:99"}),
                ("ncdrc-consumer-protection", JudgmentSearchQuery(query="Consumer Protection Act refund", court_tier="tribunal", court="NCDRC", limit=10), {"trib:ncdrc:2024:22"}),
                ("nclat-resolution-plan", JudgmentSearchQuery(query="Resolution plan Insolvency Bankruptcy Code", court_tier="tribunal", court="NCLAT", limit=10), {"trib:nclat:2024:10"}),
                ("nclat-coc-wisdom", JudgmentSearchQuery(query="Committee of Creditors commercial wisdom CIRP", court_tier="tribunal", court="NCLAT", limit=10), {"trib:nclat:2024:10"}),
                ("tribunal-cross-tier-ncdrc", JudgmentSearchQuery(query="inordinate delay residential apartment possession deficiency in service", court_tier="all", limit=10), {"trib:ncdrc:2024:22"}),
            ]
            t_rec, t_mrr, t_lat = [], [], []
            t_pass = 0
            for cid, q, exp in trib_cases:
                t0 = time.perf_counter()
                resp = lex_engine.search_judgments(q)
                dt = (time.perf_counter() - t0) * 1000.0
                r, m = _score_ids(exp, [c.case_id for c in resp.results])
                t_rec.append(r)
                t_mrr.append(m)
                t_lat.append(dt)
                if r >= 1.0:
                    t_pass += 1
                else:
                    report.failures.append(f"tribunal:{cid} got {[c.case_id for c in resp.results]}")
            report.pillar_metrics.append(
                PillarMetrics("Consumer (NCDRC) & NCLAT", len(trib_cases), t_pass, _mean(t_rec), _mean(t_mrr), _mean(t_lat))
            )

            # 2. CPC First Schedule
            cpc_cases: list[tuple[str, str, set[str]]] = [
                ("cpc-o7-r11", "Order 7 Rule 11 CPC rejection of plaint", {"cpc-1908:O7-R11"}),
                ("cpc-o39-r1", "O.39 R.1 CPC temporary injunction", {"cpc-1908:O39-R1"}),
                ("cpc-o1-r10", "Order 1 Rule 10 CPC necessary party", {"cpc-1908:O1-R10"}),
                ("cpc-o6-r17", "Order 6 Rule 17 CPC amendment of pleadings", {"cpc-1908:O6-R17"}),
                ("cpc-o9-r13", "Order 9 Rule 13 CPC setting aside decree ex parte", {"cpc-1908:O9-R13"}),
                ("cpc-o21-r1", "Order 21 Rule 1 CPC modes of paying money under decree", {"cpc-1908:O21-R1"}),
                ("cpc-o38-r5", "Order 38 Rule 5 CPC attachment before judgment", {"cpc-1908:O38-R5"}),
                ("cpc-o47-r1", "Order 47 Rule 1 CPC review of judgment", {"cpc-1908:O47-R1"}),
            ]
            c_rec, c_mrr, c_lat = [], [], []
            c_pass = 0
            for cid, q_str, exp in cpc_cases:
                t0 = time.perf_counter()
                s_resp = lex_engine.search_statutes(StatuteSearchQuery(query=q_str, act="CPC", limit=10))
                dt = (time.perf_counter() - t0) * 1000.0
                r, m = _score_ids(exp, [h.doc_id for h in s_resp.results])
                c_rec.append(r)
                c_mrr.append(m)
                c_lat.append(dt)
                if r >= 1.0:
                    c_pass += 1
                else:
                    report.failures.append(f"cpc:{cid} got {[h.doc_id for h in s_resp.results]}")
            report.pillar_metrics.append(
                PillarMetrics("CPC First Schedule", len(cpc_cases), c_pass, _mean(c_rec), _mean(c_mrr), _mean(c_lat))
            )

            # 3. Income-tax Act 1961 <-> 2025 comparison + BNSS offence_classification
            tax_bnss_cases: list[tuple[str, str, str, bool]] = [
                ("ITAct1961 s.4", "ITAct2025", "4", False),
                ("ITAct1961 s.80C", "ITAct2025", "123", False),
                ("ITAct1961 s.139", "ITAct2025", "263", False),
                ("Income Tax Act 1961 s.143", "ITAct2025", "270", False),
                ("ITAct1961 s.147", "ITAct2025", "279", False),
                ("ITAct1961 s.148", "ITAct2025", "280", False),
                ("ITAct1961 s.270A", "ITAct2025", "439", False),
                ("IPC s.302", "BNS", "103(1)", True),
                ("IPC s.420", "BNS", "318(4)", True),
                ("IPC s.376", "BNS", "64", True),
                ("IPC s.304B", "BNS", "80", True),
                ("IPC s.498A", "BNS", "85", True),
            ]
            tb_rec, tb_mrr, tb_lat = [], [], []
            tb_pass = 0
            for src, exp_act, exp_ref, need_oc in tax_bnss_cases:
                t0 = time.perf_counter()
                comp = compare_provisions(source=src)
                dt = (time.perf_counter() - t0) * 1000.0
                tgt_pairs = [(t.act, t.ref) for t in comp.target_provisions]
                matched = (exp_act, exp_ref) in tgt_pairs
                oc_ok = True
                if need_oc:
                    if comp.offence_classification is None:
                        oc_ok = False
                    else:
                        oc = (
                            comp.offence_classification
                            if isinstance(comp.offence_classification, OffenceClassification)
                            else OffenceClassification.model_validate(comp.offence_classification)
                        )
                        oc_ok = bool(oc.punishment and oc.triable_by)
                ok = matched and oc_ok
                tb_rec.append(1.0 if ok else 0.0)
                tb_mrr.append(1.0 if ok else 0.0)
                tb_lat.append(dt)
                if ok:
                    tb_pass += 1
                else:
                    report.failures.append(f"tax_bnss:{src} matched={matched} oc_ok={oc_ok} targets={tgt_pairs}")
            report.pillar_metrics.append(
                PillarMetrics("ITAct 1961<->2025 & BNSS", len(tax_bnss_cases), tb_pass, _mean(tb_rec), _mean(tb_mrr), _mean(tb_lat))
            )

            # 4. All 25 High Court neutral citations
            hc_rec, hc_mrr, hc_lat = [], [], []
            hc_pass = 0
            for case_id, _code, _cname, nc, _summary in ALL_25_HC_SAMPLES:
                t0 = time.perf_counter()
                resp = lex_engine.search_judgments(JudgmentSearchQuery(query=nc, court_tier="high_court", limit=10))
                dt = (time.perf_counter() - t0) * 1000.0
                r, m = _score_ids({case_id}, [c.case_id for c in resp.results])
                hc_rec.append(r)
                hc_mrr.append(m)
                hc_lat.append(dt)
                if r >= 1.0:
                    hc_pass += 1
                else:
                    report.failures.append(f"hc25:{nc} expected={case_id} got={[c.case_id for c in resp.results]}")
            report.pillar_metrics.append(
                PillarMetrics("25-HC Neutral Citations", len(ALL_25_HC_SAMPLES), hc_pass, _mean(hc_rec), _mean(hc_mrr), _mean(hc_lat))
            )

            # 5. Point-in-time `as_of` accuracy across historical dates
            pit_cases: list[tuple[str, str, str, bool]] = [
                ("IPC s.302", "2024-06-30", "in_force", False),
                ("IPC s.302", "2024-07-01", "repealed", False),
                ("ITAct s.66A", "2015-03-23", "in_force", False),
                ("ITAct s.66A", "2015-03-24", "struck_down", True),
                ("IPC s.377", "2018-09-05", "in_force", False),
                ("IPC s.377", "2018-09-06", "read_down", True),
                ("Article 21A", "2001-01-01", "not_yet_in_force", False),
                ("Article 21A", "2015-01-01", "in_force", False),
                ("ITAct1961 s.139", "2026-03-31", "in_force", False),
                ("ITAct1961 s.139", "2026-04-01", "repealed", False),
            ]
            pit_rec, pit_mrr, pit_lat = [], [], []
            pit_pass = 0
            for cit, dt_str, exp_status, exp_inval in pit_cases:
                t0 = time.perf_counter()
                presp = get_provision(citation=cit, as_of=dt_str)
                dt = (time.perf_counter() - t0) * 1000.0
                pit_meta = (
                    presp.as_of_meta
                    if isinstance(presp.as_of_meta, PointInTimeMeta)
                    else None
                )
                inval_present = bool(pit_meta and pit_meta.active_invalidation is not None)
                effective_status = pit_meta.effective_status if pit_meta else presp.validity.status
                ok = (pit_meta is not None) and (effective_status == exp_status) and (inval_present == exp_inval)
                pit_rec.append(1.0 if ok else 0.0)
                pit_mrr.append(1.0 if ok else 0.0)
                pit_lat.append(dt)
                if ok:
                    pit_pass += 1
                else:
                    report.failures.append(
                        f"as_of:{cit}@{dt_str} got status={effective_status} inval={inval_present} expected={exp_status}/{exp_inval}"
                    )
            report.pillar_metrics.append(
                PillarMetrics("Point-in-Time as_of", len(pit_cases), pit_pass, _mean(pit_rec), _mean(pit_mrr), _mean(pit_lat))
            )

            # 6. Scaled citation graph `trace_precedent` recall
            graph_cases: list[tuple[str, str, set[str]]] = [
                ("sc:1973:kesavananda", "cited_by", {"sc:2017:puttaswamy", "sc:2024:curative"}),
                ("sc:2017:puttaswamy", "cited_by", {"hc:2024:dhc:1234", "sc:2024:curative"}),
                ("sc:2015:shreya-singhal", "cited_by", {"sc:2024:curative"}),
                ("sc:2024:curative", "cites", {"sc:1973:kesavananda", "sc:2017:puttaswamy", "sc:2015:shreya-singhal"}),
                ("hc:2024:dhc:1234", "cited_by", {"hc:2024:ahc:107", "hc:2024:mad:104"}),
                ("hc:2024:dhc:1234", "cites", {"sc:2017:puttaswamy"}),
            ]
            g_rec, g_mrr, g_lat = [], [], []
            g_pass = 0
            for ident, direction, exp_ids in graph_cases:
                t0 = time.perf_counter()
                tr_resp = trace_precedent(identifier=ident, direction=direction, limit=25)  # type: ignore[arg-type]
                dt = (time.perf_counter() - t0) * 1000.0
                got_ids = [e.related_case.case_id for e in tr_resp.edges]
                r, m = _score_ids(exp_ids, got_ids)
                g_rec.append(r)
                g_mrr.append(m)
                g_lat.append(dt)
                if r >= 1.0:
                    g_pass += 1
                else:
                    report.failures.append(f"graph:{ident}:{direction} expected={exp_ids} got={got_ids}")
            report.pillar_metrics.append(
                PillarMetrics("Scaled Citation Graph", len(graph_cases), g_pass, _mean(g_rec), _mean(g_mrr), _mean(g_lat))
            )

            # 7. CAD debates & cause-list lookup recall
            cc_rec, cc_mrr, cc_lat = [], [], []
            cc_pass = 0
            cad_queries = [
                ("cad-art21", "Draft Article 15 procedure established by law", {"cad-vol7-1948-12-06:0"}),
                ("cad-art32", "Draft Article 25 soul of the Constitution", {"cad-vol7-1948-12-09:0"}),
                ("cad-detention", "arbitrary executive detention personal liberty", {"cad-vol7-1948-12-06:0"}),
            ]
            for cid, q_str, exp in cad_queries:
                t0 = time.perf_counter()
                s_resp = lex_engine.search_statutes(StatuteSearchQuery(query=q_str, limit=10))
                dt = (time.perf_counter() - t0) * 1000.0
                r, m = _score_ids(exp, [h.doc_id for h in s_resp.results])
                cc_rec.append(r)
                cc_mrr.append(m)
                cc_lat.append(dt)
                if r >= 1.0:
                    cc_pass += 1
                else:
                    report.failures.append(f"cad:{cid} got {[h.doc_id for h in s_resp.results]}")

            cl_queries: list[tuple[str, dict[str, Any], str]] = [
                ("cl-sci-court", {"court": "Supreme Court of India", "date": "2026-11-26"}, "W.P.(C) No. 1012/2026"),
                ("cl-sci-party", {"party": "Bar Council"}, "SCIN010010122026"),
                ("cl-dhc-court", {"court": "High Court of Delhi"}, "W.P.(C) 4421/2026"),
                ("cl-dhc-advocate", {"advocate": "Meera Krishnan"}, "DLHC010044212026"),
                ("cl-dhc-cnr", {"cnr": "DLHC010044212026"}, "W.P.(C) 4421/2026"),
            ]
            for cid, kwargs, exp_token in cl_queries:
                t0 = time.perf_counter()
                cl_resp = get_cause_list(**kwargs)
                dt = (time.perf_counter() - t0) * 1000.0
                haystacks = [f"{e.case_number or ''} {e.cnr or ''}" for e in cl_resp.entries]
                matched = any(exp_token in h for h in haystacks)
                rank_mrr = next((1.0 / i for i, h in enumerate(haystacks, start=1) if exp_token in h), 0.0)
                cc_rec.append(1.0 if matched else 0.0)
                cc_mrr.append(rank_mrr)
                cc_lat.append(dt)
                if matched:
                    cc_pass += 1
                else:
                    report.failures.append(f"causelist:{cid} expected={exp_token} got={haystacks}")
            report.pillar_metrics.append(
                PillarMetrics("CAD Debates & Cause Lists", len(cad_queries) + len(cl_queries), cc_pass, _mean(cc_rec), _mean(cc_mrr), _mean(cc_lat))
            )

            # 8. Lexical-only vs Opt-in Hybrid Semantic+BM25
            exact_suite: list[tuple[str, str, set[str]]] = [
                ("2017 INSC 801", "supreme_court", {"sc:2017:puttaswamy"}),
                ("AIR 1973 SC 1461", "supreme_court", {"sc:1973:kesavananda"}),
                ("2015 INSC 254", "supreme_court", {"sc:2015:shreya-singhal"}),
                ("2024:DHC:1234", "high_court", {"hc:2024:dhc:1234"}),
                ("2024:BHC-OS:567", "high_court", {"hc:2024:bhc:567"}),
                ("2024:MHC:104", "high_court", {"hc:2024:mad:104"}),
                ("2024:KHC:105", "high_court", {"hc:2024:kar:105"}),
                ("2024:AHC:107", "high_court", {"hc:2024:ahc:107"}),
                ("2024:CAL:126", "high_court", {"hc:2024:cal:126"}),
                ("2024:GUJ:112", "high_court", {"hc:2024:guj:112"}),
            ]

            conceptual_suite: list[tuple[str, str, set[str]]] = [
                # Synonym & paraphrase queries (<4 content tokens so hybrid vector hits surface cleanly)
                ("pre-arrest bail", "high_court", {"hc:2024:dhc:1234"}),
                ("apprehending arrest", "high_court", {"hc:2024:dhc:1234"}),
                ("dishonour of cheque", "high_court", {"hc:2024:mad:104"}),
                ("culpable homicide", "supreme_court", {"ildc:2020:001"}),
                ("defamation slander", "high_court", {"hc:2024:raj:121"}),
                ("electronic evidence", "high_court", {"hc:2024:tshc:124"}),
                ("maintenance alimony", "high_court", {"hc:2024:phc:120"}),
                ("consumer compensation", "tribunal", {"trib:ncdrc:2024:22", "trib:ncdrc:2024:99"}),
                ("insolvency resolution", "tribunal", {"trib:nclat:2024:10"}),
                ("green tribunal", "tribunal", {"trib:ngt:2024:15"}),
                ("basic structure", "supreme_court", {"sc:1973:kesavananda"}),
                ("privacy liberty", "supreme_court", {"sc:2017:puttaswamy"}),
            ]

            for suite_name, cases in (
                ("Exact Citation Queries", exact_suite),
                ("Conceptual/Paraphrased Queries", conceptual_suite),
            ):
                lex_r, lex_m, hyb_r, hyb_m = [], [], [], []
                for q_str, c_tier, exp in cases:
                    jq = JudgmentSearchQuery(query=q_str, court_tier=c_tier, commercial_safe_only=False, limit=10)  # type: ignore[arg-type]
                    l_res = lex_engine.search_judgments(jq)
                    h_res = hyb_engine.search_judgments(jq)
                    lr, lm = _score_ids(exp, [c.case_id for c in l_res.results])
                    hr, hm = _score_ids(exp, [c.case_id for c in h_res.results])
                    lex_r.append(lr)
                    lex_m.append(lm)
                    hyb_r.append(hr)
                    hyb_m.append(hm)

                summary = HybridComparisonSummary(
                    suite_name=suite_name,
                    cases=len(cases),
                    lexical_recall=_mean(lex_r),
                    lexical_mrr=_mean(lex_m),
                    hybrid_recall=_mean(hyb_r),
                    hybrid_mrr=_mean(hyb_m),
                    recall_lift_pp=(_mean(hyb_r) - _mean(lex_r)) * 100.0,
                    mrr_lift=_mean(hyb_m) - _mean(lex_m),
                )
                report.hybrid_comparisons.append(summary)
                if suite_name == "Exact Citation Queries" and (
                    summary.hybrid_recall < summary.lexical_recall or summary.hybrid_mrr < summary.lexical_mrr
                ):
                    report.failures.append(
                        f"hybrid_exact_regression: lex=({summary.lexical_recall:.3f},{summary.lexical_mrr:.3f}) "
                        f"hyb=({summary.hybrid_recall:.3f},{summary.hybrid_mrr:.3f})"
                    )
                if suite_name == "Conceptual/Paraphrased Queries" and summary.recall_lift_pp <= 0.0:
                    report.failures.append(
                        f"hybrid_conceptual_no_lift: lex={summary.lexical_recall:.3f} hyb={summary.hybrid_recall:.3f}"
                    )

            for pm in report.pillar_metrics:
                if pm.mean_recall < 0.85 or pm.mean_mrr < 0.70 or math.isnan(pm.mean_recall):
                    report.failures.append(
                        f"pillar_below_threshold:{pm.pillar} recall={pm.mean_recall:.2%} mrr={pm.mean_mrr:.2%}"
                    )

            return report
        finally:
            storage.set_retrieval(None)
            storage.set_backend(None)
            lance.close()
            backend.close()


def format_report(report: CoverageEvalReport) -> str:
    lines = [
        "==============================================================================",
        "Nyaya v2.1 New-Pillar Relevance & Lexical-vs-Hybrid Evaluation",
        "==============================================================================",
        f"{'Pillar':<28} | {'Cases':>5} | {'Pass':>5} | {'Recall@10':>9} | {'MRR@10':>8} | {'Mean ms':>8}",
        "-" * 78,
    ]
    for pm in report.pillar_metrics:
        lines.append(
            f"{pm.pillar:<28} | {pm.cases:>5} | {pm.passed:>5} | {pm.mean_recall * 100:>8.2f}% | {pm.mean_mrr:>8.4f} | {pm.mean_latency_ms:>7.2f}"
        )
    lines.extend(
        [
            "",
            f"{'Lexical vs Hybrid Suite':<31} | {'N':>3} | {'Lex Rec@10':>10} | {'Hyb Rec@10':>10} | {'Lex MRR':>8} | {'Hyb MRR':>8} | {'Lift':>8}",
            "-" * 92,
        ]
    )
    for hc in report.hybrid_comparisons:
        lines.append(
            f"{hc.suite_name:<31} | {hc.cases:>3} | {hc.lexical_recall * 100:>9.2f}% | {hc.hybrid_recall * 100:>9.2f}% | "
            f"{hc.lexical_mrr:>8.4f} | {hc.hybrid_mrr:>8.4f} | {hc.recall_lift_pp:>+6.2f}pp"
        )
    lines.append("=" * 92)
    if report.failures:
        lines.append("FAILURES:")
        for f in report.failures:
            lines.append(f"  - {f}")
    else:
        lines.append("PASS: All new-pillar relevance thresholds (>=85% Recall@10, >=70% MRR@10) and Hybrid lift verified.")
    return "\n".join(lines)


def main() -> int:
    report = run_coverage_relevance_eval()
    print(format_report(report))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
