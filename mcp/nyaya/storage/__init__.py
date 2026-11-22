"""Lazy storage/retrieval/PDF/analytics backend registry (zero import-time I/O)."""

from __future__ import annotations

import logging
import threading
from typing import Any

from ..config import Settings, get_settings
from .duckdb_store import DuckDBStore
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


def _build_lance(settings: Settings) -> LanceStore:
    local_dir = settings.data_dir / "lancedb"
    bucket_uri = f"s3://{settings.bucket_name}/lancedb" if settings.has_bucket_config else None
    storage_opts: dict[str, str] = {}
    if settings.has_bucket_config:
        if settings.bucket_endpoint:
            storage_opts["aws_endpoint"] = settings.bucket_endpoint
        if settings.bucket_access_key_id:
            storage_opts["aws_access_key_id"] = settings.bucket_access_key_id
        if settings.bucket_secret_access_key:
            storage_opts["aws_secret_access_key"] = settings.bucket_secret_access_key
        storage_opts["aws_region"] = settings.bucket_region
        storage_opts["aws_virtual_hosted_style_request"] = (
            "true" if settings.bucket_url_style == "vhost" else "false"
        )
    return LanceStore(
        local_dir=local_dir,
        bucket_uri=bucket_uri,
        storage_options=storage_opts,
        offline_only=settings.offline_only,
        default_commercial_safe_only=settings.commercial_safe_only,
    )


def _load_optional_manifest(settings: Settings) -> CorpusManifest | None:
    for cand in (settings.data_dir / "manifest.json", settings.data_dir / "parquet" / "manifest.json"):
        if cand.is_file():
            try:
                return load_manifest(cand, offline_only=settings.offline_only)
            except Exception:
                log.warning("Failed to load local manifest at %s", cand, exc_info=True)
    if settings.manifest_url and not settings.offline_only:
        try:
            return load_manifest(settings.manifest_url, offline_only=False)
        except Exception:
            log.warning("Failed to fetch remote manifest from %s", settings.manifest_url, exc_info=True)
    return None


def _get_duckdb_unlocked(settings: Settings) -> DuckDBStore:
    global _duckdb
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
    if _lance is None:
        _lance = _build_lance(settings)
    return _lance


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
    global _backend, _retrieval, _pdf_fetcher, _analytics, _lance, _duckdb
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
