"use client";

import { useEffect, useRef } from "react";
import ChatComposer from "./ChatComposer";
import ChatMessageView from "./ChatMessage";
import { useChat } from "@/lib";

// ChatPanel: the live Nyaya assistant. Streams tokens from the FastAPI chat
// backend (chat/nyaya_chat) over SSE, renders citation chips for grounded
// answers, and shows tool-call progress. Replaces the previous locked shell.
const GREETING =
  "Namaste. I'm Nyaya — I answer questions on the Constitution, CrPC, IPC/BNS and case law. Ask in plain English or legalese; I'll cite the exact provision.";

export default function ChatPanel() {
  const { messages, isStreaming, error, send, cancel } = useChat();
  const bodyRef = useRef<HTMLDivElement>(null);
  // Show the greeting until the first user message is sent (pure derivation,
  // no extra state needed).
  const showGreeting = !messages.some((m) => m.role === "user");

  // Auto-scroll to the latest message as tokens stream in.
  useEffect(() => {
    const el = bodyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages, isStreaming]);

  return (
    <div className={`chat-shell ${isStreaming ? "chat-streaming" : ""}`}>
      <div className="chat-head">
        <div className="ch-title">
          <span className={`status-dot ${isStreaming ? "live" : ""}`} aria-hidden="true" />
          Nyaya Assistant
        </div>
        <div className="ch-meta">
          <span className="pill">{isStreaming ? "Streaming…" : "Online"}</span>
          <span className="tag">Citations on</span>
        </div>
      </div>

      <div className="chat-body" id="chatBody" ref={bodyRef}>
        {showGreeting ? (
          <div className="msg bot">
            <div className="avatar">§</div>
            <div className="bubble">
              {GREETING}
              <span className="cite"><strong>Coverage:</strong> Constitution · CrPC 1973 · IPC · BNS/BNSS 2023 · SC judgments</span>
            </div>
          </div>
        ) : (
          messages.map((m) => <ChatMessageView key={m.id} msg={m} />)
        )}
      </div>

      <div className="chat-foot">
        <ChatComposer onSend={send} disabled={isStreaming} />
        <div className="composer-hint">
          <span className="status-dot" />
          Retrieval-grounded · not legal advice · verify citations before filing
          {error ? ` · ${error}` : ""}
          {isStreaming ? " · " : ""}
          {isStreaming && (
            <button
              type="button"
              onClick={cancel}
              className="cancel-btn"
            >
              cancel
            </button>
          )}
        </div>
      </div>
    </div>
  );
}