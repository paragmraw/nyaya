"""Integration tests for Nyaya v2.0 HTTP server boot, /health, /mcp handshake, and static asset caching."""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from nyaya import storage
from nyaya.config import get_settings
from nyaya.exceptions import StorageUnavailable
from nyaya.http_app import _CachedStaticFiles, create_http_app
from nyaya.storage.duckdb_store import DuckDBStore
from nyaya.storage.manifest import CorpusManifest, DatasetManifest, PartitionEntry
from nyaya.tools.corpus_status import corpus_status
from tests.test_tools_contract import _build_rich_memory_backend

pytestmark = pytest.mark.integration


def test_health_endpoint():
    backend = _build_rich_memory_backend()
    app = create_http_app(backend=backend)
    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["service"] == "nyaya"
        assert body["status"] == "healthy"
        assert body["version"] == "2.0.0"
        assert "counts" in body
        assert "backends" in body


def test_health_degraded(monkeypatch):
    backend = _build_rich_memory_backend()
    app = create_http_app(backend=backend)

    def _boom(**_kwargs):
        raise StorageUnavailable("sqlite core missing")

    monkeypatch.setattr(backend, "corpus_status", _boom)

    with TestClient(app) as client:
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "degraded"
        assert body["counts"] == {}


def test_mcp_endpoint_reachable_and_initialize_handshake():
    backend = _build_rich_memory_backend()
    app = create_http_app(backend=backend)
    with TestClient(app) as client:
        r = client.post(
            "/mcp",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "method": "initialize",
                "id": 1,
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "2.0.0"},
                },
            },
        )
        assert r.status_code in (200, 202, 400), f"unexpected status {r.status_code}: {r.text}"


def test_cors_headers():
    backend = _build_rich_memory_backend()
    app = create_http_app(backend=backend)
    with TestClient(app) as client:
        r = client.get("/health", headers={"Origin": "https://nyaya.parag.tech"})
        assert r.status_code == 200
        assert "access-control-allow-origin" in {k.lower() for k in r.headers}


def test_lifespan_aclose_all_called(monkeypatch):
    """On shutdown, storage.aclose_all() is called by the lifespan handler."""
    backend = _build_rich_memory_backend()
    app = create_http_app(backend=backend)

    closed = {"called": False}
    orig_aclose = storage.aclose_all

    async def _spy_aclose():
        closed["called"] = True
        await orig_aclose()

    monkeypatch.setattr(storage, "aclose_all", _spy_aclose)

    with TestClient(app) as client:
        client.get("/health")
    assert closed["called"], "storage.aclose_all was not called on shutdown"


def _make_static_client(tmp_path):
    web = tmp_path / "out"
    (web / "_next" / "static").mkdir(parents=True)
    (web / "_next" / "static" / "x.js").write_text("console.log(1)")
    (web / "index.html").write_text("<html>home</html>")
    (web / "robots.txt").write_text("User-agent: *\nAllow: /")
    return TestClient(
        Starlette(routes=[Mount("/", app=_CachedStaticFiles(directory=str(web), html=True))])
    )


def test_static_next_static_assets_are_immutable(tmp_path):
    client = _make_static_client(tmp_path)
    r = client.get("/_next/static/x.js")
    assert r.status_code == 200
    assert r.headers["cache-control"] == "public, max-age=31536000, immutable"


def test_static_html_and_robots_get_no_cache(tmp_path):
    client = _make_static_client(tmp_path)
    html = client.get("/")
    assert html.status_code == 200
    assert html.headers["cache-control"] == "no-cache"

    robots = client.get("/robots.txt")
    assert robots.headers["cache-control"] == "no-cache"


def test_health_and_corpus_status_three_tier_and_manifest_rollup(tmp_path, monkeypatch):
    """Verify 3-tier status reporting (unconfigured & active) + bucket manifest count rollup."""
    monkeypatch.setenv("NYAYA_DATA_DIR", str(tmp_path / "empty_data"))
    monkeypatch.setenv("NYAYA_CACHE_DIR", str(tmp_path / "empty_cache"))
    monkeypatch.setenv("NYAYA_OFFLINE_ONLY", "1")
    monkeypatch.delenv("NYAYA_BUCKET_ENDPOINT", raising=False)
    monkeypatch.delenv("NYAYA_BUCKET_NAME", raising=False)
    monkeypatch.delenv("NYAYA_COMMERCIAL_SAFE_ONLY", raising=False)
    get_settings.cache_clear()
    storage.close_all()

    backend = _build_rich_memory_backend()
    app = create_http_app(backend=backend)
    try:
        # 1. Unconfigured cold tiers do not crash /health or corpus_status
        with TestClient(app) as client:
            r = client.get("/health")
            assert r.status_code == 200
            body = r.json()
            assert body["status"] == "healthy"
            assert body["backends"]["sqlite_core"] == "ok"
            assert body["backends"]["lance_fts"] == "unconfigured"
            assert body["backends"]["duckdb_parquet"] == "unconfigured"

            st_unconf = corpus_status()
            assert st_unconf.tiers["sqlite_core"] == "ok"
            assert st_unconf.tiers["lance_fts"] == "unconfigured"
            assert st_unconf.tiers["duckdb_parquet"] == "unconfigured"

        # 2. Active DuckDB manifest rolls up larger SC/HC/Tribunal/District counts,
        # respecting commercial_safe_only.
        manifest = CorpusManifest(
            datasets={
                "supreme_court": DatasetManifest(
                    license_spdx="CC-BY-4.0",
                    commercial_safe=True,
                    row_count=35000,
                    partitions=[PartitionEntry(s3_key="parquet/supreme_court/sc.parquet", rows=35000)],
                ),
                "high_court": DatasetManifest(
                    license_spdx="CC-BY-4.0",
                    commercial_safe=True,
                    row_count=17770000,
                    partitions=[PartitionEntry(s3_key="parquet/high_court/hc.parquet", rows=17770000)],
                ),
                "tribunal": DatasetManifest(
                    license_spdx="CC-BY-4.0",
                    commercial_safe=True,
                    row_count=125000,
                    partitions=[PartitionEntry(s3_key="parquet/tribunal/tr.parquet", rows=125000)],
                ),
                "district_court": DatasetManifest(
                    license_spdx="CC-BY-NC-SA-4.0",
                    commercial_safe=False,
                    terms_status="non_commercial_only",
                    row_count=81000000,
                    partitions=[PartitionEntry(s3_key="parquet/district_court/dc.parquet", rows=81000000)],
                ),
            }
        )
        storage.set_backend(backend)
        storage.set_analytics(DuckDBStore(manifest=manifest, offline_only=True))

        st_all = corpus_status(commercial_safe_only=False)
        assert st_all.tiers["duckdb_parquet"] == "ok"
        assert st_all.counts.sc_judgments == 35000
        assert st_all.counts.hc_judgments == 17770000
        assert st_all.counts.tribunal_orders == 125000
        assert st_all.counts.district_court_records == 81000000

        st_cs = corpus_status(commercial_safe_only=True)
        assert st_cs.counts.sc_judgments == 35000
        assert st_cs.counts.hc_judgments == 17770000
        assert st_cs.counts.tribunal_orders == 125000
        assert st_cs.counts.district_court_records == 0

        with TestClient(app) as client:
            # Re-bind analytics since lifespan calls aclose_all on context exit
            storage.set_analytics(DuckDBStore(manifest=manifest, offline_only=True))
            r_health = client.get("/health")
            assert r_health.status_code == 200
            hb = r_health.json()
            assert hb["backends"]["duckdb_parquet"] == "ok"
            assert hb["counts"]["hc_judgments"] == 17770000
            assert hb["counts"]["district_court_records"] == 81000000
    finally:
        storage.close_all()
        get_settings.cache_clear()
