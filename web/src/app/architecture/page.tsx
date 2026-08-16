import type { Metadata } from "next";
import { McpToolsList } from "@/components/ArchitecturePageClient";
import Breadcrumb from "@/components/Breadcrumb";
import McpConfig from "@/components/McpConfig";
import PipelineStage from "@/components/PipelineStage";
import { FLOW, INFRA, PLANNED, SERVICES, TODAY } from "@/lib";
import StructuredData, { architectureSchema } from "@/components/StructuredData";

export const metadata: Metadata = {
  title: "Architecture",
  description:
    "How Nyaya answers: a four-stage pipeline (query → retrieve → rerank → cite) exposed via web, API, and MCP. Stack: pgvector, bge-large-en-v1.5, Nemotron-3.5 Lightning.",
  openGraph: {
    title: "Nyaya Architecture - Retrieval-Grounded Legal AI Pipeline",
    description:
      "How Nyaya answers: a four-stage pipeline (query → retrieve → rerank → cite) exposed via web, API, and MCP. Stack: pgvector, bge-large-en-v1.5, Nemotron-3.5 Lightning.",
    type: "website",
  },
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
                Nyaya ships an MCP server so Claude, Cursor, and opencode can query the corpus directly from your editor. Add this config to your client:
              </p>
            </div>

            <McpConfig variant="block" />

            <McpToolsList />
          </section>

          {/* openness / roadmap */}
          <section className="section">
            <div className="section-head">
              <h2>Openness & roadmap</h2>
              <p className="sec-desc">What is open today and what is planned. Honest status. No fake repo links.</p>
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
    </>
  );
}