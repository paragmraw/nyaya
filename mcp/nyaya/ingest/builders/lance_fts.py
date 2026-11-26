"""LanceDB native Rust BM25 FTS + optional vector table builder for SC, HC, State/Regulators, Tribunals, and CAD Debates."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Iterator, Mapping
from pathlib import Path
from typing import Any

import lancedb
import pyarrow as pa

from ...retrieval.hybrid import EmbeddingProvider
from ...storage.lance_store import (
    CAD_DEBATES_TABLE,
    HC_HOT_CHUNKS_TABLE,
    SC_CHUNKS_TABLE,
    STATE_ACTS_REGULATORS_TABLE,
    TRIBUNAL_CHUNKS_TABLE,
)
from ..extract.chunker import chunk_legal_text
from ..extract.normalize import normalize_legal_record
from ..state import IngestState, dedupe_and_merge_hc_rows
from .sqlite_core import IngestInputBundle, create_mini_ingest_input

_SC_CHUNKS_SCHEMA = pa.schema(
    [
        pa.field("chunk_id", pa.string()),
        pa.field("case_id", pa.string()),
        pa.field("year", pa.int64()),
        pa.field("decision_date", pa.string()),
        pa.field("title", pa.string()),
        pa.field("neutral_citation", pa.string()),
        pa.field("air_citation", pa.string()),
        pa.field("scc_citation", pa.string()),
        pa.field("author_judge", pa.string()),
        pa.field("bench_strength", pa.int64()),
        pa.field("opinion_type", pa.string()),
        pa.field("para_start", pa.int64()),
        pa.field("para_end", pa.int64()),
        pa.field("page_start", pa.int64()),
        pa.field("page_end", pa.int64()),
        pa.field("acts_cited", pa.list_(pa.string())),
        pa.field("text", pa.string()),
        pa.field("commercial_safe", pa.bool_()),
        pa.field("terms_status", pa.string()),
        pa.field("license_spdx", pa.string()),
        pa.field("source_id", pa.string()),
        pa.field("source_url", pa.string()),
        pa.field("text_sha256", pa.string()),
    ]
)

_HC_HOT_CHUNKS_SCHEMA = pa.schema(
    [
        *_SC_CHUNKS_SCHEMA,
        pa.field("court_code", pa.string()),
        pa.field("bench", pa.string()),
        pa.field("cnr", pa.string()),
        pa.field("disposal_nature", pa.string()),
        pa.field("is_reportable", pa.bool_()),
    ]
)

_STATE_ACTS_REGULATORS_SCHEMA = pa.schema(
    [
        pa.field("doc_id", pa.string()),
        pa.field("act_id", pa.string()),
        pa.field("short_name", pa.string()),
        pa.field("jurisdiction", pa.string()),
        pa.field("category", pa.string()),
        pa.field("kind", pa.string()),
        pa.field("ref", pa.string()),
        pa.field("title", pa.string()),
        pa.field("status", pa.string()),
        pa.field("effective_date", pa.string()),
        pa.field("text", pa.string()),
        pa.field("commercial_safe", pa.bool_()),
        pa.field("terms_status", pa.string()),
        pa.field("license_spdx", pa.string()),
        pa.field("source_id", pa.string()),
        pa.field("source_url", pa.string()),
        pa.field("text_sha256", pa.string()),
    ]
)

_TRIBUNAL_CHUNKS_SCHEMA = pa.schema(
    [
        pa.field("chunk_id", pa.string()),
        pa.field("case_id", pa.string()),
        pa.field("forum_code", pa.string()),
        pa.field("court_code", pa.string()),
        pa.field("court_name", pa.string()),
        pa.field("bench", pa.string()),
        pa.field("year", pa.int64()),
        pa.field("decision_date", pa.string()),
        pa.field("title", pa.string()),
        pa.field("neutral_citation", pa.string()),
        pa.field("author_judge", pa.string()),
        pa.field("case_type", pa.string()),
        pa.field("disposal_nature", pa.string()),
        pa.field("acts_cited", pa.list_(pa.string())),
        pa.field("para_start", pa.int64()),
        pa.field("para_end", pa.int64()),
        pa.field("text", pa.string()),
        pa.field("commercial_safe", pa.bool_()),
        pa.field("terms_status", pa.string()),
        pa.field("license_spdx", pa.string()),
        pa.field("source_id", pa.string()),
        pa.field("source_url", pa.string()),
        pa.field("text_sha256", pa.string()),
    ]
)

_CAD_DEBATES_SCHEMA = pa.schema(
    [
        pa.field("chunk_id", pa.string()),
        pa.field("debate_id", pa.string()),
        pa.field("volume", pa.string()),
        pa.field("sitting_date", pa.string()),
        pa.field("year", pa.int64()),
        pa.field("speaker", pa.string()),
        pa.field("article_ref", pa.string()),
        pa.field("title", pa.string()),
        pa.field("text", pa.string()),
        pa.field("commercial_safe", pa.bool_()),
        pa.field("terms_status", pa.string()),
        pa.field("license_spdx", pa.string()),
        pa.field("source_id", pa.string()),
        pa.field("source_url", pa.string()),
        pa.field("text_sha256", pa.string()),
    ]
)


def _schema_with_optional_vector(
    base_schema: pa.Schema,
    embedding_provider: EmbeddingProvider | None,
) -> pa.Schema:
    if embedding_provider is None:
        return base_schema
    return pa.schema([*base_schema, pa.field("vector", pa.list_(pa.float32(), embedding_provider.dim))])


def _attach_embeddings(
    batch: list[dict[str, Any]],
    embedding_provider: EmbeddingProvider | None,
) -> None:
    if embedding_provider is None or not batch:
        return
    vecs = embedding_provider.embed([str(rec.get("text") or "") for rec in batch])
    for rec, vec in zip(batch, vecs, strict=True):
        rec["vector"] = [float(x) for x in vec]


def _write_lance_table_batched(
    db: Any,
    table_name: str,
    records: Iterable[dict[str, Any]],
    schema: pa.Schema,
    *,
    batch_size: int = 10_000,
    embedding_provider: EmbeddingProvider | None = None,
) -> int:
    eff_schema = _schema_with_optional_vector(schema, embedding_provider)
    tbl: Any = None
    batch: list[dict[str, Any]] = []
    total = 0

    for rec in records:
        batch.append(rec)
        if len(batch) >= batch_size:
            _attach_embeddings(batch, embedding_provider)
            pa_tbl = pa.Table.from_pylist(batch, schema=eff_schema)
            if tbl is None:
                tbl = db.create_table(table_name, data=pa_tbl, mode="overwrite")
            else:
                tbl.add(pa_tbl)
            total += len(batch)
            batch.clear()

    if batch:
        _attach_embeddings(batch, embedding_provider)
        pa_tbl = pa.Table.from_pylist(batch, schema=eff_schema)
        if tbl is None:
            tbl = db.create_table(table_name, data=pa_tbl, mode="overwrite")
        else:
            tbl.add(pa_tbl)
        total += len(batch)

    if tbl is None:
        tbl = db.create_table(
            table_name,
            data=pa.Table.from_pylist([], schema=eff_schema),
            mode="overwrite",
        )

    if total > 0:
        tbl.create_fts_index("text", replace=True, with_position=True)
    return total


def _iter_sc_records(
    bundle: IngestInputBundle,
    *,
    commercial_safe_only: bool,
    state: IngestState | None,
) -> Iterator[dict[str, Any]]:
    sc_inputs = [*bundle.sc_judgments, *bundle.ildc_rows]
    for j in sc_inputs:
        comm_safe = bool(j.get("commercial_safe", True))
        if commercial_safe_only and not comm_safe:
            continue
        case_id = str(j["case_id"])
        raw_text = str(j.get("text") or j.get("title") or "")
        norm = normalize_legal_record(raw_text, doc_id=case_id, metadata=j)
        if norm.blocked_by_publication_ban or not norm.cleaned_text:
            continue

        chunks = chunk_legal_text(norm.cleaned_text, doc_id=case_id)
        for ch in chunks:
            yield {
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
        if state is not None:
            state.mark_doc_indexed(case_id, norm.text_sha256)


def _iter_hc_records(
    bundle: IngestInputBundle,
    *,
    commercial_safe_only: bool,
    hot_hc_limit: int,
    state: IngestState | None,
) -> Iterator[dict[str, Any]]:
    merged_hc = dedupe_and_merge_hc_rows(bundle.hc_web_rows, bundle.hc_mobile_rows)
    if len(merged_hc) > hot_hc_limit:
        by_court: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in merged_hc:
            if commercial_safe_only and not bool(r.get("commercial_safe", True)):
                continue
            cc = str(r.get("court_code") or "HC")
            by_court[cc].append(r)
        for court_rows in by_court.values():
            court_rows.sort(
                key=lambda r: (
                    1 if bool(r.get("is_reportable")) else 0,
                    int(r.get("bench_strength") or 0),
                    1 if str(r.get("text") or "").strip() else 0,
                    int(r.get("year") or 0),
                    str(r.get("decision_date") or ""),
                ),
                reverse=True,
            )
        court_iters = [iter(by_court[cc]) for cc in sorted(by_court.keys())]
        selected: list[dict[str, Any]] = []
        while len(selected) < hot_hc_limit and court_iters:
            next_iters = []
            for it in court_iters:
                item = next(it, None)
                if item is not None:
                    selected.append(item)
                    next_iters.append(it)
                    if len(selected) >= hot_hc_limit:
                        break
            court_iters = next_iters
        merged_hc = selected

    for j in merged_hc:
        comm_safe = bool(j.get("commercial_safe", True))
        if commercial_safe_only and not comm_safe:
            continue
        case_id = str(j["case_id"])
        raw_text = str(j.get("text") or j.get("title") or "")
        norm = normalize_legal_record(raw_text, doc_id=case_id, metadata=j)
        if norm.blocked_by_publication_ban or not norm.cleaned_text:
            continue
        chunks = chunk_legal_text(norm.cleaned_text, doc_id=case_id)
        for ch in chunks:
            yield {
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
        if state is not None:
            state.mark_doc_indexed(case_id, norm.text_sha256)


def _iter_state_reg_records(
    bundle: IngestInputBundle,
    *,
    commercial_safe_only: bool,
) -> Iterator[dict[str, Any]]:
    for p in bundle.state_regulator_provisions:
        comm_safe = bool(p.get("commercial_safe", True))
        if commercial_safe_only and not comm_safe:
            continue
        doc_id = str(p["doc_id"])
        norm = normalize_legal_record(str(p.get("text") or ""), doc_id=doc_id, metadata=p)
        if norm.blocked_by_publication_ban or not norm.cleaned_text:
            continue
        yield {
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


def _iter_tribunal_records(
    rows: Iterable[Mapping[str, Any]],
    *,
    commercial_safe_only: bool,
    state: IngestState | None,
) -> Iterator[dict[str, Any]]:
    for j in rows:
        comm_safe = bool(j.get("commercial_safe", True))
        if commercial_safe_only and not comm_safe:
            continue
        case_id = str(j.get("case_id") or "").strip()
        if not case_id:
            continue
        raw_text = str(j.get("text") or j.get("title") or "")
        norm = normalize_legal_record(raw_text, doc_id=case_id, metadata=j)
        if norm.blocked_by_publication_ban or not norm.cleaned_text:
            continue
        forum_code = str(j.get("forum_code") or j.get("court_code") or "NCLAT")
        acts_raw = j.get("acts_cited")
        if isinstance(acts_raw, (list, tuple)):
            acts_cited = [str(a) for a in acts_raw]
        elif j.get("act"):
            acts_cited = [str(j["act"])]
        else:
            acts_cited = []

        chunks = chunk_legal_text(norm.cleaned_text, doc_id=case_id)
        for ch in chunks:
            yield {
                "chunk_id": f"{case_id}:{ch.chunk_index}",
                "case_id": case_id,
                "forum_code": forum_code,
                "court_code": str(j.get("court_code") or forum_code),
                "court_name": str(j.get("court_name") or forum_code),
                "bench": str(j.get("bench") or "Principal"),
                "year": int(j.get("year") or 2024),
                "decision_date": str(j.get("decision_date") or ""),
                "title": str(j.get("title") or ""),
                "neutral_citation": str(j.get("neutral_citation") or ""),
                "author_judge": str(j.get("author_judge") or j.get("judge") or ""),
                "case_type": str(j.get("case_type") or ""),
                "disposal_nature": str(j.get("disposal_nature") or ""),
                "acts_cited": acts_cited,
                "para_start": int(ch.para_start),
                "para_end": int(ch.para_end),
                "text": ch.text,
                "commercial_safe": comm_safe,
                "terms_status": str(
                    j.get("terms_status")
                    or ("cleared" if comm_safe else "non_commercial_only")
                ),
                "license_spdx": str(
                    j.get("license_spdx") or ("CC-BY-4.0" if comm_safe else "CC-BY-NC-4.0")
                ),
                "source_id": str(j.get("source_id") or "vaquill_tribunals"),
                "source_url": str(j.get("source_url") or "https://oss-data-in.vaquill.ai"),
                "text_sha256": ch.chunk_sha256,
            }
        if state is not None:
            state.mark_doc_indexed(case_id, norm.text_sha256)


def _iter_cad_records(
    rows: Iterable[Mapping[str, Any]],
    *,
    commercial_safe_only: bool,
    state: IngestState | None,
) -> Iterator[dict[str, Any]]:
    for d in rows:
        comm_safe = bool(d.get("commercial_safe", True))
        if commercial_safe_only and not comm_safe:
            continue
        debate_id = str(d.get("debate_id") or d.get("doc_id") or d.get("id") or "").strip()
        if not debate_id:
            continue
        raw_text = str(d.get("text") or d.get("title") or "")
        norm = normalize_legal_record(raw_text, doc_id=debate_id, metadata=d)
        if norm.blocked_by_publication_ban or not norm.cleaned_text:
            continue
        sitting_date = str(d.get("sitting_date") or d.get("date") or "")
        yr_raw = d.get("year")
        year = (
            int(yr_raw)
            if yr_raw is not None
            else (int(sitting_date[:4]) if sitting_date[:4].isdigit() else 1948)
        )
        chunks = chunk_legal_text(norm.cleaned_text, doc_id=debate_id)
        for ch in chunks:
            yield {
                "chunk_id": f"{debate_id}:{ch.chunk_index}",
                "debate_id": debate_id,
                "volume": str(d.get("volume") or ""),
                "sitting_date": sitting_date,
                "year": year,
                "speaker": str(d.get("speaker") or ""),
                "article_ref": str(d.get("article_ref") or d.get("article") or d.get("ref") or ""),
                "title": str(d.get("title") or ""),
                "text": ch.text,
                "commercial_safe": comm_safe,
                "terms_status": str(
                    d.get("terms_status")
                    or ("cleared" if comm_safe else "non_commercial_only")
                ),
                "license_spdx": str(
                    d.get("license_spdx") or ("IN-S52-1-q" if comm_safe else "CC-BY-NC-4.0")
                ),
                "source_id": str(d.get("source_id") or "cad_debates"),
                "source_url": str(d.get("source_url") or "https://eparlib.nic.in"),
                "text_sha256": ch.chunk_sha256,
            }
        if state is not None:
            state.mark_doc_indexed(debate_id, norm.text_sha256)


def build_lance_fts_tables(
    lance_dir: Path | str,
    *,
    input_data: IngestInputBundle | None = None,
    state: IngestState | None = None,
    commercial_safe_only: bool = False,
    hot_hc_limit: int = 500_000,
    batch_size: int = 10_000,
    tribunal_rows: Iterable[Mapping[str, Any]] | None = None,
    cad_debates: Iterable[Mapping[str, Any]] | None = None,
    embedding_provider: EmbeddingProvider | None = None,
) -> dict[str, int]:
    """Build the 5 canonical LanceDB tables (SC, HC, State/Regulators, Tribunals, CAD Debates)
    with native Rust FTS indexes (``with_position=True``) and optional vector embeddings.
    """
    bundle = input_data or create_mini_ingest_input()
    out_dir = Path(lance_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(out_dir))

    eff_tribunals = bundle.tribunal_rows if tribunal_rows is None else tribunal_rows
    eff_cad = (
        (getattr(bundle, "cad_debates", None) or getattr(bundle, "cad_debate_rows", None) or ())
        if cad_debates is None
        else cad_debates
    )

    sc_count = _write_lance_table_batched(
        db,
        SC_CHUNKS_TABLE,
        _iter_sc_records(bundle, commercial_safe_only=commercial_safe_only, state=state),
        _SC_CHUNKS_SCHEMA,
        batch_size=batch_size,
        embedding_provider=embedding_provider,
    )
    hc_count = _write_lance_table_batched(
        db,
        HC_HOT_CHUNKS_TABLE,
        _iter_hc_records(
            bundle,
            commercial_safe_only=commercial_safe_only,
            hot_hc_limit=hot_hc_limit,
            state=state,
        ),
        _HC_HOT_CHUNKS_SCHEMA,
        batch_size=batch_size,
        embedding_provider=embedding_provider,
    )
    sr_count = _write_lance_table_batched(
        db,
        STATE_ACTS_REGULATORS_TABLE,
        _iter_state_reg_records(bundle, commercial_safe_only=commercial_safe_only),
        _STATE_ACTS_REGULATORS_SCHEMA,
        batch_size=batch_size,
        embedding_provider=embedding_provider,
    )
    trib_count = _write_lance_table_batched(
        db,
        TRIBUNAL_CHUNKS_TABLE,
        _iter_tribunal_records(
            eff_tribunals,
            commercial_safe_only=commercial_safe_only,
            state=state,
        ),
        _TRIBUNAL_CHUNKS_SCHEMA,
        batch_size=batch_size,
        embedding_provider=embedding_provider,
    )
    cad_count = _write_lance_table_batched(
        db,
        CAD_DEBATES_TABLE,
        _iter_cad_records(
            eff_cad,
            commercial_safe_only=commercial_safe_only,
            state=state,
        ),
        _CAD_DEBATES_SCHEMA,
        batch_size=batch_size,
        embedding_provider=embedding_provider,
    )

    return {
        SC_CHUNKS_TABLE: sc_count,
        HC_HOT_CHUNKS_TABLE: hc_count,
        STATE_ACTS_REGULATORS_TABLE: sr_count,
        TRIBUNAL_CHUNKS_TABLE: trib_count,
        CAD_DEBATES_TABLE: cad_count,
    }
