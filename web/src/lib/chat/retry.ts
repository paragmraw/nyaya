// Retry trimming: a retry resends the last user message, so the trailing run
// (that user message and everything after it) must be removed; the sender
// re-appends the user message and a fresh assistant bubble. Verbatim move
// from the old lib/chat.ts. Exported for unit testing.

import type { ChatMessage } from "../api";

export function trimForRetry(messages: ChatMessage[]): { trimmed: ChatMessage[]; text: string | null } {
  let lastUserIdx = -1;
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === "user") {
      lastUserIdx = i;
      break;
    }
  }
  if (lastUserIdx === -1) return { trimmed: messages, text: null };
  return { trimmed: messages.slice(0, lastUserIdx), text: messages[lastUserIdx].content };
}

export function uid(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}