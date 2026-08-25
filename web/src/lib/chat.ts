"use client";

// useChat: a React hook that streams a chat turn from the FastAPI backend over
// Server-Sent Events and exposes a growing list of messages.
//
// SSE events:
//   event: meta        data: {"request_id": "..."}       — request id
//   event: status      data: {"msg": "analyzing"|"searching"|"composing"} — phase
//   event: plan        data: {"content": "..."}          — supervisor plan text
//   event: token       data: {"content": "..."}          — synthesis LLM token deltas
//   event: reasoning   data: {"content": "..."}          — reasoning_content deltas
//   event: tool_start  data: {"id","name","args"}        — a tool was called
//   event: tool_result data: {"id","name","summary"}     — a tool returned
//   event: ping        data: {"ts": ...}                  — keepalive
//   event: error       data: {"message","detail"}         — failure
//   event: done        data: {}                           — stream complete

import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatCitation, ChatHistoryTurn, ChatMessage, ChatRequest, ChatToolEvent } from "./api";

const CITE_RE = /\[\[act:\s*([^,\]]+?)\s*,\s*ref:\s*([^\]]+?)\s*\]\]/g;

function citeToMarkdown(act: string, ref: string): string {
  const href = `/corpus/?act=${encodeURIComponent(act)}&ref=${encodeURIComponent(ref)}`;
  return `[${act} · ${ref}](${href} "ic")`;
}

function parseCitations(text: string): { text: string; citations: ChatCitation[] } {
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

function parseSseBlock(block: string): { event: string; data: string } | null {
  let event = "message";
  let data = "";
  for (const line of block.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data += line.slice(5).trim();
  }
  if (!data) return null;
  return { event, data };
}

const STREAM_TIMEOUT_MS = 90_000;

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
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const retryTextRef = useRef<string | null>(null);
  const [retryTrigger, setRetryTrigger] = useState(0);

  const clearStreamTimeout = useCallback(() => {
    if (timeoutRef.current) {
      globalThis.clearTimeout(timeoutRef.current);
      timeoutRef.current = null;
    }
  }, []);

  const resetStreamTimeout = useCallback(() => {
    clearStreamTimeout();
    timeoutRef.current = globalThis.setTimeout(() => {
      abortRef.current?.abort();
      abortRef.current = null;
      setIsStreaming(false);
      setError("stream_timeout");
    }, STREAM_TIMEOUT_MS);
  }, [clearStreamTimeout]);

  const cancel = useCallback(() => {
    clearStreamTimeout();
    abortRef.current?.abort();
    abortRef.current = null;
    setIsStreaming(false);
  }, [clearStreamTimeout]);

  const reset = useCallback(() => {
    cancel();
    setMessages([]);
    setError(null);
  }, [cancel]);

  const retry = useCallback(() => {
    setMessages((prev) => {
      const lastUser = [...prev].reverse().find((m) => m.role === "user");
      if (lastUser) {
        const lastAssistantIdx = [...prev].reverse().findIndex((m) => m.role === "assistant");
        const trimmed = lastAssistantIdx === -1
          ? prev
          : prev.slice(0, prev.length - 1 - lastAssistantIdx);
        retryTextRef.current = lastUser.content;
        return trimmed;
      }
      return prev;
    });
    setRetryTrigger((n) => n + 1);
  }, []);

  const send = useCallback(async (text: string) => {
    const trimmed = text.trim();
    if (!trimmed || isStreaming) return;
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

    try {
      const res = await fetch("/chat/turn", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify({ message: trimmed, history } satisfies ChatRequest),
        signal: controller.signal,
      });
      if (!res.ok) {
        const body = await res.text().catch(() => "");
        throw new Error(`${res.status} ${res.statusText} ${body.slice(0, 120)}`);
      }
      if (!res.body) throw new Error("no response body");

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let accContent = "";
      let accReasoning = "";
      let accPlan = "";

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
              const { text: cleaned, citations } = parseCitations(accContent);
              updateAssistant({ content: cleaned, citations });
              break;
            }
            case "reasoning": {
              const r = (payload.content as string) || "";
              accReasoning += r;
              updateAssistant({ reasoning: accReasoning });
              break;
            }
            case "plan": {
              const p = (payload.content as string) || "";
              accPlan += p;
              updateAssistant({ plan: accPlan });
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
                const { text: cleaned, citations } = parseCitations(correctedText);
                updateAssistant({ content: cleaned, citations, status: undefined });
              }
              break;
            }
            case "error": {
              const msg = (payload.message as string) || "agent_error";
              setError(msg);
              updateAssistant({ error: msg });
              break;
            }
            case "done":
              break;
            default:
              break;
          }
        }
      }
      const { text: cleaned, citations } = parseCitations(accContent);
      updateAssistant({ content: cleaned, citations, status: undefined });
    } catch (err) {
      const msg = err instanceof Error && err.name === "AbortError"
        ? "cancelled"
        : err instanceof Error ? err.message : "request_failed";
      setError(msg);
      setMessages((prev) =>
        prev.map((m) => (m.id === assistantId ? { ...m, error: msg } : m)),
      );
    } finally {
      clearStreamTimeout();
      setIsStreaming(false);
      abortRef.current = null;
    }
  }, [isStreaming, messages, resetStreamTimeout, clearStreamTimeout]);

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