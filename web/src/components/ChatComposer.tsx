"use client";

import { useEffect, useRef, useState } from "react";

// ChatComposer: a textarea + send button. Submits on Enter (Shift+Enter for
// newline), auto-grows up to a max height, and is disabled while streaming.
export default function ChatComposer({
  onSend,
  disabled,
  placeholder = "Ask about a provision, section, or case…",
}: {
  onSend: (text: string) => void;
  disabled: boolean;
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
        className="send"
        type="button"
        aria-label="Send"
        disabled={disabled || !value.trim()}
        onClick={submit}
      >
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M22 2 11 13" />
          <path d="M22 2 15 22l-4-9-9-4z" />
        </svg>
      </button>
    </div>
  );
}