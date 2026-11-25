"""Staged Parquet loader (`loader.py`) assembling an `IngestInputBundle` from synced
Vaquill Open India Law packs and Dattam SC/HC metadata Parquets.
"""

from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from collections.abc import Iterable
from datetime import date, datetime
from pathlib import Path
from typing import Any

import duckdb

from .builders.sqlite_core import (
    VAQUILL_CENTRAL_LEGISLATION_URL,
    IngestInputBundle,
    create_mini_ingest_input,
)
from .extract.normalize import normalize_legal_record
from .fetchers.official_statutes import load_courts_yaml, load_curated_provisions_yaml
from .fetchers.vaquill import classify_vaquill_file

_HTML_OR_ERROR_PREFIX_RE = re.compile(
    r"^\s*(?:<html\b|<!DOCTYPE\b|\{\s*\"error\"|404\s+Not\s+Found)",
    re.IGNORECASE,
)
_COMMERCIAL_HEADNOTE_RE = re.compile(
    r"\b(?:SCC\s+Online|Manupatra|AIR\s+Headnote)\b",
    re.IGNORECASE,
)
_VAQUILL_HEADER_RE = re.compile(r"^Act:\s*[^\n]+\n(?:Chapter\s+[^\n]+?\s*\|\s*)?")
_CHAPTER_TITLE_RE = re.compile(
    r"(?:^|\n)Chapter\s+[^:\n|]+:\s*([^\n|]+?)\s*(?:\||$)",
    re.IGNORECASE,
)
_LEADING_SECTION_LINE_RE = re.compile(
    r"^(?:Section|Article|Regulation|Rule)\s+[^:\n]+:\s*[^\n]*\n+(?=\S)",
    re.IGNORECASE,
)
_INDIA_CODE_FOOTNOTE_RE = re.compile(
    r"(?m)^\s*(?:>\s*)?\d+\.\s*(?:Subs\.|Ins\.|Omitted|Rep\.|Added|Substituted|Inserted|"
    r"The words|Certain words|See\s+also|Amended|Notified)(?=\s|$)[^\n]*$"
)
_INLINE_FOOTNOTE_SUP_RE = re.compile(r"(?<!\w)\d+\[")
_AMEND_BOILERPLATE_RE = re.compile(
    r"^(?:For section|In section|After section|Substitute|Geneva Convention|Making of arbitral award)",
    re.IGNORECASE,
)
_TOC_SECTION_HEADING_RE = re.compile(
    r"(?:^|\n|\|\s*)(?:Section|Article)\s+\d+[A-Z]?\s*:",
    re.IGNORECASE,
)
_SECTION_HEADING_RE = re.compile(
    r"(?:^|\n|\|\s*)(?:Section|Article|Regulation|Rule)\s+[^:\n]+:\s*([^\n*]+)",
    re.IGNORECASE,
)
_INSC_RE = re.compile(r"\b((?:19|20)\d{2})\s+INSC\s+(\d+)\b", re.IGNORECASE)
_INSC_COMPACT_RE = re.compile(r"^((?:19|20)\d{2})INSC(\d+)$", re.IGNORECASE)
_YEAR_DIR_RE = re.compile(r"year=((?:19|20)\d{2})")
_HTML_TAG_RE = re.compile(r"<[^>]+>")

_VALID_ACT_STATUSES = frozenset(
    {"in_force", "repealed", "partially_repealed", "not_yet_commenced", "superseded"}
)

# Canonical mapping from Vaquill central `act_id` -> (canon_act_id, short_name, status, replaced_by, commencement, repeal)
_VAQUILL_CENTRAL_ACT_MAP: dict[str, tuple[str, str, str, str | None, str | None, str | None]] = {
    "IND_REP_659_1860": ("ipc-1860", "IPC", "repealed", "bns-2023", "1862-01-01", "2024-07-01"),
    "IND_central_20062": ("bns-2023", "BNS", "in_force", None, "2024-07-01", None),
    "IND_central_20099": ("bnss-2023", "BNSS", "in_force", None, "2024-07-01", None),
    "IND_REP_971_1872": ("iea-1872", "EvidenceAct", "repealed", "bsa-2023", "1872-09-01", "2024-07-01"),
    "IND_central_20063": ("bsa-2023", "BSA", "in_force", None, "2024-07-01", None),
    "IND_central_1999": ("it-act-2000", "ITAct", "in_force", None, "2000-10-17", None),
    "IND_central_2191": ("cpc-1908", "CPC", "in_force", None, "1909-01-01", None),
    "IND_central_2189": ("ni-act-1881", "NIAct", "in_force", None, "1882-03-01", None),
    "IND_central_2187": ("contract-act-1872", "ContractAct", "in_force", None, "1872-09-01", None),
    "IND_central_2114": ("companies-act-2013", "CompaniesAct2013", "in_force", None, "2013-08-30", None),
    "IND_central_1978": ("arbitration-act-1996", "ArbitrationAct", "in_force", None, "1996-08-22", None),
    "IND_central_2154": ("ibc-2016", "IBC", "in_force", None, "2016-05-28", None),
    "IND_central_2160": ("aadhaar-2016", "AadhaarAct", "in_force", None, "2016-03-26", None),
    "IND_central_2096": ("rpa-1951", "RPA1951", "in_force", None, "1951-07-17", None),
    "IND_central_1583": ("specific-relief-1963", "SpecificReliefAct", "in_force", None, "1964-03-01", None),
    "IND_central_1890": ("sebi-act-1992", "SEBIAct", "in_force", None, "1992-01-30", None),
    "IND_central_2338": ("transfer-of-property-1882", "TransferOfPropertyAct", "in_force", None, "1882-07-01", None),
    "IND_central_1565": ("limitation-act-1963", "LimitationAct", "in_force", None, "1964-01-01", None),
}

_STATE_CODE_MAP: dict[str, str] = {
    "andaman-nicobar": "AN",
    "andhra-pradesh": "AP",
    "arunachal-pradesh": "AR",
    "assam": "AS",
    "bihar": "BR",
    "chandigarh": "CH",
    "chhattisgarh": "CG",
    "dadra-nagar-haveli": "DN",
    "delhi": "DL",
    "goa": "GA",
    "gujarat": "GJ",
    "haryana": "HR",
    "himachal-pradesh": "HP",
    "jammu-kashmir": "JK",
    "jharkhand": "JH",
    "karnataka": "KA",
    "kerala": "KL",
    "ladakh": "LA",
    "lakshadweep": "LD",
    "madhya-pradesh": "MP",
    "maharashtra": "MH",
    "manipur": "MN",
    "meghalaya": "ML",
    "mizoram": "MZ",
    "nagaland": "NL",
    "odisha": "OD",
    "puducherry": "PY",
    "punjab": "PB",
    "rajasthan": "RJ",
    "sikkim": "SK",
    "tamil-nadu": "TN",
    "telangana": "TS",
    "tripura": "TR",
    "uttar-pradesh": "UP",
    "uttarakhand": "UK",
    "west-bengal": "WB",
}

_REGULATOR_CODE_MAP: dict[str, str] = {
    "cbic": "CBIC",
    "cpcb": "CPCB",
    "dfs": "DFS",
    "dgft": "DGFT",
    "irdai": "IRDAI",
    "law-commission": "LAWCOMM",
    "mca": "MCA",
    "moefcc": "MOEFCC",
    "rbi": "RBI",
    "sebi": "SEBI",
    "state-gst": "STATE_GST",
    "trai": "TRAI",
}

_TRIBUNAL_CODE_MAP: dict[str, str] = {
    "aptel": "APTEL",
    "atfp": "ATFP",
    "cat": "CAT",
    "cat_cis": "CAT",
    "cci": "CCI",
    "cestat": "CESTAT",
    "drt": "DRT",
    "gst_aar": "GST_AAR",
    "ibbi": "IBBI",
    "itat": "ITAT",
    "nclat": "NCLAT",
    "nclt": "NCLT",
    "ngt": "NGT",
    "rera": "RERA",
    "sat": "SAT",
    "tdsat": "TDSAT",
}

# Verified landmark SC metadata + real Dattam S3 PDF keys (verified HTTP 200)
_CANONICAL_LANDMARKS: tuple[dict[str, Any], ...] = (
    {
        "match_cnr": "ESCR010003021973",
        "match_insc": "1973 INSC 91",
        "match_title": "kesavananda bharati",
        "case_id": "sc:1973:kesavananda",
        "neutral_citation": "1973 INSC 91",
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
        "pdf_s3_key": "data/pdf/year=1973/english/S_1973_1_1_1002_EN.pdf",
        "cited_by_count": 1450,
        "doctrine_summary": (
            "Established the Basic Structure Doctrine under Article 368 of the Constitution of India: "
            "while Parliament possesses wide constituent power under Article 368 to amend any part of "
            "the Constitution including Fundamental Rights, it cannot alter, abrogate, or destroy the "
            "basic structure or essential framework of the Constitution (13-Judge Bench; overruled Golaknath)."
        ),
    },
    {
        "match_cnr": "ESCR010000342017",
        "match_insc": "2017 INSC 801",
        "match_title": "puttaswamy",
        "case_id": "sc:2017:puttaswamy",
        "neutral_citation": "2017 INSC 801",
        "air_citation": None,
        "scc_citation": "(2017) 10 SCC 1",
        "scr_citation": "[2017] 10 S.C.R. 569",
        "title": "Justice K.S. Puttaswamy (Retd.) v. Union of India",
        "petitioner": "Justice K.S. Puttaswamy",
        "respondent": "Union of India",
        "decision_date": "2017-08-24",
        "year": 2017,
        "judge_coram": "J.S. Khehar; D.Y. Chandrachud; S.K. Kaul",
        "author_judge": "D.Y. Chandrachud",
        "bench_strength": 9,
        "disposal_nature": "Reference answered",
        "case_type": "Writ Petition (Civil)",
        "acts_cited": ["Constitution", "AadhaarAct"],
        "pdf_s3_key": "data/pdf/year=2017/english/2017_10_569_999_EN.pdf",
        "cited_by_count": 820,
        "doctrine_summary": (
            "Unanimous 9-Judge Constitution Bench affirmed the fundamental right to privacy as an "
            "intrinsic part of the right to life and personal liberty under Article 21 and Part III "
            "of the Constitution (overruling M.P. Sharma and Kharak Singh). Any State intrusion into "
            "privacy, including biometric Aadhaar schemes, must satisfy legality, legitimate State aim, "
            "and proportionality (later applied in the 2018 Aadhaar-5J decision)."
        ),
    },
    {
        "match_cnr": "ESCR010005392015",
        "match_insc": "2015 INSC 257",
        "match_title": "shreya singhal",
        "case_id": "sc:2015:shreya-singhal",
        "neutral_citation": "2015 INSC 257",
        "air_citation": "AIR 2015 SC 1523",
        "scc_citation": "(2015) 5 SCC 1",
        "scr_citation": "[2015] 5 S.C.R. 963",
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
        "pdf_s3_key": "data/pdf/year=2015/english/2015_5_963_1074_EN.pdf",
        "cited_by_count": 610,
        "doctrine_summary": (
            "Struck down Section 66A of the Information Technology Act, 2000 in its entirety as "
            "violative of freedom of speech and expression under Article 19(1)(a) and not saved by "
            "Article 19(2) on grounds of vagueness, overbreadth, and chilling effect; distinguished "
            "discussion and advocacy from incitement. Also read down Section 79(3)(b) intermediary "
            "safe harbour to require actual knowledge via a court order or government notification."
        ),
    },
    {
        "match_cnr": "ESCR010000851983",
        "match_insc": "1983 INSC 34",
        "match_title": "mithu",
        "case_id": "sc:1983:mithu",
        "neutral_citation": "1983 INSC 34",
        "air_citation": "AIR 1983 SC 473",
        "scc_citation": "(1983) 2 SCC 277",
        "scr_citation": "[1983] 2 S.C.R. 690",
        "title": "Mithu v. State of Punjab",
        "petitioner": "Mithu",
        "respondent": "State of Punjab",
        "decision_date": "1983-04-07",
        "year": 1983,
        "judge_coram": "Y.V. Chandrachud; S. Murtaza Fazal Ali; V.D. Tulzapurkar; O. Chinnappa Reddy; A. Varadarajan",
        "author_judge": "Y.V. Chandrachud",
        "bench_strength": 5,
        "disposal_nature": "Allowed",
        "case_type": "Writ Petition (Criminal)",
        "acts_cited": ["IPC", "Constitution"],
        "pdf_s3_key": "data/pdf/year=1983/english/1983_2_690_713_EN.pdf",
        "cited_by_count": 320,
        "doctrine_summary": (
            "Struck down Section 303 of the Indian Penal Code, 1860 (mandatory death penalty for "
            "murder committed by a person undergoing life imprisonment) as arbitrary, oppressive, and "
            "unconstitutional under Articles 14 and 21 of the Constitution."
        ),
    },
    {
        "match_cnr": "ESCR010007292018",
        "match_insc": "2018 INSC 790",
        "match_title": "navtej singh johar",
        "case_id": "sc:2018:navtej-johar",
        "neutral_citation": "2018 INSC 790",
        "air_citation": "AIR 2018 SC 4321",
        "scc_citation": "(2018) 10 SCC 1",
        "scr_citation": "[2018] 7 S.C.R. 379",
        "title": "Navtej Singh Johar v. Union of India",
        "petitioner": "Navtej Singh Johar",
        "respondent": "Union of India",
        "decision_date": "2018-09-06",
        "year": 2018,
        "judge_coram": "Dipak Misra; R.F. Nariman; A.M. Khanwilkar; D.Y. Chandrachud; Indu Malhotra",
        "author_judge": "Dipak Misra",
        "bench_strength": 5,
        "disposal_nature": "Disposed of",
        "case_type": "Writ Petition (Criminal)",
        "acts_cited": ["IPC", "Constitution"],
        "pdf_s3_key": "data/pdf/year=2018/english/2018_7_379_746_EN.pdf",
        "cited_by_count": 480,
        "doctrine_summary": (
            "Read down Section 377 of the Indian Penal Code, 1860 to decriminalize consensual sexual "
            "conduct between adults in private, holding it violative of equality, dignity, privacy, "
            "and non-discrimination under Articles 14, 15, 19(1)(a), and 21 (overruling Suresh Kumar Koushal)."
        ),
    },
    {
        "match_cnr": "ESCR010001032018",
        "match_insc": "2018 INSC 898",
        "match_title": "joseph shine",
        "case_id": "sc:2018:joseph-shine",
        "neutral_citation": "2018 INSC 898",
        "air_citation": "AIR 2018 SC 4898",
        "scc_citation": "(2019) 3 SCC 39",
        "scr_citation": "[2018] 11 S.C.R. 765",
        "title": "Joseph Shine v. Union of India",
        "petitioner": "Joseph Shine",
        "respondent": "Union of India",
        "decision_date": "2018-09-27",
        "year": 2018,
        "judge_coram": "Dipak Misra; R.F. Nariman; A.M. Khanwilkar; D.Y. Chandrachud; Indu Malhotra",
        "author_judge": "Dipak Misra",
        "bench_strength": 5,
        "disposal_nature": "Allowed",
        "case_type": "Writ Petition (Criminal)",
        "acts_cited": ["IPC", "CrPC", "Constitution"],
        "pdf_s3_key": "data/pdf/year=2018/english/2018_11_765_950_EN.pdf",
        "cited_by_count": 340,
        "doctrine_summary": (
            "Struck down Section 497 of the Indian Penal Code, 1860 (adultery) and Section 198(2) CrPC "
            "insofar as it applied to Section 497 as manifestly arbitrary, patriarchal, and violative "
            "of Articles 14, 15, and 21 (overruling Sowmithri Vishnu and Yusuf Abdul Aziz)."
        ),
    },
    {
        "match_cnr": "ESCR010001201978",
        "match_insc": "1978 INSC 16",
        "match_title": "maneka gandhi",
        "case_id": "sc:1978:maneka-gandhi",
        "neutral_citation": "1978 INSC 16",
        "air_citation": "AIR 1978 SC 597",
        "scc_citation": "(1978) 1 SCC 248",
        "scr_citation": "[1978] 2 S.C.R. 621",
        "title": "Maneka Gandhi v. Union of India",
        "petitioner": "Maneka Gandhi",
        "respondent": "Union of India",
        "decision_date": "1978-01-25",
        "year": 1978,
        "judge_coram": "M. Hameedullah Beg; Y.V. Chandrachud; P.N. Bhagwati; V.R. Krishna Iyer",
        "author_judge": "P.N. Bhagwati",
        "bench_strength": 7,
        "disposal_nature": "Disposed of",
        "case_type": "Writ Petition (Civil)",
        "acts_cited": ["Constitution"],
        "pdf_s3_key": "data/pdf/year=1978/english/1978_2_621_776_EN.pdf",
        "cited_by_count": 980,
        "doctrine_summary": (
            "Expanded Article 21 of the Constitution by holding that 'procedure established by law' "
            "must be fair, just, and reasonable, and not arbitrary, fanciful, or oppressive; established "
            "the golden triangle interconnecting Articles 14, 19, and 21 (departing from A.K. Gopalan)."
        ),
    },
    {
        "match_cnr": None,
        "match_insc": "2023 INSC 1066",
        "match_title": "interplay between arbitration",
        "case_id": "sc:2023:in-re-interplay",
        "neutral_citation": "2023 INSC 1066",
        "air_citation": None,
        "scc_citation": "(2024) 6 SCC 1",
        "scr_citation": None,
        "title": "In Re: Interplay Between Arbitration Agreements Under the Arbitration and Conciliation Act 1996 and the Indian Stamp Act 1899",
        "petitioner": "In Re: Interplay Between Arbitration Agreements",
        "respondent": "Union of India",
        "decision_date": "2023-12-13",
        "year": 2023,
        "judge_coram": "D.Y. Chandrachud; Sanjay Kishan Kaul; Sanjiv Khanna; B.R. Gavai; Surya Kant; J.B. Pardiwala; Manoj Misra",
        "author_judge": "D.Y. Chandrachud",
        "bench_strength": 7,
        "disposal_nature": "Reference answered",
        "case_type": "Curative Petition (Civil)",
        "acts_cited": ["ArbitrationAct", "Constitution"],
        "pdf_s3_key": None,
        "cited_by_count": 250,
        "doctrine_summary": (
            "Seven-Judge (7-Judge) Constitution Bench held that unstamped or inadequately stamped arbitration "
            "agreements are not void ab initio or unenforceable under the Contract Act, only "
            "inadmissible in evidence under Section 35 of the Stamp Act until cured; affirmed "
            "separability and kompetenz-kompetenz under Sections 7, 11(6A), and 16 of the Arbitration "
            "and Conciliation Act, 1996 (overruling N.N. Global Mercantile v. Indo Unique Flame)."
        ),
    },
    {
        "match_cnr": None,
        "match_insc": "1980 INSC 71",
        "match_title": "gurbaksh singh sibbia",
        "case_id": "sc:1980:sibbia",
        "neutral_citation": "1980 INSC 71",
        "air_citation": "AIR 1980 SC 1632",
        "scc_citation": "(1980) 2 SCC 565",
        "scr_citation": "[1980] 3 S.C.R. 383",
        "title": "Gurbaksh Singh Sibbia v. State of Punjab",
        "petitioner": "Gurbaksh Singh Sibbia",
        "respondent": "State of Punjab",
        "decision_date": "1980-04-09",
        "year": 1980,
        "judge_coram": "Y.V. Chandrachud; P.N. Bhagwati; N.L. Untwalia; R.S. Pathak; O. Chinnappa Reddy",
        "author_judge": "Y.V. Chandrachud",
        "bench_strength": 5,
        "disposal_nature": "Allowed",
        "case_type": "Criminal Appeal",
        "acts_cited": ["CrPC", "Constitution"],
        "pdf_s3_key": None,
        "cited_by_count": 750,
        "doctrine_summary": (
            "Foundational 5-Judge Constitution Bench governing anticipatory bail under Section 438 CrPC "
            "(now Section 482 BNSS): judicial discretion under Section 438 should not be cabined by "
            "rigid conditions or blanket duration limits; registration of an FIR is not a condition "
            "precedent so long as reasonable apprehension of arrest is shown."
        ),
    },
    {
        "match_cnr": None,
        "match_insc": "2020 INSC 106",
        "match_title": "sushila aggarwal",
        "case_id": "sc:2020:sushila-aggarwal",
        "neutral_citation": "2020 INSC 106",
        "air_citation": "AIR 2020 SC 831",
        "scc_citation": "(2020) 5 SCC 1",
        "scr_citation": None,
        "title": "Sushila Aggarwal v. State (NCT of Delhi)",
        "petitioner": "Sushila Aggarwal",
        "respondent": "State (NCT of Delhi)",
        "decision_date": "2020-01-29",
        "year": 2020,
        "judge_coram": "Arun Mishra; Indira Banerjee; Vineet Saran; M.R. Shah; S. Ravindra Bhat",
        "author_judge": "S. Ravindra Bhat",
        "bench_strength": 5,
        "disposal_nature": "Reference answered",
        "case_type": "Special Leave Petition (Criminal)",
        "acts_cited": ["CrPC", "BNSS", "Constitution"],
        "pdf_s3_key": None,
        "cited_by_count": 520,
        "doctrine_summary": (
            "5-Judge Constitution Bench reaffirming Sibbia on anticipatory bail under Section 438 CrPC "
            "(now Section 482 BNSS): protection granted under Section 438 should not invariably be "
            "limited to a fixed period or end upon filing of chargesheet/summons, and normally enures "
            "till the end of trial unless specific circumstances justify time-limited bail."
        ),
    },
    {
        "match_cnr": None,
        "match_insc": "2014 INSC 463",
        "match_title": "arnesh kumar",
        "case_id": "sc:2014:arnesh-kumar",
        "neutral_citation": "2014 INSC 463",
        "air_citation": "AIR 2014 SC 2756",
        "scc_citation": "(2014) 8 SCC 273",
        "scr_citation": None,
        "title": "Arnesh Kumar v. State of Bihar",
        "petitioner": "Arnesh Kumar",
        "respondent": "State of Bihar",
        "decision_date": "2014-07-02",
        "year": 2014,
        "judge_coram": "Chandramauli Kr. Prasad; Pinaki Chandra Ghose",
        "author_judge": "Chandramauli Kr. Prasad",
        "bench_strength": 2,
        "disposal_nature": "Disposed of",
        "case_type": "Criminal Appeal",
        "acts_cited": ["CrPC", "IPC", "Constitution"],
        "pdf_s3_key": None,
        "cited_by_count": 890,
        "doctrine_summary": (
            "Laid down mandatory arrest safeguards under Sections 41 and 41A CrPC (now Section 35 BNSS) "
            "for offences punishable with imprisonment up to 7 years (including Section 498A IPC): "
            "police must not arrest automatically and must serve a Section 41A notice of appearance "
            "and record written reasons under Section 41(1)(b)(ii), verified by the Magistrate."
        ),
    },
    {
        "match_cnr": None,
        "match_insc": "2020 INSC 453",
        "match_title": "arjun panditrao khotkar",
        "case_id": "sc:2020:arjun-khotkar",
        "neutral_citation": "2020 INSC 453",
        "air_citation": "AIR 2020 SC 4908",
        "scc_citation": "(2020) 7 SCC 1",
        "scr_citation": None,
        "title": "Arjun Panditrao Khotkar v. Kailash Kushanrao Gorantyal",
        "petitioner": "Arjun Panditrao Khotkar",
        "respondent": "Kailash Kushanrao Gorantyal",
        "decision_date": "2020-07-14",
        "year": 2020,
        "judge_coram": "R.F. Nariman; S. Ravindra Bhat; V. Ramasubramanian",
        "author_judge": "R.F. Nariman",
        "bench_strength": 3,
        "disposal_nature": "Disposed of",
        "case_type": "Civil Appeal",
        "acts_cited": ["EvidenceAct", "BSA", "RPA1951"],
        "pdf_s3_key": None,
        "cited_by_count": 640,
        "doctrine_summary": (
            "3-Judge Bench clarifying Section 65B(4) of the Indian Evidence Act, 1872 (now Section 63 "
            "BSA): a Section 65B(4) certificate is a mandatory condition precedent for admitting "
            "secondary electronic evidence (affirming Anvar P.V. v. P.K. Basheer and overruling "
            "Shafhi Mohammad), dispensable only when the original electronic device itself is produced."
        ),
    },
    {
        "match_cnr": None,
        "match_insc": "2019 INSC 95",
        "match_title": "swiss ribbons",
        "case_id": "sc:2019:swiss-ribbons",
        "neutral_citation": "2019 INSC 95",
        "air_citation": "AIR 2019 SC 739",
        "scc_citation": "(2019) 4 SCC 17",
        "scr_citation": None,
        "title": "Swiss Ribbons Pvt. Ltd. v. Union of India",
        "petitioner": "Swiss Ribbons Pvt. Ltd.",
        "respondent": "Union of India",
        "decision_date": "2019-01-25",
        "year": 2019,
        "judge_coram": "R.F. Nariman; Navin Sinha",
        "author_judge": "R.F. Nariman",
        "bench_strength": 2,
        "disposal_nature": "Dismissed",
        "case_type": "Writ Petition (Civil)",
        "acts_cited": ["IBC", "Constitution"],
        "pdf_s3_key": None,
        "cited_by_count": 580,
        "doctrine_summary": (
            "Upheld the constitutional validity of the Insolvency and Bankruptcy Code, 2016 in its "
            "entirety, including intelligible differentia between financial and operational creditors, "
            "Section 12A withdrawal, Section 29A eligibility bar, and Section 14 moratorium aimed at "
            "maximizing asset value and revival of the corporate debtor."
        ),
    },
)


def _is_clean_text(text: str) -> bool:
    if not text or not text.strip():
        return False
    if _HTML_OR_ERROR_PREFIX_RE.search(text):
        return False
    if _COMMERCIAL_HEADNOTE_RE.search(text):
        return False
    printable = sum(1 for ch in text if ch.isprintable() or ch in "\n\r\t")
    return (printable / len(text)) >= 0.90


def _clean_text_or_none(text: str, *, doc_id: str, metadata: dict[str, Any] | None = None) -> str | None:
    norm = normalize_legal_record(text, doc_id=doc_id, metadata=metadata)
    if norm.blocked_by_publication_ban:
        return None
    if not _is_clean_text(norm.cleaned_text):
        return None
    return norm.cleaned_text


def _normalize_date_str(val: Any) -> str | None:
    if val is None:
        return None
    if isinstance(val, datetime):
        return val.date().isoformat()
    if isinstance(val, date):
        return val.isoformat()
    raw = str(val).strip()
    if not raw or raw.lower() in ("none", "null", "nat", "nan"):
        return None
    if len(raw) >= 10 and raw[4] == "-" and raw[7] == "-":
        try:
            date.fromisoformat(raw[:10])
            return raw[:10]
        except ValueError:
            pass
    m = re.match(r"^(\d{2})[-/](\d{2})[-/](\d{4})", raw)
    if m:
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            return date(y, mo, d).isoformat()
        except ValueError:
            return None
    return None


def _map_act_status(raw_status: Any, *, in_force: Any = None, title: str = "") -> str:
    s = str(raw_status or "").strip().lower()
    if s in _VALID_ACT_STATUSES:
        return s
    if s == "spent":
        return "superseded"
    if in_force is False or "(rep." in title.lower():
        return "repealed"
    return "in_force"


def _map_provision_kind(section_type: Any) -> str:
    st = str(section_type or "").strip().lower()
    if st == "article":
        return "article"
    if st == "schedule":
        return "schedule"
    if st == "preamble":
        return "preamble"
    if st == "regulation":
        return "regulation"
    if st == "rule":
        return "rule"
    return "section"


def _derive_short_name(title: str, raw_act_id: str, used: set[str], *, prefix: str = "") -> str:
    base = re.sub(r"^The\s+", "", title.strip(), flags=re.IGNORECASE)
    base = re.sub(r"\s*\((?:Rep\.|Act\s+No\.?)[^)]*\)\s*$", "", base, flags=re.IGNORECASE)
    base = re.sub(r"[,\s]+$", "", base)
    if prefix:
        base = f"{prefix}: {base}"
    if len(base) > 96:
        base = base[:93].rstrip() + "..."
    if not base:
        base = raw_act_id
    candidate = base
    if candidate.lower() in used:
        candidate = f"{base[:80]} [{raw_act_id}]"
    if candidate.lower() in used:
        digest = hashlib.sha1(raw_act_id.encode("utf-8")).hexdigest()[:6]
        candidate = f"{base[:72]} [{digest}]"
    used.add(candidate.lower())
    return candidate


def _clean_title_str(raw: str) -> str:
    s = re.sub(r"##+\s*", "", raw or "")
    s = re.sub(r"\*\*([^*]+)\*\*", r"\1", s)
    s = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"\1", s)
    s = s.replace("**", "").replace("*", "")
    return s.strip(" .—-*")


def _clean_vaquill_provision_text(raw_text: str) -> tuple[str, str | None]:
    """Strip Vaquill boilerplate headers, markdown formatting, repeated headings, and India Code footnotes."""
    text = (raw_text or "").replace("\r\n", "\n").replace("\r", "\n")

    chap_title: str | None = None
    m_chap = _CHAPTER_TITLE_RE.search(text)
    if m_chap:
        ct = _clean_title_str(m_chap.group(1))
        if ct:
            chap_title = ct

    text = _VAQUILL_HEADER_RE.sub("", text, count=1).lstrip()
    text = re.sub(r"^Act:\s*[^\n]+(?:\n|$)", "", text).lstrip()
    text = re.sub(r"^Chapter\s+[^\n|]+\|\s*", "", text, flags=re.IGNORECASE).lstrip()

    text = re.sub(r"==> picture \[[^\]]*\] intentionally omitted <==", "", text)
    text = re.sub(r"##+\s*", "", text)
    text = re.sub(r"(?m)^#+\s*$", "", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = text.replace("**", "")
    text = re.sub(r"(?<!\w)_([^_\n]+)_(?!\w)", r"\1", text)
    text = re.sub(r"(?m)^>\s*", "", text)

    text = _LEADING_SECTION_LINE_RE.sub("", text, count=1)
    text = _INDIA_CODE_FOOTNOTE_RE.sub("", text)
    text = _INLINE_FOOTNOTE_SUP_RE.sub("[", text)
    text = re.sub(r"\s+\.\s*—\s*", ".—", text)
    text = re.sub(r"\(\s*(\d{1,2}|[a-z])\s*\)", r"(\1)", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text, chap_title


def _chunk_part_num(chunk_id: str) -> int:
    m = re.search(r"_p(\d+)$", chunk_id)
    return int(m.group(1)) if m else -1


def _extract_section_heading(text: str, default_title: str, act_title: str) -> str:
    m = _SECTION_HEADING_RE.search(text[:600])
    if m:
        heading = _clean_title_str(m.group(1))
        if heading:
            return heading[:240]
    cleaned_default = _clean_title_str(default_title)
    cleaned_act = _clean_title_str(act_title)
    if cleaned_default and cleaned_default != cleaned_act:
        return cleaned_default[:240]
    return cleaned_default[:240] or "Section"


def _iter_vaquill_rows(parquet_path: Path, columns: list[str], order_by: str = "") -> Iterable[tuple[Any, ...]]:
    con = duckdb.connect(":memory:")
    try:
        escaped = str(parquet_path).replace("'", "''")
        cols_sql = ", ".join(columns)
        order_sql = f" ORDER BY {order_by}" if order_by else ""
        cur = con.execute(f"SELECT {cols_sql} FROM read_parquet('{escaped}'){order_sql}")
        while True:
            batch = cur.fetchmany(10_000)
            if not batch:
                break
            yield from batch
    finally:
        con.close()


def _parse_sc_neutral_citation(case_id_raw: str, nc_display: str) -> str | None:
    m = _INSC_RE.search(case_id_raw)
    if m:
        return f"{m.group(1)} INSC {int(m.group(2))}"
    m2 = _INSC_COMPACT_RE.match(nc_display.strip())
    if m2:
        return f"{m2.group(1)} INSC {int(m2.group(2))}"
    return None


def _match_landmark(
    *,
    cnr: str | None,
    neutral: str | None,
    title: str,
) -> dict[str, Any] | None:
    cnr_up = (cnr or "").strip().upper()
    nc_up = (neutral or "").strip().upper()
    title_low = title.lower()
    for lm in _CANONICAL_LANDMARKS:
        match_cnr = lm.get("match_cnr")
        if cnr_up and match_cnr and cnr_up == match_cnr:
            return lm
        match_insc = lm.get("match_insc")
        if nc_up and match_insc and nc_up == match_insc:
            return lm
        if lm["match_title"] in title_low and str(lm["year"]) in (nc_up or cnr_up or title_low):
            return lm
    return None


def load_staged_ingest_bundle(
    work_dir: Path | str,
    *,
    commercial_safe_only: bool = False,
    include_hc: bool = True,
) -> IngestInputBundle:
    """Assemble an `IngestInputBundle` from real staged Vaquill & Dattam Parquets in `work_dir/raw`."""
    _ = commercial_safe_only
    wdir = Path(work_dir)
    raw_dir = wdir / "raw"

    # Seed canonical core acts, aliases, Constitution, CrPC, and verified edges
    mini = create_mini_ingest_input()
    acts_by_id: dict[str, dict[str, Any]] = {}
    used_short_names: set[str] = set()
    aliases: dict[str, str] = {}

    for act in mini.acts:
        if act["storage_tier"] == "sqlite_core":
            aid = str(act["act_id"])
            acts_by_id[aid] = dict(act)
            used_short_names.add(str(act["short_name"]).lower())

    extra_core_acts = [
        {
            "act_id": "ni-act-1881",
            "short_name": "NIAct",
            "full_name": "The Negotiable Instruments Act, 1881",
            "year": 1881,
            "act_number": "Act No. 26 of 1881",
            "jurisdiction": "Central",
            "category": "central_act",
            "status": "in_force",
            "commencement_date": "1882-03-01",
            "repeal_date": None,
            "replaced_by_act": None,
            "storage_tier": "sqlite_core",
            "source_id": "vaquill_central_legislation",
            "commercial_safe": 1,
            "as_of": "2026-11-22",
        },
        {
            "act_id": "contract-act-1872",
            "short_name": "ContractAct",
            "full_name": "The Indian Contract Act, 1872",
            "year": 1872,
            "act_number": "Act No. 9 of 1872",
            "jurisdiction": "Central",
            "category": "central_act",
            "status": "in_force",
            "commencement_date": "1872-09-01",
            "repeal_date": None,
            "replaced_by_act": None,
            "storage_tier": "sqlite_core",
            "source_id": "vaquill_central_legislation",
            "commercial_safe": 1,
            "as_of": "2026-11-22",
        },
        {
            "act_id": "companies-act-2013",
            "short_name": "CompaniesAct2013",
            "full_name": "The Companies Act, 2013",
            "year": 2013,
            "act_number": "Act No. 18 of 2013",
            "jurisdiction": "Central",
            "category": "central_act",
            "status": "in_force",
            "commencement_date": "2013-08-30",
            "repeal_date": None,
            "replaced_by_act": None,
            "storage_tier": "sqlite_core",
            "source_id": "vaquill_central_legislation",
            "commercial_safe": 1,
            "as_of": "2026-11-22",
        },
        {
            "act_id": "arbitration-act-1996",
            "short_name": "ArbitrationAct",
            "full_name": "The Arbitration and Conciliation Act, 1996",
            "year": 1996,
            "act_number": "Act No. 26 of 1996",
            "jurisdiction": "Central",
            "category": "central_act",
            "status": "in_force",
            "commencement_date": "1996-08-22",
            "repeal_date": None,
            "replaced_by_act": None,
            "storage_tier": "sqlite_core",
            "source_id": "vaquill_central_legislation",
            "commercial_safe": 1,
            "as_of": "2026-11-22",
        },
        {
            "act_id": "ibc-2016",
            "short_name": "IBC",
            "full_name": "The Insolvency and Bankruptcy Code, 2016",
            "year": 2016,
            "act_number": "Act No. 31 of 2016",
            "jurisdiction": "Central",
            "category": "central_act",
            "status": "in_force",
            "commencement_date": "2016-05-28",
            "repeal_date": None,
            "replaced_by_act": None,
            "storage_tier": "sqlite_core",
            "source_id": "vaquill_central_legislation",
            "commercial_safe": 1,
            "as_of": "2026-11-22",
        },
    ]
    for act in extra_core_acts:
        aid = str(act["act_id"])
        if aid not in acts_by_id:
            acts_by_id[aid] = act
            used_short_names.add(str(act["short_name"]).lower())

    for alias_k, target_aid in mini.aliases:
        aliases[alias_k] = target_aid
    for alias_k, target_aid in (
        ("crpc", "crpc-1973"),
        ("code of criminal procedure", "crpc-1973"),
        ("ni act", "ni-act-1881"),
        ("niact", "ni-act-1881"),
        ("negotiable instruments act", "ni-act-1881"),
        ("contract act", "contract-act-1872"),
        ("indian contract act", "contract-act-1872"),
        ("ica", "contract-act-1872"),
        ("companies act", "companies-act-2013"),
        ("companies act 2013", "companies-act-2013"),
        ("companiesact", "companies-act-2013"),
        ("companiesact2013", "companies-act-2013"),
        ("arbitration", "arbitration-act-1996"),
        ("arbitration act", "arbitration-act-1996"),
        ("arbitration and conciliation act", "arbitration-act-1996"),
        ("arbitration and conciliation act, 1996", "arbitration-act-1996"),
        ("ibc", "ibc-2016"),
        ("insolvency and bankruptcy code", "ibc-2016"),
        ("specific relief act", "specific-relief-1963"),
        ("sra", "specific-relief-1963"),
        ("sebi act", "sebi-act-1992"),
        ("transfer of property act", "transfer-of-property-1882"),
        ("tpa", "transfer-of-property-1882"),
        ("limitation act", "limitation-act-1963"),
    ):
        aliases[alias_k] = target_aid

    # Curated Constitution + CPC O7-R11 + BNSS 531 fallback provisions
    provisions_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
    for p in mini.provisions:
        key = (str(p["act_id"]), str(p["kind"]), str(p["ref_norm"]).upper())
        provisions_by_key[key] = dict(p)

    # Load courts directory for HC / tribunal / regulator names
    court_name_by_code: dict[str, str] = {}
    for c in load_courts_yaml():
        cc = str(c["court_code"])
        cn = str(c["court_name"])
        court_name_by_code[cc] = cn
        if c.get("dattam_code"):
            court_name_by_code[str(c["dattam_code"])] = cn

    # 1. Discover Vaquill Parquets
    vaquill_files = sorted((raw_dir / "vaquill").rglob("*.parquet")) if (raw_dir / "vaquill").is_dir() else []
    central_files = [f for f in vaquill_files if classify_vaquill_file(f.name) == "central"]
    state_files = [f for f in vaquill_files if classify_vaquill_file(f.name) == "state"]
    regulator_files = [f for f in vaquill_files if classify_vaquill_file(f.name) == "regulator"]
    tribunal_files = [f for f in vaquill_files if classify_vaquill_file(f.name) == "tribunal"]

    # 1a. Load Central Acts (`in_central_legislation.parquet`)
    for cfile in central_files:
        # Group chunks per (canon_act_id, kind, ref_norm)
        grouped_chunks: dict[tuple[str, str, str], list[tuple[str, str, str, str, str, str, str]]] = defaultdict(list)
        for (
            raw_act_id,
            chunk_id,
            title,
            chapter,
            section_number,
            section_title,
            text,
            act_status,
            in_force,
            section_type,
            year_val,
            source_url,
        ) in _iter_vaquill_rows(
            cfile,
            [
                "act_id",
                "chunk_id",
                "title",
                "chapter",
                "section_number",
                "section_title",
                "text",
                "act_status",
                "in_force",
                "section_type",
                "year",
                "source_url",
            ],
            order_by="act_id, section_number, chunk_id",
        ):
            raw_aid = str(raw_act_id or "").strip()
            act_title = str(title or "").strip()
            if not raw_aid or not act_title:
                continue

            mapped = _VAQUILL_CENTRAL_ACT_MAP.get(raw_aid)
            if mapped is not None:
                canon_aid, short_name, status, replaced_by, comm_date, rep_date = mapped
                if canon_aid not in acts_by_id:
                    acts_by_id[canon_aid] = {
                        "act_id": canon_aid,
                        "short_name": short_name,
                        "full_name": act_title,
                        "year": int(year_val) if year_val is not None else None,
                        "act_number": None,
                        "jurisdiction": "Central",
                        "category": "central_act",
                        "status": status,
                        "commencement_date": comm_date,
                        "repeal_date": rep_date,
                        "replaced_by_act": replaced_by,
                        "storage_tier": "sqlite_core",
                        "source_id": "vaquill_central_legislation",
                        "commercial_safe": 1,
                        "as_of": "2026-11-22",
                    }
                    used_short_names.add(short_name.lower())
            else:
                canon_aid = raw_aid.lower().replace("_", "-")
                if canon_aid not in acts_by_id:
                    short_name = _derive_short_name(act_title, raw_aid, used_short_names)
                    status = _map_act_status(act_status, in_force=in_force, title=act_title)
                    acts_by_id[canon_aid] = {
                        "act_id": canon_aid,
                        "short_name": short_name,
                        "full_name": act_title,
                        "year": int(year_val) if year_val is not None else None,
                        "act_number": None,
                        "jurisdiction": "Central",
                        "category": "central_act",
                        "status": status,
                        "commencement_date": None,
                        "repeal_date": "2024-07-01" if status == "repealed" else None,
                        "replaced_by_act": None,
                        "storage_tier": "sqlite_core",
                        "source_id": "vaquill_central_legislation",
                        "commercial_safe": 1,
                        "as_of": "2026-11-22",
                    }
                    aliases.setdefault(act_title.lower(), canon_aid)
                    no_the = re.sub(r"^the\s+", "", act_title.lower()).strip()
                    aliases.setdefault(no_the, canon_aid)
                else:
                    short_name = str(acts_by_id[canon_aid]["short_name"])
                    status = str(acts_by_id[canon_aid]["status"])

            sec_num = str(section_number or "").strip()
            sec_type = str(section_type or "section").strip()
            kind = _map_provision_kind(sec_type)
            if sec_num:
                ref = sec_num
            elif sec_type:
                ref = sec_type.replace("_", " ").title()
            else:
                ref = str(chunk_id or "1")
            ref_norm = ref.upper()

            body = str(text or "")
            if not body.strip():
                continue

            grouped_chunks[(canon_aid, kind, ref_norm)].append(
                (
                    str(chunk_id or ""),
                    ref,
                    str(chapter or "").strip(),
                    str(section_title or "").strip(),
                    act_title,
                    body,
                    str(source_url or VAQUILL_CENTRAL_LEGISLATION_URL),
                )
            )

        for (canon_aid, kind, ref_norm), items in grouped_chunks.items():
            # Preserve curated Constitution if present
            if (canon_aid, kind, ref_norm) in provisions_by_key and canon_aid == "constitution":
                continue

            stems: dict[str, list[tuple[str, str, str, str, str, str, str]]] = defaultdict(list)
            for item in items:
                base_stem = re.sub(r"_p\d+$", "", item[0])
                stem = f"{base_stem}::parts" if re.search(r"_p\d+$", item[0]) else f"{item[0]}::single"
                stems[stem].append(item)

            best_score = float("-inf")
            best_items: list[tuple[str, str, str, str, str, str, str]] = []
            best_parts: list[str] = []
            best_chap_title: str | None = None

            for stem, stem_items in stems.items():
                stem_items.sort(key=lambda x: (_chunk_part_num(x[0]), x[0]))
                stem_parts: list[str] = []
                stem_chap_title: str | None = None
                for item in stem_items:
                    chunk_cleaned, ct = _clean_vaquill_provision_text(item[5])
                    if ct and not stem_chap_title:
                        stem_chap_title = ct
                    if chunk_cleaned:
                        stem_parts.append(chunk_cleaned)
                joined = "\n\n".join(stem_parts)
                first_item = stem_items[0]
                raw_sec_title = _clean_title_str(first_item[3])
                act_title_clean = _clean_title_str(first_item[4])
                sec_heading = _extract_section_heading(first_item[5], first_item[3], first_item[4])

                score = 0.0
                if not joined.strip():
                    score -= 100.0
                if re.search(r"_d\d+$", stem):
                    score -= 50.0
                if _AMEND_BOILERPLATE_RE.search(joined) or len(_TOC_SECTION_HEADING_RE.findall(joined)) >= 3:
                    score -= 40.0
                if len(stem_items) > 1 or any(re.search(r"_p\d+$", it[0]) for it in stem_items):
                    score += 25.0
                if (
                    (raw_sec_title and raw_sec_title.lower() != act_title_clean.lower())
                    or (
                        sec_heading
                        and sec_heading.lower() != act_title_clean.lower()
                        and sec_heading != "Section"
                    )
                ):
                    score += 10.0
                score += min(len(joined), 4000) / 1000.0

                if score > best_score:
                    best_score = score
                    best_items = stem_items
                    best_parts = stem_parts
                    best_chap_title = stem_chap_title

            if not best_items:
                continue

            first = best_items[0]
            ref = first[1]
            chap = first[2] or None
            sec_title = _extract_section_heading(first[5], first[3], first[4])
            src_url = first[6] or VAQUILL_CENTRAL_LEGISLATION_URL
            full_text = "\n\n".join(best_parts)

            doc_id = f"{canon_aid}:{ref}" if kind == "section" else f"{canon_aid}:{kind[:4]}:{ref_norm}"
            cleaned = _clean_text_or_none(full_text, doc_id=doc_id, metadata={"act_id": canon_aid})
            if not cleaned:
                continue

            act_meta = acts_by_id[canon_aid]
            act_status = str(act_meta["status"])
            prov_status = "repealed" if act_status == "repealed" else "in_force"
            provisions_by_key[(canon_aid, kind, ref_norm)] = {
                "doc_id": doc_id,
                "act_id": canon_aid,
                "act_short": str(act_meta["short_name"]),
                "kind": kind,
                "ref": ref,
                "ref_norm": ref_norm,
                "chapter_num": chap,
                "chapter_title": best_chap_title,
                "part": None,
                "title": sec_title,
                "text": cleaned,
                "status": prov_status,
                "commencement_date": act_meta.get("commencement_date"),
                "repeal_date": act_meta.get("repeal_date"),
                "source_id": "vaquill_central_legislation",
                "source_url": src_url,
                "commercial_safe": 1,
            }

    # Overlay curated bare-act statutory provisions (overrides truncated Vaquill entries & adds CrPC)
    for cp in load_curated_provisions_yaml():
        aid = str(cp["act_id"])
        kind = str(cp.get("kind") or "section")
        ref_norm = str(cp.get("ref_norm") or cp["ref"]).upper()
        if aid not in acts_by_id:
            acts_by_id[aid] = {
                "act_id": aid,
                "short_name": str(cp.get("act_short") or aid),
                "full_name": str(cp.get("act_full_name") or cp.get("act_short") or aid),
                "year": cp.get("year"),
                "act_number": cp.get("act_number"),
                "jurisdiction": "Central",
                "category": "constitution" if aid == "constitution" else "central_act",
                "status": "repealed" if aid in ("ipc-1860", "crpc-1973", "iea-1872") else "in_force",
                "commencement_date": cp.get("commencement_date"),
                "repeal_date": cp.get("repeal_date"),
                "replaced_by_act": cp.get("replaced_by_act"),
                "storage_tier": "sqlite_core",
                "source_id": str(cp.get("source_id") or "vaquill_central_legislation"),
                "commercial_safe": 1,
                "as_of": "2026-11-22",
            }
        provisions_by_key[(aid, kind, ref_norm)] = dict(cp)

    # 1b. Load State Acts (36 packs -> acts + state_regulator_provisions)
    state_regulator_provisions: list[dict[str, Any]] = []
    for sfile in state_files:
        slug = sfile.name.removeprefix("in_").removesuffix("_legislation.parquet")
        juris = _STATE_CODE_MAP.get(slug, slug.upper()[:8])
        seen_state_acts: set[str] = set()
        for (
            raw_act_id,
            chunk_id,
            title,
            section_number,
            section_title,
            text,
            act_status,
            in_force,
            section_type,
            year_val,
            source_url,
        ) in _iter_vaquill_rows(
            sfile,
            [
                "act_id",
                "chunk_id",
                "title",
                "section_number",
                "section_title",
                "text",
                "act_status",
                "in_force",
                "section_type",
                "year",
                "source_url",
            ],
        ):
            raw_aid = str(raw_act_id or "").strip()
            act_title = str(title or "").strip()
            if not raw_aid or not act_title:
                continue
            canon_aid = f"{juris.lower()}:{raw_aid.lower().replace('_', '-')}"
            if canon_aid not in acts_by_id:
                short_name = _derive_short_name(act_title, canon_aid, used_short_names, prefix=juris)
                status = _map_act_status(act_status, in_force=in_force, title=act_title)
                acts_by_id[canon_aid] = {
                    "act_id": canon_aid,
                    "short_name": short_name,
                    "full_name": act_title,
                    "year": int(year_val) if year_val is not None else None,
                    "act_number": None,
                    "jurisdiction": juris,
                    "category": "state_act",
                    "status": status,
                    "commencement_date": None,
                    "repeal_date": None,
                    "replaced_by_act": None,
                    "storage_tier": "lance_bucket",
                    "source_id": "vaquill_state_legislation",
                    "commercial_safe": 1,
                    "as_of": "2026-11-22",
                }
                seen_state_acts.add(canon_aid)
            else:
                short_name = str(acts_by_id[canon_aid]["short_name"])
                status = str(acts_by_id[canon_aid]["status"])

            doc_id = str(chunk_id or f"{canon_aid}:{section_number or '0'}")
            raw_body = str(text or "")
            ref = str(section_number or "").strip() or str(section_type or "section")
            sec_heading = _extract_section_heading(raw_body, str(section_title or ""), act_title)
            clean_body, _ = _clean_vaquill_provision_text(raw_body)
            cleaned = _clean_text_or_none(clean_body, doc_id=doc_id)
            if not cleaned:
                continue
            state_regulator_provisions.append(
                {
                    "doc_id": doc_id,
                    "act_id": canon_aid,
                    "short_name": short_name,
                    "jurisdiction": juris,
                    "category": "state_act",
                    "kind": _map_provision_kind(section_type),
                    "ref": ref,
                    "title": sec_heading,
                    "status": "repealed" if status == "repealed" else "in_force",
                    "effective_date": "",
                    "text": cleaned,
                    "commercial_safe": True,
                    "terms_status": "attribution_required",
                    "license_spdx": "CC-BY-4.0",
                    "source_id": "vaquill_state_legislation",
                    "source_url": str(
                        source_url or f"https://oss-data-in.vaquill.ai/v2026.08.1/{sfile.name}"
                    ),
                }
            )

    # 1c. Load Regulators (12 packs -> acts + state_regulator_provisions + regulator_order_rows)
    regulator_order_rows: list[dict[str, Any]] = []
    for rfile in regulator_files:
        slug = rfile.name.removeprefix("in_").removesuffix("_regulations.parquet")
        reg_code = _REGULATOR_CODE_MAP.get(slug, slug.upper())
        reg_court_name = court_name_by_code.get(reg_code, reg_code)
        seen_reg_instruments: set[str] = set()
        for (
            raw_act_id,
            chunk_id,
            title,
            section_number,
            section_title,
            text,
            act_status,
            in_force,
            instrument_type,
            year_val,
            source_url,
        ) in _iter_vaquill_rows(
            rfile,
            [
                "act_id",
                "chunk_id",
                "title",
                "section_number",
                "section_title",
                "text",
                "act_status",
                "in_force",
                "instrument_type",
                "year",
                "source_url",
            ],
        ):
            raw_aid = str(raw_act_id or "").strip()
            act_title = str(title or "").strip()
            if not raw_aid or not act_title:
                continue
            canon_aid = f"{reg_code.lower()}:{raw_aid.lower().replace('_', '-')}"
            if canon_aid not in acts_by_id:
                short_name = _derive_short_name(act_title, canon_aid, used_short_names, prefix=reg_code)
                status = _map_act_status(act_status, in_force=in_force, title=act_title)
                acts_by_id[canon_aid] = {
                    "act_id": canon_aid,
                    "short_name": short_name,
                    "full_name": act_title,
                    "year": int(year_val) if year_val is not None else None,
                    "act_number": None,
                    "jurisdiction": reg_code,
                    "category": "regulator",
                    "status": status,
                    "commencement_date": None,
                    "repeal_date": None,
                    "replaced_by_act": None,
                    "storage_tier": "lance_bucket",
                    "source_id": "vaquill_regulators",
                    "commercial_safe": 1,
                    "as_of": "2026-11-22",
                }
            else:
                short_name = str(acts_by_id[canon_aid]["short_name"])
                status = str(acts_by_id[canon_aid]["status"])

            if canon_aid not in seen_reg_instruments:
                seen_reg_instruments.add(canon_aid)
                yr_int = int(year_val) if year_val is not None else 2024
                regulator_order_rows.append(
                    {
                        "case_id": f"reg:{reg_code.lower()}:{raw_aid}",
                        "regulator_code": reg_code,
                        "court_code": reg_code,
                        "court_name": reg_court_name,
                        "title": act_title[:300],
                        "decision_date": f"{yr_int}-01-01",
                        "year": yr_int,
                        "disposal_nature": status,
                        "case_type": str(instrument_type or "Regulation"),
                        "act": short_name,
                        "commercial_safe": True,
                    }
                )

            doc_id = str(chunk_id or f"{canon_aid}:{section_number or '0'}")
            raw_body = str(text or "")
            ref = str(section_number or "").strip() or "1"
            sec_heading = _extract_section_heading(raw_body, str(section_title or ""), act_title)
            clean_body, _ = _clean_vaquill_provision_text(raw_body)
            cleaned = _clean_text_or_none(clean_body, doc_id=doc_id)
            if not cleaned:
                continue
            state_regulator_provisions.append(
                {
                    "doc_id": doc_id,
                    "act_id": canon_aid,
                    "short_name": short_name,
                    "jurisdiction": reg_code,
                    "category": "regulator",
                    "kind": "regulation",
                    "ref": ref,
                    "title": sec_heading,
                    "status": "in_force" if status == "in_force" else "repealed",
                    "effective_date": "",
                    "text": cleaned,
                    "commercial_safe": True,
                    "terms_status": "attribution_required",
                    "license_spdx": "CC-BY-4.0",
                    "source_id": "vaquill_regulators",
                    "source_url": str(
                        source_url or f"https://oss-data-in.vaquill.ai/v2026.08.1/{rfile.name}"
                    ),
                }
            )

    # 1d. Load Tribunals (15 packs -> tribunal_rows)
    tribunal_rows: list[dict[str, Any]] = []
    for tfile in tribunal_files:
        slug = tfile.name.removeprefix("in_").removesuffix("_tribunal_matters.parquet")
        forum_code = _TRIBUNAL_CODE_MAP.get(slug, slug.upper())
        court_name = court_name_by_code.get(forum_code, forum_code)
        for (
            trib_val,
            case_id_val,
            case_number,
            title,
            petitioner,
            respondent,
            bench,
            judges,
            decision_date,
            year_val,
            doc_type,
        ) in _iter_vaquill_rows(
            tfile,
            [
                "tribunal",
                "case_id",
                "case_number",
                "title",
                "petitioner",
                "respondent",
                "bench",
                "judges",
                "decision_date",
                "year",
                "doc_type",
            ],
        ):
            cid = str(case_id_val or "").strip()
            if not cid:
                continue
            f_code = _TRIBUNAL_CODE_MAP.get(str(trib_val or "").strip().lower(), forum_code)
            raw_title = str(title or case_number or cid).strip()
            clean_title = _clean_text_or_none(raw_title, doc_id=cid) or cid
            dec_iso = _normalize_date_str(decision_date)
            yr_int = int(year_val) if year_val is not None else (int(dec_iso[:4]) if dec_iso else 0)
            judge_str = "; ".join(str(j) for j in (judges or []) if j) if isinstance(judges, list) else str(judges or "")
            tribunal_rows.append(
                {
                    "case_id": cid,
                    "forum_code": f_code,
                    "court_code": f_code,
                    "court_name": court_name_by_code.get(f_code, court_name),
                    "bench": str(bench or ""),
                    "title": clean_title,
                    "petitioner": str(petitioner or ""),
                    "respondent": str(respondent or ""),
                    "judge": judge_str,
                    "decision_date": dec_iso or "",
                    "year": yr_int,
                    "disposal_nature": "",
                    "case_type": str(doc_type or "Order"),
                    "act": "",
                    "commercial_safe": True,
                }
            )

    # 2. Load Dattam Supreme Court metadata Parquets
    sc_dir = raw_dir / "indian-supreme-court-judgments" / "metadata" / "parquet"
    sc_files = sorted(sc_dir.rglob("*.parquet")) if sc_dir.is_dir() else []
    sc_by_id: dict[str, dict[str, Any]] = {}
    sc_by_cnr: dict[str, str] = {}
    sc_by_neutral: dict[str, str] = {}
    matched_landmark_ids: set[str] = set()

    def _merge_sc_entry(existing: dict[str, Any], incoming: dict[str, Any], *, prefer_incoming: bool = False) -> None:
        for k in (
            "cnr",
            "neutral_citation",
            "air_citation",
            "scc_citation",
            "scr_citation",
            "petitioner",
            "respondent",
            "decision_date",
            "judge_coram",
            "author_judge",
            "disposal_nature",
            "case_type",
            "pdf_s3_bucket",
            "pdf_s3_key",
        ):
            if prefer_incoming and incoming.get(k) is not None:
                existing[k] = incoming[k]
            elif not existing.get(k) and incoming.get(k):
                existing[k] = incoming[k]

        if prefer_incoming and incoming.get("title"):
            existing["title"] = incoming["title"]
        elif len(str(incoming.get("title") or "")) > len(str(existing.get("title") or "")):
            existing["title"] = incoming["title"]

        if prefer_incoming and incoming.get("source_url"):
            existing["source_url"] = incoming["source_url"]
        elif incoming.get("pdf_s3_key") and existing.get("pdf_s3_key") == incoming.get("pdf_s3_key"):
            existing["source_url"] = incoming["source_url"]

        existing_text = str(existing.get("text") or "").strip()
        incoming_text = str(incoming.get("text") or "").strip()
        if not existing_text and incoming_text:
            existing["text"] = incoming_text
        elif incoming_text and incoming_text not in existing_text:
            if prefer_incoming:
                existing["text"] = f"{incoming_text}\n\n{existing_text}"
            elif len(incoming_text) > len(existing_text):
                existing["text"] = incoming_text

        merged_acts = list(existing.get("acts_cited") or [])
        for a in incoming.get("acts_cited") or []:
            if a not in merged_acts:
                merged_acts.append(a)
        existing["acts_cited"] = merged_acts

        if prefer_incoming:
            for k in ("bench_strength", "cited_by_count", "year"):
                if incoming.get(k):
                    existing[k] = incoming[k]
        else:
            dec_iso = str(existing.get("decision_date") or incoming.get("decision_date") or "")
            if len(dec_iso) >= 4 and dec_iso[:4].isdigit():
                dec_yr = int(dec_iso[:4])
                if existing.get("year") != dec_yr and incoming.get("year") == dec_yr:
                    existing["year"] = dec_yr
                    if incoming.get("pdf_s3_key"):
                        existing["pdf_s3_bucket"] = incoming.get("pdf_s3_bucket")
                        existing["pdf_s3_key"] = incoming.get("pdf_s3_key")
                        existing["source_url"] = incoming.get("source_url")

    for sc_file in sc_files:
        ym = _YEAR_DIR_RE.search(str(sc_file))
        dir_year = int(ym.group(1)) if ym else None
        for (
            title,
            petitioner,
            respondent,
            description,
            judge,
            author_judge,
            citation,
            raw_case_id,
            cnr,
            decision_date,
            disposal_nature,
            available_languages,
            path_val,
            nc_display,
            year_val,
        ) in _iter_vaquill_rows(
            sc_file,
            [
                "title",
                "petitioner",
                "respondent",
                "description",
                "judge",
                "author_judge",
                "citation",
                "case_id",
                "cnr",
                "decision_date",
                "disposal_nature",
                "available_languages",
                "path",
                "nc_display",
                "year",
            ],
        ):
            raw_title = str(title or "").strip()
            if not raw_title:
                continue
            raw_cid = str(raw_case_id or "").strip()
            cnr_str = str(cnr or "").strip() or None
            path_str = str(path_val or "").strip()
            dec_iso = _normalize_date_str(decision_date)
            yr_int = int(year_val) if year_val is not None else (dir_year or 2024)
            pdf_year = dir_year or yr_int
            neutral = _parse_sc_neutral_citation(raw_cid, str(nc_display or ""))

            cnr_key = cnr_str.upper() if cnr_str else None
            neutral_key = neutral.upper() if neutral else None
            existing_id = (
                (sc_by_cnr.get(cnr_key) if cnr_key else None)
                or (sc_by_neutral.get(neutral_key) if neutral_key else None)
            )

            lm = _match_landmark(cnr=cnr_str, neutral=neutral, title=raw_title)
            if lm is not None:
                case_id = str(lm["case_id"])
                matched_landmark_ids.add(case_id)
                if existing_id and existing_id != case_id and existing_id in sc_by_id:
                    migrated = sc_by_id.pop(existing_id)
                    migrated["case_id"] = case_id
                    sc_by_id[case_id] = migrated
                    for map_ref in (sc_by_cnr, sc_by_neutral):
                        for mk, mv in list(map_ref.items()):
                            if mv == existing_id:
                                map_ref[mk] = case_id
            elif existing_id and existing_id in sc_by_id:
                case_id = existing_id
            else:
                base_cid = cnr_str or raw_cid or path_str
                if not base_cid:
                    base_cid = hashlib.sha1(raw_title.encode("utf-8")).hexdigest()[:12]
                case_id = f"sc:{yr_int}:{base_cid.replace(' ', '_')}"

            clean_sc_title = _clean_text_or_none(raw_title, doc_id=case_id)
            if not clean_sc_title:
                continue

            desc_raw = str(description or "").strip()
            clean_desc = _clean_text_or_none(desc_raw, doc_id=case_id) if desc_raw else ""

            # Parse SCR / AIR / SCC citations
            cit_str = str(citation or "").strip()
            scr_cit = cit_str if "S.C.R" in cit_str.upper() or "SCR" in cit_str.upper() else None
            air_cit = cit_str if cit_str.upper().startswith("AIR ") else None
            scc_cit = cit_str if "SCC" in cit_str.upper() else None

            coram_list = [j.strip() for j in re.split(r"[;,]", str(judge or "")) if j.strip()]
            coram_str = "; ".join(coram_list)
            auth_j = str(author_judge or "").strip() or (coram_list[0] if coram_list else None)
            bench_str = len(coram_list) if coram_list else 1

            langs = str(available_languages or "").upper()
            has_en_pdf = bool(path_str) and (not langs or "ENG" in langs)
            pdf_bucket = "indian-supreme-court-judgments" if has_en_pdf else None
            pdf_key = f"data/pdf/year={pdf_year}/english/{path_str}_EN.pdf" if has_en_pdf else None
            source_url = (
                f"https://indian-supreme-court-judgments.s3.ap-south-1.amazonaws.com/{pdf_key}"
                if pdf_key
                else "https://indian-supreme-court-judgments.s3.ap-south-1.amazonaws.com"
            )

            row_dict: dict[str, Any] = {
                "case_id": case_id,
                "court_code": "SCI",
                "court_name": "Supreme Court of India",
                "bench": None,
                "cnr": cnr_str,
                "neutral_citation": neutral,
                "air_citation": air_cit,
                "scc_citation": scc_cit,
                "scr_citation": scr_cit,
                "title": clean_sc_title,
                "petitioner": str(petitioner or "").strip() or None,
                "respondent": str(respondent or "").strip() or None,
                "decision_date": dec_iso,
                "year": yr_int,
                "judge_coram": coram_str,
                "author_judge": auth_j,
                "bench_strength": bench_str,
                "disposal_nature": str(disposal_nature or "").strip() or None,
                "case_type": None,
                "acts_cited": [],
                "is_reportable": 1,
                "pdf_s3_bucket": pdf_bucket,
                "pdf_s3_key": pdf_key,
                "source_url": source_url,
                "text": clean_desc or "",
                "cited_by_count": 0,
                "source_id": "dattam_sc_judgments",
                "commercial_safe": True,
            }
            if lm is not None:
                doctrine = str(lm.get("doctrine_summary") or "").strip()
                if doctrine and clean_desc and doctrine not in clean_desc:
                    lm_text = f"{doctrine}\n\n{clean_desc}"
                else:
                    lm_text = doctrine or (clean_desc or "")
                lm_updates: dict[str, Any] = {
                    "neutral_citation": lm["neutral_citation"],
                    "air_citation": lm["air_citation"],
                    "scc_citation": lm["scc_citation"],
                    "scr_citation": lm["scr_citation"],
                    "title": lm["title"],
                    "petitioner": lm["petitioner"],
                    "respondent": lm["respondent"],
                    "decision_date": lm["decision_date"],
                    "year": lm["year"],
                    "judge_coram": lm["judge_coram"],
                    "author_judge": lm["author_judge"],
                    "bench_strength": lm["bench_strength"],
                    "disposal_nature": lm["disposal_nature"],
                    "case_type": lm["case_type"],
                    "acts_cited": list(lm["acts_cited"]),
                    "cited_by_count": lm["cited_by_count"],
                    "text": lm_text,
                }
                if lm.get("pdf_s3_key"):
                    lm_updates["pdf_s3_bucket"] = "indian-supreme-court-judgments"
                    lm_updates["pdf_s3_key"] = lm["pdf_s3_key"]
                    lm_updates["source_url"] = (
                        f"https://indian-supreme-court-judgments.s3.ap-south-1.amazonaws.com/{lm['pdf_s3_key']}"
                    )
                row_dict.update(lm_updates)

            if case_id in sc_by_id:
                _merge_sc_entry(sc_by_id[case_id], row_dict, prefer_incoming=(lm is not None))
            else:
                sc_by_id[case_id] = row_dict

            if cnr_key:
                sc_by_cnr[cnr_key] = case_id
            if neutral_key:
                sc_by_neutral[neutral_key] = case_id
            if lm is not None:
                if lm.get("match_cnr"):
                    sc_by_cnr[str(lm["match_cnr"]).upper()] = case_id
                if lm.get("match_insc"):
                    sc_by_neutral[str(lm["match_insc"]).upper()] = case_id

    # Ensure canonical landmarks are always present with doctrine summaries & verified S3 PDF keys
    for lm in _CANONICAL_LANDMARKS:
        cid = str(lm["case_id"])
        doctrine = str(lm.get("doctrine_summary") or "").strip()
        if cid not in sc_by_id:
            lm_pdf_key = lm.get("pdf_s3_key")
            sc_by_id[cid] = {
                "case_id": cid,
                "court_code": "SCI",
                "court_name": "Supreme Court of India",
                "bench": "Constitution Bench" if lm["bench_strength"] >= 5 else "Division Bench",
                "cnr": lm.get("match_cnr"),
                "neutral_citation": lm["neutral_citation"],
                "air_citation": lm["air_citation"],
                "scc_citation": lm["scc_citation"],
                "scr_citation": lm["scr_citation"],
                "title": lm["title"],
                "petitioner": lm["petitioner"],
                "respondent": lm["respondent"],
                "decision_date": lm["decision_date"],
                "year": lm["year"],
                "judge_coram": lm["judge_coram"],
                "author_judge": lm["author_judge"],
                "bench_strength": lm["bench_strength"],
                "disposal_nature": lm["disposal_nature"],
                "case_type": lm["case_type"],
                "acts_cited": list(lm["acts_cited"]),
                "is_reportable": 1,
                "pdf_s3_bucket": "indian-supreme-court-judgments" if lm_pdf_key else None,
                "pdf_s3_key": lm_pdf_key,
                "source_url": (
                    f"https://indian-supreme-court-judgments.s3.ap-south-1.amazonaws.com/{lm_pdf_key}"
                    if lm_pdf_key
                    else "https://indian-supreme-court-judgments.s3.ap-south-1.amazonaws.com"
                ),
                "text": doctrine,
                "cited_by_count": lm["cited_by_count"],
                "source_id": "dattam_sc_judgments",
                "commercial_safe": True,
            }
        elif doctrine and doctrine not in str(sc_by_id[cid].get("text") or ""):
            existing_txt = str(sc_by_id[cid].get("text") or "").strip()
            sc_by_id[cid]["text"] = f"{doctrine}\n\n{existing_txt}" if existing_txt else doctrine

    # 3. Load Dattam High Court metadata Parquets
    hc_dir = raw_dir / "indian-high-court-judgments" / "metadata" / "parquet"
    hc_files = sorted(hc_dir.rglob("*.parquet")) if hc_dir.is_dir() else []
    hc_web_rows: list[dict[str, Any]] = []
    seen_hc_ids: set[str] = set()
    hc_row_count = 0

    if not include_hc and hc_files:
        con = duckdb.connect(":memory:")
        try:
            pq_list = ", ".join("'" + str(f).replace("'", "''") + "'" for f in hc_files)
            row = con.execute(f"SELECT count(*) FROM read_parquet([{pq_list}])").fetchone()
            hc_row_count = int(row[0]) if row else 0
        finally:
            con.close()
    else:
        for hc_file in hc_files:
            for (
                raw_cc,
                title,
                description,
                judge,
                pdf_link,
                cnr,
                decision_date,
                disposal_nature,
                court_col,
                pdf_exists,
                bench,
                year_val,
            ) in _iter_vaquill_rows(
                hc_file,
                [
                    "court_code",
                    "title",
                    "description",
                    "judge",
                    "pdf_link",
                    "cnr",
                    "decision_date",
                    "disposal_nature",
                    "court",
                    "pdf_exists",
                    "bench",
                    "year",
                ],
            ):
                c_code = str(court_col or raw_cc or "").strip().replace("~", "_") or "HC"
                c_name = court_name_by_code.get(c_code, f"High Court ({c_code})")
                cnr_str = str(cnr or "").strip().upper()
                yr_int = int(year_val) if year_val is not None else 2024
                dec_iso = _normalize_date_str(decision_date)
                raw_title = str(title or "").strip()
                if not raw_title:
                    continue
                pdf_l = str(pdf_link or "").strip()
                suffix = cnr_str or hashlib.sha1(f"{raw_title}:{pdf_l}".encode()).hexdigest()[:12]
                case_id = f"hc:{yr_int}:{c_code}:{suffix}"
                if case_id in seen_hc_ids:
                    digest = hashlib.sha1(f"{pdf_l}:{dec_iso}:{len(seen_hc_ids)}".encode()).hexdigest()[:6]
                    case_id = f"{case_id}:{digest}"
                seen_hc_ids.add(case_id)

                clean_hc_title = _clean_text_or_none(raw_title, doc_id=case_id)
                if not clean_hc_title:
                    continue

                desc_plain = _HTML_TAG_RE.sub(" ", str(description or "")).strip()
                clean_desc = _clean_text_or_none(desc_plain, doc_id=case_id) if desc_plain else ""
                has_pdf = bool(pdf_exists) and bool(pdf_l)

                hc_web_rows.append(
                    {
                        "case_id": case_id,
                        "court_code": c_code,
                        "court_name": c_name,
                        "bench": str(bench or "Principal").strip(),
                        "cnr": cnr_str or None,
                        "neutral_citation": None,
                        "title": clean_hc_title,
                        "petitioner": None,
                        "respondent": None,
                        "judge": str(judge or "").strip(),
                        "author_judge": str(judge or "").strip() or None,
                        "bench_strength": 1,
                        "decision_date": dec_iso,
                        "year": yr_int,
                        "order_number": 1,
                        "disposal_nature": str(disposal_nature or "").strip(),
                        "case_type": "",
                        "act": "",
                        "acts_cited": [],
                        "is_reportable": True,
                        "pdf_s3_bucket": "indian-high-court-judgments" if has_pdf else None,
                        "pdf_s3_key": pdf_l if has_pdf else None,
                        "source_url": "https://indian-high-court-judgments.s3.ap-south-1.amazonaws.com",
                        "text": clean_desc or "",
                        "source_id": "dattam_hc_judgments",
                        "commercial_safe": True,
                    }
                )

    # Seed verified landmark citation_edges
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
            "from_id": "sc:2017:puttaswamy",
            "from_kind": "judgment",
            "to_id": "sc:1978:maneka-gandhi",
            "to_kind": "judgment",
            "relation": "cites",
            "para_ref": "para 25",
            "quoting_excerpt": "Maneka Gandhi expanded Article 21 procedure established by law to fair, just and reasonable.",
            "from_year": 2017,
            "from_bench_str": 9,
            "precedent_status": "verified_curated",
        },
        {
            "from_id": "sc:2018:navtej-johar",
            "from_kind": "judgment",
            "to_id": "sc:2017:puttaswamy",
            "to_kind": "judgment",
            "relation": "cites",
            "para_ref": "para 160",
            "quoting_excerpt": "Sexual orientation is an essential attribute of privacy under Puttaswamy.",
            "from_year": 2018,
            "from_bench_str": 5,
            "precedent_status": "verified_curated",
        },
        {
            "from_id": "sc:2018:joseph-shine",
            "from_kind": "judgment",
            "to_id": "sc:2017:puttaswamy",
            "to_kind": "judgment",
            "relation": "cites",
            "para_ref": "para 40",
            "quoting_excerpt": "Sexual autonomy and dignity are protected under Article 21 privacy.",
            "from_year": 2018,
            "from_bench_str": 5,
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
            "from_id": "sc:1983:mithu",
            "from_kind": "judgment",
            "to_id": "ipc-1860:303",
            "to_kind": "provision",
            "relation": "invalidates",
            "para_ref": "para 23",
            "quoting_excerpt": "Mandatory death penalty under Section 303 IPC violates Articles 14 and 21.",
            "from_year": 1983,
            "from_bench_str": 5,
            "precedent_status": "verified_curated",
        },
        {
            "from_id": "sc:2018:navtej-johar",
            "from_kind": "judgment",
            "to_id": "ipc-1860:377",
            "to_kind": "provision",
            "relation": "invalidates",
            "para_ref": "para 253",
            "quoting_excerpt": "Section 377 IPC read down insofar as it criminalizes consensual adult acts.",
            "from_year": 2018,
            "from_bench_str": 5,
            "precedent_status": "verified_curated",
        },
        {
            "from_id": "sc:2018:joseph-shine",
            "from_kind": "judgment",
            "to_id": "ipc-1860:497",
            "to_kind": "provision",
            "relation": "invalidates",
            "para_ref": "para 64",
            "quoting_excerpt": "Section 497 IPC (adultery) struck down as unconstitutional.",
            "from_year": 2018,
            "from_bench_str": 5,
            "precedent_status": "verified_curated",
        },
    ]

    return IngestInputBundle(
        acts=list(acts_by_id.values()),
        aliases=sorted(aliases.items()),
        provisions=list(provisions_by_key.values()),
        sc_judgments=list(sc_by_id.values()),
        hc_web_rows=hc_web_rows,
        hc_mobile_rows=[],
        state_regulator_provisions=state_regulator_provisions,
        ddl_district_rows=[],
        tribunal_rows=tribunal_rows,
        regulator_order_rows=regulator_order_rows,
        ildc_rows=[],
        citation_edges=edges,
        hc_row_count=hc_row_count if not include_hc else len(hc_web_rows),
    )
