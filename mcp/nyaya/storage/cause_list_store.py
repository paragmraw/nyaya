"""Local SQLite/JSONL cause-list store & official sci.gov.in / HC daily feed parser."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Mapping, Sequence
from datetime import date as Date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, Field

from ..citations import canonicalize_court
from ..exceptions import StorageUnavailable
from ..ingest.lockfile import _BANNED_URI_SUBSTRINGS


class CauseListEntry(BaseModel):
    """Structured daily cause-list item from an official Supreme Court or High Court feed."""

    court: str
    cause_list_date: str
    bench: str | None = None
    judge: str | None = None
    item_number: str | None = None
    case_number: str | None = None
    cnr: str | None = None
    parties: str | None = None
    advocates: list[str] = Field(default_factory=list)
    purpose: str | None = None
    source_url: str = ""


def _validate_feed_url(url: str) -> None:
    if not url:
        return
    lower = url.strip().lower()
    for banned in _BANNED_URI_SUBSTRINGS:
        if banned in lower:
            raise ValueError(f"Cause list source URL {url!r} contains banned upstream {banned!r}.")
    parsed = urlsplit(url.strip())
    if parsed.scheme and parsed.scheme.lower() not in ("http", "https"):
        raise ValueError(f"Cause list source URL {url!r} must use http or https.")


def _normalize_advocates(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        return [str(x).strip() for x in raw if str(x).strip()]
    text = str(raw).strip()
    if not text:
        return []
    if text.startswith("["):
        try:
            loaded = json.loads(text)
            if isinstance(loaded, list):
                return [str(x).strip() for x in loaded if str(x).strip()]
        except json.JSONDecodeError:
            pass
    sep = ";" if ";" in text else ","
    return [part.strip() for part in text.split(sep) if part.strip()]


def _normalize_entry(
    raw: CauseListEntry | Mapping[str, Any],
    *,
    default_court: str = "",
    default_date: str = "",
    default_bench: str | None = None,
    default_judge: str | None = None,
    default_source_url: str = "",
) -> CauseListEntry:
    if isinstance(raw, CauseListEntry):
        return raw
    date_val = raw.get("cause_list_date") or raw.get("date") or default_date
    parties = raw.get("parties")
    if not parties:
        pet = str(raw.get("petitioner") or "").strip()
        resp = str(raw.get("respondent") or "").strip()
        if pet and resp:
            parties = f"{pet} v. {resp}"
        elif pet or resp:
            parties = pet or resp
    advocates = _normalize_advocates(raw.get("advocates") or raw.get("counsel"))
    item_no = raw.get("item_number") or raw.get("item_no") or raw.get("sr_no")
    return CauseListEntry(
        court=str(raw.get("court") or default_court),
        cause_list_date=str(date_val)[:10] if date_val else "",
        bench=str(raw["bench"]).strip() if raw.get("bench") else default_bench,
        judge=str(raw["judge"]).strip() if raw.get("judge") else default_judge,
        item_number=str(item_no).strip() if item_no is not None and str(item_no).strip() else None,
        case_number=str(raw["case_number"]).strip() if raw.get("case_number") else None,
        cnr=str(raw["cnr"]).strip() if raw.get("cnr") else None,
        parties=str(parties).strip() if parties else None,
        advocates=advocates,
        purpose=str(raw["purpose"]).strip() if raw.get("purpose") else None,
        source_url=str(raw.get("source_url") or default_source_url),
    )


class _CauseListTableParser(HTMLParser):
    """Minimal stdlib HTML table parser for official sci.gov.in / HC cause-list pages."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._cell_is_header = False
        self._cur_cell: list[str] = []
        self._cur_row: list[tuple[bool, str]] = []
        self.tables: list[list[list[tuple[bool, str]]]] = []
        self._cur_table: list[list[tuple[bool, str]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        _ = attrs
        t = tag.lower()
        if t == "table":
            self._in_table = True
            self._cur_table = []
        elif t == "tr" and self._in_table:
            self._in_row = True
            self._cur_row = []
        elif t in ("td", "th") and self._in_row:
            self._in_cell = True
            self._cell_is_header = t == "th"
            self._cur_cell = []
        elif t == "br" and self._in_cell:
            self._cur_cell.append(" ")

    def handle_endtag(self, tag: str) -> None:
        t = tag.lower()
        if t in ("td", "th") and self._in_cell:
            text = " ".join("".join(self._cur_cell).split())
            self._cur_row.append((self._cell_is_header, text))
            self._in_cell = False
        elif t == "tr" and self._in_row:
            if self._cur_row:
                self._cur_table.append(self._cur_row)
            self._in_row = False
        elif t == "table" and self._in_table:
            if self._cur_table:
                self.tables.append(self._cur_table)
            self._in_table = False

    def handle_data(self, data: str) -> None:
        if self._in_cell:
            self._cur_cell.append(data)


def _map_header_col(header: str) -> str | None:
    h = header.strip().lower()
    if any(k in h for k in ("item", "sr", "sl")):
        return "item_number"
    if "cnr" in h:
        return "cnr"
    if "case" in h:
        return "case_number"
    if any(k in h for k in ("part", "title", "petitioner", "cause title")):
        return "parties"
    if any(k in h for k in ("advocate", "counsel", "lawyer")):
        return "advocates"
    if any(k in h for k in ("purpose", "stage", "remark", "listing")):
        return "purpose"
    if "bench" in h or "court hall" in h:
        return "bench"
    if "judge" in h or "coram" in h:
        return "judge"
    if "date" in h:
        return "cause_list_date"
    return None


def parse_cause_list_feed(
    payload: str | bytes | Mapping[str, Any] | Sequence[Any],
    *,
    court: str,
    cause_list_date: Date | str | None = None,
    bench: str | None = None,
    judge: str | None = None,
    source_url: str = "",
) -> list[CauseListEntry]:
    """Parse an official JSON, JSONL, or HTML daily cause-list feed into CauseListEntry records."""
    _validate_feed_url(source_url)
    date_str = cause_list_date.isoformat() if isinstance(cause_list_date, Date) else str(cause_list_date or "")[:10]

    if isinstance(payload, bytes):
        payload = payload.decode("utf-8", errors="replace")

    if isinstance(payload, Mapping):
        items = (
            payload.get("entries")
            or payload.get("results")
            or payload.get("data")
            or payload.get("cause_list")
            or payload.get("items")
            or []
        )
        return [
            _normalize_entry(
                it,
                default_court=court,
                default_date=date_str,
                default_bench=bench,
                default_judge=judge,
                default_source_url=source_url,
            )
            for it in items
            if isinstance(it, Mapping)
        ]

    if isinstance(payload, Sequence) and not isinstance(payload, str):
        return [
            _normalize_entry(
                it,
                default_court=court,
                default_date=date_str,
                default_bench=bench,
                default_judge=judge,
                default_source_url=source_url,
            )
            for it in payload
            if isinstance(it, (CauseListEntry, Mapping))
        ]

    raw_text = str(payload).strip()
    if not raw_text:
        return []

    if raw_text.startswith(("{", "[")):
        try:
            loaded = json.loads(raw_text)
            return parse_cause_list_feed(
                loaded,
                court=court,
                cause_list_date=date_str,
                bench=bench,
                judge=judge,
                source_url=source_url,
            )
        except json.JSONDecodeError:
            # May be JSONL (multiple lines of JSON objects)
            entries: list[CauseListEntry] = []
            for line in raw_text.splitlines():
                line = line.strip()
                if not line:
                    continue
                obj = json.loads(line)
                if isinstance(obj, Mapping):
                    entries.append(
                        _normalize_entry(
                            obj,
                            default_court=court,
                            default_date=date_str,
                            default_bench=bench,
                            default_judge=judge,
                            default_source_url=source_url,
                        )
                    )
            return entries

    # HTML daily cause list table parsing (sci.gov.in / High Courts)
    parser = _CauseListTableParser()
    parser.feed(raw_text)
    entries = []
    for table in parser.tables:
        if len(table) < 2:
            continue
        header_row = table[0]
        col_map: dict[int, str] = {}
        for idx, (_, cell_text) in enumerate(header_row):
            mapped = _map_header_col(cell_text)
            if mapped:
                col_map[idx] = mapped
        if not col_map:
            continue
        for row in table[1:]:
            if all(is_hdr for is_hdr, _ in row):
                continue
            row_dict: dict[str, Any] = {}
            for idx, (_, cell_text) in enumerate(row):
                field_name = col_map.get(idx)
                if field_name and cell_text:
                    row_dict[field_name] = cell_text
            if row_dict.get("case_number") or row_dict.get("parties") or row_dict.get("cnr"):
                entries.append(
                    _normalize_entry(
                        row_dict,
                        default_court=court,
                        default_date=date_str,
                        default_bench=bench,
                        default_judge=judge,
                        default_source_url=source_url,
                    )
                )
    return entries


def _court_matches(entry_court: str, requested: str) -> bool:
    req = requested.strip().lower()
    ec = entry_court.strip().lower()
    if not req:
        return True
    if req == ec or req in ec or ec in req:
        return True
    canon_req = (canonicalize_court(requested) or "").lower()
    canon_entry = (canonicalize_court(entry_court) or "").lower()
    if canon_req and canon_entry and canon_req == canon_entry:
        return True
    if canon_req and (canon_req == ec or canon_req in ec):
        return True
    if canon_entry and (canon_entry == req or canon_entry in req):
        return True
    return False


class CauseListStore:
    """Reader and filter engine over local SQLite/JSONL cause lists and official feeds."""

    def __init__(
        self,
        *,
        db_path: Path | str | None = None,
        jsonl_paths: Sequence[Path | str] | None = None,
        data_dir: Path | str | None = None,
        entries: Sequence[CauseListEntry | Mapping[str, Any]] | None = None,
    ) -> None:
        self._db_paths: list[Path] = []
        self._jsonl_paths: list[Path] = []
        self._in_memory: list[CauseListEntry] = [
            _normalize_entry(e) for e in (entries or ())
        ]

        if db_path is not None:
            self._db_paths.append(Path(db_path))
        if jsonl_paths is not None:
            self._jsonl_paths.extend(Path(p) for p in jsonl_paths)
        if data_dir is not None:
            ddir = Path(data_dir)
            for cand in (ddir / "cause_lists.sqlite", ddir / "nyaya-core.sqlite"):
                if cand.is_file() and cand not in self._db_paths:
                    self._db_paths.append(cand)
            cl_jsonl = ddir / "cause_lists.jsonl"
            if cl_jsonl.is_file() and cl_jsonl not in self._jsonl_paths:
                self._jsonl_paths.append(cl_jsonl)
            cl_dir = ddir / "cause_lists"
            if cl_dir.is_dir():
                for p in sorted(cl_dir.glob("*.jsonl")):
                    if p not in self._jsonl_paths:
                        self._jsonl_paths.append(p)

    def _load_sqlite_entries(self, db_path: Path) -> list[CauseListEntry]:
        if not db_path.is_file():
            return []
        uri = f"{db_path.resolve().as_uri()}?mode=ro"
        con: sqlite3.Connection | None = None
        try:
            con = sqlite3.connect(uri, uri=True)
            con.row_factory = sqlite3.Row
            tbl = con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='cause_lists'"
            ).fetchone()
            if tbl is None:
                return []
            rows = con.execute("SELECT * FROM cause_lists").fetchall()
            out: list[CauseListEntry] = []
            for r in rows:
                keys = set(r.keys())
                adv_raw = r["advocates"] if "advocates" in keys else (r["advocates_json"] if "advocates_json" in keys else None)
                out.append(
                    _normalize_entry(
                        {
                            "court": r["court"] if "court" in keys else "",
                            "cause_list_date": r["cause_list_date"] if "cause_list_date" in keys else "",
                            "bench": r["bench"] if "bench" in keys else None,
                            "judge": r["judge"] if "judge" in keys else None,
                            "item_number": r["item_number"] if "item_number" in keys else None,
                            "case_number": r["case_number"] if "case_number" in keys else None,
                            "cnr": r["cnr"] if "cnr" in keys else None,
                            "parties": r["parties"] if "parties" in keys else None,
                            "advocates": adv_raw,
                            "purpose": r["purpose"] if "purpose" in keys else None,
                            "source_url": r["source_url"] if "source_url" in keys else "",
                        }
                    )
                )
            return out
        finally:
            if con is not None:
                con.close()

    def _load_jsonl_entries(self, jsonl_path: Path) -> list[CauseListEntry]:
        if not jsonl_path.is_file():
            return []
        out: list[CauseListEntry] = []
        for line in jsonl_path.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            obj = json.loads(stripped)
            if isinstance(obj, Mapping):
                out.append(_normalize_entry(obj))
        return out

    def _all_entries(self) -> list[CauseListEntry]:
        all_items: list[CauseListEntry] = list(self._in_memory)
        for dbp in self._db_paths:
            all_items.extend(self._load_sqlite_entries(dbp))
        for jp in self._jsonl_paths:
            all_items.extend(self._load_jsonl_entries(jp))
        return all_items

    def fetch_official_feed(
        self,
        url: str,
        *,
        court: str,
        cause_list_date: Date | str | None = None,
        bench: str | None = None,
        judge: str | None = None,
        transport: httpx.BaseTransport | None = None,
        offline_only: bool = False,
        timeout_s: float = 10.0,
    ) -> list[CauseListEntry]:
        """Fetch and parse an official sci.gov.in / High Court daily cause-list feed."""
        _validate_feed_url(url)
        if offline_only:
            raise StorageUnavailable(
                "Official cause-list feed fetch blocked in offline_only mode.",
                degraded_capabilities=["cause_lists"],
            )
        try:
            with httpx.Client(transport=transport, follow_redirects=True, timeout=timeout_s) as client:
                resp = client.get(url)
                resp.raise_for_status()
                body = resp.text
        except httpx.HTTPError as exc:
            raise StorageUnavailable(
                f"Failed to fetch official cause-list feed {url!r}: {exc}",
                degraded_capabilities=["cause_lists"],
            ) from exc

        parsed = parse_cause_list_feed(
            body,
            court=court,
            cause_list_date=cause_list_date,
            bench=bench,
            judge=judge,
            source_url=url,
        )
        self._in_memory.extend(parsed)
        return parsed

    def search_cause_lists(
        self,
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
        """Filter cause-list entries across local SQLite, JSONL, and in-memory feeds."""
        clamped_limit = max(1, min(int(limit), 100))
        target_date = date.isoformat() if isinstance(date, Date) else (str(date).strip()[:10] if date else None)

        results: list[CauseListEntry] = []
        for entry in self._all_entries():
            if court and not _court_matches(entry.court, court):
                continue
            if target_date and entry.cause_list_date != target_date:
                continue
            if bench and bench.strip().lower() not in (entry.bench or "").lower():
                continue
            if judge and judge.strip().lower() not in (entry.judge or "").lower():
                continue
            if party and party.strip().lower() not in (entry.parties or "").lower():
                continue
            if advocate:
                adv_q = advocate.strip().lower()
                if not any(adv_q in a.lower() for a in entry.advocates):
                    continue
            if case_number and case_number.strip().lower() not in (entry.case_number or "").lower():
                continue
            if cnr and cnr.strip().upper() != (entry.cnr or "").strip().upper():
                continue
            results.append(entry)
            if len(results) >= clamped_limit:
                break

        return results
