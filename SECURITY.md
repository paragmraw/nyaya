# Security Policy

## Supported Versions

Nyaya v2.0 security fixes are applied to `main`.

| Version | Supported |
|---|---|
| `2.0.x` | Yes (`main`) |
| `< 2.0` | End-of-life (replaced by v2.0) |

## Reporting a Vulnerability

Email **mail@parag.tech** with a description of the vulnerability, reproduction steps, and impact assessment.

- **Do not** open a public GitHub issue for security vulnerabilities.
- You will receive an acknowledgement within 48 hours.
- We will investigate and respond with a fix timeline within 7 days.
- Please do not disclose the vulnerability publicly until a fix is released.

## Security Architecture

Nyaya implements defense-in-depth at the transport, middleware, storage, and LLM-prompt layers:

### Rate Limiting & Body Caps (`mcp/nyaya/ratelimit.py`)

- Read / REST endpoints (`/api/*`): `120` req/min/IP
- MCP tool calls (`POST /mcp`): `30` req/min/IP
- DuckDB Parquet analytics (`GET /api/analytics`): `20` req/min/IP
- Chat turns (`POST /chat/*`): `15` req/min/IP
- Streaming body cap (`BodySizeLimitMiddleware`): `1 MB` enforced both via safe `Content-Length` parsing (invalid/negative values return HTTP `400`, never `500`) and chunked `receive` counting (`413` on overflow)
- Trusted proxy IP extraction: selects the **right-most trusted hop** from `X-Forwarded-For` using `NYAYA_TRUSTED_PROXY_HOPS` (default `1`), defeating left-most client spoofing; loopback exemption requires no `X-Forwarded-For` header
- Bounded memory: `InMemoryBackend` uses a thread-safe `OrderedDict` LRU capped at `50,000` keys; optional `REDIS_URL` enables shared multi-replica counters (fail-open to in-memory on Redis outages)

### Security Headers & CORS (`mcp/nyaya/security_headers.py`, `mcp/nyaya/http_app.py`)

- `Content-Security-Policy` (`default-src 'self'`, `frame-ancestors 'none'`, `base-uri 'self'`, `form-action 'self'`)
- `X-Frame-Options: DENY`
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `Permissions-Policy: camera=(), microphone=(), geolocation=()`
- `Cross-Origin-Opener-Policy: same-origin` and `Cross-Origin-Resource-Policy: same-origin`
- `Strict-Transport-Security`: `max-age=31536000; includeSubDomains` when `scheme == "https"` or `X-Forwarded-Proto: https` (when `NYAYA_TRUSTED_PROXY_HOPS > 0`)
- CORS restricted to `https://nyaya.parag.tech` (`allow_credentials=False`, `GET`/`POST`/`OPTIONS`, `Content-Type`/`Accept`)

### OWASP LLM01 & Corpus Sanitization (`mcp/nyaya/sanitize.py`)

- Primary legal text is treated as **untrusted data**, wrapped in `<corpus_text doc_id="..." source="..." license="..." commercial_safe="..." as_of="..." sha256="..." trust="untrusted_primary_law">...</corpus_text>`
- Strips C0/C1 controls, BiDi overrides, soft hyphens (`U+00AD`), mid-text BOM (`U+FEFF`), variation selectors, and Unicode Tag characters (`U+E0000–U+E007F`)
- Escapes inner breakout/control delimiters (`</corpus_text>`, `<system>`, `<instructions>`, `<|im_start|>`, `<|im_end|>`)
- Enforces statutory publication bans (sealed/in-camera orders, IPC s. 228A / BNS s. 72, POCSO, JJ Act s. 74, HIV Act s. 34) via `check_publication_ban()`

### Storage, DuckDB & Direct S3 PDF Isolation

- **SQLite Core:** Opened strictly read-only (`mode=ro&immutable=1`, `PRAGMA query_only=ON`, `mmap_size=64 MB`).
- **DuckDB Parquet:** `httpfs` pre-installed at image build time into `/app/duckdb_ext` (`autoinstall_known_extensions=false`); hard-capped at `memory_limit='256MB'`, `threads=2`, 5s `con.interrupt()` timeout, and mandatory partition guards (`QueryTooBroad`). Private Railway Bucket secrets are scoped strictly (`SCOPE 's3://<bucket>'`) so credentials never leak onto public URLs.
- **Direct S3 PDF Fetcher (`pdf_fetcher.py`):** Enforces a strict bucket allowlist (`indian-supreme-court-judgments`, `indian-high-court-judgments`), strict key regex, `follow_redirects=False`, 15 MB stream cap, `%PDF-` magic-byte gate, `asyncio.Semaphore(4)`, 500-page cap, `chars_per_page >= 50` quality gate, and a 256 MB disk zstd LRU cache (`NYAYA_CACHE_DIR`).
- **License & Supply-Chain Hygiene:** CI `.github/workflows/security.yml` runs `pip-audit` on both `mcp/` and `chat/`, `npm audit` on `web/`, `osv-scanner`, `trivy` (image + filesystem), `gitleaks`, and enforces the `license-hygiene` lockfile gate banning copyleft PDF/OCR packages (`ecourts`, `pymupdf`, `fitz`, `surya-ocr`) and legacy database/vector client drivers in `mcp/uv.lock` and `chat/uv.lock`.

### Deployment Recommendations

- Run containers with `--read-only`, `--cap-drop=ALL`, `--security-opt no-new-privileges:true`, non-root user `nyaya` (`UID 1000`), and a disk-backed cache volume (`NYAYA_CACHE_DIR=/var/cache/nyaya` in Compose) so `tmpfs` never exhausts `mem_limit: 1g`.
- Deploy behind a TLS-terminating reverse proxy (Railway edge / Cloudflare / nginx) and match `NYAYA_TRUSTED_PROXY_HOPS` to your proxy chain depth (`0` if directly exposed).
- Execute `nyaya-ingest` only in trusted CI or offline environments; verify `gate-pass.json` before publishing artifacts.
- Set `NYAYA_COMMERCIAL_SAFE_ONLY=1` for commercial deployments that must exclude `CC-BY-NC` datasets (`DDL 81M`, `ILDC`).
