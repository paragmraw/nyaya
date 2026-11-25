"""Tests for nyaya_chat.tools_layer.cleaning: the shared v2 tool-result cleaner."""

from __future__ import annotations

import json

from nyaya_chat.config import MAX_TOOL_CHARS
from nyaya_chat.tools_layer.cleaning import (
    TOOL_SUMMARIZERS,
    clean_for_ui_summary,
    clean_tool_content,
    prune_list_result,
    strip_corpus_tags,
    summarize_tool_output,
)


def _search_statutes_response(n_hits: int, snippet_len: int = 2000) -> str:
    """A StatuteSearchResponse-shaped payload like search_statutes returns."""
    return json.dumps(
        {
            "query": "punishment for murder",
            "query_mode": "bm25",
            "total": n_hits * 5,
            "returned": n_hits,
            "next_cursor": "abc",
            "has_more": True,
            "degraded_sources": [],
            "results": [
                {
                    "doc_id": f"act{i}:{100 + i}",
                    "act": f"Act{i}",
                    "jurisdiction": "Central",
                    "kind": "section",
                    "ref": f"s. {100 + i}",
                    "title": f"T{i}",
                    "status": "in_force",
                    "replaced_by": [],
                    "snippet": f"HIT{i}-" + "z" * snippet_len,
                    "bm25_score": 1.0 - i * 0.01,
                    "provenance": {"source_id": "vaquill-central"},
                }
                for i in range(n_hits)
            ],
        }
    )


def test_clean_tool_content_string():
    assert clean_tool_content("short") == "short"
    assert clean_tool_content("x" * 9000) == "x" * (MAX_TOOL_CHARS - 1) + "…"


def test_clean_tool_content_cap_marks_truncation():
    result = clean_tool_content("x" * (MAX_TOOL_CHARS + 10))
    assert result.endswith("…")
    assert len(result) == MAX_TOOL_CHARS
    assert (
        clean_tool_content("\n\n  " + "y" * (MAX_TOOL_CHARS + 10))
        == "y" * (MAX_TOOL_CHARS - 1) + "…"
    )


def test_prune_condensed_snippet_marks_truncation():
    payload = _search_statutes_response(3, snippet_len=400)
    pruned = prune_list_result(payload, "search_statutes")
    assert isinstance(pruned, str)
    data = json.loads(pruned)
    tail = data["results"][1]["snippet"]
    assert tail.endswith("…")
    assert len(tail) == 300

    short = _search_statutes_response(2, snippet_len=100)
    data2 = json.loads(prune_list_result(short, "search_statutes"))
    assert not data2["results"][1]["snippet"].endswith("…")


def test_clean_tool_content_keeps_corpus_tags_by_default():
    """The tools node cleans results for the synthesis LLM, so the default must
    NOT strip <corpus_text> wrappers."""
    content = (
        '<corpus_text doc_id="ipc:302" source="vaquill" license="CC-BY-4.0" '
        'commercial_safe="true" as_of="2026-11-22" sha256="abc" trust="untrusted_primary_law">\n'
        "IPC s.302 punishment text\n"
        "</corpus_text>"
    )
    assert clean_tool_content(content) == content


def test_clean_tool_content_strips_parameterized_corpus_tags_when_asked():
    content = (
        '<corpus_text doc_id="ipc:302" source="vaquill" license="CC-BY-4.0" '
        'commercial_safe="true" as_of="2026-11-22" sha256="abc" trust="untrusted_primary_law">\n'
        "IPC s.302 punishment text\n"
        "</corpus_text>"
    )
    out = clean_tool_content(content, strip_corpus=True)
    assert out == "IPC s.302 punishment text"
    assert "<corpus_text" not in out
    assert "</corpus_text>" not in out


def test_clean_for_ui_summary_strips_unanchored_corpus_tags_inside_json():
    """Because v2 tools embed <corpus_text ...> inside JSON string values,
    clean_for_ui_summary must use unanchored regexes so UI summaries never
    leak <corpus_text> tags."""
    payload = json.dumps(
        {
            "act": "IPC",
            "ref": "302",
            "text": (
                '<corpus_text doc_id="ipc:302" source="vaquill" license="CC-BY-4.0" '
                'commercial_safe="true" as_of="2026-11-22" sha256="abc" trust="untrusted_primary_law">\n'
                "Whoever commits murder shall be punished with death.\n"
                "</corpus_text>"
            ),
        }
    )
    ui_text = clean_for_ui_summary(payload)
    assert "<corpus_text" not in ui_text
    assert "</corpus_text>" not in ui_text
    assert "Whoever commits murder" in ui_text


def test_strip_corpus_tags_leaves_unwrapped_text():
    assert strip_corpus_tags("plain result") == "plain result"


def test_clean_tool_content_stringified_blocks():
    content = "[{'type': 'text', 'text': 'alpha'}, {'type': 'text', 'text': 'beta'}]"
    assert clean_tool_content(content) == "alpha beta"


def test_clean_tool_content_list_of_blocks():
    blocks = [{"text": "alpha"}, {"text": "beta"}, {"type": "img", "src": "x"}]
    s = clean_tool_content(blocks)
    assert "alpha" in s and "beta" in s
    assert len(s) <= MAX_TOOL_CHARS


def test_clean_tool_content_other_types():
    assert clean_tool_content(123) == "123"
    assert clean_tool_content(None) == "None"


def test_prune_keeps_top_hit_verbatim_and_condenses_rest():
    content = _search_statutes_response(8)
    out = prune_list_result(content, "search_statutes")
    data = json.loads(out)
    assert len(data["results"]) == 8
    assert data["results"][0]["snippet"].startswith("HIT0-")
    assert len(data["results"][0]["snippet"]) == 5 + 2000
    assert "provenance" in data["results"][0]
    tail = data["results"][3]
    assert tail["act"] == "Act3"
    assert tail["ref"] == "s. 103"
    assert tail["bm25_score"] == 1.0 - 3 * 0.01
    assert len(tail["snippet"]) == 300
    assert tail["snippet"].endswith("…")
    assert "provenance" not in tail
    assert "total" in data and "returned" in data and "has_more" in data
    for dropped in ("query", "query_mode", "next_cursor", "degraded_sources"):
        assert dropped not in data
    assert len(out) < len(content)


def test_prune_polymorphic_browse_statute_modes():
    """browse_statute can return acts (catalog), chapters_or_parts (toc), or
    provisions (provisions). prune_list_result handles all three list_keys."""
    catalog_payload = json.dumps(
        {
            "mode": "catalog",
            "total": 2,
            "returned": 2,
            "has_more": False,
            "acts": [
                {"short_name": "IPC", "full_name": "Indian Penal Code", "jurisdiction": "Central", "status": "repealed", "extra": "kept"},
                {"short_name": "BNS", "full_name": "Bharatiya Nyaya Sanhita", "jurisdiction": "Central", "status": "in_force", "extra": "dropped"},
            ],
        }
    )
    pruned_cat = json.loads(prune_list_result(catalog_payload, "browse_statute"))
    assert pruned_cat["acts"][0]["extra"] == "kept"
    assert "extra" not in pruned_cat["acts"][1]
    assert pruned_cat["acts"][1]["short_name"] == "BNS"


def test_prune_single_hit_untouched():
    content = _search_statutes_response(1)
    assert prune_list_result(content, "search_statutes") is content


def test_prune_single_document_tool_untouched():
    full = json.dumps({"act": "IPC", "ref": "s. 302", "text": "FULL " * 500})
    assert prune_list_result(full, "get_provision") is full
    assert prune_list_result(full, "compare_provisions") is full
    assert prune_list_result(full, "get_judgment") is full
    assert prune_list_result(full) is full


def test_prune_unparseable_and_non_list_content_untouched():
    assert prune_list_result("plain prose result", "search_statutes") == "plain prose result"
    assert prune_list_result("{not json", "search_statutes") == "{not json"
    assert prune_list_result('{"results": []}', "search_statutes") == '{"results": []}'
    assert prune_list_result(None, "search_statutes") is None
    assert prune_list_result(123, "search_statutes") == 123


def test_tool_summarizers_cover_all_12_v2_tools_and_strip_corpus_tags():
    expected_12 = {
        "search_statutes",
        "get_provision",
        "browse_statute",
        "compare_provisions",
        "get_provision_history",
        "cross_reference",
        "search_judgments",
        "get_judgment",
        "trace_precedent",
        "verify_citations",
        "judgment_analytics",
        "corpus_status",
    }
    assert set(TOOL_SUMMARIZERS.keys()) == expected_12
    sample_json = json.dumps(
        {
            "act": "IPC",
            "ref": "302",
            "text": '<corpus_text doc_id="ipc:302" trust="untrusted_primary_law">\nPunishment for murder\n</corpus_text>',
        }
    )
    summary = summarize_tool_output("get_provision", sample_json)
    assert "<corpus_text" not in summary
    assert "</corpus_text>" not in summary
