"""Loaders and fetchers for official Indian statutes, Constitution Amendments 1–106,
Criminal Sanhita Concordance, Judicial Invalidations, Courts Directory, and the 5
official Pillar 1 sources (`indiacode_central_acts`, `prs_2026_delta`,
`legislative_constitution`, `cad_debates`, `escr_judgments`).
"""

from __future__ import annotations

import hashlib
import json
import re
from bisect import bisect_right
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import duckdb
import yaml  # type: ignore[import-untyped]

from ...sanitize import escape_corpus_delimiters, strip_control_chars
from ..extract.pdf import extract_pdf_file
from ..lockfile import (
    DATA_DIR,
    DEFAULT_AMENDMENTS_PATH,
    DEFAULT_CONCORDANCE_PATH,
    DEFAULT_COURTS_PATH,
    DEFAULT_INVALIDATIONS_PATH,
    DEFAULT_LOCKFILE_PATH,
    Lockfile,
    SourceEntry,
    load_and_validate_lockfile,
)
from ..state import IngestState
from .http import SafeHttpFetcher

DEFAULT_CURATED_PROVISIONS_PATH = DATA_DIR / "curated_statute_provisions.yaml"

OFFICIAL_SOURCE_IDS: tuple[str, ...] = (
    "indiacode_central_acts",
    "prs_2026_delta",
    "legislative_constitution",
    "cad_debates",
    "escr_judgments",
)

_CONSTITUTION_ARTICLE_RE = re.compile(
    r"(?m)^\s*(?:Article\s+)?(\d{1,3}(?:-[A-Z]|[A-Z]{0,2}))\.\s+([^\n.—]+?)(?:\.—|\.\s*—|\.\s+|\n)",
)
_CONSTITUTION_BODY_ARTICLE_RE = re.compile(
    r"(?:(?m:^)[ \t]*|(?<=[\.\]])[ \t]+)\*?\[?(?:Article\s+)?(\d{1,3}(?:-[A-Z]|[A-Z]{0,2}))\.\s*"
    r"(?:\d+\s*\n\s*\[)?([A-Z\[].{2,450}?)(?:(?:\.\s*\]?|\])\s*(?:—|–{1,2}|-)|(?:\.\s*—))",
    re.DOTALL,
)
_CONSTITUTION_PART_RE = re.compile(
    r"(?m)^[ \t]*\[?\s*(?:PART\s+([IVX]+[A-Z]?)\b(?!\s+of\b)[^\n]*|Part\s+([IVX]+[A-Z]?)[ \t]*)$"
)
_CONSTITUTION_TOC_RE = re.compile(r"(?im)^[ \t]*(?:CONTENTS|TABLE\s+OF\s+CONTENTS)\b")
_CONSTITUTION_PREAMBLE_BODY_RE = re.compile(
    r"(?im)^[ \t]*PREAMBLE[ \t]*\n+[ \t]*WE,\s+THE\s+PEOPLE\s+OF\s+INDIA"
)
_CONSTITUTION_PART_I_RE = re.compile(r"(?m)^[ \t]*\[?\s*PART\s+I\b(?![IVX])[^\n]*$")
_CONSTITUTION_FIRST_SCHEDULE_RE = re.compile(
    r"(?m)^[ \t]*\[?\s*(?:THE\s+)?FIRST\s+SCHEDULE\b"
)


@dataclass
class OfficialFetchResult:
    """Result summary of syncing or fetching a single official statutory/judicial source."""

    source_id: str
    uri: str
    dest_path: Path | None = None
    sha256: str = ""
    byte_count: int = 0
    skipped_unchanged: bool = False
    dry_run: bool = False


def load_amendments_yaml(path: Path | str = DEFAULT_AMENDMENTS_PATH) -> list[dict[str, Any]]:
    """Load and return all 106 verified Constitutional Amendments from YAML."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("amendments"), list):
        raise ValueError(f"Invalid amendments YAML structure at {path}")
    return [dict(item) for item in raw["amendments"] if isinstance(item, dict)]


def load_concordance_yaml(path: Path | str = DEFAULT_CONCORDANCE_PATH) -> list[dict[str, Any]]:
    """Load and return verified IPC/CrPC/IEA <-> BNS/BNSS/BSA concordance mappings."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("mappings"), list):
        raise ValueError(f"Invalid criminal_concordance YAML structure at {path}")
    return [dict(item) for item in raw["mappings"] if isinstance(item, dict)]


def load_invalidations_yaml(path: Path | str = DEFAULT_INVALIDATIONS_PATH) -> list[dict[str, Any]]:
    """Load and return verified judicial invalidations (struck-down / read-down / abeyance)."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("invalidations"), list):
        raise ValueError(f"Invalid judicial_invalidations YAML structure at {path}")
    return [dict(item) for item in raw["invalidations"] if isinstance(item, dict)]


def load_courts_yaml(path: Path | str = DEFAULT_COURTS_PATH) -> list[dict[str, Any]]:
    """Load and return canonical courts, tribunals, regulators, and district directory."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("courts"), list):
        raise ValueError(f"Invalid courts_directory YAML structure at {path}")
    return [dict(item) for item in raw["courts"] if isinstance(item, dict)]


def load_curated_provisions_yaml(
    path: Path | str = DEFAULT_CURATED_PROVISIONS_PATH,
) -> list[dict[str, Any]]:
    """Load and return curated bare-act statutory provisions from YAML."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("provisions"), list):
        raise ValueError(f"Invalid curated_statute_provisions YAML structure at {path}")
    return [dict(item) for item in raw["provisions"] if isinstance(item, dict)]


def _resolve_lockfile(lockfile: Lockfile | Path | str | None) -> Lockfile:
    if lockfile is None:
        return load_and_validate_lockfile(DEFAULT_LOCKFILE_PATH)
    if isinstance(lockfile, Lockfile):
        return lockfile
    return load_and_validate_lockfile(lockfile)


def _extract_records_from_json_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in (
            "records",
            "items",
            "data",
            "acts",
            "provisions",
            "sections",
            "articles",
            "judgments",
            "volumes",
            "debates",
            "rows",
        ):
            val = payload.get(key)
            if isinstance(val, list):
                return [dict(item) for item in val if isinstance(item, dict)]
        return [dict(payload)]
    return []


def _read_parquet_dicts(path: Path) -> list[dict[str, Any]]:
    con = duckdb.connect(":memory:")
    try:
        escaped = str(path).replace("'", "''")
        cur = con.execute(f"SELECT * FROM read_parquet('{escaped}')")
        cols = [desc[0] for desc in (cur.description or ())]
        return [dict(zip(cols, row, strict=False)) for row in cur.fetchall()]
    finally:
        con.close()


def _read_structured_files(staged_dir: Path) -> list[dict[str, Any]]:
    if not staged_dir.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for path in sorted(staged_dir.rglob("*")):
        if not path.is_file():
            continue
        suffix = path.suffix.lower()
        if suffix == ".json":
            payload = json.loads(path.read_text(encoding="utf-8"))
            records.extend(_extract_records_from_json_payload(payload))
        elif suffix == ".jsonl":
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped:
                    item = json.loads(stripped)
                    if isinstance(item, dict):
                        records.append(item)
        elif suffix == ".parquet":
            records.extend(_read_parquet_dicts(path))
    return records


def _normalize_act_provision_records(
    raw_records: Sequence[Mapping[str, Any]],
    *,
    default_source_id: str,
    default_source_url: str,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in raw_records:
        nested = row.get("provisions") or row.get("sections")
        parent_act_id = str(row.get("act_id") or "").strip()
        parent_short = str(row.get("act_short") or row.get("short_name") or "").strip()
        parent_full = str(
            row.get("act_full_name") or row.get("full_name") or row.get("title") or parent_short or parent_act_id
        ).strip()
        parent_year = row.get("year")
        parent_act_number = row.get("act_number")
        parent_status = str(row.get("act_status") or row.get("status") or "in_force").strip() or "in_force"
        parent_comm = row.get("commencement_date")
        parent_repeal = row.get("repeal_date")
        parent_replaced = row.get("replaced_by_act")
        parent_src_url = str(row.get("source_url") or default_source_url)

        if isinstance(nested, list) and nested:
            for sub in nested:
                if not isinstance(sub, Mapping):
                    continue
                item = _build_single_provision(
                    sub,
                    act_id=parent_act_id,
                    act_short=parent_short,
                    act_full_name=parent_full,
                    year=parent_year,
                    act_number=parent_act_number,
                    act_status=parent_status,
                    commencement_date=parent_comm,
                    repeal_date=parent_repeal,
                    replaced_by_act=parent_replaced,
                    default_source_id=default_source_id,
                    default_source_url=parent_src_url,
                )
                if item is not None:
                    out.append(item)
        else:
            item = _build_single_provision(
                row,
                act_id=parent_act_id,
                act_short=parent_short,
                act_full_name=parent_full,
                year=parent_year,
                act_number=parent_act_number,
                act_status=parent_status,
                commencement_date=parent_comm,
                repeal_date=parent_repeal,
                replaced_by_act=parent_replaced,
                default_source_id=default_source_id,
                default_source_url=parent_src_url,
            )
            if item is not None:
                out.append(item)
    return out


def _build_single_provision(
    row: Mapping[str, Any],
    *,
    act_id: str,
    act_short: str,
    act_full_name: str,
    year: Any,
    act_number: Any,
    act_status: str,
    commencement_date: Any,
    repeal_date: Any,
    replaced_by_act: Any,
    default_source_id: str,
    default_source_url: str,
) -> dict[str, Any] | None:
    aid = str(row.get("act_id") or act_id).strip()
    if not aid:
        return None
    text = str(row.get("text") or "").strip()
    if not text:
        return None
    short = str(row.get("act_short") or row.get("short_name") or act_short or aid).strip()
    full_name = str(row.get("act_full_name") or row.get("full_name") or act_full_name or short).strip()
    kind = str(row.get("kind") or row.get("section_type") or "section").strip().lower() or "section"
    ref = str(row.get("ref") or row.get("section_number") or "1").strip()
    ref_norm = str(row.get("ref_norm") or ref).strip().upper()
    doc_id = str(row.get("doc_id") or (f"{aid}:{ref}" if kind == "section" else f"{aid}:{kind[:4]}:{ref_norm}"))
    yr_val = row.get("year") if row.get("year") is not None else year
    yr_int = int(yr_val) if yr_val is not None and str(yr_val).strip() != "" else None
    status = str(row.get("status") or act_status or "in_force").strip() or "in_force"

    return {
        "doc_id": doc_id,
        "act_id": aid,
        "act_short": short,
        "act_full_name": full_name,
        "year": yr_int,
        "act_number": row.get("act_number") if row.get("act_number") is not None else act_number,
        "act_status": act_status,
        "kind": kind,
        "ref": ref,
        "ref_norm": ref_norm,
        "chapter_num": row.get("chapter_num") or row.get("chapter"),
        "chapter_title": row.get("chapter_title"),
        "part": row.get("part"),
        "title": str(row.get("title") or row.get("section_title") or f"Section {ref}").strip(),
        "text": text,
        "status": status,
        "commencement_date": row.get("commencement_date") if row.get("commencement_date") is not None else commencement_date,
        "repeal_date": row.get("repeal_date") if row.get("repeal_date") is not None else repeal_date,
        "replaced_by_act": row.get("replaced_by_act") if row.get("replaced_by_act") is not None else replaced_by_act,
        "metadata": dict(row.get("metadata") or {}) if isinstance(row.get("metadata"), Mapping) else {},
        "source_id": default_source_id,
        "source_url": str(row.get("source_url") or default_source_url),
        "as_of": row.get("as_of"),
        "commercial_safe": True,
    }


def _clean_constitution_text(text: str) -> str:
    """Normalize line endings/hyphens and strip control/BiDi/Tag chars and `</corpus_text>`."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\ufffe", "-")
    cleaned = strip_control_chars(normalized)
    return escape_corpus_delimiters(cleaned)


def _slice_constitution_body(text: str) -> str:
    """Slice from Constitution body start (skipping TOC when present) up to FIRST SCHEDULE."""
    body = text
    m_preamble = _CONSTITUTION_PREAMBLE_BODY_RE.search(body)
    if m_preamble is not None:
        body = body[m_preamble.start() :]
    elif _CONSTITUTION_TOC_RE.search(body) is not None:
        part_i_matches = list(_CONSTITUTION_PART_I_RE.finditer(body))
        if len(part_i_matches) >= 2:
            chosen_start: int | None = None
            for pm in part_i_matches:
                tail = body[pm.start() :]
                if _CONSTITUTION_BODY_ARTICLE_RE.search(tail[:4000]) is not None:
                    chosen_start = pm.start()
                    break
            body = body[chosen_start if chosen_start is not None else part_i_matches[-1].start() :]
        elif part_i_matches:
            body = body[part_i_matches[0].start() :]

    m_sched = _CONSTITUTION_FIRST_SCHEDULE_RE.search(body)
    if m_sched is not None:
        body = body[: m_sched.start()]

    body = re.sub(r"_{10,}.*?(?=\n\n|\Z)", "", body, flags=re.DOTALL)
    body = re.sub(r"(?m)^THE CONSTITUTION OF INDIA\s*\n\([^\n]+\)\s*\n\d+\s*$", "", body)
    if len(body) > 10_000:
        body = re.sub(r"(?m)^[ \t]*\d{1,3}[ \t]*$", "", body)
    return body


def _parse_constitution_pdf_text(full_text: str, *, source_url: str) -> list[dict[str, Any]]:
    sanitized = _clean_constitution_text(full_text or "")
    body_text = _slice_constitution_body(sanitized)
    if not body_text.strip():
        return []

    part_positions: list[int] = []
    part_names: list[str] = []
    for pm in _CONSTITUTION_PART_RE.finditer(body_text):
        roman = (pm.group(1) or pm.group(2) or "").strip().upper()
        if roman:
            part_positions.append(pm.start())
            part_names.append(f"Part {roman}")

    matches = list(_CONSTITUTION_BODY_ARTICLE_RE.finditer(body_text))
    if not matches:
        matches = list(_CONSTITUTION_ARTICLE_RE.finditer(body_text))

    rows: list[dict[str, Any]] = []
    seen_doc_ids: set[str] = set()
    if matches:
        for idx, m in enumerate(matches):
            ref = m.group(1).strip().replace("-", "")
            num_m = re.match(r"^(\d+)", ref)
            if num_m is None:
                continue
            art_num = int(num_m.group(1))
            if not (1 <= art_num <= 395):
                continue

            doc_id = f"constitution:art:{ref}"
            if doc_id in seen_doc_ids:
                continue

            raw_title = re.sub(r"\s+", " ", m.group(2)).strip(" .—–-[]")
            title = raw_title or f"Article {ref}"
            body_start = m.end()
            body_end = matches[idx + 1].start() if idx + 1 < len(matches) else len(body_text)
            body = body_text[body_start:body_end].strip() or title
            if not body:
                continue

            part_idx = bisect_right(part_positions, m.start()) - 1
            current_part = part_names[part_idx] if part_idx >= 0 else None

            seen_doc_ids.add(doc_id)
            ref_norm = ref.upper()
            rows.append(
                {
                    "doc_id": doc_id,
                    "act_id": "constitution",
                    "act_short": "Constitution",
                    "act_full_name": "The Constitution of India",
                    "year": 1950,
                    "kind": "article",
                    "ref": ref,
                    "ref_norm": ref_norm,
                    "part": current_part,
                    "title": title,
                    "text": body,
                    "status": "in_force",
                    "commencement_date": "1950-01-26",
                    "source_id": "legislative_constitution",
                    "source_url": source_url,
                    "commercial_safe": True,
                }
            )
    elif body_text.strip():
        rows.append(
            {
                "doc_id": "constitution:preamble",
                "act_id": "constitution",
                "act_short": "Constitution",
                "act_full_name": "The Constitution of India",
                "year": 1950,
                "kind": "preamble",
                "ref": "Preamble",
                "ref_norm": "PREAMBLE",
                "part": None,
                "title": "Preamble",
                "text": body_text.strip(),
                "status": "in_force",
                "commencement_date": "1950-01-26",
                "source_id": "legislative_constitution",
                "source_url": source_url,
                "commercial_safe": True,
            }
        )
    return rows


class OfficialStatutesFetcher:
    """Fetches and loads the 5 official statutory, constitutional, CAD, and e-SCR sources."""

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
        if source_id not in OFFICIAL_SOURCE_IDS:
            raise ValueError(
                f"Unknown official source {source_id!r}; expected one of {OFFICIAL_SOURCE_IDS}"
            )
        return self.lockfile.get_source(source_id)

    def _default_filename(self, entry: SourceEntry, target_url: str) -> str:
        name = Path(urlsplit(target_url).path).name
        if name and "." in name:
            return name
        if entry.access_method == "https_pdf":
            return f"{entry.id}.pdf"
        if entry.access_method == "https_parquet":
            return f"{entry.id}.parquet"
        return f"{entry.id}.json"

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
    ) -> OfficialFetchResult:
        entry = self.get_source_entry(source_id)
        target_url = url or entry.uri
        sha_to_check = expected_sha256 if expected_sha256 is not None else (entry.sha256 if verify_sha256 else None)
        fname = self._default_filename(entry, target_url)

        resolved_dir: Path | None = None
        if dest_dir is not None:
            resolved_dir = Path(dest_dir)
        elif work_dir is not None:
            resolved_dir = Path(work_dir) / "raw" / source_id

        dest_path = (resolved_dir / fname) if resolved_dir is not None else None

        if dry_run:
            return OfficialFetchResult(
                source_id=source_id,
                uri=target_url,
                dest_path=dest_path,
                sha256=sha_to_check or "",
                byte_count=0,
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
            return OfficialFetchResult(
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
                return OfficialFetchResult(
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

        return OfficialFetchResult(
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
    ) -> OfficialFetchResult:
        return self.fetch_source(
            source_id,
            work_dir=work_dir,
            dry_run=dry_run,
            fixture=fixture,
            verify_sha256=verify_sha256,
        )

    def sync_all(
        self,
        work_dir: Path | str,
        *,
        source_ids: Sequence[str] | None = None,
        dry_run: bool = False,
        fixture: bool = False,
        verify_sha256: bool = True,
    ) -> list[OfficialFetchResult]:
        targets = list(source_ids) if source_ids is not None else list(OFFICIAL_SOURCE_IDS)
        return [
            self.sync_source(
                sid,
                work_dir=work_dir,
                dry_run=dry_run,
                fixture=fixture,
                verify_sha256=verify_sha256,
            )
            for sid in targets
        ]

    def _fixture_payload_bytes(self, source_id: str) -> bytes:
        if source_id == "indiacode_central_acts":
            return json.dumps(
                [
                    {
                        "act_id": "rti-2005",
                        "act_short": "RTIAct",
                        "act_full_name": "The Right to Information Act, 2005",
                        "year": 2005,
                        "ref": "6",
                        "title": "Request for obtaining information",
                        "text": "A person who desires to obtain any information under this Act shall make a request in writing.",
                    }
                ]
            ).encode("utf-8")
        if source_id == "prs_2026_delta":
            return json.dumps(
                [
                    {
                        "act_id": "bns-2023",
                        "act_short": "BNS",
                        "act_full_name": "The Bharatiya Nyaya Sanhita, 2023",
                        "year": 2023,
                        "ref": "106",
                        "title": "Causing death by negligence",
                        "text": "Whoever causes death of any person by doing any rash or negligent act not amounting to culpable homicide.",
                    }
                ]
            ).encode("utf-8")
        if source_id == "legislative_constitution":
            return json.dumps(
                [
                    {
                        "ref": "300A",
                        "kind": "article",
                        "part": "Part XII",
                        "title": "Persons not to be deprived of property save by authority of law",
                        "text": "No person shall be deprived of his property save by authority of law.",
                    }
                ]
            ).encode("utf-8")
        if source_id == "cad_debates":
            return json.dumps(
                [
                    {
                        "volume": "VII",
                        "ref": "1948-11-04-01",
                        "date": "1948-11-04",
                        "speaker": "Dr. B.R. Ambedkar",
                        "title": "Draft Constitution Debate",
                        "text": "Constitutional morality has to be cultivated.",
                    }
                ]
            ).encode("utf-8")
        return json.dumps(
            [
                {
                    "cnr": "ESCR010000342017",
                    "neutral_citation": "2017 INSC 801",
                    "scr_citation": "[2017] 10 S.C.R. 569",
                    "title": "Justice K.S. Puttaswamy (Retd.) v. Union of India",
                    "headnote": "Right to privacy is protected under Article 21.",
                }
            ]
        ).encode("utf-8")

    def load_indiacode_central_acts(self, staged_dir: Path | str) -> list[dict[str, Any]]:
        """Load staged India Code Central Acts JSON/Parquet files into normalized provisions."""
        entry = self.get_source_entry("indiacode_central_acts")
        raw = _read_structured_files(Path(staged_dir))
        return _normalize_act_provision_records(
            raw,
            default_source_id="indiacode_central_acts",
            default_source_url=entry.uri,
        )

    def load_prs_2026_delta(self, staged_dir: Path | str) -> list[dict[str, Any]]:
        """Load staged PRS Legislative Research 2024–2026 Delta JSON/Parquet files."""
        entry = self.get_source_entry("prs_2026_delta")
        raw = _read_structured_files(Path(staged_dir))
        return _normalize_act_provision_records(
            raw,
            default_source_id="prs_2026_delta",
            default_source_url=entry.uri,
        )

    def load_legislative_constitution(self, staged_dir: Path | str) -> list[dict[str, Any]]:
        """Load staged Constitution JSON/Parquet/PDF files, tagging Constitution articles as `kind='article'`."""
        sdir = Path(staged_dir)
        if not sdir.is_dir():
            return []
        entry = self.get_source_entry("legislative_constitution")
        out: list[dict[str, Any]] = []

        for row in _read_structured_files(sdir):
            nested = row.get("articles") or row.get("provisions")
            items = nested if isinstance(nested, list) else [row]
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                ref = str(item.get("ref") or item.get("article") or item.get("section_number") or "").strip()
                text = str(item.get("text") or "").strip()
                if not ref or not text:
                    continue
                raw_kind = str(item.get("kind") or "article").strip().lower()
                kind = raw_kind if raw_kind in ("schedule", "preamble", "amendment") else "article"
                ref_norm = str(item.get("ref_norm") or ref).strip().upper()
                doc_id = str(
                    item.get("doc_id")
                    or (f"constitution:art:{ref}" if kind == "article" else f"constitution:{kind[:3]}:{ref_norm}")
                )
                out.append(
                    {
                        "doc_id": doc_id,
                        "act_id": "constitution",
                        "act_short": "Constitution",
                        "act_full_name": "The Constitution of India",
                        "year": 1950,
                        "kind": kind,
                        "ref": ref,
                        "ref_norm": ref_norm,
                        "chapter_num": item.get("chapter_num"),
                        "chapter_title": item.get("chapter_title"),
                        "part": item.get("part"),
                        "title": str(item.get("title") or f"Article {ref}").strip(),
                        "text": text,
                        "status": str(item.get("status") or "in_force"),
                        "commencement_date": item.get("commencement_date") or "1950-01-26",
                        "repeal_date": item.get("repeal_date"),
                        "source_id": "legislative_constitution",
                        "source_url": str(item.get("source_url") or entry.uri),
                        "as_of": item.get("as_of"),
                        "commercial_safe": True,
                    }
                )

        for pdf_path in sorted(sdir.rglob("*.pdf")):
            pdf_res = extract_pdf_file(pdf_path, max_bytes=50 * 1024 * 1024, max_pages=1200)
            out.extend(_parse_constitution_pdf_text(pdf_res.full_text, source_url=entry.uri))

        return out

    def load_cad_debates(self, staged_dir: Path | str) -> list[dict[str, Any]]:
        """Load staged Constituent Assembly Debates (Volumes I–XII) into `state_regulator_provisions` rows."""
        sdir = Path(staged_dir)
        if not sdir.is_dir():
            return []
        entry = self.get_source_entry("cad_debates")
        out: list[dict[str, Any]] = []
        raw_rows = _read_structured_files(sdir)

        for pdf_path in sorted(sdir.rglob("*.pdf")):
            pdf_res = extract_pdf_file(pdf_path)
            if pdf_res.full_text.strip():
                raw_rows.append(
                    {
                        "volume": pdf_path.stem,
                        "ref": pdf_path.stem,
                        "title": f"Constituent Assembly Debates ({pdf_path.stem})",
                        "text": pdf_res.full_text,
                    }
                )

        for idx, row in enumerate(raw_rows, start=1):
            vol = str(row.get("volume") or "I").strip()
            vol_slug = re.sub(r"[^a-z0-9]+", "-", vol.lower()).strip("-") or "i"
            ref = str(row.get("ref") or row.get("debate_id") or row.get("date") or idx).strip()
            speaker = str(row.get("speaker") or "").strip()
            body = str(row.get("text") or row.get("speech") or "").strip()
            if not body:
                continue
            full_text = f"{speaker}: {body}" if speaker and speaker not in body else body
            act_id = f"cad-debates-vol-{vol_slug}"
            doc_id = str(row.get("doc_id") or f"cad-debates:vol-{vol_slug}:{ref}")
            out.append(
                {
                    "doc_id": doc_id,
                    "act_id": act_id,
                    "short_name": f"CAD-Vol-{vol}",
                    "jurisdiction": "Central",
                    "category": "cad_debate",
                    "kind": "cad_debate",
                    "ref": ref,
                    "title": str(row.get("title") or row.get("subject") or f"CAD Volume {vol}").strip(),
                    "status": "in_force",
                    "effective_date": str(row.get("date") or row.get("effective_date") or ""),
                    "text": full_text,
                    "commercial_safe": True,
                    "terms_status": "cleared",
                    "license_spdx": "CC0-1.0",
                    "source_id": "cad_debates",
                    "source_url": str(row.get("source_url") or entry.uri),
                    "volume": vol,
                }
            )
        return out

    def load_escr_judgments(self, staged_dir: Path | str) -> list[dict[str, Any]]:
        """Load staged Supreme Court e-SCR headnotes and neutral citations."""
        sdir = Path(staged_dir)
        if not sdir.is_dir():
            return []
        entry = self.get_source_entry("escr_judgments")
        out: list[dict[str, Any]] = []
        for row in _read_structured_files(sdir):
            neutral = str(row.get("neutral_citation") or row.get("nc_display") or "").strip() or None
            scr = str(row.get("scr_citation") or row.get("citation") or "").strip() or None
            cnr = str(row.get("cnr") or "").strip() or None
            title = str(row.get("title") or row.get("case_name") or "").strip()
            headnote = str(row.get("headnote") or row.get("text") or row.get("summary") or "").strip()
            case_id = str(row.get("case_id") or "").strip()
            if not (case_id or cnr or neutral or title):
                continue
            coram_raw = row.get("judge_coram") or row.get("judges")
            coram_str = (
                "; ".join(str(j).strip() for j in coram_raw if str(j).strip())
                if isinstance(coram_raw, list)
                else str(coram_raw or "").strip()
            )
            year_val = row.get("year")
            dec_date = row.get("decision_date")
            yr_int: int | None = int(year_val) if year_val is not None and str(year_val).strip() else None
            if yr_int is None and dec_date and len(str(dec_date)) >= 4 and str(dec_date)[:4].isdigit():
                yr_int = int(str(dec_date)[:4])

            out.append(
                {
                    "case_id": case_id or None,
                    "cnr": cnr,
                    "neutral_citation": neutral,
                    "scr_citation": scr,
                    "air_citation": row.get("air_citation"),
                    "scc_citation": row.get("scc_citation"),
                    "title": title,
                    "petitioner": row.get("petitioner"),
                    "respondent": row.get("respondent"),
                    "decision_date": str(dec_date) if dec_date is not None else None,
                    "year": yr_int,
                    "judge_coram": coram_str or None,
                    "author_judge": row.get("author_judge"),
                    "bench_strength": row.get("bench_strength"),
                    "disposal_nature": row.get("disposal_nature"),
                    "case_type": row.get("case_type"),
                    "acts_cited": list(row.get("acts_cited") or []),
                    "headnote": headnote,
                    "text": headnote,
                    "source_id": "escr_judgments",
                    "source_url": str(row.get("source_url") or entry.uri),
                    "commercial_safe": True,
                }
            )
        return out
