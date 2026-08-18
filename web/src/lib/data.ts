// Hardcoded data arrays extracted from pages and components.
//
// These are static product copy and curated metadata — not live data — so
// they live here typed and imported where needed.

import type {
  Cap,
  CuratedRow,
  FlowStep,
  FormatCard,
  Limit,
  OpennessItem,
  PipeStep,
  StackRow,
} from "./types";

// ─── architecture/page.tsx ────────────────────────────────────────
export const SERVICES: StackRow[] = [
  { name: "Embedding model", role: "Converts text queries and corpus passages into dense vectors for semantic search.", tool: "nvidia/nemotron-3-embed-1b" },
  { name: "Vector store", role: "Stores and retrieves passage embeddings with cosine similarity search at scale.", tool: "pgvector" },
  { name: "Reranker", role: "Reorders top-k retrieved passages by relevance to the specific query using a cross-encoder model.", tool: "llama-nemotron-rerank-1b-v2" },
  { name: "LLM", role: "Drafts the cited answer from retrieved context. Constrained to cite only from the retrieved set.", tool: "Nemotron-3.5 Lightning 30B" },
  { name: "Citation resolver", role: "Parses a citation string and fetches the matching provision from the corpus.", tool: "nyaya/resolve_citation" },
];

export const INFRA: StackRow[] = [
  { name: "Hosting", role: "Web app + API + MCP server, served from one container. Deploy region chosen at deploy time.", tool: "Railway / Docker" },
  { name: "Refresh jobs", role: "Ingestion is manual via the nyaya-ingest CLI. Automated scheduled crawlers are planned.", tool: "nyaya-ingest CLI" },
  { name: "Observability", role: "Latency, retrieval quality, and error tracking.", tool: "[tool: TBD]" },
  { name: "Object storage", role: "Stores raw PDFs and parsed text from sources for audit and re-indexing.", tool: "[planned]" },
  { name: "Search index", role: "Full-text fallback alongside vector search for exact-match section/article lookups.", tool: "Postgres FTS" },
];

export const FLOW: FlowStep[] = [
  { num: "1", text: "User submits a natural-language legal question through the web chat or an MCP client." },
  { num: "2", text: "Supervisor LLM reasons briefly about which corpus tools to call, then emits all tool calls in one message for parallel execution." },
  { num: "3", text: "MCP tools (hybrid_search, get_section, get_article, …) retrieve matching provisions and judgments from the indexed corpus in parallel." },
  { num: "4", text: "Synthesis LLM composes the final answer using only the retrieved tool results, with inline [[act: X, ref: Y]] citations." },
  { num: "5", text: "Citation resolver parses each reference and fetches the matching provision from the corpus before the answer is displayed." },
];

export const TODAY: OpennessItem[] = [
  { on: true, text: "MCP server (HTTP endpoint at /mcp)", meta: "live" },
  { on: true, text: "Open-source MCP server (Apache-2.0)", meta: "live" },
  { on: true, text: "Self-host recipe (Docker + docker-compose)", meta: "live" },
  { on: true, text: "Web chat + citation engine (Nemotron-3.5 Lightning)", meta: "live" },
  { on: false, text: "Corpus data dumps", meta: "closed" },
];

export const PLANNED: OpennessItem[] = [
  { on: false, text: "Open corpus data (CC-BY)", meta: "planned" },
  { on: false, text: "Citation resolver API (public)", meta: "planned" },
  { on: false, text: "Reranker & overruled-status check", meta: "planned" },
  { on: false, text: "Automated refresh jobs", meta: "planned" },
];

// ─── citations/page.tsx ────────────────────────────────────────────
export const FORMAT_CARDS: FormatCard[] = [
  { type: "Constitution", format: "Art. 21, Constitution of India", desc: "Article number from the Constitution of India, 1950, with the full title.", url: "https://github.com/Vikhram-S/IndianConstitution", urlLabel: "→ Vikhram-S/IndianConstitution" },
  { type: "Statute (BNS / BNSS / BSA)", format: "§ 358, BNS 2023", desc: "Section number from the named statute, with the year and short title.", url: "https://prsindia.org", urlLabel: "→ prsindia.org (CC BY 4.0)" },
  { type: "Case law", format: "K.S. Puttaswamy v. Union of India, (2017) 10 SCC 1", desc: "Party names, neutral or SCC citation, and volume/journal reference.", url: "https://indiankanoon.org", urlLabel: "→ indiankanoon.org" },
];

export const PIPE_STEPS: PipeStep[] = [
  { num: "01", title: "Parse", desc: "Extract citation strings from the draft answer: article numbers, section references, case names.", eg: "\"Art. 21\" → {type: constitution, art: 21}" },
  { num: "02", title: "Match", desc: "Look up each parsed citation in the indexed corpus to confirm it exists and the text matches.", eg: "match(\"S. 41A\", \"CrPC 1973\") → found, section_text" },
  { num: "03", title: "Fetch", desc: "Pull the full provision or judgment text from the corpus so the citation can be displayed inline.", eg: "fetch(\"Art. 21\") → Constitution article text" },
  { num: "04", title: "Display", desc: "Render the citation as a link the user can click to open the source card with the full text and provenance.", eg: "→ Source card: Constitution of India, Art. 21" },
];

export const LIMITS: Limit[] = [
  { mark: "01", title: "Unreported judgments", sub: "Orders and judgments not published in a reported journal are not in the corpus. Nyaya will say it cannot find a citation rather than invent one." },
  { mark: "02", title: "Subordinate legislation & rules", sub: "Notifications, rules, and subordinate legislation under major statutes are not yet indexed. Coming per the corpus roadmap." },
  { mark: "03", title: "Pre-1950 Privy Council decisions", sub: "Judgments of the Privy Council (the apex court for British India until 1950) are not yet indexed. Planned." },
  { mark: "04", title: "Overruled / good-law status", sub: "Nyaya does not yet track whether a judgment has been overruled or modified by a later bench. Always confirm currency before filing." },
];

// ─── CapTable.tsx ──────────────────────────────────────────────────
export const CAPS: Cap[] = [
  { code: "CON-01", name: "Constitutional lookup", desc: "Parts I–XXII, Fundamental Rights & DPSP, with amendment history.", coverage: "395 arts" },
  { code: "CRP-02", name: "CrPC procedure tracer", desc: "Bail, FIR, charge, trial stages; maps a query to the exact section.", coverage: "484 secs" },
  { code: "PEN-03", name: "Penal code (IPC / BNS)", desc: "Legacy IPC + new BNS, 2023: offence → section → punishment range.", coverage: "511 / 358" },
  { code: "PRC-04", name: "Precedent retrieval", desc: "Supreme Court & High Court rulings, cited inline with neutral citations.", coverage: "5 curated" },
  { code: "DFT-05", name: "Drafting assist", desc: "Notices, affidavits, vakalatnama skeletons from a one-line brief.", coverage: "planned" },
  { code: "CTR-06", name: "Citation resolver", desc: "Paste a citation; resolve it to the matching provision in the corpus.", coverage: "static" },
];

// ─── CorpusTable.tsx ──────────────────────────────────────────────
export const CURATED: CuratedRow[] = [
  { short_name: "Constitution", name: "Constitution of India", type: "Constitution", coverage: "All 395 articles + 12 schedules", status: "live", fallback_date: "" },
  { short_name: "BNS", name: "Bharatiya Nyaya Sanhita, 2023", type: "Statute", coverage: "All 358 sections", status: "live", fallback_date: "" },
  { short_name: "BNSS", name: "Bharatiya Nagarik Suraksha Sanhita, 2023", type: "Statute", coverage: "All 531 sections", status: "live", fallback_date: "" },
  { short_name: "BSA", name: "Bharatiya Sakshya Adhiniyam, 2023", type: "Statute", coverage: "All sections (replaces Evidence Act)", status: "live", fallback_date: "" },
  { short_name: "IPC", name: "Indian Penal Code, 1860", type: "Statute", coverage: "All 511 sections (legacy reference)", status: "live", fallback_date: "" },
  { short_name: "CrPC", name: "Code of Criminal Procedure, 1973", type: "Statute", coverage: "All 484 sections (legacy reference)", status: "live", fallback_date: "" },
  { short_name: "EvidenceAct", name: "Indian Evidence Act, 1872", type: "Statute", coverage: "All sections (legacy reference)", status: "live", fallback_date: "" },
  { short_name: "CPC", name: "Code of Civil Procedure, 1908", type: "Statute", coverage: "All sections (legacy reference)", status: "live", fallback_date: "" },
  { short_name: "Companies", name: "Companies Act, 2013", type: "Statute", coverage: "Commercial statute", status: "live", fallback_date: "" },
  { short_name: "IGST", name: "Integrated Goods and Services Tax Act, 2017", type: "Statute", coverage: "Commercial statute", status: "live", fallback_date: "" },
  { short_name: "CGST", name: "Central Goods and Services Tax Act, 2017", type: "Statute", coverage: "Commercial statute", status: "live", fallback_date: "" },
  { short_name: "ITAct", name: "Information Technology Act, 2000", type: "Statute", coverage: "Commercial statute", status: "live", fallback_date: "" },
  { short_name: "Arbitration", name: "Arbitration and Conciliation Act, 1996", type: "Statute", coverage: "Commercial statute", status: "live", fallback_date: "" },
  { short_name: "ConsumerProtection", name: "Consumer Protection Act, 2019", type: "Statute", coverage: "Commercial statute", status: "live", fallback_date: "" },
  { short_name: "", name: "Supreme Court landmark judgments", type: "Case law", coverage: "5 curated judgments (Kesavananda, Maneka, Puttaswamy, Shah Bano, Navtej Johar)", status: "live", fallback_date: "2026-07-01" },
  { short_name: "", name: "High Court reported judgments", type: "Case law", coverage: "Planned", status: "coming", fallback_date: "" },
  { short_name: "", name: "Subordinate legislation & rules", type: "Regulation", coverage: "Planned", status: "coming", fallback_date: "" },
  { short_name: "", name: "Pre-1950 Privy Council decisions", type: "Case law", coverage: "Planned", status: "coming", fallback_date: "" },
];