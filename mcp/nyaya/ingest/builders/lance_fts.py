"""LanceDB native Rust BM25 FTS table builder for `sc_chunks`, `hc_hot_chunks`, and `state_acts_regulators`."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import lancedb
import pyarrow as pa

from ...storage.lance_store import (
    HC_HOT_CHUNKS_TABLE,
    SC_CHUNKS_TABLE,
    STATE_ACTS_REGULATORS_TABLE,
)
from ..extract.chunker import chunk_legal_text
from ..extract.normalize import normalize_legal_record
from ..state import IngestState, dedupe_and_merge_hc_rows
from .sqlite_core import IngestInputBundle, create_mini_ingest_input


def build_lance_fts_tables(
    lance_dir: Path | str,
    *,
    input_data: IngestInputBundle | None = None,
    state: IngestState | None = None,
    commercial_safe_only: bool = False,
    hot_hc_limit: int = 200_000,
) -> dict[str, int]:
    """Build the 3 canonical LanceDB tables and create native Rust FTS indexes (`with_position=True`)."""
    bundle = input_data or create_mini_ingest_input()
    out_dir = Path(lance_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(out_dir))

    # 1. Build `sc_chunks` (SC judgments + ILDC rows tagged commercial_safe=False)
    sc_records: list[dict[str, Any]] = []
    sc_inputs = [*bundle.sc_judgments, *bundle.ildc_rows]
    for j in sc_inputs:
        comm_safe = bool(j.get("commercial_safe", True))
        if commercial_safe_only and not comm_safe:
            continue
        case_id = str(j["case_id"])
        raw_text = str(j.get("text") or j.get("title") or "")
        norm = normalize_legal_record(raw_text, doc_id=case_id, metadata=j)
        if norm.blocked_by_publication_ban:
            continue

        chunks = chunk_legal_text(norm.cleaned_text, doc_id=case_id)
        for ch in chunks:
            sc_records.append(
                {
                    "chunk_id": f"{case_id}:{ch.chunk_index}",
                    "case_id": case_id,
                    "year": int(j.get("year") or 2020),
                    "decision_date": str(j.get("decision_date") or ""),
                    "title": str(j.get("title") or ""),
                    "neutral_citation": str(j.get("neutral_citation") or ""),
                    "air_citation": str(j.get("air_citation") or ""),
                    "scc_citation": str(j.get("scc_citation") or ""),
                    "author_judge": str(j.get("author_judge") or j.get("judge") or ""),
                    "bench_strength": int(j.get("bench_strength") or 1),
                    "opinion_type": "majority",
                    "para_start": int(ch.para_start),
                    "para_end": int(ch.para_end),
                    "page_start": int(ch.page_start),
                    "page_end": int(ch.page_end),
                    "acts_cited": [str(a) for a in (j.get("acts_cited") or [])],
                    "text": ch.text,
                    "commercial_safe": comm_safe,
                    "terms_status": str(
                        j.get("terms_status")
                        or ("cleared" if comm_safe else "non_commercial_only")
                    ),
                    "license_spdx": str(
                        j.get("license_spdx") or ("CC-BY-4.0" if comm_safe else "CC-BY-NC-4.0")
                    ),
                    "source_id": str(
                        j.get("source_id") or ("dattam_sc_judgments" if comm_safe else "ildc")
                    ),
                    "source_url": str(
                        j.get("source_url")
                        or "https://indian-supreme-court-judgments.s3.ap-south-1.amazonaws.com"
                    ),
                    "text_sha256": ch.chunk_sha256,
                }
            )
        if state is not None:
            state.mark_doc_indexed(case_id, norm.text_sha256)

    sc_table_data = pa.Table.from_pylist(sc_records)
    t_sc = db.create_table(SC_CHUNKS_TABLE, data=sc_table_data, mode="overwrite")
    t_sc.create_fts_index("text", replace=True, with_position=True)

    # 2. Build `hc_hot_chunks`
    hc_records: list[dict[str, Any]] = []
    merged_hc = dedupe_and_merge_hc_rows(bundle.hc_web_rows, bundle.hc_mobile_rows)[:hot_hc_limit]
    for j in merged_hc:
        comm_safe = bool(j.get("commercial_safe", True))
        if commercial_safe_only and not comm_safe:
            continue
        case_id = str(j["case_id"])
        raw_text = str(j.get("text") or j.get("title") or "")
        norm = normalize_legal_record(raw_text, doc_id=case_id, metadata=j)
        if norm.blocked_by_publication_ban:
            continue
        chunks = chunk_legal_text(norm.cleaned_text, doc_id=case_id)
        for ch in chunks:
            hc_records.append(
                {
                    "chunk_id": f"{case_id}:{ch.chunk_index}",
                    "case_id": case_id,
                    "year": int(j.get("year") or 2024),
                    "decision_date": str(j.get("decision_date") or ""),
                    "title": str(j.get("title") or ""),
                    "neutral_citation": str(j.get("neutral_citation") or ""),
                    "air_citation": str(j.get("air_citation") or ""),
                    "scc_citation": str(j.get("scc_citation") or ""),
                    "author_judge": str(j.get("author_judge") or j.get("judge") or ""),
                    "bench_strength": int(j.get("bench_strength") or 1),
                    "opinion_type": "majority",
                    "para_start": int(ch.para_start),
                    "para_end": int(ch.para_end),
                    "page_start": int(ch.page_start),
                    "page_end": int(ch.page_end),
                    "acts_cited": [str(a) for a in (j.get("acts_cited") or [])],
                    "text": ch.text,
                    "commercial_safe": comm_safe,
                    "terms_status": str(j.get("terms_status") or "cleared"),
                    "license_spdx": str(j.get("license_spdx") or "CC-BY-4.0"),
                    "source_id": str(j.get("source_id") or "dattam_hc_judgments"),
                    "source_url": str(
                        j.get("source_url")
                        or "https://indian-high-court-judgments.s3.ap-south-1.amazonaws.com"
                    ),
                    "text_sha256": ch.chunk_sha256,
                    "court_code": str(j.get("court_code") or "7_26"),
                    "bench": str(j.get("bench") or "Principal"),
                    "cnr": str(j.get("cnr") or ""),
                    "disposal_nature": str(j.get("disposal_nature") or ""),
                    "is_reportable": bool(j.get("is_reportable", True)),
                }
            )
        if state is not None:
            state.mark_doc_indexed(case_id, norm.text_sha256)

    hc_table_data = pa.Table.from_pylist(hc_records)
    t_hc = db.create_table(HC_HOT_CHUNKS_TABLE, data=hc_table_data, mode="overwrite")
    t_hc.create_fts_index("text", replace=True, with_position=True)

    # 3. Build `state_acts_regulators`
    sr_records: list[dict[str, Any]] = []
    for p in bundle.state_regulator_provisions:
        comm_safe = bool(p.get("commercial_safe", True))
        if commercial_safe_only and not comm_safe:
            continue
        doc_id = str(p["doc_id"])
        norm = normalize_legal_record(str(p.get("text") or ""), doc_id=doc_id, metadata=p)
        if norm.blocked_by_publication_ban:
            continue
        sr_records.append(
            {
                "doc_id": doc_id,
                "act_id": str(p["act_id"]),
                "short_name": str(p["short_name"]),
                "jurisdiction": str(p.get("jurisdiction") or "Central"),
                "category": str(p.get("category") or "state_act"),
                "kind": str(p.get("kind") or "section"),
                "ref": str(p["ref"]),
                "title": str(p.get("title") or ""),
                "status": str(p.get("status") or "in_force"),
                "effective_date": str(p.get("effective_date") or ""),
                "text": norm.cleaned_text,
                "commercial_safe": comm_safe,
                "terms_status": str(p.get("terms_status") or "attribution_required"),
                "license_spdx": str(p.get("license_spdx") or "CC-BY-4.0"),
                "source_id": str(p.get("source_id") or "vaquill_state_legislation"),
                "source_url": str(
                    p.get("source_url")
                    or "https://oss-data-in.vaquill.ai/v2026.08.1/in_state_legislation.parquet"
                ),
                "text_sha256": norm.text_sha256,
            }
        )

    sr_table_data = pa.Table.from_pylist(sr_records)
    t_sr = db.create_table(STATE_ACTS_REGULATORS_TABLE, data=sr_table_data, mode="overwrite")
    t_sr.create_fts_index("text", replace=True, with_position=True)

    return {
        SC_CHUNKS_TABLE: len(sc_records),
        HC_HOT_CHUNKS_TABLE: len(hc_records),
        STATE_ACTS_REGULATORS_TABLE: len(sr_records),
    }
