"""Test configuration: offline env, fake model + fake MCP tools."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("NVIDIA_API_KEY", "nvapi-test-key-1234567890")


@pytest.fixture(autouse=True)
def _clear_caches():
    """Reset the lru_caches on get_settings/get_model before each test."""
    from nyaya_chat import config, graph, guardrail, llm
    config.reset_settings_cache()
    llm.reset_model_cache()
    graph.reset_graph()
    guardrail.reset_classifier_cache()
    yield
    config.reset_settings_cache()
    llm.reset_model_cache()
    graph.reset_graph()
    guardrail.reset_classifier_cache()


@pytest.fixture
def settings():
    from nyaya_chat.config import get_settings
    return get_settings()


@pytest.fixture
def fake_model(monkeypatch):
    """A FakeChatModel that yields a scripted sequence of AIMessages.

    Patches ``nyaya_chat.llm.get_model`` *and* the rebound reference in
    ``nyaya_chat.graph`` (which does ``from ..llm import get_model`` at import
    time, binding its own reference). Must clear the real cache *before*
    patching.
    """
    from nyaya_chat import graph as graph_mod
    from nyaya_chat import llm as llm_mod
    llm_mod.reset_model_cache()  # clear the real singleton first
    fm = FakeChatModel()
    monkeypatch.setattr(llm_mod, "get_model", lambda _=None: fm)
    monkeypatch.setattr(graph_mod, "get_model", lambda _=None: fm, raising=False)
    return fm


@pytest.fixture
def fake_tools(monkeypatch):
    """Patch ``tools_layer.load_tools`` AND the rebound reference in
    ``nyaya_chat.graph`` to return scripted LangChain tools. (``graph.py``
    does ``from ..tools_layer import load_tools``, so patching only the
    package function is not enough — the bound name in ``graph`` also needs
    patching.)"""
    from nyaya_chat import graph as graph_mod
    from nyaya_chat import tools_layer as tools_layer_mod
    tools = _make_fake_tools(["semantic_query", "get_section"])
    async def _load(_=None):
        return tools
    monkeypatch.setattr(tools_layer_mod, "load_tools", _load)
    monkeypatch.setattr(graph_mod, "load_tools", _load, raising=False)
    return tools


def _make_fake_tools(names):
    """Build minimal fake BaseTool objects that the ToolNode will invoke.

    Results are JSON documents shaped like the real native tools (act/ref
    fields), so downstream consumers that ground citations against tool
    output see realistic content. The args schema accepts any fields (the
    real tool specs differ per tool); the function echoes act/ref from them."""
    import json as _json

    from langchain_core.tools import StructuredTool
    from pydantic import BaseModel

    def _result(name, kw):
        ref = kw.get("section_number") or kw.get("section") or "1"
        return _json.dumps({
            "act": kw.get("act") or "IPC",
            "ref": ref,
            "text": f"{name} result",
        })

    # Explicit args schema: LangChain binds tool-call args through it, and its
    # signature inference from a coroutine-only StructuredTool yields nothing.
    # Defaults are None (not "1") so `_result`'s fallback chain can tell an
    # unset field from a supplied one.
    class _FakeArgs(BaseModel):
        act: str | None = None
        section_number: str | None = None
        section: str | None = None
        query: str | None = None

    tools = []
    for name in names:
        def _run(act: str | None = None, section_number: str | None = None,
                 section: str | None = None, query: str | None = None, _name=name):
            return _result(_name, {"act": act, "section_number": section_number, "section": section})

        async def _arun(act: str | None = None, section_number: str | None = None,
                        section: str | None = None, query: str | None = None, _name=name):
            return _result(_name, {"act": act, "section_number": section_number, "section": section})

        t = StructuredTool.from_function(
            _run, coroutine=_arun, name=name, description=f"fake {name}",
            args_schema=_FakeArgs,
        )
        tools.append(t)
    return tools


class FakeChatModel:
    """A minimal stand-in for ChatNVIDIA with scripted responses.

    Supports both ``bind_tools`` (returns self) and ``with_structured_output``
    (returns a _FakeStructuredRunnable that returns the scripted result).
    The ``responses`` list is consumed in order; each entry is an AIMessage
    or a dict turned into one.

    For structured output tests, set ``fm._structured_result`` to the
    expected structured object (e.g. a ToolPlan or Intent).

    Fake protocol for ``graph._make_model``: the class attribute
    ``nyaya_fake_model = True`` marks an instance as a test fake, and
    ``with_generation_params(temperature=..., max_tokens=...)`` receives the
    phase's generation settings. Fakes are scripted, so "honouring" the
    settings means recording them (on ``.temperature`` /
    ``.max_completion_tokens``) for assertions — not changing the scripted
    output.
    """

    # Explicit marker checked by graph._make_model (no duck-type sniffing).
    nyaya_fake_model = True

    def __init__(self, responses: list | None = None):
        from langchain_core.messages import AIMessage
        self.responses: list[AIMessage] = [
            r if isinstance(r, AIMessage) else AIMessage(content=r) for r in (responses or [])
        ]
        self._i = 0
        self.calls: list = []
        self._structured_schema = None
        self._structured_result = None
        self.temperature = None
        self.max_completion_tokens = None

    def bind_tools(self, tools, **kw):
        self._bound_tools = tools
        return self

    def with_generation_params(self, *, temperature=None, max_tokens=None):
        """Record the generation settings requested for this phase; returns self."""
        self.temperature = temperature
        self.max_completion_tokens = max_tokens
        return self

    def with_structured_output(self, schema, **kw):
        self._structured_schema = schema
        return _FakeStructuredRunnable(self._structured_result)

    def invoke(self, messages, **kw):
        self.calls.append(messages)
        # A structured-output model returns the parsed schema object, not a
        # scripted AIMessage — honour _structured_result when it is set.
        if self._structured_result is not None:
            return self._structured_result
        if self._i < len(self.responses):
            r = self.responses[self._i]
            self._i += 1
            return r
        return __import__("langchain_core.messages", fromlist=["AIMessage"]).AIMessage(content="(no more scripted responses)")

    async def ainvoke(self, messages, **kw):
        return self.invoke(messages, **kw)

    async def astream(self, messages, **kw):
        """Stream the next scripted response as a single chunk."""
        self.calls.append(messages)
        if self._i < len(self.responses):
            r = self.responses[self._i]
            self._i += 1
            yield r
        else:
            from langchain_core.messages import AIMessage
            yield AIMessage(content="(no more scripted responses)")


class _FakeStructuredRunnable:
    """Stand-in for the Runnable returned by ``with_structured_output``.

    Tests set ``FakeChatModel._structured_result`` to control what
    ``ainvoke`` returns (typically a ``ToolPlan`` instance or ``None``).
    """

    def __init__(self, result):
        self._result = result

    def invoke(self, messages, **kw):
        return self._result

    async def ainvoke(self, messages, **kw):
        return self._result
