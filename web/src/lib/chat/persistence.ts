// Conversation persistence: sessionStorage serialize/restore plus a debounced
// writer.
//
// The old hook wrote sessionStorage on EVERY messages change — every
// animation frame during a stream. createPersistWriter coalesces those into
// one trailing-debounce write (500ms default), with flushNow() for the
// synchronous run-end / reset write. The sink is injected: the hook passes
// sessionStorage accessors, tests pass arrays.

import type { ChatMessage } from "../api";
import { humanizeError } from "./errors";

const STORAGE_KEY = "nyaya.chat.v1";

function isChatMessage(v: unknown): v is ChatMessage {
  if (!v || typeof v !== "object") return false;
  const m = v as Partial<ChatMessage>;
  return (
    (m.role === "user" || m.role === "assistant") &&
    typeof m.content === "string" &&
    Array.isArray(m.citations) &&
    Array.isArray(m.tools)
  );
}

// Serialize a message list for sessionStorage. Exported for unit testing.
export function serializeMessages(messages: ChatMessage[]): string {
  return JSON.stringify({ v: 1, messages });
}

// Parse (and shape-validate) a persisted message list. Restores the
// interrupted-run marker: a trailing assistant bubble with no content, tools
// or citations was mid-stream when the page was refreshed, so it is marked
// failed and becomes retryable. Exported for unit testing.
export function deserializeMessages(raw: string | null): ChatMessage[] {
  if (!raw) return [];
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return [];
  }
  const messages = (parsed as { v?: unknown; messages?: unknown })?.messages;
  if (!Array.isArray(messages)) return [];
  const valid = messages.filter(isChatMessage).map((m) => ({ ...m }));
  const last = valid[valid.length - 1];
  if (last && last.role === "assistant" && !last.content && last.tools.length === 0 && last.citations.length === 0) {
    valid[valid.length - 1] = { ...last, error: last.error || humanizeError("interrupted") };
  }
  return valid;
}

export function readStore(): string | null {
  try {
    return typeof window !== "undefined" ? window.sessionStorage.getItem(STORAGE_KEY) : null;
  } catch {
    return null; // private mode / disabled site data
  }
}

export function writeStore(raw: string | null): void {
  try {
    if (typeof window === "undefined") return;
    if (raw === null) window.sessionStorage.removeItem(STORAGE_KEY);
    else window.sessionStorage.setItem(STORAGE_KEY, raw);
  } catch {
    /* ignore quota / disabled site data */
  }
}

export type PersistWriter = {
  /** Queue a debounced write (trailing edge): many calls → one write. */
  schedule: (messages: ChatMessage[]) => void;
  /** Write immediately and cancel any pending debounced write. */
  flushNow: (messages: ChatMessage[]) => void;
  /** Drop the pending debounced write without flushing. */
  cancel: () => void;
};

export function createPersistWriter(options?: {
  delayMs?: number;
  write?: (raw: string | null) => void;
  setTimeoutFn?: (cb: () => void, ms: number) => unknown;
  clearTimeoutFn?: (handle: unknown) => void;
}): PersistWriter {
  const delayMs = options?.delayMs ?? 500;
  const write = options?.write ?? writeStore;
  const setT = options?.setTimeoutFn ?? ((cb: () => void, ms: number) => globalThis.setTimeout(cb, ms));
  const clearT = options?.clearTimeoutFn ?? ((h: unknown) => globalThis.clearTimeout(h as ReturnType<typeof setTimeout>));
  let handle: unknown = null;
  let lastRaw: string | null = null;
  return {
    schedule(messages) {
      lastRaw = messages.length > 0 ? serializeMessages(messages) : null;
      if (handle !== null) return; // a trailing write is already pending
      handle = setT(() => {
        handle = null;
        write(lastRaw);
      }, delayMs);
    },
    flushNow(messages) {
      if (handle !== null) {
        clearT(handle);
        handle = null;
      }
      lastRaw = messages.length > 0 ? serializeMessages(messages) : null;
      write(lastRaw);
    },
    cancel() {
      if (handle !== null) {
        clearT(handle);
        handle = null;
      }
    },
  };
}