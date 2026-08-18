"""Test configuration.

Provides:
  - ``offline_settings``: forces env vars so get_settings() returns
    deterministic values. Autouse so every test gets a clean config.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("DATABASE_URL", "postgresql://nobody@nowhere/db")
os.environ.setdefault("NVIDIA_API_KEY", "nvapi-test-key")


@pytest.fixture(autouse=True)
def offline_settings(monkeypatch):
    from nyaya import config

    try:
        config.get_settings.cache_clear()
    except AttributeError:
        pass

    monkeypatch.setenv("DATABASE_URL", "postgresql://nobody@nowhere/db")
    monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test-key")

    yield

    try:
        config.get_settings.cache_clear()
    except AttributeError:
        pass