"use client";

import { useEffect, useRef, useState } from "react";
import { BalanceIcon } from "./icons";
import ChatComposer from "./ChatComposer";
import ChatMessageView, { phaseLabel } from "./ChatMessage";
import { RetryButton } from "./ErrorRow";
import { useChat } from "@/lib";

// ChatPanel: the live Nyaya assistant. Streams tokens from the FastAPI chat
// backend (chat/nyaya_chat) over SSE, renders citation chips for grounded
// answers, and shows tool-call progress. Can be disabled via feature flag.
const GREETING =
  "Hello. I'm Nyaya. I answer questions on the Constitution, CrPC, IPC/BNS and case law. Ask in plain English or legalese; I'll cite the exact provision.";

// Fallback model display info (used before /chat/health responds)
const FALLBACK_MODEL_ID = "nvidia/nemotron-3.5-lightning-30b-a3b";
const FALLBACK_MODEL_NAME = "Nemotron-3.5 Lightning 30B";

// Suggested opening questions, lifted from the eval scenario patterns
// (chat/eval/golden.jsonl): one statute question, one constitutional, one
// comparison (which exercises the multi-tool path). Chips are only shown on
// the empty state — once the user has typed their own first message they're
// noise.
const SUGGESTED_PROMPTS = [
  "What is the punishment for murder under IPC?",
  "What does Article 21 of the Constitution guarantee?",
  "Compare IPC section 302 with its BNS equivalent.",
];

interface ChatPanelProps {
  disabled?: boolean;
}

export default function ChatPanel({ disabled = false }: ChatPanelProps) {
  const { messages, isStreaming, error, send, cancel, reset, retry } = useChat();
  const bodyRef = useRef<HTMLDivElement>(null);
  const [modelId, setModelId] = useState(FALLBACK_MODEL_ID);
  const [modelName, setModelName] = useState(FALLBACK_MODEL_NAME);

  // Fetch model info from /chat/health for dynamic display (fixes drift risk)
  useEffect(() => {
    fetch("/chat/health")
      .then((r) => r.json())
      .then((data) => {
        if (data.model) {
          setModelId(data.model);
          // Derive a display name from the model id
          const parts = data.model.split("/");
          const short = parts[parts.length - 1] || data.model;
          setModelName(short.replace(/-/g, " ").replace(/\b\w/g, (c: string) => c.toUpperCase()));
        }
      })
      .catch(() => {
        // Keep fallback on error
      });
  }, []);

  const showGreeting = !messages.some((m) => m.role === "user");
  const canReset = messages.length > 0 || !!error;
  const canRetry = !!error && !isStreaming;
  const modelUrl = `https://build.nvidia.com/${modelId}`;
  // Unavailability (5xx / agent_unavailable → humanizeError's "temporarily
  // unavailable" copy) gets its own composer placeholder so the input signals
  // the outage rather than inviting a submit that will likely fail again.
  const unavailable = !!error && /temporarily unavailable/i.test(error);

  // The streaming tail's announcement text for screen readers. Kept in its own
  // aria-live region (below) so token-by-token updates don't re-read the whole
  // conversation log, and phase changes / completion / failure are announced
  // in plain language.
  const lastMsg = messages[messages.length - 1];
  const liveText = isStreaming
    ? (lastMsg?.role === "assistant" && lastMsg.status ? phaseLabel(lastMsg.status) : "Nyaya is responding…")
    : lastMsg?.role === "assistant" && lastMsg.content
      ? "Response complete."
      : "";

  // Auto-scroll to the latest message as tokens stream in
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
            href={modelUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="powered-by"
            title={modelId}
          >
            <span className="pb-label">Powered by</span>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img className="ch-logo" src="/nvidia.svg" alt="NVIDIA" height="14" />
            <span className="model-full">{modelName}</span>
            <span className="model-short" aria-hidden="true">{modelName.split(" ").slice(-2).join(" ")}</span>
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
            <span className="nc-label">New chat</span>
          </button>
        </div>
      </div>

      {/* The transcript itself is NOT a live region (role="log" was): each
          token made screen readers re-read the whole log. Announcements are
          handled by the streaming-tail live region below. */}
      <div className="chat-body" id="chatBody" ref={bodyRef} aria-label="Chat conversation">
        {showGreeting ? (
          <div className="msg bot">
            <div className="avatar" aria-hidden="true"><BalanceIcon /></div>
            <div className="bubble">
              {GREETING}
              <span className="cite"><strong>Coverage:</strong> Constitution · CrPC 1973 · IPC · BNS/BNSS 2023 · SC judgments</span>
              {!disabled && (
                <div
                  className="suggest-row"
                  role="group"
                  aria-label="Suggested questions"
                  style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 10 }}
                >
                  {SUGGESTED_PROMPTS.map((p) => (
                    <button
                      key={p}
                      type="button"
                      className="suggest-chip"
                      onClick={() => send(p)}
                      style={{
                        border: "1px solid var(--border)",
                        borderRadius: "var(--radius, 8px)",
                        background: "var(--surface)",
                        color: "var(--fg)",
                        font: "inherit",
                        fontSize: 12.5,
                        padding: "5px 10px",
                        cursor: "pointer",
                        textAlign: "left",
                      }}
                    >
                      {p}
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        ) : (
          messages.map((m, i) => (
            <ChatMessageView
              key={m.id}
              msg={m}
              isStreaming={isStreaming && i === messages.length - 1}
              onRetry={m.role === "assistant" && i === messages.length - 1 ? retry : undefined}
            />
          ))
        )}
        <div className="sr-only" role="status" aria-live="polite" aria-atomic="true">
          {liveText}
        </div>
      </div>

      <div className="chat-foot">
        <ChatComposer
          onSend={send}
          onStop={cancel}
          disabled={isStreaming || disabled}
          isStreaming={isStreaming}
          disabledHint={disabled ? "Chat is currently disabled." : undefined}
          placeholder={
            unavailable
              ? "Nyaya is unavailable right now — you can still retry or send anyway."
              : undefined
          }
        />
        <div className="composer-hint">
          <span className="status-dot" />
          Retrieval-grounded · not legal advice · verify citations before filing
          {error ? ` · ${error}` : ""}
          {canRetry && (
            <RetryButton
              onClick={retry}
              label="Retry"
              title="Retry last message"
              style={{ marginLeft: 8 }}
            />
          )}
        </div>
      </div>
    </div>
  );
}