"""Tests for the MCP tool functions.

Tools are async coroutines (wrapped via ``run_sync``) so ``_tool`` awaits them.
"""

from __future__ import annotations

import pytest

from nyaya.exceptions import EmbeddingUnavailable, NotFound


def _make_app(fake_db):
    from fastmcp import FastMCP
    from nyaya.tools import register as register_tools
    from nyaya.resources import register as register_resources

    mcp = FastMCP(name="nyaya-test")
    register_tools(mcp)
    register_resources(mcp)
    return mcp


async def _tool(app, name, **args):
    tool = await app.get_tool(name)
    return await tool.fn(**args)


# ---------------------------------------------------------------------------
# list_acts / list_chapters / list_sections / list_articles
# ---------------------------------------------------------------------------

async def test_list_acts_tool(fake_db):
    app = _make_app(fake_db)
    result = await _tool(app, "list_acts")
    assert result.acts[0].short_name == "IPC"
    assert any(a.short_name == "Constitution" for a in result.acts)
    # Provenance is populated (regression guard for the hardcoded-provenance bug).
    for a in result.acts:
        assert a.source
        assert a.as_of is not None


async def test_list_acts_case_insensitive(fake_db):
    """Normalization: 'ipc' should resolve to 'IPC'."""
    app = _make_app(fake_db)
    # list_chapters with lowercase act should work via normalization.
    result = await _tool(app, "list_chapters", act="ipc")
    assert result.act == "ipc"
    assert result.chapters[0].title == "Preliminary"


async def test_list_chapters_tool(fake_db):
    app = _make_app(fake_db)
    result = await _tool(app, "list_chapters", act="IPC")
    assert result.act == "IPC"
    assert result.chapters[0].title == "Preliminary"


async def test_list_chapters_unknown_act(fake_db):
    app = _make_app(fake_db)
    with pytest.raises(NotFound) as exc_info:
        await _tool(app, "list_chapters", act="Nonexistent")
    assert exc_info.value.kind == "act"


async def test_list_sections_tool(fake_db):
    app = _make_app(fake_db)
    result = await _tool(app, "list_sections", act="IPC")
    assert len(result.sections) == 2  # stub returns 2 sections
    assert result.sections[0].section == "302"
    assert result.total == 2  # pagination metadata present


# ---------------------------------------------------------------------------
# get_section / get_article
# ---------------------------------------------------------------------------

async def test_get_section_tool(fake_db):
    app = _make_app(fake_db)
    s = await _tool(app, "get_section", act="IPC", section="302")
    assert s.act == "IPC"
    assert s.section == "302"
    assert "murder" in s.text.lower()
    assert s.source == "mratanusarkar/Indian-Laws"
    # Full-field regression guard.
    assert s.chapter_number == 16
    assert s.source_license == "Public domain"
    assert s.as_of is not None


async def test_get_section_normalizes(fake_db):
    """Whitespace and 's.' prefix are stripped."""
    app = _make_app(fake_db)
    s = await _tool(app, "get_section", act=" ipc ", section="s. 302 ")
    assert s.section == "302"


async def test_get_section_missing(fake_db):
    app = _make_app(fake_db)
    with pytest.raises(NotFound) as exc_info:
        await _tool(app, "get_section", act="IPC", section="999")
    assert exc_info.value.kind == "section"


async def test_get_article_tool(fake_db):
    app = _make_app(fake_db)
    a = await _tool(app, "get_article", article="21")
    assert a.number == "21"
    assert "life" in a.text.lower()
    assert a.part.startswith("PART III")
    # Provenance is now derived from the Constitution act row, not hardcoded.
    assert a.source_license == "Apache-2.0"
    assert a.as_of is not None


async def test_get_article_missing(fake_db):
    app = _make_app(fake_db)
    with pytest.raises(NotFound) as exc_info:
        await _tool(app, "get_article", article="999")
    assert exc_info.value.kind == "article"


# ---------------------------------------------------------------------------
# search_law
# ---------------------------------------------------------------------------

async def test_search_law_tool(fake_db):
    app = _make_app(fake_db)
    r = await _tool(app, "search_law", query="murder")
    assert r.total == 2  # stub returns 2 hits now (for pagination testing)
    assert r.returned == 2
    assert r.results[0].act == "IPC"
    assert r.results[0].ref == "s. 302"
    assert r.results[0].kind == "section"
    assert r.as_of is not None


async def test_search_law_limit_clamped(fake_db):
    """Real test: the stub returns 1 hit; clamping to 50 keeps total=1 (<= 50).
    This is no longer a tautology because the stub now respects limit — if the
    clamp were removed, limit=999 would be passed to the stub which returns 1.
    So we additionally verify the returned limit field reflects the clamp."""
    app = _make_app(fake_db)
    r = await _tool(app, "search_law", query="murder", limit=999)
    assert r.total <= 50
    assert r.limit == 50  # the clamp


async def test_search_law_limit_lower_bound(fake_db):
    app = _make_app(fake_db)
    r = await _tool(app, "search_law", query="murder", limit=0)
    assert r.limit == 1  # clamped to >= 1


async def test_search_law_act_filter(fake_db):
    """The act filter is forwarded to the DB layer (stub respects it)."""
    app = _make_app(fake_db)
    r = await _tool(app, "search_law", query="murder", act="NonexistentAct")
    assert r.total == 0
    assert r.results == []


async def test_search_law_empty_query(fake_db):
    app = _make_app(fake_db)
    r = await _tool(app, "search_law", query="")
    assert r.total == 0
    assert r.results == []


# ---------------------------------------------------------------------------
# cross_reference (bidirectional)
# ---------------------------------------------------------------------------

async def test_cross_reference_tool(fake_db):
    app = _make_app(fake_db)
    r = await _tool(app, "cross_reference", act="IPC", section="302")
    assert r.from_act == "IPC"
    assert r.direction == "both"
    assert r.references[0].to_act == "BNS"
    assert r.references[0].kind == "corresponds_to"


async def test_cross_reference_reverse(fake_db):
    """Bidirectional lookup: querying BNS 103 should find the IPC 302 ref."""
    app = _make_app(fake_db)
    r = await _tool(app, "cross_reference", act="BNS", section="103")
    # The to-direction lookup should find the IPC->BNS row.
    assert any(ref.from_act == "IPC" for ref in r.references)


async def test_cross_reference_empty(fake_db):
    """A section with no refs returns an empty list, not an error."""
    app = _make_app(fake_db)
    r = await _tool(app, "cross_reference", act="IPC", section="999")
    assert r.references == []


# ---------------------------------------------------------------------------
# semantic_query
# ---------------------------------------------------------------------------

async def test_semantic_query_disabled(fake_db, monkeypatch):
    from nyaya import embeddings
    monkeypatch.setattr(embeddings, "embed_query",
                        lambda _q: (_ for _ in ()).throw(EmbeddingUnavailable("disabled in tests")))
    app = _make_app(fake_db)
    with pytest.raises(EmbeddingUnavailable):
        await _tool(app, "semantic_query", query="right to privacy")


async def test_semantic_query_happy_path(fake_db, monkeypatch):
    """When embeddings are available, semantic_query returns ranked results."""
    from nyaya import embeddings
    monkeypatch.setattr(embeddings, "embed_query", lambda _q: [0.1] * 1024)
    app = _make_app(fake_db)
    r = await _tool(app, "semantic_query", query="right to privacy")
    assert r.total >= 1
    assert r.results[0].kind == "section"


# ---------------------------------------------------------------------------
# get_judgment
# ---------------------------------------------------------------------------

async def test_get_judgment_tool(fake_db):
    app = _make_app(fake_db)
    j = await _tool(app, "get_judgment", case_slug="AIR 1973 SC 1461")
    assert "Kesavananda" in j.case_name


async def test_get_judgment_missing(fake_db):
    app = _make_app(fake_db)
    with pytest.raises(NotFound) as exc_info:
        await _tool(app, "get_judgment", case_slug="nonexistent-case")
    assert exc_info.value.kind == "judgment"


# ---------------------------------------------------------------------------
# search_judgments
# ---------------------------------------------------------------------------

async def test_search_judgments_tool(fake_db):
    app = _make_app(fake_db)
    r = await _tool(app, "search_judgments", query="basic structure")
    assert r.total == 1
    assert r.results[0].kind == "judgment"


# ---------------------------------------------------------------------------
# list_schedules / get_schedule / list_amendments / get_amendment
# ---------------------------------------------------------------------------

async def test_list_schedules_tool(fake_db):
    app = _make_app(fake_db)
    r = await _tool(app, "list_schedules")
    assert len(r.schedules) == 1
    assert r.schedules[0].number == 1


async def test_get_schedule_tool(fake_db):
    app = _make_app(fake_db)
    s = await _tool(app, "get_schedule", number=1)
    assert s.title == "States"


async def test_get_schedule_missing(fake_db):
    app = _make_app(fake_db)
    with pytest.raises(NotFound) as exc_info:
        await _tool(app, "get_schedule", number=99)
    assert exc_info.value.kind == "schedule"


async def test_list_amendments_tool(fake_db):
    app = _make_app(fake_db)
    r = await _tool(app, "list_amendments")
    assert len(r.amendments) == 1
    assert r.amendments[0].number == 1


async def test_get_amendment_tool(fake_db):
    app = _make_app(fake_db)
    a = await _tool(app, "get_amendment", number=1)
    assert a.year == 1951


async def test_get_amendment_missing(fake_db):
    app = _make_app(fake_db)
    with pytest.raises(NotFound):
        await _tool(app, "get_amendment", number=999)


# ---------------------------------------------------------------------------
# get_sections_by_range
# ---------------------------------------------------------------------------

async def test_get_sections_by_range_tool(fake_db):
    app = _make_app(fake_db)
    r = await _tool(app, "get_sections_by_range", act="IPC", start="299", end="377")
    assert len(r.sections) == 1
    assert r.sections[0].section == "302"


async def test_get_sections_by_range_unknown_act(fake_db):
    app = _make_app(fake_db)
    with pytest.raises(NotFound) as exc_info:
        await _tool(app, "get_sections_by_range", act="Nonexistent", start="1", end="10")
    assert exc_info.value.kind == "act"


async def test_get_sections_by_range_non_numeric(fake_db):
    from nyaya.exceptions import SearchError
    app = _make_app(fake_db)
    with pytest.raises(SearchError):
        await _tool(app, "get_sections_by_range", act="IPC", start="abc", end="def")


# ---------------------------------------------------------------------------
# get_definition
# ---------------------------------------------------------------------------

async def test_get_definition_tool(fake_db):
    app = _make_app(fake_db)
    r = await _tool(app, "get_definition", term="good faith")
    assert r.total >= 1
    assert r.query == "good faith"


async def test_get_definition_empty_term(fake_db):
    app = _make_app(fake_db)
    r = await _tool(app, "get_definition", term="")
    assert r.total == 0
    assert r.results == []


# ---------------------------------------------------------------------------
# corpus_stats
# ---------------------------------------------------------------------------

async def test_corpus_stats_tool(fake_db):
    app = _make_app(fake_db)
    r = await _tool(app, "corpus_stats")
    assert r.acts == 2
    assert r.sections == 511
    assert r.as_of is not None


# ---------------------------------------------------------------------------
# hybrid_search
# ---------------------------------------------------------------------------

async def test_hybrid_search_tool(fake_db, monkeypatch):
    from nyaya import embeddings
    monkeypatch.setattr(embeddings, "embed_query", lambda _q: [0.1] * 1024)
    app = _make_app(fake_db)
    r = await _tool(app, "hybrid_search", query="murder")
    assert r.total >= 1


async def test_hybrid_search_fallback(fake_db, monkeypatch):
    """When embeddings unavailable, hybrid_search falls back to FTS-only."""
    from nyaya import embeddings
    from nyaya.exceptions import EmbeddingUnavailable
    monkeypatch.setattr(embeddings, "embed_query",
                        lambda _q: (_ for _ in ()).throw(EmbeddingUnavailable("disabled")))
    app = _make_app(fake_db)
    r = await _tool(app, "hybrid_search", query="murder")
    assert r.total >= 1  # FTS results still returned


# ---------------------------------------------------------------------------
# resolve_citation
# ---------------------------------------------------------------------------

async def test_resolve_citation_section(fake_db):
    app = _make_app(fake_db)
    r = await _tool(app, "resolve_citation", citation="IPC s.302")
    assert r.section == "302"


async def test_resolve_citation_article(fake_db):
    app = _make_app(fake_db)
    r = await _tool(app, "resolve_citation", citation="Art.21")
    assert r.number == "21"


async def test_resolve_citation_judgment(fake_db):
    app = _make_app(fake_db)
    r = await _tool(app, "resolve_citation", citation="AIR 1973 SC 1461")
    assert "Kesavananda" in r.case_name


async def test_resolve_citation_not_found(fake_db):
    app = _make_app(fake_db)
    with pytest.raises(NotFound):
        await _tool(app, "resolve_citation", citation="nonexistent citation 999")


# ---------------------------------------------------------------------------
# get_chapter
# ---------------------------------------------------------------------------

async def test_get_chapter_tool(fake_db):
    app = _make_app(fake_db)
    r = await _tool(app, "get_chapter", act="IPC", chapter=16)
    assert r.number == 16
    assert r.title == "Of Offences Affecting the Human Body"
    assert len(r.sections) >= 1


async def test_get_chapter_unknown_act(fake_db):
    app = _make_app(fake_db)
    with pytest.raises(NotFound) as exc_info:
        await _tool(app, "get_chapter", act="Nonexistent", chapter=1)
    assert exc_info.value.kind == "act"


async def test_get_chapter_missing_chapter(fake_db):
    app = _make_app(fake_db)
    with pytest.raises(NotFound) as exc_info:
        await _tool(app, "get_chapter", act="IPC", chapter=999)
    assert exc_info.value.kind == "chapter"


# ---------------------------------------------------------------------------
# search_by_kind
# ---------------------------------------------------------------------------

async def test_search_by_kind_sections(fake_db):
    app = _make_app(fake_db)
    r = await _tool(app, "search_by_kind", query="murder", kind="section")
    assert r.total >= 1
    assert r.results[0].kind == "section"


async def test_search_by_kind_judgments(fake_db):
    app = _make_app(fake_db)
    r = await _tool(app, "search_by_kind", query="basic structure", kind="judgment")
    assert r.total == 1
    assert r.results[0].kind == "judgment"


# ---------------------------------------------------------------------------
# get_amendments_for_article
# ---------------------------------------------------------------------------

async def test_get_amendments_for_article_tool(fake_db):
    app = _make_app(fake_db)
    r = await _tool(app, "get_amendments_for_article", article="13")
    assert len(r.amendments) >= 1
    assert r.amendments[0].number == 1


# ---------------------------------------------------------------------------
# list_judgments
# ---------------------------------------------------------------------------

async def test_list_judgments_tool(fake_db):
    app = _make_app(fake_db)
    r = await _tool(app, "list_judgments")
    assert len(r.judgments) == 1
    assert r.total == 1


# ---------------------------------------------------------------------------
# Pagination tests
# ---------------------------------------------------------------------------

async def test_search_law_offset(fake_db):
    """Offset is respected: offset=1 returns the 2nd hit, not the 1st."""
    app = _make_app(fake_db)
    r = await _tool(app, "search_law", query="murder", limit=1, offset=0)
    assert r.total == 2  # 2 total hits in the stub
    assert r.returned == 1
    assert r.results[0].ref == "s. 302"
    r2 = await _tool(app, "search_law", query="murder", limit=1, offset=1)
    assert r2.returned == 1
    assert r2.results[0].ref == "s. 303"


async def test_search_law_offset_past_end(fake_db):
    """When offset >= total, returns empty with correct total."""
    app = _make_app(fake_db)
    r = await _tool(app, "search_law", query="murder", limit=10, offset=100)
    assert r.total == 2
    assert r.returned == 0
    assert r.results == []


async def test_search_law_negative_offset_clamped(fake_db):
    app = _make_app(fake_db)
    r = await _tool(app, "search_law", query="murder", offset=-5)
    assert r.offset == 0  # clamped to >= 0


# ---------------------------------------------------------------------------
# Direction param tests
# ---------------------------------------------------------------------------

async def test_cross_reference_direction_from(fake_db):
    app = _make_app(fake_db)
    r = await _tool(app, "cross_reference", act="IPC", section="302", direction="from")
    assert r.direction == "from"
    assert len(r.references) == 1


async def test_cross_reference_direction_to(fake_db):
    app = _make_app(fake_db)
    r = await _tool(app, "cross_reference", act="BNS", section="103", direction="to")
    assert r.direction == "to"
    assert len(r.references) == 1


async def test_cross_reference_invalid_direction(fake_db):
    from nyaya.exceptions import SearchError
    app = _make_app(fake_db)
    with pytest.raises(SearchError):
        await _tool(app, "cross_reference", act="IPC", section="302", direction="sideways")


# ---------------------------------------------------------------------------
# search_judgments filter tests
# ---------------------------------------------------------------------------

async def test_search_judgments_empty_query(fake_db):
    app = _make_app(fake_db)
    r = await _tool(app, "search_judgments", query="")
    assert r.total == 0


async def test_search_judgments_limit_clamped(fake_db):
    app = _make_app(fake_db)
    r = await _tool(app, "search_judgments", query="basic structure", limit=999)
    assert r.limit == 50


async def test_search_judgments_invalid_date(fake_db):
    from nyaya.exceptions import SearchError
    app = _make_app(fake_db)
    with pytest.raises(SearchError):
        await _tool(app, "search_judgments", query="x", date_from="last tuesday")