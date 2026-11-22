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


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _write_zstd_parquet(rows: list[dict[str, Any]], out_path: Path) -> int:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pylist(rows)
    con = duckdb.connect(":memory:")
    try:
        con.register("tbl", table)
        escaped = str(out_path).replace("'", "''")
        con.execute(
            f"COPY (SELECT * FROM tbl ORDER BY year DESC, decision_date DESC) "
            f"TO '{escaped}' (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
    finally:
        con.close()
    return table.num_rows


def _decade_label(year: int) -> str:
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
    ) -> None:
        if not rows:
            return
        fpath = root_dir / rel_key
        row_cnt = _write_zstd_parquet(rows, fpath)
        years = [int(r["year"]) for r in rows if r.get("year") is not None]
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
        )

    # 2. District Court (DDL 81M): sanitize ML columns & tag commercial_safe=False
    if not commercial_safe_only:
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
        trib_groups[forum].append(dict(r))
    for forum, grp_rows in sorted(trib_groups.items()):
        rel_key = f"parquet/tribunal/forum={forum}.parquet"
        _add_partition("tribunal", rel_key, grp_rows, {"forum_code": forum})

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
    )

    # 5. Regulator
    reg_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in bundle.regulator_order_rows:
        reg_code = str(r.get("regulator_code") or r.get("court_code") or "SEBI")
        reg_groups[reg_code].append(dict(r))
    for reg_code, grp_rows in sorted(reg_groups.items()):
        rel_key = f"parquet/regulator/body={reg_code}.parquet"
        _add_partition("regulator", rel_key, grp_rows, {"regulator_code": reg_code})

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
