"""DuckDB ZSTD Parquet compactor and `manifest.json` generator."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pyarrow as pa

from ..fetchers.research_packs import sanitize_ddl_district_rows
from ..state import dedupe_and_merge_hc_rows
from .sqlite_core import IngestInputBundle, create_mini_ingest_input

_HC_SCHEMA = pa.schema(
    [
        pa.field("case_id", pa.string()),
        pa.field("cnr", pa.string()),
        pa.field("court_code", pa.string()),
        pa.field("court_name", pa.string()),
        pa.field("bench", pa.string()),
        pa.field("neutral_citation", pa.string()),
        pa.field("title", pa.string()),
        pa.field("petitioner", pa.string()),
        pa.field("respondent", pa.string()),
        pa.field("judge", pa.string()),
        pa.field("author_judge", pa.string()),
        pa.field("bench_strength", pa.int64()),
        pa.field("decision_date", pa.string()),
        pa.field("year", pa.int64()),
        pa.field("disposal_nature", pa.string()),
        pa.field("case_type", pa.string()),
        pa.field("act", pa.string()),
        pa.field("commercial_safe", pa.bool_()),
    ]
)

_SC_SCHEMA = pa.schema(
    [
        pa.field("case_id", pa.string()),
        pa.field("court_code", pa.string()),
        pa.field("court_name", pa.string()),
        pa.field("neutral_citation", pa.string()),
        pa.field("air_citation", pa.string()),
        pa.field("scc_citation", pa.string()),
        pa.field("title", pa.string()),
        pa.field("judge", pa.string()),
        pa.field("bench_strength", pa.int64()),
        pa.field("decision_date", pa.string()),
        pa.field("year", pa.int64()),
        pa.field("disposal_nature", pa.string()),
        pa.field("case_type", pa.string()),
        pa.field("act", pa.string()),
        pa.field("commercial_safe", pa.bool_()),
    ]
)

_TRIBUNAL_SCHEMA = pa.schema(
    [
        pa.field("case_id", pa.string()),
        pa.field("forum_code", pa.string()),
        pa.field("court_code", pa.string()),
        pa.field("court_name", pa.string()),
        pa.field("bench", pa.string()),
        pa.field("title", pa.string()),
        pa.field("petitioner", pa.string()),
        pa.field("respondent", pa.string()),
        pa.field("judge", pa.string()),
        pa.field("decision_date", pa.string()),
        pa.field("year", pa.int64()),
        pa.field("disposal_nature", pa.string()),
        pa.field("case_type", pa.string()),
        pa.field("act", pa.string()),
        pa.field("commercial_safe", pa.bool_()),
    ]
)

_REGULATOR_SCHEMA = pa.schema(
    [
        pa.field("case_id", pa.string()),
        pa.field("regulator_code", pa.string()),
        pa.field("court_code", pa.string()),
        pa.field("court_name", pa.string()),
        pa.field("title", pa.string()),
        pa.field("decision_date", pa.string()),
        pa.field("year", pa.int64()),
        pa.field("disposal_nature", pa.string()),
        pa.field("case_type", pa.string()),
        pa.field("act", pa.string()),
        pa.field("commercial_safe", pa.bool_()),
    ]
)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_zstd_parquet(
    rows: list[dict[str, Any]],
    out_path: Path,
    *,
    schema: pa.Schema | None = None,
) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows, schema=schema) if schema is not None else pa.Table.from_pylist(rows)
    con = duckdb.connect(":memory:")
    try:
        con.register("tbl", table)
        escaped = str(out_path).replace("'", "''")
        cols = set(table.column_names)
        order_cols = [c for c in ("year", "decision_date") if c in cols]
        order_sql = (
            " ORDER BY " + ", ".join(f"{c} DESC NULLS LAST" for c in order_cols)
            if order_cols
            else ""
        )
        con.execute(
            f"COPY (SELECT * FROM tbl{order_sql}) "
            f"TO '{escaped}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    finally:
        con.close()
    return table.num_rows


def _decade_label(year: int) -> str:
    if year <= 0:
        return "unknown"
    return f"{(year // 10) * 10}s"


def build_compacted_parquets(
    out_dir: Path | str,
    *,
    input_data: IngestInputBundle | None = None,
    commercial_safe_only: bool = False,
    public_base_url: str = "https://huggingface.co/datasets/nyaya-mcp/corpus/resolve/main",
) -> Path:
    """Compact raw records into the 5 canonical ZSTD Parquet layouts and emit `manifest.json`."""
    bundle = input_data or create_mini_ingest_input()
    root_dir = Path(out_dir)
    pq_root = root_dir / "parquet"
    pq_root.mkdir(parents=True, exist_ok=True)

    datasets: dict[str, dict[str, Any]] = {
        "high_court": {
            "license_spdx": "CC-BY-4.0",
            "commercial_safe": True,
            "terms_status": "cleared",
            "row_count": 0,
            "partitions": [],
        },
        "district_court": {
            "license_spdx": "CC-BY-NC-SA-4.0",
            "commercial_safe": False,
            "terms_status": "non_commercial_only",
            "row_count": 0,
            "partitions": [],
        },
        "tribunal": {
            "license_spdx": "CC-BY-4.0",
            "commercial_safe": True,
            "terms_status": "cleared",
            "row_count": 0,
            "partitions": [],
        },
        "supreme_court": {
            "license_spdx": "CC-BY-4.0",
            "commercial_safe": True,
            "terms_status": "cleared",
            "row_count": 0,
            "partitions": [],
        },
        "regulator": {
            "license_spdx": "CC-BY-4.0",
            "commercial_safe": True,
            "terms_status": "cleared",
            "row_count": 0,
            "partitions": [],
        },
    }

    def _add_partition(
        dataset_name: str,
        rel_key: str,
        rows: list[dict[str, Any]],
        meta: dict[str, Any],
        *,
        schema: pa.Schema | None = None,
    ) -> None:
        if not rows:
            return
        fpath = root_dir / rel_key
        row_cnt = _write_zstd_parquet(rows, fpath, schema=schema)
        years = [int(r["year"]) for r in rows if r.get("year") is not None and int(r["year"]) > 0]
        part = {
            **meta,
            "year_min": meta.get("year_min", (min(years) // 10) * 10 if years else 1950),
            "year_max": meta.get("year_max", ((max(years) // 10) * 10 + 9) if years else 2029),
            "rows": row_cnt,
            "s3_key": rel_key,
            "public_url": f"{public_base_url.rstrip('/')}/{rel_key}",
            "bytes": fpath.stat().st_size,
            "sha256": _sha256_file(fpath),
        }
        datasets[dataset_name]["partitions"].append(part)
        datasets[dataset_name]["row_count"] += row_cnt

    # 1. High Court: decade-split per court after LEFT JOIN with case_details-mobile
    merged_hc = dedupe_and_merge_hc_rows(bundle.hc_web_rows, bundle.hc_mobile_rows)
    hc_groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for r in merged_hc:
        c_code = str(r.get("court_code") or "7_26")
        yr = int(r.get("year") or 2024)
        decade = _decade_label(yr)
        hc_groups[(c_code, decade)].append(
            {
                "case_id": str(r["case_id"]),
                "cnr": str(r.get("cnr") or ""),
                "court_code": c_code,
                "court_name": str(r.get("court_name") or ""),
                "bench": str(r.get("bench") or "Principal"),
                "neutral_citation": str(r.get("neutral_citation") or ""),
                "title": str(r.get("title") or ""),
                "petitioner": str(r.get("petitioner") or ""),
                "respondent": str(r.get("respondent") or ""),
                "judge": str(r.get("judge") or r.get("author_judge") or ""),
                "author_judge": str(r.get("author_judge") or r.get("judge") or ""),
                "bench_strength": int(r.get("bench_strength") or 1),
                "decision_date": str(r.get("decision_date") or ""),
                "year": yr,
                "disposal_nature": str(r.get("disposal_nature") or ""),
                "case_type": str(r.get("case_type") or ""),
                "act": str(r.get("act") or ""),
                "commercial_safe": True,
            }
        )
    for (c_code, decade), grp_rows in sorted(hc_groups.items()):
        rel_key = f"parquet/high_court/court={c_code}/decade={decade}.parquet"
        _add_partition(
            "high_court",
            rel_key,
            grp_rows,
            {"court_code": c_code, "court_name": grp_rows[0]["court_name"]},
            schema=_HC_SCHEMA,
        )

    # 2. District Court (DDL 81M): sanitize ML columns & tag commercial_safe=False
    if not commercial_safe_only and bundle.ddl_district_rows:
        sanitized_ddl = sanitize_ddl_district_rows(bundle.ddl_district_rows)
        ddl_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for r in sanitized_ddl:
            st = str(r.get("state_code") or "MH")
            ddl_groups[st].append(r)
        for st, grp_rows in sorted(ddl_groups.items()):
            rel_key = f"parquet/district_court/state={st}.parquet"
            _add_partition(
                "district_court",
                rel_key,
                grp_rows,
                {"state_code": st, "year_min": 2010, "year_max": 2018},
            )

    # 3. Tribunal
    trib_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in bundle.tribunal_rows:
        forum = str(r.get("forum_code") or r.get("court_code") or "NCLAT")
        trib_groups[forum].append(
            {
                "case_id": str(r.get("case_id") or ""),
                "forum_code": forum,
                "court_code": str(r.get("court_code") or forum),
                "court_name": str(r.get("court_name") or forum),
                "bench": str(r.get("bench") or ""),
                "title": str(r.get("title") or ""),
                "petitioner": str(r.get("petitioner") or ""),
                "respondent": str(r.get("respondent") or ""),
                "judge": str(r.get("judge") or ""),
                "decision_date": str(r.get("decision_date") or ""),
                "year": int(r.get("year") or 0),
                "disposal_nature": str(r.get("disposal_nature") or ""),
                "case_type": str(r.get("case_type") or ""),
                "act": str(r.get("act") or ""),
                "commercial_safe": bool(r.get("commercial_safe", True)),
            }
        )
    for forum, grp_rows in sorted(trib_groups.items()):
        rel_key = f"parquet/tribunal/forum={forum}.parquet"
        _add_partition("tribunal", rel_key, grp_rows, {"forum_code": forum}, schema=_TRIBUNAL_SCHEMA)

    # 4. Supreme Court metadata mirror
    sc_meta_rows = [
        {
            "case_id": str(r["case_id"]),
            "court_code": "SCI",
            "court_name": "Supreme Court of India",
            "neutral_citation": str(r.get("neutral_citation") or ""),
            "air_citation": str(r.get("air_citation") or ""),
            "scc_citation": str(r.get("scc_citation") or ""),
            "title": str(r.get("title") or ""),
            "judge": str(r.get("author_judge") or r.get("judge_coram") or ""),
            "bench_strength": int(r.get("bench_strength") or 1),
            "decision_date": str(r.get("decision_date") or ""),
            "year": int(r.get("year") or 2020),
            "disposal_nature": str(r.get("disposal_nature") or ""),
            "case_type": str(r.get("case_type") or ""),
            "act": str((r.get("acts_cited") or ["Constitution"])[0]),
            "commercial_safe": True,
        }
        for r in bundle.sc_judgments
    ]
    _add_partition(
        "supreme_court",
        "parquet/supreme_court/sc_metadata.parquet",
        sc_meta_rows,
        {"court_code": "SCI", "year_min": 1950, "year_max": 2026},
        schema=_SC_SCHEMA,
    )

    # 5. Regulator
    reg_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in bundle.regulator_order_rows:
        reg_code = str(r.get("regulator_code") or r.get("court_code") or "SEBI")
        reg_groups[reg_code].append(
            {
                "case_id": str(r.get("case_id") or ""),
                "regulator_code": reg_code,
                "court_code": str(r.get("court_code") or reg_code),
                "court_name": str(r.get("court_name") or reg_code),
                "title": str(r.get("title") or ""),
                "decision_date": str(r.get("decision_date") or ""),
                "year": int(r.get("year") or 0),
                "disposal_nature": str(r.get("disposal_nature") or ""),
                "case_type": str(r.get("case_type") or ""),
                "act": str(r.get("act") or ""),
                "commercial_safe": bool(r.get("commercial_safe", True)),
            }
        )
    for reg_code, grp_rows in sorted(reg_groups.items()):
        rel_key = f"parquet/regulator/body={reg_code}.parquet"
        _add_partition("regulator", rel_key, grp_rows, {"regulator_code": reg_code}, schema=_REGULATOR_SCHEMA)

    manifest_dict = {
        "schema_version": "2.0",
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "datasets": datasets,
    }
    manifest_json = json.dumps(manifest_dict, indent=2)
    root_manifest = root_dir / "manifest.json"
    root_manifest.write_text(manifest_json, encoding="utf-8")
    (pq_root / "manifest.json").write_text(manifest_json, encoding="utf-8")
    return root_manifest
