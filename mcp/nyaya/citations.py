"""Shared Indian legal citation parser, court canonicalizer, and verifier.

Single source of truth imported by MCP tools (``verify_citations``,
``get_provision``, ``get_judgment``), ingestion citation edge extraction, and
``chat/nyaya_chat/citations.py``.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

from pydantic import BaseModel, Field

from .sanitize import BIDI_RE, strip_control_chars

log = logging.getLogger("nyaya.citations")

CitationKind = Literal[
    "cnr",
    "sc_neutral",
    "hc_neutral",
    "air",
    "scc",
    "scr",
    "other_reporter",
    "constitution_article",
    "constitution_schedule",
    "cpc_order_rule",
    "statute_section",
    "regulation_clause",
    "constitution_amendment",
]

CITATION_MARKER_RE = re.compile(
    r"\[\[act:\s*([^,\]]+?)\s*,\s*ref:\s*([^\]]+?)\s*\]\]"
)


class ParsedCitation(BaseModel):
    raw_text: str
    span: tuple[int, int] | None = None
    kind: CitationKind
    canonical_key: str
    act_or_court: str | None = None
    base_ref: str
    sub_ref: str | None = None
    components: dict[str, str | int | None] = Field(default_factory=dict)


# Static built-in alias table so chat/ and standalone callers work without SQLite.
_DEFAULT_ALIAS_MAP: dict[str, str] = {
    "ipc": "IPC",
    "indian penal code": "IPC",
    "penal code": "IPC",
    "bns": "BNS",
    "bharatiya nyaya sanhita": "BNS",
    "nyaya sanhita": "BNS",
    "crpc": "CrPC",
    "code of criminal procedure": "CrPC",
    "criminal procedure code": "CrPC",
    "bnss": "BNSS",
    "bharatiya nagarik suraksha sanhita": "BNSS",
    "nagarik suraksha sanhita": "BNSS",
    "iea": "EvidenceAct",
    "indian evidence act": "EvidenceAct",
    "evidence act": "EvidenceAct",
    "evidenceact": "EvidenceAct",
    "bsa": "BSA",
    "bharatiya sakshya adhiniyam": "BSA",
    "sakshya adhiniyam": "BSA",
    "cpc": "CPC",
    "code of civil procedure": "CPC",
    "civil procedure code": "CPC",
    "constitution": "Constitution",
    "the constitution": "Constitution",
    "constitution of india": "Constitution",
    "it act": "ITAct",
    "itact": "ITAct",
    "information technology act": "ITAct",
    "companies": "CompaniesAct2013",
    "companies act": "CompaniesAct2013",
    "companies act 2013": "CompaniesAct2013",
    "companiesact2013": "CompaniesAct2013",
    "ica": "ContractAct",
    "contract act": "ContractAct",
    "indian contract act": "ContractAct",
    "indian contract act, 1872": "ContractAct",
    "tpa": "TransferOfPropertyAct",
    "transfer of property act": "TransferOfPropertyAct",
    "sra": "SpecificReliefAct",
    "specific relief act": "SpecificReliefAct",
    "specific relief act, 1963": "SpecificReliefAct",
    "ni act": "NIAct",
    "niact": "NIAct",
    "negotiable instruments act": "NIAct",
    "negotiable instruments act, 1881": "NIAct",
    "ibc": "IBC",
    "insolvency and bankruptcy code": "IBC",
    "sarfaesi": "SARFAESI",
    "arbitration": "ArbitrationAct",
    "arbitration act": "ArbitrationAct",
    "arbitration and conciliation act": "ArbitrationAct",
    "arbitration and conciliation act, 1996": "ArbitrationAct",
    "consumer protection act": "ConsumerProtection",
    "consumerprotection": "ConsumerProtection",
    "cgst": "CGST",
    "central goods and services tax": "CGST",
    "igst": "IGST",
    "integrated goods and services tax": "IGST",
    "pocso": "POCSO",
    "ndps": "NDPS",
    "pmla": "PMLA",
    "uapa": "UAPA",
    "hma": "HMA",
    "hindu marriage act": "HMA",
    "limitation": "LimitationAct",
    "limitation act": "LimitationAct",
    "limitation act, 1963": "LimitationAct",
    "rti": "RTI",
    "right to information act": "RTI",
    "sebi act": "SEBIAct",
    "sebi act, 1992": "SEBIAct",
    "sebi lodr": "SEBI-LODR",
    "sebi (lodr)": "SEBI-LODR",
    "sebi-lodr": "SEBI-LODR",
    "sebi": "SEBI",
    "rbi": "RBI",
    "mh-mofa": "MH-MOFA",
    "mofa": "MH-MOFA",
    "aadhaar act": "AadhaarAct",
    "aadhaar": "AadhaarAct",
    "jj act": "JJAct",
    "hiv act": "HIVAct",
}

_REGULATOR_ACTS = frozenset({"SEBI-LODR", "SEBI", "RBI"})

_COURT_ALIASES: dict[str, str] = {
    "sc": "SC",
    "sci": "SC",
    "supreme court": "SC",
    "supreme court of india": "SC",
    "pc": "PC",
    "privy council": "PC",
    "dhc": "DHC",
    "del": "DHC",
    "delhi": "DHC",
    "delhi hc": "DHC",
    "delhi high court": "DHC",
    "7_26": "DHC",
    "bhc": "BHC",
    "bom": "BHC",
    "bombay": "BHC",
    "bombay hc": "BHC",
    "bombay high court": "BHC",
    "mumbai": "BHC",
    "27_1": "BHC",
    "cal": "CAL",
    "calcutta": "CAL",
    "calcutta hc": "CAL",
    "calcutta high court": "CAL",
    "19_16": "CAL",
    "mad": "MAD",
    "madras": "MAD",
    "madras hc": "MAD",
    "madras high court": "MAD",
    "33_10": "MAD",
    "ahc": "AHC",
    "all": "AHC",
    "allahabad": "AHC",
    "allahabad hc": "AHC",
    "allahabad high court": "AHC",
    "9_13": "AHC",
    "kar": "KAR",
    "karnataka": "KAR",
    "karnataka hc": "KAR",
    "karnataka high court": "KAR",
    "ker": "KER",
    "kerala": "KER",
    "kerala hc": "KER",
    "kerala high court": "KER",
    "guj": "GUJ",
    "gujarat": "GUJ",
    "gujarat hc": "GUJ",
    "gujarat high court": "GUJ",
    "raj": "RAJ",
    "rajasthan": "RAJ",
    "rajasthan hc": "RAJ",
    "rajasthan high court": "RAJ",
    "phc": "PHC",
    "p&h": "PHC",
    "punjab and haryana": "PHC",
    "punjab and haryana high court": "PHC",
    "pat": "PAT",
    "patna": "PAT",
    "patna hc": "PAT",
    "patna high court": "PAT",
    "mphc": "MPHC",
    "mp": "MPHC",
    "madhya pradesh": "MPHC",
    "madhya pradesh high court": "MPHC",
    "aphc": "APHC",
    "ap": "APHC",
    "andhra pradesh": "APHC",
    "andhra pradesh high court": "APHC",
    "tshc": "TSHC",
    "telangana": "TSHC",
    "telangana high court": "TSHC",
    "ori": "ORI",
    "orissa": "ORI",
    "odisha": "ORI",
    "orissa high court": "ORI",
    "gau": "GAU",
    "gauhati": "GAU",
    "guwahati": "GAU",
    "gauhati high court": "GAU",
    "jhc": "JHC",
    "jharkhand": "JHC",
    "jharkhand high court": "JHC",
    "cghc": "CGHC",
    "chhattisgarh": "CGHC",
    "chhattisgarh high court": "CGHC",
    "ukhc": "UKHC",
    "uttarakhand": "UKHC",
    "uttarakhand high court": "UKHC",
    "hphc": "HPHC",
    "himachal pradesh": "HPHC",
    "himachal pradesh high court": "HPHC",
    "jkhc": "JKHC",
    "jammu and kashmir": "JKHC",
    "man": "MAN",
    "manipur": "MAN",
    "meg": "MEG",
    "meghalaya": "MEG",
    "tri": "TRI",
    "tripura": "TRI",
    "sik": "SIK",
    "sikkim": "SIK",
    "nclt": "NCLT",
    "nclat": "NCLAT",
    "itat": "ITAT",
    "ngt": "NGT",
    "cat": "CAT",
    "sat": "SAT",
    "aptel": "APTEL",
    "cestat": "CESTAT",
    "drt": "DRT",
    "cci": "CCI",
    "ibbi": "IBBI",
    "tdsat": "TDSAT",
    "rera": "RERA",
    "atfp": "ATFP",
    "gst_aar": "GST_AAR",
}

_ROMAN_MAP: dict[str, int] = {"I": 1, "V": 5, "X": 10, "L": 50}


def _roman_or_int(val: str) -> str:
    clean = val.strip().upper()
    if clean.isdigit():
        return str(int(clean))
    m = re.fullmatch(r"([IVXL]+)([A-Z]?)", clean)
    if not m:
        return clean
    roman, suffix = m.group(1), m.group(2)
    total = 0
    prev = 0
    for ch in reversed(roman):
        cur = _ROMAN_MAP.get(ch, 0)
        if cur < prev:
            total -= cur
        else:
            total += cur
        prev = cur
    return f"{total}{suffix}" if total > 0 else clean


_CANON_TO_CODE_VARIANTS: dict[str, tuple[str, ...]] = {
    "SC": ("SC", "SCI", "Supreme Court"),
    "DHC": ("DHC", "7_26", "High Court of Delhi", "Delhi High Court"),
    "BHC": ("BHC", "27_1", "High Court of Bombay", "Bombay High Court"),
    "CAL": ("CAL", "19_16", "High Court of Calcutta", "Calcutta High Court"),
    "MAD": ("MAD", "33_10", "High Court of Madras", "Madras High Court"),
    "AHC": ("AHC", "9_13", "High Court of Allahabad", "Allahabad High Court"),
}


def canonicalize_court(raw: str | None) -> str | None:
    """Normalize a court name, abbreviation, or Dattam numeric code to canonical code."""
    if not raw:
        return None
    cleaned = BIDI_RE.sub("", strip_control_chars(raw)).strip()
    if not cleaned:
        return None
    key = re.sub(r"\s+", " ", cleaned.lower()).rstrip(".")
    if key in _COURT_ALIASES:
        return _COURT_ALIASES[key]
    if key.endswith(" high court"):
        prefix = key[: -len(" high court")].strip()
        if prefix in _COURT_ALIASES:
            return _COURT_ALIASES[prefix]
    return None


def court_code_variants(raw: str | None) -> tuple[str, ...]:
    """Return all code/name variants (canonical code + Dattam numeric code + raw) for a court."""
    if not raw or not raw.strip():
        return ()
    clean = raw.strip()
    canon = canonicalize_court(clean)
    variants: list[str] = [clean]
    if canon and canon not in variants:
        variants.append(canon)
    if canon and canon in _CANON_TO_CODE_VARIANTS:
        for v in _CANON_TO_CODE_VARIANTS[canon]:
            if v not in variants:
                variants.append(v)
    return tuple(variants)


_DATTAM_CODE_TO_NAME: dict[str, str] = {
    "SC": "Supreme Court of India",
    "SCI": "Supreme Court of India",
    "AHC": "High Court of Judicature at Allahabad",
    "9_13": "High Court of Judicature at Allahabad",
    "9~13": "High Court of Judicature at Allahabad",
    "APHC": "High Court of Andhra Pradesh",
    "28_2": "High Court of Andhra Pradesh",
    "28~2": "High Court of Andhra Pradesh",
    "BHC": "High Court of Bombay",
    "27_1": "High Court of Bombay",
    "27~1": "High Court of Bombay",
    "CAL": "High Court at Calcutta",
    "19_16": "High Court at Calcutta",
    "19~16": "High Court at Calcutta",
    "CGHC": "High Court of Chhattisgarh",
    "22_18": "High Court of Chhattisgarh",
    "22~18": "High Court of Chhattisgarh",
    "DHC": "High Court of Delhi",
    "7_26": "High Court of Delhi",
    "7~26": "High Court of Delhi",
    "GAU": "Gauhati High Court",
    "18_6": "Gauhati High Court",
    "18~6": "Gauhati High Court",
    "GUJ": "High Court of Gujarat",
    "24_17": "High Court of Gujarat",
    "24~17": "High Court of Gujarat",
    "HPHC": "High Court of Himachal Pradesh",
    "2_5": "High Court of Himachal Pradesh",
    "2~5": "High Court of Himachal Pradesh",
    "JKHC": "High Court of Jammu & Kashmir and Ladakh",
    "1_12": "High Court of Jammu & Kashmir and Ladakh",
    "1~12": "High Court of Jammu & Kashmir and Ladakh",
    "JHC": "High Court of Jharkhand",
    "20_7": "High Court of Jharkhand",
    "20~7": "High Court of Jharkhand",
    "KAR": "High Court of Karnataka",
    "29_3": "High Court of Karnataka",
    "29~3": "High Court of Karnataka",
    "KER": "High Court of Kerala",
    "32_4": "High Court of Kerala",
    "32~4": "High Court of Kerala",
    "MPHC": "High Court of Madhya Pradesh",
    "23_23": "High Court of Madhya Pradesh",
    "23~23": "High Court of Madhya Pradesh",
    "MAD": "High Court of Judicature at Madras",
    "33_10": "High Court of Judicature at Madras",
    "33~10": "High Court of Judicature at Madras",
    "MAN": "High Court of Manipur",
    "14_25": "High Court of Manipur",
    "14~25": "High Court of Manipur",
    "MEG": "High Court of Meghalaya",
    "17_21": "High Court of Meghalaya",
    "17~21": "High Court of Meghalaya",
    "ORI": "High Court of Orissa",
    "21_11": "High Court of Orissa",
    "21~11": "High Court of Orissa",
    "PAT": "High Court of Judicature at Patna",
    "10_8": "High Court of Judicature at Patna",
    "10~8": "High Court of Judicature at Patna",
    "PHC": "High Court of Punjab and Haryana",
    "3_22": "High Court of Punjab and Haryana",
    "3~22": "High Court of Punjab and Haryana",
    "RAJ": "High Court of Judicature for Rajasthan",
    "8_9": "High Court of Judicature for Rajasthan",
    "8~9": "High Court of Judicature for Rajasthan",
    "SIK": "High Court of Sikkim",
    "11_24": "High Court of Sikkim",
    "11~24": "High Court of Sikkim",
    "TSHC": "High Court for the State of Telangana",
    "36_29": "High Court for the State of Telangana",
    "36~29": "High Court for the State of Telangana",
    "TRI": "High Court of Tripura",
    "16_20": "High Court of Tripura",
    "16~20": "High Court of Tripura",
    "UKHC": "High Court of Uttarakhand",
    "5_15": "High Court of Uttarakhand",
    "5~15": "High Court of Uttarakhand",
}


def court_display_name(code: str | None) -> str | None:
    """Resolve a Dattam code (``_`` or ``~``) or canonical court code to its display name."""
    if not code:
        return None
    cleaned = BIDI_RE.sub("", strip_control_chars(code)).strip()
    if not cleaned:
        return None
    norm = cleaned.replace("~", "_").upper()
    return _DATTAM_CODE_TO_NAME.get(norm)


_JURISDICTION_NAME_MAP: dict[str, str] = {
    "central": "Central",
    "union of india": "Central",
    "andaman and nicobar islands": "AN",
    "andaman and nicobar": "AN",
    "andhra pradesh": "AP",
    "arunachal pradesh": "AR",
    "assam": "AS",
    "bihar": "BR",
    "chandigarh": "CH",
    "chhattisgarh": "CG",
    "chattisgarh": "CG",
    "dadra and nagar haveli and daman and diu": "DN",
    "dadra and nagar haveli": "DN",
    "daman and diu": "DN",
    "delhi": "DL",
    "nct of delhi": "DL",
    "national capital territory of delhi": "DL",
    "new delhi": "DL",
    "goa": "GA",
    "gujarat": "GJ",
    "haryana": "HR",
    "himachal pradesh": "HP",
    "jammu and kashmir": "JK",
    "jharkhand": "JH",
    "karnataka": "KA",
    "kerala": "KL",
    "ladakh": "LA",
    "lakshadweep": "LD",
    "madhya pradesh": "MP",
    "maharashtra": "MH",
    "manipur": "MN",
    "meghalaya": "ML",
    "mizoram": "MZ",
    "nagaland": "NL",
    "odisha": "OD",
    "orissa": "OD",
    "puducherry": "PY",
    "pondicherry": "PY",
    "punjab": "PB",
    "rajasthan": "RJ",
    "sikkim": "SK",
    "tamil nadu": "TN",
    "telangana": "TS",
    "tripura": "TR",
    "uttar pradesh": "UP",
    "uttarakhand": "UK",
    "uttaranchal": "UK",
    "west bengal": "WB",
}


def normalize_jurisdiction(jurisdiction: str | None) -> str | None:
    """Map full Indian state/UT names case-insensitively to canonical codes, preserving short codes."""
    if not jurisdiction:
        return None
    cleaned = BIDI_RE.sub("", strip_control_chars(jurisdiction)).strip()
    if not cleaned:
        return None
    key = re.sub(
        r"\s+",
        " ",
        cleaned.lower().replace("&", " and ").replace("-", " "),
    ).strip()
    if key in _JURISDICTION_NAME_MAP:
        return _JURISDICTION_NAME_MAP[key]
    return cleaned


# Priority 1..11 compiled patterns (applied strictly in order)
_CNR_RE = re.compile(r"\b([A-Z]{2}[A-Z0-9]{2}\d{2}\d{6}\d{4})\b")
_SC_NEUTRAL_RE = re.compile(r"\b((?:19|20)\d{2})(?:\s+INSC\s+|:INSC:)(\d{1,7})\b", re.IGNORECASE)
_HC_NEUTRAL_RE = re.compile(r"\b((?:19|20)\d{2}):([A-Z]{2,6}(?:-[A-Z]{2,4})?):(\d{1,7})\b")
_AIR_RE = re.compile(r"\bAIR\s+((?:19|20)\d{2})\s+(SC|PC|[A-Z][a-zA-Z&]{1,12})\s+(\d{1,7})\b")
_SCC_RE = re.compile(
    r"\(((?:19|20)\d{2})\)\s*(\d{1,2})\s+SCC(?:\s*\((Cri|L&S|Civ|Tax|Supp)\))?\s+(\d{1,7})\b",
    re.IGNORECASE,
)
_SCR_RE = re.compile(
    r"[\[(]((?:19|20)\d{2})[\])]\s*(?:(\d{1,2})|(Supp\.?\s*\d?))?\s*S\.?C\.?R\.?\s+(\d{1,7})\b",
    re.IGNORECASE,
)
_OTHER_REPORTER_RE = re.compile(
    r"\b((?:19|20)\d{2})\s+(?:(\d{1,2})\s+)?(SCALE|JT|Cri\.?\s*LJ|CrLJ|SCC\s+OnLine\s+[A-Za-z]+)\s+(\d{1,6})\b",
    re.IGNORECASE,
)
_CONST_SCHED_RE = re.compile(
    r"(?:(?:of\s+(?:the\s+)?)?Constitution(?:\s+of\s+India)?\s*,?\s*)?"
    r"(?:(\d{1,2})(?:st|nd|rd|th)?\s+Schedule|Schedule\s+(XI{1,2}|IX|X|VI{1,3}|IV|V|I{1,3}|\d{1,2}))\b"
    r"(?:\s*,?\s*List\s+(I{1,3}|\d))?"
    r"(?:\s*,?\s*Entry\s+(\d+[A-Z]?))?",
    re.IGNORECASE,
)
_CONST_ART_RE = re.compile(
    r"\bArt(?:icle|s)?\.?\s*(\d{1,3}[A-Z]?)((?:\([0-9a-zA-Z]+\)){0,4})(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_SINGLE_CPC_RULE_RE = re.compile(
    r"(?P<rule>\d{1,3}[A-Z]?)(?P<sub>(?:\([0-9a-zA-Z]+\)){0,3})"
)
_CPC_PLURAL_RE = re.compile(
    r"(?:\bCPC\s*,?\s*)?\bO(?:rder)?\.?\s*"
    r"(?P<order>(?=[IVX\d])(?:X{0,3}(?:IX|IV|V?I{0,3})|\d{1,2}[A-Z]?))\s*,?\s*"
    r"R(?:ules?|s)\.?\s*"
    r"(?P<rules>\d{1,3}[A-Z]?(?:\([0-9a-zA-Z]+\)){0,3}"
    r"(?:\s*(?:,|&|\band\b|\bor\b|r/w|read\s+with|/)\s*(?:R(?:ules?)?\.?\s*)?\d{1,3}[A-Z]?(?:\([0-9a-zA-Z]+\)){0,3})+)"
    r"(?:\s+(?:of\s+(?:the\s+)?)?(?:CPC|Code\s+of\s+Civil\s+Procedure))?(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_CPC_INVERTED_RE = re.compile(
    r"\bR(?:ules?)?\.?\s*"
    r"(?P<rules>\d{1,3}[A-Z]?(?:\([0-9a-zA-Z]+\)){0,3}"
    r"(?:\s*(?:,|&|\band\b|\bor\b|r/w|read\s+with|/)\s*(?:R(?:ules?)?\.?\s*)?\d{1,3}[A-Z]?(?:\([0-9a-zA-Z]+\)){0,3})*)"
    r"(?:\s+of|\s*,)\s*O(?:rder)?\.?\s*"
    r"(?P<order>(?=[IVX\d])(?:X{0,3}(?:IX|IV|V?I{0,3})|\d{1,2}[A-Z]?))"
    r"(?!\s*,?\s*R(?:ule)?\.?\s*\d)"
    r"(?:\s+(?:of\s+(?:the\s+)?)?(?:CPC|Code\s+of\s+Civil\s+Procedure))?(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_CPC_ORDER_RULE_RE = re.compile(
    r"(?:\bCPC\s*,?\s*)?\bO(?:rder)?\.?\s*(X{0,3}(?:IX|IV|V?I{0,3})|\d{1,2}[A-Z]?)\s*,?\s*"
    r"R(?:ule)?\.?\s*(\d{1,3}[A-Z]?)((?:\([0-9a-zA-Z]+\)){0,3})(?![A-Za-z0-9])"
    r"(?:\s+(?:of\s+(?:the\s+)?)?(?:CPC|Code\s+of\s+Civil\s+Procedure))?",
    re.IGNORECASE,
)
_CONST_AMEND_RE = re.compile(
    r"\b(\d{1,3})(?:st|nd|rd|th)\s+(?:Constitutional\s+|Constitution\s+)?Amendment(?:\s+Act(?:,?\s*\d{4})?)?\b",
    re.IGNORECASE,
)

_SINGLE_REF_RE = re.compile(
    r"(?P<base>\d{1,4}[A-Z]{0,2})(?P<sub>(?:\([0-9a-zA-Z]+\)){0,4})"
)
_CHAIN_ITEM_SEP = r"(?:,|&|\band\b|\bor\b|r/w|read\s+with|/)"
_NON_CITATION_YEAR_LOOKAHEAD = r"(?!\s*(?:INSC\b|:|SCC\b|SCR\b|SCALE\b|JT\b|Cr\.?\s*LJ\b|CrLJ\b))"
_CHAIN_EXPR = (
    rf"(?P<chain>\d{{1,4}}[A-Z]{{0,2}}{_NON_CITATION_YEAR_LOOKAHEAD}(?:\([0-9a-zA-Z]+\)){{0,4}}"
    rf"(?:\s*{_CHAIN_ITEM_SEP}\s*(?:s(?:ec(?:tion)?s?|s)?\.?\s*)?\d{{1,4}}[A-Z]{{0,2}}{_NON_CITATION_YEAR_LOOKAHEAD}(?:\([0-9a-zA-Z]+\)){{0,4}})*)"
)
_MARKER_EXPR = r"(?P<marker>s(?:ec(?:tion)?s?|s)?|Reg(?:ulation)?s?|Rules?)"


def _merged_alias_map(alias_map: Mapping[str, str] | None) -> dict[str, str]:
    merged = dict(_DEFAULT_ALIAS_MAP)
    if alias_map:
        for k, v in alias_map.items():
            clean_k = re.sub(r"\s+", " ", BIDI_RE.sub("", k).strip().lower())
            if clean_k and v:
                merged[clean_k] = v
    return merged


def _overlaps(span: tuple[int, int], occupied: list[tuple[int, int]]) -> bool:
    s, e = span
    return any(s < oe and os < e for os, oe in occupied)


def _resolve_act_alias(raw_act: str, aliases: Mapping[str, str]) -> str:
    key = re.sub(r"\s+", " ", BIDI_RE.sub("", raw_act).strip().lower()).rstrip(".")
    return aliases.get(key, raw_act.strip())


def parse_citations(
    text: str,
    *,
    alias_map: Mapping[str, str] | None = None,
) -> list[ParsedCitation]:
    """Parse Indian legal citations in strict specificity priority order."""
    if not text or not text.strip():
        return []

    aliases = _merged_alias_map(alias_map)
    results: list[ParsedCitation] = []
    occupied: list[tuple[int, int]] = []

    def _claim(span: tuple[int, int]) -> bool:
        if _overlaps(span, occupied):
            return False
        occupied.append(span)
        return True

    # 1. CNR
    for m in _CNR_RE.finditer(text):
        if _claim(m.span()):
            cnr = m.group(1).upper()
            results.append(
                ParsedCitation(
                    raw_text=m.group(0),
                    span=m.span(),
                    kind="cnr",
                    canonical_key=f"judgment::CNR::{cnr}",
                    act_or_court=cnr[:4],
                    base_ref=cnr,
                    components={"cnr": cnr},
                )
            )

    # 2. SC Neutral (INSC)
    for m in _SC_NEUTRAL_RE.finditer(text):
        if _claim(m.span()):
            year, num = int(m.group(1)), int(m.group(2))
            results.append(
                ParsedCitation(
                    raw_text=m.group(0),
                    span=m.span(),
                    kind="sc_neutral",
                    canonical_key=f"judgment::INSC::{year}:{num}",
                    act_or_court="SC",
                    base_ref=f"{year} INSC {num}",
                    components={"year": year, "number": num},
                )
            )

    # 3. HC Neutral (YEAR:COURT:NUM)
    for m in _HC_NEUTRAL_RE.finditer(text):
        if _claim(m.span()):
            year = int(m.group(1))
            hc_raw = m.group(2).upper()
            num = int(m.group(3))
            results.append(
                ParsedCitation(
                    raw_text=m.group(0),
                    span=m.span(),
                    kind="hc_neutral",
                    canonical_key=f"judgment::HC_NEUTRAL::{year}:{hc_raw}:{num}",
                    act_or_court=canonicalize_court(hc_raw.split("-", 1)[0]) or hc_raw,
                    base_ref=f"{year}:{hc_raw}:{num}",
                    components={"year": year, "court": hc_raw, "number": num},
                )
            )

    # 4. AIR
    for m in _AIR_RE.finditer(text):
        if _claim(m.span()):
            year = int(m.group(1))
            raw_court = m.group(2)
            page = int(m.group(3))
            canon_court = canonicalize_court(raw_court) or raw_court.upper()
            results.append(
                ParsedCitation(
                    raw_text=m.group(0),
                    span=m.span(),
                    kind="air",
                    canonical_key=f"judgment::AIR::{year}:{canon_court}:{page}",
                    act_or_court=canon_court,
                    base_ref=f"AIR {year} {raw_court} {page}",
                    components={"year": year, "court": canon_court, "page": page},
                )
            )

    # 5. SCC
    for m in _SCC_RE.finditer(text):
        if _claim(m.span()):
            year = int(m.group(1))
            vol = int(m.group(2))
            series = (m.group(3) or "").upper()
            page = int(m.group(4))
            results.append(
                ParsedCitation(
                    raw_text=m.group(0),
                    span=m.span(),
                    kind="scc",
                    canonical_key=f"judgment::SCC::{year}:{vol}:{series}:{page}",
                    act_or_court="SC",
                    base_ref=m.group(0).strip(),
                    components={
                        "year": year,
                        "volume": vol,
                        "series": series or None,
                        "page": page,
                    },
                )
            )

    # 6. SCR
    for m in _SCR_RE.finditer(text):
        if _claim(m.span()):
            year = int(m.group(1))
            vol_str = m.group(2)
            supp = m.group(3)
            page = int(m.group(4))
            vol_key = (
                "SUPP"
                if supp
                else (str(int(vol_str)) if vol_str else "")
            )
            results.append(
                ParsedCitation(
                    raw_text=m.group(0),
                    span=m.span(),
                    kind="scr",
                    canonical_key=f"judgment::SCR::{year}:{vol_key}:{page}",
                    act_or_court="SC",
                    base_ref=m.group(0).strip(),
                    components={"year": year, "volume": vol_key or None, "page": page},
                )
            )

    # 7. Other reporters (SCALE, JT, CriLJ, SCC OnLine)
    for m in _OTHER_REPORTER_RE.finditer(text):
        if _claim(m.span()):
            year = int(m.group(1))
            vol_opt = m.group(2)
            reporter_raw = re.sub(r"\s+", " ", m.group(3).strip())
            page = int(m.group(4))
            upper_rep = reporter_raw.upper()
            if upper_rep.startswith("SCC ONLINE"):
                court_part = upper_rep.replace("SCC ONLINE", "").strip()
                key = f"judgment::SCC_ONLINE::{year}:{court_part}:{page}"
            elif "CR" in upper_rep and "LJ" in upper_rep:
                key = f"judgment::CRILJ::{year}:{vol_opt or ''}:{page}"
            else:
                key = f"judgment::{upper_rep}::{year}:{vol_opt or ''}:{page}"
            results.append(
                ParsedCitation(
                    raw_text=m.group(0),
                    span=m.span(),
                    kind="other_reporter",
                    canonical_key=key,
                    act_or_court=None,
                    base_ref=m.group(0).strip(),
                    components={"year": year, "reporter": reporter_raw, "page": page},
                )
            )

    # 8. Constitution Schedule
    for m in _CONST_SCHED_RE.finditer(text):
        if _claim(m.span()):
            sched_raw = m.group(1) or m.group(2)
            sched_num = _roman_or_int(sched_raw)
            list_part = m.group(3)
            entry_part = m.group(4)
            sub_parts: list[str] = []
            if list_part:
                sub_parts.append(f"List {list_part.upper()}")
            if entry_part:
                sub_parts.append(f"Entry {entry_part.upper()}")
            sub_ref = " ".join(sub_parts) if sub_parts else None
            results.append(
                ParsedCitation(
                    raw_text=m.group(0),
                    span=m.span(),
                    kind="constitution_schedule",
                    canonical_key=f"statute::Constitution::SCH{sched_num}",
                    act_or_court="Constitution",
                    base_ref=f"Schedule {sched_num}",
                    sub_ref=sub_ref,
                    components={"schedule": sched_num, "list": list_part, "entry": entry_part},
                )
            )

    # 9. Constitution Article
    for m in _CONST_ART_RE.finditer(text):
        if _claim(m.span()):
            base = m.group(1).upper()
            sub = m.group(2) or None
            results.append(
                ParsedCitation(
                    raw_text=m.group(0),
                    span=m.span(),
                    kind="constitution_article",
                    canonical_key=f"statute::Constitution::{base}",
                    act_or_court="Constitution",
                    base_ref=base,
                    sub_ref=sub,
                    components={"article": base},
                )
            )

    # 10. CPC Order / Rule (plural and inverted forms before single Order/Rule)
    def _emit_cpc_rule(order_raw: str, rule_raw: str, sub: str | None, raw_text: str, span: tuple[int, int]) -> None:
        order_num = _roman_or_int(order_raw)
        rule_num = rule_raw.upper()
        results.append(
            ParsedCitation(
                raw_text=raw_text,
                span=span,
                kind="cpc_order_rule",
                canonical_key=f"statute::CPC::O{order_num}-R{rule_num}",
                act_or_court="CPC",
                base_ref=f"Order {order_num} Rule {rule_num}",
                sub_ref=sub,
                components={"order": order_num, "rule": rule_num},
            )
        )

    def _emit_each_cpc_rule(
        order_raw: str, rule_matches: list[re.Match[str]], rules_start: int
    ) -> None:
        for rm in rule_matches:
            r_span = (rules_start + rm.start(), rules_start + rm.end())
            _emit_cpc_rule(
                order_raw, rm.group("rule"), rm.group("sub") or None, rm.group(0), r_span
            )

    for m in _CPC_PLURAL_RE.finditer(text):
        if _claim(m.span()):
            _emit_each_cpc_rule(
                m.group("order"),
                list(_SINGLE_CPC_RULE_RE.finditer(m.group("rules"))),
                m.start("rules"),
            )

    for m in _CPC_INVERTED_RE.finditer(text):
        if _claim(m.span()):
            order_raw = m.group("order")
            rule_matches = list(_SINGLE_CPC_RULE_RE.finditer(m.group("rules")))
            if len(rule_matches) == 1:
                # A single rule keeps the span of the whole "Rule X of Order Y" phrase.
                rm = rule_matches[0]
                _emit_cpc_rule(
                    order_raw,
                    rm.group("rule"),
                    rm.group("sub") or None,
                    m.group(0),
                    m.span(),
                )
            else:
                _emit_each_cpc_rule(order_raw, rule_matches, m.start("rules"))

    for m in _CPC_ORDER_RULE_RE.finditer(text):
        if _claim(m.span()):
            _emit_cpc_rule(
                m.group(1),
                m.group(2),
                m.group(3) or None,
                m.group(0),
                m.span(),
            )

    # 11. Constitution Amendment
    for m in _CONST_AMEND_RE.finditer(text):
        if _claim(m.span()):
            num = int(m.group(1))
            results.append(
                ParsedCitation(
                    raw_text=m.group(0),
                    span=m.span(),
                    kind="constitution_amendment",
                    canonical_key=f"amendment::Constitution::{num}",
                    act_or_court="Constitution",
                    base_ref=str(num),
                    components={"number": num},
                )
            )

    # 12. Statute sections & regulator clauses (dual-order with chained sections)
    act_keys = sorted(aliases.keys(), key=len, reverse=True)
    if act_keys:
        act_alt = "|".join(re.escape(k) for k in act_keys)
        act_before_re = re.compile(
            rf"(?<![A-Za-z0-9])(?P<act>{act_alt})(?:\s*,?\s*(?:18|19|20)\d{{2}})?\s*,?\s*{_MARKER_EXPR}\.?\s*{_CHAIN_EXPR}",
            re.IGNORECASE,
        )
        sec_before_re = re.compile(
            rf"\b{_MARKER_EXPR}\.?\s*{_CHAIN_EXPR}\s+(?:of\s+(?:the\s+)?)?(?P<act>{act_alt})(?![A-Za-z0-9])",
            re.IGNORECASE,
        )

        for pattern in (act_before_re, sec_before_re):
            for m in pattern.finditer(text):
                act_span = m.span("act")
                marker_span = m.span("marker")
                if _overlaps(act_span, occupied) or _overlaps(marker_span, occupied):
                    continue
                canon_act = _resolve_act_alias(m.group("act"), aliases)
                marker = m.group("marker").lower()
                is_reg = marker.startswith("reg") or canon_act in _REGULATOR_ACTS
                kind: CitationKind = "regulation_clause" if is_reg else "statute_section"
                prefix = "regulation" if is_reg else "statute"
                chain_text = m.group("chain")
                chain_start = m.start("chain")
                matched_any = False
                for ref_m in _SINGLE_REF_RE.finditer(chain_text):
                    ref_span = (chain_start + ref_m.start(), chain_start + ref_m.end())
                    if _overlaps(ref_span, occupied):
                        break
                    occupied.append(ref_span)
                    matched_any = True
                    base = ref_m.group("base").upper()
                    sub = ref_m.group("sub") or None
                    results.append(
                        ParsedCitation(
                            raw_text=ref_m.group(0)
                            if len(chain_text) > len(ref_m.group(0))
                            else m.group(0),
                            span=ref_span if len(chain_text) > len(ref_m.group(0)) else m.span(),
                            kind=kind,
                            canonical_key=f"{prefix}::{canon_act}::{base}",
                            act_or_court=canon_act,
                            base_ref=base,
                            sub_ref=sub,
                            components={"act": canon_act, "marker": marker},
                        )
                    )
                if matched_any:
                    occupied.append(act_span)
                    occupied.append(marker_span)

    results.sort(key=lambda c: c.span[0] if c.span else 0)
    return results


def parse_single_citation(
    raw: str,
    *,
    alias_map: Mapping[str, str] | None = None,
) -> ParsedCitation | None:
    """Parse a single citation string and return the primary ParsedCitation or None."""
    if not raw or not raw.strip():
        return None
    parsed = parse_citations(raw.strip(), alias_map=alias_map)
    return parsed[0] if parsed else None


def _keys_from_citation_str(
    raw: str,
    aliases: Mapping[str, str],
) -> set[str]:
    keys: set[str] = set()
    for p in parse_citations(raw, alias_map=aliases):
        keys.add(p.canonical_key)
    return keys


def _index_provision_like(
    obj: Mapping[str, Any],
    aliases: Mapping[str, str],
    out: set[str],
) -> None:
    act = obj.get("act")
    ref = obj.get("ref")
    if not act or not ref:
        return
    canon_act = _resolve_act_alias(str(act), aliases)
    ref_str = str(ref).strip()
    parsed = parse_single_citation(f"{canon_act} {ref_str}", alias_map=aliases)
    if parsed is not None:
        out.add(parsed.canonical_key)
        return
    # Handle bare numeric/alphanumeric ref for statute or Constitution
    stripped = re.sub(
        r"^(?:s(?:ec(?:tion)?)?\.?\s*|art(?:icle)?\.?\s*|reg(?:ulation)?\.?\s*)",
        "",
        ref_str,
        flags=re.IGNORECASE,
    ).strip()
    m = _SINGLE_REF_RE.match(stripped)
    if m:
        base = m.group("base").upper()
        prefix = "regulation" if canon_act in _REGULATOR_ACTS else "statute"
        out.add(f"{prefix}::{canon_act}::{base}")
    elif canon_act == "CPC":
        cpc_p = parse_single_citation(f"CPC {ref_str}", alias_map=aliases)
        if cpc_p:
            out.add(cpc_p.canonical_key)
    elif canon_act == "Constitution":
        sched_p = parse_single_citation(f"Constitution {ref_str}", alias_map=aliases)
        if sched_p:
            out.add(sched_p.canonical_key)


def _index_judgment_like(
    obj: Mapping[str, Any],
    aliases: Mapping[str, str],
    out: set[str],
) -> None:
    case_id = obj.get("case_id")
    if case_id:
        out.add(f"judgment::CASE_ID::{str(case_id).strip().lower()}")
    for field in (
        "cnr",
        "neutral_citation",
        "air_citation",
        "scc_citation",
        "scr_citation",
        "citation",
    ):
        val = obj.get(field)
        if isinstance(val, str) and val.strip():
            out.update(_keys_from_citation_str(val, aliases))
    reps = obj.get("reporter_citations")
    if isinstance(reps, list):
        for r in reps:
            if isinstance(r, str) and r.strip():
                out.update(_keys_from_citation_str(r, aliases))


def _walk_payload(
    node: Any,
    aliases: Mapping[str, str],
    out: set[str],
) -> None:
    if isinstance(node, list):
        for item in node:
            _walk_payload(item, aliases, out)
        return
    if not isinstance(node, dict):
        return
    if isinstance(node.get("error"), dict):
        return

    _index_provision_like(node, aliases, out)
    _index_judgment_like(node, aliases, out)

    # CrossRef / StatutoryLink style {from_act, from_section, to_act, to_section} or {from_ref, to_ref}
    for act_k, sec_k in (("from_act", "from_section"), ("to_act", "to_section")):
        if node.get(act_k) and node.get(sec_k):
            _index_provision_like({"act": node[act_k], "ref": node[sec_k]}, aliases, out)

    for v in node.values():
        if isinstance(v, (dict, list)):
            _walk_payload(v, aliases, out)


def extract_grounded_keys(
    tool_payloads: Sequence[str],
    *,
    alias_map: Mapping[str, str] | None = None,
) -> set[str]:
    """Extract canonical citation/provision/judgment keys from serialized tool outputs."""
    aliases = _merged_alias_map(alias_map)
    keys: set[str] = set()
    for content in tool_payloads:
        if not content or not content.strip():
            continue
        try:
            data = json.loads(content)
        except (json.JSONDecodeError, TypeError):
            continue
        _walk_payload(data, aliases, keys)
    return keys


def _marker_candidate_keys(
    act: str,
    ref: str,
    aliases: Mapping[str, str],
) -> set[str]:
    act_clean = act.strip()
    ref_clean = ref.strip()
    act_lower = act_clean.lower()

    # Judgment markers (including act="judgment", act="SC", act="DHC", etc.)
    if act_lower in {"judgment", "judgments", "case", "cases"} or (
        canonicalize_court(act_clean) is not None and act_lower not in aliases
    ):
        keys = _keys_from_citation_str(ref_clean, aliases)
        if not keys and ref_clean:
            keys.add(f"judgment::CASE_ID::{ref_clean.lower()}")
        return keys

    # Statutory / Constitutional / Regulatory markers
    canon_act = _resolve_act_alias(act_clean, aliases)
    keys = _keys_from_citation_str(f"{canon_act} {ref_clean}", aliases)
    if keys:
        return keys

    if canon_act == "Constitution":
        art_m = _CONST_ART_RE.search(ref_clean)
        if art_m:
            return {f"statute::Constitution::{art_m.group(1).upper()}"}
        sched_m = _CONST_SCHED_RE.search(ref_clean)
        if sched_m:
            sched_raw = sched_m.group(1) or sched_m.group(2)
            return {f"statute::Constitution::SCH{_roman_or_int(sched_raw)}"}
        if re.fullmatch(r"\d{1,3}[A-Za-z]?", ref_clean):
            return {f"statute::Constitution::{ref_clean.upper()}"}

    if canon_act == "CPC":
        cpc_m = _CPC_ORDER_RULE_RE.search(ref_clean)
        if cpc_m:
            return {
                f"statute::CPC::O{_roman_or_int(cpc_m.group(1))}-R{cpc_m.group(2).upper()}"
            }

    stripped = re.sub(
        r"^(?:s(?:ec(?:tion)?)?\.?\s*|art(?:icle)?\.?\s*|reg(?:ulation)?\.?\s*)",
        "",
        ref_clean,
        flags=re.IGNORECASE,
    ).strip()
    m = _SINGLE_REF_RE.fullmatch(stripped)
    if m:
        base = m.group("base").upper()
        prefix = "regulation" if canon_act in _REGULATOR_ACTS else "statute"
        return {f"{prefix}::{canon_act}::{base}"}

    # Fallback: maybe ref itself is a full judgment citation
    return _keys_from_citation_str(ref_clean, aliases)


def _repair_whitespace_around_markers(text: str) -> str:
    """Collapse marker-removal spacing debris between surviving citation markers."""
    punct_re = r"[ \t]+([.,;:!?])"
    horiz_re = r"(?<=\S)[ \t]{2,}(?=\S)"

    def _repair(gap: str) -> str:
        gap = re.sub(punct_re, r"\1", gap)
        return re.sub(horiz_re, " ", gap)

    if not CITATION_MARKER_RE.search(text):
        return _repair(text)
    parts: list[str] = []
    pos = 0
    for m in CITATION_MARKER_RE.finditer(text):
        parts.append(_repair(text[pos : m.start()]))
        parts.append(m.group(0))
        pos = m.end()
    parts.append(_repair(text[pos:]))
    return "".join(parts)


def verify_inline_markers(
    answer_text: str,
    tool_payloads: Sequence[str],
    *,
    alias_map: Mapping[str, str] | None = None,
    had_tool_calls: bool = False,
) -> str:
    """Verify ``[[act: X, ref: Y]]`` inline markers strictly against tool payloads."""
    matches = list(CITATION_MARKER_RE.finditer(answer_text))
    if not matches:
        if had_tool_calls:
            return answer_text.rstrip() + (
                "\n\n> **Note:** The model's response did not include verifiable "
                "citations from the corpus. Please verify the information independently."
            )
        return answer_text

    aliases = _merged_alias_map(alias_map)
    grounded_keys = extract_grounded_keys(tool_payloads, alias_map=aliases)
    grounded_count = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal grounded_count
        act_str = match.group(1).strip()
        ref_str = match.group(2).strip()
        candidates = _marker_candidate_keys(act_str, ref_str, aliases)
        if candidates and (candidates & grounded_keys):
            grounded_count += 1
            return match.group(0)
        log.info("ungrounded citation stripped: act=%s ref=%s", act_str, ref_str)
        return ""

    result = CITATION_MARKER_RE.sub(_replace, answer_text)
    result = _repair_whitespace_around_markers(result)

    if grounded_count == 0 and had_tool_calls:
        result = result.rstrip() + (
            "\n\n> **Note:** The model's response did not include verifiable "
            "citations from the corpus. Please verify the information independently."
        )
    return result
