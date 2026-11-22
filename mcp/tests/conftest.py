"""Hermetic test configuration for Nyaya MCP v2.0 (zero DATABASE_URL)."""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))


@pytest.fixture(autouse=True)
def offline_settings(monkeypatch: pytest.MonkeyPatch):
    from nyaya import config, storage

    for fn in (config.get_settings, config.get_rate_limit_settings):
        try:
            fn.cache_clear()
        except AttributeError:
            pass

    monkeypatch.delenv("DATABASE_URL", raising=False)

    yield

    storage.close_all()
    for fn in (config.get_settings, config.get_rate_limit_settings):
        try:
            fn.cache_clear()
        except AttributeError:
            pass
