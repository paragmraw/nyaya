"use client";

// ChatPanel is the only genuinely client-only block on the home page: it
// opens the SSE stream, reads sessionStorage and renders only after mount,
// so it keeps `ssr: false` (not allowed to move to a Server Component).
// CapTable and McpConfig are SSR-safe (no mount-time browser state) and are
// now imported directly by app/page.tsx so their markup is server-rendered.
import dynamic from "next/dynamic";

const ChatPanel = dynamic(() => import("@/components/ChatPanel").then((mod) => mod.default), {
  ssr: false,
  loading: () => <div className="chat-shell" style={{ height: "100%", minHeight: 300 }} />,
});

export { ChatPanel };