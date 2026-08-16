"use client";

import dynamic from "next/dynamic";

const McpToolsList = dynamic(() => import("@/components/McpToolsList").then((mod) => mod.default), {
  ssr: false,
  loading: () => <p className="meta">Loading MCP tools…</p>,
});

export { McpToolsList };