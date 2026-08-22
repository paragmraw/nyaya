"""Tests for nyaya_chat.citations — citation parsing and verification."""

from __future__ import annotations

from nyaya_chat.citations import parse_citations, verify_citations


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
    tool_results = []
    result = verify_citations(answer, tool_results, had_tool_calls=False)
    assert "did not include verifiable" not in result.lower()


def test_verify_citations_search_response_results():
    answer = "Article 21 guarantees life [[act: Constitution, ref: 21]]."
    tool_results = ['{"results": [{"act": "Constitution", "ref": "21", "kind": "article"}]}']
    result = verify_citations(answer, tool_results, had_tool_calls=True)
    assert "[[act: Constitution, ref: 21]]" in result


def test_verify_citations_cross_ref_list():
    answer = "IPC 302 corresponds to BNS 103 [[act: BNS, ref: s. 103]]."
    tool_results = ['{"references": [{"from_act": "IPC", "from_section": "302", "to_act": "BNS", "to_section": "103", "kind": "corresponds_to"}]}']
    result = verify_citations(answer, tool_results, had_tool_calls=True)
    assert "[[act: BNS, ref: s. 103]]" in result


def test_verify_citations_error_tool_result_ignored():
    answer = "X [[act: IPC, ref: s. 302]]."
    tool_results = ['{"error": {"code": "not_found", "message": "not found"}}']
    result = verify_citations(answer, tool_results, had_tool_calls=True)
    # The citation is not grounded since the tool returned an error
    assert "did not include verifiable" in result.lower()


def test_verify_citations_handles_empty_tool_content():
    answer = "X [[act: IPC, ref: s. 302]]."
    result = verify_citations(answer, ["", "  "], had_tool_calls=True)
    assert "did not include verifiable" in result.lower()


def test_verify_citations_handles_non_json_tool_content():
    answer = "X [[act: IPC, ref: s. 302]]."
    result = verify_citations(answer, ["not json at all"], had_tool_calls=True)
    # Non-JSON content can't be parsed; citation is considered ungrounded
    assert "did not include verifiable" in result.lower()
