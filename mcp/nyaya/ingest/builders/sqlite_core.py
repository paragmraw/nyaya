"""Staged `nyaya-core.sqlite` builder using `nyaya.storage.sqlite_store.SCHEMA_DDL`."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ...storage.sqlite_store import SCHEMA_DDL
from ..extract.normalize import normalize_legal_record
from ..fetchers.official_statutes import (
    load_amendments_yaml,
    load_concordance_yaml,
    load_courts_yaml,
    load_invalidations_yaml,
)
from ..fetchers.research_packs import sanitize_ddl_district_rows, tag_ildc_research_rows
from ..fetchers.tribunals_regulators import (
    load_cpc_first_schedule_yaml,
    load_tax_concordance_yaml,
)
from ..lockfile import DEFAULT_LOCKFILE_PATH, load_and_validate_lockfile
from ..state import dedupe_and_merge_hc_rows
from .citation_graph import build_citation_edges_for_corpus


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _ref_num(ref: str) -> int:
    m = re.search(r"(\d+)", ref)
    return int(m.group(1)) if m else 0


VAQUILL_CENTRAL_LEGISLATION_URL = "https://oss-data-in.vaquill.ai/v2026.08.1/in_central_legislation.parquet"
_HTML_OR_ERR_RE = re.compile(
    r"^\s*(?:<html\b|<!DOCTYPE\b|\{\s*\"error\"|404\s+Not\s+Found)",
    re.IGNORECASE,
)


def _is_gate_clean(text: str) -> bool:
    if not text or not text.strip():
        return False
    if _HTML_OR_ERR_RE.search(text):
        return False
    printable = sum(1 for ch in text if ch.isprintable() or ch in "\n\r\t")
    return (printable / len(text)) >= 0.90


def _flush_batches(
    con: sqlite3.Connection,
    sql: str,
    rows: list[tuple[Any, ...]],
    batch_size: int = 5000,
) -> None:
    for i in range(0, len(rows), batch_size):
        con.executemany(sql, rows[i : i + batch_size])


@dataclass
class IngestInputBundle:
    """In-memory or staged input dataset bundle for SQLite, LanceDB, and Parquet builders."""

    acts: list[dict[str, Any]] = field(default_factory=list)
    aliases: list[tuple[str, str]] = field(default_factory=list)
    provisions: list[dict[str, Any]] = field(default_factory=list)
    sc_judgments: list[dict[str, Any]] = field(default_factory=list)
    hc_web_rows: list[dict[str, Any]] = field(default_factory=list)
    hc_mobile_rows: list[dict[str, Any]] = field(default_factory=list)
    state_regulator_provisions: list[dict[str, Any]] = field(default_factory=list)
    ddl_district_rows: list[dict[str, Any]] = field(default_factory=list)
    tribunal_rows: list[dict[str, Any]] = field(default_factory=list)
    regulator_order_rows: list[dict[str, Any]] = field(default_factory=list)
    ildc_rows: list[dict[str, Any]] = field(default_factory=list)
    citation_edges: list[dict[str, Any]] = field(default_factory=list)
    hc_row_count: int = 0
    cad_debates: list[dict[str, Any]] = field(default_factory=list)
    cause_lists: list[dict[str, Any]] = field(default_factory=list)


def create_mini_ingest_input() -> IngestInputBundle:
    """Build a rich hermetic miniature input bundle combining real verified YAML data
    with representative statutes, SC/HC judgments, state/regulator provisions, tribunals,
    DDL district rows (with ML religion/caste columns to test stripping), and ILDC rows.
    """
    acts = [
        {
            "act_id": "constitution",
            "short_name": "Constitution",
            "full_name": "The Constitution of India",
            "year": 1950,
            "act_number": "Constitution",
            "jurisdiction": "Central",
            "category": "constitution",
            "status": "in_force",
            "commencement_date": "1950-01-26",
            "repeal_date": None,
            "replaced_by_act": None,
            "storage_tier": "sqlite_core",
            "source_id": "legislative_constitution",
            "commercial_safe": 1,
            "as_of": "2026-11-22",
        },
        {
            "act_id": "bns-2023",
            "short_name": "BNS",
            "full_name": "The Bharatiya Nyaya Sanhita, 2023",
            "year": 2023,
            "act_number": "Act No. 45 of 2023",
            "jurisdiction": "Central",
            "category": "central_act",
            "status": "in_force",
            "commencement_date": "2024-07-01",
            "repeal_date": None,
            "replaced_by_act": None,
            "storage_tier": "sqlite_core",
            "source_id": "prs_2026_delta",
            "commercial_safe": 1,
            "as_of": "2026-11-22",
        },
        {
            "act_id": "ipc-1860",
            "short_name": "IPC",
            "full_name": "The Indian Penal Code, 1860",
            "year": 1860,
            "act_number": "Act No. 45 of 1860",
            "jurisdiction": "Central",
            "category": "central_act",
            "status": "repealed",
            "commencement_date": "1862-01-01",
            "repeal_date": "2024-07-01",
            "replaced_by_act": "bns-2023",
            "storage_tier": "sqlite_core",
            "source_id": "vaquill_central_legislation",
            "commercial_safe": 1,
            "as_of": "2026-11-22",
        },
        {
            "act_id": "bnss-2023",
            "short_name": "BNSS",
            "full_name": "The Bharatiya Nagarik Suraksha Sanhita, 2023",
            "year": 2023,
            "act_number": "Act No. 46 of 2023",
            "jurisdiction": "Central",
            "category": "central_act",
            "status": "in_force",
            "commencement_date": "2024-07-01",
            "repeal_date": None,
            "replaced_by_act": None,
            "storage_tier": "sqlite_core",
            "source_id": "prs_2026_delta",
            "commercial_safe": 1,
            "as_of": "2026-11-22",
        },
        {
            "act_id": "crpc-1973",
            "short_name": "CrPC",
            "full_name": "The Code of Criminal Procedure, 1973",
            "year": 1973,
            "act_number": "Act No. 2 of 1974",
            "jurisdiction": "Central",
            "category": "central_act",
            "status": "repealed",
            "commencement_date": "1974-04-01",
            "repeal_date": "2024-07-01",
            "replaced_by_act": "bnss-2023",
            "storage_tier": "sqlite_core",
            "source_id": "vaquill_central_legislation",
            "commercial_safe": 1,
            "as_of": "2026-11-22",
        },
        {
            "act_id": "bsa-2023",
            "short_name": "BSA",
            "full_name": "The Bharatiya Sakshya Adhiniyam, 2023",
            "year": 2023,
            "act_number": "Act No. 47 of 2023",
            "jurisdiction": "Central",
            "category": "central_act",
            "status": "in_force",
            "commencement_date": "2024-07-01",
            "repeal_date": None,
            "replaced_by_act": None,
            "storage_tier": "sqlite_core",
            "source_id": "prs_2026_delta",
            "commercial_safe": 1,
            "as_of": "2026-11-22",
        },
        {
            "act_id": "iea-1872",
            "short_name": "EvidenceAct",
            "full_name": "The Indian Evidence Act, 1872",
            "year": 1872,
            "act_number": "Act No. 1 of 1872",
            "jurisdiction": "Central",
            "category": "central_act",
            "status": "repealed",
            "commencement_date": "1872-09-01",
            "repeal_date": "2024-07-01",
            "replaced_by_act": "bsa-2023",
            "storage_tier": "sqlite_core",
            "source_id": "vaquill_central_legislation",
            "commercial_safe": 1,
            "as_of": "2026-11-22",
        },
        {
            "act_id": "cpc-1908",
            "short_name": "CPC",
            "full_name": "The Code of Civil Procedure, 1908",
            "year": 1908,
            "act_number": "Act No. 5 of 1908",
            "jurisdiction": "Central",
            "category": "central_act",
            "status": "in_force",
            "commencement_date": "1909-01-01",
            "repeal_date": None,
            "replaced_by_act": None,
            "storage_tier": "sqlite_core",
            "source_id": "vaquill_central_legislation",
            "commercial_safe": 1,
            "as_of": "2026-11-22",
        },
        {
            "act_id": "it-act-2000",
            "short_name": "ITAct",
            "full_name": "The Information Technology Act, 2000",
            "year": 2000,
            "act_number": "Act No. 21 of 2000",
            "jurisdiction": "Central",
            "category": "central_act",
            "status": "in_force",
            "commencement_date": "2000-10-17",
            "repeal_date": None,
            "replaced_by_act": None,
            "storage_tier": "sqlite_core",
            "source_id": "vaquill_central_legislation",
            "commercial_safe": 1,
            "as_of": "2026-11-22",
        },
        {
            "act_id": "aadhaar-2016",
            "short_name": "AadhaarAct",
            "full_name": "The Aadhaar (Targeted Delivery of Financial and Other Subsidies, Benefits and Services) Act, 2016",
            "year": 2016,
            "act_number": "Act No. 18 of 2016",
            "jurisdiction": "Central",
            "category": "central_act",
            "status": "in_force",
            "commencement_date": "2016-03-26",
            "repeal_date": None,
            "replaced_by_act": None,
            "storage_tier": "sqlite_core",
            "source_id": "vaquill_central_legislation",
            "commercial_safe": 1,
            "as_of": "2026-11-22",
        },
        {
            "act_id": "rpa-1951",
            "short_name": "RPA1951",
            "full_name": "The Representation of the People Act, 1951",
            "year": 1951,
            "act_number": "Act No. 43 of 1951",
            "jurisdiction": "Central",
            "category": "central_act",
            "status": "in_force",
            "commencement_date": "1951-07-17",
            "repeal_date": None,
            "replaced_by_act": None,
            "storage_tier": "sqlite_core",
            "source_id": "vaquill_central_legislation",
            "commercial_safe": 1,
            "as_of": "2026-11-22",
        },
        {
            "act_id": "mh-mofa-1963",
            "short_name": "MH-MOFA",
            "full_name": "Maharashtra Ownership Flats Act, 1963",
            "year": 1963,
            "act_number": "Mah. Act 45 of 1963",
            "jurisdiction": "MH",
            "category": "state_act",
            "status": "in_force",
            "commencement_date": "1964-02-10",
            "repeal_date": None,
            "replaced_by_act": None,
            "storage_tier": "lance_bucket",
            "source_id": "vaquill_state_legislation",
            "commercial_safe": 1,
            "as_of": "2026-11-22",
        },
        {
            "act_id": "sebi-lodr-2015",
            "short_name": "SEBI-LODR",
            "full_name": "SEBI (Listing Obligations and Disclosure Requirements) Regulations, 2015",
            "year": 2015,
            "act_number": "SEBI/LAD-NRO/GN/2015-16/013",
            "jurisdiction": "SEBI",
            "category": "regulator",
            "status": "in_force",
            "commencement_date": "2015-12-01",
            "repeal_date": None,
            "replaced_by_act": None,
            "storage_tier": "lance_bucket",
            "source_id": "vaquill_regulators",
            "commercial_safe": 1,
            "as_of": "2026-11-22",
        },
        {
            "act_id": "it-act-2025",
            "short_name": "ITAct2025",
            "full_name": "The Income-tax Act, 2025",
            "year": 2025,
            "act_number": "Act No. 12 of 2025",
            "jurisdiction": "Central",
            "category": "central_act",
            "status": "in_force",
            "commencement_date": "2026-04-01",
            "repeal_date": None,
            "replaced_by_act": None,
            "storage_tier": "sqlite_core",
            "source_id": "indiacode_central_acts",
            "commercial_safe": 1,
            "as_of": "2026-11-26",
        },
        {
            "act_id": "it-act-1961",
            "short_name": "ITAct1961",
            "full_name": "The Income-tax Act, 1961",
            "year": 1961,
            "act_number": "Act No. 43 of 1961",
            "jurisdiction": "Central",
            "category": "central_act",
            "status": "repealed",
            "commencement_date": "1962-04-01",
            "repeal_date": "2026-04-01",
            "replaced_by_act": "it-act-2025",
            "storage_tier": "sqlite_core",
            "source_id": "indiacode_central_acts",
            "commercial_safe": 1,
            "as_of": "2026-11-26",
        },
    ]

    aliases = [
        ("constitution", "constitution"),
        ("constitution of india", "constitution"),
        ("the constitution", "constitution"),
        ("coi", "constitution"),
        ("ipc", "ipc-1860"),
        ("indian penal code", "ipc-1860"),
        ("bns", "bns-2023"),
        ("bharatiya nyaya sanhita", "bns-2023"),
        ("crpc", "crpc-1973"),
        ("code of criminal procedure", "crpc-1973"),
        ("bnss", "bnss-2023"),
        ("bharatiya nagarik suraksha sanhita", "bnss-2023"),
        ("iea", "iea-1872"),
        ("evidence act", "iea-1872"),
        ("indian evidence act", "iea-1872"),
        ("evidenceact", "iea-1872"),
        ("bsa", "bsa-2023"),
        ("bharatiya sakshya adhiniyam", "bsa-2023"),
        ("cpc", "cpc-1908"),
        ("code of civil procedure", "cpc-1908"),
        ("it act", "it-act-2000"),
        ("itact", "it-act-2000"),
        ("it_2000", "it-act-2000"),
        ("information technology act", "it-act-2000"),
        ("aadhaar", "aadhaar-2016"),
        ("aadhaar act", "aadhaar-2016"),
        ("rpa", "rpa-1951"),
        ("rpa1951", "rpa-1951"),
        ("mh-mofa", "mh-mofa-1963"),
        ("mofa", "mh-mofa-1963"),
        ("sebi lodr", "sebi-lodr-2015"),
        ("sebi-lodr", "sebi-lodr-2015"),
        ("itact1961", "it-act-1961"),
        ("it act 1961", "it-act-1961"),
        ("incometax1961", "it-act-1961"),
        ("income tax act 1961", "it-act-1961"),
        ("income-tax act 1961", "it-act-1961"),
        ("itact2025", "it-act-2025"),
        ("it act 2025", "it-act-2025"),
        ("incometax2025", "it-act-2025"),
        ("income tax act 2025", "it-act-2025"),
        ("income-tax act 2025", "it-act-2025"),
    ]

    provisions = [
        {
            "doc_id": "constitution:art:14",
            "act_id": "constitution",
            "act_short": "Constitution",
            "kind": "article",
            "ref": "14",
            "ref_norm": "14",
            "part": "Part III - Fundamental Rights",
            "title": "Equality before law",
            "text": "The State shall not deny to any person equality before the law or the equal protection of the laws within the territory of India. Equality is part of the basic structure of the Constitution.",
            "status": "in_force",
            "commencement_date": "1950-01-26",
            "source_id": "legislative_constitution",
            "source_url": "https://legislative.gov.in/sites/default/files/COI_2026.pdf",
        },
        {
            "doc_id": "constitution:art:19",
            "act_id": "constitution",
            "act_short": "Constitution",
            "kind": "article",
            "ref": "19",
            "ref_norm": "19",
            "part": "Part III - Fundamental Rights",
            "title": "Protection of certain rights regarding freedom of speech, etc.",
            "text": "All citizens shall have the right to freedom of speech and expression, to assemble peaceably, and to form associations, unions, or co-operative societies.",
            "status": "in_force",
            "commencement_date": "1950-01-26",
            "source_id": "legislative_constitution",
            "source_url": "https://legislative.gov.in/sites/default/files/COI_2026.pdf",
        },
        {
            "doc_id": "constitution:art:21",
            "act_id": "constitution",
            "act_short": "Constitution",
            "kind": "article",
            "ref": "21",
            "ref_norm": "21",
            "part": "Part III - Fundamental Rights",
            "title": "Protection of life and personal liberty",
            "text": "No person shall be deprived of his life or personal liberty except according to procedure established by law.",
            "status": "in_force",
            "commencement_date": "1950-01-26",
            "source_id": "legislative_constitution",
            "source_url": "https://legislative.gov.in/sites/default/files/COI_2026.pdf",
        },
        {
            "doc_id": "constitution:art:21A",
            "act_id": "constitution",
            "act_short": "Constitution",
            "kind": "article",
            "ref": "21A",
            "ref_norm": "21A",
            "part": "Part III - Fundamental Rights",
            "title": "Right to education",
            "text": "The State shall provide free and compulsory education to all children of the age of six to fourteen years in such manner as the State may, by law, determine.",
            "status": "in_force",
            "commencement_date": "2010-04-01",
            "source_id": "legislative_constitution",
            "source_url": "https://legislative.gov.in/sites/default/files/COI_2026.pdf",
        },
        {
            "doc_id": "constitution:art:32",
            "act_id": "constitution",
            "act_short": "Constitution",
            "kind": "article",
            "ref": "32",
            "ref_norm": "32",
            "part": "Part III - Fundamental Rights",
            "title": "Remedies for enforcement of rights conferred by this Part",
            "text": "The right to move the Supreme Court by appropriate proceedings for the enforcement of the rights conferred by this Part is guaranteed.",
            "status": "in_force",
            "commencement_date": "1950-01-26",
            "source_id": "legislative_constitution",
            "source_url": "https://legislative.gov.in/sites/default/files/COI_2026.pdf",
        },
        {
            "doc_id": "constitution:art:368",
            "act_id": "constitution",
            "act_short": "Constitution",
            "kind": "article",
            "ref": "368",
            "ref_norm": "368",
            "part": "Part XX - Amendment of the Constitution",
            "title": "Power of Parliament to amend the Constitution and procedure therefor",
            "text": "Notwithstanding anything in this Constitution, Parliament may in exercise of its constituent power amend by way of addition, variation or repeal any provision of this Constitution in accordance with the procedure laid down in this article.",
            "status": "in_force",
            "commencement_date": "1950-01-26",
            "source_id": "legislative_constitution",
            "source_url": "https://legislative.gov.in/sites/default/files/COI_2026.pdf",
        },
        {
            "doc_id": "constitution:sch:7",
            "act_id": "constitution",
            "act_short": "Constitution",
            "kind": "schedule",
            "ref": "Schedule 7",
            "ref_norm": "SCH7",
            "part": "Schedules",
            "title": "Seventh Schedule - Union, State and Concurrent Lists",
            "text": "List I - Union List (Entry 82 Taxes on income other than agricultural income); List II - State List; List III - Concurrent List.",
            "status": "in_force",
            "commencement_date": "1950-01-26",
            "source_id": "legislative_constitution",
            "source_url": "https://legislative.gov.in/sites/default/files/COI_2026.pdf",
        },
        {
            "doc_id": "cpc-1908:O7-R11",
            "act_id": "cpc-1908",
            "act_short": "CPC",
            "kind": "order_rule",
            "ref": "Order 7 Rule 11",
            "ref_norm": "O7-R11",
            "chapter_num": "First Schedule",
            "chapter_title": "Orders and Rules",
            "title": "Rejection of plaint",
            "text": "The plaint shall be rejected in the following cases: (a) where it does not disclose a cause of action; (d) where the suit appears from the statement in the plaint to be barred by any law.",
            "status": "in_force",
            "commencement_date": "1909-01-01",
            "source_id": "vaquill_central_legislation",
            "source_url": VAQUILL_CENTRAL_LEGISLATION_URL,
        },
        {
            "doc_id": "bnss-2023:531",
            "act_id": "bnss-2023",
            "act_short": "BNSS",
            "kind": "section",
            "ref": "531",
            "ref_norm": "531",
            "chapter_num": "XXXIX",
            "chapter_title": "Repeal and Savings",
            "title": "Repeal and savings",
            "text": "The Code of Criminal Procedure, 1973 is hereby repealed; provided that pending appeals, applications, trials, inquiries or investigations prior to 1 July 2024 shall continue under the repealed Code.",
            "status": "in_force",
            "commencement_date": "2024-07-01",
            "source_id": "prs_2026_delta",
            "source_url": "https://prsindia.org/files/bills_acts/acts_parliament/2026/prs_2026_delta.json",
        },
    ]

    sc_judgments = [
        {
            "case_id": "sc:1973:kesavananda",
            "court_code": "SCI",
            "court_name": "Supreme Court of India",
            "bench": "Full Court",
            "neutral_citation": None,
            "air_citation": "AIR 1973 SC 1461",
            "scc_citation": "(1973) 4 SCC 225",
            "scr_citation": "[1973] Supp SCR 1",
            "title": "Kesavananda Bharati v. State of Kerala",
            "petitioner": "Kesavananda Bharati",
            "respondent": "State of Kerala",
            "decision_date": "1973-04-24",
            "year": 1973,
            "judge_coram": "S.M. Sikri; H.R. Khanna; A.N. Ray",
            "author_judge": "S.M. Sikri",
            "bench_strength": 13,
            "disposal_nature": "Disposed of",
            "case_type": "Writ Petition (Civil)",
            "acts_cited": ["Constitution"],
            "is_reportable": 1,
            "pdf_s3_bucket": "indian-supreme-court-judgments",
            "pdf_s3_key": "data/pdf/year=1973/english/kesavananda_bharati.pdf",
            "source_url": "https://indian-supreme-court-judgments.s3.ap-south-1.amazonaws.com/data/pdf/year=1973/english/kesavananda_bharati.pdf",
            "text": "1. Parliament cannot alter the basic structure or framework of the Constitution under Article 368. Fundamental rights under Articles 14, 19 and 21 form part of the basic structure.",
            "cited_by_count": 1450,
            "source_id": "dattam_sc_judgments",
            "commercial_safe": True,
        },
        {
            "case_id": "sc:2017:puttaswamy",
            "court_code": "SCI",
            "court_name": "Supreme Court of India",
            "bench": "Constitution Bench",
            "neutral_citation": "2017 INSC 801",
            "air_citation": None,
            "scc_citation": "(2017) 10 SCC 1",
            "scr_citation": None,
            "title": "Justice K.S. Puttaswamy (Retd.) v. Union of India",
            "petitioner": "Justice K.S. Puttaswamy",
            "respondent": "Union of India",
            "decision_date": "2017-08-24",
            "year": 2017,
            "judge_coram": "J.S. Khehar; D.Y. Chandrachud; S.K. Kaul",
            "author_judge": "D.Y. Chandrachud",
            "bench_strength": 9,
            "disposal_nature": "Allowed",
            "case_type": "Writ Petition (Civil)",
            "acts_cited": ["Constitution", "AadhaarAct"],
            "is_reportable": 1,
            "pdf_s3_bucket": "indian-supreme-court-judgments",
            "pdf_s3_key": "data/pdf/year=2017/english/puttaswamy_2017.pdf",
            "source_url": "https://indian-supreme-court-judgments.s3.ap-south-1.amazonaws.com/data/pdf/year=2017/english/puttaswamy_2017.pdf",
            "text": "1. The right to privacy is protected as an intrinsic part of the right to life and personal liberty under Article 21 and as a part of the freedoms guaranteed by Part III of the Constitution, affirming AIR 1973 SC 1461.",
            "cited_by_count": 820,
            "source_id": "dattam_sc_judgments",
            "commercial_safe": True,
        },
        {
            "case_id": "sc:2015:shreya-singhal",
            "court_code": "SCI",
            "court_name": "Supreme Court of India",
            "bench": "Division Bench",
            "neutral_citation": "2015 INSC 254",
            "air_citation": "AIR 2015 SC 1523",
            "scc_citation": "(2015) 5 SCC 1",
            "scr_citation": None,
            "title": "Shreya Singhal v. Union of India",
            "petitioner": "Shreya Singhal",
            "respondent": "Union of India",
            "decision_date": "2015-03-24",
            "year": 2015,
            "judge_coram": "J. Chelameswar; R.F. Nariman",
            "author_judge": "R.F. Nariman",
            "bench_strength": 2,
            "disposal_nature": "Allowed",
            "case_type": "Writ Petition (Criminal)",
            "acts_cited": ["ITAct", "Constitution"],
            "is_reportable": 1,
            "pdf_s3_bucket": "indian-supreme-court-judgments",
            "pdf_s3_key": "data/pdf/year=2015/english/shreya_singhal.pdf",
            "source_url": "https://indian-supreme-court-judgments.s3.ap-south-1.amazonaws.com/data/pdf/year=2015/english/shreya_singhal.pdf",
            "text": "1. Section 66A of the Information Technology Act 2000 is struck down in its entirety as being violative of Article 19(1)(a) and not saved under Article 19(2).",
            "cited_by_count": 610,
            "source_id": "dattam_sc_judgments",
            "commercial_safe": True,
        },
    ]

    hc_web_rows = [
        {
            "case_id": "hc:2024:dhc:1234",
            "court_code": "7_26",
            "court_name": "High Court of Delhi",
            "bench": "Principal",
            "cnr": "DLHC010123452024",
            "neutral_citation": "2024:DHC:1234",
            "title": "Arjun Mehta v. State NCT of Delhi",
            "petitioner": "Arjun Mehta",
            "respondent": "State NCT of Delhi",
            "judge": "Vibhu Bakhru",
            "author_judge": "Vibhu Bakhru",
            "bench_strength": 2,
            "decision_date": "2024-05-14",
            "year": 2024,
            "order_number": 1,
            "disposal_nature": "",
            "case_type": "CRL.M.C.",
            "act": "CrPC",
            "acts_cited": ["CrPC", "BNSS", "IPC"],
            "is_reportable": True,
            "pdf_s3_bucket": "indian-high-court-judgments",
            "pdf_s3_key": "data/pdf/year=2024/court=7_26/bench=principal/dhc_1234.pdf",
            "source_url": "https://indian-high-court-judgments.s3.ap-south-1.amazonaws.com/data/pdf/year=2024/court=7_26/bench=principal/dhc_1234.pdf",
            "text": "1. Anticipatory bail application under Section 438 CrPC and BNSS Section 482 granted considering personal liberty safeguards in (2017) 10 SCC 1.",
            "source_id": "dattam_hc_judgments",
            "commercial_safe": True,
        },
        {
            "case_id": "hc:2023:dhc:888",
            "court_code": "7_26",
            "court_name": "High Court of Delhi",
            "bench": "Principal",
            "cnr": "DLHC010008882023",
            "neutral_citation": "2023:DHC:888",
            "title": "Priya Sharma v. Union of India",
            "petitioner": "Priya Sharma",
            "respondent": "Union of India",
            "judge": "Prathiba M. Singh",
            "author_judge": "Prathiba M. Singh",
            "bench_strength": 1,
            "decision_date": "2023-11-20",
            "year": 2023,
            "order_number": 1,
            "disposal_nature": "Dismissed",
            "case_type": "W.P.(C)",
            "act": "ITAct",
            "acts_cited": ["ITAct"],
            "is_reportable": False,
            "pdf_s3_bucket": "indian-high-court-judgments",
            "pdf_s3_key": "data/pdf/year=2023/court=7_26/bench=principal/dhc_888.pdf",
            "source_url": "https://indian-high-court-judgments.s3.ap-south-1.amazonaws.com/data/pdf/year=2023/court=7_26/bench=principal/dhc_888.pdf",
            "text": "1. Writ petition concerning intermediary guidelines under the Information Technology Act 2000.",
            "source_id": "dattam_hc_judgments",
            "commercial_safe": True,
        },
        {
            "case_id": "hc:2024:bhc:567",
            "court_code": "27_1",
            "court_name": "High Court of Bombay",
            "bench": "Original Side",
            "cnr": "MHAU019999992015",
            "neutral_citation": "2024:BHC-OS:567",
            "title": "Rohan Infra Pvt Ltd v. Maharashtra Housing Board",
            "petitioner": "Rohan Infra Pvt Ltd",
            "respondent": "Maharashtra Housing Board",
            "judge": "B.P. Colabawalla",
            "author_judge": "B.P. Colabawalla",
            "bench_strength": 1,
            "decision_date": "2024-03-10",
            "year": 2024,
            "order_number": 1,
            "disposal_nature": "Disposed of",
            "case_type": "W.P.(L)",
            "act": "CPC",
            "acts_cited": ["CPC", "MH-MOFA"],
            "is_reportable": True,
            "pdf_s3_bucket": "indian-high-court-judgments",
            "pdf_s3_key": "data/pdf/year=2024/court=27_1/bench=original/bhc_567.pdf",
            "source_url": "https://indian-high-court-judgments.s3.ap-south-1.amazonaws.com/data/pdf/year=2024/court=27_1/bench=original/bhc_567.pdf",
            "text": "1. Plaint rejection application under Order VII Rule 11(d) CPC in Maharashtra Ownership Flats Act dispute.",
            "source_id": "dattam_hc_judgments",
            "commercial_safe": True,
        },
    ]

    hc_mobile_rows = [
        {
            "cnr": "DLHC010123452024",
            "filing_date": "2024-01-10",
            "registration_date": "2024-01-12",
            "disposal_nature": "Allowed",
            "petitioner_advocates": ["Adv. R.K. Sharma"],
            "respondent_advocates": ["APP for State"],
            "acts_sections": ["CrPC s.438", "BNSS s.482"],
            "hearing_count": 4,
        },
        {
            "cnr": "MHAU019999992015",
            "filing_date": "2023-12-01",
            "registration_date": "2023-12-05",
            "disposal_nature": "Disposed of",
            "petitioner_advocates": ["Adv. S. Deshmukh"],
            "respondent_advocates": ["Adv. M. Kulkarni"],
            "acts_sections": ["CPC O.7 R.11", "MH-MOFA s.4"],
            "hearing_count": 6,
        },
    ]

    state_reg_rows = [
        {
            "doc_id": "mh-mofa-1963:4",
            "act_id": "mh-mofa-1963",
            "short_name": "MH-MOFA",
            "jurisdiction": "MH",
            "category": "state_act",
            "kind": "section",
            "ref": "4",
            "title": "Promoter before accepting advance payment to enter into agreement for sale",
            "status": "in_force",
            "effective_date": "1964-02-10",
            "text": "Notwithstanding anything contained in any other law, a promoter who intends to construct flats shall execute a registered agreement for sale before accepting advance payment.",
            "commercial_safe": True,
            "terms_status": "attribution_required",
            "license_spdx": "CC-BY-4.0",
            "source_id": "vaquill_state_legislation",
            "source_url": "https://oss-data-in.vaquill.ai/v2026.08.1/in_state_legislation.parquet",
        },
        {
            "doc_id": "sebi-lodr-2015:30",
            "act_id": "sebi-lodr-2015",
            "short_name": "SEBI-LODR",
            "jurisdiction": "SEBI",
            "category": "regulator",
            "kind": "regulation",
            "ref": "30",
            "title": "Disclosure of events or information by listed entity",
            "status": "in_force",
            "effective_date": "2015-12-01",
            "text": "Every listed entity shall make disclosures of any events or information which, in the opinion of the board of directors of the listed company, is material under Regulation 30.",
            "commercial_safe": True,
            "terms_status": "attribution_required",
            "license_spdx": "CC-BY-4.0",
            "source_id": "vaquill_regulators",
            "source_url": "https://oss-data-in.vaquill.ai/v2026.08.1/in_regulators.parquet",
        },
    ]

    # Raw DDL District rows intentionally include ML religion/caste columns to test stripping
    ddl_district_rows = [
        {
            "case_id": "ddl:mh:2015:1",
            "cnr": "MHAU020000012015",
            "state_code": "MH",
            "court_code": "MH_DIST_01",
            "court_name": "District and Sessions Court, Pune",
            "district": "Pune",
            "title": "State of Maharashtra v. Accused A",
            "petitioner": "State of Maharashtra",
            "respondent": "Accused A",
            "judge": "District Judge 1",
            "decision_date": "2015-08-12",
            "year": 2015,
            "disposal_nature": "Acquitted",
            "case_type": "Sessions Case",
            "act": "IPC",
            "predicted_religion": "SHOULD_BE_STRIPPED",
            "predicted_caste": "SHOULD_BE_STRIPPED",
            "judge_predicted_religion": "SHOULD_BE_STRIPPED",
        },
        {
            "case_id": "ddl:mh:2016:2",
            "cnr": "MHAU020000022016",
            "state_code": "MH",
            "court_code": "MH_DIST_01",
            "court_name": "District and Sessions Court, Pune",
            "district": "Pune",
            "title": "Ramesh Patil v. Suresh Kulkarni",
            "petitioner": "Ramesh Patil",
            "respondent": "Suresh Kulkarni",
            "judge": "Civil Judge Sr. Div.",
            "decision_date": "2016-04-05",
            "year": 2016,
            "disposal_nature": "Decreed",
            "case_type": "Regular Civil Suit",
            "act": "CPC",
            "predicted_religion": "SHOULD_BE_STRIPPED",
            "predicted_caste": "SHOULD_BE_STRIPPED",
        },
    ]

    tribunal_rows = [
        {
            "case_id": "trib:nclat:2024:10",
            "forum_code": "NCLAT",
            "court_code": "NCLAT",
            "court_name": "National Company Law Appellate Tribunal",
            "title": "Apex Creditors v. Resolution Professional",
            "judge": "Justice Ashok Bhushan",
            "decision_date": "2024-02-18",
            "year": 2024,
            "disposal_nature": "Allowed",
            "case_type": "Company Appeal (AT) (Insolvency)",
            "act": "IBC",
            "commercial_safe": True,
        }
    ]

    regulator_order_rows = [
        {
            "case_id": "reg:sebi:2024:1",
            "regulator_code": "SEBI",
            "court_code": "SEBI",
            "court_name": "Securities and Exchange Board of India",
            "title": "Adjudication Order in the matter of XYZ Listed Ltd",
            "decision_date": "2024-06-01",
            "year": 2024,
            "disposal_nature": "Penalty Imposed",
            "case_type": "Adjudication Order",
            "act": "SEBI-LODR",
            "commercial_safe": True,
        }
    ]

    ildc_rows = tag_ildc_research_rows(
        [
            {
                "case_id": "ildc:2020:001",
                "court_code": "SCI",
                "court_name": "Supreme Court of India",
                "bench": "Division Bench",
                "neutral_citation": "2020 INSC 99",
                "title": "State of Maharashtra v. Sample ILDC Appellant",
                "petitioner": "State of Maharashtra",
                "respondent": "Sample ILDC Appellant",
                "decision_date": "2020-02-01",
                "year": 2020,
                "judge_coram": "N.V. Ramana; Sanjiv Khanna",
                "author_judge": "Sanjiv Khanna",
                "bench_strength": 2,
                "disposal_nature": "Dismissed",
                "case_type": "Criminal Appeal",
                "acts_cited": ["IPC"],
                "source_url": "https://huggingface.co/datasets/Exploration-Lab/ILDC",
                "text": "1. ILDC non-commercial academic explanation sample for criminal appeal sentencing and murder conviction under IPC section 302.",
            }
        ]
    )

    edges = [
        {
            "from_id": "sc:2017:puttaswamy",
            "from_kind": "judgment",
            "to_id": "sc:1973:kesavananda",
            "to_kind": "judgment",
            "relation": "cites",
            "para_ref": "para 142",
            "quoting_excerpt": "Basic structure doctrine affirmed in Kesavananda Bharati protects fundamental rights.",
            "from_year": 2017,
            "from_bench_str": 9,
            "precedent_status": "verified_curated",
        },
        {
            "from_id": "sc:2015:shreya-singhal",
            "from_kind": "judgment",
            "to_id": "it-act-2000:66A",
            "to_kind": "provision",
            "relation": "invalidates",
            "para_ref": "para 119",
            "quoting_excerpt": "Section 66A of the Information Technology Act is struck down in its entirety.",
            "from_year": 2015,
            "from_bench_str": 2,
            "precedent_status": "verified_curated",
        },
        {
            "from_id": "sc:2015:shreya-singhal",
            "from_kind": "judgment",
            "to_id": "constitution:art:19",
            "to_kind": "provision",
            "relation": "interprets",
            "para_ref": "para 15",
            "quoting_excerpt": "Discussion, advocacy and incitement under Article 19(1)(a) and Article 19(2).",
            "from_year": 2015,
            "from_bench_str": 2,
            "precedent_status": "verified_curated",
        },
        {
            "from_id": "hc:2024:dhc:1234",
            "from_kind": "judgment",
            "to_id": "sc:2017:puttaswamy",
            "to_kind": "judgment",
            "relation": "cites",
            "para_ref": "para 1",
            "quoting_excerpt": "Personal liberty safeguards under Article 21 discussed.",
            "from_year": 2024,
            "from_bench_str": 2,
            "precedent_status": "unverified_automated",
        },
    ]

    cad_debates = [
        {
            "chunk_id": "cad:vol7:art21:ambedkar",
            "debate_id": "cad-vol7-1948-12-06",
            "volume": "Vol VII",
            "sitting_date": "1948-12-06",
            "year": 1948,
            "speaker": "Dr. B.R. Ambedkar",
            "article_ref": "Article 21",
            "title": "Constituent Assembly Debate on Article 21 (Procedure Established by Law)",
            "text": (
                "The question of substance in Draft Article 15 (Article 21 of the Constitution of India) "
                "is whether personal liberty and due process require judicial scrutiny against arbitrary "
                "executive detention and deprivation without procedure established by law."
            ),
            "commercial_safe": True,
            "terms_status": "cleared",
            "license_spdx": "IN-S52-1-q",
            "source_id": "legislative_constitution",
            "source_url": "https://constitutionofindia.net/debates/06-dec-1948/",
        },
        {
            "chunk_id": "cad:vol7:art32:ambedkar",
            "debate_id": "cad-vol7-1948-12-09",
            "volume": "Vol VII",
            "sitting_date": "1948-12-09",
            "year": 1948,
            "speaker": "Dr. B.R. Ambedkar",
            "article_ref": "Article 32",
            "title": "Constituent Assembly Debate on Article 32 (Heart and Soul of the Constitution)",
            "text": (
                "If I was asked to name any particular article in this Constitution as the most "
                "important—an article without which this Constitution would be a nullity—I could not "
                "refer to any other article except this one, Draft Article 25 (Article 32). It is the "
                "very soul of the Constitution and the very heart of it."
            ),
            "commercial_safe": True,
            "terms_status": "cleared",
            "license_spdx": "IN-S52-1-q",
            "source_id": "legislative_constitution",
            "source_url": "https://constitutionofindia.net/debates/09-dec-1948/",
        },
    ]

    cause_lists = [
        {
            "court": "Supreme Court of India",
            "cause_list_date": "2026-11-26",
            "bench": "Court No. 1 (Chief Justice's Court)",
            "judge": "Hon'ble Chief Justice of India",
            "item_number": "1",
            "case_number": "W.P.(C) No. 1012/2026",
            "cnr": "SCIN010010122026",
            "parties": "Union of India v. Bar Council Petitioners",
            "advocates": ["Attorney General for India", "Sr. Adv. A. Counsel"],
            "purpose": "Final Hearing",
            "source_url": "https://www.sci.gov.in/daily-cause-list",
        },
        {
            "court": "High Court of Delhi",
            "cause_list_date": "2026-11-26",
            "bench": "DB-I",
            "judge": "Hon'ble Chief Justice & Bench",
            "item_number": "12",
            "case_number": "W.P.(C) 4421/2026",
            "cnr": "DLHC010044212026",
            "parties": "Rohan Sharma v. State of NCT of Delhi",
            "advocates": ["Adv. Meera Krishnan"],
            "purpose": "Anticipatory Bail / Urgent Mentioning",
            "source_url": "https://delhihighcourt.nic.in/causelist",
        },
    ]

    return IngestInputBundle(
        acts=acts,
        aliases=aliases,
        provisions=provisions,
        sc_judgments=sc_judgments,
        hc_web_rows=hc_web_rows,
        hc_mobile_rows=hc_mobile_rows,
        state_regulator_provisions=state_reg_rows,
        ddl_district_rows=sanitize_ddl_district_rows(ddl_district_rows),
        tribunal_rows=tribunal_rows,
        regulator_order_rows=regulator_order_rows,
        ildc_rows=ildc_rows,
        citation_edges=edges,
        cad_debates=cad_debates,
        cause_lists=cause_lists,
    )


def build_sqlite_core(
    out_path: Path | str,
    *,
    input_data: IngestInputBundle | None = None,
    lockfile_path: Path | str = DEFAULT_LOCKFILE_PATH,
    amendments_path: Path | str | None = None,
    concordance_path: Path | str | None = None,
    invalidations_path: Path | str | None = None,
    courts_path: Path | str | None = None,
    cpc_schedule_path: Path | str | None = None,
    tax_concordance_path: Path | str | None = None,
    commercial_safe_only: bool = False,
    include_hc_in_sqlite: bool | None = None,
) -> Path:
    """Build staged `nyaya-core.sqlite`, populate all tables + FTS5 indexes,
    run integrity checks + VACUUM + ANALYZE, and emit `.sha256`.
    """
    bundle = input_data or create_mini_ingest_input()
    lock = load_and_validate_lockfile(lockfile_path)
    amendments = (
        load_amendments_yaml(amendments_path)
        if amendments_path is not None
        else load_amendments_yaml()
    )
    concordance_list = (
        load_concordance_yaml(concordance_path)
        if concordance_path is not None
        else load_concordance_yaml()
    )
    tax_concordance_list = (
        load_tax_concordance_yaml(tax_concordance_path)
        if tax_concordance_path is not None
        else load_tax_concordance_yaml()
    )
    cpc_schedule_list = (
        load_cpc_first_schedule_yaml(cpc_schedule_path)
        if cpc_schedule_path is not None
        else load_cpc_first_schedule_yaml()
    )
    invalidations_list = (
        load_invalidations_yaml(invalidations_path)
        if invalidations_path is not None
        else load_invalidations_yaml()
    )
    courts_list = (
        load_courts_yaml(courts_path)
        if courts_path is not None
        else load_courts_yaml()
    )

    final_path = Path(out_path)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    staging_path = final_path.with_suffix(final_path.suffix + ".staging")
    if staging_path.exists():
        staging_path.unlink()

    con = sqlite3.connect(staging_path)
    try:
        con.executescript(SCHEMA_DDL)
        con.execute("PRAGMA synchronous = OFF")
        con.execute("PRAGMA temp_store = MEMORY")
        con.execute("PRAGMA cache_size = -65536")

        # 1. Populate sources from lockfile
        source_rows = [
            (
                src.id,
                src.name,
                src.uri,
                src.license_spdx,
                1 if src.commercial_safe else 0,
                src.terms_status,
                src.release_tag or lock.updated_at,
                lock.updated_at,
                src.sha256 or _sha256_text(src.uri),
                0,
                src.attribution,
            )
            for src in lock.sources
            if not (commercial_safe_only and not src.commercial_safe)
        ]
        _flush_batches(con, "INSERT OR REPLACE INTO sources VALUES (?,?,?,?,?,?,?,?,?,?,?)", source_rows)

        # 2. Populate acts & aliases (ensure CPC & Income-tax acts exist if referenced by schedule/concordance)
        effective_acts = list(bundle.acts)
        present_act_ids = {str(a["act_id"]) for a in effective_acts}
        for default_act in (
            {
                "act_id": "cpc-1908",
                "short_name": "CPC",
                "full_name": "The Code of Civil Procedure, 1908",
                "year": 1908,
                "act_number": "Act No. 5 of 1908",
                "jurisdiction": "Central",
                "category": "central_act",
                "status": "in_force",
                "commencement_date": "1909-01-01",
                "repeal_date": None,
                "replaced_by_act": None,
                "storage_tier": "sqlite_core",
                "source_id": "indiacode_central_acts",
                "commercial_safe": 1,
                "as_of": lock.updated_at,
            },
            {
                "act_id": "it-act-2025",
                "short_name": "ITAct2025",
                "full_name": "The Income-tax Act, 2025",
                "year": 2025,
                "act_number": "Act No. 12 of 2025",
                "jurisdiction": "Central",
                "category": "central_act",
                "status": "in_force",
                "commencement_date": "2026-04-01",
                "repeal_date": None,
                "replaced_by_act": None,
                "storage_tier": "sqlite_core",
                "source_id": "indiacode_central_acts",
                "commercial_safe": 1,
                "as_of": lock.updated_at,
            },
            {
                "act_id": "it-act-1961",
                "short_name": "ITAct1961",
                "full_name": "The Income-tax Act, 1961",
                "year": 1961,
                "act_number": "Act No. 43 of 1961",
                "jurisdiction": "Central",
                "category": "central_act",
                "status": "repealed",
                "commencement_date": "1962-04-01",
                "repeal_date": "2026-04-01",
                "replaced_by_act": "it-act-2025",
                "storage_tier": "sqlite_core",
                "source_id": "indiacode_central_acts",
                "commercial_safe": 1,
                "as_of": lock.updated_at,
            },
        ):
            if default_act["act_id"] not in present_act_ids:
                effective_acts.append(default_act)
                present_act_ids.add(str(default_act["act_id"]))

        act_short_by_id: dict[str, str] = {}
        seen_short_names: set[str] = set()
        act_rows: list[tuple[Any, ...]] = []
        for act in effective_acts:
            if commercial_safe_only and not bool(act.get("commercial_safe", 1)):
                continue
            act_id = str(act["act_id"])
            if act_id in act_short_by_id:
                continue
            short_name = str(act["short_name"])
            if short_name.lower() in seen_short_names:
                short_name = f"{short_name[:72]} [{act_id}]"
            seen_short_names.add(short_name.lower())
            act_short_by_id[act_id] = short_name
            act_rows.append(
                (
                    act_id,
                    short_name,
                    str(act["full_name"]),
                    act.get("year"),
                    act.get("act_number"),
                    str(act.get("jurisdiction") or "Central"),
                    str(act.get("category") or "central_act"),
                    str(act.get("status") or "in_force"),
                    act.get("commencement_date"),
                    act.get("repeal_date"),
                    act.get("replaced_by_act"),
                    str(act.get("storage_tier") or "sqlite_core"),
                    str(act.get("source_id") or "vaquill_central_legislation"),
                    1 if act.get("commercial_safe", 1) else 0,
                    str(act.get("as_of") or lock.updated_at),
                )
            )
        _flush_batches(con, "INSERT OR REPLACE INTO acts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", act_rows)
        act_commencement_by_id: dict[str, Any] = {a[0]: a[8] for a in act_rows}
        act_repeal_by_id: dict[str, Any] = {a[0]: a[9] for a in act_rows}

        effective_aliases = list(bundle.aliases)
        for fallback_alias in (
            ("cpc", "cpc-1908"),
            ("itact1961", "it-act-1961"),
            ("it act 1961", "it-act-1961"),
            ("incometax1961", "it-act-1961"),
            ("income tax act 1961", "it-act-1961"),
            ("itact2025", "it-act-2025"),
            ("it act 2025", "it-act-2025"),
            ("incometax2025", "it-act-2025"),
            ("income tax act 2025", "it-act-2025"),
        ):
            if fallback_alias not in effective_aliases:
                effective_aliases.append(fallback_alias)

        alias_rows = [
            (alias_lower.strip().lower(), target_act_id)
            for alias_lower, target_act_id in effective_aliases
            if target_act_id in act_short_by_id and alias_lower.strip()
        ]
        _flush_batches(con, "INSERT OR REPLACE INTO act_aliases VALUES (?,?)", alias_rows)

        # 3. Populate provisions (from bundle + auto-materialized provisions required by concordance/invalidations)
        inserted_provisions: set[str] = set()
        inserted_act_kind_ref: set[tuple[str, str, str]] = set()
        inval_status_by_doc: dict[str, str] = {
            str(inv["target_doc_id"]): str(inv["effect"]) for inv in invalidations_list
        }
        prov_rows: list[tuple[Any, ...]] = []
        prov_by_doc_id: dict[str, dict[str, Any]] = {}

        def _queue_provision(p: dict[str, Any]) -> None:
            doc_id = str(p["doc_id"])
            if doc_id in inserted_provisions:
                return
            act_id = str(p["act_id"])
            if act_id not in act_short_by_id:
                return
            kind = str(p.get("kind") or "section")
            ref = str(p["ref"])
            ref_norm = str(p.get("ref_norm") or ref).upper()
            akr = (act_id, kind, ref_norm)
            if akr in inserted_act_kind_ref:
                return

            norm = normalize_legal_record(str(p.get("text") or ""), doc_id=doc_id, metadata=p)
            if norm.blocked_by_publication_ban or not _is_gate_clean(norm.cleaned_text):
                return

            act_short = str(p.get("act_short") or act_short_by_id.get(act_id, act_id))
            default_status = "repealed" if act_id in ("ipc-1860", "crpc-1973", "iea-1872") else "in_force"
            status = inval_status_by_doc.get(doc_id) or str(p.get("status") or default_status)
            raw_ref_num = p.get("ref_num")
            ref_num_val = int(raw_ref_num) if raw_ref_num is not None else _ref_num(ref)
            source_id_val = str(p.get("source_id") or "vaquill_central_legislation")
            source_url_val = str(
                p.get("source_url")
                or VAQUILL_CENTRAL_LEGISLATION_URL
            )
            prov_rows.append(
                (
                    doc_id,
                    act_id,
                    act_short,
                    kind,
                    ref,
                    ref_norm,
                    ref_num_val,
                    p.get("chapter_num"),
                    p.get("chapter_title"),
                    p.get("part"),
                    p.get("title"),
                    norm.cleaned_text,
                    status,
                    p.get("commencement_date"),
                    p.get("repeal_date"),
                    json.dumps(p.get("metadata") or {}),
                    source_id_val,
                    source_url_val,
                    norm.text_sha256,
                    1 if p.get("commercial_safe", True) else 0,
                )
            )
            prov_by_doc_id[doc_id] = {
                "doc_id": doc_id,
                "act_id": act_id,
                "act_short": act_short,
                "kind": kind,
                "ref": ref,
                "ref_norm": ref_norm,
                "chapter_num": p.get("chapter_num"),
                "chapter_title": p.get("chapter_title"),
                "part": p.get("part"),
                "title": p.get("title"),
                "text": norm.cleaned_text,
                "commencement_date": p.get("commencement_date"),
                "source_id": source_id_val,
                "source_url": source_url_val,
            }
            inserted_provisions.add(doc_id)
            inserted_act_kind_ref.add(akr)

        for p in bundle.provisions:
            if commercial_safe_only and not bool(p.get("commercial_safe", True)):
                continue
            _queue_provision(p)

        # Load CPC First Schedule Orders & Rules from cpc_first_schedule.yaml
        for cpc_item in cpc_schedule_list:
            if commercial_safe_only and not bool(cpc_item.get("commercial_safe", True)):
                continue
            _queue_provision(
                {
                    "doc_id": cpc_item["doc_id"],
                    "act_id": str(cpc_item.get("act_id") or "cpc-1908"),
                    "act_short": str(cpc_item.get("act_short") or "CPC"),
                    "kind": str(cpc_item.get("kind") or "order_rule"),
                    "ref": str(cpc_item["ref"]),
                    "ref_norm": str(cpc_item.get("ref_norm") or cpc_item["ref"]).upper(),
                    "chapter_num": cpc_item.get("order_roman") or cpc_item.get("order_number"),
                    "chapter_title": cpc_item.get("order_title"),
                    "title": cpc_item.get("title"),
                    "text": cpc_item.get("text") or cpc_item.get("title") or "",
                    "status": str(cpc_item.get("status") or "in_force"),
                    "commencement_date": act_commencement_by_id.get("cpc-1908") or "1909-01-01",
                    "metadata": {
                        "order_number": cpc_item.get("order_number"),
                        "order_roman": cpc_item.get("order_roman"),
                        "rule_number": cpc_item.get("rule_number"),
                    },
                    "source_id": str(cpc_item.get("source_id") or "indiacode_central_acts"),
                    "source_url": str(cpc_item.get("source_url") or VAQUILL_CENTRAL_LEGISLATION_URL),
                }
            )

        all_concordance = [*concordance_list, *tax_concordance_list]

        # Ensure every provision referenced in criminal + tax concordance exists
        for m in all_concordance:
            old_act_id = str(m["old_act_id"])
            old_ref = str(m["old_ref"])
            old_doc_id = f"{old_act_id}:{old_ref}"
            default_old_source = (
                "indiacode_central_acts" if old_act_id == "it-act-1961" else "vaquill_central_legislation"
            )
            default_new_date = "2026-04-01" if str(m.get("new_act_id") or "") == "it-act-2025" else "2024-07-01"
            if old_doc_id not in inserted_provisions:
                _queue_provision(
                    {
                        "doc_id": old_doc_id,
                        "act_id": old_act_id,
                        "act_short": act_short_by_id.get(old_act_id, str(m["old_act"])),
                        "kind": "section",
                        "ref": old_ref,
                        "ref_norm": old_ref.upper(),
                        "title": m.get("title") or f"{m['old_act']} Section {old_ref}",
                        "text": f"Section {old_ref} of {m['old_act']}: {m['substantive_delta']}",
                        "status": inval_status_by_doc.get(old_doc_id, "repealed"),
                        "commencement_date": act_commencement_by_id.get(old_act_id),
                        "repeal_date": act_repeal_by_id.get(old_act_id) or default_new_date,
                        "source_id": str(m.get("source_id") or default_old_source),
                        "source_url": VAQUILL_CENTRAL_LEGISLATION_URL,
                    }
                )

            new_ref = str(m.get("new_ref") or "NONE")
            if new_ref != "NONE" and m.get("mapping_type") != "omitted":
                new_act_id = str(m["new_act_id"])
                new_doc_id = f"{new_act_id}:{new_ref}"
                base_ref = str(m.get("new_base_ref") or new_ref.split("(", 1)[0])
                base_doc_id = f"{new_act_id}:{base_ref}"
                default_conc_source = (
                    "indiacode_central_acts" if new_act_id == "it-act-2025" else "prs_2026_delta"
                )
                if new_doc_id not in inserted_provisions:
                    parent = prov_by_doc_id.get(base_doc_id)
                    if parent is not None:
                        _queue_provision(
                            {
                                "doc_id": new_doc_id,
                                "act_id": new_act_id,
                                "act_short": act_short_by_id.get(new_act_id, str(m["new_act"])),
                                "kind": "section",
                                "ref": new_ref,
                                "ref_norm": new_ref.upper(),
                                "chapter_num": parent.get("chapter_num"),
                                "chapter_title": parent.get("chapter_title"),
                                "part": parent.get("part"),
                                "title": parent.get("title"),
                                "text": parent["text"],
                                "status": "in_force",
                                "commencement_date": (
                                    parent.get("commencement_date")
                                    or act_commencement_by_id.get(new_act_id)
                                    or default_new_date
                                ),
                                "metadata": {
                                    "parent_doc_id": base_doc_id,
                                    "sub_clause_focus": new_ref,
                                },
                                "source_id": parent.get("source_id") or "vaquill_central_legislation",
                                "source_url": parent.get("source_url")
                                or VAQUILL_CENTRAL_LEGISLATION_URL,
                            }
                        )
                    else:
                        _queue_provision(
                            {
                                "doc_id": new_doc_id,
                                "act_id": new_act_id,
                                "act_short": act_short_by_id.get(new_act_id, str(m["new_act"])),
                                "kind": "section",
                                "ref": new_ref,
                                "ref_norm": new_ref.upper(),
                                "title": m.get("title") or f"{m['new_act']} Section {new_ref}",
                                "text": f"Section {new_ref} of {m['new_act']}: {m['substantive_delta']}",
                                "status": "in_force",
                                "commencement_date": act_commencement_by_id.get(new_act_id) or default_new_date,
                                "source_id": str(m.get("source_id") or default_conc_source),
                                "source_url": "https://prsindia.org/files/bills_acts/acts_parliament/2026/prs_2026_delta.json",
                            }
                        )
                # Also materialize base section if new_ref has sub-clause like 103(1)
                if base_ref != new_ref and base_doc_id not in inserted_provisions:
                    _queue_provision(
                        {
                            "doc_id": base_doc_id,
                            "act_id": new_act_id,
                            "act_short": act_short_by_id.get(new_act_id, str(m["new_act"])),
                            "kind": "section",
                            "ref": base_ref,
                            "ref_norm": base_ref.upper(),
                            "title": f"{m['new_act']} Section {base_ref}",
                            "text": f"Section {base_ref} of {m['new_act']}: {m['substantive_delta']}",
                            "status": "in_force",
                            "commencement_date": act_commencement_by_id.get(new_act_id) or default_new_date,
                            "source_id": str(m.get("source_id") or default_conc_source),
                            "source_url": "https://prsindia.org/files/bills_acts/acts_parliament/2026/prs_2026_delta.json",
                        }
                    )

        # Ensure every provision referenced in judicial_invalidations exists
        for inv in invalidations_list:
            t_doc_id = str(inv["target_doc_id"])
            if t_doc_id not in inserted_provisions:
                t_act_id = str(inv["act_id"])
                t_ref = str(inv["ref"])
                kind = "amendment" if "amend" in t_doc_id else "section"
                _queue_provision(
                    {
                        "doc_id": t_doc_id,
                        "act_id": t_act_id,
                        "act_short": act_short_by_id.get(t_act_id, str(inv["act"])),
                        "kind": kind,
                        "ref": t_ref,
                        "ref_norm": t_ref.upper(),
                        "title": f"{inv['act']} {t_ref}",
                        "text": f"{inv['act']} {t_ref}: {inv['holding_summary']}",
                        "status": str(inv["effect"]),
                        "source_id": "vaquill_central_legislation",
                        "source_url": VAQUILL_CENTRAL_LEGISLATION_URL,
                    }
                )

        _flush_batches(
            con,
            "INSERT OR REPLACE INTO provisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            prov_rows,
        )

        # 4. Populate all 106 Constitutional Amendments from amendments_1_106.yaml
        amend_rows = []
        for amend in amendments:
            num = int(amend["number"])
            arts = amend.get("articles_affected") or []
            arts_csv = ",".join(str(a) for a in arts)
            primary_ref = str(arts[0]).upper() if len(arts) == 1 else None
            amend_rows.append(
                (
                    f"const-amend-{num}",
                    "constitution",
                    primary_ref,
                    str(amend["title"]),
                    num,
                    str(amend["enacted_date"]),
                    str(amend.get("effective_date") or amend["enacted_date"]),
                    str(amend.get("action") or "amended"),
                    str(amend["summary"]),
                    arts_csv,
                    "legislative_constitution",
                )
            )
        _flush_batches(
            con,
            "INSERT OR REPLACE INTO provision_amendments VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            amend_rows,
        )

        # 5. Populate Criminal & Tax Concordance from criminal_concordance.yaml + tax_concordance.yaml
        conc_rows = []
        for idx, m in enumerate(all_concordance, start=1):
            old_doc_id = f"{m['old_act_id']}:{m['old_ref']}"
            new_ref = str(m.get("new_ref") or "NONE")
            conc_new_doc_id: str | None = (
                None
                if new_ref == "NONE" or m.get("mapping_type") == "omitted"
                else f"{m['new_act_id']}:{new_ref}"
            )
            if old_doc_id not in inserted_provisions:
                continue
            if conc_new_doc_id is not None and conc_new_doc_id not in inserted_provisions:
                conc_new_doc_id = None
            raw_oc = m.get("offence_classification")
            oc_json = json.dumps(raw_oc, sort_keys=True) if isinstance(raw_oc, dict) else None
            default_src = (
                "indiacode_central_acts" if m.get("old_act_id") == "it-act-1961" else "prs_2026_delta"
            )
            conc_rows.append(
                (
                    idx,
                    old_doc_id,
                    conc_new_doc_id,
                    str(m["mapping_type"]),
                    str(m["substantive_delta"]),
                    m.get("punishment_delta"),
                    m.get("procedural_delta"),
                    m.get("savings_note"),
                    str(m.get("verified_by") or "bprd_official_table_v1"),
                    str(m.get("source_id") or default_src),
                    oc_json,
                )
            )
        _flush_batches(
            con,
            "INSERT OR REPLACE INTO concordance VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            conc_rows,
        )

        # 6. Populate Judicial Invalidations from judicial_invalidations.yaml
        inval_rows = [
            (
                idx,
                str(inv["target_doc_id"]),
                str(inv["effect"]),
                str(inv["case_id"]),
                str(inv["case_name"]),
                str(inv["citation"]),
                str(inv["decision_date"]),
                int(inv["bench_strength"]),
                str(inv["holding_summary"]),
                "dattam_sc_judgments",
            )
            for idx, inv in enumerate(invalidations_list, start=1)
            if str(inv["target_doc_id"]) in inserted_provisions
        ]
        _flush_batches(
            con,
            "INSERT OR REPLACE INTO judicial_invalidations VALUES (?,?,?,?,?,?,?,?,?,?)",
            inval_rows,
        )

        # 7. Populate judgments_meta (SC + optional HC + optional ILDC)
        merged_hc = dedupe_and_merge_hc_rows(bundle.hc_web_rows, bundle.hc_mobile_rows)
        use_hc = include_hc_in_sqlite if include_hc_in_sqlite is not None else (len(merged_hc) <= 10_000)
        all_judgments: list[tuple[str, dict[str, Any]]] = [
            *[("sc", j) for j in bundle.sc_judgments],
            *([("hc", j) for j in merged_hc] if use_hc else []),
            *[("sc", j) for j in bundle.ildc_rows],
        ]
        jmeta_rows: list[tuple[Any, ...]] = []
        for tier_code, j in all_judgments:
            comm_safe = bool(j.get("commercial_safe", True))
            if commercial_safe_only and not comm_safe:
                continue
            case_id = str(j["case_id"])
            title_norm = normalize_legal_record(str(j.get("title") or ""), doc_id=case_id, metadata=j)
            if title_norm.blocked_by_publication_ban or not _is_gate_clean(title_norm.cleaned_text):
                continue
            raw_text = str(j.get("text") or "").strip()
            if raw_text:
                text_norm = normalize_legal_record(raw_text, doc_id=case_id, metadata=j)
                if text_norm.blocked_by_publication_ban:
                    continue
                content_sha = text_norm.text_sha256
            else:
                content_sha = title_norm.text_sha256

            acts_cited = j.get("acts_cited") or ([j["act"]] if j.get("act") else [])
            jmeta_rows.append(
                (
                    case_id,
                    tier_code,
                    str(j.get("court_code") or ("SCI" if tier_code == "sc" else "7_26")),
                    str(j.get("court_name") or "Supreme Court of India"),
                    j.get("bench"),
                    j.get("cnr"),
                    j.get("neutral_citation"),
                    j.get("air_citation"),
                    j.get("scc_citation"),
                    j.get("scr_citation"),
                    title_norm.cleaned_text,
                    j.get("petitioner"),
                    j.get("respondent"),
                    j.get("decision_date"),
                    int(j["year"]) if j.get("year") is not None else None,
                    str(j.get("judge_coram") or j.get("judge") or ""),
                    j.get("author_judge") or j.get("judge"),
                    int(j["bench_strength"]) if j.get("bench_strength") is not None else None,
                    j.get("disposal_nature"),
                    j.get("case_type"),
                    json.dumps(acts_cited),
                    1 if j.get("is_reportable") else 0,
                    j.get("pdf_s3_bucket"),
                    j.get("pdf_s3_key"),
                    str(j.get("source_url") or "https://indian-supreme-court-judgments.s3.ap-south-1.amazonaws.com"),
                    content_sha,
                    1 if len(raw_text) >= 120 else 0,
                    int(j.get("cited_by_count") or 0),
                    str(j.get("source_id") or ("dattam_sc_judgments" if tier_code == "sc" else "dattam_hc_judgments")),
                    1 if comm_safe else 0,
                )
            )
        _flush_batches(
            con,
            "INSERT OR REPLACE INTO judgments_meta VALUES ("
            "?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            jmeta_rows,
        )

        # 8. Populate citation_edges (curated + corpus-wide automated extraction)
        edge_rows = list(bundle.citation_edges)
        alias_dict = {
            alias: act_short_by_id[aid]
            for alias, aid in alias_rows
            if aid in act_short_by_id
        }
        corpus_edges = build_citation_edges_for_corpus(
            bundle.sc_judgments,
            [*merged_hc, *bundle.ildc_rows],
            provisions=prov_by_doc_id.values(),
            alias_map=alias_dict,
        )
        edge_rows.extend(corpus_edges)

        edge_tuples = [
            (
                str(e["from_id"]).strip(),
                str(e["from_kind"]),
                str(e["to_id"]).strip(),
                str(e["to_kind"]),
                str(e["relation"]),
                e.get("para_ref"),
                e.get("quoting_excerpt"),
                e.get("from_year"),
                e.get("from_bench_str"),
                str(e.get("precedent_status") or "unverified_automated"),
            )
            for e in edge_rows
            if str(e.get("from_id") or "").strip()
            and str(e.get("to_id") or "").strip()
            and str(e.get("from_id")).strip() != str(e.get("to_id")).strip()
        ]
        _flush_batches(
            con,
            "INSERT OR IGNORE INTO citation_edges "
            "(from_id, from_kind, to_id, to_kind, relation, para_ref, quoting_excerpt, from_year, from_bench_str, precedent_status) "
            "VALUES (?,?,?,?,?,?,?,?,?,?)",
            edge_tuples,
        )

        # 9. Populate courts from courts_directory.yaml (both canonical court_code and dattam_code if distinct)
        court_tuples: list[tuple[Any, ...]] = []
        for c in courts_list:
            c_code = str(c["court_code"])
            c_name = str(c["court_name"])
            c_tier = str(c["tier"])
            b_code = str(c.get("bench_code") or c_code)
            b_name = str(c.get("bench_name") or "Principal Bench")
            state_ut = str(c.get("state_ut") or "All India")
            seat_city = c.get("seat_city")
            juris = c.get("hc_jurisdiction") or []
            juris_csv = ",".join(str(x) for x in juris) if isinstance(juris, list) else str(juris)
            court_tuples.append((c_code, c_name, c_tier, b_code, b_name, state_ut, seat_city, juris_csv))
            dattam_code = c.get("dattam_code")
            if dattam_code and str(dattam_code) != c_code:
                court_tuples.append(
                    (str(dattam_code), c_name, c_tier, str(dattam_code), b_name, state_ut, seat_city, juris_csv)
                )
        _flush_batches(con, "INSERT OR REPLACE INTO courts VALUES (?,?,?,?,?,?,?,?)", court_tuples)

        # 9b. Populate cause_lists (official SC/HC feeds)
        cl_tuples: list[tuple[Any, ...]] = []
        for idx, cl in enumerate(bundle.cause_lists, start=1):
            adv_raw = cl.get("advocates")
            if isinstance(adv_raw, list):
                adv_json = json.dumps([str(x) for x in adv_raw if str(x).strip()])
            elif isinstance(adv_raw, str) and adv_raw.strip().startswith("["):
                adv_json = adv_raw.strip()
            elif adv_raw:
                adv_json = json.dumps([str(adv_raw).strip()])
            else:
                adv_json = "[]"
            cl_tuples.append(
                (
                    idx,
                    str(cl.get("court") or ""),
                    str(cl.get("cause_list_date") or ""),
                    cl.get("bench"),
                    cl.get("judge"),
                    cl.get("item_number"),
                    cl.get("case_number"),
                    cl.get("cnr"),
                    cl.get("parties"),
                    adv_json,
                    cl.get("purpose"),
                    str(cl.get("source_url") or ""),
                )
            )
        _flush_batches(
            con,
            "INSERT OR REPLACE INTO cause_lists VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            cl_tuples,
        )

        # 10. Compute real record counts per source_id, rebuild FTS5 virtual tables, and verify integrity
        source_counts: dict[str, int] = {}
        for tbl in (
            "provisions",
            "provision_amendments",
            "concordance",
            "judicial_invalidations",
            "judgments_meta",
        ):
            for sid, cnt in con.execute(
                f"SELECT source_id, count(*) FROM {tbl} GROUP BY source_id"
            ).fetchall():
                source_counts[str(sid)] = source_counts.get(str(sid), 0) + int(cnt)

        for item in bundle.state_regulator_provisions:
            if commercial_safe_only and not bool(item.get("commercial_safe", True)):
                continue
            sid = str(
                item.get("source_id")
                or (
                    "vaquill_regulators"
                    if item.get("category") == "regulator"
                    else "vaquill_state_legislation"
                )
            )
            source_counts[sid] = source_counts.get(sid, 0) + 1

        for rows, default_sid in (
            (bundle.tribunal_rows, "vaquill_tribunals"),
            (bundle.regulator_order_rows, "vaquill_regulators"),
            (bundle.ddl_district_rows, "ddl_81m"),
        ):
            for item in rows:
                if commercial_safe_only and not bool(item.get("commercial_safe", True)):
                    continue
                sid = str(item.get("source_id") or default_sid)
                source_counts[sid] = source_counts.get(sid, 0) + 1

        if (not use_hc) or (not merged_hc and bundle.hc_row_count > 0):
            hc_total = len(bundle.hc_web_rows)
            if bundle.hc_row_count > hc_total:
                hc_total = int(bundle.hc_row_count)
            if hc_total > 0:
                source_counts["dattam_hc_judgments"] = (
                    source_counts.get("dattam_hc_judgments", 0) + hc_total
                )

        con.executemany(
            "UPDATE sources SET record_count = ? WHERE source_id = ?",
            [(cnt, sid) for sid, cnt in source_counts.items()],
        )

        con.execute("INSERT INTO provisions_fts(provisions_fts) VALUES('rebuild')")
        con.execute("INSERT INTO judgments_meta_fts(judgments_meta_fts) VALUES('rebuild')")
        con.commit()

        fk_errors = con.execute("PRAGMA foreign_key_check").fetchall()
        if fk_errors:
            raise RuntimeError(f"SQLite foreign_key_check failed: {fk_errors}")
        integrity = con.execute("PRAGMA integrity_check").fetchone()
        if not integrity or str(integrity[0]).lower() != "ok":
            raise RuntimeError(f"SQLite integrity_check failed: {integrity}")

        con.execute("PRAGMA synchronous = NORMAL")
        con.execute("PRAGMA journal_mode=DELETE")
        con.execute("VACUUM")
        con.execute("ANALYZE")
    finally:
        con.close()

    if final_path.exists():
        final_path.unlink()
    staging_path.replace(final_path)

    sha_path = final_path.with_suffix(final_path.suffix + ".sha256")
    digest = _sha256_file(final_path)
    sha_path.write_text(f"{digest}  {final_path.name}\n", encoding="utf-8")
    return sha_path
