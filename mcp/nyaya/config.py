"""Runtime configuration from environment variables."""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from dotenv import load_dotenv

load_dotenv()


def _required(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        raise RuntimeError(
            f"Required environment variable {name!r} is not set. "
            "Copy .env.example to .env and fill in the values."
        )
    return val


@dataclass(frozen=True)
class Settings:
    database_url: str
    port: int

    def as_dict(self) -> dict[str, Any]:
        return {"database_url": self.database_url[:20] + "…", "port": self.port}


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        database_url=_required("DATABASE_URL"),
        port=int(os.environ.get("PORT", "8000")),
    )