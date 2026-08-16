"use client";

import dynamic from "next/dynamic";

const CapTable = dynamic(() => import("@/components/CapTable").then((mod) => mod.default), {
  ssr: false,
  loading: () => <p className="meta">Loading capabilities…</p>,
});

const ChatPanel = dynamic(() => import("@/components/ChatPanel").then((mod) => mod.default), {
  ssr: false,
  loading: () => <div className="chat-shell" style={{ height: "100%", minHeight: 300 }} />,
});

const McpConfig = dynamic(() => import("@/components/McpConfig").then((mod) => mod.default), {
  ssr: false,
  loading: () => <p className="meta">Loading MCP config…</p>,
});

export { CapTable, ChatPanel, McpConfig };