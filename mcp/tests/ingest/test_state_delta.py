"""Tests for incremental delta watermark tracking, S3 ListObjectsV2 full-year ETag diffing,
superseded_files / part-*-refetch.tar detection, and HC web vs mobile deduplication.
"""

from __future__ import annotations

from pathlib import Path

from nyaya.ingest.state import (
    IngestState,
    dedupe_and_merge_hc_rows,
    parse_s3_list_objects_xml,
)

SAMPLE_LIST_OBJECTS_XML_RUN1 = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Name>indian-high-court-judgments</Name>
  <IsTruncated>false</IsTruncated>
  <Contents>
    <Key>metadata/parquet/year=2018/court=7_26/bench=dhc/metadata.parquet</Key>
    <LastModified>2025-01-10T00:00:00.000Z</LastModified>
    <ETag>"etag-2018-v1"</ETag>
    <Size>1048576</Size>
  </Contents>
  <Contents>
    <Key>metadata/parquet/year=2024/court=7_26/bench=dhc/metadata.parquet</Key>
    <LastModified>2025-01-10T00:00:00.000Z</LastModified>
    <ETag>"etag-2024-v1"</ETag>
    <Size>2097152</Size>
  </Contents>
  <Contents>
    <Key>data/tar/year=2018/court=7_26/bench=dhc/part-0.tar</Key>
    <LastModified>2025-01-10T00:00:00.000Z</LastModified>
    <ETag>"etag-tar-2018-p0"</ETag>
    <Size>5242880</Size>
  </Contents>
</ListBucketResult>
"""

SAMPLE_LIST_OBJECTS_XML_RUN2 = """<?xml version="1.0" encoding="UTF-8"?>
<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">
  <Name>indian-high-court-judgments</Name>
  <IsTruncated>false</IsTruncated>
  <Contents>
    <Key>metadata/parquet/year=2018/court=7_26/bench=dhc/metadata.parquet</Key>
    <LastModified>2026-11-20T00:00:00.000Z</LastModified>
    <ETag>"etag-2018-v2-mutated"</ETag>
    <Size>1099000</Size>
  </Contents>
  <Contents>
    <Key>metadata/parquet/year=2024/court=7_26/bench=dhc/metadata.parquet</Key>
    <LastModified>2025-01-10T00:00:00.000Z</LastModified>
    <ETag>"etag-2024-v1"</ETag>
    <Size>2097152</Size>
  </Contents>
  <Contents>
    <Key>data/tar/year=2018/court=7_26/bench=dhc/part-0.tar</Key>
    <LastModified>2025-01-10T00:00:00.000Z</LastModified>
    <ETag>"etag-tar-2018-p0"</ETag>
    <Size>5242880</Size>
  </Contents>
  <Contents>
    <Key>data/tar/year=2018/court=7_26/bench=dhc/part-1-refetch.tar</Key>
    <LastModified>2026-11-20T00:00:00.000Z</LastModified>
    <ETag>"etag-tar-2018-refetch"</ETag>
    <Size>412000</Size>
  </Contents>
</ListBucketResult>
"""


def test_s3_full_year_etag_diff_and_superseded_files_detection(tmp_path: Path) -> None:
    state_db = tmp_path / "ingest_state.sqlite"
    state = IngestState(state_db)
    bucket = "indian-high-court-judgments"

    objs_run1 = parse_s3_list_objects_xml(SAMPLE_LIST_OBJECTS_XML_RUN1)
    assert len(objs_run1) == 3

    delta1 = state.plan_s3_delta(bucket, objs_run1)
    assert len(delta1.added) == 3
    assert len(delta1.modified) == 0
    assert len(delta1.unchanged) == 0
    state.commit_s3_objects(bucket, objs_run1)

    # Run 2: historical year=2018 ETag mutated, year=2024 unchanged, plus part-1-refetch.tar
    # and data.index.json listing a superseded placeholder PDF
    objs_run2 = parse_s3_list_objects_xml(SAMPLE_LIST_OBJECTS_XML_RUN2)
    index_json = {
        "updated_at": "2026-11-20T00:00:00Z",
        "superseded_files": [
            "data/pdf/year=2018/court=7_26/bench=dhc/DLHC010011222018_1.pdf",
            "data/tar/year=2018/court=7_26/bench=dhc/part-0.tar",
        ],
        "parts": [
            {"name": "part-0.tar", "file_count": 100},
            {"name": "part-1-refetch.tar", "file_count": 4},
        ],
    }

    delta2 = state.plan_s3_delta(bucket, objs_run2, index_json=index_json)
    # Historical year=2018 metadata parquet detected as modified
    assert [o.key for o in delta2.modified] == [
        "metadata/parquet/year=2018/court=7_26/bench=dhc/metadata.parquet"
    ]
    # New refetch tar detected in added and refetch_tars
    assert [o.key for o in delta2.added] == [
        "data/tar/year=2018/court=7_26/bench=dhc/part-1-refetch.tar"
    ]
    assert "data/tar/year=2018/court=7_26/bench=dhc/part-1-refetch.tar" in delta2.refetch_tars
    # Unchanged 2024 metadata skipped
    assert "metadata/parquet/year=2024/court=7_26/bench=dhc/metadata.parquet" in [
        o.key for o in delta2.unchanged
    ]
    # Superseded files surfaced for re-extraction
    assert (
        "data/pdf/year=2018/court=7_26/bench=dhc/DLHC010011222018_1.pdf"
        in delta2.superseded_files
    )
    state.close()


def test_release_tag_and_text_sha256_watermarks(tmp_path: Path) -> None:
    state = IngestState(tmp_path / "ingest_state.sqlite")
    assert state.has_artifact_changed(
        "vaquill_open_india_law",
        "in_central_legislation.parquet",
        sha256="abc123",
        release_tag="v2026.08.1",
    )
    state.record_artifact_watermark(
        "vaquill_open_india_law",
        "in_central_legislation.parquet",
        sha256="abc123",
        release_tag="v2026.08.1",
    )
    assert not state.has_artifact_changed(
        "vaquill_open_india_law",
        "in_central_legislation.parquet",
        sha256="abc123",
        release_tag="v2026.08.1",
    )
    # Different SHA or tag triggers change
    assert state.has_artifact_changed(
        "vaquill_open_india_law",
        "in_central_legislation.parquet",
        sha256="def456",
        release_tag="v2026.08.1",
    )

    # Document text_sha256 dedup for LanceDB incremental builds
    assert not state.is_doc_unchanged("sc:1973:kesavananda", "sha-v1")
    state.mark_doc_indexed("sc:1973:kesavananda", "sha-v1")
    assert state.is_doc_unchanged("sc:1973:kesavananda", "sha-v1")
    assert not state.is_doc_unchanged("sc:1973:kesavananda", "sha-v2")
    state.close()


def test_dedupe_and_merge_hc_web_and_mobile_rows() -> None:
    web_rows = [
        {
            "court_code": "7_26",
            "cnr": "DLHC010123452024",
            "decision_date": "2024-05-14",
            "order_number": 1,
            "title": "Arjun Mehta v. State NCT of Delhi",
            "judge": "Vibhu Bakhru",
            "disposal_nature": "",
        },
        # Duplicate web row with identical (court_code, cnr, decision_date, order_number)
        {
            "court_code": "7_26",
            "cnr": "DLHC010123452024",
            "decision_date": "2024-05-14",
            "order_number": 1,
            "title": "Arjun Mehta v. State NCT of Delhi",
            "judge": "Vibhu Bakhru",
            "disposal_nature": "",
        },
        # Distinct order_number on same CNR
        {
            "court_code": "7_26",
            "cnr": "DLHC010123452024",
            "decision_date": "2024-05-01",
            "order_number": 0,
            "title": "Arjun Mehta v. State NCT of Delhi (Interim)",
            "judge": "Vibhu Bakhru",
            "disposal_nature": "Interim Order",
        },
    ]
    mobile_rows = [
        {
            "cnr": "DLHC010123452024",
            "filing_date": "2024-01-10",
            "registration_date": "2024-01-12",
            "disposal_nature": "Allowed",
            "petitioner_advocates": ["Adv. R.K. Sharma"],
            "respondent_advocates": ["APP for State"],
            "acts_sections": ["CrPC s.438", "BNSS s.482"],
            "hearing_count": 4,
        }
    ]

    merged = dedupe_and_merge_hc_rows(web_rows, mobile_rows)
    assert len(merged) == 2
    by_order = {r["order_number"]: r for r in merged}
    final_order = by_order[1]
    assert final_order["disposal_nature"] == "Allowed"
    assert final_order["filing_date"] == "2024-01-10"
    assert final_order["hearing_count"] == 4
    assert "CrPC s.438" in final_order["acts_sections"]
