"""Lazy storage/retrieval/PDF/analytics backend registry (zero import-time I/O)."""

from __future__ import annotations

import json
import logging
import threading
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import duckdb

from ..config import Settings, get_settings
from ..exceptions import StorageUnavailable
from ..models import CorpusStatusResponse
from .duckdb_store import DuckDBStore, _sql_literal, build_s3_secret_sql
from .lance_store import LanceStore
from .manifest import CorpusManifest, load_manifest
from .pdf_fetcher import DirectS3PdfFetcher
from .protocols import AnalyticsEngine, CorpusBackend, PdfFetcher, RetrievalEngine
from .sqlite_store import SqliteStore

log = logging.getLogger("nyaya.storage")

_lock = threading.Lock()
_backend: CorpusBackend | None = None
_retrieval: RetrievalEngine | None = None
_pdf_fetcher: PdfFetcher | None = None
_analytics: AnalyticsEngine | None = None
_lance: LanceStore | None = None
_duckdb: DuckDBStore | None = None
_cold_settings: Settings | None = None


def build_lance_storage_options(settings: Settings) -> dict[str, str]:
    """Build LanceDB/object_store S3 storage_options compatible with Tigris/Railway Buckets."""
    if not settings.has_bucket_config:
        return {}
    storage_opts: dict[str, str] = {}
    if settings.bucket_endpoint:
        raw_ep = settings.bucket_endpoint.strip()
        if "://" not in raw_ep:
            raw_ep = f"https://{raw_ep}"
        parsed = urlsplit(raw_ep)
        netloc = parsed.netloc
        # LanceDB's Rust object_store does not prepend <bucket>. to a custom
        # aws_endpoint under virtual-hosted-style mode, so normalize it here.
        if (
            settings.bucket_url_style == "vhost"
            and settings.bucket_name
            and netloc
            and not netloc.lower().startswith(f"{settings.bucket_name.lower()}.")
        ):
            netloc = f"{settings.bucket_name}.{netloc}"
        endpoint = urlunsplit((parsed.scheme, netloc, parsed.path.rstrip("/"), "", ""))
        storage_opts["aws_endpoint"] = endpoint
        if parsed.scheme.lower() == "http":
            storage_opts["aws_allow_http"] = "true"
    if settings.bucket_access_key_id:
        storage_opts["aws_access_key_id"] = settings.bucket_access_key_id
    if settings.bucket_secret_access_key:
        storage_opts["aws_secret_access_key"] = settings.bucket_secret_access_key
    storage_opts["aws_region"] = settings.bucket_region
    storage_opts["aws_virtual_hosted_style_request"] = (
        "true" if settings.bucket_url_style == "vhost" else "false"
    )
    return storage_opts


def _build_lance(settings: Settings) -> LanceStore:
    local_dir = settings.data_dir / "lancedb"
    bucket_uri = f"s3://{settings.bucket_name}/lancedb" if settings.has_bucket_config else None
    storage_opts = build_lance_storage_options(settings)
    if settings.hybrid_search:
        from ..retrieval.hybrid import (
            EmbeddingProvider,
            HashedNgramEmbeddingProvider,
            OpenAICompatibleEmbeddingProvider,
        )

        embedder: EmbeddingProvider | None = None
        if settings.embedding_endpoint:
            embedder = OpenAICompatibleEmbeddingProvider(
                endpoint=settings.embedding_endpoint,
                model=settings.embedding_model or "BAAI/bge-m3",
            )
        else:
            embedder = HashedNgramEmbeddingProvider()
    else:
        embedder = None
    return LanceStore(
        local_dir=local_dir,
        bucket_uri=bucket_uri,
        storage_options=storage_opts,
        offline_only=settings.offline_only,
        default_commercial_safe_only=settings.commercial_safe_only,
        embedding_provider=embedder,
    )


def fetch_bucket_manifest(settings: Settings) -> CorpusManifest:
    """Fetch ``s3://{bucket}/current/manifest.json`` via DuckDB ``httpfs`` and cache locally."""
    if not settings.has_bucket_config or not settings.bucket_name:
        raise StorageUnavailable(
            "Bucket configuration is incomplete.",
            degraded_capabilities=["parquet_cold"],
        )
    secret_sql = build_s3_secret_sql(settings)
    if not secret_sql:
        raise StorageUnavailable(
            "Failed to build S3 secret for manifest fetch.",
            degraded_capabilities=["parquet_cold"],
        )

    s3_uri = f"s3://{settings.bucket_name}/current/manifest.json"
    con = duckdb.connect(":memory:")
    try:
        if settings.duckdb_ext_dir is not None:
            con.execute(f"SET extension_directory={_sql_literal(str(settings.duckdb_ext_dir))}")
        con.execute("SET autoinstall_known_extensions=false")
        con.execute("SET autoload_known_extensions=false")
        con.execute("LOAD httpfs")
        con.execute("SET http_timeout=4000")
        con.execute("SET http_retries=2")
        con.execute(secret_sql)
        row = con.execute("SELECT content FROM read_text(?)", [s3_uri]).fetchone()
    finally:
        con.close()

    if not row or not row[0]:
        raise StorageUnavailable(
            f"Empty manifest returned from {s3_uri}",
            degraded_capabilities=["parquet_cold"],
        )
    raw_text = str(row[0])
    manifest = CorpusManifest.model_validate(json.loads(raw_text))

    try:
        settings.cache_dir.mkdir(parents=True, exist_ok=True)
        cache_path = settings.cache_dir / "manifest.json"
        tmp_path = settings.cache_dir / "manifest.json.tmp"
        tmp_path.write_text(raw_text, encoding="utf-8")
        tmp_path.replace(cache_path)
    except Exception:
        log.warning("Failed to cache bucket manifest to %s", settings.cache_dir, exc_info=True)

    return manifest


def _load_optional_manifest(settings: Settings) -> CorpusManifest | None:
    for cand in (settings.data_dir / "manifest.json", settings.data_dir / "parquet" / "manifest.json"):
        if cand.is_file():
            try:
                return load_manifest(cand, offline_only=settings.offline_only)
            except Exception:
                log.warning("Failed to load local manifest at %s", cand, exc_info=True)
    if settings.has_bucket_config and not settings.offline_only:
        try:
            return fetch_bucket_manifest(settings)
        except Exception:
            log.warning(
                "Failed to fetch remote manifest from s3://%s/current/manifest.json",
                settings.bucket_name,
                exc_info=True,
            )
    if settings.manifest_url and not settings.offline_only:
        try:
            return load_manifest(settings.manifest_url, offline_only=False)
        except Exception:
            log.warning("Failed to fetch remote manifest from %s", settings.manifest_url, exc_info=True)
    cached = settings.cache_dir / "manifest.json"
    if cached.is_file():
        try:
            return load_manifest(cached, offline_only=settings.offline_only)
        except Exception:
            log.warning("Failed to load cached manifest at %s", cached, exc_info=True)
    return None


def _sync_cold_settings_unlocked(settings: Settings) -> None:
    global _lance, _duckdb, _cold_settings
    if _cold_settings != settings:
        if _duckdb is not None:
            try:
                _duckdb.close()
            except Exception:
                pass
            _duckdb = None
        if _lance is not None:
            try:
                _lance.close()
            except Exception:
                pass
            _lance = None
        _cold_settings = settings


def _get_duckdb_unlocked(settings: Settings) -> DuckDBStore:
    global _duckdb
    _sync_cold_settings_unlocked(settings)
    if _duckdb is None:
        manifest = _load_optional_manifest(settings)
        _duckdb = DuckDBStore(
            manifest=manifest,
            settings=settings,
            offline_only=settings.offline_only,
            default_commercial_safe_only=settings.commercial_safe_only,
        )
    return _duckdb


def _get_lance_unlocked(settings: Settings) -> LanceStore:
    global _lance
    _sync_cold_settings_unlocked(settings)
    if _lance is None:
        _lance = _build_lance(settings)
    return _lance


def _snapshot_cold_tiers() -> tuple[Any, Any]:
    settings = get_settings()
    with _lock:
        lance = getattr(_retrieval, "lance", None) if _retrieval is not None else None
        if lance is None:
            lance = _get_lance_unlocked(settings)
        duck = _analytics if _analytics is not None else _get_duckdb_unlocked(settings)
    return lance, duck


def get_cold_tier_statuses() -> dict[str, str]:
    """Safely probe cold-tier health statuses (``lance_fts``, ``duckdb_parquet``) without raising."""
    try:
        lance, duck = _snapshot_cold_tiers()
    except Exception:
        return {"lance_fts": "degraded", "duckdb_parquet": "degraded"}

    try:
        lance_health = lance.health() if hasattr(lance, "health") else {"status": "ok"}
        lance_status = str(lance_health.get("status", "degraded"))
    except Exception:
        lance_status = "degraded"

    try:
        duck_health = duck.health() if hasattr(duck, "health") else {"status": "ok"}
        duck_status = str(duck_health.get("status", "degraded"))
    except Exception:
        duck_status = "degraded"

    return {
        "lance_fts": lance_status,
        "duckdb_parquet": duck_status,
    }


def enrich_corpus_status(
    resp: CorpusStatusResponse,
    *,
    commercial_safe_only: bool | None = None,
) -> CorpusStatusResponse:
    """Attach live 3-tier health statuses and roll up bucket manifest counts onto ``resp``."""
    try:
        settings = get_settings()
        cs_only = (
            settings.commercial_safe_only
            if commercial_safe_only is None
            else bool(commercial_safe_only)
        )
    except Exception:
        cs_only = bool(commercial_safe_only)

    try:
        lance, duck = _snapshot_cold_tiers()
    except Exception:
        lance, duck = None, None

    lance_status = "degraded" if lance is None else "ok"
    if lance is not None:
        try:
            lance_health = lance.health() if hasattr(lance, "health") else {"status": "ok"}
            lance_status = str(lance_health.get("status", "degraded"))
        except Exception:
            lance_status = "degraded"

    duck_status = "degraded" if duck is None else "ok"
    if duck is not None:
        try:
            duck_health = duck.health() if hasattr(duck, "health") else {"status": "ok"}
            duck_status = str(duck_health.get("status", "degraded"))
        except Exception:
            duck_status = "degraded"

        try:
            if hasattr(duck, "manifest_counts"):
                extra_counts = duck.manifest_counts(commercial_safe_only=cs_only)
                for field_name, remote_val in extra_counts.items():
                    current_val = int(getattr(resp.counts, field_name, 0))
                    if remote_val > current_val:
                        setattr(resp.counts, field_name, remote_val)
        except Exception:
            log.warning("Failed to roll up DuckDB manifest counts", exc_info=True)

    tiers = dict(resp.tiers)
    tiers.setdefault("sqlite_core", "ok")
    tiers["lance_fts"] = lance_status
    tiers["duckdb_parquet"] = duck_status
    resp.tiers = tiers
    return resp


def get_backend() -> CorpusBackend:
    global _backend
    with _lock:
        if _backend is None:
            settings = get_settings()
            _backend = SqliteStore(
                settings.data_dir / "nyaya-core.sqlite",
                default_commercial_safe_only=settings.commercial_safe_only,
            )
        return _backend


def set_backend(backend: CorpusBackend | None) -> None:
    global _backend, _retrieval
    with _lock:
        if _backend is not None and _backend is not backend:
            try:
                _backend.close()
            except Exception:
                pass
        _backend = backend
        _retrieval = None


def get_retrieval() -> RetrievalEngine:
    global _retrieval, _backend
    with _lock:
        if _retrieval is None:
            from ..retrieval import build_retrieval_engine

            settings = get_settings()
            backend = _backend
            if backend is None:
                backend = SqliteStore(
                    settings.data_dir / "nyaya-core.sqlite",
                    default_commercial_safe_only=settings.commercial_safe_only,
                )
                _backend = backend
            _retrieval = build_retrieval_engine(
                settings,
                backend,
                lance=_get_lance_unlocked(settings),
                duckdb=_get_duckdb_unlocked(settings),
            )
        return _retrieval


def set_retrieval(engine: RetrievalEngine | None) -> None:
    global _retrieval
    with _lock:
        _retrieval = engine


def get_pdf_fetcher() -> PdfFetcher:
    global _pdf_fetcher
    with _lock:
        if _pdf_fetcher is None:
            settings = get_settings()
            _pdf_fetcher = DirectS3PdfFetcher(
                cache_dir=settings.cache_dir,
                offline_only=settings.offline_only,
                enable_ocr=settings.enable_ocr,
            )
        return _pdf_fetcher


def set_pdf_fetcher(fetcher: PdfFetcher | None) -> None:
    global _pdf_fetcher
    with _lock:
        _pdf_fetcher = fetcher


def get_analytics() -> AnalyticsEngine:
    global _analytics
    with _lock:
        if _analytics is None:
            settings = get_settings()
            _analytics = _get_duckdb_unlocked(settings)
        return _analytics


def set_analytics(analytics: AnalyticsEngine | None) -> None:
    global _analytics
    with _lock:
        _analytics = analytics


def close_all() -> None:
    global _backend, _retrieval, _pdf_fetcher, _analytics, _lance, _duckdb, _cold_settings
    with _lock:
        for obj in (_backend, _lance, _duckdb, _analytics):
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
        _backend = None
        _retrieval = None
        _analytics = None
        _lance = None
        _duckdb = None
        _cold_settings = None


async def aclose_all() -> None:
    global _pdf_fetcher
    fetcher: Any
    with _lock:
        fetcher = _pdf_fetcher
        _pdf_fetcher = None
    if fetcher is not None:
        try:
            await fetcher.aclose()
        except Exception:
            pass
    close_all()
