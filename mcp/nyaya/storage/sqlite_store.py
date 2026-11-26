"""Read-only immutable SQLite3 FTS5 + Validity + Concordance + Citation Graph store."""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
import threading
from collections.abc import Iterable
from datetime import date as Date
from pathlib import Path
from typing import Any

from ..citations import (
    canonicalize_court,
    court_code_variants,
    normalize_jurisdiction,
    parse_single_citation,
)
from ..exceptions import CommercialLicenseBlocked, NotFound, StorageUnavailable
from ..models import (
    ActInfo,
    AmendmentRecord,
    BrowseResult,
    CauseListEntry,
    CitationEdgeRecord,
    ConcordanceEntry,
    CorpusCounts,
    CorpusStatusResponse,
    CourtInfo,
    CrossRefDirection,
    InvalidatingJudgment,
    JudgmentCard,
    JudgmentMeta,
    OffenceClassification,
    PrecedentTrace,
    ProvisionHistory,
    ProvisionIndexEntry,
    ProvisionRecord,
    ProvisionRef,
    SourceProvenance,
    SourceStatusEntry,
    StatuteHit,
    StatutoryLink,
    StatutoryValidity,
    TermsStatus,
    TextAvailability,
    TocEntry,
    TraceDirection,
)
from ..sanitize import BIDI_RE, strip_control_chars

SCHEMA_DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sources (
    source_id       TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    url             TEXT NOT NULL,
    license_spdx    TEXT NOT NULL,
    commercial_safe INTEGER NOT NULL CHECK (commercial_safe IN (0, 1)),
    terms_status    TEXT NOT NULL CHECK (terms_status IN ('cleared','attribution_required','non_commercial_only','upstream_unknown')),
    snapshot_tag    TEXT NOT NULL,
    as_of           TEXT NOT NULL,
    sha256          TEXT NOT NULL,
    record_count    INTEGER NOT NULL DEFAULT 0,
    notes           TEXT
);

CREATE TABLE IF NOT EXISTS acts (
    act_id            TEXT PRIMARY KEY,
    short_name        TEXT NOT NULL UNIQUE,
    full_name         TEXT NOT NULL,
    year              INTEGER,
    act_number        TEXT,
    jurisdiction      TEXT NOT NULL DEFAULT 'Central',
    category          TEXT NOT NULL CHECK (category IN ('constitution','central_act','state_act','regulator','rule','ordinance','cad_debate')),
    status            TEXT NOT NULL DEFAULT 'in_force'
                      CHECK (status IN ('in_force','repealed','partially_repealed','not_yet_commenced','superseded')),
    commencement_date TEXT,
    repeal_date       TEXT,
    replaced_by_act   TEXT REFERENCES acts(act_id),
    storage_tier      TEXT NOT NULL CHECK (storage_tier IN ('sqlite_core','lance_bucket')),
    source_id         TEXT NOT NULL REFERENCES sources(source_id),
    commercial_safe   INTEGER NOT NULL DEFAULT 1 CHECK (commercial_safe IN (0, 1)),
    as_of             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_acts_short_lower ON acts(lower(short_name));
CREATE INDEX IF NOT EXISTS idx_acts_juris_cat ON acts(jurisdiction, category, commercial_safe);

CREATE TABLE IF NOT EXISTS act_aliases (
    alias_lower TEXT PRIMARY KEY,
    act_id      TEXT NOT NULL REFERENCES acts(act_id) ON DELETE CASCADE
) WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS provisions (
    doc_id            TEXT PRIMARY KEY,
    act_id            TEXT NOT NULL REFERENCES acts(act_id) ON DELETE CASCADE,
    act_short         TEXT NOT NULL DEFAULT '',
    kind              TEXT NOT NULL CHECK (kind IN ('section','article','schedule','amendment','order_rule','preamble','regulation','circular','rule','cad_debate')),
    ref               TEXT NOT NULL,
    ref_norm          TEXT NOT NULL,
    ref_num           INTEGER NOT NULL DEFAULT 0,
    chapter_num       TEXT,
    chapter_title     TEXT,
    part              TEXT,
    title             TEXT,
    text              TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'in_force'
                      CHECK (status IN ('in_force','repealed','omitted','struck_down','read_down','partially_struck_down','kept_in_abeyance','substituted','unverified')),
    commencement_date TEXT,
    repeal_date       TEXT,
    metadata_json     TEXT NOT NULL DEFAULT '{}',
    source_id         TEXT NOT NULL REFERENCES sources(source_id),
    source_url        TEXT NOT NULL,
    text_sha256       TEXT NOT NULL,
    commercial_safe   INTEGER NOT NULL DEFAULT 1 CHECK (commercial_safe IN (0, 1))
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_prov_act_ref ON provisions(act_id, kind, ref_norm);
CREATE INDEX IF NOT EXISTS idx_prov_browse ON provisions(act_id, kind, ref_num, ref_norm);
CREATE INDEX IF NOT EXISTS idx_prov_chapter ON provisions(act_id, chapter_num) WHERE chapter_num IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_prov_status ON provisions(status) WHERE status != 'in_force';

CREATE VIRTUAL TABLE IF NOT EXISTS provisions_fts USING fts5(
    doc_id UNINDEXED,
    act_short,
    ref,
    title,
    text,
    content='provisions',
    content_rowid='rowid',
    tokenize="porter unicode61 remove_diacritics 2 tokenchars '.-/()'"
);

CREATE TABLE IF NOT EXISTS provision_amendments (
    amendment_id      TEXT PRIMARY KEY,
    target_act_id     TEXT NOT NULL REFERENCES acts(act_id),
    target_ref_norm   TEXT,
    amending_act      TEXT NOT NULL,
    amendment_no      INTEGER,
    enacted_date      TEXT,
    effective_date    TEXT,
    action            TEXT NOT NULL CHECK (action IN ('inserted','substituted','omitted','amended','repealed')),
    summary           TEXT NOT NULL,
    articles_affected TEXT,
    source_id         TEXT NOT NULL REFERENCES sources(source_id)
);
CREATE INDEX IF NOT EXISTS idx_amend_target ON provision_amendments(target_act_id, target_ref_norm);
CREATE INDEX IF NOT EXISTS idx_amend_year ON provision_amendments(target_act_id, enacted_date);

CREATE TABLE IF NOT EXISTS concordance (
    id                          INTEGER PRIMARY KEY,
    old_doc_id                  TEXT NOT NULL REFERENCES provisions(doc_id),
    new_doc_id                  TEXT REFERENCES provisions(doc_id),
    mapping_type                TEXT NOT NULL CHECK (mapping_type IN ('one_to_one','one_to_many','many_to_one','omitted','newly_inserted')),
    substantive_delta           TEXT NOT NULL,
    punishment_delta            TEXT,
    procedural_delta            TEXT,
    savings_note                TEXT,
    verified_by                 TEXT NOT NULL,
    source_id                   TEXT NOT NULL REFERENCES sources(source_id),
    offence_classification_json TEXT
);
CREATE INDEX IF NOT EXISTS idx_conc_old ON concordance(old_doc_id);
CREATE INDEX IF NOT EXISTS idx_conc_new ON concordance(new_doc_id);

CREATE TABLE IF NOT EXISTS judicial_invalidations (
    id              INTEGER PRIMARY KEY,
    target_doc_id   TEXT NOT NULL REFERENCES provisions(doc_id),
    effect          TEXT NOT NULL CHECK (effect IN ('struck_down','read_down','kept_in_abeyance','partially_struck_down')),
    case_id         TEXT NOT NULL,
    case_name       TEXT NOT NULL,
    citation        TEXT NOT NULL,
    decision_date   TEXT NOT NULL,
    bench_strength  INTEGER NOT NULL,
    holding_summary TEXT NOT NULL,
    source_id       TEXT NOT NULL REFERENCES sources(source_id)
);
CREATE INDEX IF NOT EXISTS idx_inval_doc ON judicial_invalidations(target_doc_id);

CREATE TABLE IF NOT EXISTS judgments_meta (
    case_id          TEXT PRIMARY KEY,
    tier             TEXT NOT NULL CHECK (tier IN ('sc','hc','tribunal','district')),
    court_code       TEXT NOT NULL,
    court_name       TEXT NOT NULL,
    bench            TEXT,
    cnr              TEXT,
    neutral_citation TEXT,
    air_citation     TEXT,
    scc_citation     TEXT,
    scr_citation     TEXT,
    title            TEXT NOT NULL,
    petitioner       TEXT,
    respondent       TEXT,
    decision_date    TEXT,
    year             INTEGER,
    judge_coram      TEXT,
    author_judge     TEXT,
    bench_strength   INTEGER,
    disposal_nature  TEXT,
    case_type        TEXT,
    acts_cited_json  TEXT NOT NULL DEFAULT '[]',
    is_reportable    INTEGER NOT NULL DEFAULT 0,
    pdf_s3_bucket    TEXT,
    pdf_s3_key       TEXT,
    source_url       TEXT NOT NULL,
    text_sha256      TEXT NOT NULL,
    has_lance_text   INTEGER NOT NULL DEFAULT 0,
    cited_by_count   INTEGER NOT NULL DEFAULT 0,
    source_id        TEXT NOT NULL REFERENCES sources(source_id),
    commercial_safe  INTEGER NOT NULL DEFAULT 1 CHECK (commercial_safe IN (0, 1))
);
CREATE INDEX IF NOT EXISTS idx_jmeta_cnr ON judgments_meta(cnr) WHERE cnr IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_jmeta_nc ON judgments_meta(lower(neutral_citation)) WHERE neutral_citation IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_jmeta_air ON judgments_meta(lower(air_citation)) WHERE air_citation IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_jmeta_scc ON judgments_meta(lower(scc_citation)) WHERE scc_citation IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_jmeta_court_yr ON judgments_meta(tier, court_code, year, commercial_safe);

CREATE VIRTUAL TABLE IF NOT EXISTS judgments_meta_fts USING fts5(
    case_id UNINDEXED,
    title,
    petitioner,
    respondent,
    judge_coram,
    neutral_citation,
    air_citation,
    scc_citation,
    content='judgments_meta',
    content_rowid='rowid',
    tokenize="porter unicode61 remove_diacritics 2 tokenchars '.-:/()'"
);

CREATE TABLE IF NOT EXISTS citation_edges (
    edge_id          INTEGER PRIMARY KEY,
    from_id          TEXT NOT NULL,
    from_kind        TEXT NOT NULL CHECK (from_kind IN ('provision','judgment')),
    to_id            TEXT NOT NULL,
    to_kind          TEXT NOT NULL CHECK (to_kind IN ('provision','judgment')),
    relation         TEXT NOT NULL CHECK (relation IN (
                         'cites','references','repeals','replaced_by','amends',
                         'corresponds_to','invalidates','interprets','delegates_to')),
    para_ref         TEXT,
    quoting_excerpt  TEXT,
    from_year        INTEGER,
    from_bench_str   INTEGER,
    precedent_status TEXT NOT NULL DEFAULT 'unverified_automated'
                     CHECK (precedent_status IN ('verified_curated','unverified_automated')),
    UNIQUE (from_id, to_id, relation)
);
CREATE INDEX IF NOT EXISTS idx_edges_from ON citation_edges(from_id, relation);
CREATE INDEX IF NOT EXISTS idx_edges_to ON citation_edges(to_id, relation, from_bench_str DESC, from_year DESC);

CREATE TABLE IF NOT EXISTS courts (
    court_code      TEXT PRIMARY KEY,
    court_name      TEXT NOT NULL,
    tier            TEXT NOT NULL CHECK (tier IN ('sc','hc','district','tribunal','regulator')),
    bench_code      TEXT,
    bench_name      TEXT,
    state_ut        TEXT NOT NULL,
    seat_city       TEXT,
    hc_jurisdiction TEXT
);

CREATE TABLE IF NOT EXISTS cause_lists (
    id              INTEGER PRIMARY KEY,
    court           TEXT NOT NULL,
    cause_list_date TEXT NOT NULL,
    bench           TEXT,
    judge           TEXT,
    item_number     TEXT,
    case_number     TEXT,
    cnr             TEXT,
    parties         TEXT,
    advocates_json  TEXT NOT NULL DEFAULT '[]',
    purpose         TEXT,
    source_url      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_cl_court_date ON cause_lists(court, cause_list_date);
"""

_REF_PREFIX_RE = re.compile(
    r"^(?:s(?:ec(?:tion)?)?\.?\s*|art(?:icle)?\.?\s*|reg(?:ulation)?\.?\s*|rule\s+)",
    re.IGNORECASE,
)
_FTS_TOKEN_RE = re.compile(r"[A-Za-z0-9_./()-]+")
_SUB_CLAUSE_RE = re.compile(r"^([A-Za-z0-9-]+?)((?:\([0-9a-zA-Z]+\))+)$")
_STOPWORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "been", "being", "by",
        "did", "do", "does", "for", "from", "has", "have", "how", "in", "is",
        "it", "its", "may", "no", "not", "of", "on", "or", "regarding", "say",
        "shall", "state", "such", "that", "the", "these", "this", "those", "to",
        "under", "upon", "was", "were", "what", "when", "where", "which", "who",
        "whom", "whose", "with", "about", "any", "all", "into",
    }
)

_TIER_TO_PUBLIC: dict[str, str] = {
    "sc": "supreme_court",
    "hc": "high_court",
    "tribunal": "tribunal",
    "district": "district_court",
}

_VERIFICATION_NOTICE = (
    "Citation graph edges are extracted automatically from indexed SC and hot-HC "
    "judgments; un-OCR'd High Court scans and post-cutoff rulings are excluded, and "
    "this tool does NOT perform automated Shepardizing or good-law validation."
)


def _parse_date(val: str | None) -> Date | None:
    if not val:
        return None
    try:
        return Date.fromisoformat(val[:10])
    except ValueError:
        return None


def _normalize_ref(ref: str) -> tuple[str, str | None]:
    """Return (canonical_ref_norm, sub_ref_or_none)."""
    cleaned = BIDI_RE.sub("", strip_control_chars(ref)).strip()
    cleaned = _REF_PREFIX_RE.sub("", cleaned).strip()
    # Handle CPC Order/Rule or Constitution Schedule directly via parser if matched
    parsed = parse_single_citation(cleaned)
    if parsed is not None:
        if parsed.kind == "cpc_order_rule":
            return f"O{parsed.components['order']}-R{parsed.components['rule']}", parsed.sub_ref
        if parsed.kind == "constitution_schedule":
            return f"SCH{parsed.components['schedule']}", parsed.sub_ref
    # Strip trailing parenthesized sub-clauses only if exact match isn't used later
    m = _SUB_CLAUSE_RE.match(cleaned)
    if m:
        return cleaned.upper(), m.group(2)
    return cleaned.upper(), None


_INFLECTION_SUFFIXES: tuple[str, ...] = (
    "ment",
    "tion",
    "sion",
    "ing",
    "ity",
    "es",
    "ed",
    "er",
    "ly",
    "s",
)
_INFLECTION_ALT = "s|es|ed|ing|er|ly|ment|tion|sion|ity"


def _token_matches_boundary(tok: str, haystack: str) -> bool:
    """Match a lowercased token against a lowercased haystack with strict word boundaries."""
    tok = tok.lower()
    if not tok:
        return False
    if len(tok) < 6:
        return bool(
            re.search(rf"(?<![a-z0-9])(?<![a-z0-9]'){re.escape(tok)}(?![a-z0-9])", haystack)
        )
    stems = [tok]
    for suf in _INFLECTION_SUFFIXES:
        if tok.endswith(suf):
            stem = tok[: -len(suf)]
            if len(stem) >= 5 and stem not in stems:
                stems.append(stem)
    for stem in stems:
        if re.search(rf"\b{re.escape(stem)}(?:{_INFLECTION_ALT})?\b", haystack):
            return True
    return False


def _distinct_lower(tokens: Iterable[str]) -> list[str]:
    """Lower-case ``tokens`` and drop duplicates while preserving first-seen order."""
    return list(dict.fromkeys(t.lower() for t in tokens))


def _multitoken_min_required(n: int) -> int:
    """Return length-scaled minimum distinct content token matches for OR-fallback precision."""
    if n <= 1:
        return n
    if n <= 3:
        return 2
    return max(3, math.ceil(0.6 * n))


def _extract_fts_tokens(raw: str) -> tuple[list[str], list[str]]:
    """Extract normalized FTS tokens and non-stopword content tokens."""
    all_tokens = [t.strip(".-/()") for t in _FTS_TOKEN_RE.findall(raw)]
    all_tokens = [t for t in all_tokens if t]
    content_tokens = [t for t in all_tokens if t.lower() not in _STOPWORDS]
    return all_tokens, content_tokens


def _fts_query(
    raw: str,
    *,
    exact_phrase: bool = False,
    operator: str = "OR",
    max_tokens: int | None = None,
) -> str:
    all_tokens, content_tokens = _extract_fts_tokens(raw)
    if not all_tokens:
        return ""
    if exact_phrase:
        escaped = " ".join(all_tokens).replace('"', '""')
        return f'"{escaped}"'
    selected = content_tokens if content_tokens else all_tokens
    op = "AND" if operator.upper() == "AND" else "OR"
    cap = max_tokens if max_tokens is not None else (8 if op == "AND" else 16)
    return f" {op} ".join(f'"{t.replace(chr(34), chr(34) * 2)}"' for t in selected[:cap])


def _row_provenance(row: sqlite3.Row, *, content_sha256: str | None = None) -> SourceProvenance:
    terms_raw = str(row["src_terms_status"] if "src_terms_status" in row.keys() else "cleared")
    terms: TermsStatus = (
        terms_raw  # type: ignore[assignment]
        if terms_raw in ("cleared", "attribution_required", "non_commercial_only", "upstream_unknown")
        else "cleared"
    )
    as_of = _parse_date(row["src_as_of"]) or Date(2026, 11, 22)
    return SourceProvenance(
        source_id=str(row["source_id"]),
        source_name=str(row["src_name"]),
        source_url=str(row["source_url"] if "source_url" in row.keys() and row["source_url"] else row["src_url"]),
        license_spdx=str(row["src_license"]),
        commercial_safe=bool(row["commercial_safe"]),
        terms_status=terms,
        as_of=as_of,
        content_sha256=content_sha256,
    )


class SqliteStore:
    """Thread-safe immutable read-only SQLite3 core store."""

    def __init__(self, db_path: Path | str, *, default_commercial_safe_only: bool = False) -> None:
        self.db_path = Path(db_path)
        self.default_commercial_safe_only = default_commercial_safe_only
        self._local = threading.local()
        self._lock = threading.Lock()
        self._conns: set[sqlite3.Connection] = set()
        self._closed = False

    def is_available(self) -> bool:
        return not self._closed and self.db_path.is_file()

    def _effective_cs(self, commercial_safe_only: bool | None) -> bool:
        return self.default_commercial_safe_only if commercial_safe_only is None else bool(commercial_safe_only)

    def _conn(self) -> sqlite3.Connection:
        if self._closed:
            raise StorageUnavailable("SqliteStore is closed", degraded_capabilities=["sqlite_core"])
        conn: sqlite3.Connection | None = getattr(self._local, "conn", None)
        if conn is not None:
            return conn
        if not self.db_path.is_file():
            raise StorageUnavailable(
                f"SQLite core not found at {self.db_path}",
                degraded_capabilities=["sqlite_core"],
                hint="Run `nyaya-mcp sync` or set NYAYA_DATA_DIR.",
            )
        uri = f"{self.db_path.resolve().as_uri()}?mode=ro&immutable=1"
        try:
            conn = sqlite3.connect(uri, uri=True, check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA mmap_size=67108864")
            conn.execute("PRAGMA query_only=ON")
        except sqlite3.Error as exc:
            raise StorageUnavailable(
                f"Failed to open SQLite core: {exc}",
                degraded_capabilities=["sqlite_core"],
            ) from exc
        with self._lock:
            self._conns.add(conn)
        self._local.conn = conn
        return conn

    def close(self) -> None:
        self._closed = True
        with self._lock:
            conns = list(self._conns)
            self._conns.clear()
        for conn in conns:
            try:
                conn.close()
            except Exception:
                pass
        if hasattr(self._local, "conn"):
            delattr(self._local, "conn")

    def get_alias_map(self) -> dict[str, str]:
        if not self.is_available():
            return {}
        con = self._conn()
        alias_map: dict[str, str] = {}
        act_rows = con.execute("SELECT short_name, full_name FROM acts").fetchall()
        for r in act_rows:
            short = str(r["short_name"])
            full = str(r["full_name"] or "").strip()
            if short:
                alias_map[short.lower()] = short
            if full:
                full_l = full.lower()
                alias_map[full_l] = short
                stripped = re.sub(r"^the\s+", "", full_l).strip()
                if stripped:
                    alias_map[stripped] = short
        rows = con.execute(
            "SELECT aa.alias_lower, a.short_name "
            "FROM act_aliases aa JOIN acts a ON aa.act_id = a.act_id"
        ).fetchall()
        for r in rows:
            alias_map[str(r["alias_lower"])] = str(r["short_name"])
        return alias_map

    def _resolve_act_id(self, act: str, *, jurisdiction: str | None = None) -> str | None:
        clean = BIDI_RE.sub("", strip_control_chars(act)).strip().lower()
        if not clean:
            return None
        con = self._conn()
        norm_juris = normalize_jurisdiction(jurisdiction)
        params: list[Any] = [clean, clean]
        juris_sql = ""
        if norm_juris:
            juris_sql = " AND lower(a.jurisdiction) = lower(?)"
            params.append(norm_juris)
        row = con.execute(
            f"SELECT a.act_id FROM acts a WHERE (lower(a.short_name) = ? OR lower(a.act_id) = ?){juris_sql} LIMIT 1",
            params,
        ).fetchone()
        if row:
            return str(row["act_id"])
        row = con.execute(
            "SELECT aa.act_id FROM act_aliases aa "
            "JOIN acts a ON aa.act_id = a.act_id "
            f"WHERE aa.alias_lower = ?{juris_sql} LIMIT 1",
            [clean, *([norm_juris] if norm_juris else [])],
        ).fetchone()
        return str(row["act_id"]) if row else None

    def _build_validity(self, con: sqlite3.Connection, row: sqlite3.Row) -> StatutoryValidity:
        doc_id = str(row["doc_id"])
        replaced_by: list[ProvisionRef] = []
        replaces: list[ProvisionRef] = []
        transitional_note: str | None = None

        conc_out = con.execute(
            "SELECT p.ref, p.title, p.kind, a.short_name AS act, a.jurisdiction, c.savings_note "
            "FROM concordance c "
            "LEFT JOIN provisions p ON c.new_doc_id = p.doc_id "
            "LEFT JOIN acts a ON p.act_id = a.act_id "
            "WHERE c.old_doc_id = ?",
            (doc_id,),
        ).fetchall()
        for r in conc_out:
            if r["savings_note"] and not transitional_note:
                transitional_note = str(r["savings_note"])
            if r["ref"] and r["act"]:
                replaced_by.append(
                    ProvisionRef(
                        act=str(r["act"]),
                        jurisdiction=str(r["jurisdiction"] or "Central"),
                        ref=str(r["ref"]),
                        title=r["title"],
                        kind=r["kind"],
                    )
                )

        conc_in = con.execute(
            "SELECT p.ref, p.title, p.kind, a.short_name AS act, a.jurisdiction, c.savings_note "
            "FROM concordance c "
            "JOIN provisions p ON c.old_doc_id = p.doc_id "
            "JOIN acts a ON p.act_id = a.act_id "
            "WHERE c.new_doc_id = ?",
            (doc_id,),
        ).fetchall()
        for r in conc_in:
            if r["savings_note"] and not transitional_note:
                transitional_note = str(r["savings_note"])
            replaces.append(
                ProvisionRef(
                    act=str(r["act"]),
                    jurisdiction=str(r["jurisdiction"] or "Central"),
                    ref=str(r["ref"]),
                    title=r["title"],
                    kind=r["kind"],
                )
            )

        inval_row = con.execute(
            "SELECT * FROM judicial_invalidations WHERE target_doc_id = ? ORDER BY decision_date DESC LIMIT 1",
            (doc_id,),
        ).fetchone()
        invalidating: InvalidatingJudgment | None = None
        if inval_row is not None:
            invalidating = InvalidatingJudgment(
                citation=str(inval_row["citation"]),
                case_id=str(inval_row["case_id"]),
                case_name=str(inval_row["case_name"]),
                decision_date=_parse_date(inval_row["decision_date"]),
                coram_size=inval_row["bench_strength"],
                effect=inval_row["effect"],
                holding_summary=str(inval_row["holding_summary"]),
            )

        ref_norm = str(row["ref_norm"])
        amend_cnt_row = con.execute(
            "SELECT count(*) AS n FROM provision_amendments "
            "WHERE target_act_id = ? "
            "AND (upper(target_ref_norm) = upper(?) "
            "OR ',' || upper(replace(coalesce(articles_affected, ''), ' ', '')) || ',' LIKE ?)",
            (row["act_id"], ref_norm, f"%,{ref_norm.upper()},%"),
        ).fetchone()
        amend_count = int(amend_cnt_row["n"]) if amend_cnt_row else 0

        if not transitional_note and str(row["status"]) == "repealed" and str(row["act_short"]) in {"IPC", "CrPC", "EvidenceAct"}:
            transitional_note = "BNSS s.531: offences/FIRs/proceedings pending before 2024-07-01 continue under the repealed Code."

        return StatutoryValidity(
            status=row["status"],
            commencement_date=_parse_date(row["commencement_date"]),
            repeal_date=_parse_date(row["repeal_date"]),
            replaced_by=replaced_by,
            replaces=replaces,
            invalidating_judgment=invalidating,
            transitional_note=transitional_note,
            amendment_count=amend_count,
            verified_as_of=_parse_date(row["src_as_of"]),
        )

    def get_provision(
        self,
        act: str,
        ref: str,
        *,
        jurisdiction: str | None = None,
        include_text: bool = True,
        include_links: bool = True,
        commercial_safe_only: bool | None = None,
    ) -> ProvisionRecord | None:
        con = self._conn()
        norm_juris = normalize_jurisdiction(jurisdiction)
        act_id = self._resolve_act_id(act, jurisdiction=norm_juris)
        if act_id is None:
            return None
        ref_norm, sub_ref = _normalize_ref(ref)
        candidates = [ref_norm]
        m = _SUB_CLAUSE_RE.match(ref_norm)
        if m:
            candidates.append(m.group(1))

        row: sqlite3.Row | None = None
        for cand in candidates:
            row = con.execute(
                "SELECT p.*, a.short_name AS act_short_name, a.full_name AS act_full_name, "
                "a.jurisdiction AS act_jurisdiction, "
                "s.name AS src_name, s.url AS src_url, s.license_spdx AS src_license, "
                "s.terms_status AS src_terms_status, s.as_of AS src_as_of "
                "FROM provisions p "
                "JOIN acts a ON p.act_id = a.act_id "
                "JOIN sources s ON p.source_id = s.source_id "
                "WHERE p.act_id = ? AND upper(p.ref_norm) = upper(?) LIMIT 1",
                (act_id, cand),
            ).fetchone()
            if row is not None:
                break

        if row is None:
            return None
        if self._effective_cs(commercial_safe_only) and not bool(row["commercial_safe"]):
            raise CommercialLicenseBlocked(
                f"Provision {act} {ref} is restricted to non-commercial use ({row['src_license']}).",
                license_spdx=str(row["src_license"]),
            )

        validity = self._build_validity(con, row)
        links: list[StatutoryLink] = []
        if include_links:
            edges, _ = self.get_cross_refs(
                str(row["doc_id"]),
                direction="both",
                limit=15,
                commercial_safe_only=commercial_safe_only,
            )
            for e in edges:
                if e.from_ref and e.to_ref:
                    links.append(
                        StatutoryLink(
                            from_ref=e.from_ref,
                            to_ref=e.to_ref,
                            relation_kind=e.relation,
                            context_note=e.quoting_excerpt,
                            provenance=e.provenance,
                        )
                    )

        meta = json.loads(row["metadata_json"] or "{}")
        if sub_ref:
            meta["requested_sub_ref"] = sub_ref
            meta["sub_ref_found"] = sub_ref in str(row["text"])

        return ProvisionRecord(
            doc_id=str(row["doc_id"]),
            act_id=str(row["act_id"]),
            act=str(row["act_short_name"]),
            act_full_name=str(row["act_full_name"]),
            jurisdiction=str(row["act_jurisdiction"]),
            kind=row["kind"],
            ref=str(row["ref"]),
            ref_norm=str(row["ref_norm"]),
            chapter_num=row["chapter_num"],
            chapter_title=row["chapter_title"],
            part=row["part"],
            title=row["title"],
            text=str(row["text"]) if include_text else "",
            validity=validity,
            top_links=links,
            provenance=_row_provenance(row, content_sha256=str(row["text_sha256"])),
            text_sha256=str(row["text_sha256"]),
            metadata=meta,
        )

    def browse_statute(
        self,
        act: str | None = None,
        *,
        query: str | None = None,
        jurisdiction: str | None = None,
        category: str | None = None,
        status: str | None = None,
        chapter: str | int | None = None,
        part: str | None = None,
        ref_from: str | None = None,
        ref_to: str | None = None,
        kind: str | None = None,
        include_text: bool = False,
        limit: int = 50,
        offset: int = 0,
        commercial_safe_only: bool | None = None,
    ) -> BrowseResult:
        con = self._conn()
        cs_only = self._effective_cs(commercial_safe_only)
        norm_juris = normalize_jurisdiction(jurisdiction)
        limit = max(1, min(int(limit), 200))
        offset = max(0, int(offset))

        if act is None:
            where = ["1=1"]
            params: list[Any] = []
            if cs_only:
                where.append("a.commercial_safe = 1")
            if norm_juris:
                where.append("lower(a.jurisdiction) = lower(?)")
                params.append(norm_juris)
            if category:
                where.append("lower(a.category) = lower(?)")
                params.append(category.strip())
            if status and status != "all":
                where.append("lower(a.status) = lower(?)")
                params.append(status.strip())
            if query:
                like = f"%{query.strip().lower()}%"
                where.append("(lower(a.short_name) LIKE ? OR lower(a.full_name) LIKE ?)")
                params.extend([like, like])

            where_sql = " AND ".join(where)
            total = int(con.execute(f"SELECT count(*) FROM acts a WHERE {where_sql}", params).fetchone()[0])
            rows = con.execute(
                "SELECT a.*, s.name AS src_name, s.url AS src_url, s.license_spdx AS src_license, "
                "s.terms_status AS src_terms_status, s.as_of AS src_as_of, "
                "(SELECT count(*) FROM provisions p WHERE p.act_id = a.act_id) AS prov_cnt "
                f"FROM acts a JOIN sources s ON a.source_id = s.source_id WHERE {where_sql} "
                "ORDER BY a.short_name LIMIT ? OFFSET ?",
                [*params, limit, offset],
            ).fetchall()
            acts = [
                ActInfo(
                    act_id=str(r["act_id"]),
                    short_name=str(r["short_name"]),
                    full_name=str(r["full_name"]),
                    year=r["year"],
                    act_number=r["act_number"],
                    jurisdiction=str(r["jurisdiction"]),
                    category=str(r["category"]),
                    status=r["status"],
                    commencement_date=_parse_date(r["commencement_date"]),
                    repeal_date=_parse_date(r["repeal_date"]),
                    replaced_by_act=r["replaced_by_act"],
                    storage_tier=r["storage_tier"],
                    provision_count=int(r["prov_cnt"]),
                    provenance=SourceProvenance(
                        source_id=str(r["source_id"]),
                        source_name=str(r["src_name"]),
                        source_url=str(r["src_url"]),
                        license_spdx=str(r["src_license"]),
                        commercial_safe=bool(r["commercial_safe"]),
                        terms_status=r["src_terms_status"],
                        as_of=_parse_date(r["src_as_of"]) or Date(2026, 11, 22),
                    ),
                )
                for r in rows
            ]
            return BrowseResult(mode="catalog", acts=acts, total=total, returned=len(acts))

        act_id = self._resolve_act_id(act, jurisdiction=norm_juris)
        if act_id is None:
            raise NotFound(f"Act {act!r} not found in corpus.", kind="act")

        act_row = con.execute(
            "SELECT a.*, s.name AS src_name, s.url AS src_url, s.license_spdx AS src_license, "
            "s.terms_status AS src_terms_status, s.as_of AS src_as_of, "
            "(SELECT count(*) FROM provisions p WHERE p.act_id = a.act_id) AS prov_cnt "
            "FROM acts a JOIN sources s ON a.source_id = s.source_id WHERE a.act_id = ?",
            (act_id,),
        ).fetchone()
        assert act_row is not None
        if cs_only and not bool(act_row["commercial_safe"]):
            raise CommercialLicenseBlocked(
                f"Act {act!r} is non-commercial only.", license_spdx=str(act_row["src_license"])
            )

        act_info = ActInfo(
            act_id=str(act_row["act_id"]),
            short_name=str(act_row["short_name"]),
            full_name=str(act_row["full_name"]),
            year=act_row["year"],
            act_number=act_row["act_number"],
            jurisdiction=str(act_row["jurisdiction"]),
            category=str(act_row["category"]),
            status=act_row["status"],
            commencement_date=_parse_date(act_row["commencement_date"]),
            repeal_date=_parse_date(act_row["repeal_date"]),
            replaced_by_act=act_row["replaced_by_act"],
            storage_tier=act_row["storage_tier"],
            provision_count=int(act_row["prov_cnt"]),
            provenance=SourceProvenance(
                source_id=str(act_row["source_id"]),
                source_name=str(act_row["src_name"]),
                source_url=str(act_row["src_url"]),
                license_spdx=str(act_row["src_license"]),
                commercial_safe=bool(act_row["commercial_safe"]),
                terms_status=act_row["src_terms_status"],
                as_of=_parse_date(act_row["src_as_of"]) or Date(2026, 11, 22),
            ),
        )

        # If no structural filter supplied, return TOC
        if chapter is None and part is None and kind is None and ref_from is None and ref_to is None:
            toc_rows = con.execute(
                "SELECT chapter_num, chapter_title, part, kind, count(*) AS cnt, "
                "min(ref) AS ref_start, max(ref) AS ref_end "
                "FROM provisions WHERE act_id = ? "
                "GROUP BY chapter_num, chapter_title, part, kind "
                "ORDER BY min(ref_num), chapter_num, part",
                (act_id,),
            ).fetchall()
            toc = [
                TocEntry(
                    chapter_num=r["chapter_num"],
                    chapter_title=r["chapter_title"],
                    part=r["part"],
                    kind=r["kind"],
                    provision_count=int(r["cnt"]),
                    ref_start=r["ref_start"],
                    ref_end=r["ref_end"],
                )
                for r in toc_rows
            ]
            return BrowseResult(
                mode="toc",
                act_info=act_info,
                chapters_or_parts=toc,
                total=len(toc),
                returned=len(toc),
            )

        where = ["p.act_id = ?"]
        params = [act_id]
        if cs_only:
            where.append("p.commercial_safe = 1")
        if chapter is not None:
            where.append("upper(p.chapter_num) = upper(?)")
            params.append(str(chapter))
        if part is not None:
            where.append("lower(p.part) LIKE ?")
            params.append(f"%{part.strip().lower()}%")
        if kind is not None:
            where.append("p.kind = ?")
            params.append(kind)
        if ref_from is not None:
            n_from, _ = _normalize_ref(ref_from)
            num_m = re.match(r"(\d+)", n_from)
            if num_m:
                where.append("p.ref_num >= ?")
                params.append(int(num_m.group(1)))
        if ref_to is not None:
            n_to, _ = _normalize_ref(ref_to)
            num_m = re.match(r"(\d+)", n_to)
            if num_m:
                where.append("p.ref_num <= ?")
                params.append(int(num_m.group(1)))

        where_sql = " AND ".join(where)
        total = int(con.execute(f"SELECT count(*) FROM provisions p WHERE {where_sql}", params).fetchone()[0])
        rows = con.execute(
            "SELECT p.*, s.name AS src_name, s.url AS src_url, s.license_spdx AS src_license, "
            "s.terms_status AS src_terms_status, s.as_of AS src_as_of "
            f"FROM provisions p JOIN sources s ON p.source_id = s.source_id WHERE {where_sql} "
            "ORDER BY p.ref_num, p.ref_norm LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        provs = [
            ProvisionIndexEntry(
                doc_id=str(r["doc_id"]),
                act=str(act_info.short_name),
                jurisdiction=act_info.jurisdiction,
                kind=r["kind"],
                ref=str(r["ref"]),
                title=r["title"],
                status=r["status"],
                snippet=str(r["text"])[:300] if include_text else None,
                provenance=_row_provenance(r, content_sha256=str(r["text_sha256"])),
            )
            for r in rows
        ]
        return BrowseResult(
            mode="provisions",
            act_info=act_info,
            provisions=provs,
            total=total,
            returned=len(provs),
        )

    def compare_provisions(
        self,
        act: str,
        ref: str,
        *,
        commercial_safe_only: bool | None = None,
    ) -> list[ConcordanceEntry]:
        prov = self.get_provision(
            act, ref, include_text=False, include_links=False, commercial_safe_only=commercial_safe_only
        )
        if prov is None:
            return []
        con = self._conn()
        rows = con.execute(
            "SELECT c.*, "
            "po.ref AS old_ref, po.title AS old_title, po.kind AS old_kind, ao.short_name AS old_act, ao.jurisdiction AS old_juris, "
            "pn.ref AS new_ref, pn.title AS new_title, pn.kind AS new_kind, an.short_name AS new_act, an.jurisdiction AS new_juris, "
            "s.name AS src_name, s.url AS src_url, s.license_spdx AS src_license, s.terms_status AS src_terms_status, s.as_of AS src_as_of, s.commercial_safe AS commercial_safe "
            "FROM concordance c "
            "JOIN provisions po ON c.old_doc_id = po.doc_id "
            "JOIN acts ao ON po.act_id = ao.act_id "
            "LEFT JOIN provisions pn ON c.new_doc_id = pn.doc_id "
            "LEFT JOIN acts an ON pn.act_id = an.act_id "
            "JOIN sources s ON c.source_id = s.source_id "
            "WHERE c.old_doc_id = ? OR c.new_doc_id = ?",
            (prov.doc_id, prov.doc_id),
        ).fetchall()
        entries: list[ConcordanceEntry] = []
        for r in rows:
            row_keys = set(r.keys())
            raw_oc = (
                r["offence_classification_json"]
                if "offence_classification_json" in row_keys
                else None
            )
            offence_cls: OffenceClassification | None = None
            if raw_oc:
                try:
                    parsed_oc = json.loads(str(raw_oc))
                    if isinstance(parsed_oc, dict):
                        offence_cls = OffenceClassification(**parsed_oc)
                except Exception:
                    offence_cls = None
            new_ref_obj = (
                ProvisionRef(
                    act=str(r["new_act"]),
                    jurisdiction=str(r["new_juris"] or "Central"),
                    ref=str(r["new_ref"]),
                    title=r["new_title"],
                    kind=r["new_kind"],
                )
                if r["new_doc_id"] and r["new_act"]
                else None
            )
            entries.append(
                ConcordanceEntry(
                    old_doc_id=str(r["old_doc_id"]),
                    old_ref=ProvisionRef(
                        act=str(r["old_act"]),
                        jurisdiction=str(r["old_juris"] or "Central"),
                        ref=str(r["old_ref"]),
                        title=r["old_title"],
                        kind=r["old_kind"],
                    ),
                    new_doc_id=r["new_doc_id"],
                    new_ref=new_ref_obj,
                    mapping_type=r["mapping_type"],
                    substantive_delta=str(r["substantive_delta"]),
                    punishment_delta=r["punishment_delta"],
                    procedural_delta=r["procedural_delta"],
                    savings_note=r["savings_note"],
                    verified_by=str(r["verified_by"]),
                    provenance=SourceProvenance(
                        source_id=str(r["source_id"]),
                        source_name=str(r["src_name"]),
                        source_url=str(r["src_url"]),
                        license_spdx=str(r["src_license"]),
                        commercial_safe=bool(r["commercial_safe"]),
                        terms_status=r["src_terms_status"],
                        as_of=_parse_date(r["src_as_of"]) or Date(2026, 11, 22),
                    ),
                    offence_classification=offence_cls,
                )
            )
        return entries

    def get_provision_history(
        self,
        act: str,
        ref: str | None = None,
        *,
        amendment_number: int | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        limit: int = 50,
        offset: int = 0,
        commercial_safe_only: bool | None = None,
    ) -> ProvisionHistory:
        con = self._conn()
        act_id = self._resolve_act_id(act)
        if act_id is None:
            raise NotFound(f"Act {act!r} not found in corpus.", kind="act")

        limit = max(1, min(int(limit), 106))
        offset = max(0, int(offset))
        where = ["pa.target_act_id = ?"]
        params: list[Any] = [act_id]
        if ref is not None:
            ref_norm, _ = _normalize_ref(ref)
            where.append(
                "(upper(pa.target_ref_norm) = upper(?) OR ',' || upper(replace(coalesce(pa.articles_affected,''), ' ', '')) || ',' LIKE ?)"
            )
            params.extend([ref_norm, f"%,{ref_norm.upper()},%"])
        if amendment_number is not None:
            where.append("pa.amendment_no = ?")
            params.append(int(amendment_number))
        if year_from is not None:
            where.append("cast(substr(pa.enacted_date, 1, 4) as integer) >= ?")
            params.append(int(year_from))
        if year_to is not None:
            where.append("cast(substr(pa.enacted_date, 1, 4) as integer) <= ?")
            params.append(int(year_to))

        where_sql = " AND ".join(where)
        total = int(
            con.execute(f"SELECT count(*) FROM provision_amendments pa WHERE {where_sql}", params).fetchone()[0]
        )
        rows = con.execute(
            "SELECT pa.*, a.short_name AS act_short, s.name AS src_name, s.url AS src_url, "
            "s.license_spdx AS src_license, s.terms_status AS src_terms_status, "
            "s.as_of AS src_as_of, s.commercial_safe AS commercial_safe "
            "FROM provision_amendments pa "
            "JOIN acts a ON pa.target_act_id = a.act_id "
            "JOIN sources s ON pa.source_id = s.source_id "
            f"WHERE {where_sql} ORDER BY coalesce(pa.amendment_no, 0), pa.enacted_date LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()

        amends: list[AmendmentRecord] = []
        for r in rows:
            enacted = _parse_date(r["enacted_date"])
            affected = [
                x.strip()
                for x in str(r["articles_affected"] or "").split(",")
                if x.strip()
            ]
            summary_str = str(r["summary"])
            amends.append(
                AmendmentRecord(
                    amendment_id=str(r["amendment_id"]),
                    number=r["amendment_no"],
                    title=str(r["amending_act"]),
                    act=str(r["act_short"]),
                    year=enacted.year if enacted else None,
                    enactment_date=enacted,
                    effective_date=_parse_date(r["effective_date"]),
                    action=str(r["action"]),
                    provisions_affected=affected,
                    summary=summary_str,
                    provenance=SourceProvenance(
                        source_id=str(r["source_id"]),
                        source_name=str(r["src_name"]),
                        source_url=str(r["src_url"]),
                        license_spdx=str(r["src_license"]),
                        commercial_safe=bool(r["commercial_safe"]),
                        terms_status=r["src_terms_status"],
                        as_of=_parse_date(r["src_as_of"]) or Date(2026, 11, 22),
                        content_sha256=hashlib.sha256(summary_str.encode("utf-8")).hexdigest(),
                    ),
                )
            )

        inval_events: list[InvalidatingJudgment] = []
        if ref is not None:
            ref_norm, _ = _normalize_ref(ref)
            inval_rows = con.execute(
                "SELECT ji.* FROM judicial_invalidations ji "
                "JOIN provisions p ON ji.target_doc_id = p.doc_id "
                "WHERE p.act_id = ? AND upper(p.ref_norm) = upper(?)",
                (act_id, ref_norm),
            ).fetchall()
            for ir in inval_rows:
                inval_events.append(
                    InvalidatingJudgment(
                        citation=str(ir["citation"]),
                        case_id=str(ir["case_id"]),
                        case_name=str(ir["case_name"]),
                        decision_date=_parse_date(ir["decision_date"]),
                        coram_size=ir["bench_strength"],
                        effect=ir["effect"],
                        holding_summary=str(ir["holding_summary"]),
                    )
                )

        return ProvisionHistory(
            act=act,
            ref=ref,
            amendments=amends,
            judicial_validity_events=inval_events,
            total=total,
        )

    def _provision_ref_for_id(self, con: sqlite3.Connection, doc_id: str) -> ProvisionRef | None:
        r = con.execute(
            "SELECT p.ref, p.title, p.kind, a.short_name, a.jurisdiction "
            "FROM provisions p JOIN acts a ON p.act_id = a.act_id WHERE p.doc_id = ?",
            (doc_id,),
        ).fetchone()
        if not r:
            return None
        return ProvisionRef(
            act=str(r["short_name"]),
            jurisdiction=str(r["jurisdiction"] or "Central"),
            ref=str(r["ref"]),
            title=r["title"],
            kind=r["kind"],
        )

    def get_cross_refs(
        self,
        doc_id: str,
        *,
        direction: CrossRefDirection = "both",
        relation: str | None = None,
        limit: int = 50,
        offset: int = 0,
        commercial_safe_only: bool | None = None,
    ) -> tuple[list[CitationEdgeRecord], int]:
        con = self._conn()
        limit = max(1, min(int(limit), 100))
        offset = max(0, int(offset))

        where_parts: list[str] = []
        params: list[Any] = []
        if direction == "from":
            where_parts.append("from_id = ?")
            params.append(doc_id)
        elif direction == "to":
            where_parts.append("to_id = ?")
            params.append(doc_id)
        else:
            where_parts.append("(from_id = ? OR to_id = ?)")
            params.extend([doc_id, doc_id])
        if relation:
            where_parts.append("relation = ?")
            params.append(relation)

        where_sql = " AND ".join(where_parts)
        total = int(con.execute(f"SELECT count(*) FROM citation_edges WHERE {where_sql}", params).fetchone()[0])
        rows = con.execute(
            f"SELECT * FROM citation_edges WHERE {where_sql} ORDER BY coalesce(from_year, 0) DESC, edge_id LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()

        records: list[CitationEdgeRecord] = []
        for r in rows:
            from_ref = self._provision_ref_for_id(con, str(r["from_id"])) if r["from_kind"] == "provision" else None
            to_ref = self._provision_ref_for_id(con, str(r["to_id"])) if r["to_kind"] == "provision" else None
            records.append(
                CitationEdgeRecord(
                    from_id=str(r["from_id"]),
                    from_kind=r["from_kind"],
                    to_id=str(r["to_id"]),
                    to_kind=r["to_kind"],
                    relation=str(r["relation"]),
                    para_ref=r["para_ref"],
                    quoting_excerpt=r["quoting_excerpt"],
                    from_year=r["from_year"],
                    from_bench_strength=r["from_bench_str"],
                    precedent_status=r["precedent_status"],
                    from_ref=from_ref,
                    to_ref=to_ref,
                )
            )
        return records, total

    def _row_to_judgment_meta(self, row: sqlite3.Row) -> JudgmentMeta:
        reps: list[str] = []
        for col in ("air_citation", "scc_citation", "scr_citation"):
            val = row[col]
            if val:
                reps.append(str(val))
        coram = [j.strip() for j in str(row["judge_coram"] or "").split(";") if j.strip()]
        acts_cited = json.loads(row["acts_cited_json"] or "[]")
        return JudgmentMeta(
            case_id=str(row["case_id"]),
            court_tier=_TIER_TO_PUBLIC.get(str(row["tier"]), "high_court"),  # type: ignore[arg-type]
            court_code=str(row["court_code"]),
            court_name=str(row["court_name"]),
            bench=row["bench"],
            cnr=row["cnr"],
            neutral_citation=row["neutral_citation"],
            air_citation=row["air_citation"],
            scc_citation=row["scc_citation"],
            scr_citation=row["scr_citation"],
            reporter_citations=reps,
            title=str(row["title"]),
            petitioner=row["petitioner"],
            respondent=row["respondent"],
            decision_date=_parse_date(row["decision_date"]),
            year=row["year"],
            coram=coram,
            author_judge=row["author_judge"],
            bench_strength=row["bench_strength"],
            disposal_nature=row["disposal_nature"],
            case_type=row["case_type"],
            acts_cited=acts_cited,
            is_reportable=bool(row["is_reportable"]),
            pdf_s3_bucket=row["pdf_s3_bucket"],
            pdf_s3_key=row["pdf_s3_key"],
            source_url=str(row["source_url"]),
            has_lance_text=bool(row["has_lance_text"]),
            cited_by_count=int(row["cited_by_count"]),
            text_sha256=str(row["text_sha256"]),
            provenance=_row_provenance(row, content_sha256=str(row["text_sha256"])),
        )

    def resolve_judgment_id(
        self,
        identifier: str,
        *,
        court: str | None = None,
        year: int | None = None,
        commercial_safe_only: bool | None = None,
    ) -> list[JudgmentMeta]:
        con = self._conn()
        cs_only = self._effective_cs(commercial_safe_only)
        ident = BIDI_RE.sub("", strip_control_chars(identifier)).strip()
        if not ident:
            return []

        base_sql = (
            "SELECT jm.*, s.name AS src_name, s.url AS src_url, s.license_spdx AS src_license, "
            "s.terms_status AS src_terms_status, s.as_of AS src_as_of "
            "FROM judgments_meta jm JOIN sources s ON jm.source_id = s.source_id "
        )

        # 1. Exact case_id / CNR / Neutral / AIR / SCC / SCR lookup
        parsed = parse_single_citation(ident)
        exact_rows: list[sqlite3.Row] = []

        exact_rows = con.execute(
            base_sql
            + "WHERE jm.case_id = ? OR upper(jm.cnr) = upper(?) "
            + "OR lower(jm.neutral_citation) = lower(?) "
            + "OR lower(jm.air_citation) = lower(?) "
            + "OR lower(jm.scc_citation) = lower(?) "
            + "OR lower(jm.scr_citation) = lower(?)",
            (ident, ident, ident, ident, ident, ident),
        ).fetchall()

        if not exact_rows and parsed is not None:
            if parsed.kind == "cnr":
                exact_rows = con.execute(
                    base_sql + "WHERE upper(jm.cnr) = upper(?)",
                    (parsed.base_ref,),
                ).fetchall()
            elif parsed.kind in {"sc_neutral", "hc_neutral"}:
                exact_rows = con.execute(
                    base_sql + "WHERE lower(jm.neutral_citation) = lower(?)",
                    (parsed.base_ref,),
                ).fetchall()
            elif parsed.kind == "air":
                exact_rows = con.execute(
                    base_sql + "WHERE lower(jm.air_citation) = lower(?)",
                    (parsed.base_ref,),
                ).fetchall()
            elif parsed.kind == "scc":
                exact_rows = con.execute(
                    base_sql + "WHERE lower(jm.scc_citation) = lower(?)",
                    (parsed.base_ref,),
                ).fetchall()
            elif parsed.kind == "scr":
                exact_rows = con.execute(
                    base_sql + "WHERE lower(jm.scr_citation) = lower(?)",
                    (parsed.base_ref,),
                ).fetchall()

        if exact_rows:
            filtered: list[JudgmentMeta] = []
            for r in exact_rows:
                if cs_only and not bool(r["commercial_safe"]):
                    raise CommercialLicenseBlocked(
                        f"Judgment {ident!r} is restricted to non-commercial use ({r['src_license']}).",
                        license_spdx=str(r["src_license"]),
                    )
                filtered.append(self._row_to_judgment_meta(r))
            return filtered

        # 2. Case name / title substring lookup (when >= 4 chars)
        if len(ident) < 4:
            return []
        where = ["lower(jm.title) LIKE ?"]
        params: list[Any] = [f"%{ident.lower()}%"]
        if court:
            canon = canonicalize_court(court) or court
            where.append("(lower(jm.court_code) = lower(?) OR lower(jm.court_name) LIKE ?)")
            params.extend([canon, f"%{court.lower()}%"])
        if year is not None:
            where.append("jm.year = ?")
            params.append(int(year))
        if cs_only:
            where.append("jm.commercial_safe = 1")

        rows = con.execute(
            base_sql + f"WHERE {' AND '.join(where)} ORDER BY jm.cited_by_count DESC LIMIT 10",
            params,
        ).fetchall()
        return [self._row_to_judgment_meta(r) for r in rows]

    @staticmethod
    def _meta_to_card(
        meta: JudgmentMeta,
        *,
        matched_via: str = "direct",
        snippet: str | None = None,
    ) -> JudgmentCard:
        from ..retrieval.ranking import compute_authority_level

        avail: TextAvailability = (
            "indexed_bm25"
            if meta.has_lance_text
            else ("on_demand_s3_pdf" if (meta.pdf_s3_bucket and meta.pdf_s3_key) else "metadata_only")
        )
        authority = compute_authority_level(
            meta.court_tier,
            meta.court_code,
            meta.court_name,
            binding_for_court=None,
        )
        return JudgmentCard(
            case_id=meta.case_id,
            cnr=meta.cnr,
            neutral_citation=meta.neutral_citation,
            reporter_citations=meta.reporter_citations,
            title=meta.title,
            court=meta.court_name,
            court_code=meta.court_code,
            court_tier=meta.court_tier,
            bench=meta.bench,
            decision_date=meta.decision_date,
            coram=meta.coram,
            bench_strength=meta.bench_strength,
            author_judge=meta.author_judge,
            disposal_nature=meta.disposal_nature,
            case_type=meta.case_type,
            authority_level=authority,
            acts_cited=meta.acts_cited,
            matched_via=matched_via,  # type: ignore[arg-type]
            cited_by_count=meta.cited_by_count,
            snippet=snippet or meta.snippet,
            text_availability=avail,
            provenance=meta.provenance,
        )

    def trace_precedent(
        self,
        case_id: str,
        *,
        direction: TraceDirection = "cited_by",
        court: str | None = None,
        min_bench_strength: int | None = None,
        larger_bench_only: bool = False,
        date_from: Date | None = None,
        limit: int = 25,
        offset: int = 0,
        commercial_safe_only: bool | None = None,
    ) -> PrecedentTrace:
        metas = self.resolve_judgment_id(case_id, commercial_safe_only=commercial_safe_only)
        if not metas:
            raise NotFound(f"Judgment {case_id!r} not found in corpus.", kind="judgment")
        target_meta = metas[0]
        con = self._conn()
        cs_only = self._effective_cs(commercial_safe_only)
        limit = max(1, min(int(limit), 25))
        offset = max(0, int(offset))

        cited_by_total = int(
            con.execute(
                "SELECT count(*) FROM citation_edges WHERE to_id = ? AND to_kind = 'judgment' AND from_kind = 'judgment'",
                (target_meta.case_id,),
            ).fetchone()[0]
        )
        cites_total = int(
            con.execute(
                "SELECT count(*) FROM citation_edges WHERE from_id = ? AND from_kind = 'judgment' AND to_kind = 'judgment'",
                (target_meta.case_id,),
            ).fetchone()[0]
        )
        target_bench = target_meta.bench_strength or 0
        larger_cnt = int(
            con.execute(
                "SELECT count(*) FROM citation_edges WHERE to_id = ? AND to_kind = 'judgment' "
                "AND from_kind = 'judgment' AND coalesce(from_bench_str, 0) > ?",
                (target_meta.case_id, target_bench),
            ).fetchone()[0]
        )

        edges_rows: list[tuple[str, sqlite3.Row]] = []
        if direction in ("cited_by", "both"):
            for r in con.execute(
                "SELECT * FROM citation_edges WHERE to_id = ? AND to_kind = 'judgment' AND from_kind = 'judgment' "
                "ORDER BY coalesce(from_bench_str, 0) DESC, coalesce(from_year, 0) DESC",
                (target_meta.case_id,),
            ).fetchall():
                edges_rows.append(("cited_by", r))
        if direction in ("cites", "both"):
            for r in con.execute(
                "SELECT * FROM citation_edges WHERE from_id = ? AND from_kind = 'judgment' AND to_kind = 'judgment' "
                "ORDER BY edge_id",
                (target_meta.case_id,),
            ).fetchall():
                edges_rows.append(("cites", r))

        filtered_edges: list[CitationEdgeRecord] = []
        for edge_dir, er in edges_rows:
            rel_id = str(er["from_id"]) if edge_dir == "cited_by" else str(er["to_id"])
            rel_rows = self.resolve_judgment_id(rel_id, commercial_safe_only=False)
            if not rel_rows:
                continue
            rel_meta = rel_rows[0]
            if cs_only and not rel_meta.provenance.commercial_safe:
                continue
            if court:
                canon = canonicalize_court(court) or court
                if rel_meta.court_code.lower() != canon.lower() and court.lower() not in rel_meta.court_name.lower():
                    continue
            if min_bench_strength is not None and (rel_meta.bench_strength or 0) < min_bench_strength:
                continue
            if larger_bench_only and (rel_meta.bench_strength or 0) <= target_bench:
                continue
            if date_from is not None and rel_meta.decision_date and rel_meta.decision_date < date_from:
                continue

            rel_bench = rel_meta.bench_strength
            if rel_bench is not None and target_meta.bench_strength is not None:
                if rel_bench > target_meta.bench_strength:
                    comp = "larger_bench"
                elif rel_bench == target_meta.bench_strength:
                    comp = "co_equal_bench"
                else:
                    comp = "smaller_bench"
            else:
                comp = "unknown"

            para_num: int | None = None
            if er["para_ref"]:
                pm = re.search(r"(\d+)", str(er["para_ref"]))
                if pm:
                    para_num = int(pm.group(1))

            filtered_edges.append(
                CitationEdgeRecord(
                    from_id=str(er["from_id"]),
                    from_kind=er["from_kind"],
                    to_id=str(er["to_id"]),
                    to_kind=er["to_kind"],
                    relation=str(er["relation"]),
                    para_ref=er["para_ref"],
                    quoting_excerpt=er["quoting_excerpt"],
                    from_year=er["from_year"],
                    from_bench_strength=er["from_bench_str"],
                    precedent_status=er["precedent_status"],
                    related_case=self._meta_to_card(rel_meta),
                    direction=edge_dir,  # type: ignore[arg-type]
                    bench_comparison=comp,  # type: ignore[arg-type]
                    citing_para_num=para_num,
                    provenance=rel_meta.provenance,
                )
            )

        total = len(filtered_edges)
        sliced = filtered_edges[offset : offset + limit]
        return PrecedentTrace(
            target=self._meta_to_card(target_meta),
            precedent_status="unverified_automated",
            verification_notice=_VERIFICATION_NOTICE,
            cited_by_total=cited_by_total,
            cites_total=cites_total,
            cited_by_larger_bench_count=larger_cnt,
            edges=sliced,
            total=total,
        )

    def search_statutes_fts(
        self,
        query: str,
        *,
        act: str | None = None,
        jurisdiction: str | None = None,
        kind: str | None = None,
        category: str | None = None,
        status: str | None = None,
        exact_phrase: bool = False,
        limit: int = 25,
        offset: int = 0,
        commercial_safe_only: bool | None = None,
    ) -> list[StatuteHit]:
        con = self._conn()
        cs_only = self._effective_cs(commercial_safe_only)
        norm_juris = normalize_jurisdiction(jurisdiction)
        all_tokens, content_tokens = _extract_fts_tokens(query)
        if not all_tokens:
            return []

        where = ["provisions_fts MATCH ?"]
        base_params: list[Any] = []
        if cs_only:
            where.append("p.commercial_safe = 1")
        if act:
            act_id = self._resolve_act_id(act, jurisdiction=norm_juris)
            if act_id is None:
                return []
            where.append("p.act_id = ?")
            base_params.append(act_id)
        if norm_juris:
            where.append("lower(a.jurisdiction) = lower(?)")
            base_params.append(norm_juris)
        if kind:
            where.append("p.kind = ?")
            base_params.append(kind)
        if category:
            where.append("a.category = ?")
            base_params.append(category.strip())
        if status and status != "all":
            where.append("p.status = ?")
            base_params.append(status)

        prefer_in_force = status is None or status == "all"
        order_expr = (
            "(bm25(provisions_fts, 0.0, 2.0, 3.0, 6.0, 1.0) "
            "- CASE WHEN p.status = 'in_force' THEN 0.5 ELSE 0.0 END)"
            if prefer_in_force
            else "fts_rank"
        )
        sql = (
            "SELECT p.*, a.short_name AS act_short_name, a.jurisdiction AS act_jurisdiction, "
            "a.category AS act_category, "
            "s.name AS src_name, s.url AS src_url, s.license_spdx AS src_license, "
            "s.terms_status AS src_terms_status, s.as_of AS src_as_of, "
            "bm25(provisions_fts, 0.0, 2.0, 3.0, 6.0, 1.0) AS fts_rank "
            "FROM provisions_fts "
            "JOIN provisions p ON provisions_fts.rowid = p.rowid "
            "JOIN acts a ON p.act_id = a.act_id "
            "JOIN sources s ON p.source_id = s.source_id "
            f"WHERE {' AND '.join(where)} ORDER BY {order_expr} LIMIT ? OFFSET ?"
        )

        def _run_fts(expr: str, q_limit: int, q_offset: int) -> list[sqlite3.Row]:
            if not expr:
                return []
            return con.execute(sql, [expr, *base_params, q_limit, q_offset]).fetchall()

        distinct_content = _distinct_lower(content_tokens)
        q_lower = query.lower()
        q_bigrams = [
            f"{distinct_content[i]} {distinct_content[i + 1]}"
            for i in range(len(distinct_content) - 1)
        ]

        def _score_row(r: sqlite3.Row, *, in_and: bool) -> float:
            s = max(0.0, -float(r["fts_rank"]))
            if in_and:
                s += 3.0
            if prefer_in_force and r["status"] == "in_force":
                s += 0.5
            act_short_low = str(r["act_short_name"] or "").strip().lower()
            if act_short_low and re.search(rf"(?<![a-z0-9]){re.escape(act_short_low)}(?![a-z0-9])", q_lower):
                s += 10.0
            if r["act_category"] == "constitution":
                s += 24.0 if r["kind"] == "article" else 3.0
            title_str = str(r["title"] or "").lower()
            _, raw_title_toks = _extract_fts_tokens(title_str)
            title_toks = _distinct_lower(raw_title_toks)
            if len(title_toks) >= 2:
                matched_t = sum(
                    1
                    for tt in title_toks
                    if any(
                        _token_matches_boundary(qt, tt) or _token_matches_boundary(tt, qt)
                        for qt in distinct_content
                    )
                )
                if matched_t == len(title_toks):
                    s += 15.0
                elif matched_t >= 2:
                    s += 6.0 * (matched_t / len(title_toks))
            if q_bigrams:
                body_low = f"{title_str} {str(r['text'] or '').lower()}"
                bg_hits = sum(1 for bg in q_bigrams if bg in body_low)
                if bg_hits > 0:
                    s += min(12.0, 6.0 * bg_hits)
            return s

        custom_scores: dict[str, float] = {}
        rows: list[sqlite3.Row]
        if exact_phrase or len(distinct_content) < 2:
            fts_expr = _fts_query(query, exact_phrase=exact_phrase, operator="OR")
            rows = _run_fts(fts_expr, limit, offset)
        elif len(distinct_content) == 2:
            and_expr = _fts_query(query, exact_phrase=False, operator="AND", max_tokens=8)
            raw_rows = _run_fts(and_expr, max(50, limit + offset), 0)
            scored = sorted(
                ((_score_row(r, in_and=True), -len(str(r["ref"] or "")), r) for r in raw_rows),
                key=lambda x: (x[0], x[1]),
                reverse=True,
            )
            for score_val, _, r in scored:
                custom_scores[str(r["doc_id"])] = round(score_val, 4)
            rows = [r for _, _, r in scored[offset : offset + limit]]
        else:
            need = limit + offset
            and_expr = _fts_query(query, exact_phrase=False, operator="AND", max_tokens=8)
            and_rows = _run_fts(and_expr, max(50, need * 3), 0)
            or_expr = _fts_query(query, exact_phrase=False, operator="OR", max_tokens=16)
            or_candidates = _run_fts(or_expr, max(need * 10, 400), 0)
            min_required = _multitoken_min_required(len(distinct_content))
            and_ids = {str(r["doc_id"]) for r in and_rows}
            seen_ids: set[str] = set()
            candidates: list[tuple[float, int, sqlite3.Row]] = []
            for r in (*and_rows, *or_candidates):
                doc_id = str(r["doc_id"])
                if doc_id in seen_ids:
                    continue
                seen_ids.add(doc_id)
                in_and = doc_id in and_ids
                if not in_and:
                    haystack = (
                        f"{r['title'] or ''} {r['text'] or ''} "
                        f"{r['act_short_name'] or ''} {r['ref'] or ''}"
                    ).lower()
                    if sum(1 for tok in distinct_content if _token_matches_boundary(tok, haystack)) < min_required:
                        continue
                score_val = _score_row(r, in_and=in_and)
                custom_scores[doc_id] = round(score_val, 4)
                candidates.append((score_val, -len(str(r["ref"] or "")), r))
            candidates.sort(key=lambda x: (x[0], x[1]), reverse=True)
            rows = [r for _, _, r in candidates[offset : offset + limit]]

        hits: list[StatuteHit] = []
        for r in rows:
            conc = con.execute(
                "SELECT p.ref, p.title, p.kind, a.short_name, a.jurisdiction "
                "FROM concordance c JOIN provisions p ON c.new_doc_id = p.doc_id "
                "JOIN acts a ON p.act_id = a.act_id WHERE c.old_doc_id = ?",
                (r["doc_id"],),
            ).fetchall()
            replaced_by = [
                ProvisionRef(
                    act=str(cr["short_name"]),
                    jurisdiction=str(cr["jurisdiction"] or "Central"),
                    ref=str(cr["ref"]),
                    title=cr["title"],
                    kind=cr["kind"],
                )
                for cr in conc
            ]
            # SQLite bm25() returns negative scores (more negative = better match)
            score = custom_scores.get(str(r["doc_id"]))
            if score is None:
                score = max(0.0, -float(r["fts_rank"]))
                if prefer_in_force and r["status"] == "in_force":
                    score += 0.5
            hits.append(
                StatuteHit(
                    doc_id=str(r["doc_id"]),
                    act=str(r["act_short_name"]),
                    jurisdiction=str(r["act_jurisdiction"]),
                    kind=r["kind"],
                    ref=str(r["ref"]),
                    title=r["title"],
                    status=r["status"],
                    replaced_by=replaced_by,
                    snippet=str(r["text"])[:400],
                    bm25_score=score,
                    provenance=_row_provenance(r, content_sha256=str(r["text_sha256"])),
                )
            )
        return hits

    def search_judgments_fts(
        self,
        query: str | None = None,
        *,
        court: str | list[str] | None = None,
        tier: str | None = None,
        judge: str | None = None,
        author_judge: str | None = None,
        party: str | None = None,
        min_bench_strength: int | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        disposal_nature: str | None = None,
        case_type: str | None = None,
        limit: int = 25,
        offset: int = 0,
        commercial_safe_only: bool | None = None,
    ) -> list[JudgmentCard]:
        con = self._conn()
        cs_only = self._effective_cs(commercial_safe_only)

        where = ["1=1"]
        params: list[Any] = []
        join_fts = ""
        order_by = "jm.cited_by_count DESC, jm.year DESC"

        if query:
            fts_expr = _fts_query(query)
            if fts_expr:
                join_fts = "JOIN judgments_meta_fts ON judgments_meta_fts.rowid = jm.rowid "
                where.append("judgments_meta_fts MATCH ?")
                params.append(fts_expr)
                order_by = "bm25(judgments_meta_fts), jm.cited_by_count DESC"

        if cs_only:
            where.append("jm.commercial_safe = 1")
        if tier and tier != "all":
            tier_map = {"supreme_court": "sc", "high_court": "hc", "tribunal": "tribunal", "district_court": "district"}
            db_tier = tier_map.get(tier, tier)
            where.append("jm.tier = ?")
            params.append(db_tier)
        if court:
            c_list = [court] if isinstance(court, str) else list(court)
            c_clauses = []
            for c in c_list:
                for variant in court_code_variants(c):
                    c_clauses.append("(lower(jm.court_code) = lower(?) OR lower(jm.court_name) LIKE ?)")
                    params.extend([variant, f"%{variant.lower()}%"])
            where.append("(" + " OR ".join(c_clauses) + ")")
        if judge:
            where.append("lower(coalesce(jm.judge_coram,'')) LIKE ?")
            params.append(f"%{judge.strip().lower()}%")
        if author_judge:
            where.append("lower(coalesce(jm.author_judge,'')) LIKE ?")
            params.append(f"%{author_judge.strip().lower()}%")
        if party:
            plike = f"%{party.strip().lower()}%"
            where.append("(lower(coalesce(jm.petitioner,'')) LIKE ? OR lower(coalesce(jm.respondent,'')) LIKE ? OR lower(jm.title) LIKE ?)")
            params.extend([plike, plike, plike])
        if min_bench_strength is not None:
            where.append("coalesce(jm.bench_strength, 0) >= ?")
            params.append(int(min_bench_strength))
        if year_from is not None:
            where.append("jm.year >= ?")
            params.append(int(year_from))
        if year_to is not None:
            where.append("jm.year <= ?")
            params.append(int(year_to))
        if disposal_nature:
            where.append("lower(coalesce(jm.disposal_nature,'')) LIKE ?")
            params.append(f"%{disposal_nature.strip().lower()}%")
        if case_type:
            where.append("lower(coalesce(jm.case_type,'')) LIKE ?")
            params.append(f"%{case_type.strip().lower()}%")

        has_structured_filter = any(
            [court, judge, author_judge, party, min_bench_strength is not None, disposal_nature, case_type]
        )
        j_tokens: list[str] = []
        if query and not has_structured_filter:
            _, raw_j_tokens = _extract_fts_tokens(query)
            j_tokens = _distinct_lower(raw_j_tokens)
        apply_trap_filter = len(j_tokens) >= 4
        fetch_limit = max(limit * 4, 100) if apply_trap_filter else limit
        fetch_offset = 0 if apply_trap_filter else offset

        rows = con.execute(
            "SELECT jm.*, s.name AS src_name, s.url AS src_url, s.license_spdx AS src_license, "
            "s.terms_status AS src_terms_status, s.as_of AS src_as_of "
            f"FROM judgments_meta jm {join_fts}"
            "JOIN sources s ON jm.source_id = s.source_id "
            f"WHERE {' AND '.join(where)} ORDER BY {order_by} LIMIT ? OFFSET ?",
            [*params, fetch_limit, fetch_offset],
        ).fetchall()
        if apply_trap_filter:
            min_req = _multitoken_min_required(len(j_tokens))
            filtered_rows = []
            for r in rows:
                haystack = (
                    f"{r['title'] or ''} {r['petitioner'] or ''} {r['respondent'] or ''} "
                    f"{r['judge_coram'] or ''} {r['neutral_citation'] or ''} "
                    f"{r['air_citation'] or ''} {r['scc_citation'] or ''}"
                ).lower()
                if sum(1 for tok in j_tokens if _token_matches_boundary(tok, haystack)) >= min_req:
                    filtered_rows.append(r)
            rows = filtered_rows[offset : offset + limit]
        return [self._meta_to_card(self._row_to_judgment_meta(r), matched_via="metadata") for r in rows]

    def list_courts(self, query: str | None = None) -> list[CourtInfo]:
        con = self._conn()
        if query:
            like = f"%{query.strip().lower()}%"
            rows = con.execute(
                "SELECT * FROM courts WHERE lower(court_code) LIKE ? OR lower(court_name) LIKE ? OR lower(state_ut) LIKE ? ORDER BY tier, court_code",
                (like, like, like),
            ).fetchall()
        else:
            rows = con.execute("SELECT * FROM courts ORDER BY tier, court_code").fetchall()
        return [
            CourtInfo(
                court_code=str(r["court_code"]),
                court_name=str(r["court_name"]),
                tier=r["tier"],
                bench_code=r["bench_code"],
                bench_name=r["bench_name"],
                state_ut=str(r["state_ut"]),
                seat_city=r["seat_city"],
                hc_jurisdiction=[x.strip() for x in str(r["hc_jurisdiction"] or "").split(",") if x.strip()],
            )
            for r in rows
        ]

    def corpus_status(
        self,
        *,
        court: str | None = None,
        include_sources: bool = True,
        commercial_safe_only: bool | None = None,
    ) -> CorpusStatusResponse:
        con = self._conn()
        cs_only = self._effective_cs(commercial_safe_only)
        cs_filter = " AND commercial_safe = 1" if cs_only else ""

        def _cnt(sql: str) -> int:
            return int(con.execute(sql).fetchone()[0])

        counts = CorpusCounts(
            legislation_jurisdictions=_cnt(f"SELECT count(DISTINCT jurisdiction) FROM acts WHERE 1=1{cs_filter}"),
            central_acts=_cnt(f"SELECT count(*) FROM acts WHERE category='central_act'{cs_filter}"),
            state_acts=_cnt(f"SELECT count(*) FROM acts WHERE category='state_act'{cs_filter}"),
            regulators=_cnt(f"SELECT count(*) FROM acts WHERE category='regulator'{cs_filter}"),
            tribunals=_cnt("SELECT count(DISTINCT court_code) FROM courts WHERE tier='tribunal'"),
            provisions=_cnt(f"SELECT count(*) FROM provisions WHERE kind IN ('section','order_rule','preamble','regulation','circular','rule'){cs_filter}"),
            articles=_cnt(f"SELECT count(*) FROM provisions WHERE kind='article'{cs_filter}"),
            schedules=_cnt(f"SELECT count(*) FROM provisions WHERE kind='schedule'{cs_filter}"),
            amendments=_cnt("SELECT count(*) FROM provision_amendments"),
            concordance_mappings=_cnt("SELECT count(*) FROM concordance"),
            judicial_invalidations=_cnt("SELECT count(*) FROM judicial_invalidations"),
            sc_judgments=_cnt(f"SELECT count(*) FROM judgments_meta WHERE tier='sc'{cs_filter}"),
            hc_judgments=_cnt(f"SELECT count(*) FROM judgments_meta WHERE tier='hc'{cs_filter}"),
            tribunal_orders=_cnt(f"SELECT count(*) FROM judgments_meta WHERE tier='tribunal'{cs_filter}"),
            district_court_records=_cnt(f"SELECT count(*) FROM judgments_meta WHERE tier='district'{cs_filter}"),
            citation_edges=_cnt("SELECT count(*) FROM citation_edges"),
        )

        sources: list[SourceStatusEntry] = []
        as_of: Date | None = None
        src_rows = con.execute("SELECT * FROM sources ORDER BY source_id").fetchall()
        for r in src_rows:
            d = _parse_date(r["as_of"])
            if d and (as_of is None or d > as_of):
                as_of = d
            if cs_only and not bool(r["commercial_safe"]):
                continue
            if include_sources:
                sources.append(
                    SourceStatusEntry(
                        source_id=str(r["source_id"]),
                        record_count=int(r["record_count"]),
                        provenance=SourceProvenance(
                            source_id=str(r["source_id"]),
                            source_name=str(r["name"]),
                            source_url=str(r["url"]),
                            license_spdx=str(r["license_spdx"]),
                            commercial_safe=bool(r["commercial_safe"]),
                            terms_status=r["terms_status"],
                            as_of=d or Date(2026, 11, 22),
                            content_sha256=str(r["sha256"]),
                        ),
                    )
                )

        court_detail: dict[str, Any] | None = None
        if court:
            matches = self.list_courts(court)
            if matches:
                court_detail = matches[0].model_dump(mode="json")

        return CorpusStatusResponse(
            as_of=as_of,
            commercial_safe_locked=self.default_commercial_safe_only,
            tiers={"sqlite_core": "ok"},
            counts=counts,
            sources=sources,
            licensing={
                "code_license": "Apache-2.0",
                "commercial_safe_default": not self.default_commercial_safe_only,
                "nc_datasets": ["ddl_district_courts", "ildc_research"],
            },
            court_detail=court_detail,
        )

    def search_cause_lists(
        self,
        *,
        court: str | None = None,
        date: Date | str | None = None,
        bench: str | None = None,
        judge: str | None = None,
        party: str | None = None,
        advocate: str | None = None,
        case_number: str | None = None,
        cnr: str | None = None,
        limit: int = 25,
    ) -> list[CauseListEntry]:
        """Search ``cause_lists`` in SQLite, returning ``[]`` if an older artifact lacks the table."""
        from .cause_list_store import CauseListStore

        con = self._conn()
        tbl = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cause_lists'"
        ).fetchone()
        if tbl is None:
            return []
        store = CauseListStore(db_path=self.db_path)
        return store.search_cause_lists(
            court=court,
            date=date,
            bench=bench,
            judge=judge,
            party=party,
            advocate=advocate,
            case_number=case_number,
            cnr=cnr,
            limit=limit,
        )
