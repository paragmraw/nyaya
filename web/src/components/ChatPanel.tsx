"use client";

import { useEffect, useRef } from "react";
import BalanceIcon from "@mui/icons-material/Balance";
import ChatComposer from "./ChatComposer";
import ChatMessageView from "./ChatMessage";
import { useChat } from "@/lib";

// ChatPanel: the live Nyaya assistant. Streams tokens from the FastAPI chat
// backend (chat/nyaya_chat) over SSE, renders citation chips for grounded
// answers, and shows tool-call progress. Can be disabled via feature flag.
const GREETING =
  "Namaste. I'm Nyaya. I answer questions on the Constitution, CrPC, IPC/BNS and case law. Ask in plain English or legalese; I'll cite the exact provision.";

// LLM powering the assistant. Must match the model id configured in
// chat/nyaya_chat/config.py (SYNTHESIS_MODEL).
const MODEL_ID = "nvidia/nemotron-3.5-lightning-30b-a3b";
const MODEL_NAME = "Nemotron-3.5 Lightning 30B";
const MODEL_URL = `https://build.nvidia.com/${MODEL_ID}`;

interface ChatPanelProps {
  /** When true, the chat panel is disabled and shown in a blurred/locked state */
  disabled?: boolean;
}

export default function ChatPanel({ disabled = false }: ChatPanelProps) {
  const { messages, isStreaming, error, send, cancel, reset } = useChat();
  const bodyRef = useRef<HTMLDivElement>(null);
  // Show the greeting until the first user message is sent (pure derivation,
  // no extra state needed).
  const showGreeting = !messages.some((m) => m.role === "user");
  // The new-chat action only makes sense once a conversation has started.
  const canReset = messages.length > 0 || !!error;

  // Auto-scroll to the latest message as tokens stream in — but only when the
  // user is already near the bottom, so we don't yank them away while they've
  // scrolled up to read earlier messages.
  useEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    const nearBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight < 120;
    if (nearBottom) el.scrollTop = el.scrollHeight;
  }, [messages, isStreaming]);

  return (
    <div className={`chat-shell ${isStreaming ? "chat-streaming" : ""} ${disabled ? "chat-locked" : ""}`}>
      <div className="chat-head">
        <div className="ch-title">
          <span className={`status-dot ${isStreaming ? "live" : ""}`} aria-hidden="true" />
          Nyaya Assistant
        </div>
        <div className="ch-meta">
          <a
            href={MODEL_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="powered-by"
            title={MODEL_ID}
          >
            Powered by
            <img className="ch-logo" src="/nvidia.svg" alt="NVIDIA" height="14" />
            {MODEL_NAME}
          </a>
          <button
            type="button"
            className="new-chat-btn"
            onClick={reset}
            disabled={isStreaming || !canReset}
            aria-label="Start a new chat"
            title="Start a new chat"
          >
            <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M12 5v14M5 12h14" />
            </svg>
            New chat
          </button>
        </div>
      </div>

      <div className="chat-body" id="chatBody" ref={bodyRef}>
        {showGreeting ? (
          <div className="msg bot">
            <div className="avatar" aria-hidden="true"><BalanceIcon fontSize="small" /></div>
            <div className="bubble">
              {GREETING}
              <span className="cite"><strong>Coverage:</strong> Constitution · CrPC 1973 · IPC · BNS/BNSS 2023 · SC judgments</span>
            </div>
          </div>
        ) : (
          messages.map((m) => (
            <ChatMessageView key={m.id} msg={m} isStreaming={isStreaming} />
          ))
        )}
      </div>

      <div className="chat-foot">
        <ChatComposer
          onSend={send}
          onStop={cancel}
          disabled={isStreaming || disabled}
          isStreaming={isStreaming}
        />
        <div className="composer-hint">
          <span className="status-dot" />
          Retrieval-grounded · not legal advice · verify citations before filing
          {error ? ` · ${error}` : ""}
        </div>
      </div>
    </div>
  );
}