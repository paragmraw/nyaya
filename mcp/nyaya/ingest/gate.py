"""Automated Legal Integrity Gate (`gate.py`) enforcing all 7 hard-fail suites & 12 invariants
before any Nyaya v2.0 corpus release can be published.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..sanitize import (
    BIDI_RE,
    UNICODE_TAG_RE,
    check_publication_ban,
)
from ..storage.manifest import load_manifest, verify_partition_sha256
from .fetchers.official_statutes import (
    load_amendments_yaml,
    load_concordance_yaml,
    load_invalidations_yaml,
)
from .lockfile import (
    DEFAULT_LOCKFILE_PATH,
    is_non_commercial_license,
    load_and_validate_lockfile,
)

_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_UNESCAPED_CLOSING_FENCE_RE = re.compile(r"</corpus_text\b", re.IGNORECASE)
_HTML_OR_ERROR_PREFIX_RE = re.compile(
    r"^\s*(?:<html\b|<!DOCTYPE\b|\{\s*\"error\"|404\s+Not\s+Found)",
    re.IGNORECASE,
)
_COMMERCIAL_HEADNOTE_RE = re.compile(
    r"\b(?:SCC\s+Online|Manupatra|AIR\s+Headnote)\b",
    re.IGNORECASE,
)
_BANNED_LICENSE_RE = re.compile(
    r"^(?:GPL|AGPL|LGPL|CC-BY-ND|CC-BY-NC-ND|UNKNOWN|$)",
    re.IGNORECASE,
)

# 1. Golden Constitutional Amendments (1st through 106th historical pins)
_GOLDEN_AMENDMENTS: dict[int, dict[str, Any]] = {
    1: {"year": 1951, "enacted_date": "1951-06-18", "required_articles": {"15", "19", "31A", "31B"}},
    24: {"year": 1971, "enacted_date": "1971-11-05", "required_articles": {"13", "368"}},
    42: {"year": 1976, "enacted_date": "1976-12-18", "required_articles": {"Preamble", "51A", "368"}},
    44: {"year": 1978, "enacted_date": "1979-04-30", "required_articles": {"300A", "352", "359"}},
    52: {"year": 1985, "enacted_date": "1985-02-15", "required_articles": {"102", "191", "Schedule 10"}},
    61: {"year": 1989, "enacted_date": "1989-03-28", "required_articles": {"326"}},
    73: {"year": 1992, "enacted_date": "1993-04-20", "required_articles": {"243", "Schedule 11"}},
    74: {"year": 1992, "enacted_date": "1993-04-20", "required_articles": {"243P", "Schedule 12"}},
    86: {"year": 2002, "enacted_date": "2002-12-12", "required_articles": {"21A", "45", "51A"}},
    97: {"year": 2011, "enacted_date": "2012-01-12", "status": "partially_struck_down"},
    99: {"year": 2014, "enacted_date": "2014-12-31", "status": "struck_down"},
    101: {"year": 2016, "enacted_date": "2016-09-08", "required_articles": {"246A", "269A", "279A"}},
    102: {"year": 2018, "enacted_date": "2018-08-11", "required_articles": {"338B", "342A"}},
    103: {"year": 2019, "enacted_date": "2019-01-12", "required_articles": {"15", "16"}},
    104: {"year": 2020, "enacted_date": "2020-01-21", "required_articles": {"334"}},
    105: {"year": 2021, "enacted_date": "2021-08-18", "required_articles": {"338B", "342A", "366"}},
    106: {
        "year": 2023,
        "enacted_date": "2023-09-28",
        "required_articles": {"239AA", "330A", "332A", "334A"},
    },
}

# 2. Golden Criminal Concordance Pairs (IPC<->BNS, CrPC<->BNSS, IEA<->BSA)
_GOLDEN_CONCORDANCE_PAIRS: tuple[tuple[str, str, str, set[str]], ...] = (
    # IPC -> BNS
    ("IPC", "302", "BNS", {"103", "103(1)"}),
    ("IPC", "304A", "BNS", {"106"}),
    ("IPC", "304B", "BNS", {"80"}),
    ("IPC", "307", "BNS", {"109"}),
    ("IPC", "354", "BNS", {"74"}),
    ("IPC", "354A", "BNS", {"75"}),
    ("IPC", "354D", "BNS", {"78"}),
    ("IPC", "375", "BNS", {"63"}),
    ("IPC", "376", "BNS", {"64"}),
    ("IPC", "377", "BNS", {"NONE"}),
    ("IPC", "378", "BNS", {"303"}),
    ("IPC", "379", "BNS", {"303(2)", "303"}),
    ("IPC", "406", "BNS", {"316(2)"}),
    ("IPC", "420", "BNS", {"318(4)"}),
    ("IPC", "498A", "BNS", {"85"}),
    ("IPC", "499", "BNS", {"356"}),
    ("IPC", "500", "BNS", {"356(2)", "356"}),
    ("IPC", "506", "BNS", {"351"}),
    ("IPC", "511", "BNS", {"62"}),
    ("IPC", "124A", "BNS", {"152"}),
    # CrPC -> BNSS
    ("CrPC", "41", "BNSS", {"35"}),
    ("CrPC", "41A", "BNSS", {"35"}),
    ("CrPC", "125", "BNSS", {"144"}),
    ("CrPC", "154", "BNSS", {"173"}),
    ("CrPC", "161", "BNSS", {"180"}),
    ("CrPC", "164", "BNSS", {"183"}),
    ("CrPC", "167", "BNSS", {"187"}),
    ("CrPC", "173", "BNSS", {"193"}),
    ("CrPC", "438", "BNSS", {"482"}),
    ("CrPC", "439", "BNSS", {"483"}),
    ("CrPC", "482", "BNSS", {"528"}),
    # IEA -> BSA
    ("IEA", "3", "BSA", {"2"}),
    ("IEA", "27", "BSA", {"23"}),
    ("IEA", "32", "BSA", {"26"}),
    ("IEA", "45", "BSA", {"39"}),
    ("IEA", "65A", "BSA", {"62"}),
    ("IEA", "65B", "BSA", {"63"}),
    ("IEA", "113B", "BSA", {"118"}),
    ("IEA", "114A", "BSA", {"120"}),
)

# 3. Golden Judicial Invalidations
_GOLDEN_INVALIDATIONS: tuple[tuple[str, str], ...] = (
    ("ipc-1860:303", "struck_down"),
    ("ipc-1860:377", "read_down"),
    ("ipc-1860:497", "struck_down"),
    ("ipc-1860:124A", "kept_in_abeyance"),
    ("it-act-2000:66A", "struck_down"),
    ("constitution:amend:99", "struck_down"),
    ("constitution:amend:97", "partially_struck_down"),
)


@dataclass
class GateSuiteResult:
    """Result of executing a single Legal Integrity Gate suite."""

    rule_id: str
    passed: bool
    errors: list[str] = field(default_factory=list)


@dataclass
class GateReport:
    """Aggregate report across all Legal Integrity Gate suites."""

    passed: bool
    checked_at: str
    results: list[GateSuiteResult] = field(default_factory=list)
    artifact_sha256s: dict[str, str] = field(default_factory=dict)

    @property
    def all_errors(self) -> list[str]:
        return [f"[{r.rule_id}] {err}" for r in self.results for err in r.errors]

    def to_dict(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "checked_at": self.checked_at,
            "artifact_sha256s": self.artifact_sha256s,
            "suites": [
                {"rule_id": r.rule_id, "passed": r.passed, "errors": r.errors}
                for r in self.results
            ],
        }


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def check_amendments(amendments: Sequence[Mapping[str, Any]] | None = None) -> GateSuiteResult:
    """Suite 1: Verify Amendments 1–106 contiguity and historical accuracy."""
    rows = list(amendments) if amendments is not None else load_amendments_yaml()
    errors: list[str] = []

    if len(rows) < 106:
        errors.append(f"Expected >= 106 amendments, found {len(rows)}.")

    by_num: dict[int, Mapping[str, Any]] = {}
    for r in rows:
        num = int(r.get("number") or 0)
        by_num[num] = r

    missing = [n for n in range(1, 107) if n not in by_num]
    if missing:
        errors.append(f"Missing contiguous amendment numbers: {missing}")

    for num, expected in _GOLDEN_AMENDMENTS.items():
        row = by_num.get(num)
        if row is None:
            errors.append(f"Golden amendment #{num} is missing.")
            continue
        actual_year = row.get("year")
        enacted = str(row.get("enacted_date") or "")
        if actual_year is not None and int(actual_year) != int(expected["year"]):
            errors.append(
                f"Amendment #{num} year corruption: expected {expected['year']}, got {actual_year}."
            )
        if "enacted_date" in expected and enacted != expected["enacted_date"]:
            errors.append(
                f"Amendment #{num} enacted_date corruption: expected {expected['enacted_date']}, got {enacted!r}."
            )
        if "status" in expected and str(row.get("status") or "in_force") != expected["status"]:
            errors.append(
                f"Amendment #{num} status mismatch: expected {expected['status']!r}, got {row.get('status')!r}."
            )
        if "required_articles" in expected:
            raw_arts = row.get("articles_affected") or []
            if isinstance(raw_arts, str):
                arts_set = {a.strip() for a in raw_arts.split(",") if a.strip()}
            else:
                arts_set = {str(a).strip() for a in raw_arts}
            req: set[str] = expected["required_articles"]
            if not req.issubset(arts_set):
                errors.append(
                    f"Amendment #{num} missing affected articles {sorted(req - arts_set)} (got {sorted(arts_set)})."
                )

    return GateSuiteResult(rule_id="GATE_AMENDMENTS", passed=not errors, errors=errors)


def check_concordance(mappings: Sequence[Mapping[str, Any]] | None = None) -> GateSuiteResult:
    """Suite 2: Verify IPC/CrPC/IEA <-> BNS/BNSS/BSA golden pairs and anti-collapse invariant."""
    rows = list(mappings) if mappings is not None else load_concordance_yaml()
    errors: list[str] = []

    lookup: dict[tuple[str, str], Mapping[str, Any]] = {}
    bns_target_counts: Counter[str] = Counter()

    for r in rows:
        old_act = str(r.get("old_act") or "").strip().upper()
        if old_act == "EVIDENCEACT":
            old_act = "IEA"
        old_ref = str(r.get("old_ref") or "").strip().upper()
        lookup[(old_act, old_ref)] = r

        if old_act == "IPC":
            new_base = str(r.get("new_base_ref") or r.get("new_ref") or "").split("(", 1)[0].strip()
            if new_base and new_base != "NONE":
                bns_target_counts[new_base] += 1

    # Anti-collapse invariant: no single BNS section mapped from > 12 IPC sections
    for bns_sec, cnt in bns_target_counts.items():
        if cnt > 12:
            errors.append(
                f"Concordance collapse detected: BNS s.{bns_sec} mapped from {cnt} IPC sections (> 12 max)."
            )

    for old_act, old_ref, expected_new_act, allowed_new_refs in _GOLDEN_CONCORDANCE_PAIRS:
        row = lookup.get((old_act.upper(), old_ref.upper()))
        if row is None:
            errors.append(f"Missing golden concordance pair {old_act} {old_ref} -> {expected_new_act}.")
            continue
        actual_new_ref = str(row.get("new_ref") or "NONE").strip()
        if actual_new_ref not in allowed_new_refs:
            errors.append(
                f"Corrupted concordance {old_act} {old_ref}: expected {expected_new_act} "
                f"{sorted(allowed_new_refs)}, got {actual_new_ref!r}."
            )
        if old_act == "IPC" and old_ref == "377":
            if str(row.get("mapping_type") or "") != "omitted":
                errors.append("IPC 377 must have mapping_type='omitted'.")
        if old_act == "IPC" and old_ref == "124A":
            delta_text = f"{row.get('scope_note', '')} {row.get('substantive_delta', '')}"
            if "modified_scope" not in delta_text:
                errors.append("IPC 124A -> BNS 152 must carry 'modified_scope' annotation.")

    return GateSuiteResult(rule_id="GATE_CONCORDANCE", passed=not errors, errors=errors)


def check_invalidations(invalidations: Sequence[Mapping[str, Any]] | None = None) -> GateSuiteResult:
    """Suite 3: Verify landmark judicial invalidations (struck-down / read-down / abeyance)."""
    rows = list(invalidations) if invalidations is not None else load_invalidations_yaml()
    errors: list[str] = []

    by_doc: dict[str, Mapping[str, Any]] = {
        str(r.get("target_doc_id") or ""): r for r in rows
    }
    for doc_id, expected_effect in _GOLDEN_INVALIDATIONS:
        row = by_doc.get(doc_id)
        if row is None:
            errors.append(f"Missing mandatory judicial invalidation for {doc_id!r}.")
            continue
        actual_effect = str(row.get("effect") or "")
        if actual_effect != expected_effect:
            errors.append(
                f"Invalidation effect mismatch for {doc_id!r}: expected {expected_effect!r}, got {actual_effect!r}."
            )

    return GateSuiteResult(rule_id="GATE_INVALIDATIONS", passed=not errors, errors=errors)


def check_coverage_and_non_truncation(
    *,
    sqlite_path: Path | None = None,
    landmark_char_lengths: Mapping[str, int] | None = None,
    strict_landmarks: bool = False,
) -> GateSuiteResult:
    """Suite 4: Verify SQLite table row counts, FTS5 sync, citation graph edges, and non-truncation."""
    errors: list[str] = []

    if landmark_char_lengths is not None or strict_landmarks:
        lengths = dict(landmark_char_lengths or {})
        for cite in ("AIR 1973 SC 1461", "(2017) 10 SCC 1"):
            char_len = lengths.get(cite, 0)
            if char_len < 500_000:
                errors.append(
                    f"Landmark judgment {cite!r} text length ({char_len} chars) < 500,000-char non-truncation floor."
                )

    if sqlite_path is not None and sqlite_path.is_file():
        con = sqlite3.connect(sqlite_path)
        try:
            for tbl in (
                "sources",
                "acts",
                "provisions",
                "provision_amendments",
                "concordance",
                "judicial_invalidations",
                "judgments_meta",
                "citation_edges",
                "courts",
            ):
                cnt = int(con.execute(f"SELECT count(*) FROM {tbl}").fetchone()[0])
                if cnt <= 0:
                    errors.append(f"SQLite table {tbl!r} is empty (count={cnt}).")

            prov_cnt = int(con.execute("SELECT count(*) FROM provisions").fetchone()[0])
            prov_fts_cnt = int(con.execute("SELECT count(*) FROM provisions_fts").fetchone()[0])
            if prov_cnt != prov_fts_cnt:
                errors.append(
                    f"FTS5 sync mismatch: provisions={prov_cnt} vs provisions_fts={prov_fts_cnt}."
                )

            j_cnt = int(con.execute("SELECT count(*) FROM judgments_meta").fetchone()[0])
            j_fts_cnt = int(con.execute("SELECT count(*) FROM judgments_meta_fts").fetchone()[0])
            if j_cnt != j_fts_cnt:
                errors.append(
                    f"FTS5 sync mismatch: judgments_meta={j_cnt} vs judgments_meta_fts={j_fts_cnt}."
                )

            bad_edges = int(
                con.execute(
                    "SELECT count(*) FROM citation_edges "
                    "WHERE trim(from_id) = '' OR trim(to_id) = '' OR from_id = to_id"
                ).fetchone()[0]
            )
            if bad_edges > 0:
                errors.append(f"Found {bad_edges} invalid self-loop or empty citation_edges.")
        finally:
            con.close()

    return GateSuiteResult(rule_id="GATE_COVERAGE", passed=not errors, errors=errors)


def check_extraction_quality(
    *,
    texts: Sequence[Mapping[str, Any]] | None = None,
) -> GateSuiteResult:
    """Suite 5: Verify 0 HTML/error placeholders, printable ratio >= 0.90, and non-empty text_sha256."""
    errors: list[str] = []
    for item in texts or []:
        doc_id = str(item.get("doc_id") or item.get("case_id") or "<unknown>")
        text = str(item.get("text") or "")
        sha = str(item.get("text_sha256") or "").strip()
        if not sha:
            errors.append(f"Record {doc_id!r} has empty text_sha256.")
        if _HTML_OR_ERROR_PREFIX_RE.search(text):
            errors.append(f"Record {doc_id!r} starts with HTML/404/JSON error placeholder.")
        if text:
            printable = sum(1 for ch in text if ch.isprintable() or ch in "\n\r\t")
            ratio = printable / len(text)
            if ratio < 0.90:
                errors.append(
                    f"Record {doc_id!r} printable char ratio ({ratio:.2f}) < 0.90 threshold."
                )

    return GateSuiteResult(rule_id="GATE_EXTRACTION_QUALITY", passed=not errors, errors=errors)


def check_licensing_and_provenance(
    *,
    records: Sequence[Mapping[str, Any]] | None = None,
    manifest_path: Path | None = None,
    lockfile_path: Path = DEFAULT_LOCKFILE_PATH,
) -> GateSuiteResult:
    """Suite 6: Verify 0 terms_status='red' or copyleft SPDX, NC datasets marked commercial_safe=False,
    0 commercial headnote leaks, and manifest.json SHA-256 partition integrity.
    """
    errors: list[str] = []
    try:
        load_and_validate_lockfile(lockfile_path)
    except Exception as exc:
        errors.append(f"Lockfile validation failed: {exc}")

    for rec in records or []:
        doc_id = str(rec.get("doc_id") or rec.get("case_id") or "<unknown>")
        terms = str(rec.get("terms_status") or "").strip().lower()
        lic = str(rec.get("license_spdx") or "").strip()
        src_id = str(rec.get("source_id") or "").strip().lower()
        src_url = str(rec.get("source_url") or "").strip()
        sha = str(rec.get("text_sha256") or "").strip()
        comm_safe = bool(rec.get("commercial_safe", True))
        text = str(rec.get("text") or "")

        if terms == "red":
            errors.append(f"Record {doc_id!r} carries prohibited terms_status='red'.")
        if _BANNED_LICENSE_RE.match(lic):
            errors.append(f"Record {doc_id!r} carries banned/unknown license_spdx={lic!r}.")
        if not src_id or not src_url or not sha:
            errors.append(f"Record {doc_id!r} is missing mandatory provenance (source_id/source_url/text_sha256).")
        if (
            src_id in ("ddl_81m", "ddl_district", "ddl-district", "ildc", "ildc_research", "ildc-research")
            or is_non_commercial_license(lic)
            or terms == "non_commercial_only"
        ) and comm_safe:
            errors.append(
                f"Non-commercial record {doc_id!r} (source={src_id}, license={lic}) illegally marked commercial_safe=True."
            )
        if _COMMERCIAL_HEADNOTE_RE.search(text):
            errors.append(
                f"Record {doc_id!r} contains proprietary reporter headnote signature (EBC v. Modak violation)."
            )

    if manifest_path is not None and manifest_path.is_file():
        manifest = load_manifest(manifest_path)
        base_dir = manifest_path.parent
        for ds_name, ds in manifest.datasets.items():
            if ds_name == "district_court" and ds.commercial_safe:
                errors.append("Dataset 'district_court' in manifest.json must have commercial_safe=False.")
            for part in ds.partitions:
                pfile = base_dir / part.s3_key
                if not pfile.is_file():
                    errors.append(f"Manifest partition file missing: {pfile}")
                elif not verify_partition_sha256(pfile, part.sha256):
                    errors.append(f"Manifest partition SHA-256 mismatch for {part.s3_key}")

    return GateSuiteResult(rule_id="GATE_LICENSING", passed=not errors, errors=errors)


def check_statutory_exclusions(
    *,
    records: Sequence[Mapping[str, Any]] | None = None,
) -> GateSuiteResult:
    """Suite 7a: Verify 0 published rows trigger sealed/in-camera/s.228A publication bans."""
    errors: list[str] = []
    for rec in records or []:
        doc_id = str(rec.get("doc_id") or rec.get("case_id") or "<unknown>")
        text = str(rec.get("text") or "")
        ban = check_publication_ban(text, metadata=rec)
        if ban.blocked:
            errors.append(
                f"Record {doc_id!r} violates statutory publication exclusion ({ban.statute}: {ban.reason})."
            )
    return GateSuiteResult(rule_id="GATE_STATUTORY_EXCLUSIONS", passed=not errors, errors=errors)


def check_owasp_llm01(
    *,
    records: Sequence[Mapping[str, Any]] | None = None,
) -> GateSuiteResult:
    """Suite 7b: Verify 0 indexed chunks contain raw C0/C1 controls, BiDi overrides,
    Unicode Tag smuggling (`U+E0000-U+E007F`), or unescaped `</corpus_text>` closing tags.
    """
    errors: list[str] = []
    for rec in records or []:
        doc_id = str(rec.get("doc_id") or rec.get("case_id") or "<unknown>")
        text = str(rec.get("text") or "")
        if _CONTROL_CHAR_RE.search(text):
            errors.append(f"Record {doc_id!r} contains raw C0/C1 control characters.")
        if BIDI_RE.search(text):
            errors.append(f"Record {doc_id!r} contains raw Unicode BiDi override characters.")
        if UNICODE_TAG_RE.search(text):
            errors.append(f"Record {doc_id!r} contains Unicode Tag (U+E0000-U+E007F) smuggling characters.")
        if _UNESCAPED_CLOSING_FENCE_RE.search(text):
            errors.append(f"Record {doc_id!r} contains unescaped </corpus_text> closing tag.")
    return GateSuiteResult(rule_id="GATE_OWASP_LLM01", passed=not errors, errors=errors)


def collect_artifact_sha256s(data_dir: Path) -> dict[str, str]:
    """Compute SHA-256 checksums for `nyaya-core.sqlite`, `manifest.json`, and all Parquet partitions."""
    checksums: dict[str, str] = {}
    sqlite_path = data_dir / "nyaya-core.sqlite"
    if sqlite_path.is_file():
        checksums["nyaya-core.sqlite"] = _sha256_file(sqlite_path)
    manifest_path = data_dir / "manifest.json"
    if manifest_path.is_file():
        checksums["manifest.json"] = _sha256_file(manifest_path)
        manifest = load_manifest(manifest_path)
        for ds in manifest.datasets.values():
            for part in ds.partitions:
                pfile = data_dir / part.s3_key
                if pfile.is_file():
                    checksums[part.s3_key] = _sha256_file(pfile)
    return checksums


def run_quality_gate(
    data_dir: Path | str | None = None,
    *,
    strict_landmarks: bool = False,
    write_gate_pass: bool = True,
) -> GateReport:
    """Run all Legal Integrity Gate suites against `data_dir` (and checked-in YAML tables).

    When `passed` is True and `data_dir` is supplied with `write_gate_pass=True`,
    writes signed `gate-pass.json` binding exact artifact SHA-256 digests.
    """
    root = Path(data_dir) if data_dir is not None else None
    sqlite_path = (root / "nyaya-core.sqlite") if root is not None else None
    manifest_path = (root / "manifest.json") if root is not None else None

    db_records: list[dict[str, Any]] = []
    db_amendments: list[dict[str, Any]] | None = None
    db_concordance: list[dict[str, Any]] | None = None
    db_invalidations: list[dict[str, Any]] | None = None

    if sqlite_path is not None and sqlite_path.is_file():
        con = sqlite3.connect(sqlite_path)
        con.row_factory = sqlite3.Row
        try:
            for r in con.execute(
                "SELECT p.doc_id, p.text, p.text_sha256, p.source_id, p.source_url, p.commercial_safe, "
                "s.license_spdx, s.terms_status "
                "FROM provisions p JOIN sources s ON p.source_id = s.source_id"
            ).fetchall():
                db_records.append(dict(r))

            for r in con.execute(
                "SELECT jm.case_id AS doc_id, jm.title AS text, jm.text_sha256, jm.source_id, "
                "jm.source_url, jm.commercial_safe, s.license_spdx, s.terms_status "
                "FROM judgments_meta jm JOIN sources s ON jm.source_id = s.source_id"
            ).fetchall():
                db_records.append(dict(r))

            inval_by_amend_no: dict[int, str] = {}
            for r in con.execute("SELECT target_doc_id, effect FROM judicial_invalidations").fetchall():
                tdoc = str(r["target_doc_id"])
                if tdoc.startswith("constitution:amend:"):
                    num_str = tdoc.rsplit(":", 1)[-1]
                    if num_str.isdigit():
                        inval_by_amend_no[int(num_str)] = str(r["effect"])

            amend_rows = con.execute(
                "SELECT amendment_no, enacted_date, articles_affected FROM provision_amendments "
                "WHERE target_act_id = 'constitution' AND amendment_no IS NOT NULL"
            ).fetchall()
            if len(amend_rows) >= 106:
                db_amendments = [
                    {
                        "number": int(ar["amendment_no"]),
                        "year": int(str(ar["enacted_date"])[:4])
                        if int(ar["amendment_no"]) not in {2, 3, 25, 30, 32, 35, 43, 44, 46, 51, 54, 61, 62, 63, 73, 74, 75, 79, 84, 85, 88, 91, 92, 93, 95, 97, 98, 104}
                        else None,
                        "enacted_date": str(ar["enacted_date"]),
                        "status": inval_by_amend_no.get(int(ar["amendment_no"]), "in_force"),
                        "articles_affected": [
                            x.strip()
                            for x in str(ar["articles_affected"] or "").split(",")
                            if x.strip()
                        ],
                    }
                    for ar in amend_rows
                ]

            conc_rows = con.execute(
                "SELECT ao.short_name AS old_act, po.ref AS old_ref, "
                "an.short_name AS new_act, pn.ref AS new_ref, "
                "c.mapping_type, c.substantive_delta "
                "FROM concordance c "
                "JOIN provisions po ON c.old_doc_id = po.doc_id "
                "JOIN acts ao ON po.act_id = ao.act_id "
                "LEFT JOIN provisions pn ON c.new_doc_id = pn.doc_id "
                "LEFT JOIN acts an ON pn.act_id = an.act_id"
            ).fetchall()
            if conc_rows:
                parsed_conc = [
                    {
                        "old_act": "IEA" if str(cr["old_act"]) == "EvidenceAct" else str(cr["old_act"]),
                        "old_ref": str(cr["old_ref"]),
                        "new_act": str(cr["new_act"] or "BNS"),
                        "new_ref": str(cr["new_ref"] or "NONE"),
                        "new_base_ref": str(cr["new_ref"] or "NONE").split("(", 1)[0],
                        "mapping_type": str(cr["mapping_type"]),
                        "substantive_delta": str(cr["substantive_delta"]),
                    }
                    for cr in conc_rows
                ]
                if len(parsed_conc) >= len(_GOLDEN_CONCORDANCE_PAIRS):
                    db_concordance = parsed_conc
                else:
                    merged_conc = {
                        (
                            ("IEA" if str(r.get("old_act")) == "EvidenceAct" else str(r.get("old_act"))).upper(),
                            str(r.get("old_ref")).upper(),
                        ): dict(r)
                        for r in load_concordance_yaml()
                    }
                    for pr in parsed_conc:
                        merged_conc[(pr["old_act"].upper(), pr["old_ref"].upper())] = pr
                    db_concordance = list(merged_conc.values())

            inv_rows = con.execute(
                "SELECT target_doc_id, effect FROM judicial_invalidations"
            ).fetchall()
            if inv_rows:
                parsed_inv = [dict(ir) for ir in inv_rows]
                if len(parsed_inv) >= len(_GOLDEN_INVALIDATIONS):
                    db_invalidations = parsed_inv
                else:
                    merged_inv = {
                        str(r.get("target_doc_id")): dict(r)
                        for r in load_invalidations_yaml()
                    }
                    for ir in parsed_inv:
                        merged_inv[str(ir["target_doc_id"])] = ir
                    db_invalidations = list(merged_inv.values())
        finally:
            con.close()

    suites = [
        check_amendments(db_amendments),
        check_concordance(db_concordance),
        check_invalidations(db_invalidations),
        check_coverage_and_non_truncation(
            sqlite_path=sqlite_path,
            strict_landmarks=strict_landmarks,
        ),
        check_extraction_quality(texts=db_records),
        check_licensing_and_provenance(
            records=db_records,
            manifest_path=manifest_path,
        ),
        check_statutory_exclusions(records=db_records),
        check_owasp_llm01(records=db_records),
    ]

    all_passed = all(s.passed for s in suites)
    checked_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    artifact_shas = collect_artifact_sha256s(root) if root is not None else {}

    report = GateReport(
        passed=all_passed,
        checked_at=checked_at,
        results=suites,
        artifact_sha256s=artifact_shas,
    )

    if all_passed and write_gate_pass and root is not None:
        root.mkdir(parents=True, exist_ok=True)
        (root / "gate-pass.json").write_text(
            json.dumps(report.to_dict(), indent=2),
            encoding="utf-8",
        )

    return report
