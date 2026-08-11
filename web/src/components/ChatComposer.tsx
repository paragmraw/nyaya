"use client";

import { useEffect, useRef, useState } from "react";

// ChatComposer: a textarea + send/stop button. Submits on Enter (Shift+Enter for
// newline), auto-grows up to a max height, and is disabled while streaming.
// While streaming, the send button morphs into a stop button that cancels
// the in-flight turn.
export default function ChatComposer({
  onSend,
  onStop,
  disabled,
  isStreaming = false,
  placeholder = "Ask about a provision, section, or case…",
}: {
  onSend: (text: string) => void;
  onStop: () => void;
  disabled: boolean;
  isStreaming?: boolean;
  placeholder?: string;
}) {
  const [value, setValue] = useState("");
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

  return (
    <div className="composer">
      <textarea
        ref={ref}
        rows={1}
        aria-label="Ask Nyaya a legal question"
        placeholder={placeholder}
        value={value}
        disabled={disabled}
        onChange={(e) => setValue(e.target.value)}
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
  );
}