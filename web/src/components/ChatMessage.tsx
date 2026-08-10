"use client";

import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import PersonIcon from "@mui/icons-material/Person";
import BalanceIcon from "@mui/icons-material/Balance";
import CitationChip from "./CitationChip";
import type { ChatMessage } from "@/lib";

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
//   class on the last bot message), list tool calls as small status rows, and
//   render citation chips for any [[act: X, ref: Y]] markers the model emitted.
export default function ChatMessageView({ msg }: { msg: ChatMessage }) {
  const isBot = msg.role === "assistant";
  return (
    <div className={`msg ${isBot ? "bot" : msg.role}`}>
      <div className="avatar" aria-hidden="true">
        {isBot ? <BalanceIcon fontSize="small" /> : <PersonIcon fontSize="small" />}
      </div>
      <div className="bubble" data-error={msg.error ? "" : undefined}>
        {isBot ? (
          msg.content ? (
            <div className="md">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{normaliseMd(msg.content)}</ReactMarkdown>
            </div>
          ) : msg.status ? (
            <span className="chat-status">{msg.status}…</span>
          ) : null
        ) : (
          msg.content
        )}
        {isBot && !msg.content && !msg.status && msg.error && (
          <span className="chat-status chat-halted" aria-label="response halted">
            <span className="halt-dot" aria-hidden="true" />
            Stopped — no response was generated.
          </span>
        )}
        {msg.error && msg.content && <span className="chat-status" style={{ color: "#d44430" }}> — {msg.error}</span>}

        {isBot && msg.tools.length > 0 && (
          <div className="tools" aria-label="Tool calls">
            {msg.tools.map((t) => (
              <span key={t.id} title={t.summary || JSON.stringify(t.args || {})}>
                {t.state === "start" ? "↳ " : "✓ "}
                {t.name}
                {t.state === "start" ? "(…)" : ""}
              </span>
            ))}
          </div>
        )}

        {isBot && msg.reasoning && (
          <details className="reasoning" aria-label="Reasoning trace">
            <summary>Reasoning trace</summary>
            <div className="reasoning-body">{msg.reasoning}</div>
          </details>
        )}

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