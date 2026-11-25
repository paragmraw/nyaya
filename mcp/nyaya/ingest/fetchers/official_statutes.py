"""Loaders and fetchers for official Indian statutes, Constitution Amendments 1–106,
Criminal Sanhita Concordance, Judicial Invalidations, and Courts Directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml  # type: ignore[import-untyped]

from ..lockfile import (
    DATA_DIR,
    DEFAULT_AMENDMENTS_PATH,
    DEFAULT_CONCORDANCE_PATH,
    DEFAULT_COURTS_PATH,
    DEFAULT_INVALIDATIONS_PATH,
)

DEFAULT_CURATED_PROVISIONS_PATH = DATA_DIR / "curated_statute_provisions.yaml"


def load_amendments_yaml(path: Path | str = DEFAULT_AMENDMENTS_PATH) -> list[dict[str, Any]]:
    """Load and return all 106 verified Constitutional Amendments from YAML."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("amendments"), list):
        raise ValueError(f"Invalid amendments YAML structure at {path}")
    return [dict(item) for item in raw["amendments"] if isinstance(item, dict)]


def load_concordance_yaml(path: Path | str = DEFAULT_CONCORDANCE_PATH) -> list[dict[str, Any]]:
    """Load and return verified IPC/CrPC/IEA <-> BNS/BNSS/BSA concordance mappings."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("mappings"), list):
        raise ValueError(f"Invalid criminal_concordance YAML structure at {path}")
    return [dict(item) for item in raw["mappings"] if isinstance(item, dict)]


def load_invalidations_yaml(path: Path | str = DEFAULT_INVALIDATIONS_PATH) -> list[dict[str, Any]]:
    """Load and return verified judicial invalidations (struck-down / read-down / abeyance)."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("invalidations"), list):
        raise ValueError(f"Invalid judicial_invalidations YAML structure at {path}")
    return [dict(item) for item in raw["invalidations"] if isinstance(item, dict)]


def load_courts_yaml(path: Path | str = DEFAULT_COURTS_PATH) -> list[dict[str, Any]]:
    """Load and return canonical courts, tribunals, regulators, and district directory."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("courts"), list):
        raise ValueError(f"Invalid courts_directory YAML structure at {path}")
    return [dict(item) for item in raw["courts"] if isinstance(item, dict)]


def load_curated_provisions_yaml(
    path: Path | str = DEFAULT_CURATED_PROVISIONS_PATH,
) -> list[dict[str, Any]]:
    """Load and return curated bare-act statutory provisions from YAML."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or not isinstance(raw.get("provisions"), list):
        raise ValueError(f"Invalid curated_statute_provisions YAML structure at {path}")
    return [dict(item) for item in raw["provisions"] if isinstance(item, dict)]
