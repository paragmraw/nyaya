"""DuckDB + httpfs compacted ZSTD Parquet reader and AnalyticsEngine."""

from __future__ import annotations

import threading
from datetime import date as Date
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import duckdb

from ..citations import court_code_variants
from ..config import Settings
from ..exceptions import (
    InvalidFilter,
    QueryTimeout,
    QueryTooBroad,
    StorageUnavailable,
)
from ..models import (
    AnalyticsBucket,
    AnalyticsResult,
    AnalyticsTier,
    JudgmentCard,
    SourceProvenance,
)
from .manifest import CorpusManifest, resolve_partitions

_ANALYTICS_DATASET_MAP: dict[str, str] = {
    "sc": "supreme_court",
    "supreme_court": "supreme_court",
    "hc": "high_court",
    "high_court": "high_court",
    "district": "district_court",
    "district_court": "district_court",
    "tribunal": "tribunal",
    "regulator": "regulator",
}

_ALLOWED_GROUP_BY: dict[str, str] = {
    "court": "court_name",
    "court_tier": "court_code",
    "bench": "bench",
    "year": "CAST(year AS VARCHAR)",
    "disposal_nature": "disposal_nature",
    "act": "act",
    "case_type": "case_type",
    "judge": "judge",
    "state": "state_code",
    "district": "district",
    "regulator": "regulator_code",
}


def normalize_duckdb_endpoint(endpoint: str) -> tuple[str, bool]:
    """Strip scheme from S3 endpoint for DuckDB CREATE SECRET and infer USE_SSL."""
    raw = endpoint.strip()
    parsed = urlsplit(raw)
    if parsed.scheme:
        host = (parsed.netloc or parsed.path).rstrip("/")
        use_ssl = parsed.scheme.lower() == "https"
        return host, use_ssl
    return raw.rstrip("/"), True


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build_s3_secret_sql(settings: Settings) -> str | None:
    """Build scoped DuckDB S3 secret DDL for Tigris/Railway or MinIO buckets."""
    if not settings.has_bucket_config:
        return None
    assert settings.bucket_endpoint is not None
    assert settings.bucket_name is not None
    endpoint, use_ssl = normalize_duckdb_endpoint(settings.bucket_endpoint)
    key_id = settings.bucket_access_key_id or ""
    secret = settings.bucket_secret_access_key or ""
    region = settings.bucket_region or "auto"
    url_style = settings.bucket_url_style or "vhost"
    scope = f"s3://{settings.bucket_name}"
    return (
        "CREATE OR REPLACE SECRET nyaya_bucket ("
        "TYPE s3, PROVIDER config, "
        f"KEY_ID {_sql_literal(key_id)}, "
        f"SECRET {_sql_literal(secret)}, "
        f"REGION {_sql_literal(region)}, "
        f"ENDPOINT {_sql_literal(endpoint)}, "
        f"URL_STYLE {_sql_literal(url_style)}, "
        f"USE_SSL {'true' if use_ssl else 'false'}, "
        f"SCOPE {_sql_literal(scope)}"
        ")"
    )


class DuckDBStore:
    """Bounded-memory, timeout-guarded DuckDB Parquet query & analytics engine."""

    def __init__(
        self,
        *,
        manifest: CorpusManifest | None = None,
        settings: Settings | None = None,
        base_dir: Path | None = None,
        temp_dir: Path | None = None,
        offline_only: bool = False,
        default_commercial_safe_only: bool = False,
        query_timeout_s: float = 5.0,
    ) -> None:
        self.manifest = manifest
        self.settings = settings
        self.base_dir = base_dir or (settings.data_dir if settings else None)
        self.temp_dir = temp_dir or (
            (settings.cache_dir / "duckdb_tmp") if settings else Path.home() / ".cache" / "nyaya" / "duckdb_tmp"
        )
        self.offline_only = settings.offline_only if settings else offline_only
        self.default_commercial_safe_only = (
            settings.commercial_safe_only if settings else default_commercial_safe_only
        )
        self.query_timeout_s = query_timeout_s
        self._lock = threading.Lock()
        self._con: duckdb.DuckDBPyConnection | None = None
        self._secret_configured = False

    def _effective_cs(self, commercial_safe_only: bool | None) -> bool:
        return (
            self.default_commercial_safe_only
            if commercial_safe_only is None
            else bool(commercial_safe_only)
        )

    def _connect(self, *, require_httpfs: bool = False) -> duckdb.DuckDBPyConnection:
        if self._con is not None:
            if require_httpfs:
                self._ensure_httpfs(self._con)
            return self._con

        self.temp_dir.mkdir(parents=True, exist_ok=True)
        con = duckdb.connect(":memory:")
        try:
            ext_dir = self.settings.duckdb_ext_dir if self.settings else None
            if ext_dir is not None:
                con.execute(f"SET extension_directory={_sql_literal(str(ext_dir))}")
            con.execute("SET autoinstall_known_extensions=false")
            con.execute("SET autoload_known_extensions=false")
            con.execute("SET memory_limit='256MB'")
            con.execute("SET threads=2")
            con.execute(f"SET temp_directory={_sql_literal(str(self.temp_dir))}")
            con.execute("SET max_temp_directory_size='192MB'")
            con.execute("SET preserve_insertion_order=false")
            if ext_dir is not None or require_httpfs:
                self._ensure_httpfs(con)
        except duckdb.Error as exc:
            con.close()
            raise StorageUnavailable(
                f"Failed to initialize DuckDB runtime: {exc}",
                degraded_capabilities=["parquet_cold"],
            ) from exc
        self._con = con
        return con

    def _ensure_httpfs(self, con: duckdb.DuckDBPyConnection) -> None:
        con.execute("LOAD httpfs")
        con.execute("SET http_timeout=4000")
        con.execute("SET http_retries=2")
        if self.settings and not self._secret_configured and not self.offline_only:
            secret_sql = build_s3_secret_sql(self.settings)
            if secret_sql:
                con.execute(secret_sql)
                self._secret_configured = True

    def _execute_with_timeout(
        self,
        sql: str,
        params: list[Any] | None = None,
        *,
        require_httpfs: bool = False,
    ) -> tuple[list[str], list[tuple[Any, ...]]]:
        with self._lock:
            con = self._connect(require_httpfs=require_httpfs)
            timer = threading.Timer(self.query_timeout_s, con.interrupt)
            timer.daemon = True
            timer.start()
            try:
                cur = con.execute(sql, params or [])
                cols = [d[0] for d in cur.description] if cur.description else []
                rows = cur.fetchall()
                return cols, rows
            except duckdb.InterruptException as exc:
                raise QueryTimeout(
                    f"Parquet query exceeded {self.query_timeout_s:.1f}s timeout.",
                    hint="Narrow court, state, year_from/year_to, or judge filters.",
                ) from exc
            except duckdb.Error as exc:
                raise StorageUnavailable(
                    f"DuckDB query failed: {exc}",
                    degraded_capabilities=["parquet_cold"],
                ) from exc
            finally:
                timer.cancel()

    def _validate_breadth(
        self,
        dataset: str,
        *,
        cnr: str | None = None,
        court: str | list[str] | None = None,
        state: str | None = None,
        judge: str | None = None,
        act: str | None = None,
        party: str | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
    ) -> None:
        narrow_year = (
            year_from is not None
            and year_to is not None
            and (year_to - year_from) <= 5
        )
        if dataset == "high_court":
            if not any([cnr, court, judge, act, party, narrow_year]):
                raise QueryTooBroad(
                    "High Court 17.77M-record scan requires at least one narrowing filter "
                    "(court, cnr, judge, act, party, or year window <= 5 years).",
                    hint="Pass court='DHC' or year_from/year_to within 5 years.",
                )
        elif dataset == "district_court":
            if not any([cnr, state, court, narrow_year]):
                raise QueryTooBroad(
                    "District Court 81M-record scan requires state or a <=5-year window.",
                    hint="Pass state='MH' or year_from/year_to within 5 years.",
                )

    def search_judgments(
        self,
        *,
        tier: str = "high_court",
        query: str | None = None,
        cnr: str | None = None,
        court: str | list[str] | None = None,
        state: str | None = None,
        bench: str | None = None,
        judge: str | None = None,
        party: str | None = None,
        act: str | None = None,
        disposal_nature: str | None = None,
        case_type: str | None = None,
        year_from: int | None = None,
        year_to: int | None = None,
        limit: int = 25,
        offset: int = 0,
        commercial_safe_only: bool | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        if self.manifest is None:
            return [], 0
        dataset = _ANALYTICS_DATASET_MAP.get(tier, tier)
        cs_only = self._effective_cs(commercial_safe_only)

        # Check license BEFORE breadth check so NC block is unambiguous
        if dataset == "district_court":
            resolve_partitions(
                self.manifest,
                dataset,
                state=state,
                year_from=year_from,
                year_to=year_to,
                commercial_safe_only=cs_only,
                base_dir=self.base_dir,
                s3_bucket=self.settings.bucket_name if self.settings else None,
                offline_only=self.offline_only,
            )

        self._validate_breadth(
            dataset,
            cnr=cnr,
            court=court,
            state=state,
            judge=judge,
            act=act,
            party=party,
            year_from=year_from,
            year_to=year_to,
        )

        partitions = resolve_partitions(
            self.manifest,
            dataset,
            court=court,
            state=state,
            year_from=year_from,
            year_to=year_to,
            commercial_safe_only=cs_only,
            base_dir=self.base_dir,
            s3_bucket=self.settings.bucket_name if self.settings else None,
            offline_only=self.offline_only,
        )[:12]
        if not partitions:
            return [], 0

        require_httpfs = any(p.startswith(("s3://", "http://", "https://")) for p in partitions)
        pq_list = ", ".join(_sql_literal(p) for p in partitions)
        where = ["1=1"]
        params: list[Any] = []
        if cnr:
            where.append("upper(cnr) = upper(?)")
            params.append(cnr.strip())
        if court:
            c_list = [court] if isinstance(court, str) else list(court)
            c_sub: list[str] = []
            for c in c_list:
                for variant in court_code_variants(c):
                    c_sub.append("(lower(court_code) = lower(?) OR lower(court_name) LIKE ?)")
                    params.extend([variant, f"%{variant.lower()}%"])
            where.append("(" + " OR ".join(c_sub) + ")")
        if bench:
            where.append("lower(coalesce(bench, '')) LIKE ?")
            params.append(f"%{bench.strip().lower()}%")
        if judge:
            where.append("lower(coalesce(judge, '')) LIKE ?")
            params.append(f"%{judge.strip().lower()}%")
        if party:
            plike = f"%{party.strip().lower()}%"
            where.append("(lower(coalesce(title, '')) LIKE ? OR lower(coalesce(petitioner, '')) LIKE ? OR lower(coalesce(respondent, '')) LIKE ?)")
            params.extend([plike, plike, plike])
        if act:
            where.append("lower(coalesce(act, '')) LIKE ?")
            params.append(f"%{act.strip().lower()}%")
        if disposal_nature:
            where.append("lower(coalesce(disposal_nature, '')) LIKE ?")
            params.append(f"%{disposal_nature.strip().lower()}%")
        if case_type:
            where.append("lower(coalesce(case_type, '')) LIKE ?")
            params.append(f"%{case_type.strip().lower()}%")
        if year_from is not None:
            where.append("year >= ?")
            params.append(int(year_from))
        if year_to is not None:
            where.append("year <= ?")
            params.append(int(year_to))
        if query:
            qlike = f"%{query.strip().lower()}%"
            where.append("lower(coalesce(title, '')) LIKE ?")
            params.append(qlike)

        where_sql = " AND ".join(where)
        max_lim = 50 if dataset == "district_court" else 100
        limit = max(1, min(int(limit), max_lim))
        offset = max(0, int(offset))

        _, cnt_rows = self._execute_with_timeout(
            f"SELECT count(*) FROM read_parquet([{pq_list}], union_by_name=true) WHERE {where_sql}",
            params,
            require_httpfs=require_httpfs,
        )
        total = int(cnt_rows[0][0]) if cnt_rows else 0
        cols, rows = self._execute_with_timeout(
            f"SELECT * FROM read_parquet([{pq_list}], union_by_name=true) "
            f"WHERE {where_sql} ORDER BY year DESC, decision_date DESC LIMIT ? OFFSET ?",
            [*params, limit, offset],
            require_httpfs=require_httpfs,
        )
        return [dict(zip(cols, r)) for r in rows], total

    def aggregate(
        self,
        dataset: AnalyticsTier,
        group_by: str,
        *,
        filters: dict[str, Any],
        top_n: int = 25,
        sample_cases: int = 0,
        commercial_safe_only: bool | None = None,
    ) -> AnalyticsResult:
        ds_name = _ANALYTICS_DATASET_MAP.get(str(dataset))
        if ds_name is None:
            raise InvalidFilter(
                f"Unknown analytics dataset {dataset!r}",
                kind="court_tier",
                allowed_values=sorted(set(_ANALYTICS_DATASET_MAP.values())),
            )
        col_expr = _ALLOWED_GROUP_BY.get(group_by)
        if col_expr is None:
            raise InvalidFilter(
                f"Invalid group_by {group_by!r}",
                kind="group_by",
                allowed_values=sorted(_ALLOWED_GROUP_BY.keys()),
            )
        if self.manifest is None:
            return AnalyticsResult(dataset=ds_name, group_by=group_by, total_matched=0)

        cs_only = self._effective_cs(commercial_safe_only)
        court = filters.get("court")
        state = filters.get("state")
        year_from = filters.get("year_from")
        year_to = filters.get("year_to")
        judge = filters.get("judge")
        act = filters.get("act")

        # First check commercial_safe_only via resolve_partitions
        partitions = resolve_partitions(
            self.manifest,
            ds_name,
            court=court,
            state=state,
            year_from=year_from,
            year_to=year_to,
            commercial_safe_only=cs_only,
            base_dir=self.base_dir,
            s3_bucket=self.settings.bucket_name if self.settings else None,
            offline_only=self.offline_only,
        )[:25]

        self._validate_breadth(
            ds_name,
            court=court,
            state=state,
            judge=judge,
            act=act,
            year_from=year_from,
            year_to=year_to,
        )
        if not partitions:
            return AnalyticsResult(dataset=ds_name, group_by=group_by, total_matched=0)

        require_httpfs = any(p.startswith(("s3://", "http://", "https://")) for p in partitions)
        pq_list = ", ".join(_sql_literal(p) for p in partitions)
        where = ["1=1"]
        params: list[Any] = []
        if year_from is not None:
            where.append("year >= ?")
            params.append(int(year_from))
        if year_to is not None:
            where.append("year <= ?")
            params.append(int(year_to))
        if judge:
            where.append("lower(coalesce(judge, '')) LIKE ?")
            params.append(f"%{str(judge).strip().lower()}%")
        if act:
            where.append("lower(coalesce(act, '')) LIKE ?")
            params.append(f"%{str(act).strip().lower()}%")
        if filters.get("disposal_nature"):
            where.append("lower(coalesce(disposal_nature, '')) LIKE ?")
            params.append(f"%{str(filters['disposal_nature']).strip().lower()}%")
        if filters.get("district"):
            where.append("lower(coalesce(district, '')) LIKE ?")
            params.append(f"%{str(filters['district']).strip().lower()}%")

        where_sql = " AND ".join(where)
        top_n = max(1, min(int(top_n), 100))

        _, total_rows = self._execute_with_timeout(
            f"SELECT count(*) FROM read_parquet([{pq_list}], union_by_name=true) WHERE {where_sql}",
            params,
            require_httpfs=require_httpfs,
        )
        total_matched = int(total_rows[0][0]) if total_rows else 0

        _, agg_rows = self._execute_with_timeout(
            f"SELECT coalesce(cast({col_expr} AS VARCHAR), 'Unknown') AS grp_key, count(*) AS cnt "
            f"FROM read_parquet([{pq_list}], union_by_name=true) "
            f"WHERE {where_sql} GROUP BY 1 ORDER BY cnt DESC LIMIT ?",
            [*params, top_n],
            require_httpfs=require_httpfs,
        )
        buckets = [
            AnalyticsBucket(
                key=str(k),
                count=int(c),
                share_pct=round((int(c) / total_matched * 100.0), 2) if total_matched else 0.0,
                samples=[],
            )
            for k, c in agg_rows
        ]
        if sample_cases > 0 and buckets:
            sample_rows, _ = self.search_judgments(
                tier=ds_name,
                court=court,
                state=state,
                year_from=year_from,
                year_to=year_to,
                limit=min(5, sample_cases),
                commercial_safe_only=cs_only,
            )
            ds_meta = self.manifest.datasets[ds_name]
            prov = SourceProvenance(
                source_id=f"parquet-{ds_name}",
                source_name=f"Compacted Parquet ({ds_name})",
                source_url="s3://nyaya-bucket/parquet",
                license_spdx=ds_meta.license_spdx,
                commercial_safe=ds_meta.commercial_safe,
                terms_status=ds_meta.terms_status,
                as_of=Date(2026, 11, 22),
            )
            cards = [
                JudgmentCard(
                    case_id=str(sr.get("case_id", "")),
                    cnr=sr.get("cnr"),
                    neutral_citation=sr.get("neutral_citation"),
                    title=str(sr.get("title", "Untitled")),
                    court=str(sr.get("court_name", "")),
                    court_code=sr.get("court_code"),
                    court_tier=ds_name if ds_name in ("supreme_court", "high_court", "tribunal", "district_court") else "tribunal",  # type: ignore[arg-type]
                    provenance=prov,
                )
                for sr in sample_rows[:sample_cases]
            ]
            buckets[0].samples = cards

        ds_meta = self.manifest.datasets[ds_name]
        return AnalyticsResult(
            dataset=ds_name,
            group_by=group_by,
            total_matched=total_matched,
            buckets=buckets,
            completeness_caveat=(
                "eCourts disposal labels vary across High Courts; pre-2015 act tags may be incomplete; "
                "DDL District dataset covers 2010-2018 only (CC-BY-NC-SA-4.0)."
            ),
            provenances=[
                SourceProvenance(
                    source_id=f"parquet-{ds_name}",
                    source_name=f"Compacted Parquet ({ds_name})",
                    source_url="s3://nyaya-bucket/parquet",
                    license_spdx=ds_meta.license_spdx,
                    commercial_safe=ds_meta.commercial_safe,
                    terms_status=ds_meta.terms_status,
                    as_of=Date(2026, 11, 22),
                )
            ],
        )

    def close(self) -> None:
        with self._lock:
            if self._con is not None:
                try:
                    self._con.close()
                except Exception:
                    pass
                self._con = None
                self._secret_configured = False
