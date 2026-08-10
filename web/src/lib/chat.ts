"use client";

// useChat: a React hook that streams a chat turn from the FastAPI backend over
// Server-Sent Events and exposes a growing list of messages.
//
// The backend (chat/nyaya_chat/server.py) responds to POST /chat/turn with a
// text/event-stream of typed SSE events:
//   event: token       data: {"content": "..."}        — LLM token deltas
//   event: tool_start  data: {"id","name","args"}      — a tool was called
//   event: tool_result data: {"id","name","summary"}   — a tool returned
//   event: status      data: {"msg": "thinking"}       — progress
//   event: error       data: {"message","detail"}      — failure
//   event: done        data: {}                         — stream complete
//
// The hook assembles token deltas into a single assistant message, collects
// tool events and citations (parsed from [[act: X, ref: Y]] markers the system
// prompt instructs the model to emit), and reports isStreaming/error state.

import { useCallback, useRef, useState } from "react";
import type { ChatCitation, ChatHistoryTurn, ChatMessage, ChatRequest, ChatToolEvent } from "./api";

// Matches [[act: <short_name>, ref: <ref>]] inline citation markers.
const CITE_RE = /\[\[act:\s*([^,\]]+?)\s*,\s*ref:\s*([^\]]+?)\s*\]\]/g;

function parseCitations(text: string): { text: string; citations: ChatCitation[] } {
  const citations: ChatCitation[] = [];
  const seen = new Set<string>();
  let cleaned = text;
  let m: RegExpExecArray | null;
  // Use a fresh regex each call (stateful with /g).
  const re = new RegExp(CITE_RE);
  while ((m = re.exec(text)) !== null) {
    const act = m[1].trim();
    const ref = m[2].trim();
    const key = `${act}|${ref}`;
    if (!seen.has(key)) {
      seen.add(key);
      citations.push({ act, ref });
    }
  }
  cleaned = text.replace(re, "").replace(/\s{2,}/g, " ").trim();
  return { text: cleaned, citations };
}

function uid(): string {
  return Math.random().toString(36).slice(2) + Date.now().toString(36);
}

// Parse a single SSE event block (already accumulated between blank-line
// separators) into { event, data }.
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

export type UseChat = {
  messages: ChatMessage[];
  isStreaming: boolean;
  error: string | null;
  send: (text: string) => Promise<void>;
  cancel: () => void;
  reset: () => void;
};

export function useChat(): UseChat {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [isStreaming, setIsStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setIsStreaming(false);
  }, []);

  const reset = useCallback(() => {
    cancel();
    setMessages([]);
    setError(null);
  }, [cancel]);

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

    // Build history from the current messages (excluding the new user msg and
    // the empty assistant placeholder) for context.
    const history: ChatHistoryTurn[] = messages
      .filter((m) => m.content && (m.role === "user" || m.role === "assistant"))
      .slice(-8)
      .map((m) => ({ role: m.role, content: m.content }));

    const controller = new AbortController();
    abortRef.current = controller;

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

      const updateAssistant = (patch: Partial<ChatMessage>) => {
        setMessages((prev) =>
          prev.map((m) => (m.id === assistantId ? { ...m, ...patch } : m)),
        );
      };
      const appendTool = (ev: ChatToolEvent) => {
        setMessages((prev) =>
          prev.map((m) =>
            m.id === assistantId
              ? { ...m, tools: [...m.tools.filter((t) => t.id !== ev.id), ev] }
              : m,
          ),
        );
      };

      // Read the stream, split into SSE event blocks separated by a blank line.
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
          let payload: Record<string, unknown> = {};
          try { payload = evt.data ? JSON.parse(evt.data) : {}; } catch { /* keep empty */ }
          switch (evt.event) {
            case "token": {
              const c = (payload.content as string) || "";
              accContent += c;
              const { text: cleaned, citations } = parseCitations(accContent);
              updateAssistant({ content: cleaned, citations });
              break;
            }
            case "tool_start": {
              appendTool({
                id: (payload.id as string) || uid(),
                name: (payload.name as string) || "",
                args: payload.args as Record<string, unknown> | undefined,
                state: "start",
              });
              break;
            }
            case "tool_result": {
              appendTool({
                id: (payload.id as string) || uid(),
                name: (payload.name as string) || "",
                summary: (payload.summary as string) || "",
                state: "result",
              });
              break;
            }
            case "status": {
              updateAssistant({ status: (payload.msg as string) || "" });
              break;
            }
            case "error": {
              const msg = (payload.message as string) || "agent_error";
              setError(msg);
              updateAssistant({ error: msg });
              break;
            }
            case "done": {
              break;
            }
            default:
              break;
          }
        }
      }
      // Final cleanup of citations (re-parse in case last tokens completed a marker).
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
      setIsStreaming(false);
      abortRef.current = null;
    }
  }, [isStreaming, messages]);

  return { messages, isStreaming, error, send, cancel, reset };
}