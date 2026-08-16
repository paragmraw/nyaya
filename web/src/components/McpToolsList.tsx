"use client";

import { useTools, type ToolsResponse } from "@/lib";

const TOOLS_FALLBACK: ToolsResponse = { items: [], total: 0 };

export default function McpToolsList() {
  const { data, error, isLoading } = useTools(TOOLS_FALLBACK);
  const tools = data?.items ?? [];

  return (
    <div className="mcp-tools-wrap" style={{ marginTop: "var(--gap-md)" }}>
      <h3 className="mcp-tools-head">Supported MCP tools</h3>
      {isLoading ? (
        <p className="meta">Loading tools…</p>
      ) : error ? (
        <p className="meta">Live tool list unavailable; showing the server&apos;s advertised tools on connect.</p>
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
  );
}