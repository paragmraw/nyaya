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
// NOTE: reconciled against the live project via `railway config plan`. The
// builder (DOCKERFILE, root Dockerfile — auto-detected), restart policy
// (ON_FAILURE, max 3 retries) and the healthcheck interval (30 s) are not
// IaC-authorable fields; they live as dashboard service settings carried
// over from the railway.toml deploys — verify them in the service Settings
// panel. Always review `railway config plan --detailed-exit-code` before
// applying: variables and deploy settings absent from this file are deleted.

export default defineRailway(() =>
  project("nyaya", {
    resources: [
      service("nyaya", {
        source: github("paragmraw/nyaya", { branch: "main" }),
        healthcheck: "/health",
        healthcheckTimeout: 60,
        replicas: 1,
        // Dashboard-managed service settings that Railway IaC's "omit means
        // delete" would otherwise unset: keep scale-to-zero sleep and IPv6
        // egress exactly as configured live.
        deploy: { sleepApplication: true, ipv6EgressEnabled: true },
        env: {
          // External Supabase Postgres+pgvector — NOT a Railway-managed
          // database. Do not add postgres()/redis() resources here.
          // preserve() keeps the values already set in the Railway Variables
          // tab; add any further variables here before applying, since an
          // apply can delete live variables absent from this map.
          DATABASE_URL: preserve(),
          NVIDIA_API_KEY: preserve(),
          // Build-time flag for the chat panel (Dockerfile ARG); "true" makes
          // the intent explicit — a preserve() would keep a stale "false".
          NEXT_PUBLIC_CHAT_ENABLED: "true",
        },
      }),
    ],
  }),
);