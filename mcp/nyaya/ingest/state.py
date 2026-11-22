"""SQLite watermark state store (`ingest_state.sqlite`) for S3 ETag diffs, release tags,
superseded placeholder files, document SHA-256 deduplication, and HC web/mobile merge.
"""

from __future__ import annotations

import re
import sqlite3
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

STATE_SCHEMA_DDL = """
CREATE TABLE IF NOT EXISTS s3_etags (
    bucket       TEXT NOT NULL,
    s3_key       TEXT NOT NULL,
    year         INTEGER,
    etag         TEXT NOT NULL,
    size_bytes   INTEGER NOT NULL DEFAULT 0,
    last_mod     TEXT,
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (bucket, s3_key)
);

CREATE TABLE IF NOT EXISTS artifact_watermarks (
    source_id    TEXT NOT NULL,
    artifact_key TEXT NOT NULL,
    release_tag  TEXT NOT NULL DEFAULT '',
    sha256       TEXT NOT NULL DEFAULT '',
    etag         TEXT NOT NULL DEFAULT '',
    updated_at   TEXT NOT NULL,
    PRIMARY KEY (source_id, artifact_key)
);

CREATE TABLE IF NOT EXISTS indexed_docs (
    doc_id       TEXT PRIMARY KEY,
    text_sha256  TEXT NOT NULL,
    updated_at   TEXT NOT NULL
);
"""

_YEAR_PREFIX_RE = re.compile(r"\byear=((?:19|20)\d{2})\b")
_REFETCH_TAR_RE = re.compile(r"part-\d+-refetch\.tar$")


@dataclass(frozen=True)
class S3ObjectInfo:
    """Metadata of a single object from S3 ``ListObjectsV2``."""

    key: str
    etag: str
    size_bytes: int = 0
    last_modified: str = ""
    year: int | None = None


@dataclass
class S3DeltaPlan:
    """Computed delta between upstream S3 bucket listing and local watermark state."""

    bucket: str
    added: list[S3ObjectInfo] = field(default_factory=list)
    modified: list[S3ObjectInfo] = field(default_factory=list)
    unchanged: list[S3ObjectInfo] = field(default_factory=list)
    superseded_files: list[str] = field(default_factory=list)
    refetch_tars: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.modified or self.superseded_files or self.refetch_tars)


def _extract_year_from_key(key: str) -> int | None:
    m = _YEAR_PREFIX_RE.search(key)
    return int(m.group(1)) if m else None


def _clean_etag(raw_etag: str) -> str:
    return raw_etag.strip().strip('"').strip("'")


def parse_s3_list_objects_xml(xml_text: str) -> list[S3ObjectInfo]:
    """Parse AWS S3 ``ListObjectsV2`` XML response across all year prefixes."""
    root = ET.fromstring(xml_text.strip())
    objs: list[S3ObjectInfo] = []
    for elem in root.iter():
        if not elem.tag.endswith("Contents"):
            continue
        key = ""
        etag = ""
        size = 0
        last_mod = ""
        for child in elem:
            tag = child.tag.split("}", 1)[-1]
            text = (child.text or "").strip()
            if tag == "Key":
                key = text
            elif tag == "ETag":
                etag = _clean_etag(text)
            elif tag == "Size" and text.isdigit():
                size = int(text)
            elif tag == "LastModified":
                last_mod = text
        if key:
            objs.append(
                S3ObjectInfo(
                    key=key,
                    etag=etag,
                    size_bytes=size,
                    last_modified=last_mod,
                    year=_extract_year_from_key(key),
                )
            )
    return objs


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


class IngestState:
    """Persistent SQLite watermark tracker for idempotent incremental ingestion."""

    def __init__(self, db_path: Path | str = ":memory:") -> None:
        self.db_path = str(db_path)
        if self.db_path != ":memory:":
            Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(self.db_path)
        self._con.row_factory = sqlite3.Row
        self._con.executescript(STATE_SCHEMA_DDL)
        self._con.commit()

    def close(self) -> None:
        try:
            self._con.close()
        except Exception:
            pass

    def plan_s3_delta(
        self,
        bucket: str,
        listed_objects: Sequence[S3ObjectInfo] | str,
        *,
        index_json: Mapping[str, Any] | None = None,
    ) -> S3DeltaPlan:
        """Compare S3 objects across ALL years against recorded ETags and ``data.index.json``."""
        objects = (
            parse_s3_list_objects_xml(listed_objects)
            if isinstance(listed_objects, str)
            else list(listed_objects)
        )
        rows = self._con.execute(
            "SELECT s3_key, etag FROM s3_etags WHERE bucket = ?",
            (bucket,),
        ).fetchall()
        existing = {str(r["s3_key"]): str(r["etag"]) for r in rows}

        plan = S3DeltaPlan(bucket=bucket)
        for obj in objects:
            cleaned_etag = _clean_etag(obj.etag)
            prev_etag = existing.get(obj.key)
            if prev_etag is None:
                plan.added.append(obj)
            elif prev_etag != cleaned_etag:
                plan.modified.append(obj)
            else:
                plan.unchanged.append(obj)

            if _REFETCH_TAR_RE.search(obj.key) and prev_etag != cleaned_etag:
                plan.refetch_tars.append(obj.key)

        if index_json:
            superseded = index_json.get("superseded_files")
            if isinstance(superseded, list):
                for item in superseded:
                    if isinstance(item, str) and item.strip():
                        plan.superseded_files.append(item.strip())
            parts = index_json.get("parts")
            if isinstance(parts, list):
                for part in parts:
                    if isinstance(part, dict):
                        name = str(part.get("name") or "")
                        if _REFETCH_TAR_RE.search(name) and name not in plan.refetch_tars:
                            # Match against full key if present in objects
                            matched_keys = [o.key for o in objects if o.key.endswith(name)]
                            if matched_keys:
                                for mk in matched_keys:
                                    if mk not in plan.refetch_tars:
                                        plan.refetch_tars.append(mk)
                            else:
                                plan.refetch_tars.append(name)
        return plan

    def commit_s3_objects(self, bucket: str, objects: Iterable[S3ObjectInfo]) -> None:
        now = _utc_now_iso()
        rows = [
            (
                bucket,
                o.key,
                o.year if o.year is not None else _extract_year_from_key(o.key),
                _clean_etag(o.etag),
                int(o.size_bytes),
                o.last_modified,
                now,
            )
            for o in objects
        ]
        self._con.executemany(
            "INSERT OR REPLACE INTO s3_etags "
            "(bucket, s3_key, year, etag, size_bytes, last_mod, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self._con.commit()

    def has_artifact_changed(
        self,
        source_id: str,
        artifact_key: str,
        *,
        sha256: str = "",
        release_tag: str = "",
        etag: str = "",
    ) -> bool:
        row = self._con.execute(
            "SELECT release_tag, sha256, etag FROM artifact_watermarks "
            "WHERE source_id = ? AND artifact_key = ?",
            (source_id, artifact_key),
        ).fetchone()
        if row is None:
            return True
        if release_tag and str(row["release_tag"]) != release_tag:
            return True
        if sha256 and str(row["sha256"]).lower() != sha256.strip().lower():
            return True
        if etag and _clean_etag(str(row["etag"])) != _clean_etag(etag):
            return True
        return False

    def record_artifact_watermark(
        self,
        source_id: str,
        artifact_key: str,
        *,
        sha256: str = "",
        release_tag: str = "",
        etag: str = "",
    ) -> None:
        self._con.execute(
            "INSERT OR REPLACE INTO artifact_watermarks "
            "(source_id, artifact_key, release_tag, sha256, etag, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                source_id,
                artifact_key,
                release_tag,
                sha256.strip().lower(),
                _clean_etag(etag),
                _utc_now_iso(),
            ),
        )
        self._con.commit()

    def is_doc_unchanged(self, doc_id: str, text_sha256: str) -> bool:
        row = self._con.execute(
            "SELECT text_sha256 FROM indexed_docs WHERE doc_id = ?",
            (doc_id,),
        ).fetchone()
        if row is None:
            return False
        return str(row["text_sha256"]).lower() == text_sha256.strip().lower()

    def mark_doc_indexed(self, doc_id: str, text_sha256: str) -> None:
        self._con.execute(
            "INSERT OR REPLACE INTO indexed_docs (doc_id, text_sha256, updated_at) VALUES (?, ?, ?)",
            (doc_id, text_sha256.strip().lower(), _utc_now_iso()),
        )
        self._con.commit()


def dedupe_and_merge_hc_rows(
    web_rows: Sequence[Mapping[str, Any]],
    mobile_rows: Sequence[Mapping[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Deduplicate High Court web rows on ``(court_code, cnr, decision_date, order_number)``
    and LEFT JOIN ``case_details-mobile`` docket enrichment on ``cnr``.
    """
    mobile_by_cnr: dict[str, Mapping[str, Any]] = {}
    if mobile_rows:
        for mrow in mobile_rows:
            cnr_raw = str(mrow.get("cnr") or "").strip().upper()
            if cnr_raw:
                mobile_by_cnr[cnr_raw] = mrow

    seen_keys: set[tuple[str, str, str, str]] = set()
    merged_list: list[dict[str, Any]] = []

    for wrow in web_rows:
        court_code = str(wrow.get("court_code") or "").strip().replace("~", "_")
        cnr = str(wrow.get("cnr") or "").strip().upper()
        decision_date = str(wrow.get("decision_date") or "").strip()
        order_num = str(
            wrow.get("order_number")
            if wrow.get("order_number") is not None
            else (wrow.get("pdf_s3_key") or wrow.get("raw_html_sha256") or "1")
        )
        dedupe_key = (court_code, cnr, decision_date, order_num)
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)

        record = dict(wrow)
        record["court_code"] = court_code
        if cnr:
            record["cnr"] = cnr
        mob = mobile_by_cnr.get(cnr) if cnr else None
        if mob is not None:
            for field_name in (
                "filing_date",
                "registration_date",
                "petitioner_advocates",
                "respondent_advocates",
                "acts_sections",
                "hearing_count",
                "case_number",
            ):
                if not record.get(field_name) and mob.get(field_name) is not None:
                    record[field_name] = mob[field_name]
            if not record.get("disposal_nature") and mob.get("disposal_nature"):
                record["disposal_nature"] = mob["disposal_nature"]
        merged_list.append(record)

    return merged_list
