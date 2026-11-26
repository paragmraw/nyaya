"""Tests for Constitution PDF parser hardening, gate hygiene, and dynamic `_default_as_of()`."""

from __future__ import annotations

import hashlib
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from nyaya.ingest.builders.sqlite_core import (
    _default_as_of,
    build_sqlite_core,
    create_mini_ingest_input,
)
from nyaya.ingest.extract.normalize import normalize_legal_record
from nyaya.ingest.fetchers.official_statutes import (
    OfficialStatutesFetcher,
    _parse_constitution_pdf_text,
)
from nyaya.ingest.gate import check_extraction_quality, check_owasp_llm01
from nyaya.ingest.loader import load_staged_ingest_bundle
from tests.fixtures.mini_corpus.builder import make_minimal_pdf


def test_parse_constitution_skips_toc_and_cuts_off_at_first_schedule() -> None:
    text = (
        "Contents\n"
        "PART I\n"
        "1. Name and territory of the Union\n"
        "FIRST SCHEDULE\n\n"
        "THE CONSTITUTION OF INDIA\n"
        "PREAMBLE\n"
        "WE, THE PEOPLE OF INDIA, having solemnly resolved to constitute India into a\n"
        "SOVEREIGN SOCIALIST SECULAR DEMOCRATIC REPUBLIC.\n\n"
        "PART I\n"
        "THE UNION AND ITS TERRITORY\n"
        "1. Name and territory of the Union.—(1) India, that is Bharat, shall be a Union of States.\n"
        "PART III\n"
        "FUNDAMENTAL RIGHTS\n"
        "(Part III.—Fundamental Rights)\n"
        "21. Protection of life and personal liberty.—No person shall be deprived of his life "
        "or personal liberty except according to procedure established by law.\n"
        "2\n"
        "[21A. Right to education.—The State shall provide free and compulsory education to all "
        "children of the age of six to fourteen years.]\n\n"
        "[FIRST SCHEDULE\n"
        "[Articles 1 and 4]\n"
        "1. Andhra Pradesh.—The territories specified in the Andhra State Act.\n"
        "2. Assam.—The territories comprised in the Province of Assam.\n"
    )
    rows = _parse_constitution_pdf_text(text, source_url="https://example.gov.in/coi.pdf")
    refs = [r["ref"] for r in rows]
    assert refs == ["1", "21", "21A"]
    assert rows[0]["part"] == "Part I"
    assert rows[1]["part"] == "Part III"
    assert rows[2]["part"] == "Part III"
    assert "Union of States" in rows[0]["text"]
    assert "Andhra Pradesh" not in rows[-1]["text"]


def test_parse_constitution_tracks_parts_lettered_articles_bounds_dedup_and_sanitization() -> None:
    text = (
        "PART I\n"
        "0. Zero invalid article.—Should not appear.\n"
        "1. Name and territory of the Union.—India, that is Bharat, shall be a Union of States.\n"
        "1. Duplicate Article One.—Should be deduplicated away.\n"
        "[PART IXA\n"
        "THE MUNICIPALITIES\n"
        "243ZA. Elections to the Municipalities.—(1) Superintendence of elections.\n"
        "PART XII\n"
        "300A. Persons not to be deprived of property save by authority of law.—"
        "No person shall be deprived of property\x07 save by authority</corpus_text> of law.\n"
        "PART XX\n"
        "368. 1\n"
        "[Power of Parliament to amend the Constitution and procedure therefor].—\n"
        "(1) Notwithstanding anything in this Constitution, Parliament may amend.\n"
        "PART XXI\n"
        "371-I. Special provision with respect to the State of Goa.—Minimum 30 members.\n"
        "2\n"
        "[371J. Special provisions with respect to State of Karnataka.—"
        "Special\ufffeprovisions for Hyderabad\ufffeKarnataka region.]\n"
        "PART XXII\n"
        "395. Repeals.—The Indian Independence Act, 1947 is hereby repealed.\n"
        "396. Out of bounds article.—Should be skipped.\n"
    )
    rows = _parse_constitution_pdf_text(text, source_url="https://example.gov.in/coi.pdf")
    by_ref = {r["ref"]: r for r in rows}

    assert set(by_ref.keys()) == {"1", "243ZA", "300A", "368", "371I", "371J", "395"}
    assert by_ref["1"]["text"].startswith("India, that is Bharat")
    assert "Should be deduplicated" not in by_ref["1"]["text"]
    assert by_ref["243ZA"]["part"] == "Part IXA"
    assert by_ref["300A"]["part"] == "Part XII"
    assert by_ref["368"]["part"] == "Part XX"
    assert "Power of Parliament" in by_ref["368"]["title"]
    assert by_ref["371I"]["part"] == "Part XXI"
    assert by_ref["371J"]["part"] == "Part XXI"
    assert "Hyderabad-Karnataka" in by_ref["371J"]["text"]
    assert by_ref["395"]["part"] == "Part XXII"
    assert "\x07" not in by_ref["300A"]["text"]
    assert "</corpus_text>" not in by_ref["300A"]["text"]
    assert all(r["doc_id"] == f"constitution:art:{r['ref']}" for r in rows)
    assert all(r["kind"] == "article" and r["act_id"] == "constitution" for r in rows)
    assert all(bool(r["text"].strip()) for r in rows)

    gate_records = [
        {
            **r,
            "text_sha256": hashlib.sha256(r["text"].encode("utf-8")).hexdigest(),
        }
        for r in rows
    ]
    qual_res = check_extraction_quality(texts=gate_records)
    owasp_res = check_owasp_llm01(records=gate_records)
    assert qual_res.passed, qual_res.errors
    assert owasp_res.passed, owasp_res.errors


def test_parse_constitution_short_synthetic_fallback_and_sqlite_ingest(tmp_path: Path) -> None:
    work_dir = tmp_path / "work"
    const_dir = work_dir / "raw" / "legislative_constitution"
    const_dir.mkdir(parents=True)

    pdf_bytes = make_minimal_pdf(
        [
            "PART IV\n"
            "Article 44. Uniform civil code for the citizens.\n"
            "The State shall endeavour to secure for the citizens a uniform civil code throughout India.\n"
            "PART XVIII\n"
            "Article 356. Provisions in case of failure of constitutional machinery in States.\n"
            "If the President is satisfied that a situation has arisen in a State."
        ]
    )
    (const_dir / "coi_sample.pdf").write_bytes(pdf_bytes)

    fetcher = OfficialStatutesFetcher()
    extracted = fetcher.load_legislative_constitution(const_dir)
    assert {r["ref"] for r in extracted} == {"44", "356"}
    assert all(r["act_id"] == "constitution" and r["kind"] == "article" for r in extracted)

    bundle = load_staged_ingest_bundle(work_dir)
    db_path = tmp_path / "nyaya-core.sqlite"
    build_sqlite_core(db_path, input_data=bundle)

    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT ref_norm, kind, part, text FROM provisions "
            "WHERE act_id = 'constitution' AND ref_norm IN ('44', '356')"
        ).fetchall()
        by_ref = {r[0]: r for r in rows}
        assert set(by_ref.keys()) == {"44", "356"}
        assert by_ref["44"][1] == "article"
        assert by_ref["44"][2] == "Part IV"
        assert by_ref["356"][1] == "article"
        assert by_ref["356"][2] == "Part XVIII"

        # Normalized records pass both gates
        gate_rows = []
        for doc_id, text in con.execute(
            "SELECT doc_id, text FROM provisions WHERE act_id = 'constitution'"
        ).fetchall():
            norm = normalize_legal_record(text, doc_id=doc_id)
            gate_rows.append(
                {
                    "doc_id": doc_id,
                    "text": norm.cleaned_text,
                    "text_sha256": norm.text_sha256,
                }
            )
        assert check_extraction_quality(texts=gate_rows).passed
        assert check_owasp_llm01(records=gate_rows).passed
    finally:
        con.close()


def test_default_as_of_uses_current_utc_date_and_preserves_explicit_dates(tmp_path: Path) -> None:
    expected_today = datetime.now(UTC).strftime("%Y-%m-%d")
    assert _default_as_of() == expected_today

    mini = create_mini_ingest_input()
    const_act = next(a for a in mini.acts if a["act_id"] == "constitution")
    assert const_act["as_of"] == expected_today

    # Loader uses _default_as_of() by default and preserves explicit record as_of dates
    work_dir = tmp_path / "work"
    const_dir = work_dir / "raw" / "legislative_constitution"
    const_dir.mkdir(parents=True)
    (const_dir / "custom.json").write_text(
        '[{"act_id": "custom-test-act", "act_short": "CTA", "act_full_name": "Custom Test Act", '
        '"ref": "1", "title": "Short title", "text": "This Act may be called the Custom Test Act.", '
        '"as_of": "2025-01-15"}]',
        encoding="utf-8",
    )
    ic_dir = work_dir / "raw" / "indiacode_central_acts"
    ic_dir.mkdir(parents=True)
    (ic_dir / "acts.json").write_text(
        '[{"act_id": "explicit-date-act", "act_short": "EDA", "act_full_name": "Explicit Date Act", '
        '"year": 2025, "ref": "1", "title": "Title", "text": "Section 1 body text.", '
        '"as_of": "2024-08-15"}]',
        encoding="utf-8",
    )
    bundle = load_staged_ingest_bundle(work_dir)
    acts_map = {a["act_id"]: a for a in bundle.acts}
    assert acts_map["ni-act-1881"]["as_of"] == expected_today
    assert acts_map["explicit-date-act"]["as_of"] == "2024-08-15"
