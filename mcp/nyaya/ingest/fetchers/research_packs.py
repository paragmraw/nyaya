"""Fetchers and sanitizers for academic/research packs:
- DDL 81M District Courts (`CC-BY-NC-SA-4.0`, `commercial_safe=False`, strips ML religion/caste columns)
- ILDC Supreme Court Research Pack (`CC-BY-NC-4.0`, `commercial_safe=False`)
- Justice Hub KHOJ High Court Judges (`CC-BY-4.0`, `commercial_safe=True`)
"""

from __future__ import annotations

import csv
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import duckdb

from ..lockfile import (
    DEFAULT_LOCKFILE_PATH,
    Lockfile,
    SourceEntry,
    load_and_validate_lockfile,
)
from ..state import IngestState
from .http import SafeHttpFetcher

DDL_BANNED_DEMOGRAPHIC_COLUMNS = frozenset(
    {
        "predicted_religion",
        "predicted_caste",
        "judge_predicted_religion",
        "judge_predicted_caste",
        "defendant_religion",
        "petitioner_religion",
        "defendant_caste",
        "petitioner_caste",
    }
)

RESEARCH_SOURCE_IDS: tuple[str, ...] = (
    "justicehub_khoj",
    "ddl_81m",
    "ildc",
)

_HONORIFIC_RE = re.compile(
    r"^(?:(?:hon'?ble|mr\.?|mrs\.?|ms\.?|shri|smt\.?|dr\.?|justice)\s+)+",
    re.IGNORECASE,
)


@dataclass
class ResearchFetchResult:
    """Result summary of syncing or fetching a research pack or directory."""

    source_id: str
    uri: str
    dest_path: Path | None = None
    sha256: str = ""
    byte_count: int = 0
    skipped_unchanged: bool = False
    dry_run: bool = False


def sanitize_ddl_district_rows(
    rows: Sequence[Mapping[str, Any]],
    *,
    extra_drop_columns: Sequence[str] | None = None,
) -> list[dict[str, Any]]:
    """Strip ML-inferred religion/caste columns from DDL District Court rows and force
    ``commercial_safe=False``, ``license_spdx="CC-BY-NC-SA-4.0"``, ``terms_status="non_commercial_only"``.
    """
    drop_set = set(DDL_BANNED_DEMOGRAPHIC_COLUMNS)
    if extra_drop_columns:
        drop_set.update(c.strip() for c in extra_drop_columns if c.strip())

    cleaned: list[dict[str, Any]] = []
    for row in rows:
        item = {k: v for k, v in row.items() if k not in drop_set}
        item["commercial_safe"] = False
        item["license_spdx"] = "CC-BY-NC-SA-4.0"
        item["terms_status"] = "non_commercial_only"
        item["source_id"] = "ddl_81m"
        cleaned.append(item)
    return cleaned


def tag_ildc_research_rows(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Ensure ILDC research rows carry mandatory non-commercial provenance metadata."""
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        item["commercial_safe"] = False
        item["license_spdx"] = "CC-BY-NC-4.0"
        item["terms_status"] = "non_commercial_only"
        item["source_id"] = str(item.get("source_id") or "ildc")
        out.append(item)
    return out


def normalize_judge_name(raw_name: str) -> str:
    """Strip judicial honorifics and collapse whitespace for canonical HC judge matching."""
    cleaned = re.sub(r"\s+", " ", (raw_name or "").strip())
    cleaned = _HONORIFIC_RE.sub("", cleaned).strip(" ,.-")
    return cleaned


def _resolve_lockfile(lockfile: Lockfile | Path | str | None) -> Lockfile:
    if lockfile is None:
        return load_and_validate_lockfile(DEFAULT_LOCKFILE_PATH)
    if isinstance(lockfile, Lockfile):
        return lockfile
    return load_and_validate_lockfile(lockfile)


def _read_research_files(staged_dir: Path) -> list[dict[str, Any]]:
    if not staged_dir.is_dir():
        return []
    rows: list[dict[str, Any]] = []
    for path in sorted(staged_dir.rglob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix == ".parquet":
            con = duckdb.connect(":memory:")
            try:
                escaped = str(path).replace("'", "''")
                cur = con.execute(f"SELECT * FROM read_parquet('{escaped}')")
                cols = [desc[0] for desc in (cur.description or ())]
                rows.extend(dict(zip(cols, r, strict=False)) for r in cur.fetchall())
            finally:
                con.close()
        elif suffix == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                rows.extend(dict(x) for x in payload if isinstance(x, dict))
            elif isinstance(payload, dict):
                extracted = False
                for key in ("records", "rows", "data", "judges", "cases", "items"):
                    val = payload.get(key)
                    if isinstance(val, list):
                        rows.extend(dict(x) for x in val if isinstance(x, dict))
                        extracted = True
                        break
                if not extracted:
                    rows.append(dict(payload))
        elif suffix == ".jsonl":
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped:
                    item = json.loads(stripped)
                    if isinstance(item, dict):
                        rows.append(item)
        elif suffix == ".csv":
            with path.open("r", encoding="utf-8", newline="") as f:
                reader = csv.DictReader(f)
                rows.extend(dict(r) for r in reader)
    return rows


class ResearchPacksFetcher:
    """Fetches and loads DDL 81M, ILDC, and Justice Hub KHOJ packs with strict license hygiene."""

    def __init__(
        self,
        http_fetcher: SafeHttpFetcher | None = None,
        lockfile: Lockfile | Path | str | None = None,
        *,
        state: IngestState | None = None,
    ) -> None:
        self.lockfile = _resolve_lockfile(lockfile)
        self.http_fetcher = http_fetcher or SafeHttpFetcher(
            allowed_domains=self.lockfile.allowed_domains
        )
        self.state = state

    def get_source_entry(self, source_id: str) -> SourceEntry:
        if source_id not in RESEARCH_SOURCE_IDS:
            raise ValueError(
                f"Unknown research source {source_id!r}; expected one of {RESEARCH_SOURCE_IDS}"
            )
        return self.lockfile.get_source(source_id)

    def fetch_source(
        self,
        source_id: str,
        *,
        work_dir: Path | str | None = None,
        dest_dir: Path | str | None = None,
        url: str | None = None,
        expected_sha256: str | None = None,
        verify_sha256: bool = True,
        dry_run: bool = False,
        fixture: bool = False,
    ) -> ResearchFetchResult:
        entry = self.get_source_entry(source_id)
        target_url = url or entry.uri
        sha_to_check = expected_sha256 if expected_sha256 is not None else (entry.sha256 if verify_sha256 else None)
        fname = Path(urlsplit(target_url).path).name
        if not fname or "." not in fname:
            fname = f"{source_id}.json"

        resolved_dir: Path | None = None
        if dest_dir is not None:
            resolved_dir = Path(dest_dir)
        elif work_dir is not None:
            resolved_dir = Path(work_dir) / "raw" / source_id

        dest_path = (resolved_dir / fname) if resolved_dir is not None else None

        if dry_run:
            return ResearchFetchResult(
                source_id=source_id,
                uri=target_url,
                dest_path=dest_path,
                sha256=sha_to_check or "",
                dry_run=True,
            )

        if fixture:
            payload = self._fixture_payload_bytes(source_id)
            digest = hashlib.sha256(payload).hexdigest()
            if dest_path is not None:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                dest_path.write_bytes(payload)
            if self.state is not None:
                self.state.record_artifact_watermark(
                    source_id,
                    fname,
                    sha256=digest,
                    release_tag=entry.release_tag or "",
                )
            return ResearchFetchResult(
                source_id=source_id,
                uri=target_url,
                dest_path=dest_path,
                sha256=digest,
                byte_count=len(payload),
            )

        if self.state is not None and sha_to_check and dest_path is not None and dest_path.is_file():
            if not self.state.has_artifact_changed(
                source_id,
                fname,
                sha256=sha_to_check,
                release_tag=entry.release_tag or "",
            ):
                return ResearchFetchResult(
                    source_id=source_id,
                    uri=target_url,
                    dest_path=dest_path,
                    sha256=sha_to_check,
                    byte_count=dest_path.stat().st_size,
                    skipped_unchanged=True,
                )

        if dest_path is not None:
            digest = self.http_fetcher.download_to_file(
                target_url,
                dest_path,
                expected_sha256=sha_to_check,
            )
            byte_count = dest_path.stat().st_size
        else:
            raw_bytes = self.http_fetcher.fetch_bytes(
                target_url,
                expected_sha256=sha_to_check,
            )
            digest = hashlib.sha256(raw_bytes).hexdigest()
            byte_count = len(raw_bytes)

        if self.state is not None:
            self.state.record_artifact_watermark(
                source_id,
                fname,
                sha256=digest,
                release_tag=entry.release_tag or "",
            )

        return ResearchFetchResult(
            source_id=source_id,
            uri=target_url,
            dest_path=dest_path,
            sha256=digest,
            byte_count=byte_count,
        )

    def sync_source(
        self,
        source_id: str,
        *,
        work_dir: Path | str,
        dry_run: bool = False,
        fixture: bool = False,
        verify_sha256: bool = True,
    ) -> ResearchFetchResult:
        return self.fetch_source(
            source_id,
            work_dir=work_dir,
            dry_run=dry_run,
            fixture=fixture,
            verify_sha256=verify_sha256,
        )

    def _fixture_payload_bytes(self, source_id: str) -> bytes:
        if source_id == "justicehub_khoj":
            return json.dumps(
                [
                    {
                        "judge_name": "Hon'ble Justice Vibhu Bakhru",
                        "court_code": "7_26",
                        "court_name": "High Court of Delhi",
                        "designation": "Judge",
                        "appointment_date": "2013-04-17",
                        "retirement_date": "2028-11-01",
                    }
                ]
            ).encode("utf-8")
        if source_id == "ddl_81m":
            return json.dumps(
                [
                    {
                        "case_id": "ddl:mh:2015:1",
                        "cnr": "MHAU020000012015",
                        "state_code": "MH",
                        "court_code": "MH_DIST_01",
                        "court_name": "District and Sessions Court, Pune",
                        "district": "Pune",
                        "title": "State of Maharashtra v. Accused A",
                        "decision_date": "2015-08-12",
                        "year": 2015,
                        "disposal_nature": "Acquitted",
                        "case_type": "Sessions Case",
                        "act": "IPC",
                        "predicted_religion": "STRIPPED",
                        "predicted_caste": "STRIPPED",
                    }
                ]
            ).encode("utf-8")
        return json.dumps(
            [
                {
                    "case_id": "ildc:2020:001",
                    "title": "State of Maharashtra v. Sample ILDC Appellant",
                    "year": 2020,
                    "decision_date": "2020-02-01",
                    "text": "1. ILDC non-commercial academic research annotation.",
                }
            ]
        ).encode("utf-8")

    def load_ddl_district_rows(
        self,
        staged_dir: Path | str,
        *,
        extra_drop_columns: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Load staged DDL 81M District Court rows, stripping ML demographic columns."""
        raw = _read_research_files(Path(staged_dir))
        if not raw:
            return []
        entry = self.get_source_entry("ddl_81m")
        combined_drops = list(entry.drop_columns)
        if extra_drop_columns:
            combined_drops.extend(extra_drop_columns)
        return sanitize_ddl_district_rows(raw, extra_drop_columns=combined_drops)

    def load_ildc_rows(self, staged_dir: Path | str) -> list[dict[str, Any]]:
        """Load staged ILDC Supreme Court research rows with non-commercial provenance."""
        raw = _read_research_files(Path(staged_dir))
        if not raw:
            return []
        entry = self.get_source_entry("ildc")
        normalized: list[dict[str, Any]] = []
        for idx, row in enumerate(raw, start=1):
            item = dict(row)
            case_id = str(item.get("case_id") or f"ildc:{item.get('year') or 2020}:{idx}").strip()
            text = str(item.get("text") or item.get("explanation") or item.get("judgment_text") or "").strip()
            title = str(item.get("title") or item.get("case_name") or case_id).strip()
            if not title:
                continue
            item["case_id"] = case_id
            item["court_code"] = str(item.get("court_code") or "SCI")
            item["court_name"] = str(item.get("court_name") or "Supreme Court of India")
            item["title"] = title
            item["text"] = text
            item["source_id"] = "ildc"
            item["source_url"] = str(item.get("source_url") or entry.uri)
            if item.get("year") is not None and str(item["year"]).strip() != "":
                item["year"] = int(item["year"])
            normalized.append(item)
        return tag_ildc_research_rows(normalized)

    def load_justicehub_khoj(self, staged_dir: Path | str) -> list[dict[str, Any]]:
        """Load and normalize Justice Hub KHOJ High Court Judges directory (`CC-BY-4.0`, `commercial_safe=True`)."""
        raw = _read_research_files(Path(staged_dir))
        if not raw:
            return []
        entry = self.get_source_entry("justicehub_khoj")
        out: list[dict[str, Any]] = []
        for row in raw:
            raw_name = str(
                row.get("judge_name") or row.get("name") or row.get("judge") or ""
            ).strip()
            canon_name = normalize_judge_name(raw_name)
            if not canon_name:
                continue
            court_code = str(row.get("court_code") or row.get("hc_code") or "").strip().replace("~", "_")
            court_name = str(row.get("court_name") or row.get("high_court") or row.get("court") or "").strip()
            out.append(
                {
                    "judge_name": canon_name,
                    "judge_name_norm": canon_name.lower(),
                    "raw_name": raw_name,
                    "court_code": court_code or None,
                    "court_name": court_name or None,
                    "designation": str(row.get("designation") or "Judge").strip(),
                    "bench": str(row.get("bench") or "").strip() or None,
                    "appointment_date": (
                        str(row.get("appointment_date") or row.get("date_of_appointment") or "").strip() or None
                    ),
                    "retirement_date": (
                        str(row.get("retirement_date") or row.get("date_of_retirement") or "").strip() or None
                    ),
                    "gender": str(row.get("gender") or "").strip() or None,
                    "parent_high_court": str(row.get("parent_high_court") or "").strip() or None,
                    "source_of_recruitment": str(row.get("source_of_recruitment") or "").strip() or None,
                    "elevated_to_sc": bool(row.get("elevated_to_sc", False)),
                    "commercial_safe": True,
                    "license_spdx": "CC-BY-4.0",
                    "terms_status": "attribution_required",
                    "source_id": "justicehub_khoj",
                    "source_url": str(row.get("source_url") or entry.uri),
                }
            )
        return out
