"use client";

import { useEffect, useRef, useState } from "react";

// Composer limits. MAX mirrors the backend contract exactly
// (chat/nyaya_chat/schemas.py: message max_length=4000) so the browser blocks
// oversize submits instead of round-tripping a 422. The counter appears only
// near the limit — a permanently visible counter is noise.
const MAX_MESSAGE_CHARS = 4000;
const COUNTER_THRESHOLD = 3500;

// Draft persistence: the typed-but-unsent message survives navigation within
// the tab (sessionStorage is per-tab and cleared when the tab closes — the
// right scope for a draft). Wrapped in try/catch: browsers set to block site
// data, and some embedded contexts, throw on access.
const DRAFT_KEY = "nyaya.chat.draft";

function readDraft(): string {
  try {
    return globalThis.sessionStorage?.getItem(DRAFT_KEY) ?? "";
  } catch {
    return "";
  }
}

function writeDraft(text: string) {
  try {
    if (text) globalThis.sessionStorage?.setItem(DRAFT_KEY, text);
    else globalThis.sessionStorage?.removeItem(DRAFT_KEY);
  } catch {
    /* storage unavailable — draft simply doesn't persist */
  }
}

// ChatComposer: a textarea + send/stop button. Submits on Enter (Shift+Enter for
// newline), auto-grows up to a max height, and is disabled while streaming.
// While streaming, the send button morphs into a stop button that cancels
// the in-flight turn. A character counter appears past COUNTER_THRESHOLD, and
// the unsent draft persists across in-tab navigation.
export default function ChatComposer({
  onSend,
  onStop,
  disabled,
  isStreaming = false,
  placeholder = "Ask about a provision, section, or case…",
  disabledHint,
}: {
  onSend: (text: string) => void;
  onStop: () => void;
  disabled: boolean;
  isStreaming?: boolean;
  placeholder?: string;
  // Explicit copy for the disabled state (e.g. the feature-flag locked path)
  // so the composer never looks broken without an explanation.
  disabledHint?: string;
}) {
  // ChatPanel is dynamically imported with ssr:false, so the lazy initializer
  // runs client-side only; readDraft guards anyway.
  const [value, setValue] = useState<string>(() => readDraft());
  const ref = useRef<HTMLTextAreaElement>(null);

  // Auto-grow the textarea up to its max-height (CSS caps at 90px).
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 90)}px`;
  }, [value]);

  const submit = () => {
    const t = value.trim();
    if (!t || disabled) return;
    onSend(t);
    setValue("");
    writeDraft("");
  };

  const onChange = (next: string) => {
    setValue(next);
    writeDraft(next);
  };

  // While streaming, the action button becomes a stop control.
  const action = isStreaming
    ? {
        className: "send stop",
        ariaLabel: "Stop streaming",
        title: "Stop streaming",
        disabled: false,
        onClick: onStop,
        icon: (
          <svg viewBox="0 0 24 24" width="16" height="16" aria-hidden="true">
            <rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none" />
          </svg>
        ),
      }
    : {
        className: "send",
        ariaLabel: "Send",
        title: "Send",
        disabled: disabled || !value.trim(),
        onClick: submit,
        icon: (
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <path d="M22 2 11 13" />
            <path d="M22 2 15 22l-4-9-9-4z" />
          </svg>
        ),
      };

  const showCounter = value.length > COUNTER_THRESHOLD;

  return (
    <div>
      <div className="composer">
        <textarea
          ref={ref}
          rows={1}
          maxLength={MAX_MESSAGE_CHARS}
          aria-label="Ask Nyaya a legal question"
          placeholder={placeholder}
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && !e.shiftKey) {
              e.preventDefault();
              submit();
            }
          }}
        />
        <button
          className={action.className}
          type="button"
          aria-label={action.ariaLabel}
          title={action.title}
          disabled={action.disabled}
          onClick={action.onClick}
        >
          {action.icon}
        </button>
      </div>
      {disabledHint && disabled ? (
        <div role="note" style={{ marginTop: 6, fontSize: 12.5, color: "var(--muted, inherit)" }}>
          {disabledHint}
        </div>
      ) : showCounter ? (
        // Only rendered near the limit; polite live so the remaining budget is
        // announced without interrupting whatever is being read.
        <div
          aria-live="polite"
          style={{
            textAlign: "right",
            marginTop: 4,
            fontSize: 11,
            fontFamily: "var(--font-mono, monospace)",
            color: value.length >= MAX_MESSAGE_CHARS ? "var(--error, #d44430)" : "var(--muted, inherit)",
          }}
        >
          {MAX_MESSAGE_CHARS - value.length} characters left
        </div>
      ) : null}
    </div>
  );
}