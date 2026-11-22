"""Tests for nyaya_chat.citations — citation parsing and verification (v2)."""

from __future__ import annotations

import json

from nyaya_chat.citations import (
    CITATION_MARKER_RE,
    CITATION_RE,
    extract_grounded_keys,
    parse_citations,
    verify_citations,
    verify_inline_markers,
)


def test_marker_regex_aliases():
    assert CITATION_RE is CITATION_MARKER_RE


def test_parse_citations_empty():
    assert parse_citations("No citations here.") == []


def test_parse_citations_single():
    text = "Murder is punishable by death [[act: IPC, ref: s. 302]]."
    cites = parse_citations(text)
    assert len(cites) == 1
    assert cites[0].act == "IPC"
    assert cites[0].ref == "s. 302"


def test_parse_citations_multiple():
    text = (
        "IPC says X [[act: IPC, ref: s. 302]]. "
        "BNS says Y [[act: BNS, ref: s. 103]]."
    )
    cites = parse_citations(text)
    assert len(cites) == 2
    assert cites[0].act == "IPC"
    assert cites[1].act == "BNS"


def test_parse_citations_dedup():
    text = "A [[act: IPC, ref: s. 302]] and again [[act: IPC, ref: s. 302]]."
    cites = parse_citations(text)
    assert len(cites) == 1  # deduped


def test_verify_citations_all_grounded():
    answer = "Murder is punishable by death [[act: IPC, ref: s. 302]]."
    tool_results = ['{"act": "IPC", "ref": "302", "kind": "section"}']
    result = verify_citations(answer, tool_results, had_tool_calls=True)
    assert "[[act: IPC, ref: s. 302]]" in result


def test_verify_citations_strip_ungrounded():
    answer = (
        "Murder is punishable by death [[act: IPC, ref: s. 302]]. "
        "Also theft [[act: IPC, ref: s. 999]]."
    )
    tool_results = ['{"act": "IPC", "ref": "302", "kind": "section"}']
    result = verify_citations(answer, tool_results, had_tool_calls=True)
    assert "[[act: IPC, ref: s. 302]]" in result
    assert "999" not in result  # ungrounded citation stripped


def test_verify_citations_no_citations_with_tools_adds_caveat():
    answer = "Murder is punishable by death."
    tool_results = ['{"act": "IPC", "ref": "302", "kind": "section"}']
    result = verify_citations(answer, tool_results, had_tool_calls=True)
    assert "did not include verifiable" in result.lower()


def test_verify_citations_no_citations_no_tools_no_caveat():
    answer = "I don't know."
    tool_results: list[str] = []
    result = verify_citations(answer, tool_results, had_tool_calls=False)
    assert "did not include verifiable" not in result.lower()


def test_verify_citations_search_response_results():
    answer = "Article 21 guarantees life [[act: Constitution, ref: 21]]."
    tool_results = ['{"results": [{"act": "Constitution", "ref": "21", "kind": "article"}]}']
    result = verify_citations(answer, tool_results, had_tool_calls=True)
    assert "[[act: Constitution, ref: 21]]" in result


def test_verify_citations_cross_ref_list():
    answer = "IPC 302 corresponds to BNS 103 [[act: BNS, ref: s. 103]]."
    tool_results = [
        '{"references": [{"from_act": "IPC", "from_section": "302", "to_act": "BNS", "to_section": "103", "kind": "corresponds_to"}]}'
    ]
    result = verify_citations(answer, tool_results, had_tool_calls=True)
    assert "[[act: BNS, ref: s. 103]]" in result


def test_verify_citations_error_tool_result_ignored():
    answer = "X [[act: IPC, ref: s. 302]]."
    tool_results = ['{"error": {"code": "not_found", "message": "not found"}}']
    result = verify_citations(answer, tool_results, had_tool_calls=True)
    assert "did not include verifiable" in result.lower()


def test_verify_citations_handles_empty_tool_content():
    answer = "X [[act: IPC, ref: s. 302]]."
    result = verify_citations(answer, ["", "  "], had_tool_calls=True)
    assert "did not include verifiable" in result.lower()


def test_verify_citations_handles_non_json_tool_content():
    answer = "X [[act: IPC, ref: s. 302]]."
    result = verify_citations(answer, ["not json at all"], had_tool_calls=True)
    assert "did not include verifiable" in result.lower()


def test_verify_citations_strips_whitespace_variant_marker():
    answer = "Claim one [[act:IPC,ref: s. 999]] and claim two."
    result = verify_citations(
        answer,
        ['{"act": "IPC", "ref": "s. 302", "text": "..."}'],
        had_tool_calls=True,
    )
    assert "[[act:IPC,ref:" not in result
    assert "claim two" in result


def test_verify_citations_keeps_grounded_whitespace_variant_marker():
    answer = "Murder is punished [[act: IPC , ref: s.302]] harshly."
    result = verify_citations(
        answer,
        ['{"act": "IPC", "ref": "s. 302", "text": "..."}'],
        had_tool_calls=True,
    )
    assert "[[act: IPC , ref: s.302]]" in result


def test_verify_citations_strip_preserves_paragraph_breaks():
    answer = "First para ends here [[act: Ghost, ref: 1]].\n\n  - indented bullet\n  - another"
    result = verify_citations(
        answer,
        ['{"act": "IPC", "ref": "s. 302", "text": "..."}'],
        had_tool_calls=True,
    )
    assert "\n\n" in result
    assert "  - indented bullet" in result
    assert "Ghost" not in result


def test_verify_citations_strip_repairs_punctuation_space():
    answer = "Ends with marker [[act: Ghost, ref: 1]]. Next sentence."
    result = verify_citations(
        answer,
        ['{"act": "IPC", "ref": "s. 302", "text": "..."}'],
        had_tool_calls=True,
    )
    assert "marker." in result
    assert ". Next" in result
    assert "  " not in result.replace("\n", "")


# ---------------------------------------------------------------------------
# v2 Regression Matrix: First-number bug, act=None judgments, INSC, HC neutral,
# CNR, SCR/SCC/AIR, s.65B(4), Art. 21A, O. 7 R. 11, regulations, circulars
# ---------------------------------------------------------------------------


def test_v2_fixes_first_number_bug_air_1973_sc_9999_vs_1461():
    """v0.2 extracted only the first integer ('1973'), allowing hallucinated
    AIR 1973 SC 9999 to falsely match AIR 1973 SC 1461. v2 compares the full
    canonical (reporter, year, court, page) key."""
    tool_payload = json.dumps(
        {
            "card": {
                "case_id": "sc:1973:kesavananda",
                "act": None,
                "reporter_citations": ["AIR 1973 SC 1461", "(1973) 4 SCC 225", "[1973] Supp SCR 1"],
            }
        }
    )
    answer = (
        "Basic structure held in Kesavananda [[act: judgment, ref: AIR 1973 SC 1461]] "
        "and [[act: SC, ref: [1973] Supp SCR 1]], not fake [[act: judgment, ref: AIR 1973 SC 9999]] "
        "or fake [[act: judgment, ref: (1973) 4 SCC 999]]."
    )
    verified = verify_citations(answer, [tool_payload], had_tool_calls=True)
    assert "[[act: judgment, ref: AIR 1973 SC 1461]]" in verified
    assert "[[act: SC, ref: [1973] Supp SCR 1]]" in verified
    assert "AIR 1973 SC 9999" not in verified
    assert "(1973) 4 SCC 999" not in verified


def test_v2_grounds_judgments_with_act_none_and_all_citation_formats():
    """Judgments have act=None in raw metadata. v2 grounds INSC, HC neutral,
    SCC, AIR, SCR, and 16-char CNR markers."""
    tool_payload = json.dumps(
        {
            "results": [
                {
                    "case_id": "sc:2017:puttaswamy",
                    "act": None,
                    "neutral_citation": "2017 INSC 801",
                    "reporter_citations": ["(2017) 10 SCC 1"],
                },
                {
                    "case_id": "hc:2024:dhc:1234",
                    "cnr": "DLHC010123452024",
                    "neutral_citation": "2024:DHC:1234",
                    "reporter_citations": [],
                },
                {
                    "case_id": "hc:2024:bhc:567",
                    "cnr": "MHAU019999992015",
                    "neutral_citation": "2024:BHC-OS:567",
                    "reporter_citations": [],
                },
            ]
        }
    )
    keys = extract_grounded_keys([tool_payload])
    assert "judgment::INSC::2017:801" in keys
    assert "judgment::SCC::2017:10::1" in keys
    assert "judgment::HC_NEUTRAL::2024:DHC:1234" in keys
    assert "judgment::HC_NEUTRAL::2024:BHC-OS:567" in keys
    assert "judgment::CNR::MHAU019999992015" in keys

    answer = (
        "Privacy in Puttaswamy [[act: SC, ref: 2017 INSC 801]] / [[act: judgment, ref: (2017) 10 SCC 1]]. "
        "Delhi HC ruling [[act: DHC, ref: 2024:DHC:1234]]. "
        "Bombay HC order [[act: BHC, ref: 2024:BHC-OS:567]] (CNR [[act: judgment, ref: MHAU019999992015]]). "
        "Hallucinated HC [[act: DHC, ref: 2024:DHC:9999]]."
    )
    verified = verify_inline_markers(answer, [tool_payload], had_tool_calls=True)
    assert "[[act: SC, ref: 2017 INSC 801]]" in verified
    assert "[[act: judgment, ref: (2017) 10 SCC 1]]" in verified
    assert "[[act: DHC, ref: 2024:DHC:1234]]" in verified
    assert "[[act: BHC, ref: 2024:BHC-OS:567]]" in verified
    assert "[[act: judgment, ref: MHAU019999992015]]" in verified
    assert "2024:DHC:9999" not in verified


def test_v2_grounds_subsections_articles_cpc_regulations_circulars_amendments():
    """Supports s. 65B(4), Art. 21A, O. 7 R. 11(d), SEBI regulations, circulars,
    notifications, and Constitutional amendments while stripping 302A when only 302 is grounded."""
    tool_payloads = [
        json.dumps({"act": "EvidenceAct", "ref": "65B", "kind": "section"}),
        json.dumps({"act": "Constitution", "ref": "21A", "kind": "article"}),
        json.dumps({"act": "CPC", "ref": "Order 7 Rule 11", "kind": "order_rule"}),
        json.dumps({"act": "SEBI-LODR", "ref": "30", "kind": "regulation"}),
        json.dumps({"act": "RBI", "ref": "Circular 2024-25/12", "kind": "circular"}),
        json.dumps({"act": "CGST", "ref": "Notification 12/2024", "kind": "circular"}),
        json.dumps({"act": "Constitution", "amendments": [{"act": "Constitution", "number": 42, "year": 1976}]}),
        json.dumps({"act": "IPC", "ref": "302", "kind": "section"}),
    ]
    answer = (
        "Electronic certificate [[act: IEA, ref: s. 65B(4)]]. "
        "Right to education [[act: Constitution, ref: Art. 21A]]. "
        "Rejection of plaint [[act: CPC, ref: O. VII R. 11(d)]]. "
        "Disclosure [[act: SEBI-LODR, ref: Reg. 30(1)]]. "
        "RBI directive [[act: RBI, ref: Circular 2024-25/12]]. "
        "GST notification [[act: CGST, ref: Notification 12/2024]]. "
        "42nd Amendment [[act: Constitution, ref: 42nd Amendment]]. "
        "Murder [[act: IPC, ref: s. 302]], but not [[act: IPC, ref: s. 302A]] "
        "or fake circular [[act: RBI, ref: Circular 2024-25/99]]."
    )
    verified = verify_citations(answer, tool_payloads, had_tool_calls=True)
    assert "[[act: IEA, ref: s. 65B(4)]]" in verified
    assert "[[act: Constitution, ref: Art. 21A]]" in verified
    assert "[[act: CPC, ref: O. VII R. 11(d)]]" in verified
    assert "[[act: SEBI-LODR, ref: Reg. 30(1)]]" in verified
    assert "[[act: RBI, ref: Circular 2024-25/12]]" in verified
    assert "[[act: CGST, ref: Notification 12/2024]]" in verified
    assert "[[act: Constitution, ref: 42nd Amendment]]" in verified
    assert "[[act: IPC, ref: s. 302]]" in verified
    assert "302A" not in verified
    assert "2024-25/99" not in verified
