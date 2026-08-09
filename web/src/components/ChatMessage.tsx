"use client";

import CitationChip from "./CitationChip";
import type { ChatMessage } from "@/lib";

// ChatMessage: renders a single chat turn (user or assistant).
// - User messages are right-aligned with an inverted avatar.
// - Assistant messages stream in (content grows as tokens arrive), show a
//   thinking cursor while streaming (driven by the parent's .chat-streaming
//   class on the last bot message), list tool calls as small status rows, and
//   render citation chips for any [[act: X, ref: Y]] markers the model emitted.
export default function ChatMessageView({ msg }: { msg: ChatMessage }) {
  const isBot = msg.role === "assistant";
  return (
    <div className={`msg ${msg.role}`}>
      <div className="avatar" aria-hidden="true">{isBot ? "§" : "You"}</div>
      <div className="bubble" data-error={msg.error ? "" : undefined}>
        {msg.content || (isBot && msg.status ? <span className="chat-status">{msg.status}…</span> : "")}
        {msg.error && <span className="chat-status" style={{ color: "#d44430" }}> — {msg.error}</span>}

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