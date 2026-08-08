"use client";

import McpConfig from "@/components/McpConfig";
import PipelineStage from "@/components/PipelineStage";
import { useTools, FLOW, INFRA, PLANNED, SERVICES, TODAY, type ToolsResponse } from "@/lib";

// Empty fallback so the page renders a sensible state while the tools list
// is loading instead of flashing "No tools registered."
const TOOLS_FALLBACK: ToolsResponse = { items: [], total: 0 };

export default function ArchitecturePage() {
  const { data, error, isLoading } = useTools(TOOLS_FALLBACK);
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