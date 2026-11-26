"""BYO live docket adapter protocol & httpx client with strict upstream URL hygiene."""

from __future__ import annotations

import os
import threading
from datetime import date as Date
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlsplit

import httpx

from ..exceptions import StorageUnavailable
from ..ingest.lockfile import _BANNED_URI_SUBSTRINGS
from .cause_list_store import CauseListEntry, parse_cause_list_feed


@runtime_checkable
class LiveDocketAdapter(Protocol):
    """Protocol for user-supplied official/licensed live cause-list & docket adapters."""

    def search_cause_lists(
        self,
        *,
        court: str | None = None,
        date: Date | str | None = None,
        bench: str | None = None,
        judge: str | None = None,
        party: str | None = None,
        advocate: str | None = None,
        case_number: str | None = None,
        cnr: str | None = None,
        limit: int = 25,
    ) -> list[CauseListEntry]: ...


class ByoLiveDocketAdapter:
    """Bring-your-own licensed/official live docket HTTP adapter.

    - Rejects any URL containing banned substrings from ``_BANNED_URI_SUBSTRINGS`` on init.
    - Raises ``StorageUnavailable`` when ``api_url`` or ``api_key`` are unset.
    - Queries the configured endpoint via ``httpx`` when configured.
    """

    def __init__(
        self,
        api_url: str | None = None,
        api_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
        *,
        timeout_s: float = 10.0,
    ) -> None:
        cleaned_url = api_url.strip() if api_url else None
        if cleaned_url:
            lower_url = cleaned_url.lower()
            for banned in _BANNED_URI_SUBSTRINGS:
                if banned in lower_url:
                    raise ValueError(
                        f"Live docket api_url {cleaned_url!r} contains banned upstream {banned!r}."
                    )
            parsed = urlsplit(cleaned_url)
            if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
                raise ValueError(
                    f"Live docket api_url {cleaned_url!r} must be a valid http(s) URL."
                )

        self.api_url: str | None = cleaned_url
        self.api_key: str | None = api_key.strip() if api_key else None
        self.transport: httpx.BaseTransport | None = transport
        self.timeout_s = timeout_s

    @property
    def is_configured(self) -> bool:
        return bool(self.api_url and self.api_key)

    def search_cause_lists(
        self,
        *,
        court: str | None = None,
        date: Date | str | None = None,
        bench: str | None = None,
        judge: str | None = None,
        party: str | None = None,
        advocate: str | None = None,
        case_number: str | None = None,
        cnr: str | None = None,
        limit: int = 25,
    ) -> list[CauseListEntry]:
        if not self.api_url or not self.api_key:
            raise StorageUnavailable(
                "Live docket adapter is not configured. Provide NYAYA_LIVE_DOCKET_URL and "
                "NYAYA_LIVE_DOCKET_API_KEY (or pass api_url and api_key) to enable live queries.",
                degraded_capabilities=["live_docket"],
                hint="Use live=False to query the local CauseListStore.",
            )

        params: dict[str, Any] = {"limit": max(1, min(int(limit), 100))}
        if court:
            params["court"] = court
        if date:
            params["date"] = date.isoformat() if isinstance(date, Date) else str(date)[:10]
        if bench:
            params["bench"] = bench
        if judge:
            params["judge"] = judge
        if party:
            params["party"] = party
        if advocate:
            params["advocate"] = advocate
        if case_number:
            params["case_number"] = case_number
        if cnr:
            params["cnr"] = cnr

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Accept": "application/json",
        }

        try:
            with httpx.Client(
                transport=self.transport,
                follow_redirects=False,
                timeout=self.timeout_s,
            ) as client:
                resp = client.get(self.api_url, params=params, headers=headers)
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPError as exc:
            raise StorageUnavailable(
                f"Live docket query to {self.api_url} failed: {exc}",
                degraded_capabilities=["live_docket"],
            ) from exc
        except ValueError as exc:
            raise StorageUnavailable(
                f"Live docket endpoint {self.api_url} returned invalid JSON: {exc}",
                degraded_capabilities=["live_docket"],
            ) from exc

        return parse_cause_list_feed(
            data,
            court=court or "",
            cause_list_date=date,
            bench=bench,
            judge=judge,
            source_url=self.api_url,
        )[: params["limit"]]


_adapter_lock = threading.Lock()
_live_adapter: LiveDocketAdapter | None = None


def get_live_docket_adapter() -> LiveDocketAdapter:
    """Return the configured LiveDocketAdapter (lazily reading NYAYA_LIVE_DOCKET_* env vars)."""
    with _adapter_lock:
        if _live_adapter is not None:
            return _live_adapter
        return ByoLiveDocketAdapter(
            api_url=(
                os.environ.get("NYAYA_LIVE_DOCKET_API_URL")
                or os.environ.get("NYAYA_LIVE_DOCKET_URL")
                or None
            ),
            api_key=os.environ.get("NYAYA_LIVE_DOCKET_API_KEY") or None,
        )


def set_live_docket_adapter(adapter: LiveDocketAdapter | None) -> None:
    """Override or clear the process-wide LiveDocketAdapter (primarily for tests)."""
    global _live_adapter
    with _adapter_lock:
        _live_adapter = adapter
