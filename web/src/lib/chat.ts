"use client";

// useChat: a React hook that streams a chat turn from the FastAPI backend over
// Server-Sent Events and exposes a growing list of messages.
//
// SSE events:
//   event: meta        data: {"request_id": "..."}       — request id
//   event: status      data: {"msg": "analyzing"|"searching"|"composing", "rid": "..."} — phase
//   event: plan        data: {"content": "..."}          — supervisor plan text
//   event: token       data: {"content": "..."}          — synthesis LLM token deltas
//   event: reasoning   data: {"content": "..."}          — reasoning_content deltas
//   event: tool_start  data: {"id","name","args"}        — a tool was called
//   event: tool_result data: {"id","name","summary"}     — a tool returned
//   event: citations   data: {"citations": [{act, ref}]} — citations from the verified answer
//   event: correction  data: {"content": "..."}          — the verified answer, only when
//                                                           it differs from the streamed tokens
//   event: ping        data: {"ts": ...}                  — keepalive
//   event: error       data: {"message","detail","rid"}   — failure
//   event: done        data: {}                           — stream complete

import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatCitation, ChatHistoryTurn, ChatMessage, ChatRequest, ChatToolEvent } from "./api";

const CITE_RE = /\[\[act:\s*([^,\]]+?)\s*,\s*ref:\s*([^\]]+?)\s*\]\]/g;

// Inline citations are rendered as normal markdown links whose href points
// back at the corpus page. That href prefix doubles as the citation marker:
// it is the only place ChatMessage.tsx needs to look to recognise a citation
// chip, and (unlike the old `title="ic"` attribute it replaces) it survives
// the markdown pipeline unambiguously and needs no special-cased title text.
// parseCitations is the only producer of such links.
export const CITE_HREF_PREFIX = "/corpus/?act=";

export function isCitationHref(href: string | undefined): boolean {
  return !!href && href.startsWith(CITE_HREF_PREFIX);
}

function citeToMarkdown(act: string, ref: string): string {
  const href = `/corpus/?act=${encodeURIComponent(act)}&ref=${encodeURIComponent(ref)}`;
  return `[${act} · ${ref}](${href})`;
}

// Convert the raw [[act: X, ref: Y]] markers emitted by the backend into
// markdown citation links (see CITE_HREF_PREFIX) plus a de-duplicated list of
// citation pairs. Exported for unit testing.
export function parseCitations(text: string): { text: string; citations: ChatCitation[] } {
  const citations: ChatCitation[] = [];
  const seen = new Set<string>();
  const re = new RegExp(CITE_RE);
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    const act = m[1].trim();
    const ref = m[2].trim();
    const key = `${act}|${ref}`;
    if (!seen.has(key)) {
      seen.add(key);
      citations.push({ act, ref });
    }
  }
  const cleaned = text.replace(re, (_, act: string, ref: string) => citeToMarkdown(act.trim(), ref.trim())).replace(/\s{2,}/g, " ").trim();
  return { text: cleaned, citations };
}

function uid(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

// ─── Error humanization ───────────────────────────────────────────
// The unified error contract (chat/nyaya_chat SSE + REST) carries machine
// codes ({message, detail, rid}); humanizeError maps them to copy a user can
// act on, keeping server-provided detail where it adds value. Exported for
// unit testing.
export function humanizeError(code: string, detail = ""): string {
  const c = code.trim();
  const d = detail.trim();
  const withDetail = (base: string) => (d ? `${base} (${d})` : base);

  // Transport classes: a bare `TypeError: Failed to fetch`, a browser-specific
  // variant, or an explicit abort (user stop / stream timeout).
  if (/^(cancelled|aborted|aborterror)$/i.test(c)) return "Response cancelled.";
  // Set by the session-restoration path for a run that was mid-stream when
  // the page was refreshed (see deserializeMessages).
  if (/^interrupted$/i.test(c)) {
    return withDetail("This response was interrupted. Retry to resend your question.");
  }
  if (/failed to fetch|networkerror|load failed|network request failed/i.test(c)) {
    return withDetail("Couldn't reach the Nyaya service. Check your connection and try again.");
  }
  if (/^no response body$|^empty response$/i.test(c)) {
    return "Nyaya returned an empty response.";
  }

  // HTTP status lines from the non-2xx handler ("503 Service Unavailable").
  if (/^429\b/.test(c)) {
    return withDetail("Nyaya is handling a lot of requests right now. Try again in a moment.");
  }
  if (/^5\d\d\b/.test(c)) {
    return withDetail("Nyaya's assistant is temporarily unavailable. Please retry in a moment.");
  }
  if (/^4\d\d\b/.test(c)) {
    return withDetail("Nyaya couldn't process this request.");
  }
  if (/rate_limit/.test(c)) {
    return withDetail("Nyaya is handling a lot of requests right now. Try again in a moment.");
  }

  // Anything else that already reads like a sentence passes through (server
  // error messages may be human-phrased), with detail appended. This check
  // precedes the machine-code matches below so phrased messages ("The
  // verification layer timed out") are not re-mapped.
  const looksHuman = c.includes(" ") && /[a-z]{3}/.test(c) && !/^[a-z0-9_]+$/.test(c);
  if (looksHuman) return withDetail(c);

  // Machine codes the backend sends in `message`.
  if (/timed?\s?out|timeout/i.test(c)) {
    return withDetail("Nyaya took too long to respond. Try resending your question.");
  }
  if (/agent_unavailable|agent_error|internal_error|degraded/i.test(c)) {
    return withDetail("Nyaya's assistant is temporarily unavailable. Please retry in a moment.");
  }

  // Unknown opaque code: generic copy, with the raw code (or the detail) so
  // the user can still report it.
  return `Something went wrong while getting an answer. (${d || c})`;
}

// ─── Session persistence ──────────────────────────────────────────
// The conversation survives a mid-conversation page refresh by living in
// sessionStorage (per-tab, cleared when the tab closes). Only the serialized
// allow-listed fields are persisted.
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

function readStore(): string | null {
  try {
    return (typeof window !== "undefined" ? window.sessionStorage.getItem(STORAGE_KEY) : null);
  } catch {
    return null; // private mode / disabled site data
  }
}

function writeStore(raw: string | null): void {
  try {
    if (typeof window === "undefined") return;
    if (raw === null) window.sessionStorage.removeItem(STORAGE_KEY);
    else window.sessionStorage.setItem(STORAGE_KEY, raw);
  } catch {
    /* ignore quota / disabled site data */
  }
}

// ─── Retry trimming ───────────────────────────────────────────────
// A retry resends the last user message, so the trailing run (that user
// message and everything after it) must be removed; `send` re-appends the
// user message and a fresh assistant bubble. Trimming from the last *user*
// message (rather than from "the last assistant message anywhere") keeps
// earlier successful turns intact even when the failed run has no assistant
// bubble (e.g. a restored mid-stream refresh). Returns null text when there
// is no user message to resend. Exported for unit testing.
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

// Parse one SSE block (lines separated by \n, blocks by \n\n) into its event
// name and data payload. Per the SSE spec: multiple `data:` lines are joined
// with a literal newline, and only the single leading space after `data:` is
// stripped (`data: x` → `x`, but `data:  x` keeps the second space). The
// backend's encoder emits single-line JSON `data:` payloads, so this is a
// hardening for well-formed multi-line events, not a behavior change.
export function parseSseBlock(block: string): { event: string; data: string } | null {
  let event = "message";
  const dataLines: string[] = [];
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) {
      const d = line.slice(5);
      dataLines.push(d.startsWith(" ") ? d.slice(1) : d);
    }
  }
  if (dataLines.length === 0) return null;
  return { event, data: dataLines.join("\n") };
}

const STREAM_TIMEOUT_MS = 90_000;

// ─── rAF token batching (plan user-decision 4) ────────────────────
// Streamed `token` SSE events can arrive far more often than once per frame;
// updating React state per token re-renders the markdown bubble per token
// (O(n²) cumulative work over a long answer). createFrameBatcher coalesces
// any number of schedule() calls between two frames into a single flush().
// Exported for unit testing (tests/raf-batch.test.ts); raf/caf are injectable
// so the batching contract is testable in node (no requestAnimationFrame there).
export type FrameBatcher = { schedule: () => void; cancel: () => void };

export function createFrameBatcher(
  flush: () => void,
  raf: (cb: () => void) => number = (cb) => requestAnimationFrame(cb),
  caf: (handle: number) => void = (h) => cancelAnimationFrame(h),
): FrameBatcher {
  let handle: number | null = null;
  return {
    schedule() {
      if (handle !== null) return; // a frame is already pending
      handle = raf(() => {
        handle = null;
        flush();
      });
    },
    cancel() {
      if (handle !== null) {
        caf(handle);
        handle = null;
      }
    },
  };
}

// Error thrown when the server replies non-2xx, carrying the unified error
// contract's {message, detail, rid} fields so the catch path can humanize
// them and surface the request id for support/debugging.
class RequestError extends Error {
  detail: string;
  rid: string;
  constructor(code: string, detail = "", rid = "") {
    super(code);
    this.detail = detail;
    this.rid = rid;
  }
}

export type UseChat = {
  messages: ChatMessage[];
  isStreaming: boolean;
  error: string | null;
  send: (text: string) => Promise<void>;
  cancel: () => void;
  reset: () => void;
  retry: () => void;
};

export function useChat(): UseChat {
  // Lazy initializer restores the persisted thread (sessionStorage) at first
  // render, so a mid-conversation refresh preserves the conversation without
  // a first-tick flash of the greeting. useChat is used only inside
  // ChatPanel, which is dynamically imported with ssr:false, so this runs
  // client-side only; readStore() guards / try-catches for other contexts.
  const [messages, setMessages] = useState<ChatMessage[]>(() => deserializeMessages(readStore()));
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // Set when the stream timeout fires, so the catch path can keep the
  // timeout error copy instead of letting the deliberate abort read as a
  // user cancellation ("Response cancelled.").
  const timedOutRef = useRef(false);
  const retryTextRef = useRef<string | null>(null);
  const [retryTrigger, setRetryTrigger] = useState(0);
  // A mirror of `messages` so retry can read the latest list and mutate refs
  // / call setMessages with plain values OUTSIDE any state updater (React
  // strict mode may invoke updaters more than once, so they must stay pure).
  const messagesRef = useRef<ChatMessage[]>(messages);
  const isStreamingRef = useRef(false);

  // Persist on every message change so a mid-conversation refresh preserves
  // the thread (streaming messages included — the deserializer marks
  // interrupted runs retryable). Empty list → clear the stored conversation.
  useEffect(() => {
    messagesRef.current = messages;
    writeStore(messages.length > 0 ? serializeMessages(messages) : null);
  }, [messages]);

  const clearStreamTimeout = useCallback(() => {
    if (timeoutRef.current) {
      globalThis.clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
  }, []);

  const resetStreamTimeout = useCallback(() => {
    clearStreamTimeout();
    timeoutRef.current = globalThis.setTimeout(() => {
      timedOutRef.current = true;
      abortRef.current?.abort();
      abortRef.current = null;
      isStreamingRef.current = false;
      setIsStreaming(false);
      setError(humanizeError("stream_timeout"));
    }, STREAM_TIMEOUT_MS);
  }, [clearStreamTimeout]);

  const cancel = useCallback(() => {
    clearStreamTimeout();
    abortRef.current?.abort();
    abortRef.current = null;
    isStreamingRef.current = false;
    setIsStreaming(false);
  }, [clearStreamTimeout]);

  const reset = useCallback(() => {
    cancel();
    setMessages([]);
    setError(null);
  }, [cancel]);

  const retry = useCallback(() => {
    if (retryTextRef.current !== null || isStreamingRef.current) return;
    // Compute the trim from the messages mirror, then mutate refs and set
    // state with plain values — never inside a state updater (strict mode).
    const { trimmed, text } = trimForRetry(messagesRef.current);
    if (text === null) return;
    retryTextRef.current = text;
    setMessages(trimmed);
    messagesRef.current = trimmed;
    setRetryTrigger((n) => n + 1);
  }, []);

  const send = useCallback(async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || isStreamingRef.current) return;
    isStreamingRef.current = true;
    timedOutRef.current = false;
    setError(null);
    setIsStreaming(true);

    const userMsg: ChatMessage = { id: uid(), role: "user", content: trimmed, citations: [], tools: [] };
    const assistantId = uid();
    const assistantMsg: ChatMessage = {
      id: assistantId, role: "assistant", content: "", citations: [], tools: [],
    };
    setMessages((prev) => [...prev, userMsg, assistantMsg]);

    const history: ChatHistoryTurn[] = messages
      .filter((m) => m.content && (m.role === "user" || m.role === "assistant"))
      .slice(-8)
      .map((m) => ({ role: m.role, content: m.content }));

    const controller = new AbortController();
    abortRef.current = controller;

    const updateAssistant = (patch: Partial<ChatMessage>) => {
      setMessages((prev) =>
        prev.map((m) => (m.id === assistantId ? { ...m, ...patch } : m)),
      );
    };
    const appendTool = (ev: ChatToolEvent) => {
      setMessages((prev) =>
        prev.map((m) =>
          m.id === assistantId
            ? {
                ...m,
                tools: [
                  ...m.tools.filter((t) => t.id !== ev.id),
                  ev.state === "result"
                    ? { ...m.tools.find((t) => t.id === ev.id), ...ev }
                    : ev,
                ],
              }
            : m,
        ),
      );
    };

    let accContent = "";
    let accReasoning = "";
    let accPlan = "";

    // Token/reasoning/plan deltas accumulate in the strings above and are
    // flushed to React state at most once per animation frame (rAF batching,
    // plan user-decision 4): per-token setState would re-render the markdown
    // bubble per token. Each dirty accumulator is applied at most once per
    // frame; `correction` and the final flush bypass the batcher so the last
    // state written is always the authoritative one. The batcher is declared
    // outside the try so `catch` and `finally` can cancel any pending frame.
    let contentDirty = false;
    let reasoningDirty = false;
    let planDirty = false;
    const batcher = createFrameBatcher(() => {
      if (contentDirty) {
        contentDirty = false;
        const { text: cleaned, citations } = parseCitations(accContent);
        updateAssistant({ content: cleaned, citations });
      }
      if (reasoningDirty) {
        reasoningDirty = false;
        updateAssistant({ reasoning: accReasoning });
      }
      if (planDirty) {
        planDirty = false;
        updateAssistant({ plan: accPlan });
      }
    });

    try {
      const res = await fetch("/chat/turn", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify({ message: trimmed, history } satisfies ChatRequest),
        signal: controller.signal,
      });
      if (!res.ok) {
        // Non-2xx responses carry the unified error shape {message, detail, rid}
        // (e.g. the 503 agent-unavailable body). Read `message`; never the old
        // `error` key. Fall back to the status line for non-JSON bodies.
        let code = `${res.status} ${res.statusText}`;
        let detail = "";
        let rid = "";
        try {
          const body: unknown = await res.json();
          if (body && typeof body === "object") {
            const b = body as { message?: unknown; detail?: unknown; rid?: unknown };
            if (typeof b.message === "string" && b.message) code = b.message;
            if (typeof b.detail === "string") detail = b.detail;
            if (typeof b.rid === "string") rid = b.rid;
          }
        } catch {
          /* non-JSON body: keep the status-line message */
        }
        throw new RequestError(code, detail, rid);
      }
      if (!res.body) throw new RequestError("no response body");

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      // Token batching: the batcher closure and accumulators are hoisted above
      // so `catch`/`finally` can cancel any pending frame.
      resetStreamTimeout();

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        let sep: number;
        while ((sep = buffer.indexOf("\n\n")) !== -1) {
          const block = buffer.slice(0, sep);
          buffer = buffer.slice(sep + 2);
          const evt = parseSseBlock(block);
          if (!evt) continue;
          resetStreamTimeout();
          let payload: Record<string, unknown> = {};
          try { payload = evt.data ? JSON.parse(evt.data) : {}; } catch { /* keep empty */ }
          switch (evt.event) {
            case "meta":
              updateAssistant({ requestId: (payload.request_id as string) || "" } as Partial<ChatMessage> & { requestId?: string });
              break;
            case "token": {
              const c = (payload.content as string) || "";
              accContent += c;
              contentDirty = true;
              batcher.schedule();
              break;
            }
            case "reasoning": {
              const r = (payload.content as string) || "";
              accReasoning += r;
              reasoningDirty = true;
              batcher.schedule();
              break;
            }
            case "plan": {
              const p = (payload.content as string) || "";
              accPlan += p;
              planDirty = true;
              batcher.schedule();
              break;
            }
            case "tool_start":
              appendTool({
                id: (payload.id as string) || uid(),
                name: (payload.name as string) || "",
                args: payload.args as Record<string, unknown> | undefined,
                state: "start",
              });
              break;
            case "tool_result":
              appendTool({
                id: (payload.id as string) || uid(),
                name: (payload.name as string) || "",
                summary: (payload.summary as string) || "",
                state: "result",
              });
              break;
            case "status":
              updateAssistant({ status: (payload.msg as string) || "" });
              break;
            case "ping":
              break;
            case "citations": {
              const cites = (payload.citations as ChatCitation[]) || [];
              if (cites.length > 0) {
                updateAssistant({ citations: cites });
              }
              break;
            }
            case "correction": {
              const correctedText = (payload.content as string) || "";
              if (correctedText) {
                // Authoritative replacement: drop any pending batched flush so
                // it cannot overwrite the correction with stale accumulated
                // tokens, and rebase the accumulator onto the corrected text —
                // otherwise the final flush below would re-apply the raw
                // pre-correction tokens and win over the verified answer.
                batcher.cancel();
                contentDirty = false;
                accContent = correctedText;
                const { text: cleaned, citations } = parseCitations(correctedText);
                updateAssistant({ content: cleaned, citations, status: undefined });
              }
              break;
            }
            case "error": {
              // Unified error shape: {message, detail, rid}.
              const code = (payload.message as string) || "agent_error";
              const detail = (payload.detail as string) || "";
              const rid = (payload.rid as string) || "";
              const human = humanizeError(code, detail);
              setError(human);
              updateAssistant({
                // The human phrasing is what gets rendered; the request id
                // (rid) is surfaced next to it for support/debugging.
                error: human,
                ...(rid ? { requestId: rid } : {}),
              } as Partial<ChatMessage> & { requestId?: string });
              break;
            }
            case "done":
              break;
            default:
              break;
          }
        }
      }
      // Stream complete: flush the final accumulated text authoritatively.
      batcher.cancel();
      const { text: cleaned, citations } = parseCitations(accContent);
      updateAssistant({ content: cleaned, citations, status: undefined });
    } catch (err) {
      // Humanize the failure for both the footer note and the failed assistant
      // bubble: stream timeouts (the deliberate abort must keep the timeout
      // copy, not read as a user cancellation), user stops, network errors,
      // non-2xx RequestErrors (message + detail + rid), and anything else.
      const isAbort = err instanceof Error && err.name === "AbortError";
      const human = timedOutRef.current
        ? humanizeError("stream_timeout")
        : isAbort
          ? humanizeError("cancelled")
          : err instanceof RequestError
            ? humanizeError(err.message, err.detail)
            : humanizeError(err instanceof Error ? err.message : "request_failed");
      const rid = err instanceof RequestError ? err.rid : "";
      setError(human);
      updateAssistant({
        error: human,
        ...(rid ? { requestId: rid } : {}),
      } as Partial<ChatMessage> & { requestId?: string });
    } finally {
      // No pending frame may fire after abort/error/timeout: a late flush would
      // write partial accumulated text into the aborted assistant bubble.
      batcher.cancel();
      contentDirty = false;
      reasoningDirty = false;
      planDirty = false;
      clearStreamTimeout();
      isStreamingRef.current = false;
      setIsStreaming(false);
      abortRef.current = null;
    }
  }, [messages, resetStreamTimeout, clearStreamTimeout]);

  // Retry effect: when retryTrigger changes, re-send the last user message.
  useEffect(() => {
    if (retryTrigger > 0 && retryTextRef.current && !isStreaming) {
      const text = retryTextRef.current;
      retryTextRef.current = null;
      void send(text);
    }
  }, [retryTrigger, isStreaming, send]);

  return { messages, isStreaming, error, send, cancel, reset, retry };
}