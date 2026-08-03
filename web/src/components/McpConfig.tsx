"use client";

import { useCallback, useMemo, useState } from "react";

type Variant = "promo" | "block";

function buildMcpJson(origin: string): string {
  // Streamable HTTP form — the real config for the deployed HTTP MCP server.
  // Replaces the placeholder `npx -y @nyaya/mcp` from the design export.
  return JSON.stringify(
    {
      mcpServers: {
        nyaya: {
          url: `${origin}/mcp`,
          transport: "http",
        },
      },
    },
    null,
    2,
  );
}

function fallbackCopy(text: string) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.style.position = "fixed";
  ta.style.opacity = "0";
  document.body.appendChild(ta);
  ta.select();
  try {
    document.execCommand("copy");
  } catch {
    // ignore
  }
  document.body.removeChild(ta);
}

const PlugIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <path d="M12 2v4" /><path d="M12 18v4" /><path d="m4.9 4.9 2.8 2.8" /><path d="m16.3 16.3 2.8 2.8" /><path d="M2 12h4" /><path d="M18 12h4" /><path d="m4.9 19.1 2.8-2.8" /><path d="m16.3 7.7 2.8-2.8" />
  </svg>
);

const CopyIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <rect x="9" y="9" width="13" height="13" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
  </svg>
);

export default function McpConfig({ variant = "promo" }: { variant?: Variant }) {
  // Read window.location.origin once, lazily — avoids setState-in-effect
  // (the new react-hooks/set-state-in-effect rule) and avoids an extra render.
  // SSR renders with the placeholder; the client hydrates with the real origin.
  const [origin] = useState<string | null>(() =>
    typeof window !== "undefined" ? window.location.origin : null,
  );
  const [copied, setCopied] = useState(false);

  const json = useMemo(() => buildMcpJson(origin ?? "https://nyaya.example"), [origin]);

  const onCopy = useCallback(async () => {
    const text = json;
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        fallbackCopy(text);
      }
    } catch {
      fallbackCopy(text);
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  }, [json]);

  // Render the JSON with light syntax highlighting (keys accent, strings muted).
  const rendered = useMemo(() => {
    return json.split("\n").map((line, i) => {
      // crude but stable highlight: keys in --accent, string values in --muted
      const parts: React.ReactNode[] = [];
      const re = /("[^"]+")(\s*:)?(\s*("[^"]*"))?/g;
      let last = 0;
      let m: RegExpExecArray | null;
      let k = 0;
      while ((m = re.exec(line)) !== null) {
        if (m.index > last) parts.push(<span key={`t${k++}`}>{line.slice(last, m.index)}</span>);
        parts.push(<span className="k" key={`k${k++}`}>{m[1]}</span>);
        if (m[2]) parts.push(<span key={`c${k++}`}>{m[2]}</span>);
        if (m[4]) parts.push(<span className="s" key={`s${k++}`}>{m[4]}</span>);
        last = m.index + m[0].length;
      }
      if (last < line.length) parts.push(<span key={`r${k++}`}>{line.slice(last)}</span>);
      return (
        <span key={i}>
          {parts}
          {"\n"}
        </span>
      );
    });
  }, [json]);

  if (variant === "promo") {
    return (
      <div className="mcp-card" style={{ marginTop: "var(--gap-md)", flexShrink: 0 }}>
        <div className="mcp-head">
          <span className="mcp-ico" aria-hidden="true"><PlugIcon /></span>
          <span className="mcp-body">
            <span className="mcp-title">MCP server <span className="mcp-tag">plug in</span></span>
            <span className="mcp-desc">
              Bring Nyaya&apos;s corpus into your editor — retrieve cited answers to{" "}
              <b>Constitution</b>, <b>CrPC</b> and <b>BNS</b> directly inside your workflow.
            </span>
            <span className="mcp-clients">
              <span className="c">Claude</span><span className="c">Cursor</span><span className="c">Windsurf</span>
            </span>
          </span>
        </div>
        <div className="mcp-config">
          <div className="mcp-config-head">
            <span>mcp.json</span>
            <button className={`mcp-copy${copied ? " copied" : ""}`} type="button" onClick={onCopy} aria-label="Copy MCP config JSON">
              <CopyIcon />
              <span className="cp-label">{copied ? "Copied" : "Copy"}</span>
            </button>
          </div>
          <pre className="mcp-json">{rendered}</pre>
        </div>
      </div>
    );
  }

  // block variant (architecture page)
  return (
    <div className="mcp-config-block">
      <div className="mcp-config-head">
        <span>mcp.json</span>
        <button className={`mcp-copy${copied ? " copied" : ""}`} type="button" onClick={onCopy} aria-label="Copy MCP config JSON">
          <CopyIcon />
          <span className="cp-label">{copied ? "Copied" : "Copy"}</span>
        </button>
      </div>
      <pre className="mcp-json" id="mcp-json">{rendered}</pre>
    </div>
  );
}