# Security Policy

## Supported versions

nyaya is in alpha. Security fixes are applied to the latest `main` branch only.

| Version | Supported |
|---------|-----------|
| 0.2.x   | Yes (latest `main`) |

## Reporting a vulnerability

Email **mail@parag.tech** with a description of the vulnerability, reproduction steps, and impact assessment.

- **Do not** open a public GitHub issue for security vulnerabilities.
- You will receive an acknowledgement within 48 hours.
- We will investigate and respond with a fix timeline within 7 days.
- Please do not disclose the vulnerability publicly until a fix is released.

## Security measures

nyaya implements defense-in-depth at the application layer:

### Rate limiting
- Read endpoints (REST `/api/*`): 120 req/min/IP (hardcoded in `nyaya/config.py`)
- MCP tool calls (`POST /mcp`): 30 req/min/IP (hardcoded in `nyaya/config.py`)
- Body size cap: 1 MB (hardcoded in `nyaya/config.py`)
- Redis backend for multi-worker deployments (set `REDIS_URL` constant in `nyaya/config.py`)
- In-memory fallback for single-worker (dev/local) and Redis outages (fail-open)

### Security headers
- Content-Security-Policy (restricts all resource loading to same-origin)
- X-Frame-Options: DENY
- X-Content-Type-Options: nosniff
- Referrer-Policy: strict-origin-when-cross-origin
- Permissions-Policy: camera, microphone, geolocation denied
- HSTS (HTTPS only, 1 year + subdomains)

### CORS
- Restricted to `https://nyaya.parag.tech`
- `allow_credentials=False`
- Only `GET`, `POST`, `OPTIONS` methods
- Only `Content-Type`, `Accept` headers

### Input sanitization
- Control characters stripped at ingest time (C0/C1 controls, bidi overrides)
- 200 KB max text length per row at ingest time (sections, articles, schedules)
- Judgment text has no length cap (some judgments exceed 700 KB)
- Query length cap (4096 chars) prevents DoS via expensive embedding/semantic operations

### Database
- Connection pooling with statement timeout (15s default)
- All queries use parameterized SQL (no string interpolation of user input)
- Read-only role recommended for production deployments

### Deployment recommendations
- Deploy behind a reverse proxy (Cloudflare, Railway edge, nginx) with TLS and authentication
- Use a read-only Postgres role for `DATABASE_URL`
- Run the hydration notebook from a trusted machine, not the public deployment
- Set the `REDIS_URL` constant in `nyaya/config.py` for multi-worker rate limiting