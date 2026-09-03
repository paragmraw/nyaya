"""Per-tool contract tests + tool-registration fail-fast tests.

Two layers:

1. **Registration contract** — every register module lands exactly the tools
   it claims, with descriptions and read-only annotations, on a real FastMCP
   instance (no live DB: registration only inspects function signatures).

2. **Behavioural contract** — tool functions return structured error
   ``ToolResult``s for domain failures (``@structured_errors``) and pass raw
   arguments through to the db layer (faked via monkeypatch).

Also covers the Task 11 fail-fast stance in ``tools/__init__.py``: duplicate
or malformed registration raises instead of being silently skipped or
silently shadowed (FastMCP itself only logs a warning on duplicates and keeps
the FIRST registration), plus the Task 11 dedupe/removal contracts.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest
from fastmcp import FastMCP

from nyaya import db
from nyaya import tools as tools_pkg
from nyaya.models import Act, CrossRef, Document
from nyaya.tools import (
    cross_reference,
    get_amendments_for_article,
    get_section,
    schedules_amendments,
)
from nyaya.tools import list_acts as list_acts_tool
from nyaya.tools._error import structured_errors

# ---------------------------------------------------------------------------
# 1. Registration contract
# ---------------------------------------------------------------------------

EXPECTED_TOOLS = {
    "semantic_query", "get_section", "get_article", "get_judgment",
    "list_acts", "list_chapters", "list_sections", "list_articles",
    "list_judgments", "cross_reference", "list_schedules", "get_schedule",
    "list_amendments", "get_amendment", "get_amendments_for_article",
    "corpus_stats",
}


def _fresh_mcp() -> FastMCP:
    mcp = FastMCP(name="test")
    tools_pkg.register(mcp)
    return mcp


async def _tool_map(mcp: FastMCP) -> dict[str, Any]:
    return {t.name: t for t in await mcp.list_tools()}


async def test_all_16_tools_register_with_expected_names():
    mcp = _fresh_mcp()
    assert {t.name for t in await mcp.list_tools()} == EXPECTED_TOOLS


async def test_every_tool_has_description_and_read_only_annotation():
    for t in (await _tool_map(_fresh_mcp())).values():
        assert t.description, f"tool {t.name} has no description"
        annotations = t.annotations
        assert annotations is not None and annotations.readOnlyHint is True, (
            f"tool {t.name} must be annotated readOnlyHint=True (read-only corpus)"
        )
        assert annotations.openWorldHint is False, f"tool {t.name} must declare openWorldHint=False"


async def test_cross_reference_direction_is_a_closed_enum_in_the_schema():
    """direction must be a Literal enum in the tool's input schema, so invalid
    values are rejected by schema validation instead of reaching the db layer."""
    tools = await _tool_map(_fresh_mcp())
    enum = tools["cross_reference"].parameters["properties"]["direction"].get("enum")
    assert enum == ["from", "to", "both"]


async def test_list_tools_include_text_params_are_documented():
    """The include_text / snippet_chars params exist on the list-style tools
    (documented surface — see mcp/README.md)."""
    tools = await _tool_map(_fresh_mcp())
    for name in ("list_sections", "list_articles", "list_judgments"):
        props = tools[name].parameters["properties"]
        assert props["include_text"]["type"] == "boolean", name
        assert props["include_text"].get("default") is False, name
        assert props["snippet_chars"]["type"] == "integer", name


# ---------------------------------------------------------------------------
# 2. Registration fail-fast
# ---------------------------------------------------------------------------

def _mcp_with_tool(name: str) -> FastMCP:
    mcp = FastMCP(name="test")

    @mcp.tool(name=name)
    def _colliding() -> str:  # pragma: no cover - registration only
        return "x"

    return mcp


def test_duplicate_tool_registration_raises():
    """Registering over a colliding name must raise: FastMCP itself only logs
    a warning and silently keeps the FIRST registration, so a duplicate would
    otherwise be invisible (a crippled-but-booting server)."""
    mcp = _mcp_with_tool("semantic_query")
    with pytest.raises(RuntimeError, match="would overwrite"):
        tools_pkg.register(mcp)
    # The colliding module's registration never went through un-flagged: the
    # pre-existing tool is still the one registered (first wins is irrelevant
    # because we refuse to boot).


def test_duplicate_module_registration_raises_for_whole_loop():
    """register() over a full second pass must fail on the first duplicate."""
    mcp = _fresh_mcp()
    with pytest.raises(RuntimeError, match="would overwrite"):
        tools_pkg.register(mcp)


def test_raising_register_module_raises_runtime_error(monkeypatch):
    """A register function that raises fails the whole registration (no
    log-and-continue), wrapping the original error."""
    def _boom(_mcp):
        raise ValueError("broken tool module")

    saved = tools_pkg._REGISTRATIONS[:]
    tools_pkg._REGISTRATIONS[:] = [(_boom, "broken", frozenset({"broken"}))]
    try:
        with pytest.raises(RuntimeError, match="broken tool module"):
            tools_pkg.register(FastMCP(name="t"))
    finally:
        tools_pkg._REGISTRATIONS[:] = saved


def test_malformed_registration_missing_tools_raises(monkeypatch):
    """A module that lands fewer tools than it claims (malformed) must raise."""

    def register(_mcp):  # registers NOTHING
        return None

    saved = tools_pkg._REGISTRATIONS[:]
    tools_pkg._REGISTRATIONS[:] = [(register, "list_acts", frozenset({
        "list_acts", "list_chapters", "list_sections",
        "list_articles", "list_judgments",
    }))]
    try:
        with pytest.raises(RuntimeError, match="did not land"):
            tools_pkg.register(FastMCP(name="t"))
    finally:
        tools_pkg._REGISTRATIONS[:] = saved


def test_registry_introspection_is_none_without_fastmcp_internals():
    """On an object without the (private) component store the helper returns
    None and register() degrades to exception-only fail-fast."""

    class _OpaqueMCP:
        pass  # no _local_provider

    assert tools_pkg._registered_tool_names(_OpaqueMCP()) is None


def test_registration_helper_reads_fastmcp_component_store():
    mcp = FastMCP(name="t")
    get_section.register(mcp)
    assert tools_pkg._registered_tool_names(mcp) == {"get_section"}


# ---------------------------------------------------------------------------
# 3. Behavioural contract (db faked)
# ---------------------------------------------------------------------------

def _doc(kind: str = "section") -> Document:
    return Document(
        kind=kind, ref="302", act="IPC", title="Punishment for murder",
        text="Whoever commits murder shall be punished with death or imprisonment for life.",
        metadata={}, source="PRS (CC BY 4.0)",
    )


_ACT = {
    "short_name": "IPC", "full_name": "The Indian Penal Code, 1860",
    "year": 1860, "citation": "Act No. 45 of 1860", "kind": "criminal",
    "source": "PRS (CC BY 4.0)", "source_license": "CC BY 4.0", "as_of": None,
}


def _patch_db(monkeypatch, **overrides):
    """Patch the db functions the tool modules call, recording the calls."""
    calls: dict[str, Any] = {}

    def _mk(name, ret):
        def fn(*args, **kwargs):
            calls[name] = (args, kwargs)
            return ret

        return fn

    defaults: dict[str, Any] = {
        "get_section": _doc(),
        "get_cross_refs": [CrossRef(from_act="IPC", from_section="302",
                                    to_act="BNS", to_section="103",
                                    kind="replaced_by")],
        "list_chapters": [{"number": 1, "title": "Of Punishments"}],
        "get_act": Act(**_ACT),
        "list_sections": ([_doc()], 1, "Of Punishments"),
        "list_schedules": [_doc("schedule")],
        "get_schedule": _doc("schedule"),
        "list_amendments": [_doc("amendment")],
        "get_amendment": _doc("amendment"),
        "get_amendments_for_article": [_doc("amendment")],
    }
    for name, ret in {**defaults, **overrides}.items():
        monkeypatch.setattr(db, name, _mk(name, ret), raising=False)
    return calls


_TOOL_MODULES = {
    "get_section": get_section,
    "cross_reference": cross_reference,
    "list_chapters": list_acts_tool,
    "list_acts": list_acts_tool,
    "schedules_amendments": schedules_amendments,
    "get_amendments_for_article": get_amendments_for_article,
}


async def _tool(name: str) -> Any:
    """Register one tool module on a throwaway FastMCP and return its fn."""
    mcp = FastMCP(name="t")
    _TOOL_MODULES[name].register(mcp)
    tools = {t.name: t for t in await mcp.list_tools()}
    return tools[name].fn


async def test_get_section_passes_args_through_to_db(monkeypatch):
    """Normalization (case/alias/s. prefix) lives in the db layer; the tool is
    a pass-through (contract: tool does not second-guess db normalization)."""
    calls = _patch_db(monkeypatch)
    fn = await _tool("get_section")
    result = await fn(act="IPC", section=" s.302 ")
    assert result.ref == "302"
    assert calls["get_section"][0] == ("IPC", " s.302 ")


async def test_get_section_parses_citation_string_when_act_empty(monkeypatch):
    calls = _patch_db(monkeypatch)
    fn = await _tool("get_section")
    result = await fn(act="", section="IPC s.302")
    assert result.kind == "section"
    assert calls["get_section"][0] == ("IPC", "302")


async def test_get_section_not_found_is_structured_error(monkeypatch):
    _patch_db(monkeypatch, get_section=None)
    fn = await _tool("get_section")
    result = await fn(act="IPC", section="99999")
    assert result.is_error is True
    error = result.structured_content["error"]
    assert error["code"] == "not_found"
    assert error["kind"] == "section"
    assert error["hint"]
    assert any("99999" in c.text for c in result.content)


async def test_cross_reference_returns_list_with_direction(monkeypatch):
    calls = _patch_db(monkeypatch)
    fn = await _tool("cross_reference")
    result = await fn(act="IPC", section="302", direction="from")
    assert result.direction == "from"
    assert result.references[0].to_act == "BNS"
    assert calls["get_cross_refs"][1] == {"direction": "from"}


async def test_schedules_amendments_tools_surface(monkeypatch):
    _patch_db(monkeypatch, get_schedule=None)
    mcp = FastMCP(name="t")
    schedules_amendments.register(mcp)
    tools = {t.name: t for t in await mcp.list_tools()}
    assert set(tools) == {"list_schedules", "get_schedule", "list_amendments", "get_amendment"}

    scheds = await tools["list_schedules"].fn()
    assert [d.kind for d in scheds] == ["schedule"]

    # Not-found comes back as a structured error, not an exception.
    result = await tools["get_schedule"].fn(99)
    assert result.is_error is True
    assert result.structured_content["error"]["kind"] == "schedule"


async def test_get_amendments_for_article_empty_is_structured_not_found(monkeypatch):
    _patch_db(monkeypatch, get_amendments_for_article=[])
    fn = await _tool("get_amendments_for_article")
    result = await fn(article="31")
    assert result.is_error is True
    assert result.structured_content["error"]["kind"] == "article"


async def test_get_amendments_for_article_blank_is_structured_not_found(monkeypatch):
    _patch_db(monkeypatch)
    fn = await _tool("get_amendments_for_article")
    result = await fn(article="  ")
    assert result.is_error is True
    assert result.structured_content["error"]["kind"] == "article"


async def test_list_chapters_unknown_act_is_structured_not_found(monkeypatch):
    _patch_db(monkeypatch, list_chapters=[], get_act=None)
    fn = await _tool("list_chapters")
    result = await fn(act="NOPE")
    assert result.is_error is True
    error = result.structured_content["error"]
    assert error["kind"] == "act"
    assert "list_acts" in error["hint"]


async def test_list_sections_passes_pagination_and_text_flags(monkeypatch):
    calls = _patch_db(monkeypatch)
    mcp = FastMCP(name="t")
    list_acts_tool.register(mcp)
    tools = {t.name: t for t in await mcp.list_tools()}
    result = await tools["list_sections"].fn(
        act="IPC", chapter=16, limit=9999, offset=-3,
        include_text=False, snippet_chars=120,
    )
    assert result.total == 1
    assert result.limit == 500  # clamped from 9999
    assert result.offset == 0  # clamped from -3
    assert calls["list_sections"][1]["include_text"] is False
    assert calls["list_sections"][1]["snippet_chars"] == 120


async def test_structured_errors_catches_only_nyaya_errors():
    """Non-NyayaError exceptions propagate (FastMCP's default handling)."""

    @structured_errors
    async def boom():
        raise ValueError("not a nyaya error")


    try:
        await boom()
    except ValueError as exc:
        assert "not a nyaya error" in str(exc)
    else:
        raise AssertionError("ValueError should propagate through structured_errors")


# ---------------------------------------------------------------------------
# 4. Task 11 dedupe/removal contracts
# ---------------------------------------------------------------------------

def test_dead_embedding_service_symbols_are_gone():
    """The unused EmbeddingService indirection was deleted; the module-level
    embed_query/rerank_query functions are the only API."""
    import nyaya.embeddings as emb

    for gone in ("EmbeddingService", "get_default_service", "embed_texts"):
        assert not hasattr(emb, gone), f"{gone} should be removed"
    for kept in ("embed_query", "rerank_query"):
        assert hasattr(emb, kept), f"{kept} should remain"


def test_bidi_regex_has_a_single_home():
    """db.normalize_act must strip the same bidi characters as sanitize."""
    from nyaya.sanitize import BIDI_RE as sanitize_re

    assert db.BIDI_RE is sanitize_re
    assert not hasattr(inspect.getmodule(db), "_BIDI_RE")


def test_redact_url_has_a_single_home():
    """ratelimit imports config._redact_url instead of its own copy."""

    import nyaya.ratelimit as rl

    assert rl._redact_url is _redact_url_ref()
    assert "urlsplit" not in inspect.getsource(rl)


def _redact_url_ref():
    from nyaya.config import _redact_url

    return _redact_url
