"use client";

import McpConfig from "@/components/McpConfig";
import PipelineStage from "@/components/PipelineStage";
import { useTools } from "@/lib";

type StackRow = { name: string; role: string; tool: string };
const SERVICES: StackRow[] = [
  { name: "Embedding model", role: "Converts text queries and corpus passages into dense vectors for semantic search.", tool: "bge-large-en-v1.5" },
  { name: "Vector store", role: "Stores and retrieves passage embeddings with cosine similarity search at scale.", tool: "pgvector" },
  { name: "Reranker", role: "Reorders top-k retrieved passages by relevance to the specific query.", tool: "[planned]" },
  { name: "LLM", role: "Drafts the cited answer from retrieved context. Constrained to cite only from the retrieved set.", tool: "[model: TBD]" },
  { name: "Citation resolver", role: "Parses a citation string and fetches the matching provision from the corpus.", tool: "nyaya/resolve_citation" },
];
const INFRA: StackRow[] = [
  { name: "Hosting", role: "Web app + API + MCP server, served from one container. Deploy region chosen at deploy time.", tool: "Railway / Docker" },
  { name: "Refresh jobs", role: "Ingestion is manual via the nyaya-ingest CLI. Automated scheduled crawlers are planned.", tool: "[planned]" },
  { name: "Observability", role: "Latency, retrieval quality, and error tracking.", tool: "[tool: TBD]" },
  { name: "Object storage", role: "Stores raw PDFs and parsed text from sources for audit and re-indexing.", tool: "[planned]" },
  { name: "Search index", role: "Full-text fallback alongside vector search for exact-match section/article lookups.", tool: "Postgres FTS" },
];

type FlowStep = { num: string; text: React.ReactNode };
const FLOW: FlowStep[] = [
  { num: "1", text: "User submits a natural-language legal question through the web chat or MCP tool." },
  { num: "2", text: "Query is embedded and the vector store returns the top-k matching passages from the indexed corpus." },
  { num: "3", text: "Optional reranker reorders the results by relevance (planned — not yet implemented)." },
  { num: "4", text: "LLM drafts an answer using only the retrieved passages, with inline citations to articles, sections, or cases." },
  { num: "5", text: "Citation resolver parses each reference and fetches the matching provision from the corpus before the answer is displayed." },
];

type OpennessItem = { on: boolean; text: string; meta: string };
const TODAY: OpennessItem[] = [
  { on: true, text: "MCP server (HTTP endpoint at /mcp)", meta: "live" },
  { on: true, text: "Open-source MCP server (Apache-2.0)", meta: "live" },
  { on: true, text: "Self-host recipe (Docker + docker-compose)", meta: "live" },
  { on: false, text: "Web chat + citation engine", meta: "coming soon" },
  { on: false, text: "Corpus data dumps", meta: "closed" },
];
const PLANNED: OpennessItem[] = [
  { on: false, text: "Open corpus data (CC-BY)", meta: "planned" },
  { on: false, text: "Citation resolver API (public)", meta: "planned" },
  { on: false, text: "Reranker & overruled-status check", meta: "planned" },
  { on: false, text: "Automated refresh jobs", meta: "planned" },
];

export default function ArchitecturePage() {
  const { data, error, isLoading } = useTools();
  const tools = data?.items ?? [];

  return (
    <main className="page">
      <div className="container">
        {/* hero */}
        <section>
          <p className="eyebrow">Architecture</p>
          <h1>How Nyaya answers</h1>
          <p className="lead lead-wide">
            Query → retrieve → cite. A four-stage pipeline over an indexed legal corpus, exposed via web, API, and MCP.
          </p>
        </section>

        {/* pipeline */}
        <section className="section">
          <div className="section-head">
            <h2>Pipeline</h2>
            <p className="sec-desc">Click a stage to expand technical detail. Each stage is a discrete service with its own contract.</p>
          </div>
          <PipelineStage />
        </section>

        {/* stack */}
        <section className="section">
          <div className="section-head">
            <h2>Stack</h2>
            <p className="sec-desc">Services and infrastructure. Tool tags are generic roles, not vendor endorsements.</p>
          </div>
          <div className="stack-grid">
            <div className="stack-col">
              <div className="sx-head">Services</div>
              {SERVICES.map((s) => (
                <div className="stack-row" key={s.name}>
                  <div>
                    <div className="sr-name">{s.name}</div>
                    <div className="sr-role">{s.role}</div>
                  </div>
                  <span className="sr-tool">{s.tool}</span>
                </div>
              ))}
            </div>
            <div className="stack-col">
              <div className="sx-head">Infrastructure</div>
              {INFRA.map((s) => (
                <div className="stack-row" key={s.name}>
                  <div>
                    <div className="sr-name">{s.name}</div>
                    <div className="sr-role">{s.role}</div>
                  </div>
                  <span className="sr-tool">{s.tool}</span>
                </div>
              ))}
            </div>
          </div>
        </section>

        {/* data flow */}
        <section className="section">
          <div className="section-head">
            <h2>Data flow</h2>
            <p className="sec-desc">From question to cited answer, step by step. Each step links to the pipeline stage it belongs to.</p>
          </div>
          <div className="flow">
            {FLOW.map((f) => (
              <div className="flow-step" key={f.num}>
                <span className="fs-num">{f.num}</span>
                <span className="fs-text">{f.text}</span>
              </div>
            ))}
          </div>
        </section>

        {/* MCP server */}
        <section className="section">
          <div className="section-head">
            <h2>MCP server</h2>
            <p className="sec-desc">
              Nyaya ships an MCP server so Claude, Cursor, and Windsurf can query the corpus directly from your editor. Add this config to your client:
            </p>
          </div>

          <McpConfig variant="block" />

          <div className="mcp-tools-wrap" style={{ marginTop: "var(--gap-md)" }}>
            <h3 className="mcp-tools-head">Supported MCP tools</h3>
            {isLoading ? (
              <p className="meta">Loading tools…</p>
            ) : error ? (
              <p className="meta">Live tool list unavailable — showing the server&apos;s advertised tools on connect.</p>
            ) : tools.length === 0 ? (
              <p className="meta">No tools registered.</p>
            ) : (
              <div className="tools">
                {tools.map((t) => (
                  <div className="tool-row" key={t.name}>
                    <span className="tr-name">{t.name}</span>
                    <div>
                      <div className="tr-desc">
                        {t.description.split("\n")[0].slice(0, 180)}
                        {t.description.length > 180 ? "…" : ""}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            )}
          </div>
        </section>

        {/* openness / roadmap */}
        <section className="section">
          <div className="section-head">
            <h2>Openness &amp; roadmap</h2>
            <p className="sec-desc">What is open today and what is planned. Honest status — no fake repo links.</p>
          </div>
          <div className="openness">
            <div className="openness-card">
              <div className="oc-title">Today</div>
              <div className="oc-list">
                {TODAY.map((o) => (
                  <div className="oc-item" key={o.text}>
                    <span className={`oc-dot ${o.on ? "on" : "off"}`} />
                    <span className="oc-text">{o.text}</span>
                    <span className="oc-meta">{o.meta}</span>
                  </div>
                ))}
              </div>
            </div>
            <div className="openness-card">
              <div className="oc-title">Planned</div>
              <div className="oc-list">
                {PLANNED.map((o) => (
                  <div className="oc-item" key={o.text}>
                    <span className={`oc-dot ${o.on ? "on" : "off"}`} />
                    <span className="oc-text">{o.text}</span>
                    <span className="oc-meta">{o.meta}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}