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
// newlines to a paragraph break, and splits jammed-together block elements
// (e.g. "### Heading- list item") onto separate lines so they parse correctly.
// Exported for unit testing (see tests/normalise-md.test.ts).
export function normaliseMd(src: string): string {
  if (!src) return src;
  // Collapse runs of 3+ newlines to exactly two (a single blank line).
  let s = src.replace(/\n{3,}/g, "\n\n");

  // Fix jammed emphasis markers: when the model emits "**text1****text2**",
  // the inner "****" is a closing-then-opening bold delimiter with no space.
  // Insert a space so it becomes "**text1** **text2**" and both parse as bold.
  // Only matches exactly 4 * (not 6+) to avoid touching *** (bold+italic).
  s = s.replace(/\*\*\*\*(?!\*)/g, "** **");
  s = s.replace(/____(?!_)/g, "__ __");

  // Normalise malformed ATX headings the model emits as "##3." (no space
  // between the hashes and the number) → "## 3." so CommonMark recognises
  // them as headings rather than leaving them glued to surrounding text.
  // Only fires when the digits are immediately followed by "." (a heading
  // number like "##1.", "##3."), avoiding false positives like "#1" in prose.
  s = s.replace(/(#{1,6})(\d+\.)/g, "$1 $2");

  // Split jammed-together block elements that the model emits on a single line.
  // The model frequently emits headings, list items, table rows, blockquotes,
  // and horizontal rules all on the same line without newline separators.
  //
  // We insert a newline before any block-start marker that appears mid-line.
  // This runs BEFORE the line-based blank-line insertion below.
  //
  // Patterns to split (insert \n before the marker):
  //  1. "text ### Heading"  /  "text - item"  /  "text > quote"  (whitespace before marker)
  //     NOTE: Only - and * as list markers after whitespace, NOT + (which
  //     appears in regular text like "imprisonment + fine").
  //  2. "word- Capital"  /  "word.- Capital"  /  "word.- **Bold"  (list marker jammed after word/punct)
  //  3. "**bold**| table"  /  "text)| table"  /  "**bold**- list"  /  "**bold**1. ordered"
  //  4. "text.> quote"  /  "text)> quote"  (blockquote jammed after punctuation, no space)
  //  5. "text.---"  /  "text ---"  (horizontal rule jammed after text)
  s = s.replace(/(\s)(#{1,6}\s|>\s?|[-*]\s)/g, "$1\n$2");
  s = s.replace(/([a-zA-Z.,\)])(- (?:[A-Z*]|\*\*))/g, "$1\n$2");
  s = s.replace(/(\*\*)(\|)/g, "$1\n$2");
  s = s.replace(/(\*\*)(- (?:[A-Z*]|\*\*))/g, "$1\n$2");
  s = s.replace(/(\*\*)(\d+\.\s)/g, "$1\n$2");
  // Split | from any preceding non-whitespace, non-pipe character (table row start).
  // The regex requires at least one | in the captured row and the text before
  // the first | must NOT be only dashes/colons (which would be a GFM separator
  // row like |---|---|). We use a negative-lookahead-ish approach: match a
  // non-pipe/non-space char followed by |, but only if what follows the | is
  // not just dashes and pipes (i.e., it's a real data row with text).
  // To keep it simple and avoid breaking separator rows, we only split when
  // the character before | is NOT a dash or colon.
  s = s.replace(/([a-zA-Z0-9\)\.\u2019\u201c\u201d"'])\|/g, "$1\n|");
  // Split > from preceding punctuation (blockquote after sentence end)
  s = s.replace(/([.,\)])(>)/g, "$1\n$2");
  // Split --- (horizontal rule) from preceding text on the same line.
  // Only match when --- is at the start of what looks like a standalone
  // horizontal rule (preceded by whitespace or sentence-ending punctuation),
  // NOT when --- is part of a GFM table separator row (|---|---|).
  s = s.replace(/([.,])(---)/g, "$1\n$2");
  s = s.replace(/(\s)(---\s*$)/g, "$1\n$2");
  // Split ATX headings from following text on the same line. The model emits
  // "### Heading titleParagraph text..." with no newline after the title.
  // We detect a lowercase letter immediately followed by an uppercase letter
  // within a heading line and split there (e.g. "saysSection" → "says\nSection").
  // This runs after the earlier heading split, so it only applies to lines
  // that already start with ###.
  s = s.replace(/(#{1,6} .+?[a-z])([A-Z][a-z])/g, "$1\n$2");

  // Shared GFM table-row detectors (used by the header-repair pass below and
  // the blank-line inserter after it).
  //  - separator row: a line of |, -, :, and spaces only (|---|---|…)
  //  - table data row: a line containing | that starts and ends with |
  const tableSeparator = /^\s*\|?\s*:?-{2,}(:?\s*\|\s*:?-{2,})*:?\s*\|?\s*$/;
  const tableDataRow = /^\s*\|.*\|\s*$/;

  // Repair GFM table headers jammed onto the same line as preceding text
  // (commonly a heading or sentence-end). The model frequently emits:
  //   "## 3. Key terms (…) | Term | Explanation |"
  // with the table header glued to the heading, and the separator on the NEXT
  // line. GFM requires the header row and separator on consecutive lines, so
  // we split the current line before its first `|` to give the header its own
  // line. We only split when the text before the first `|` looks like a heading
  // (contains #) or a sentence (contains whitespace and ends with sentence
  // punctuation), so valid no-leading-pipe header rows like "Score | Result"
  // are left intact.
  {
    const lines = s.split("\n");
    for (let i = 0; i + 1 < lines.length; i++) {
      if (!tableSeparator.test(lines[i + 1].trim())) continue;
      const pipeIdx = lines[i].indexOf("|");
      if (pipeIdx <= 0) continue; // already starts with |, or no pipe at all
      const before = lines[i].slice(0, pipeIdx).trim();
      if (!before) continue;
      const looksLikeHeadingOrSentence =
        /#/.test(before) ||
        (/\s/.test(before) && /[\])!?:."']$/.test(before));
      if (!looksLikeHeadingOrSentence) continue;
      lines.splice(i, 1, before, lines[i].slice(pipeIdx));
    }
    s = lines.join("\n");
  }

  // Ensure a blank line before block-start markers when they follow text on
  // the previous line. Matches: ATX headings (#…), blockquotes (>), unordered
  // list items (- * +), ordered list items (1.), fenced code (```), tables
  // (a line starting & ending with |), and horizontal rules (--- / *** / ___).
  //
  // EXCEPTION: GFM table rows must NOT be separated from each other — GFM
  // requires the header, separator, and data rows on consecutive lines.
  const blockStart =
    /^(#{1,6}\s|>\s?|[-*+]\s|\d+[.)]\s|```| {4,}|\|.*\|\s*$|([-*_]\s?){3,}$)/;
  const lines = s.split("\n");
  const out: string[] = [];
  for (let i = 0; i < lines.length; i++) {
    const cur = lines[i];
    const prev = out[out.length - 1];
    // Insert a blank line before a block-start marker, but not between
    // consecutive GFM table rows (header, separator, data) — those must
    // stay on consecutive lines for GFM to recognise the table.
    const isTableSep = tableSeparator.test(cur.trim());
    const isTableData = tableDataRow.test(cur.trim());
    const prevIsTable = prev !== undefined && (tableDataRow.test(prev.trim()) || tableSeparator.test(prev.trim()));
    const isTableLine = (isTableSep || isTableData) && prevIsTable;
    if (
      prev !== undefined && prev.trim() !== "" && cur.trim() !== "" &&
      blockStart.test(cur) && !isTableLine
    ) {
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
                  // Inline citation links emitted by parseCitations carry a
                  // title="ic" sentinel; style them as compact chips. Other
                  // links render with the default .md a styling.
                  a: ({ ...props }) =>
                    props.title === "ic" ? (
                      <a {...props} title={undefined} className="inline-cite" />
                    ) : (
                      <a {...props}>{props.children ?? props.href}</a>
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