"""Loaders and record normalizers for CPC First Schedule, Income-tax 1961<->2025
concordance, Appellate Tribunals (NCDRC/NCLAT/DRAT/AFT/GSTAT/RCT/CIC/SEBI_ADJ),
and e-Gazette subordinate legislation records.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from ..extract.normalize import normalize_legal_record
from ..lockfile import _BANNED_URI_SUBSTRINGS, DATA_DIR
from .official_statutes import load_courts_yaml

DEFAULT_CPC_SCHEDULE_PATH = DATA_DIR / "cpc_first_schedule.yaml"
DEFAULT_TAX_CONCORDANCE_PATH = DATA_DIR / "tax_concordance.yaml"

_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _validate_source_url(url: str) -> str:
    cleaned = url.strip()
    lower = cleaned.lower()
    for banned in _BANNED_URI_SUBSTRINGS:
        if banned in lower:
            raise ValueError(
                f"Record source_url {cleaned!r} contains banned upstream {banned!r}."
            )
    return cleaned


def _court_lookup() -> dict[str, dict[str, Any]]:
    lookup: dict[str, dict[str, Any]] = {}
    for entry in load_courts_yaml():
        code = str(entry.get("court_code") or "").strip().upper()
        if code:
            lookup[code] = entry
        for alias in entry.get("aliases") or []:
            a_key = str(alias).strip().upper()
            if a_key and a_key not in lookup:
                lookup[a_key] = entry
    return lookup


def load_cpc_first_schedule_yaml(
    path: Path | str = DEFAULT_CPC_SCHEDULE_PATH,
) -> list[dict[str, Any]]:
    """Load and return structured CPC, 1908 First Schedule Orders & Rules from YAML."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"Invalid cpc_first_schedule YAML structure at {path}")
    items = raw.get("rules") if isinstance(raw.get("rules"), list) else raw.get("provisions")
    if not isinstance(items, list):
        raise ValueError(f"Invalid cpc_first_schedule YAML structure at {path}: missing 'rules' list")
    return [dict(item) for item in items if isinstance(item, dict)]


def load_tax_concordance_yaml(
    path: Path | str = DEFAULT_TAX_CONCORDANCE_PATH,
) -> list[dict[str, Any]]:
    """Load and return curated Income-tax Act, 1961 <-> 2025 section crosswalk mappings."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("mappings"), list):
        raise ValueError(f"Invalid tax_concordance YAML structure at {path}")
    return [dict(item) for item in raw["mappings"] if isinstance(item, dict)]


def _extract_year(raw: Mapping[str, Any], decision_date: str | None) -> int | None:
    if raw.get("year") is not None:
        return int(raw["year"])
    if decision_date:
        m = _YEAR_RE.search(decision_date)
        if m:
            return int(m.group(1))
    return None


def normalize_tribunal_order_record(
    raw: Mapping[str, Any],
    forum_code: str,
) -> dict[str, Any]:
    """Normalize a raw tribunal or regulator adjudication order into a sanitized
    Nyaya record suitable for Parquet compaction and SQLite/Lance indexing.
    """
    raw_forum = forum_code.strip()
    if not raw_forum:
        raise ValueError("forum_code must be a non-empty string.")
    forum_key = raw_forum.upper()

    courts = _court_lookup()
    court_entry = courts.get(forum_key)
    canonical_code = str(court_entry["court_code"]) if court_entry else forum_key
    court_name = (
        str(raw.get("court_name") or "").strip()
        or (str(court_entry["court_name"]) if court_entry else canonical_code)
    )
    tier = str(court_entry.get("tier") if court_entry else raw.get("tier") or "tribunal")

    source_url = _validate_source_url(str(raw.get("source_url") or ""))
    terms_status = str(raw.get("terms_status") or "cleared").strip().lower()
    if terms_status == "red":
        raise ValueError("Tribunal record with terms_status='red' is prohibited.")

    case_id = str(raw.get("case_id") or "").strip()
    if not case_id:
        ref_slug = _SLUG_RE.sub("-", str(raw.get("case_number") or raw.get("title") or "order").lower()).strip("-")
        case_id = f"trib:{canonical_code.lower()}:{ref_slug or 'unknown'}"

    raw_title = str(raw.get("title") or "").strip()
    raw_text = str(raw.get("text") or raw_title).strip()

    title_norm = normalize_legal_record(raw_title or case_id, doc_id=case_id, metadata=raw)
    text_norm = normalize_legal_record(raw_text, doc_id=case_id, metadata=raw)
    blocked = title_norm.blocked_by_publication_ban or text_norm.blocked_by_publication_ban

    decision_date = str(raw.get("decision_date") or "").strip() or None
    year = _extract_year(raw, decision_date)

    default_source_id = (
        "ncdrc_jagriti"
        if canonical_code in {"NCDRC", "SCDRC", "DCDRC"}
        else ("nclat_orders" if canonical_code == "NCLAT" else "vaquill_tribunals")
    )

    return {
        "case_id": case_id,
        "forum_code": canonical_code,
        "court_code": str(raw.get("court_code") or canonical_code),
        "court_name": court_name,
        "tier": tier,
        "bench": str(raw.get("bench") or "").strip() or None,
        "title": title_norm.cleaned_text,
        "petitioner": str(raw.get("petitioner") or "").strip() or None,
        "respondent": str(raw.get("respondent") or "").strip() or None,
        "judge": str(raw.get("judge") or raw.get("coram") or "").strip() or None,
        "decision_date": decision_date,
        "year": year,
        "disposal_nature": str(raw.get("disposal_nature") or "").strip() or None,
        "case_type": str(raw.get("case_type") or "").strip() or None,
        "act": str(raw.get("act") or "").strip() or None,
        "text": text_norm.cleaned_text,
        "text_sha256": text_norm.text_sha256,
        "source_id": str(raw.get("source_id") or default_source_id),
        "source_url": source_url,
        "license_spdx": str(raw.get("license_spdx") or "IN-S52-1-q"),
        "terms_status": terms_status,
        "commercial_safe": bool(raw.get("commercial_safe", True)),
        "blocked_by_publication_ban": blocked,
        "suspected_injection_rules": sorted(
            set(title_norm.suspected_injection_rules + text_norm.suspected_injection_rules)
        ),
    }


def parse_egazette_rule_record(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Parse and sanitize an official Gazette of India (e-Gazette) rule, regulation,
    or statutory notification into a normalized subordinate-legislation record.
    """
    source_url = _validate_source_url(str(raw.get("source_url") or "https://egazette.gov.in"))
    terms_status = str(raw.get("terms_status") or "cleared").strip().lower()
    if terms_status == "red":
        raise ValueError("e-Gazette record with terms_status='red' is prohibited.")

    notif_no = str(raw.get("notification_number") or raw.get("ref") or "").strip()
    if not notif_no:
        raise ValueError("e-Gazette record must include 'notification_number' or 'ref'.")

    act_id = str(raw.get("act_id") or "egazette-central").strip()
    act_short = str(raw.get("act_short") or act_id).strip()
    ref_norm = re.sub(r"\s+", "-", notif_no.upper()).strip("-")
    doc_id = str(raw.get("doc_id") or f"{act_id}:{ref_norm}").strip()

    raw_title = str(raw.get("title") or notif_no).strip()
    raw_text = str(raw.get("text") or raw_title).strip()
    title_norm = normalize_legal_record(raw_title, doc_id=doc_id, metadata=raw)
    text_norm = normalize_legal_record(raw_text, doc_id=doc_id, metadata=raw)

    raw_kind = str(raw.get("kind") or "rule").strip().lower()
    kind = raw_kind if raw_kind in {"rule", "regulation", "circular"} else "rule"

    published_date = (
        str(raw.get("commencement_date") or raw.get("published_date") or "").strip() or None
    )

    return {
        "doc_id": doc_id,
        "act_id": act_id,
        "act_short": act_short,
        "kind": kind,
        "ref": notif_no,
        "ref_norm": ref_norm,
        "title": title_norm.cleaned_text,
        "text": text_norm.cleaned_text,
        "text_sha256": text_norm.text_sha256,
        "ministry": str(raw.get("ministry") or "").strip() or None,
        "gazette_part": str(raw.get("gazette_part") or "").strip() or None,
        "notification_number": notif_no,
        "commencement_date": published_date,
        "status": str(raw.get("status") or "in_force").strip(),
        "source_id": str(raw.get("source_id") or "egazette_rules"),
        "source_url": source_url,
        "license_spdx": str(raw.get("license_spdx") or "IN-S52-1-q"),
        "terms_status": terms_status,
        "commercial_safe": bool(raw.get("commercial_safe", True)),
        "blocked_by_publication_ban": (
            title_norm.blocked_by_publication_ban or text_norm.blocked_by_publication_ban
        ),
    }
