"use client";

import { useEffect, useRef, useState } from "react";
import { BalanceIcon } from "./icons";
import ChatComposer from "./ChatComposer";
import ChatMessageView, { phaseLabel } from "./ChatMessage";
import { RetryButton } from "./ErrorRow";
import { createAutoScroller, type AutoScroller } from "@/lib/chat/auto-scroll";
import { useChat } from "@/lib";

// ChatPanel: optional conversational assistant over /chat/turn. Rendered on
// the home page via a dynamic ssr:false import; the build flag
// NEXT_PUBLIC_CHAT_ENABLED="true" unlocks the composer, and the backend
// serves /chat only when NVIDIA_API_KEY is configured (otherwise /chat/* 404s).
// Streams SSE tokens, tool events, and grounded citation chips verified
// against shared nyaya.citations.
const GREETING =
  "Hello. I'm Nyaya v2.0. I verify statutes, BNS/BNSS/BSA transitions, 12 regulators, 15 tribunals, and Supreme Court / High Court precedents. Ask in plain English or legalese; I'll cite exact provisions and neutral citations.";

// Fallback model display info (used before /chat/health responds)
const FALLBACK_MODEL_ID = "nvidia/nemotron-3.5-lightning-30b-a3b";
const FALLBACK_MODEL_NAME = "Nemotron-3.5 Lightning 30B";

const SUGGESTED_PROMPTS = [
  "Compare IPC section 420 with its BNS equivalent and BNSS s.531 transition.",
  "Is Section 66A of the IT Act still in force? Cite the Supreme Court judgment.",
  "Search SC & Delhi High Court precedent on electronic evidence under IEA s.65B(4) / BSA s.63.",
];

interface ChatPanelProps {
  disabled?: boolean;
}

export default function ChatPanel({ disabled = false }: ChatPanelProps) {
  const { messages, isStreaming, error, send, cancel, reset, retry } = useChat();
  const bodyRef = useRef<HTMLDivElement>(null);
  const [modelId, setModelId] = useState(FALLBACK_MODEL_ID);
  const [modelName, setModelName] = useState(FALLBACK_MODEL_NAME);

  useEffect(() => {
    fetch("/chat/health")
      .then((r) => r.json())
      .then((data) => {
        if (data.model) {
          setModelId(data.model);
          const parts = data.model.split("/");
          const short = parts[parts.length - 1] || data.model;
          setModelName(short.replace(/-/g, " ").replace(/\b[a-z]/g, (c: string) => c.toUpperCase()));
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
  const unavailable = !!error && /temporarily unavailable/i.test(error);

  const lastMsg = messages[messages.length - 1];
  const liveText = isStreaming
    ? (lastMsg?.role === "assistant" && lastMsg.status ? phaseLabel(lastMsg.status) : "Nyaya is responding…")
    : lastMsg?.role === "assistant" && lastMsg.content
      ? "Response complete."
      : "";

  const scrollerRef = useRef<AutoScroller | null>(null);
  useEffect(() => {
    const el = bodyRef.current;
    if (!el) return;
    const scroller = createAutoScroller();
    scrollerRef.current = scroller;
    scroller.attach(el);
    return () => scroller.detach();
  }, []);
  useEffect(() => {
    scrollerRef.current?.onContentChanged();
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

      <div className="chat-body" id="chatBody" ref={bodyRef} aria-label="Chat conversation">
        {showGreeting ? (
          <div className="msg bot">
            <div className="avatar" aria-hidden="true"><BalanceIcon /></div>
            <div className="bubble">
              {GREETING}
              <span className="cite">
                <strong>Coverage:</strong> Constitution (106 Amds) · BNS/BNSS/BSA ↔ IPC/CrPC/IEA · 37 Legislatures · 12 Regulators · 15 Tribunals · SC &amp; 25 HCs
              </span>
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
              ? "Nyaya is unavailable right now; you can still retry or send anyway."
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
