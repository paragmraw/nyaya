import type { Metadata } from "next";
import Breadcrumb from "@/components/Breadcrumb";
import McpConfig from "@/components/McpConfig";
import McpToolsList from "@/components/McpToolsList";
import PipelineStage from "@/components/PipelineStage";
import { FLOW, INFRA, PLANNED, SERVICES, TODAY } from "@/lib";
import StructuredData, { architectureSchema } from "@/components/StructuredData";
import { API_VERSION, pageOpenGraph } from "@/lib/site";

export const metadata: Metadata = {
  title: "Architecture",
  description:
    "How Nyaya v2.0 works: database-free pure-lexical pipeline (SQLite FTS5 + LanceDB BM25 + DuckDB ZSTD Parquet + direct S3 pypdfium2) serving 12 MCP tools, 8 resources, 4 prompts, and optional /chat.",
  openGraph: pageOpenGraph({
    title: "Nyaya v2.0 Architecture - Database-Free Pure-Lexical Legal MCP",
    description:
      "Baked SQLite FTS5 core, LanceDB BM25 & DuckDB ZSTD Parquet on Railway Storage Bucket, direct AWS S3 pypdfium2, and dual uvx stdio + HTTP transport.",
  }),
};

export default function ArchitecturePage() {
  return (
    <>
      <StructuredData data={architectureSchema} />
      <main id="content" className="page">
        <Breadcrumb />
        <div className="container">
          {/* hero */}
          <section>
            <p className="eyebrow">Architecture · v{API_VERSION}</p>
            <h1>Pure lexical, database-free</h1>
            <p className="lead lead-wide">
              Baked SQLite FTS5 core + LanceDB native BM25 + DuckDB ZSTD Parquet + direct S3 <code>pypdfium2</code> — serving 12 MCP tools, 8 <code>nyaya://</code> resources, 4 practitioner prompts, REST APIs, and optional <code>/chat</code> inside a single &lt;1 GB RAM container.
            </p>
          </section>

          {/* pipeline */}
          <section className="section">
            <div className="section-head">
              <h2>Pipeline</h2>
              <p className="sec-desc">Click any stage to expand storage, security, and verification details.</p>
            </div>
            <PipelineStage />
          </section>

          {/* stack */}
          <section className="section">
            <div className="section-head">
              <h2>Stack</h2>
              <p className="sec-desc">Embedded &amp; object-storage components: zero managed database servers, zero dense-vector dependencies in v2.0, and zero API keys required for MCP.</p>
            </div>
            <div className="stack-grid">
              <div className="stack-col">
                <div className="sx-head">Retrieval &amp; Storage Tiers</div>
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
                <div className="sx-head">Runtime &amp; Governance</div>
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
              <p className="sec-desc">From query or draft brief to verified citation output, step by step.</p>
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
              <h2>MCP server (stdio &amp; Streamable HTTP)</h2>
              <p className="sec-desc">
                Connect Claude Desktop, Cursor, opencode, or Antigravity locally over <code>stdio</code> via <code>uvx nyaya-mcp</code> (zero secrets, private client files stay on disk) or over Streamable HTTP at <code>/mcp</code>.
              </p>
            </div>

            <McpConfig variant="block" />

            <McpToolsList />
          </section>

          {/* openness / roadmap */}
          <section className="section">
            <div className="section-head">
              <h2>Openness &amp; roadmap</h2>
              <p className="sec-desc">Apache-2.0 server and ingestion tooling with clear licensing boundaries across public and academic datasets.</p>
            </div>
            <div className="openness">
              <div className="openness-card">
                <div className="oc-title">Live in v{API_VERSION}</div>
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
    </>
  );
}
