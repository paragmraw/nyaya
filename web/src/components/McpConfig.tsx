"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

type Variant = "promo" | "block";

function buildMcpJson(origin: string): string {
  // Streamable HTTP form — the real config for the deployed HTTP MCP server.
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

const McpIcon = () => (
  <svg fill="currentColor" fillRule="evenodd" viewBox="0 0 24 24" width="1em" height="1em" style={{ flex: "none", lineHeight: 1 }} aria-hidden="true">
    <title>ModelContextProtocol</title>
    <path d="M15.688 2.343a2.588 2.588 0 00-3.61 0l-9.626 9.44a.863.863 0 01-1.203 0 .823.823 0 010-1.18l9.626-9.44a4.313 4.313 0 016.016 0 4.116 4.116 0 011.204 3.54 4.3 4.3 0 013.609 1.18l.05.05a4.115 4.115 0 010 5.9l-8.706 8.537a.274.274 0 000 .393l1.788 1.754a.823.823 0 010 1.18.863.863 0 01-1.203 0l-1.788-1.753a1.92 1.92 0 010-2.754l8.706-8.538a2.47 2.47 0 000-3.54l-.05-.049a2.588 2.588 0 00-3.607-.003l-7.172 7.034-.002.002-.098.097a.863.863 0 01-1.204 0 .823.823 0 010-1.18l7.273-7.133a2.47 2.47 0 00-.003-3.537z" />
    <path d="M14.485 4.703a.823.823 0 000-1.18.863.863 0 00-1.204 0l-7.119 6.982a4.115 4.115 0 000 5.9 4.314 4.314 0 006.016 0l7.12-6.982a.823.823 0 000-1.18.863.863 0 00-1.204 0l-7.119 6.982a2.588 2.588 0 01-3.61 0 2.47 2.47 0 010-3.54l7.12-6.982z" />
  </svg>
);

const CopyIcon = () => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
    <rect x="9" y="9" width="13" height="13" rx="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
  </svg>
);

export default function McpConfig({ variant = "promo" }: { variant?: Variant }) {
  // Render with the placeholder on the first client render (matches SSR
  // output) to avoid hydration mismatches, then update to the real
  // window.location.origin in a useEffect after hydration completes.
  // Fallback uses NEXT_PUBLIC_MCP_URL (build-time).
  const [origin, setOrigin] = useState<string | null>(null);
  useEffect(() => {
    setOrigin(typeof window !== "undefined" ? window.location.origin : null);
  }, []);
  const [copied, setCopied] = useState(false);

  // In production, fail-closed if the origin is not HTTPS (prevent the user
  // from connecting their editor to an insecure MCP endpoint).
  const json = useMemo(() => {
    const fallback = process.env.NEXT_PUBLIC_MCP_URL ?? "https://nyaya.example";
    const effectiveOrigin = origin ?? fallback;
    if (
      process.env.NODE_ENV === "production" &&
      effectiveOrigin.startsWith("http:") &&
      !effectiveOrigin.includes("localhost")
    ) {
      return JSON.stringify(
        { error: "INSECURE_ORIGIN", message: "MCP URL must be HTTPS in production." },
        null,
        2,
      );
    }
    return buildMcpJson(effectiveOrigin);
  }, [origin]);

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

  // Render the JSON with light syntax highlighting: keys in --accent,
  // string values in --muted.
  const rendered = useMemo(() => {
    return json.split("\n").map((line, i) => {
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
        <div className="mcp-header">
          <span className="mcp-ico" aria-hidden="true"><McpIcon /></span>
          <div className="mcp-meta">
            <div className="mcp-title-row">
              <span className="mcp-title">MCP server</span>
              <span className="mcp-tag">plug in</span>
            </div>
            <p className="mcp-desc">
              Bring Nyaya&apos;s corpus into your editor — retrieve cited answers to{" "}
              <b>Constitution</b>, <b>CrPC</b> and <b>BNS</b> directly inside your workflow.
            </p>
          </div>
        </div>
        <div className="mcp-config">
          <div className="mcp-config-head">
            <span className="mcp-filename">mcp.json</span>
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