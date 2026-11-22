"""Hermetic synthetic mini-corpus builder for tests and offline Docker builds.

Builds:
- Read-only-ready SQLite core (``nyaya-core.sqlite``) with FTS5, concordance,
  judicial invalidations, SC/HC metadata, citation graph, and courts directory.
- LanceDB 3-table native Rust BM25 FTS directory (``lancedb/``).
- Compacted ZSTD Parquet partitions (``parquet/``) + ``manifest.json``.
- Valid multi-paragraph & scanned 1-page PDF fixtures (``pdfs/``).

Runnable directly as a module::

    python -m tests.fixtures.mini_corpus.builder /tmp/mini_corpus
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

import duckdb
import lancedb
import pyarrow as pa

MINI_SQLITE_DDL = """
PRAGMA foreign_keys = ON;

CREATE TABLE sources (
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

CREATE TABLE acts (
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
CREATE INDEX idx_acts_short_lower ON acts(lower(short_name));
CREATE INDEX idx_acts_juris_cat ON acts(jurisdiction, category, commercial_safe);

CREATE TABLE act_aliases (
    alias_lower TEXT PRIMARY KEY,
    act_id      TEXT NOT NULL REFERENCES acts(act_id) ON DELETE CASCADE
) WITHOUT ROWID;

CREATE TABLE provisions (
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
CREATE UNIQUE INDEX idx_prov_act_ref ON provisions(act_id, kind, ref_norm);
CREATE INDEX idx_prov_browse ON provisions(act_id, kind, ref_num, ref_norm);
CREATE INDEX idx_prov_chapter ON provisions(act_id, chapter_num) WHERE chapter_num IS NOT NULL;
CREATE INDEX idx_prov_status ON provisions(status) WHERE status != 'in_force';

CREATE VIRTUAL TABLE provisions_fts USING fts5(
    doc_id UNINDEXED,
    act_short,
    ref,
    title,
    text,
    content='provisions',
    content_rowid='rowid',
    tokenize="porter unicode61 remove_diacritics 2 tokenchars '.-/()'"
);

CREATE TABLE provision_amendments (
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
CREATE INDEX idx_amend_target ON provision_amendments(target_act_id, target_ref_norm);
CREATE INDEX idx_amend_year ON provision_amendments(target_act_id, enacted_date);

CREATE TABLE concordance (
    id                INTEGER PRIMARY KEY,
    old_doc_id        TEXT NOT NULL REFERENCES provisions(doc_id),
    new_doc_id        TEXT REFERENCES provisions(doc_id),
    mapping_type      TEXT NOT NULL CHECK (mapping_type IN ('one_to_one','one_to_many','many_to_one','omitted','newly_inserted')),
    substantive_delta TEXT NOT NULL,
    punishment_delta  TEXT,
    procedural_delta  TEXT,
    savings_note      TEXT,
    verified_by       TEXT NOT NULL,
    source_id         TEXT NOT NULL REFERENCES sources(source_id)
);
CREATE INDEX idx_conc_old ON concordance(old_doc_id);
CREATE INDEX idx_conc_new ON concordance(new_doc_id);

CREATE TABLE judicial_invalidations (
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
CREATE INDEX idx_inval_doc ON judicial_invalidations(target_doc_id);

CREATE TABLE judgments_meta (
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
CREATE INDEX idx_jmeta_cnr ON judgments_meta(cnr) WHERE cnr IS NOT NULL;
CREATE INDEX idx_jmeta_nc ON judgments_meta(lower(neutral_citation)) WHERE neutral_citation IS NOT NULL;
CREATE INDEX idx_jmeta_air ON judgments_meta(lower(air_citation)) WHERE air_citation IS NOT NULL;
CREATE INDEX idx_jmeta_scc ON judgments_meta(lower(scc_citation)) WHERE scc_citation IS NOT NULL;
CREATE INDEX idx_jmeta_court_yr ON judgments_meta(tier, court_code, year, commercial_safe);

CREATE VIRTUAL TABLE judgments_meta_fts USING fts5(
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

CREATE TABLE citation_edges (
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
CREATE INDEX idx_edges_from ON citation_edges(from_id, relation);
CREATE INDEX idx_edges_to ON citation_edges(to_id, relation, from_bench_str DESC, from_year DESC);

CREATE TABLE courts (
    court_code      TEXT PRIMARY KEY,
    court_name      TEXT NOT NULL,
    tier            TEXT NOT NULL CHECK (tier IN ('sc','hc','district','tribunal','regulator')),
    bench_code      TEXT,
    bench_name      TEXT,
    state_ut        TEXT NOT NULL,
    seat_city       TEXT,
    hc_jurisdiction TEXT
);
"""


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def make_minimal_pdf(pages_text: list[str]) -> bytes:
    """Create a valid PDF 1.4 document with optional text pages or blank scanned pages."""
    objects: list[tuple[int, bytes]] = []
    page_ids: list[int] = []
    next_id = 4
    page_objs: list[tuple[int, bytes]] = []

    for text in pages_text:
        content_id = next_id
        page_id = next_id + 1
        next_id += 2
        page_ids.append(page_id)
        if text:
            lines = text.splitlines() or [text]
            ops = ["BT", "/F1 12 Tf", "72 720 Td", "14 TL"]
            for idx, line in enumerate(lines):
                safe = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
                if idx == 0:
                    ops.append(f"({safe}) Tj")
                else:
                    ops.append(f"T* ({safe}) Tj")
            ops.append("ET")
            stream = "\n".join(ops).encode("latin-1", errors="replace")
        else:
            stream = b""
        content_obj = (
            f"<< /Length {len(stream)} >>\nstream\n".encode("ascii")
            + stream
            + b"\nendstream"
        )
        page_obj = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            f"/Resources << /Font << /F1 3 0 R >> >> /Contents {content_id} 0 R >>"
        ).encode("ascii")
        page_objs.append((content_id, content_obj))
        page_objs.append((page_id, page_obj))

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects.append((1, b"<< /Type /Catalog /Pages 2 0 R >>"))
    objects.append(
        (2, f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode("ascii"))
    )
    objects.append((3, b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"))
    objects.extend(page_objs)
    objects.sort(key=lambda x: x[0])

    buf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for obj_id, body in objects:
        offsets.append(len(buf))
        buf.extend(f"{obj_id} 0 obj\n".encode("ascii"))
        buf.extend(body)
        buf.extend(b"\nendobj\n")
    xref_pos = len(buf)
    buf.extend(f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode("ascii"))
    for off in offsets[1:]:
        buf.extend(f"{off:010d} 00000 n \n".encode("ascii"))
    buf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref_pos}\n%%EOF\n".encode(
            "ascii"
        )
    )
    return bytes(buf)


def _build_sqlite(sqlite_path: Path) -> None:
    if sqlite_path.exists():
        sqlite_path.unlink()
    con = sqlite3.connect(sqlite_path)
    try:
        con.executescript(MINI_SQLITE_DDL)

        sources = [
            ("vaquill-central", "Vaquill Open India Law", "https://oss-data-in.vaquill.ai/v2026.08.1/", "CC-BY-4.0", 1, "cleared", "v2026.08.1", "2026-08-01", "a" * 64, 15, None),
            ("prs-sanhitas", "PRS Legislative Research", "https://prsindia.org/billtrack/", "CC-BY-4.0", 1, "cleared", "2026-11-01", "2026-11-01", "b" * 64, 10, None),
            ("legislative-constitution", "Legislative Dept Constitution", "https://legislative.gov.in/constitution-of-india/", "IN-S52-1-q", 1, "cleared", "2026-01-01", "2026-11-01", "c" * 64, 10, None),
            ("dattam-sc", "Dattam Labs SC Judgments", "s3://indian-supreme-court-judgments", "CC-BY-4.0", 1, "cleared", "2026-11-22", "2026-11-22", "d" * 64, 3, None),
            ("dattam-hc", "Dattam Labs HC Judgments", "s3://indian-high-court-judgments", "CC-BY-4.0", 1, "cleared", "2026-11-22", "2026-11-22", "e" * 64, 2, None),
            ("ddl-district", "DDL Indian Judicial Dataset", "https://www.devdatalab.org/judicial-data", "CC-BY-NC-SA-4.0", 0, "non_commercial_only", "2018-12-31", "2018-12-31", "f" * 64, 2, "Non-commercial research dataset"),
            ("ildc-research", "ILDC Supreme Court Pack", "https://huggingface.co/datasets/ildc", "CC-BY-NC-4.0", 0, "non_commercial_only", "2021-01-01", "2021-01-01", "0" * 64, 1, "Non-commercial research dataset"),
        ]
        con.executemany("INSERT INTO sources VALUES (?,?,?,?,?,?,?,?,?,?,?)", sources)

        acts = [
            ("constitution", "Constitution", "The Constitution of India", 1950, "Constitution", "Central", "constitution", "in_force", "1950-01-26", None, None, "sqlite_core", "legislative-constitution", 1, "2026-11-22"),
            ("bns-2023", "BNS", "The Bharatiya Nyaya Sanhita, 2023", 2023, "Act No. 45 of 2023", "Central", "central_act", "in_force", "2024-07-01", None, None, "sqlite_core", "prs-sanhitas", 1, "2026-11-22"),
            ("ipc-1860", "IPC", "The Indian Penal Code, 1860", 1860, "Act No. 45 of 1860", "Central", "central_act", "repealed", "1862-01-01", "2024-07-01", "bns-2023", "sqlite_core", "vaquill-central", 1, "2026-11-22"),
            ("bnss-2023", "BNSS", "The Bharatiya Nagarik Suraksha Sanhita, 2023", 2023, "Act No. 46 of 2023", "Central", "central_act", "in_force", "2024-07-01", None, None, "sqlite_core", "prs-sanhitas", 1, "2026-11-22"),
            ("crpc-1973", "CrPC", "The Code of Criminal Procedure, 1973", 1973, "Act No. 2 of 1974", "Central", "central_act", "repealed", "1974-04-01", "2024-07-01", "bnss-2023", "sqlite_core", "vaquill-central", 1, "2026-11-22"),
            ("bsa-2023", "BSA", "The Bharatiya Sakshya Adhiniyam, 2023", 2023, "Act No. 47 of 2023", "Central", "central_act", "in_force", "2024-07-01", None, None, "sqlite_core", "prs-sanhitas", 1, "2026-11-22"),
            ("iea-1872", "EvidenceAct", "The Indian Evidence Act, 1872", 1872, "Act No. 1 of 1872", "Central", "central_act", "repealed", "1872-09-01", "2024-07-01", "bsa-2023", "sqlite_core", "vaquill-central", 1, "2026-11-22"),
            ("cpc-1908", "CPC", "The Code of Civil Procedure, 1908", 1908, "Act No. 5 of 1908", "Central", "central_act", "in_force", "1909-01-01", None, None, "sqlite_core", "vaquill-central", 1, "2026-11-22"),
            ("it-act-2000", "ITAct", "The Information Technology Act, 2000", 2000, "Act No. 21 of 2000", "Central", "central_act", "in_force", "2000-10-17", None, None, "sqlite_core", "vaquill-central", 1, "2026-11-22"),
            ("mh-mofa-1963", "MH-MOFA", "Maharashtra Ownership Flats Act, 1963", 1963, "Mah. Act 45 of 1963", "MH", "state_act", "in_force", "1964-02-10", None, None, "lance_bucket", "vaquill-central", 1, "2026-11-22"),
            ("sebi-lodr-2015", "SEBI-LODR", "SEBI (Listing Obligations and Disclosure Requirements) Regulations, 2015", 2015, "SEBI/LAD-NRO/GN/2015-16/013", "SEBI", "regulator", "in_force", "2015-12-01", None, None, "lance_bucket", "vaquill-central", 1, "2026-11-22"),
        ]
        con.executemany("INSERT INTO acts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", acts)

        aliases = [
            ("constitution", "constitution"),
            ("constitution of india", "constitution"),
            ("the constitution", "constitution"),
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
            ("information technology act", "it-act-2000"),
            ("mh-mofa", "mh-mofa-1963"),
            ("mofa", "mh-mofa-1963"),
            ("sebi lodr", "sebi-lodr-2015"),
            ("sebi-lodr", "sebi-lodr-2015"),
        ]
        con.executemany("INSERT INTO act_aliases VALUES (?,?)", aliases)

        prov_rows = [
            ("constitution:art:14", "constitution", "Constitution", "article", "14", "14", 14, None, None, "Part III - Fundamental Rights", "Equality before law", "The State shall not deny to any person equality before the law or the equal protection of the laws within the territory of India.", "in_force", "1950-01-26", None, "{}", "legislative-constitution", "https://legislative.gov.in/constitution-of-india/", 1),
            ("constitution:art:19", "constitution", "Constitution", "article", "19", "19", 19, None, None, "Part III - Fundamental Rights", "Protection of certain rights regarding freedom of speech, etc.", "All citizens shall have the right to freedom of speech and expression.", "in_force", "1950-01-26", None, "{}", "legislative-constitution", "https://legislative.gov.in/constitution-of-india/", 1),
            ("constitution:art:21", "constitution", "Constitution", "article", "21", "21", 21, None, None, "Part III - Fundamental Rights", "Protection of life and personal liberty", "No person shall be deprived of his life or personal liberty except according to procedure established by law.", "in_force", "1950-01-26", None, "{}", "legislative-constitution", "https://legislative.gov.in/constitution-of-india/", 1),
            ("constitution:art:21A", "constitution", "Constitution", "article", "21A", "21A", 21, None, None, "Part III - Fundamental Rights", "Right to education", "The State shall provide free and compulsory education to all children of the age of six to fourteen years.", "in_force", "2002-12-12", None, "{}", "legislative-constitution", "https://legislative.gov.in/constitution-of-india/", 1),
            ("constitution:art:32", "constitution", "Constitution", "article", "32", "32", 32, None, None, "Part III - Fundamental Rights", "Remedies for enforcement of rights conferred by this Part", "The right to move the Supreme Court by appropriate proceedings for the enforcement of the rights conferred by this Part is guaranteed.", "in_force", "1950-01-26", None, "{}", "legislative-constitution", "https://legislative.gov.in/constitution-of-india/", 1),
            ("constitution:sch:7", "constitution", "Constitution", "schedule", "Schedule 7", "SCH7", 7, None, None, "Schedules", "Seventh Schedule - Union, State and Concurrent Lists", "List I - Union List; List II - State List; List III - Concurrent List.", "in_force", "1950-01-26", None, "{}", "legislative-constitution", "https://legislative.gov.in/constitution-of-india/", 1),
            ("ipc-1860:2", "ipc-1860", "IPC", "section", "2", "2", 2, "I", "Introduction", None, "Definitions and punishment of offences committed within India", "Every person shall be liable to punishment under this Code and not otherwise for every act or omission contrary to the provisions thereof.", "repealed", "1862-01-01", "2024-07-01", "{}", "vaquill-central", "https://oss-data-in.vaquill.ai/v2026.08.1/", 1),
            ("ipc-1860:124A", "ipc-1860", "IPC", "section", "124A", "124A", 124, "VI", "Of Offences against the State", None, "Sedition", "Whoever by words brings or attempts to bring into hatred or contempt towards the Government established by law in India.", "kept_in_abeyance", "1870-01-01", "2024-07-01", "{}", "vaquill-central", "https://oss-data-in.vaquill.ai/v2026.08.1/", 1),
            ("ipc-1860:302", "ipc-1860", "IPC", "section", "302", "302", 302, "XVI", "Of Offences Affecting the Human Body", None, "Punishment for murder", "Whoever commits murder shall be punished with death, or imprisonment for life, and shall also be liable to fine.", "repealed", "1862-01-01", "2024-07-01", "{}", "vaquill-central", "https://oss-data-in.vaquill.ai/v2026.08.1/", 1),
            ("ipc-1860:303", "ipc-1860", "IPC", "section", "303", "303", 303, "XVI", "Of Offences Affecting the Human Body", None, "Punishment for murder by life-convict", "Whoever, being under sentence of imprisonment for life, commits murder, shall be punished with death.", "struck_down", "1862-01-01", "2024-07-01", "{}", "vaquill-central", "https://oss-data-in.vaquill.ai/v2026.08.1/", 1),
            ("ipc-1860:375", "ipc-1860", "IPC", "section", "375", "375", 375, "XVI", "Of Offences Affecting the Human Body", None, "Rape", "A man is said to commit rape who penetrates...", "repealed", "1862-01-01", "2024-07-01", "{}", "vaquill-central", "https://oss-data-in.vaquill.ai/v2026.08.1/", 1),
            ("ipc-1860:377", "ipc-1860", "IPC", "section", "377", "377", 377, "XVI", "Of Offences Affecting the Human Body", None, "Unnatural offences", "Whoever voluntarily has carnal intercourse against the order of nature...", "read_down", "1862-01-01", "2024-07-01", "{}", "vaquill-central", "https://oss-data-in.vaquill.ai/v2026.08.1/", 1),
            ("ipc-1860:420", "ipc-1860", "IPC", "section", "420", "420", 420, "XVII", "Of Offences Against Property", None, "Cheating and dishonestly inducing delivery of property", "Whoever cheats and thereby dishonestly induces the person deceived to deliver any property...", "repealed", "1862-01-01", "2024-07-01", "{}", "vaquill-central", "https://oss-data-in.vaquill.ai/v2026.08.1/", 1),
            ("ipc-1860:497", "ipc-1860", "IPC", "section", "497", "497", 497, "XX", "Of Offences Relating to Marriage", None, "Adultery", "Whoever has sexual intercourse with a person who is and whom he knows or has reason to believe to be the wife of another man...", "struck_down", "1862-01-01", "2024-07-01", "{}", "vaquill-central", "https://oss-data-in.vaquill.ai/v2026.08.1/", 1),
            ("bns-2023:2", "bns-2023", "BNS", "section", "2", "2", 2, "I", "Preliminary", None, "Definitions", "In this Sanhita, unless the context otherwise requires, words and expressions are defined herein.", "in_force", "2024-07-01", None, "{}", "prs-sanhitas", "https://prsindia.org/", 1),
            ("bns-2023:63", "bns-2023", "BNS", "section", "63", "63", 63, "V", "Of Offences against Woman and Child", None, "Rape", "A man is said to commit rape if he penetrates...", "in_force", "2024-07-01", None, "{}", "prs-sanhitas", "https://prsindia.org/", 1),
            ("bns-2023:103", "bns-2023", "BNS", "section", "103", "103", 103, "VI", "Of Offences Affecting the Human Body", None, "Punishment for murder", "(1) Whoever commits murder shall be punished with death or imprisonment for life, and shall also be liable to fine. (2) Mob lynching provision.", "in_force", "2024-07-01", None, "{}", "prs-sanhitas", "https://prsindia.org/", 1),
            ("bns-2023:318(4)", "bns-2023", "BNS", "section", "318(4)", "318(4)", 318, "XVII", "Of Offences Against Property", None, "Cheating and dishonestly inducing delivery of property", "Whoever cheats and thereby dishonestly induces the person deceived to deliver any property shall be punished with imprisonment up to seven years.", "in_force", "2024-07-01", None, "{}", "prs-sanhitas", "https://prsindia.org/", 1),
            ("crpc-1973:438", "crpc-1973", "CrPC", "section", "438", "438", 438, "XXXIII", "Provisions as to Bail and Bonds", None, "Direction for grant of bail to person apprehending arrest", "Anticipatory bail before High Court or Court of Session.", "repealed", "1974-04-01", "2024-07-01", "{}", "vaquill-central", "https://oss-data-in.vaquill.ai/v2026.08.1/", 1),
            ("bnss-2023:482", "bnss-2023", "BNSS", "section", "482", "482", 482, "XXXV", "Provisions as to Bail and Bonds", None, "Direction for grant of bail to person apprehending arrest", "When any person has reason to believe that he may be arrested on accusation of a non-bailable offence, he may apply to the High Court or Court of Session.", "in_force", "2024-07-01", None, "{}", "prs-sanhitas", "https://prsindia.org/", 1),
            ("bnss-2023:531", "bnss-2023", "BNSS", "section", "531", "531", 531, "XXXIX", "Repeal and Savings", None, "Repeal and savings", "Notwithstanding repeal of the Code of Criminal Procedure, 1973, pending appeals, applications, trials, inquiries or investigations prior to 1 July 2024 shall continue under the old Code.", "in_force", "2024-07-01", None, "{}", "prs-sanhitas", "https://prsindia.org/", 1),
            ("iea-1872:65B", "iea-1872", "EvidenceAct", "section", "65B", "65B", 65, "V", "Of Documentary Evidence", None, "Admissibility of electronic records", "(1) Any information contained in an electronic record... (4) Certificate signed by a person occupying a responsible official position.", "repealed", "2000-10-17", "2024-07-01", "{}", "vaquill-central", "https://oss-data-in.vaquill.ai/v2026.08.1/", 1),
            ("bsa-2023:63", "bsa-2023", "BSA", "section", "63", "63", 63, "V", "Of Documentary Evidence", None, "Admissibility of electronic records", "Electronic or digital record admissibility with certificate in Schedule format under BSA.", "in_force", "2024-07-01", None, "{}", "prs-sanhitas", "https://prsindia.org/", 1),
            ("cpc-1908:O7-R11", "cpc-1908", "CPC", "order_rule", "Order 7 Rule 11", "O7-R11", 7, "First Schedule", "Pleadings and Plaint", None, "Rejection of plaint", "The plaint shall be rejected in the following cases: (a) where it does not disclose a cause of action; (d) where the suit appears from the statement in the plaint to be barred by any law.", "in_force", "1909-01-01", None, "{}", "vaquill-central", "https://oss-data-in.vaquill.ai/v2026.08.1/", 1),
            ("it-act-2000:66A", "it-act-2000", "ITAct", "section", "66A", "66A", 66, "XI", "Offences", None, "Punishment for sending offensive messages through communication service, etc.", "Any person who sends, by means of a computer resource or a communication device, any information that is grossly offensive...", "struck_down", "2009-10-27", None, "{}", "vaquill-central", "https://oss-data-in.vaquill.ai/v2026.08.1/", 1),
        ]
        for row in prov_rows:
            doc_id, act_id, act_short, kind, ref, ref_norm, ref_num, ch_num, ch_title, part, title, text, status, comm, rep, meta_j, src_id, src_url, comm_safe = row
            con.execute(
                "INSERT INTO provisions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (doc_id, act_id, act_short, kind, ref, ref_norm, ref_num, ch_num, ch_title, part, title, text, status, comm, rep, meta_j, src_id, src_url, _sha256_text(text), comm_safe),
            )

        amendments = [
            ("const-amend-1", "constitution", "19", "Constitution (First Amendment) Act, 1951", 1, "1951-06-18", "1951-06-18", "amended", "Added reasonable restrictions to Art. 19(2) and inserted Ninth Schedule.", "15,19,31A,31B", "legislative-constitution"),
            ("const-amend-42", "constitution", None, "Constitution (Forty-second Amendment) Act, 1976", 42, "1976-12-18", "1977-01-03", "amended", "Inserted Socialist and Secular in Preamble, Fundamental Duties Part IVA, and amended Art. 368.", "Preamble,31C,39A,48A,51A,368", "legislative-constitution"),
            ("const-amend-86", "constitution", "21A", "Constitution (Eighty-sixth Amendment) Act, 2002", 86, "2002-12-12", "2010-04-01", "inserted", "Inserted Article 21A making free and compulsory education a Fundamental Right.", "21A,45,51A", "legislative-constitution"),
            ("const-amend-106", "constitution", None, "Constitution (One Hundred and Sixth Amendment) Act, 2023", 106, "2023-09-28", "2023-09-28", "inserted", "Nari Shakti Vandan Adhiniyam reserving one-third seats for women in Lok Sabha and State Assemblies.", "239AA,330A,332A,334A", "legislative-constitution"),
        ]
        con.executemany("INSERT INTO provision_amendments VALUES (?,?,?,?,?,?,?,?,?,?,?)", amendments)

        concordance_rows = [
            (1, "ipc-1860:302", "bns-2023:103", "one_to_one", "Core murder punishment retained in s.103(1); s.103(2) adds mob lynching offence.", "Death or life imprisonment + fine preserved; s.103(2) adds specific mob lynching minimum/death.", "Cognizable, non-bailable, triable by Court of Session.", "BNSS s.531: pre-2024-07-01 offences and pending trials continue under IPC.", "bprd_official_table_v1", "prs-sanhitas"),
            (2, "ipc-1860:420", "bns-2023:318(4)", "one_to_one", "Cheating and dishonestly inducing delivery of property moved from IPC s.420 to BNS s.318(4).", "Imprisonment up to 7 years and fine unchanged.", "Cognizable, non-bailable.", "BNSS s.531 savings applies to pre-2024-07-01 FIRs.", "bprd_official_table_v1", "prs-sanhitas"),
            (3, "ipc-1860:375", "bns-2023:63", "one_to_one", "Definition of rape relocated to BNS s.63 with updated exception age (18 years).", "Substantive ingredients preserved; marital exception threshold raised to 18 years.", "Cognizable, non-bailable.", "BNSS s.531 savings applies.", "bprd_official_table_v1", "prs-sanhitas"),
            (4, "crpc-1973:438", "bnss-2023:482", "one_to_one", "Anticipatory bail consolidated in BNSS s.482 omitting state-specific proviso clutter.", "N/A", "Retains Court of Session / High Court anticipatory bail jurisdiction.", "BNSS s.531 savings applies.", "bprd_official_table_v1", "prs-sanhitas"),
            (5, "iea-1872:65B", "bsa-2023:63", "one_to_one", "Electronic record admissibility moved from IEA s.65B to BSA s.63 with explicit expert-signed Schedule certificate.", "N/A", "Requires statutory certificate plus expert certification where applicable.", "BNSS s.531 savings applies.", "bprd_official_table_v1", "prs-sanhitas"),
        ]
        con.executemany("INSERT INTO concordance VALUES (?,?,?,?,?,?,?,?,?,?)", concordance_rows)

        invalidations = [
            (1, "it-act-2000:66A", "struck_down", "sc:2015:shreya-singhal", "Shreya Singhal v. Union of India", "(2015) 5 SCC 1", "2015-03-24", 2, "Section 66A of the IT Act, 2000 struck down in its entirety as unconstitutional under Article 19(1)(a).", "dattam-sc"),
            (2, "ipc-1860:497", "struck_down", "sc:2018:joseph-shine", "Joseph Shine v. Union of India", "(2019) 3 SCC 39", "2018-09-27", 5, "Section 497 IPC decriminalized and struck down as violative of Articles 14, 15, and 21.", "dattam-sc"),
            (3, "ipc-1860:303", "struck_down", "sc:1983:mithu", "Mithu v. State of Punjab", "AIR 1983 SC 473", "1983-04-07", 5, "Mandatory death sentence under Section 303 IPC struck down as arbitrary under Articles 14 and 21.", "dattam-sc"),
            (4, "ipc-1860:377", "read_down", "sc:2018:navtej-johar", "Navtej Singh Johar v. Union of India", "(2018) 10 SCC 1", "2018-09-06", 5, "Section 377 IPC read down so consensual sexual conduct between adults in private is not criminalized.", "dattam-sc"),
            (5, "ipc-1860:124A", "kept_in_abeyance", "sc:2022:vombatkere", "S.G. Vombatkere v. Union of India", "2022 SCC OnLine SC 609", "2022-05-11", 3, "All pending trials, appeals, and fresh FIRs under Section 124A IPC kept in abeyance pending legislative review.", "dattam-sc"),
        ]
        con.executemany("INSERT INTO judicial_invalidations VALUES (?,?,?,?,?,?,?,?,?,?)", invalidations)

        judgments = [
            ("sc:1973:kesavananda", "sc", "SCI", "Supreme Court of India", "Full Court", None, None, "AIR 1973 SC 1461", "(1973) 4 SCC 225", "[1973] Supp SCR 1", "Kesavananda Bharati v. State of Kerala", "Kesavananda Bharati", "State of Kerala", "1973-04-24", 1973, "S.M. Sikri; H.R. Khanna; A.N. Ray", "S.M. Sikri", 13, "Disposed of", "Writ Petition (Civil)", '["Constitution"]', 1, "indian-supreme-court-judgments", "data/pdf/year=1973/english/kesavananda_bharati.pdf", "https://indian-supreme-court-judgments.s3.ap-south-1.amazonaws.com/data/pdf/year=1973/english/kesavananda_bharati.pdf", _sha256_text("Kesavananda Bharati basic structure doctrine"), 1, 1450, "dattam-sc", 1),
            ("sc:2017:puttaswamy", "sc", "SCI", "Supreme Court of India", "Constitution Bench", None, "2017 INSC 801", None, "(2017) 10 SCC 1", None, "Justice K.S. Puttaswamy (Retd.) v. Union of India", "Justice K.S. Puttaswamy", "Union of India", "2017-08-24", 2017, "J.S. Khehar; D.Y. Chandrachud; S.K. Kaul", "D.Y. Chandrachud", 9, "Allowed", "Writ Petition (Civil)", '["Constitution", "AadhaarAct"]', 1, "indian-supreme-court-judgments", "data/pdf/year=2017/english/puttaswamy_2017.pdf", "https://indian-supreme-court-judgments.s3.ap-south-1.amazonaws.com/data/pdf/year=2017/english/puttaswamy_2017.pdf", _sha256_text("Right to privacy Article 21"), 1, 820, "dattam-sc", 1),
            ("sc:2015:shreya-singhal", "sc", "SCI", "Supreme Court of India", "Division Bench", None, "2015 INSC 254", None, "(2015) 5 SCC 1", None, "Shreya Singhal v. Union of India", "Shreya Singhal", "Union of India", "2015-03-24", 2015, "J. Chelameswar; R.F. Nariman", "R.F. Nariman", 2, "Allowed", "Writ Petition (Criminal)", '["ITAct", "Constitution"]', 1, "indian-supreme-court-judgments", "data/pdf/year=2015/english/shreya_singhal.pdf", "https://indian-supreme-court-judgments.s3.ap-south-1.amazonaws.com/data/pdf/year=2015/english/shreya_singhal.pdf", _sha256_text("Section 66A IT Act struck down"), 1, 610, "dattam-sc", 1),
            ("hc:2024:dhc:1234", "hc", "7_26", "High Court of Delhi", "Principal", "DLHC010123452024", "2024:DHC:1234", None, None, None, "Arjun Mehta v. State NCT of Delhi", "Arjun Mehta", "State NCT of Delhi", "2024-05-14", 2024, "Vibhu Bakhru; Amit Mahajan", "Vibhu Bakhru", 2, "Allowed", "CRL.M.C.", '["CrPC", "BNSS", "IPC"]', 1, "indian-high-court-judgments", "data/pdf/year=2024/court=7_26/bench=principal/dhc_1234.pdf", "https://indian-high-court-judgments.s3.ap-south-1.amazonaws.com/data/pdf/year=2024/court=7_26/bench=principal/dhc_1234.pdf", _sha256_text("Delhi High Court bail Section 438 CrPC"), 1, 12, "dattam-hc", 1),
            ("hc:2024:bhc:567", "hc", "27_1", "High Court of Bombay", "Original Side", "MHAU019999992015", "2024:BHC-OS:567", None, None, None, "Rohan Infra Pvt Ltd v. Maharashtra Housing Board", "Rohan Infra Pvt Ltd", "Maharashtra Housing Board", "2024-03-10", 2024, "B.P. Colabawalla", "B.P. Colabawalla", 1, "Disposed of", "W.P.(L)", '["CPC", "MH-MOFA"]', 1, "indian-high-court-judgments", "data/pdf/year=2024/court=27_1/bench=original/bhc_567.pdf", "https://indian-high-court-judgments.s3.ap-south-1.amazonaws.com/data/pdf/year=2024/court=27_1/bench=original/bhc_567.pdf", _sha256_text("Bombay HC Order 7 Rule 11 CPC"), 1, 4, "dattam-hc", 1),
            ("ildc:2020:001", "sc", "SCI", "Supreme Court of India", "Division Bench", None, "2020 INSC 99", None, None, None, "State of Maharashtra v. Sample ILDC Appellant", "State of Maharashtra", "Sample Appellant", "2020-02-01", 2020, "N.V. Ramana; Sanjiv Khanna", "Sanjiv Khanna", 2, "Dismissed", "Criminal Appeal", '["IPC"]', 0, None, None, "https://huggingface.co/datasets/ildc", _sha256_text("ILDC research sample"), 1, 1, "ildc-research", 0),
        ]
        con.executemany(
            "INSERT INTO judgments_meta VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            judgments,
        )

        edges = [
            (1, "sc:2017:puttaswamy", "judgment", "sc:1973:kesavananda", "judgment", "cites", "para 142", "Basic structure doctrine affirmed in Kesavananda Bharati protects fundamental rights.", 2017, 9, "verified_curated"),
            (2, "sc:2015:shreya-singhal", "judgment", "it-act-2000:66A", "provision", "invalidates", "para 119", "Section 66A of the Information Technology Act is struck down in its entirety.", 2015, 2, "verified_curated"),
            (3, "sc:2015:shreya-singhal", "judgment", "constitution:art:19", "provision", "interprets", "para 15", "Discussion, advocacy and incitement under Article 19(1)(a) and Article 19(2).", 2015, 2, "verified_curated"),
            (4, "ipc-1860:302", "provision", "bns-2023:103", "provision", "replaced_by", None, "Replaced effective 2024-07-01 by BNS Section 103.", None, None, "verified_curated"),
            (5, "hc:2024:dhc:1234", "judgment", "sc:2017:puttaswamy", "judgment", "cites", "para 18", "Personal liberty safeguards under Article 21 discussed.", 2024, 2, "unverified_automated"),
        ]
        con.executemany("INSERT INTO citation_edges VALUES (?,?,?,?,?,?,?,?,?,?,?)", edges)

        courts = [
            ("SCI", "Supreme Court of India", "sc", "SCI", "Principal", "Delhi", "New Delhi", "All India"),
            ("7_26", "High Court of Delhi", "hc", "7_26", "Principal Bench", "Delhi", "New Delhi", "NCT of Delhi"),
            ("27_1", "High Court of Bombay", "hc", "27_1", "Original & Appellate", "Maharashtra", "Mumbai", "Maharashtra,Goa,Dadra and Nagar Haveli"),
            ("9_13", "High Court of Allahabad", "hc", "9_13", "Principal Bench", "Uttar Pradesh", "Prayagraj", "Uttar Pradesh"),
            ("NCLAT", "National Company Law Appellate Tribunal", "tribunal", "NCLAT", "Principal", "Delhi", "New Delhi", "All India"),
            ("ITAT", "Income Tax Appellate Tribunal", "tribunal", "ITAT", "All Benches", "Maharashtra", "Mumbai", "All India"),
            ("SEBI", "Securities and Exchange Board of India", "regulator", "SEBI", "Head Office", "Maharashtra", "Mumbai", "All India"),
        ]
        con.executemany("INSERT INTO courts VALUES (?,?,?,?,?,?,?,?)", courts)

        con.execute("INSERT INTO provisions_fts(provisions_fts) VALUES('rebuild')")
        con.execute("INSERT INTO judgments_meta_fts(judgments_meta_fts) VALUES('rebuild')")
        con.commit()
        con.execute("PRAGMA journal_mode=DELETE")
        con.execute("VACUUM")
        con.execute("ANALYZE")
    finally:
        con.close()


def _build_lancedb(lance_dir: Path) -> None:
    lance_dir.mkdir(parents=True, exist_ok=True)
    db = lancedb.connect(str(lance_dir))

    sc_rows = pa.table(
        {
            "chunk_id": [
                "sc:1973:kesavananda:0",
                "sc:2017:puttaswamy:0",
                "sc:2015:shreya-singhal:0",
                "ildc:2020:001:0",
            ],
            "case_id": [
                "sc:1973:kesavananda",
                "sc:2017:puttaswamy",
                "sc:2015:shreya-singhal",
                "ildc:2020:001",
            ],
            "year": pa.array([1973, 2017, 2015, 2020], type=pa.int32()),
            "decision_date": ["1973-04-24", "2017-08-24", "2015-03-24", "2020-02-01"],
            "title": [
                "Kesavananda Bharati v. State of Kerala",
                "Justice K.S. Puttaswamy (Retd.) v. Union of India",
                "Shreya Singhal v. Union of India",
                "State of Maharashtra v. Sample ILDC Appellant",
            ],
            "neutral_citation": ["", "2017 INSC 801", "2015 INSC 254", "2020 INSC 99"],
            "air_citation": ["AIR 1973 SC 1461", "", "", ""],
            "scc_citation": ["(1973) 4 SCC 225", "(2017) 10 SCC 1", "(2015) 5 SCC 1", ""],
            "author_judge": ["S.M. Sikri", "D.Y. Chandrachud", "R.F. Nariman", "Sanjiv Khanna"],
            "bench_strength": pa.array([13, 9, 2, 2], type=pa.int32()),
            "opinion_type": ["majority", "majority", "majority", "majority"],
            "para_start": pa.array([1, 1, 1, 1], type=pa.int32()),
            "para_end": pa.array([5, 5, 5, 3], type=pa.int32()),
            "page_start": pa.array([1, 1, 1, 1], type=pa.int32()),
            "page_end": pa.array([2, 2, 2, 1], type=pa.int32()),
            "acts_cited": [["Constitution"], ["Constitution"], ["ITAct", "Constitution"], ["IPC"]],
            "text": [
                "Parliament cannot alter the basic structure or framework of the Constitution under Article 368.",
                "The right to privacy is protected as an intrinsic part of the right to life and personal liberty under Article 21.",
                "Section 66A of the Information Technology Act 2000 is vague and overbroad, violating Article 19(1)(a), and is struck down.",
                "ILDC non-commercial academic explanation sample for criminal appeal sentencing under IPC section 302.",
            ],
            "commercial_safe": [True, True, True, False],
            "terms_status": ["cleared", "cleared", "cleared", "non_commercial_only"],
            "license_spdx": ["CC-BY-4.0", "CC-BY-4.0", "CC-BY-4.0", "CC-BY-NC-4.0"],
            "source_id": ["dattam-sc", "dattam-sc", "dattam-sc", "ildc-research"],
            "source_url": [
                "https://indian-supreme-court-judgments.s3.ap-south-1.amazonaws.com",
                "https://indian-supreme-court-judgments.s3.ap-south-1.amazonaws.com",
                "https://indian-supreme-court-judgments.s3.ap-south-1.amazonaws.com",
                "https://huggingface.co/datasets/ildc",
            ],
            "text_sha256": [_sha256_text("k"), _sha256_text("p"), _sha256_text("s"), _sha256_text("i")],
        }
    )
    t_sc = db.create_table("sc_chunks", data=sc_rows, mode="overwrite")
    t_sc.create_fts_index("text", replace=True, with_position=True)

    hc_rows = pa.table(
        {
            "chunk_id": ["hc:2024:dhc:1234:0", "hc:2024:bhc:567:0"],
            "case_id": ["hc:2024:dhc:1234", "hc:2024:bhc:567"],
            "year": pa.array([2024, 2024], type=pa.int32()),
            "decision_date": ["2024-05-14", "2024-03-10"],
            "title": [
                "Arjun Mehta v. State NCT of Delhi",
                "Rohan Infra Pvt Ltd v. Maharashtra Housing Board",
            ],
            "neutral_citation": ["2024:DHC:1234", "2024:BHC-OS:567"],
            "air_citation": ["", ""],
            "scc_citation": ["", ""],
            "author_judge": ["Vibhu Bakhru", "B.P. Colabawalla"],
            "bench_strength": pa.array([2, 1], type=pa.int32()),
            "opinion_type": ["majority", "majority"],
            "para_start": pa.array([1, 1], type=pa.int32()),
            "para_end": pa.array([4, 4], type=pa.int32()),
            "page_start": pa.array([1, 1], type=pa.int32()),
            "page_end": pa.array([2, 2], type=pa.int32()),
            "acts_cited": [["CrPC", "BNSS"], ["CPC", "MH-MOFA"]],
            "text": [
                "Anticipatory bail application under Section 438 CrPC and BNSS Section 482 granted considering personal liberty.",
                "Plaint rejection application under Order VII Rule 11(d) CPC in Maharashtra Ownership Flats Act dispute.",
            ],
            "commercial_safe": [True, True],
            "terms_status": ["cleared", "cleared"],
            "license_spdx": ["CC-BY-4.0", "CC-BY-4.0"],
            "source_id": ["dattam-hc", "dattam-hc"],
            "source_url": [
                "https://indian-high-court-judgments.s3.ap-south-1.amazonaws.com",
                "https://indian-high-court-judgments.s3.ap-south-1.amazonaws.com",
            ],
            "text_sha256": [_sha256_text("dhc"), _sha256_text("bhc")],
            "court_code": ["7_26", "27_1"],
            "bench": ["Principal", "Original Side"],
            "cnr": ["DLHC010123452024", "MHAU019999992015"],
            "disposal_nature": ["Allowed", "Disposed of"],
            "is_reportable": [True, True],
        }
    )
    t_hc = db.create_table("hc_hot_chunks", data=hc_rows, mode="overwrite")
    t_hc.create_fts_index("text", replace=True, with_position=True)

    state_reg_rows = pa.table(
        {
            "doc_id": ["mh-mofa-1963:4", "sebi-lodr-2015:30"],
            "act_id": ["mh-mofa-1963", "sebi-lodr-2015"],
            "short_name": ["MH-MOFA", "SEBI-LODR"],
            "jurisdiction": ["MH", "SEBI"],
            "category": ["state_act", "regulator"],
            "kind": ["section", "regulation"],
            "ref": ["4", "30"],
            "title": [
                "Promoter before accepting advance payment to enter into agreement for sale",
                "Disclosure of events or information by listed entity",
            ],
            "status": ["in_force", "in_force"],
            "effective_date": ["1964-02-10", "2015-12-01"],
            "text": [
                "Notwithstanding anything contained in any other law, a promoter who intends to construct flats shall execute a registered agreement for sale before accepting advance payment.",
                "Every listed entity shall make disclosures of any events or information which, in the opinion of the board of directors of the listed company, is material under Regulation 30.",
            ],
            "commercial_safe": [True, True],
            "terms_status": ["cleared", "cleared"],
            "license_spdx": ["CC-BY-4.0", "CC-BY-4.0"],
            "source_id": ["vaquill-central", "vaquill-central"],
            "source_url": [
                "https://oss-data-in.vaquill.ai/v2026.08.1/",
                "https://oss-data-in.vaquill.ai/v2026.08.1/",
            ],
            "text_sha256": [_sha256_text("mofa"), _sha256_text("lodr")],
        }
    )
    t_sr = db.create_table("state_acts_regulators", data=state_reg_rows, mode="overwrite")
    t_sr.create_fts_index("text", replace=True, with_position=True)


def _write_zstd_parquet(table: pa.Table, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(":memory:")
    try:
        con.register("tbl", table)
        escaped = str(out_path).replace("'", "''")
        con.execute(f"COPY tbl TO '{escaped}' (FORMAT PARQUET, COMPRESSION ZSTD)")
    finally:
        con.close()


def _build_parquet_and_manifest(out_dir: Path) -> Path:
    pq_root = out_dir / "parquet"

    hc_dhc = pa.table(
        {
            "case_id": ["hc:2024:dhc:1234", "hc:2023:dhc:888"],
            "cnr": ["DLHC010123452024", "DLHC010008882023"],
            "court_code": ["7_26", "7_26"],
            "court_name": ["High Court of Delhi", "High Court of Delhi"],
            "bench": ["Principal", "Principal"],
            "neutral_citation": ["2024:DHC:1234", "2023:DHC:888"],
            "title": ["Arjun Mehta v. State NCT of Delhi", "Priya Sharma v. Union of India"],
            "petitioner": ["Arjun Mehta", "Priya Sharma"],
            "respondent": ["State NCT of Delhi", "Union of India"],
            "judge": ["Vibhu Bakhru", "Prathiba M. Singh"],
            "author_judge": ["Vibhu Bakhru", "Prathiba M. Singh"],
            "bench_strength": pa.array([2, 1], type=pa.int32()),
            "decision_date": ["2024-05-14", "2023-11-20"],
            "year": pa.array([2024, 2023], type=pa.int32()),
            "disposal_nature": ["Allowed", "Dismissed"],
            "case_type": ["CRL.M.C.", "W.P.(C)"],
            "act": ["CrPC", "ITAct"],
            "commercial_safe": [True, True],
        }
    )
    hc_bhc = pa.table(
        {
            "case_id": ["hc:2024:bhc:567"],
            "cnr": ["MHAU019999992015"],
            "court_code": ["27_1"],
            "court_name": ["High Court of Bombay"],
            "bench": ["Original Side"],
            "neutral_citation": ["2024:BHC-OS:567"],
            "title": ["Rohan Infra Pvt Ltd v. Maharashtra Housing Board"],
            "petitioner": ["Rohan Infra Pvt Ltd"],
            "respondent": ["Maharashtra Housing Board"],
            "judge": ["B.P. Colabawalla"],
            "author_judge": ["B.P. Colabawalla"],
            "bench_strength": pa.array([1], type=pa.int32()),
            "decision_date": ["2024-03-10"],
            "year": pa.array([2024], type=pa.int32()),
            "disposal_nature": ["Disposed of"],
            "case_type": ["W.P.(L)"],
            "act": ["CPC"],
            "commercial_safe": [True],
        }
    )
    ddl_mh = pa.table(
        {
            "case_id": ["ddl:mh:2015:1", "ddl:mh:2016:2"],
            "cnr": ["MHAU020000012015", "MHAU020000022016"],
            "state_code": ["MH", "MH"],
            "court_code": ["MH_DIST_01", "MH_DIST_01"],
            "court_name": ["District and Sessions Court, Pune", "District and Sessions Court, Pune"],
            "district": ["Pune", "Pune"],
            "title": ["State of Maharashtra v. Accused A", "Ramesh Patil v. Suresh Kulkarni"],
            "petitioner": ["State of Maharashtra", "Ramesh Patil"],
            "respondent": ["Accused A", "Suresh Kulkarni"],
            "judge": ["District Judge 1", "Civil Judge Sr. Div."],
            "decision_date": ["2015-08-12", "2016-04-05"],
            "year": pa.array([2015, 2016], type=pa.int32()),
            "disposal_nature": ["Acquitted", "Decreed"],
            "case_type": ["Sessions Case", "Regular Civil Suit"],
            "act": ["IPC", "CPC"],
            "commercial_safe": [False, False],
        }
    )
    trib_nclat = pa.table(
        {
            "case_id": ["trib:nclat:2024:10"],
            "forum_code": ["NCLAT"],
            "court_code": ["NCLAT"],
            "court_name": ["National Company Law Appellate Tribunal"],
            "title": ["Apex Creditors v. Resolution Professional"],
            "judge": ["Justice Ashok Bhushan"],
            "decision_date": ["2024-02-18"],
            "year": pa.array([2024], type=pa.int32()),
            "disposal_nature": ["Allowed"],
            "case_type": ["Company Appeal (AT) (Insolvency)"],
            "act": ["IBC"],
            "commercial_safe": [True],
        }
    )
    sc_meta = pa.table(
        {
            "case_id": ["sc:1973:kesavananda", "sc:2017:puttaswamy", "sc:2015:shreya-singhal"],
            "court_code": ["SCI", "SCI", "SCI"],
            "court_name": ["Supreme Court of India", "Supreme Court of India", "Supreme Court of India"],
            "neutral_citation": ["", "2017 INSC 801", "2015 INSC 254"],
            "air_citation": ["AIR 1973 SC 1461", "", ""],
            "scc_citation": ["(1973) 4 SCC 225", "(2017) 10 SCC 1", "(2015) 5 SCC 1"],
            "title": [
                "Kesavananda Bharati v. State of Kerala",
                "Justice K.S. Puttaswamy (Retd.) v. Union of India",
                "Shreya Singhal v. Union of India",
            ],
            "judge": ["S.M. Sikri", "D.Y. Chandrachud", "R.F. Nariman"],
            "bench_strength": pa.array([13, 9, 2], type=pa.int32()),
            "decision_date": ["1973-04-24", "2017-08-24", "2015-03-24"],
            "year": pa.array([1973, 2017, 2015], type=pa.int32()),
            "disposal_nature": ["Disposed of", "Allowed", "Allowed"],
            "case_type": ["Writ Petition (Civil)", "Writ Petition (Civil)", "Writ Petition (Criminal)"],
            "act": ["Constitution", "Constitution", "ITAct"],
            "commercial_safe": [True, True, True],
        }
    )
    reg_sebi = pa.table(
        {
            "case_id": ["reg:sebi:2024:1"],
            "regulator_code": ["SEBI"],
            "court_code": ["SEBI"],
            "court_name": ["Securities and Exchange Board of India"],
            "title": ["Adjudication Order in the matter of XYZ Listed Ltd"],
            "decision_date": ["2024-06-01"],
            "year": pa.array([2024], type=pa.int32()),
            "disposal_nature": ["Penalty Imposed"],
            "case_type": ["Adjudication Order"],
            "act": ["SEBI-LODR"],
            "commercial_safe": [True],
        }
    )

    files: list[tuple[str, str, pa.Table, dict[str, Any]]] = [
        ("high_court", "parquet/high_court/court=7_26/decade=2020s.parquet", hc_dhc, {"court_code": "7_26", "court_name": "High Court of Delhi", "year_min": 2020, "year_max": 2029}),
        ("high_court", "parquet/high_court/court=27_1/decade=2020s.parquet", hc_bhc, {"court_code": "27_1", "court_name": "High Court of Bombay", "year_min": 2020, "year_max": 2029}),
        ("district_court", "parquet/district_court/state=MH.parquet", ddl_mh, {"state_code": "MH", "year_min": 2010, "year_max": 2018}),
        ("tribunal", "parquet/tribunal/forum=NCLAT.parquet", trib_nclat, {"forum_code": "NCLAT", "year_min": 2020, "year_max": 2029}),
        ("supreme_court", "parquet/supreme_court/sc_metadata.parquet", sc_meta, {"court_code": "SCI", "year_min": 1950, "year_max": 2026}),
        ("regulator", "parquet/regulator/body=SEBI.parquet", reg_sebi, {"regulator_code": "SEBI", "year_min": 2015, "year_max": 2026}),
    ]

    datasets: dict[str, dict[str, Any]] = {
        "high_court": {"license_spdx": "CC-BY-4.0", "commercial_safe": True, "terms_status": "cleared", "row_count": 0, "partitions": []},
        "district_court": {"license_spdx": "CC-BY-NC-SA-4.0", "commercial_safe": False, "terms_status": "non_commercial_only", "row_count": 0, "partitions": []},
        "tribunal": {"license_spdx": "CC-BY-4.0", "commercial_safe": True, "terms_status": "cleared", "row_count": 0, "partitions": []},
        "supreme_court": {"license_spdx": "CC-BY-4.0", "commercial_safe": True, "terms_status": "cleared", "row_count": 0, "partitions": []},
        "regulator": {"license_spdx": "CC-BY-4.0", "commercial_safe": True, "terms_status": "cleared", "row_count": 0, "partitions": []},
    }

    for ds_name, rel_key, tbl, meta in files:
        fpath = out_dir / rel_key
        _write_zstd_parquet(tbl, fpath)
        part = {
            **meta,
            "rows": tbl.num_rows,
            "s3_key": rel_key,
            "public_url": f"https://example.invalid/nyaya/{rel_key}",
            "bytes": fpath.stat().st_size,
            "sha256": _sha256_file(fpath),
        }
        datasets[ds_name]["partitions"].append(part)
        datasets[ds_name]["row_count"] += tbl.num_rows

    manifest = {
        "schema_version": "2.0",
        "generated_at": "2026-11-22T00:00:00Z",
        "datasets": datasets,
    }
    manifest_json = json.dumps(manifest, indent=2)
    m_root = out_dir / "manifest.json"
    m_root.write_text(manifest_json, encoding="utf-8")
    (pq_root / "manifest.json").write_text(manifest_json, encoding="utf-8")
    return m_root


def _build_pdfs(pdf_dir: Path) -> None:
    pdf_dir.mkdir(parents=True, exist_ok=True)
    valid_pdf = make_minimal_pdf(
        [
            "1. In Kesavananda Bharati v. State of Kerala, this Court considered Article 368.\n"
            "2. Parliament's amending power does not extend to destroying the basic structure of the Constitution.\n"
            "3. Fundamental rights under Part III and Article 21 remain core pillars of constitutional supremacy.",
            "4. Under Section 65B of the Evidence Act, electronic records require statutory certification.\n"
            "5. Petition disposed of accordingly with directions on constitutional validity.",
        ]
    )
    (pdf_dir / "valid_judgment.pdf").write_bytes(valid_pdf)
    (pdf_dir / "scanned_judgment.pdf").write_bytes(make_minimal_pdf([""]))


def build_mini_corpus(out_dir: str | Path) -> dict[str, Path]:
    """Build the complete synthetic v2 corpus (SQLite, LanceDB, Parquet, manifest, PDFs)."""
    root = Path(out_dir)
    root.mkdir(parents=True, exist_ok=True)

    sqlite_path = root / "nyaya-core.sqlite"
    lance_dir = root / "lancedb"
    parquet_dir = root / "parquet"
    pdfs_dir = root / "pdfs"

    _build_sqlite(sqlite_path)
    _build_lancedb(lance_dir)
    manifest_path = _build_parquet_and_manifest(root)
    _build_pdfs(pdfs_dir)

    return {
        "root": root,
        "sqlite": sqlite_path,
        "lancedb": lance_dir,
        "parquet": parquet_dir,
        "manifest": manifest_path,
        "pdfs": pdfs_dir,
    }


def main(argv: list[str] | None = None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("Usage: python -m tests.fixtures.mini_corpus.builder <out_dir>", file=sys.stderr)
        return 2
    out = Path(args[0])
    paths = build_mini_corpus(out)
    print(f"Built mini_corpus at {paths['root']} (sqlite={paths['sqlite']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
