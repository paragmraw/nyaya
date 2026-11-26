"use client";

import { MCP_PROMPTS, MCP_RESOURCES, MCP_TOOLS_FALLBACK, useTools } from "@/lib";

export default function McpToolsList() {
  const { data, error, isLoading } = useTools(MCP_TOOLS_FALLBACK);
  const tools = data?.items && data.items.length > 0 ? data.items : MCP_TOOLS_FALLBACK.items;
  const resourcesCount = data?.resources_count ?? MCP_RESOURCES.length;
  const promptsCount = data?.prompts_count ?? MCP_PROMPTS.length;

  return (
    <div className="mcp-tools-wrap" style={{ marginTop: "var(--gap-lg)" }}>
      <div className="mcp-tools-header">
        <h3 className="mcp-tools-head">
          MCP v2.1 surface ({tools.length} tools · {resourcesCount} resources · {promptsCount} prompts)
        </h3>
        {isLoading && <span className="meta">Checking live /api/tools…</span>}
        {error && !isLoading && (
          <span className="meta">Showing canonical v2.1 specification (live /api/tools offline).</span>
        )}
      </div>

      <div className="tools">
        {tools.map((t) => (
          <div className="tool-row" key={t.name}>
            <div className="tr-left">
              <span className="tr-name">{t.name}</span>
              {typeof t.open_world === "boolean" && (
                <span className={`badge-commercial ${t.open_world ? "badge-bucket" : "badge-safe"}`}>
                  {t.open_world ? "Bucket / S3" : "Local SQLite"}
                </span>
              )}
            </div>
            <div>
              <div className="tr-desc">
                {t.description.split("\n")[0].slice(0, 220)}
                {t.description.length > 220 ? "…" : ""}
              </div>
            </div>
          </div>
        ))}
      </div>

      <div className="mcp-subgrid">
        <div>
          <h4 className="mcp-subhead">{MCP_RESOURCES.length} nyaya:// resources</h4>
          <div className="tools">
            {MCP_RESOURCES.map((r) => (
              <div className="tool-row" key={r.uri}>
                <span className="tr-name">{r.uri}</span>
                <div className="tr-desc">{r.desc}</div>
              </div>
            ))}
          </div>
        </div>
        <div>
          <h4 className="mcp-subhead">{MCP_PROMPTS.length} practitioner prompts</h4>
          <div className="tools">
            {MCP_PROMPTS.map((p) => (
              <div className="tool-row" key={p.name}>
                <span className="tr-name">{p.name}</span>
                <div className="tr-desc">{p.desc}</div>
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
