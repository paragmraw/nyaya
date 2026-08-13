"use client";

import { useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import PersonIcon from "@mui/icons-material/Person";
import BalanceIcon from "@mui/icons-material/Balance";
import CitationChip from "./CitationChip";
import type { ChatMessage, ChatToolEvent } from "@/lib";

// Build a compact label for a tool chip, showing the most relevant arg.
function toolArgsLabel(name: string, args?: Record<string, unknown>): string {
  if (!args) return name;
  const act = args.act || args.act_short_name;
  const section = args.section_number || args.article_number || args.section;
  const query = args.query;
  if (act && section) return `${name}(${act} §${section})`;
  if (query) return `${name}("${String(query).slice(0, 30)}…")`;
  if (act) return `${name}(${act})`;
  if (section) return `${name}(§${section})`;
  return name;
}

// Map backend status codes to user-friendly phase labels.
function phaseLabel(status: string): string {
  const labels: Record<string, string> = {
    thinking: "Thinking…",
    analyzing: "Analysing your question…",
    searching: "Searching legal corpus…",
    composing: "Composing answer…",
  };
  return labels[status] ?? `${status}…`;
}

// Render a tool-result value as a compact, readable string. Long strings are
// left intact — the panel truncates with CSS ellipsis + a scroll fallback so
// nothing overflows the bubble.
function formatToolValue(v: unknown): string {
  if (v == null) return "N/A";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  try { return JSON.stringify(v); } catch { return String(v); }
}

// ToolChip: an interactive pill for a single tool call.
//  • running → animated spinner, non-interactive
//  • done + summary → clickable, expands a formatted result panel
//  • done, no summary → static check chip
function ToolChipView({
  tool,
  isOpen,
  onToggle,
}: {
  tool: ChatToolEvent;
  isOpen: boolean;
  onToggle: () => void;
}) {
  const isRunning = tool.state === "start";
  const hasDetail = !isRunning && !!tool.summary;
  return (
    <button
      type="button"
      className={`tool-chip ${isRunning ? "running" : "done"}${isOpen ? " open" : ""}`}
      onClick={hasDetail ? onToggle : undefined}
      disabled={isRunning || !hasDetail}
      aria-expanded={hasDetail ? isOpen : undefined}
      aria-label={`Tool ${tool.name}${isRunning ? " (running)" : " (complete)"}`}
    >
      <span className="tool-chip-icon" aria-hidden="true">
        {isRunning ? (
          <span className="tool-chip-spinner" />
        ) : (
          <svg viewBox="0 0 24 24" width="10" height="10" fill="none" stroke="currentColor" strokeWidth="3.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M5 12l5 5L20 6" />
          </svg>
        )}
      </span>
      <span className="tool-chip-name">{toolArgsLabel(tool.name, tool.args)}</span>
      {hasDetail && (
        <span className="tool-chip-caret" aria-hidden="true">
          <svg viewBox="0 0 24 24" width="9" height="9" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
            <path d="M6 9l6 6 6-6" />
          </svg>
        </span>
      )}
    </button>
  );
}

// ToolResultPanel: the expandable panel below the chips row that formats a
// tool's JSON summary as a readable key-value list. Falls back to raw text if
// the summary isn't valid JSON (e.g. truncated by the backend's 400-char cap).
function ToolResultPanel({ tool, onClose }: { tool: ChatToolEvent; onClose: () => void }) {
  let data: Record<string, unknown> | null = null;
  if (tool.summary) {
    try { data = JSON.parse(tool.summary) as Record<string, unknown>; } catch { /* truncated/invalid JSON */ }
  }
  const entries = data ? Object.entries(data) : [];
  return (
    <div className="tool-panel" role="region" aria-label={`${tool.name} result`}>
      <div className="tool-panel-head">
        <span className="tool-panel-title">{tool.name} · result</span>
        <button type="button" className="tool-panel-close" onClick={onClose} aria-label="Collapse">
          <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M6 6l12 12M18 6L6 18" />
          </svg>
        </button>
      </div>
      {entries.length > 0 ? (
        <dl className="tool-panel-fields">
          {entries.map(([k, v]) => (
            <div key={k} className="tool-field">
              <dt>{k}</dt>
              <dd>{formatToolValue(v)}</dd>
            </div>
          ))}
        </dl>
      ) : (
        <pre className="tool-panel-raw">{tool.summary}</pre>
      )}
    </div>
  );
}

// Normalise streamed markdown so block-level constructs (headings, blockquotes,
// lists, tables, code fences) are recognised by CommonMark even when the model
// emits them without the required preceding blank line. Also collapses 3+
// newlines to a paragraph break.
function normaliseMd(src: string): string {
  if (!src) return src;
  // Collapse runs of 3+ newlines to exactly two (a single blank line).
  let s = src.replace(/\n{3,}/g, "\n\n");
  // Ensure a blank line before block-start markers when they follow text on
  // the previous line. Matches: ATX headings (#…), blockquotes (>), unordered
  // list items (- * +), ordered list items (1.), fenced code (```), tables
  // (a line starting & ending with |), and horizontal rules (--- / *** / ___).
  const blockStart =
    /^(#{1,6}\s|>\s?|[-*+]\s|\d+[.)]\s|```| {4,}|\|.*\|\s*$|([-*_]\s?){3,}$)/;
  const lines = s.split("\n");
  const out: string[] = [];
  for (let i = 0; i < lines.length; i++) {
    const cur = lines[i];
    const prev = out[out.length - 1];
    if (prev !== undefined && prev.trim() !== "" && cur.trim() !== "" && blockStart.test(cur)) {
      out.push(""); // insert blank line separator
    }
    out.push(cur);
  }
  return out.join("\n");
}

// ChatMessage: renders a single chat turn (user or assistant).
// - User messages are right-aligned with an inverted avatar.
// - Assistant messages stream in (content grows as tokens arrive), show a
//   thinking cursor while streaming (driven by the parent's .chat-streaming
//   class on the last bot message), list tool calls as interactive chips with
//   an expandable result panel, and render citation chips for any
//   [[act: X, ref: Y]] markers the model emitted.
export default function ChatMessageView({ msg, isStreaming = false }: { msg: ChatMessage; isStreaming?: boolean }) {
  const isBot = msg.role === "assistant";
  const [expandedTool, setExpandedTool] = useState<string | null>(null);
  return (
    <div className={`msg ${isBot ? "bot" : msg.role}`}>
      <div className="avatar" aria-hidden="true">
        {isBot ? <BalanceIcon fontSize="small" /> : <PersonIcon fontSize="small" />}
      </div>
      <div className="bubble" data-error={msg.error ? "" : undefined}>
        {/* Phase status indicator (shown while streaming with no content yet) */}
        {isBot && !msg.content && (msg.status || (isStreaming && msg.tools.length === 0 && !msg.reasoning)) && (
          <span className="chat-status chat-phase">
            <span className="phase-spinner" aria-hidden="true" />
            {msg.status ? phaseLabel(msg.status) : "Thinking…"}
          </span>
        )}

        {/* Tool calls — shown above content for prominence during streaming */}
        {isBot && msg.tools.length > 0 && (
          <div className="tools" aria-label="Tool calls">
            <div className="tool-chips">
              {msg.tools.map((t) => (
                <ToolChipView
                  key={t.id}
                  tool={t}
                  isOpen={expandedTool === t.id}
                  onToggle={() =>
                    setExpandedTool((cur) => (cur === t.id ? null : t.id))
                  }
                />
              ))}
            </div>
            {expandedTool && (() => {
              const t = msg.tools.find((x) => x.id === expandedTool);
              if (!t) return null;
              return (
                <ToolResultPanel tool={t} onClose={() => setExpandedTool(null)} />
              );
            })()}
          </div>
        )}

        {/* Content (streams in token-by-token) */}
        {isBot ? (
          msg.content ? (
            <div className="md">
              <ReactMarkdown
                remarkPlugins={[remarkGfm]}
                components={{
                  table: (props) => (
                    <div className="md-table-wrap"><table {...props} /></div>
                  ),
                }}
              >
                {normaliseMd(msg.content)}
              </ReactMarkdown>
            </div>
          ) : null
        ) : (
          msg.content
        )}

        {/* Halted-before-content state */}
        {isBot && !msg.content && !msg.status && msg.error && (
          <span className="chat-status chat-halted" aria-label="response halted">
            <span className="halt-dot" aria-hidden="true" />
            Stopped; no response was generated.
          </span>
        )}
        {msg.error && msg.content && <span className="chat-status" style={{ color: "#d44430" }}>: {msg.error}</span>}

        {/* Agent plan (supervisor's reasoning, collapsible) */}
        {isBot && msg.plan && msg.plan.trim() && (
          <details className="plan-trace" aria-label="Agent plan">
            <summary>Agent plan</summary>
            <div className="plan-body">{msg.plan.trim()}</div>
          </details>
        )}

        {/* Reasoning trace from Nemotron thinking mode (collapsible) */}
        {isBot && msg.reasoning && msg.reasoning.trim() && (
          <details className="reasoning" aria-label="Reasoning trace">
            <summary>Reasoning trace</summary>
            <div className="reasoning-body">{msg.reasoning.trim()}</div>
          </details>
        )}

        {/* Citations */}
        {isBot && msg.citations.length > 0 && (
          <div className="cite">
            <strong>Citations:</strong>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 4 }}>
              {msg.citations.map((c, i) => (
                <CitationChip key={`${c.act}-${c.ref}-${i}`} cite={c} />
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}