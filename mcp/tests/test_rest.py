"""Contract tests for the v2 REST endpoints in ``nyaya.rest``."""

from __future__ import annotations

from datetime import date as Date

import pytest
from starlette.testclient import TestClient

from nyaya import storage
from nyaya.config import get_settings
from nyaya.exceptions import StorageUnavailable
from nyaya.models import AnalyticsBucket, AnalyticsResult, AnalyticsTier, SourceProvenance
from nyaya.server import create_http_app
from nyaya.storage.duckdb_store import DuckDBStore
from nyaya.storage.manifest import CorpusManifest, DatasetManifest, PartitionEntry
from tests.test_tools_contract import EXPECTED_15_TOOLS, _build_rich_memory_backend

pytestmark = pytest.mark.integration


def _setup_test_app():
    backend = _build_rich_memory_backend()
    storage.set_backend(backend)
    return create_http_app(backend=backend)


def test_corpus_status_and_alias_shape():
    """GET /api/corpus-status and /api/corpus-stats return v2 CorpusStatusResponse."""
    app = _setup_test_app()
    with TestClient(app) as client:
        for path in ("/api/corpus-status", "/api/corpus-stats"):
            r = client.get(path)
            assert r.status_code == 200
            body = r.json()
            assert body["service_version"] == "2.1.0"
            assert "counts" in body
            assert body["counts"]["central_acts"] >= 2
            assert body["counts"]["provisions"] >= 2


def test_acts_endpoint_serializes_v2_models():
    """GET /api/acts returns {items, total, limit, offset} with v2 provenance fields."""
    app = _setup_test_app()
    with TestClient(app) as client:
        r = client.get("/api/acts?q=IPC")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["short_name"] == "IPC"
        assert body["items"][0]["license_spdx"] == "CC-BY-4.0"
        assert body["items"][0]["commercial_safe"] is True


def test_judgments_endpoint_filters_and_clamps():
    """GET /api/judgments validates integer params and filters commercial_safe_only."""
    app = _setup_test_app()
    with TestClient(app) as client:
        r_bad = client.get("/api/judgments?limit=abc")
        assert r_bad.status_code == 400
        assert r_bad.json()["error"] == "bad_request"

        r_all = client.get("/api/judgments?limit=999")
        assert r_all.status_code == 200
        body_all = r_all.json()
        assert body_all["limit"] == 100
        assert body_all["total"] == 2

        r_cs = client.get("/api/judgments?commercial_safe_only=1")
        assert r_cs.status_code == 200
        body_cs = r_cs.json()
        assert body_cs["total"] == 1
        assert body_cs["items"][0]["case_id"] == "sc:1973:kesavananda"


def test_analytics_endpoint_and_commercial_safe_block():
    """GET /api/analytics returns rows/buckets and blocks district_court under commercial_safe_only."""
    app = _setup_test_app()

    class _FakeAnalytics:
        def aggregate(
            self,
            dataset: AnalyticsTier,
            group_by: str,
            *,
            filters: dict,
            top_n: int = 25,
            sample_cases: int = 0,
            commercial_safe_only: bool | None = None,
        ) -> AnalyticsResult:
            return AnalyticsResult(
                dataset=dataset,
                group_by=group_by,
                total_matched=42,
                buckets=[AnalyticsBucket(key="2024", count=42, share_pct=100.0)],
                completeness_caveat="Test caveat",
                provenances=[
                    SourceProvenance(
                        source_id="dattam-hc",
                        source_name="Dattam HC",
                        source_url="https://example.invalid",
                        license_spdx="CC-BY-4.0",
                        commercial_safe=True,
                        terms_status="cleared",
                        as_of=Date(2026, 11, 22),
                    )
                ],
            )

        def close(self) -> None:
            return None

    storage.set_analytics(_FakeAnalytics())

    with TestClient(app) as client:
        r = client.get("/api/analytics?dataset=hc&group_by=year&court=DHC")
        assert r.status_code == 200
        body = r.json()
        assert body["total_cases"] == 42
        assert body["rows"][0]["key"] == "2024"

        r_nc = client.get("/api/analytics?dataset=district_court&state=MH&commercial_safe_only=1")
        assert r_nc.status_code == 403
        assert r_nc.json()["error"] == "commercial_license_blocked"


def test_health_summary_healthy_and_degraded(monkeypatch):
    """GET /api/health-summary returns 200 healthy or degrades gracefully when storage is down."""
    app = _setup_test_app()
    with TestClient(app) as client:
        r_ok = client.get("/api/health-summary")
        assert r_ok.status_code == 200
        assert r_ok.json()["status"] == "healthy"

        def _boom(**_kwargs):
            raise StorageUnavailable("sqlite core missing")

        monkeypatch.setattr(storage.get_backend(), "corpus_status", _boom)
        r_deg = client.get("/api/health-summary")
        assert r_deg.status_code == 200
        assert r_deg.json()["status"] == "degraded"
        assert r_deg.json()["counts"] == {}


def test_corpus_status_storage_down_is_503_with_request_id(monkeypatch):
    app = _setup_test_app()
    with TestClient(app) as client:
        def _boom(**_kwargs):
            raise StorageUnavailable("sqlite core missing")

        monkeypatch.setattr(storage.get_backend(), "corpus_status", _boom)
        r = client.get("/api/corpus-status")
        assert r.status_code == 503
        body = r.json()
        assert body["error"] == "storage_unavailable"
        assert body.get("request_id")
        assert r.headers.get("x-request-id") == body["request_id"]


def test_internal_error_is_500_without_leaked_details(monkeypatch):
    app = _setup_test_app()
    with TestClient(app) as client:
        def _boom(**_kwargs):
            raise RuntimeError("SECRET bucket key leaked in traceback")

        monkeypatch.setattr(storage.get_backend(), "corpus_status", _boom)
        r = client.get("/api/corpus-status")
        assert r.status_code == 500
        assert r.json()["error"] == "internal_error"
        assert "SECRET" not in r.text


def test_tools_endpoint_lists_15_tools_8_resources_4_prompts():
    """GET /api/tools introspects the MCP v2.1 surface (15 tools, 8 resources, 4 prompts)."""
    app = _setup_test_app()
    with TestClient(app) as client:
        r = client.get("/api/tools")
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 15
        assert body["resources_count"] == 8
        assert body["prompts_count"] == 4
        names = {item["name"] for item in body["items"]}
        assert names == EXPECTED_15_TOOLS
        assert all(item["description"] for item in body["items"])
        assert all(isinstance(item["open_world"], bool) for item in body["items"])


def test_corpus_status_and_health_summary_enrich_three_tiers_and_manifest_counts(tmp_path, monkeypatch):
    """GET /api/corpus-status and /api/health-summary expose 3-tier status + rolled-up HC/Tribunal counts."""
    monkeypatch.setenv("NYAYA_DATA_DIR", str(tmp_path / "empty_data"))
    monkeypatch.setenv("NYAYA_CACHE_DIR", str(tmp_path / "empty_cache"))
    monkeypatch.setenv("NYAYA_OFFLINE_ONLY", "1")
    monkeypatch.delenv("NYAYA_BUCKET_ENDPOINT", raising=False)
    monkeypatch.delenv("NYAYA_BUCKET_NAME", raising=False)
    get_settings.cache_clear()
    storage.close_all()

    manifest = CorpusManifest(
        datasets={
            "high_court": DatasetManifest(
                license_spdx="CC-BY-4.0",
                commercial_safe=True,
                row_count=11162843,
                partitions=[PartitionEntry(s3_key="parquet/high_court/hc.parquet", rows=11162843)],
            ),
            "tribunal": DatasetManifest(
                license_spdx="CC-BY-4.0",
                commercial_safe=True,
                row_count=813168,
                partitions=[PartitionEntry(s3_key="parquet/tribunal/tr.parquet", rows=813168)],
            ),
        }
    )

    app = _setup_test_app()
    try:
        with TestClient(app) as client:
            storage.set_analytics(DuckDBStore(manifest=manifest, offline_only=True))
            r_cs = client.get("/api/corpus-status")
            assert r_cs.status_code == 200
            cs_body = r_cs.json()
            assert cs_body["tiers"]["sqlite_core"] == "ok"
            assert "lance_fts" in cs_body["tiers"]
            assert cs_body["tiers"]["duckdb_parquet"] == "ok"
            assert cs_body["counts"]["hc_judgments"] == 11162843
            assert cs_body["counts"]["tribunal_orders"] == 813168

            r_hs = client.get("/api/health-summary")
            assert r_hs.status_code == 200
            hs_body = r_hs.json()
            assert hs_body["status"] == "healthy"
            assert hs_body["backends"]["duckdb_parquet"] == "ok"
            assert "lance_fts" in hs_body["backends"]
            assert hs_body["counts"]["hc_judgments"] == 11162843
            assert hs_body["counts"]["tribunal_orders"] == 813168
    finally:
        storage.close_all()
        get_settings.cache_clear()


def test_analytics_accepts_limit_alias():
    """GET /api/analytics?limit=5 forwards top_n=5 and returns both rows and buckets."""
    app = _setup_test_app()
    seen: list[int] = []

    class _SpyAnalytics:
        def aggregate(
            self,
            dataset: AnalyticsTier,
            group_by: str,
            *,
            filters: dict,
            top_n: int = 25,
            sample_cases: int = 0,
            commercial_safe_only: bool | None = None,
        ) -> AnalyticsResult:
            seen.append(top_n)
            return AnalyticsResult(
                dataset=dataset,
                group_by=group_by,
                total_matched=10,
                buckets=[AnalyticsBucket(key="2024", count=10, share_pct=100.0)],
                completeness_caveat="Test caveat",
                provenances=[],
            )

        def close(self) -> None:
            return None

    storage.set_analytics(_SpyAnalytics())
    try:
        with TestClient(app) as client:
            r = client.get("/api/analytics?dataset=hc&group_by=year&limit=5")
            assert r.status_code == 200
            body = r.json()
            assert seen == [5]
            assert body["rows"] == [{"key": "2024", "count": 10, "share_pct": 100.0}]
            assert len(body["buckets"]) == 1

            r_bad = client.get("/api/analytics?limit=abc")
            assert r_bad.status_code == 400
            assert r_bad.json()["error"] == "bad_request"
    finally:
        storage.close_all()


def test_openapi_json_and_self_contained_docs():
    """GET /openapi.json returns OpenAPI 3.1.0 and GET /docs serves CSP-safe HTML."""
    app = _setup_test_app()
    with TestClient(app) as client:
        r_spec = client.get("/openapi.json")
        assert r_spec.status_code == 200
        spec = r_spec.json()
        assert spec["openapi"] == "3.1.0"
        assert spec["info"]["version"] == "2.1.0"
        paths = spec["paths"]
        for required_path in (
            "/health",
            "/mcp",
            "/api/corpus-status",
            "/api/corpus-stats",
            "/api/acts",
            "/api/judgments",
            "/api/analytics",
            "/api/tools",
            "/api/health-summary",
        ):
            assert required_path in paths, f"Missing {required_path} in OpenAPI paths"

        r_docs = client.get("/docs")
        assert r_docs.status_code == 200
        assert "text/html" in r_docs.headers.get("content-type", "")
        html = r_docs.text
        assert "/openapi.json" in html
        assert "2.1.0" in html
        assert "https://" not in html and "http://" not in html
