"use client";

// Tool-call chips + expandable result panel for an assistant message.
// Extracted from ChatMessage.tsx (P4 decomposition) so the message view stays
// a thin composition layer; the expanded-chip state is local to this
// component, which also keeps ChatMessageView's memo effective (toggling a
// chip re-renders only the chips row, not the whole bubble).

import { useState } from "react";
import type { ChatToolEvent } from "@/lib";

// Build a compact label for a tool chip, showing the most relevant arg.
function toolArgsLabel(name: string, args?: Record<string, unknown>): string {
  if (!args) return name;
  const act = args.act || args.act_short_name;
  const section = args.section_number || args.article_number || args.section;
  const query = args.query;
  if (act && section) return `${name}(${act} §${section})`;
  if (query) {
    // Cap at 30 chars, but avoid cutting mid-word: when truncating, drop the
    // trailing partial word so the label reads cleanly with the ellipsis.
    const q = String(query);
    const shown = q.length > 30 ? `${q.slice(0, 30).replace(/\s+\S*$/, "")}…` : q;
    return `${name}("${shown}")`;
  }
  if (act) return `${name}(${act})`;
  if (section) return `${name}(§${section})`;
  return name;
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
// the summary isn't valid JSON (e.g. truncated by the backend's cap).
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

// The chips row for one assistant message, plus the single expanded panel.
// Shown above content for prominence during streaming.
export default function ToolChips({ tools }: { tools: ChatToolEvent[] }) {
  const [expanded, setExpanded] = useState<string | null>(null);
  if (tools.length === 0) return null;
  return (
    <div className="tools" aria-label="Tool calls">
      <div className="tool-chips">
        {tools.map((t) => (
          <ToolChipView
            key={t.id}
            tool={t}
            isOpen={expanded === t.id}
            onToggle={() => setExpanded((cur) => (cur === t.id ? null : t.id))}
          />
        ))}
      </div>
      {expanded && (() => {
        const t = tools.find((x) => x.id === expanded);
        if (!t) return null;
        return <ToolResultPanel tool={t} onClose={() => setExpanded(null)} />;
      })()}
    </div>
  );
}