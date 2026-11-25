import { defineRailway, github, preserve, project, service } from "railway/iac";

// Railway IaC (https://docs.railway.com/infrastructure-as-code), the replacement
// for the deprecated railway.toml Config-as-Code, which Railway stops reading
// on 2026-12-01.
//
// Single-service Railway architecture (database-free, sleep-on-idle):
// - One service, one origin: the root multi-stage Dockerfile builds the Next.js
//   SPA (Node 20 stage) and the Python 3.12 Debian-slim MCP runtime with a baked
//   read-only SQLite core (~250 MB) + pre-installed DuckDB httpfs extension,
//   then serves the SPA + REST + /mcp + optional /chat from a single uvicorn
//   process (`nyaya.cli --transport http`).
// - Attach a Railway Storage Bucket (Tigris S3, private) to this service and
//   set the dashboard deployment region to `asia-southeast1` (Singapore) for
//   minimal RTT to Indian users and AWS `ap-south-1` (Mumbai) Open Data PDFs.
// - The server is stateless (`stateless_http=True`): safe to scale `replicas`
//   later without code changes; multi-replica deployments should also configure
//   REDIS_URL for cross-replica rate limiting (mcp/nyaya/ratelimit.py).
//
// Managed with `railway config plan` (dry run) and `railway config apply`.
//
// NOTE: reconciled against the live project via `railway config plan`. The
// builder (DOCKERFILE, root Dockerfile, auto-detected), deployment region
// (`asia-southeast1`), restart policy (ON_FAILURE, max 3 retries), and
// healthcheck interval (30 s) are dashboard service settings; verify them in
// the service Settings panel. Always review `railway config plan --detailed-exit-code`
// before applying: variables absent from `env` are deleted on apply.

export default defineRailway(() =>
  project("nyaya", {
    resources: [
      service("nyaya", {
        source: github("paragmraw/nyaya", { branch: "main" }),
        healthcheck: "/health",
        healthcheckTimeout: 60,
        replicas: 1,
        // Keep sleep-on-idle (sleepApplication) and IPv6 egress enabled.
        deploy: { sleepApplication: true, ipv6EgressEnabled: true },
        env: {
          // Railway Storage Bucket (Tigris S3) cold tiers: LanceDB native BM25 FTS
          // tables + compacted ZSTD Parquet partitions (17.77M HC, 81M DDL District,
          // 15 Tribunals, 12 Regulators, SC mirror). preserve() keeps the live
          // credentials configured in the Railway Variables tab.
          NYAYA_BUCKET_ENDPOINT: preserve(),
          NYAYA_BUCKET_NAME: preserve(),
          NYAYA_BUCKET_ACCESS_KEY_ID: preserve(),
          NYAYA_BUCKET_SECRET_ACCESS_KEY: preserve(),
          NYAYA_BUCKET_REGION: "auto",
          NYAYA_BUCKET_URL_STYLE: "vhost",
          // Build-time SQLite core release artifact (Dockerfile Stage 2b); preserve()
          // keeps the release URL and SHA-256 configured in Railway Variables.
          NYAYA_CORE_SQLITE_URL: preserve(),
          NYAYA_CORE_SQLITE_SHA256: preserve(),
          // Right-most trusted proxy hop count (1 behind Railway edge).
          NYAYA_TRUSTED_PROXY_HOPS: "1",
          // Academic/research-inclusive default (CC-BY-NC DDL 81M & ILDC tagged
          // commercial_safe=false); set "true" for commercial-only lock.
          NYAYA_COMMERCIAL_SAFE_ONLY: "false",
          // Optional: mounts the /chat LangGraph sub-app when set; MCP core
          // retrieval requires zero API keys.
          NVIDIA_API_KEY: preserve(),
          // Build-time flag for the chat panel (Dockerfile ARG); explicit "true"
          // avoids preserving a stale "false".
          NEXT_PUBLIC_CHAT_ENABLED: "true",
        },
      }),
    ],
  }),
);
