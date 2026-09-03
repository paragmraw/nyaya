import { defineRailway, github, preserve, project, service } from "railway/iac";

// Railway IaC (https://docs.railway.com/infrastructure-as-code) — replacement
// for the deprecated railway.toml Config-as-Code, which Railway stops reading
// on 2026-12-01.
//
// One service, one origin: the root multi-stage Dockerfile builds the Next.js
// SPA (Node stage) and the Python MCP server (Alpine stage), then serves the
// SPA + REST + /mcp + /chat from a single uvicorn process. The build context
// is the repo root — the Dockerfile lives at the repo root, not here.
//
// The server is stateless (stateless_http=True): safe to scale `replicas`
// later without code changes, but a multi-replica deployment must also set
// REDIS_URL for cross-replica rate limiting (mcp/nyaya/ratelimit.py).
//
// Managed with `railway config plan` (dry run) and `railway config apply`.
//
// NOTE (hand-authored fallback): the Railway CLI was not installed when this
// file was written, so only fields from the public IaC reference are declared.
// Builder (DOCKERFILE + dockerfilePath "Dockerfile"), restart policy
// (ON_FAILURE, max 3 retries) and the healthcheck interval (30 s) live as
// dashboard service settings carried over from the railway.toml deploys —
// verify them in the service Settings panel, or run `railway config migrate`
// after installing the CLI to reconcile the canonical fields into this file.

export default defineRailway(() =>
  project("nyaya", {
    resources: [
      service("nyaya", {
        source: github("paragmraw/nyaya", { branch: "main" }),
        healthcheck: "/health",
        healthcheckTimeout: 60,
        replicas: 1,
        env: {
          // External Supabase Postgres+pgvector — NOT a Railway-managed
          // database. Do not add postgres()/redis() resources here.
          // preserve() keeps the values already set in the Railway Variables
          // tab; add any further variables here before applying, since an
          // apply can delete live variables absent from this map.
          DATABASE_URL: preserve(),
          NVIDIA_API_KEY: preserve(),
        },
      }),
    ],
  }),
);