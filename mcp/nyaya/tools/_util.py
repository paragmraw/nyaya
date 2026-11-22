"""Shared helpers for Nyaya v2.0 MCP tool implementations."""

from __future__ import annotations

import asyncio
import functools
from collections.abc import Callable
from datetime import date as Date
from typing import Any, ParamSpec, TypeVar

from ..citations import parse_single_citation
from ..config import get_settings
from ..exceptions import InvalidFilter
from ..models import SourceProvenance
from ..sanitize import frame_corpus_text

P = ParamSpec("P")
T = TypeVar("T")

MAX_QUERY_LENGTH = 4096
MAX_DRAFT_TEXT_LENGTH = 50_000


def effective_commercial_safe(commercial_safe_only: bool | None) -> bool:
    """Return True if either the caller argument or global setting enforces commercial-safe mode."""
    return bool(commercial_safe_only or get_settings().commercial_safe_only)


def validate_query_length(query: str, *, max_length: int = MAX_QUERY_LENGTH, field: str = "query") -> str:
    """Validate that a query string is within the allowed length."""
    if len(query) > max_length:
        raise InvalidFilter(
            f"{field} too long ({len(query)} chars); maximum is {max_length}.",
            kind=field,
            hint=f"Shorten {field} to at most {max_length} characters.",
        )
    return query


def parse_optional_date(val: Date | str | None, *, field: str = "date") -> Date | None:
    """Parse an optional ISO-8601 date string or Date object."""
    if val is None:
        return None
    if isinstance(val, Date):
        return val
    raw = str(val).strip()
    if not raw:
        return None
    try:
        return Date.fromisoformat(raw[:10])
    except ValueError as exc:
        raise InvalidFilter(
            f"Invalid {field} {val!r}; expected ISO format YYYY-MM-DD.",
            kind=field,
            hint="Use YYYY-MM-DD format (e.g. '2024-07-01').",
        ) from exc


def ensure_framed(
    text: str | None,
    *,
    doc_id: str,
    provenance: SourceProvenance,
    extraction_quality: str | None = "verified",
) -> str:
    """Wrap primary corpus text in an OWASP LLM01 ``<corpus_text ...>`` envelope if not already wrapped."""
    if not text:
        return ""
    stripped = text.lstrip()
    if stripped.startswith("<corpus_text") and stripped.rstrip().endswith("</corpus_text>"):
        return text
    return frame_corpus_text(
        text,
        doc_id=doc_id,
        source=provenance.source_id,
        license_spdx=provenance.license_spdx,
        commercial_safe=provenance.commercial_safe,
        as_of=provenance.as_of.isoformat(),
        sha256=provenance.content_sha256 or "",
        extraction_quality=extraction_quality,
        source_url=provenance.source_url,
    )


def resolve_act_and_ref(
    act: str = "",
    ref: str = "",
    citation: str | None = None,
    *,
    alias_map: dict[str, str] | None = None,
) -> tuple[str, str, str | None]:
    """Resolve (act, ref, sub_ref) from explicit (act, ref) or a free-form citation string."""
    clean_act = (act or "").strip()
    clean_ref = (ref or "").strip()
    raw_cit = (citation or "").strip()

    if raw_cit:
        parsed = parse_single_citation(raw_cit, alias_map=alias_map)
        if parsed is not None and parsed.act_or_court:
            return parsed.act_or_court, parsed.base_ref, parsed.sub_ref
        if not clean_act and not clean_ref:
            # Try splitting "IPC 302" or "IPC s.302"
            parts = raw_cit.split(None, 1)
            if len(parts) == 2:
                return parts[0], parts[1], None
            return "Constitution", raw_cit, None

    if not clean_act and clean_ref:
        parsed = parse_single_citation(clean_ref, alias_map=alias_map)
        if parsed is not None and parsed.act_or_court:
            return parsed.act_or_court, parsed.base_ref, parsed.sub_ref
        parts = clean_ref.split(None, 1)
        if len(parts) == 2:
            return parts[0], parts[1], None

    if clean_act and clean_ref:
        parsed = parse_single_citation(f"{clean_act} {clean_ref}", alias_map=alias_map)
        if parsed is not None and parsed.act_or_court and parsed.sub_ref:
            return parsed.act_or_court, parsed.base_ref, parsed.sub_ref

    return clean_act, clean_ref, None


def run_sync(func: Callable[P, T]) -> Callable[P, Any]:
    """Decorator: run synchronous storage/retrieval function in a worker thread."""

    @functools.wraps(func)
    async def wrapper(*args: Any, **kwargs: Any) -> T:
        return await asyncio.to_thread(func, *args, **kwargs)

    return wrapper
