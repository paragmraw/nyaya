"""LanceDB native Rust BM25 FTS reader (zero Tantivy, zero dense vectors in Phase 1)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import lancedb

from ..exceptions import StorageUnavailable

SC_CHUNKS_TABLE = "sc_chunks"
HC_HOT_CHUNKS_TABLE = "hc_hot_chunks"
STATE_ACTS_REGULATORS_TABLE = "state_acts_regulators"


def _sql_str(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


class LanceStore:
    """Lazy-connecting LanceDB native BM25 FTS reader for Bucket or local packs."""

    def __init__(
        self,
        *,
        local_dir: Path | str | None = None,
        bucket_uri: str | None = None,
        storage_options: dict[str, str] | None = None,
        offline_only: bool = False,
        default_commercial_safe_only: bool = False,
    ) -> None:
        self.local_dir = Path(local_dir) if local_dir else None
        self.bucket_uri = bucket_uri
        self.storage_options = dict(storage_options or {})
        self.offline_only = offline_only
        self.default_commercial_safe_only = default_commercial_safe_only
        self._db: Any = None
        self._tables: dict[str, Any] = {}

    def _effective_cs(self, commercial_safe_only: bool | None) -> bool:
        return (
            self.default_commercial_safe_only
            if commercial_safe_only is None
            else bool(commercial_safe_only)
        )

    def _connect(self) -> Any:
        if self._db is not None:
            return self._db
        if self.local_dir is not None and self.local_dir.is_dir():
            self._db = lancedb.connect(str(self.local_dir))
            return self._db
        if self.bucket_uri and not self.offline_only:
            try:
                self._db = lancedb.connect(self.bucket_uri, storage_options=self.storage_options)
                return self._db
            except Exception as exc:
                raise StorageUnavailable(
                    f"Failed to connect to LanceDB bucket {self.bucket_uri}: {exc}",
                    degraded_capabilities=["lance_fts"],
                ) from exc
        raise StorageUnavailable(
            "LanceDB store is unavailable locally and no remote bucket is configured.",
            degraded_capabilities=["lance_fts"],
        )

    def _open_table(self, name: str) -> Any:
        if name in self._tables:
            return self._tables[name]
        db = self._connect()
        try:
            tbl = db.open_table(name)
        except Exception as exc:
            raise StorageUnavailable(
                f"LanceDB table {name!r} could not be opened: {exc}",
                degraded_capabilities=["lance_fts"],
            ) from exc
        self._tables[name] = tbl
        return tbl

    def is_available(self) -> bool:
        if self.local_dir is not None and self.local_dir.is_dir():
            return True
        return bool(self.bucket_uri and not self.offline_only)

    def health(self) -> dict[str, Any]:
        if not self.is_available():
            return {"status": "unavailable"}
        try:
            db = self._connect()
            names = list(db.table_names())
            return {"status": "ok", "tables": names}
        except Exception as exc:
            return {"status": "degraded", "error": str(exc)}

    def _fts_query(
        self,
        table_name: str,
        query: str,
        *,
        filters: list[str],
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        if not query or not query.strip():
            return []
        tbl = self._open_table(table_name)
        try:
            search = tbl.search(query.strip(), query_type="fts")
            if filters:
                search = search.where(" AND ".join(filters))
            rows: list[dict[str, Any]] = search.limit(max(1, int(limit))).to_list()
            return rows
        except StorageUnavailable:
            raise
        except Exception as exc:
            raise StorageUnavailable(
                f"LanceDB FTS query failed on {table_name}: {exc}",
                degraded_capabilities=["lance_fts"],
            ) from exc

    def search_sc(
        self,
        query: str,
        *,
        year_from: int | None = None,
        year_to: int | None = None,
        author_judge: str | None = None,
        min_bench_strength: int | None = None,
        commercial_safe_only: bool | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        if self._effective_cs(commercial_safe_only):
            clauses.append("commercial_safe = true")
        if year_from is not None:
            clauses.append(f"year >= {int(year_from)}")
        if year_to is not None:
            clauses.append(f"year <= {int(year_to)}")
        if author_judge:
            clauses.append(f"author_judge = {_sql_str(author_judge.strip())}")
        if min_bench_strength is not None:
            clauses.append(f"bench_strength >= {int(min_bench_strength)}")
        return self._fts_query(SC_CHUNKS_TABLE, query, filters=clauses, limit=limit)

    def search_hc(
        self,
        query: str,
        *,
        court_code: str | None = None,
        bench: str | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        min_bench_strength: int | None = None,
        disposal_nature: str | None = None,
        commercial_safe_only: bool | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        if self._effective_cs(commercial_safe_only):
            clauses.append("commercial_safe = true")
        if court_code:
            clauses.append(f"court_code = {_sql_str(court_code.strip())}")
        if bench:
            clauses.append(f"bench = {_sql_str(bench.strip())}")
        if year_from is not None:
            clauses.append(f"year >= {int(year_from)}")
        if year_to is not None:
            clauses.append(f"year <= {int(year_to)}")
        if min_bench_strength is not None:
            clauses.append(f"bench_strength >= {int(min_bench_strength)}")
        if disposal_nature:
            clauses.append(f"disposal_nature = {_sql_str(disposal_nature.strip())}")
        return self._fts_query(HC_HOT_CHUNKS_TABLE, query, filters=clauses, limit=limit)

    def search_state_regulators(
        self,
        query: str,
        *,
        act: str | None = None,
        jurisdiction: str | None = None,
        category: str | None = None,
        status: str | None = None,
        commercial_safe_only: bool | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        if self._effective_cs(commercial_safe_only):
            clauses.append("commercial_safe = true")
        if act:
            clauses.append(f"short_name = {_sql_str(act.strip())}")
        if jurisdiction:
            clauses.append(f"jurisdiction = {_sql_str(jurisdiction.strip())}")
        if category:
            clauses.append(f"category = {_sql_str(category.strip())}")
        if status and status != "all":
            clauses.append(f"status = {_sql_str(status.strip())}")
        return self._fts_query(STATE_ACTS_REGULATORS_TABLE, query, filters=clauses, limit=limit)

    def get_judgment_chunks(
        self,
        case_id: str,
        *,
        commercial_safe_only: bool | None = None,
    ) -> list[dict[str, Any]]:
        cs = self._effective_cs(commercial_safe_only)
        clauses = [f"case_id = {_sql_str(case_id)}"]
        if cs:
            clauses.append("commercial_safe = true")
        where_sql = " AND ".join(clauses)
        for table_name in (SC_CHUNKS_TABLE, HC_HOT_CHUNKS_TABLE):
            try:
                tbl = self._open_table(table_name)
                rows: list[dict[str, Any]] = tbl.search().where(where_sql).limit(500).to_list()
                if rows:
                    rows.sort(key=lambda r: int(r.get("para_start") or 0))
                    return rows
            except StorageUnavailable:
                continue
        return []

    def close(self) -> None:
        self._tables.clear()
        self._db = None
