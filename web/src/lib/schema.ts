// Page-specific JSON-LD schemas. Pure data (no React imports) so tests can
// import and structurally validate them (see tests/structured-data.test.ts).
//
// Every site-absolute URL is built from SITE (src/lib/site.ts) so identity
// stays single-sourced. URL fragments are only used when a matching DOM id
// exists on the target route — the test asserts this.

import { API_VERSION, OG_IMAGE, SITE } from "./site";

const ORG_ID = `${SITE}/#organization`;

export const siteSchema = {
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "Organization",
      "@id": ORG_ID,
      name: "Nyaya",
      url: SITE,
      logo: `${SITE}/logo.svg`,
      sameAs: ["https://github.com/paragmraw/nyaya"],
      description:
        "Database-free Indian legal MCP server and research assistant covering statutes, regulators, tribunals, Supreme Court, High Courts, and District metadata.",
    },
    {
      "@type": "WebSite",
      "@id": `${SITE}/#website`,
      url: SITE,
      name: "Nyaya",
      description:
        "Pure-lexical and citation-graph legal research MCP server and assistant for Indian law.",
      publisher: { "@id": ORG_ID },
    },
  ],
};

// Page-specific schemas
export const homeSchema = {
  "@context": "https://schema.org",
  "@type": ["SoftwareApplication", "WebApplication"],
  name: "Nyaya",
  description:
    "Pure-lexical Indian legal MCP server and assistant covering 37 legislatures, 12 regulators, 15 tribunals, 38k+ SC judgments, and 20.7M+ High Court records.",
  url: `${SITE}/`,
  applicationCategory: "LegalApplication",
  operatingSystem: "Linux, macOS, Windows, Web",
  softwareVersion: API_VERSION,
  license: "https://www.apache.org/licenses/LICENSE-2.0",
  offers: {
    "@type": "Offer",
    price: "0",
    priceCurrency: "INR",
    availability: "https://schema.org/InStock",
  },
  publisher: {
    "@id": ORG_ID,
  },
  featureList: [
    "Constitution of India (articles, 12 schedules, Amendments 1–106)",
    "IPC/CrPC/IEA ↔ BNS/BNSS/BSA concordance and BNSS s. 531 savings",
    "37 Central & State/UT Legislation jurisdictions + 12 Regulators",
    "Judiciary & Tribunals (38k+ SC, 20.7M+ HC, 15 Tribunals / 813,168 orders)",
    "Deterministic citation parsing & verification (verify_citations)",
    "12 MCP tools, 8 nyaya:// resources, and 4 practitioner prompts via uvx stdio or HTTP",
  ],
  screenshot: `${SITE}${OG_IMAGE}`,
};

export const corpusSchema = {
  "@context": "https://schema.org",
  "@type": "DataCatalog",
  name: "Nyaya Legal Corpus",
  description:
    "Open-India-Law Pack indexed across SQLite FTS5, LanceDB BM25, DuckDB ZSTD Parquet, and on-demand S3 PDFs.",
  url: `${SITE}/corpus/`,
  publisher: { "@id": ORG_ID },
  dataset: [
    {
      "@type": "Dataset",
      name: "Constitution of India & Amendments 1–106",
      description: "Constitutional provisions, 12 schedules, and verified Amendments 1–106",
      url: `${SITE}/corpus/`,
      keywords: ["Constitution", "Fundamental Rights", "Amendments 1-106"],
      license: "IN-S52-1-q",
      creator: { "@id": ORG_ID },
    },
    {
      "@type": "Dataset",
      name: "Central & State/UT Legislation (37 Jurisdictions)",
      description: "1,569 Central Acts, BNS/BNSS/BSA 2023, IPC/CrPC/IEA concordance, and 8,544 State/UT Acts",
      url: `${SITE}/corpus/`,
      keywords: ["BNS", "BNSS", "BSA", "IPC", "CrPC", "Central Acts", "State Acts"],
      license: "CC-BY-4.0",
      creator: { "@id": ORG_ID },
    },
    {
      "@type": "Dataset",
      name: "12 Financial & Sectoral Regulators",
      description: "12,152 regulatory instruments across SEBI, RBI, MCA, CBIC, TRAI, IRDAI, and more",
      url: `${SITE}/corpus/`,
      keywords: ["SEBI", "RBI", "CBIC", "Regulators", "Circulars"],
      license: "CC-BY-4.0",
      creator: { "@id": ORG_ID },
    },
    {
      "@type": "Dataset",
      name: "Supreme Court of India Judgments",
      description: "38,292 Supreme Court judgments, citation graph edges, and AWS Open Data PDFs",
      url: `${SITE}/corpus/`,
      keywords: ["Supreme Court", "INSC", "AIR", "SCC", "Case Law"],
      license: "CC-BY-4.0",
      creator: { "@id": ORG_ID },
    },
    {
      "@type": "Dataset",
      name: "25 High Courts Judgments & Orders",
      description: "20.7M+ High Court orders/metadata in DuckDB ZSTD Parquet plus hot reportable full text and on-demand PDFs",
      url: `${SITE}/corpus/`,
      keywords: ["High Court", "Neutral Citation", "CNR", "eCourts"],
      license: "CC-BY-4.0",
      creator: { "@id": ORG_ID },
    },
    {
      "@type": "Dataset",
      name: "15 Indian Tribunals & Quasi-Judicial Forums",
      description: "813,168 matters across ITAT, NCLT, NGT, SAT, CAT, CESTAT, DRT, CCI, and IBBI",
      url: `${SITE}/corpus/`,
      keywords: ["Tribunals", "NCLT", "ITAT", "NGT", "SAT"],
      license: "CC-BY-4.0",
      creator: { "@id": ORG_ID },
    },
    {
      "@type": "Dataset",
      name: "DDL Indian Judicial District Court Dataset (2010–2018)",
      description: "District Court metadata schema (Non-Commercial; defined in sources.lock.yaml and excluded under NYAYA_COMMERCIAL_SAFE_ONLY)",
      url: `${SITE}/corpus/`,
      keywords: ["District Courts", "DDL", "eCourts", "Non-Commercial"],
      license: "CC-BY-NC-SA-4.0",
      creator: { "@id": ORG_ID },
    },
    {
      "@type": "Dataset",
      name: "ILDC Supreme Court Research Annotations",
      description: "SC outcome and explanation research annotations (Non-Commercial; defined in sources.lock.yaml and excluded under NYAYA_COMMERCIAL_SAFE_ONLY)",
      url: `${SITE}/corpus/`,
      keywords: ["ILDC", "Supreme Court", "Non-Commercial"],
      license: "CC-BY-NC-4.0",
      creator: { "@id": ORG_ID },
    },
  ],
};

export const citationsSchema = {
  "@context": "https://schema.org",
  "@type": ["TechArticle", "HowTo"],
  name: "Nyaya Citation System",
  description:
    "How Nyaya parses, resolves, audits statutory validity, and verifies CNR, INSC, HC Neutral, AIR, SCC, and statutory citations",
  url: `${SITE}/citations/`,
  publisher: { "@id": ORG_ID },
  about: {
    "@type": "Thing",
    name: "Indian Legal Citation Format",
    description: "Standardized citation formats for Indian statutes, regulations, and courts",
  },
  step: [
    {
      "@type": "HowToStep",
      position: 1,
      name: "Parse",
      text: "Priority-ordered parser extracts CNR, INSC, HC Neutral, AIR, SCC, SCR, CPC Orders/Rules, and statutory sub-sections.",
      itemListElement: {
        "@type": "HowToDirection",
        text: "Parse \"2024:DHC:1234\" → judgment::HC_NEUTRAL::2024:DHC:1234",
      },
    },
    {
      "@type": "HowToStep",
      position: 2,
      name: "Resolve",
      text: "Exact canonical tuple lookup across SQLite and Bucket indices without bare first-number guessing.",
      itemListElement: {
        "@type": "HowToDirection",
        text: "resolve(\"IEA s.65B(4)\") → EvidenceAct 65B (repealed → BSA 63)",
      },
    },
    {
      "@type": "HowToStep",
      position: 3,
      name: "Audit & Frame",
      text: "Attaches StatutoryValidity status, raw citation-graph counts (unverified_automated), and OWASP <corpus_text> tags.",
      itemListElement: {
        "@type": "HowToDirection",
        text: "status=\"VERIFIED_STRUCK_DOWN_OR_READ_DOWN\" (IT Act s.66A)",
      },
    },
    {
      "@type": "HowToStep",
      position: 4,
      name: "Verify & Link",
      text: "Optional verbatim quote check against AWS S3 PDFs via pypdfium2 and provenance badge rendering.",
      itemListElement: {
        "@type": "HowToDirection",
        text: "→ Source card: 2017 INSC 801 · CC-BY-4.0",
      },
    },
  ],
};

export const architectureSchema = {
  "@context": "https://schema.org",
  "@type": ["SoftwareApplication", "APIReference"],
  name: "Nyaya Architecture",
  description:
    "Database-free pure-lexical legal MCP pipeline: SQLite FTS5, LanceDB BM25, DuckDB ZSTD Parquet, direct S3 pypdfium2, 12 MCP tools, 8 resources, 4 prompts, and optional /chat.",
  url: `${SITE}/architecture/`,
  publisher: { "@id": ORG_ID },
  applicationCategory: "DeveloperApplication",
  operatingSystem: "Linux, macOS, Windows",
  featureList: [
    "QueryAnalyzer with Indian statute aliases and IPC/CrPC/IEA ↔ BNS/BNSS/BSA expansion",
    "Baked read-only SQLite FTS5 + Citation Graph + Concordance core",
    "LanceDB native Rust BM25 FTS tables and DuckDB ZSTD Parquet on Railway Storage Bucket",
    "Direct AWS Open Data S3 PDF extraction via Apache-2.0 pypdfium2 + 256 MB disk zstd LRU cache",
    "OWASP LLM01 <corpus_text> framing and s. 228A / BNS s. 72 exclusions",
    "12 read-only MCP tools, 8 nyaya:// resources, and 4 prompts over uvx stdio or HTTP /mcp",
  ],
  provider: { "@id": ORG_ID },
  softwareVersion: API_VERSION,
  releaseNotes: "https://github.com/paragmraw/nyaya/releases",
};

export const faqSchema = {
  "@context": "https://schema.org",
  "@type": "FAQPage",
  mainEntity: [
    {
      "@type": "Question",
      name: "Does Nyaya automatically determine whether a judgment is overruled or good law?",
      acceptedAnswer: {
        "@type": "Answer",
        text: "No. trace_precedent and verify_citations return raw citation-graph edges, coram bench-strength comparisons, and quoting excerpts with precedent_status=\"unverified_automated\". Nyaya does not perform automated NLP Shepardizing; always verify currency before filing.",
      },
    },
    {
      "@type": "Question",
      name: "How does Nyaya handle scanned or image-only court PDFs?",
      acceptedAnswer: {
        "@type": "Answer",
        text: "Direct AWS Open Data PDFs are extracted on demand with Apache-2.0 pypdfium2. Documents yielding fewer than 50 characters per page (common in pre-2010 scanned High Court orders) are rejected as scanned_image_only (text_unavailable_metadata_only) rather than returning garbled OCR output.",
      },
    },
    {
      "@type": "Question",
      name: "Can commercial law firms use Nyaya without non-commercial dataset contamination?",
      acceptedAnswer: {
        "@type": "Answer",
        text: "Yes. Nyaya code is Apache-2.0 and its legislation, regulator, tribunal, SC, and HC packs are CC-BY-4.0 / Apache-2.0 / s.52(1)(q). Bundled DDL 81M District metadata (CC-BY-NC-SA-4.0) and ILDC (CC-BY-NC-4.0) are strictly tagged commercial_safe=false and excluded whenever NYAYA_COMMERCIAL_SAFE_ONLY=1 or commercial_safe_only=true is set.",
      },
    },
    {
      "@type": "Question",
      name: "Are sealed orders, s. 228A victim details, or commercial headnotes included?",
      acceptedAnswer: {
        "@type": "Answer",
        text: "No. In compliance with EBC v. D.B. Modak and Indian Copyright Act s. 52(1)(q), proprietary reporter headnotes are excluded, and sealed, in-camera, or s. 228A / BNS s. 72 / POCSO protected orders are blocked at ingestion.",
      },
    },
    {
      "@type": "Question",
      name: "Is my conversation stored anywhere?",
      acceptedAnswer: {
        "@type": "Answer",
        text: "Only in your browser tab. The transcript lives in sessionStorage under nyaya.chat.v1 (and the composer draft under nyaya.chat.draft) and is cleared when the tab closes or via the New chat button. The server holds no conversation database; chat logs carry request ids and message lengths, not message text. Your message is, however, sent to NVIDIA's hosted model for processing — retention there is governed by NVIDIA's terms. See the Privacy page for details.",
      },
    },
    {
      "@type": "Question",
      name: "How often is the corpus updated?",
      acceptedAnswer: {
        "@type": "Answer",
        text: "The Supreme Court and High Court judgment packs and the central legislation deltas follow a daily scheduled ingest; other packs refresh with their upstream releases. Updates ship as versioned corpus releases after an 8-suite verification gate — the current published baseline is corpus-v2026.11.25-p1. Coverage is a release snapshot, not a real-time feed.",
      },
    },
    {
      "@type": "Question",
      name: "Do I need an account or an API key to use Nyaya?",
      acceptedAnswer: {
        "@type": "Answer",
        text: "No. There are no accounts or sign-in anywhere in the product, and local MCP use needs no LLM or embedding keys — retrieval is purely lexical over local indexes. The hosted chat uses the server's own model key; you never share credentials with the service.",
      },
    },
    {
      "@type": "Question",
      name: "What are the usage limits?",
      acceptedAnswer: {
        "@type": "Answer",
        text: "Per client IP, per minute: 15 chat turns, 120 REST requests, 30 MCP calls, and 20 analytics requests, with a 1 MB request-body cap and a 4000-character limit per chat message. Exceeding a rate limit returns HTTP 429 — retry after waiting.",
      },
    },
  ],
};
