"""Unit tests for ``nyaya.citations`` (shared Indian legal citation parser & verifier)."""

from __future__ import annotations

import json

from nyaya.citations import (
    canonicalize_court,
    extract_grounded_keys,
    normalize_jurisdiction,
    parse_citations,
    parse_single_citation,
    verify_inline_markers,
)


def test_parse_cnr_and_neutral_citations() -> None:
    cnr = parse_single_citation("MHAU019999992015")
    assert cnr is not None
    assert cnr.kind == "cnr"
    assert cnr.canonical_key == "judgment::CNR::MHAU019999992015"

    sc = parse_single_citation("2024 INSC 123")
    assert sc is not None
    assert sc.kind == "sc_neutral"
    assert sc.canonical_key == "judgment::INSC::2024:123"

    sc_colon = parse_single_citation("2024:INSC:123")
    assert sc_colon is not None
    assert sc_colon.canonical_key == sc.canonical_key

    dhc = parse_single_citation("2024:DHC:1234")
    assert dhc is not None
    assert dhc.kind == "hc_neutral"
    assert dhc.canonical_key == "judgment::HC_NEUTRAL::2024:DHC:1234"

    bhc = parse_single_citation("2024:BHC-OS:567")
    assert bhc is not None
    assert bhc.kind == "hc_neutral"
    assert bhc.canonical_key == "judgment::HC_NEUTRAL::2024:BHC-OS:567"


def test_parse_reporters_air_scc_scr_other() -> None:
    air = parse_single_citation("AIR 1973 SC 1461")
    assert air is not None
    assert air.kind == "air"
    assert air.canonical_key == "judgment::AIR::1973:SC:1461"

    scc = parse_single_citation("(2017) 10 SCC 1")
    assert scc is not None
    assert scc.kind == "scc"
    assert scc.canonical_key == "judgment::SCC::2017:10::1"

    scc_cri = parse_single_citation("(2018) 2 SCC (Cri) 100")
    assert scc_cri is not None
    assert scc_cri.canonical_key == "judgment::SCC::2018:2:CRI:100"

    scr = parse_single_citation("[1973] Supp SCR 1")
    assert scr is not None
    assert scr.kind == "scr"
    assert scr.canonical_key == "judgment::SCR::1973:SUPP:1"

    online = parse_single_citation("2023 SCC OnLine Del 412")
    assert online is not None
    assert online.kind == "other_reporter"
    assert online.canonical_key == "judgment::SCC_ONLINE::2023:DEL:412"


def test_parse_statutes_articles_schedules_cpc_amendments_regulators() -> None:
    iea = parse_single_citation("IEA s.65B(4)")
    assert iea is not None
    assert iea.kind == "statute_section"
    assert iea.canonical_key == "statute::EvidenceAct::65B"
    assert iea.base_ref == "65B"
    assert iea.sub_ref == "(4)"

    cpc = parse_single_citation("CPC O.VII R.11(d)")
    assert cpc is not None
    assert cpc.kind == "cpc_order_rule"
    assert cpc.canonical_key == "statute::CPC::O7-R11"
    assert cpc.sub_ref == "(d)"

    sched = parse_single_citation("Schedule VII List I Entry 82")
    assert sched is not None
    assert sched.kind == "constitution_schedule"
    assert sched.canonical_key == "statute::Constitution::SCH7"
    assert sched.sub_ref == "List I Entry 82"

    art = parse_single_citation("Art. 19(1)(a)")
    assert art is not None
    assert art.kind == "constitution_article"
    assert art.canonical_key == "statute::Constitution::19"
    assert art.sub_ref == "(1)(a)"

    amend = parse_single_citation("42nd Amendment")
    assert amend is not None
    assert amend.kind == "constitution_amendment"
    assert amend.canonical_key == "amendment::Constitution::42"

    sebi = parse_single_citation("SEBI LODR Reg. 30(1)")
    assert sebi is not None
    assert sebi.kind == "regulation_clause"
    assert sebi.canonical_key == "regulation::SEBI-LODR::30"
    assert sebi.sub_ref == "(1)"


def test_parse_chained_sections_and_priority_dedup() -> None:
    chained = parse_citations("Accused charged under IPC ss. 302, 307 r/w 34 and 2024 INSC 123.")
    keys = [c.canonical_key for c in chained]
    assert "judgment::INSC::2024:123" in keys
    assert "statute::IPC::302" in keys
    assert "statute::IPC::307" in keys
    assert "statute::IPC::34" in keys
    # 2024 INSC 123 must NOT also parse as a section number
    assert len([k for k in keys if "123" in k]) == 1


def test_canonicalize_court() -> None:
    assert canonicalize_court("Bom") == "BHC"
    assert canonicalize_court("Bombay HC") == "BHC"
    assert canonicalize_court("Delhi High Court") == "DHC"
    assert canonicalize_court("7_26") == "DHC"
    assert canonicalize_court("Supreme Court") == "SC"


def test_verify_inline_markers_negative_matrix_fixes_v02_bugs() -> None:
    tool_payload = json.dumps(
        {
            "card": {
                "case_id": "sc:1973:kesavananda",
                "act": None,
                "neutral_citation": "2017 INSC 801",
                "reporter_citations": ["AIR 1973 SC 1461", "(2017) 10 SCC 1"],
            },
            "provision": {"act": "IPC", "ref": "302"},
        }
    )

    grounded = extract_grounded_keys([tool_payload])
    assert "judgment::AIR::1973:SC:1461" in grounded
    assert "judgment::SCC::2017:10::1" in grounded
    assert "judgment::INSC::2017:801" in grounded
    assert "statute::IPC::302" in grounded

    answer = (
        "As held in Kesavananda [[act: judgment, ref: AIR 1973 SC 1461]] "
        "and hallucinated [[act: judgment, ref: AIR 1973 SC 9999]], "
        "privacy [[act: judgment, ref: (2017) 10 SCC 1]] and [[act: SC, ref: 2017 INSC 801]], "
        "under murder [[act: IPC, ref: 302]] vs fake [[act: IPC, ref: 302A]]."
    )
    verified = verify_inline_markers(answer, [tool_payload], had_tool_calls=True)
    # Valid judgments (even with act=None) and valid IPC 302 survive:
    assert "[[act: judgment, ref: AIR 1973 SC 1461]]" in verified
    assert "[[act: judgment, ref: (2017) 10 SCC 1]]" in verified
    assert "[[act: SC, ref: 2017 INSC 801]]" in verified
    assert "[[act: IPC, ref: 302]]" in verified
    # Hallucinated AIR 1973 SC 9999 (first-number bug!) and IPC 302A are STRIPPED:
    assert "AIR 1973 SC 9999" not in verified
    assert "302A" not in verified
    assert "did not include verifiable citations" not in verified


def test_parse_wide_hallucinated_citations() -> None:
    text = (
        "Reliance on 2024 INSC 999999, AIR 1973 SC 9999999, "
        "(2017) 10 SCC 999999, and [1973] Supp SCR 999999."
    )
    parsed = parse_citations(text)
    keys = [c.canonical_key for c in parsed]
    assert "judgment::INSC::2024:999999" in keys
    assert "judgment::AIR::1973:SC:9999999" in keys
    assert "judgment::SCC::2017:10::999999" in keys
    assert "judgment::SCR::1973:SUPP:999999" in keys


def test_alias_map_and_act_year_before_section() -> None:
    cases = [
        ("Arbitration and Conciliation Act, 1996, Section 11", "ArbitrationAct", "11"),
        ("Arbitration Act s. 34", "ArbitrationAct", "34"),
        ("Negotiable Instruments Act, 1881, Section 138", "NIAct", "138"),
        ("NIAct s. 138", "NIAct", "138"),
        ("Specific Relief Act, 1963, Section 14", "SpecificReliefAct", "14"),
        ("SRA s. 10", "SpecificReliefAct", "10"),
        ("SEBI Act, 1992, Section 11", "SEBIAct", "11"),
        ("Transfer of Property Act, 1882, Section 54", "TransferOfPropertyAct", "54"),
        ("TPA s. 54", "TransferOfPropertyAct", "54"),
        ("Limitation Act, 1963, Section 5", "LimitationAct", "5"),
        ("Indian Contract Act, 1872, Section 73", "ContractAct", "73"),
    ]
    for raw, exp_act, exp_ref in cases:
        p = parse_single_citation(raw)
        assert p is not None, raw
        assert p.kind == "statute_section", raw
        assert p.act_or_court == exp_act, raw
        assert p.base_ref == exp_ref, raw
        assert p.canonical_key == f"statute::{exp_act}::{exp_ref}", raw


def test_parse_cpc_plural_and_inverted_order_rule() -> None:
    plural1 = parse_citations("Under Order 7 Rules 11 and 14 of CPC")
    assert [c.canonical_key for c in plural1] == ["statute::CPC::O7-R11", "statute::CPC::O7-R14"]
    assert [c.base_ref for c in plural1] == ["Order 7 Rule 11", "Order 7 Rule 14"]

    plural2 = parse_citations("Order VII Rules 11, 14")
    assert [c.canonical_key for c in plural2] == ["statute::CPC::O7-R11", "statute::CPC::O7-R14"]

    inv = parse_single_citation("Rule 11 of Order 7")
    assert inv is not None
    assert inv.kind == "cpc_order_rule"
    assert inv.canonical_key == "statute::CPC::O7-R11"
    assert inv.base_ref == "Order 7 Rule 11"

    pair = parse_citations("Order 7 Rule 11, Order 8 Rule 1")
    assert [c.canonical_key for c in pair] == ["statute::CPC::O7-R11", "statute::CPC::O8-R1"]


def test_normalize_jurisdiction_all_states_uts_and_short_codes() -> None:
    assert normalize_jurisdiction(None) is None
    assert normalize_jurisdiction("   ") is None
    assert normalize_jurisdiction("Maharashtra") == "MH"
    assert normalize_jurisdiction("maharashtra") == "MH"
    assert normalize_jurisdiction("Delhi") == "DL"
    assert normalize_jurisdiction("NCT of Delhi") == "DL"
    assert normalize_jurisdiction("Karnataka") == "KA"
    assert normalize_jurisdiction("Tamil Nadu") == "TN"
    assert normalize_jurisdiction("West Bengal") == "WB"
    assert normalize_jurisdiction("Uttar Pradesh") == "UP"
    assert normalize_jurisdiction("Gujarat") == "GJ"
    assert normalize_jurisdiction("Jammu & Kashmir") == "JK"
    assert normalize_jurisdiction("Jammu and Kashmir") == "JK"
    assert normalize_jurisdiction("Telangana") == "TS"
    assert normalize_jurisdiction("Odisha") == "OD"
    assert normalize_jurisdiction("Orissa") == "OD"
    assert normalize_jurisdiction("Uttarakhand") == "UK"
    assert normalize_jurisdiction("Andaman and Nicobar Islands") == "AN"
    assert normalize_jurisdiction("Dadra and Nagar Haveli and Daman and Diu") == "DN"
    assert normalize_jurisdiction("Ladakh") == "LA"
    assert normalize_jurisdiction("Lakshadweep") == "LD"
    assert normalize_jurisdiction("Puducherry") == "PY"
    assert normalize_jurisdiction("central") == "Central"
    assert normalize_jurisdiction("Central") == "Central"
    assert normalize_jurisdiction("MH") == "MH"
    assert normalize_jurisdiction("SEBI") == "SEBI"
    assert normalize_jurisdiction("RBI") == "RBI"
