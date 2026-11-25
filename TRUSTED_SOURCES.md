# Trusted Sources & Corpus Provenance — Nyaya v2.0

This document describes the legal provenance, copyright basis, licensing
boundaries, automated integrity gates, and LLM security posture for data
ingested and served by **Nyaya (`nyaya-mcp`)**.

## Legal & Copyright Basis (India)

Nyaya does **not** rely on US public-domain doctrines. Primary Indian legal
materials are reproduced and indexed under **Indian law**:

1. **Indian Copyright Act, 1957 — Section 52(1)(q):**
   - **Section 52(1)(q)(ii):** Reproduction or publication of any Act of a
     Legislature (and rules/regulations made thereunder), subject to statutory
     conditions.
   - **Section 52(1)(q)(iv):** Reproduction or publication of any judgment or
     order of a Court, Tribunal, or other judicial authority, unless prohibited
     from publication by that authority.
2. ***Eastern Book Company & Ors. v. D.B. Modak & Anr.*, (2008) 1 SCC 1:**
   Raw judicial decisions are reproducible under Section 52(1)(q)(iv), whereas
   commercial law-reporter editorial additions (headnotes, proprietary
   paragraph numbering, cross-notes, and digests) carry independent copyright.
   Nyaya **strictly excludes** commercial reporter headnotes and proprietary
   editorial digests (`verify-gate` checks for reporter signatures such as
   `"SCC Online"`, `"Manupatra"`, and `"AIR Headnote"`).
3. **Statutory Non-Publication Exclusions:**
   Orders subject to judicial or statutory publication bans are blocked by
   `check_publication_ban()` (`mcp/nyaya/sanitize.py`) and audited by
   `nyaya-ingest verify-gate`:
   - Sealed-cover and *in-camera* proceedings prohibited from publication
   - Victim-identifying orders under **IPC s. 228A / BNS s. 72**, **POCSO Act
     ss. 23 & 33(7)**, **Juvenile Justice Act s. 74**, and **HIV/AIDS Act s. 34**
   *(Context-aware detection distinguishes operative suppression directives from
   ordinary doctrinal precedents discussing those sections.)*

## Trust Model & OWASP LLM01 Posture

**The legal corpus is UNTRUSTED DATA, not trusted instructions.**

Primary court filings, party pleadings quoted verbatim inside judgments, and
external legal compilations can contain adversarial text or malformed payloads.
Accordingly, Nyaya treats every retrieved statutory and judicial string as
**untrusted primary legal data** (`trust="untrusted_primary_law"`) under
**OWASP LLM01 (Prompt Injection)**:

- **Canonical Data Framing (`frame_corpus_text()` in `mcp/nyaya/sanitize.py`):**
  Every primary legal text field returned by any MCP tool or resource is wrapped
  in an attributed envelope:
  ```xml
  <corpus_text doc_id="..." source="..." license="..." commercial_safe="true|false" as_of="..." sha256="..." trust="untrusted_primary_law">
  ...sanitized and escaped text...
  </corpus_text>
  ```
- **Invisible & Control-Character Stripping:** Strips C0/C1 control characters
  (preserving `\n`, `\r`, `\t`), BiDi overrides, soft hyphens (`U+00AD`),
  mid-text BOM (`U+FEFF`), variation selectors (`U+FE00–U+FE0F`), and **Unicode
  Tag smuggling (`U+E0000–U+E007F`)**, while preserving valid Devanagari and
  Indic Unicode ranges (`U+0900–U+0D7F`).
- **Inner Delimiter Escaping (`escape_corpus_delimiters()`):** Escapes `<` to
  `&lt;` on breakout/control tags (`</corpus_text>`, `<system>`, `<instructions>`,
  `<|im_start|>`, `<|im_end|>`) without corrupting ordinary mathematical `<`/`>`
  prose.
- **Non-Destructive Injection Flagging (`detect_suspected_injection()`):**
  Records `suspected_injection="true"` for audit visibility without silently
  altering verbatim primary statutory or judicial wording.
- **Lossless Chunking (`MAX_CHUNK_BYTES = 16_384`):** Eliminates legacy
  200 KB document truncation limits while keeping individual chunks bounded.
- **System-Prompt Isolation (`mcp_app.py`, `prompts/__init__.py`, `chat/`):**
  Server instructions and MCP prompt templates explicitly instruct downstream
  LLMs that `<corpus_text>` contents are untrusted legal data and never
  executable instructions.

## Upstream Sources (`sources.lock.yaml`)

All upstream sources are pinned in `mcp/nyaya/ingest/data/sources.lock.yaml`,
restricted to an explicit domain allowlist (cross-domain HTTP redirects blocked),
verified by SHA-256 / S3 ETag / `SHA256SUMS.json`, and validated before any
build or publish.

| Source | URI / Bucket | Coverage | License (`SPDX`) | `commercial_safe` | Verification |
|---|---|---|---|---|---|
| **Vaquill Open India Law (`v2026.08.1`)** | `https://oss-data-in.vaquill.ai/v2026.08.1/` | Central + 36 State/UT Acts, 12 Regulators, 15 Tribunals, SC/HC compilations | `CC-BY-4.0` (`coverage.yml` `red` rejected) | `true` | `SHA256SUMS.json` + domain allowlist |
| **Dattam Labs — Supreme Court** | `s3://indian-supreme-court-judgments` | 38k+ SC judgments metadata & direct AWS S3 PDFs | `CC-BY-4.0` (+ s. 52(1)(q)(iv)) | `true` | Full-year S3 ETag watermark + `%PDF-` magic check |
| **Dattam Labs — High Courts** | `s3://indian-high-court-judgments` | 20.7M+ HC metadata across 25 HCs + `case_details-mobile` + direct PDFs | `CC-BY-4.0` (+ s. 52(1)(q)(iv)) | `true` | Full-year S3 ETag + `superseded_files` refetch |
| **PRS Legislative Research** | `https://prsindia.org` | BNS / BNSS / BSA 2023 & legislative act deltas | `CC-BY-4.0` | `true` | Pinned SHA-256 + domain allowlist |
| **Legislative Department / India Code** | `https://legislative.gov.in` | Constitution of India (Articles, Schedules, Amendments 1–106) | `CC0-1.0` (s. 52(1)(q)(ii)) | `true` | Pinned SHA-256 + `verify-gate` |
| **India Code Digital Repository** | `https://indiacode.nic.in` | Central Acts official statutory repository | `CC0-1.0` (s. 52(1)(q)(ii)) | `true` | Pinned SHA-256 + domain allowlist |
| **Supreme Court e-SCR** | `https://digiscr.sci.gov.in` | Official SC neutral citations & judgment metadata | `CC-BY-4.0` (s. 52(1)(q)(iv)) | `true` | Pinned SHA-256 + domain allowlist |
| **Sansad Digital Library (CAD)** | `https://eparlib.sansad.in` | Constituent Assembly Debates (12 volumes) | `CC0-1.0` (s. 52(1)(q) / s. 28) | `true` | Pinned SHA-256 |
| **Justice Hub / KHOJ** | `https://justicehub.in` | High Court judge metadata (CC-BY-4.0 subset) | `CC-BY-4.0` | `true` | Pinned SHA-256 |
| **Development Data Lab (DDL)** | `https://www.devdatalab.org/judicial-data` | 81M District Court metadata (2010–2018) | `CC-BY-NC-SA-4.0` | **`false`** | Pinned SHA-256; ML religion/caste cols dropped |
| **ILDC Research Pack** | Hugging Face research pack | 35k SC outcome/explanation annotations | `CC-BY-NC-4.0` | **`false`** | Pinned SHA-256 |

### Non-Commercial Filtering (`NYAYA_COMMERCIAL_SAFE_ONLY`)

- By default (academic/research-inclusive posture), Nyaya includes DDL 81M and
  ILDC tagged `commercial_safe=false` and `terms_status="non_commercial_only"`.
- Commercial deployments **must** set `NYAYA_COMMERCIAL_SAFE_ONLY=1` (or pass
  `commercial_safe_only=true` per call) so SQLite, LanceDB, DuckDB, MCP tools,
  and REST endpoints automatically exclude all `commercial_safe=false` datasets
  (returning `commercial_license_blocked` on targeted NC lookups).

### Optional Chat LLM Provider

- **MCP retrieval requires zero LLM or embedding API keys.** Search is 100%
  local/lexical (SQLite FTS5 + LanceDB native Rust BM25 FTS + DuckDB Parquet).
- Only the optional `/chat` FastAPI + LangGraph sub-app calls `ChatNVIDIA`
  (`integrate.api.nvidia.com`) when `NVIDIA_API_KEY` is configured.

## Automated Legal Integrity Gate (`nyaya-ingest verify-gate`)

Every ingestion run must pass 8 hard-fail suites before producing `gate-pass.json`
(which binds exact artifact SHA-256s required by `nyaya-ingest publish`):

1. `check_amendments`: Contiguous Constitutional Amendments 1–106 with golden
   date/article assertions (e.g., 42nd=1976, 99th=`struck_down`, 106th=2023).
2. `check_concordance`: Referential integrity and anti-collapse checks across
   IPC↔BNS, CrPC↔BNSS, and IEA↔BSA, plus 39 mandatory golden pairs.
3. `check_invalidations`: Landmark judicial invalidations (struck-down /
   read-down / abeyance) are present with the expected `effect`.
4. `check_coverage_and_non_truncation`: Completeness floors and `>500 KB`
   non-truncation checks (*Kesavananda Bharati*, *Puttaswamy*).
5. `check_extraction_quality`: Rejects HTML 404/error placeholders masquerading
   as PDFs, non-UTF-8 text, and missing `text_sha256`.
6. `check_licensing_and_provenance`: Rejects `terms_status="red"`, unknown/copyleft
   SPDX tags, missing provenance, NC mis-tagging, and reporter headnotes.
7. `check_statutory_exclusions`: Blocks any row flagged by `check_publication_ban()`.
8. `check_owasp_llm01`: Verifies zero raw controls, BiDi overrides, Unicode Tags,
   or unescaped `</corpus_text>` tags in indexed chunks.

## Banned Sources & Copyleft Dependencies

CI (`.github/workflows/security.yml`) and `lockfile.py` strictly ban:
- **Scraped / unclear-rights sources:** Unofficial third-party case-law scrapers,
  unverified community dumps (`civictech-India`, `mratanusarkar`), and
  `terms_status="red"` Vaquill entries (enforced via `_BANNED_URI_SUBSTRINGS` in
  `mcp/nyaya/ingest/lockfile.py`).
- **Copyleft & banned packages in lockfiles:** `ecourts` (GPL-3.0),
  `pymupdf` / `fitz` (AGPL-3.0), `surya-ocr` (GPL), and legacy database/vector
  client drivers (enforced by `.github/workflows/security.yml`).
