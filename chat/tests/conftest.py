"""Test configuration: offline env, fake model + fake MCP tools."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("NVIDIA_API_KEY", "nvapi-test-key-1234567890")
os.environ.setdefault("NYAYA_MCP_URL", "http://localhost:8000/mcp")
os.environ.setdefault("PORT", "8001")


@pytest.fixture(autouse=True)
def _clear_caches():
    """Reset the lru_caches on get_settings/get_model before each test."""
    from nyaya_chat import config, llm
    config.reset_settings_cache()
    llm.reset_model_cache()
    yield
    config.reset_settings_cache()
    llm.reset_model_cache()


@pytest.fixture
def settings():
    from nyaya_chat.config import get_settings
    return get_settings()


@pytest.fixture
def fake_model(monkeypatch):
    """A FakeChatModel that yields a scripted sequence of AIMessages.

    Patches ``nyaya_chat.llm.get_model`` *and* the rebound reference in
    ``nyaya_chat.agent`` (which does ``from .llm import get_model`` at import
    time, binding its own reference). Must clear the real cache *before*
    patching.
    """
    from nyaya_chat import agent as agent_mod
    from nyaya_chat import llm as llm_mod
    llm_mod.reset_model_cache()  # clear the real singleton first
    fm = FakeChatModel()
    monkeypatch.setattr(llm_mod, "get_model", lambda _=None: fm)
    monkeypatch.setattr(agent_mod, "get_model", lambda _=None: fm, raising=False)
    return fm


@pytest.fixture
def fake_tools(monkeypatch):
    """Patch nyaya_chat.tools.load_tools AND the rebound reference in
    nyaya_chat.agent to return scripted LangChain tools. (``agent.py`` does
    ``from .tools import load_tools``, so patching only ``tools.load_tools``
    is not enough — the bound name in ``agent`` also needs patching.)"""
    from nyaya_chat import agent as agent_mod
    from nyaya_chat import tools as tools_mod
    tools = _make_fake_tools(["hybrid_search", "get_section"])
    async def _load(_=None):
        return tools
    monkeypatch.setattr(tools_mod, "load_tools", _load)
    monkeypatch.setattr(agent_mod, "load_tools", _load, raising=False)
    return tools


def _make_fake_tools(names):
    """Build minimal fake BaseTool objects that the ToolNode will invoke."""
    from langchain_core.tools import StructuredTool

    tools = []
    for name in names:
        def _run(query: str = "x", **kw):
            return f"{name}({query}) => result"

        async def _arun(query: str = "x", **kw):
            return f"{name}({query}) => result"

        t = StructuredTool.from_function(
            _run, coroutine=_arun, name=name, description=f"fake {name}",
        )
        tools.append(t)
    return tools


class FakeChatModel:
    """A minimal stand-in for ChatNVIDIA with scripted responses.

    ``bind_tools`` returns ``self`` (the agent calls ``.invoke`` on it). The
    ``responses`` list is consumed in order; each entry is an AIMessage or a
    dict turned into one.
    """

    def __init__(self, responses: list | None = None):
        from langchain_core.messages import AIMessage
        self.responses: list[AIMessage] = [
            r if isinstance(r, AIMessage) else AIMessage(content=r) for r in (responses or [])
        ]
        self._i = 0
        self.calls: list = []

    def bind_tools(self, tools, **kw):
        self._bound_tools = tools
        return self

    def invoke(self, messages, **kw):
        self.calls.append(messages)
        if self._i < len(self.responses):
            r = self.responses[self._i]
            self._i += 1
            return r
        return __import__("langchain_core.messages", fromlist=["AIMessage"]).AIMessage(content="(no more scripted responses)")

    async def ainvoke(self, messages, **kw):
        return self.invoke(messages, **kw)
