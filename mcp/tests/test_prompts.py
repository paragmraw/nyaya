"""Contract and behavioural tests for Nyaya MCP v2.0 8 resources and 4 prompts."""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastmcp import FastMCP

from nyaya import prompts as prompts_pkg
from nyaya import resources as resources_pkg
from nyaya import storage
from nyaya.exceptions import NotFound
from nyaya.models import JudgmentContent, JudgmentExcerpt, JudgmentMeta
from tests.test_tools_contract import _build_rich_memory_backend

EXPECTED_8_RESOURCE_URIS = {
    "nyaya://corpus/status",
    "nyaya://statutes",
    "nyaya://statute/{act}",
    "nyaya://provision/{act}/{ref}",
    "nyaya://amendment/{act}/{number}",
    "nyaya://judgment/{identifier}",
    "nyaya://judgment/{identifier}/paragraphs/{start}-{end}",
    "nyaya://courts",
}

EXPECTED_4_PROMPTS = {
    "audit_legal_draft",
    "transition_criminal_code",
    "precedent_research_memo",
    "irac_case_brief",
}


def _fresh_mcp_with_resources_and_prompts() -> FastMCP:
    mcp = FastMCP(name="test-nyaya-v2")
    resources_pkg.register(mcp)
    prompts_pkg.register(mcp)
    return mcp


async def test_all_8_nyaya_resources_and_templates_register():
    mcp = _fresh_mcp_with_resources_and_prompts()
    static_uris = {str(r.uri) for r in await mcp.list_resources()}
    template_uris = {str(t.uri_template) for t in await mcp.list_resource_templates()}
    assert (static_uris | template_uris) == EXPECTED_8_RESOURCE_URIS
    assert len(static_uris) + len(template_uris) == 8


async def test_all_4_mcp_prompts_register():
    mcp = _fresh_mcp_with_resources_and_prompts()
    names = {p.name for p in await mcp.list_prompts()}
    assert names == EXPECTED_4_PROMPTS
    assert len(names) == 4


async def test_prompts_render_corpus_text_isolation_tool_protocols_and_disclaimer():
    for prompt_fn, kwargs, expected_tools in [
        (
            prompts_pkg.audit_legal_draft,
            {"draft_text": "IPC s.302 and AIR 1973 SC 1461", "target_court": "Delhi High Court"},
            ["verify_citations", "compare_provisions", "trace_precedent"],
        ),
        (
            prompts_pkg.transition_criminal_code,
            {"provisions": "IPC 302, CrPC 438", "offence_or_fir_date": "2024-06-15"},
            ["compare_provisions", "search_judgments", "531"],
        ),
        (
            prompts_pkg.precedent_research_memo,
            {"legal_issue": "anticipatory bail", "forum_court": "Bombay High Court"},
            ["get_provision", "search_judgments", "trace_precedent"],
        ),
        (
            prompts_pkg.irac_case_brief,
            {"case_identifier": "AIR 1973 SC 1461", "focus_issue": "basic structure"},
            ["get_judgment", "trace_precedent"],
        ),
    ]:
        rendered = prompt_fn(**kwargs)
        assert "<corpus_text" in rendered
        assert "Not legal advice" in rendered
        for tool_ref in expected_tools:
            assert tool_ref in rendered


async def test_resources_resolve_and_frame_corpus_text():
    backend = _build_rich_memory_backend()
    backend.judgments["sc:1973:kesavananda"].pdf_s3_bucket = "indian-supreme-court-judgments"
    backend.judgments["sc:1973:kesavananda"].pdf_s3_key = "data/pdf/year=1973/english/kesavananda.pdf"
    storage.set_backend(backend)

    class _FakePdf:
        async def fetch_and_read(self, meta: JudgmentMeta, **kwargs: Any) -> JudgmentContent:
            return JudgmentContent(
                case_id=meta.case_id,
                mode=kwargs.get("mode", "overview"),
                extraction_quality="verified",
                extraction_method="pypdfium2_s3_pdf",
                excerpts=[JudgmentExcerpt(para_num=1, text="Basic structure ratio excerpt.")],
                total_paragraphs=10,
                total_pages=5,
                chars_per_page=900.0,
            )

        async def aclose(self) -> None:
            return None

    storage.set_pdf_fetcher(_FakePdf())

    status_json = json.loads(resources_pkg.read_corpus_status())
    assert status_json["service_version"] == "2.0.0"

    statutes_json = json.loads(resources_pkg.read_statutes())
    assert len(statutes_json["acts"]) >= 2

    statute_toc = json.loads(resources_pkg.read_statute("IPC"))
    assert statute_toc["act_info"]["short_name"] == "IPC"

    prov_json = json.loads(resources_pkg.read_provision("IPC", "302"))
    assert prov_json["act"] == "IPC"
    assert prov_json["text"].startswith("<corpus_text ")

    amend_json = json.loads(resources_pkg.read_amendment("Constitution", "42"))
    assert amend_json["amendments"][0]["number"] == 42
    assert amend_json["amendments"][0]["summary"].startswith("<corpus_text ")

    jud_json = json.loads(await resources_pkg.read_judgment("AIR 1973 SC 1461"))
    assert jud_json["card"]["case_id"] == "sc:1973:kesavananda"
    assert jud_json["excerpts"][0]["text"].startswith("<corpus_text ")

    paras_json = json.loads(await resources_pkg.read_judgment_paragraphs("AIR 1973 SC 1461", "1", "5"))
    assert paras_json["view"] == "paragraphs"
    assert paras_json["excerpts"][0]["text"].startswith("<corpus_text ")

    courts_json = json.loads(resources_pkg.read_courts())
    assert "courts" in courts_json

    with pytest.raises(NotFound):
        resources_pkg.read_provision("IPC", "99999")
