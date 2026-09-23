"use client";

// useChat: the React adapter around the headless streaming state machine
// (chat/session.ts). This hook owns only what needs React state: the message
// list, the assistant-bubble patch scoping, history building, retry
// trimming, and (debounced) sessionStorage persistence. Everything between
// "send" and the final assistant patch — fetch, SSE parsing, rAF batching,
// citation stripping, tool events, corrections, errors, timeouts, cancel —
// lives in createChatSession and is node-tested in tests/chat-session.test.ts.

import { useCallback, useEffect, useRef, useState } from "react";
import type { ChatHistoryTurn, ChatMessage } from "../api";
import { createPersistWriter, deserializeMessages, readStore, writeStore } from "./persistence";
import { trimForRetry, uid } from "./retry";
import { createChatSession, type ChatSessionPatch } from "./session";

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
  const isStreamingRef = useRef(false);
  const retryTextRef = useRef<string | null>(null);
  const [retryTrigger, setRetryTrigger] = useState(0);
  // A mirror of `messages` so retry/send can read the latest list and mutate
  // refs / call setMessages with plain values OUTSIDE any state updater
  // (React strict mode may invoke updaters more than once, so they must stay
  // pure).
  const messagesRef = useRef<ChatMessage[]>(messages);
  // A mirror of the streaming flag for the same reason.
  const isStreamingFlagRef = useRef(false);
  const runIdRef = useRef(0);
  // One session and one persist writer for the hook's lifetime — stable
  // non-render values, built once via a lazy useState initializer (the
  // if-null ref-init pattern is rejected by react-hooks/refs).
  const [session] = useState(() => createChatSession());
  const [persist] = useState(() => createPersistWriter({ write: writeStore }));

  // Persist message changes: debounced during a stream (the old hook wrote
  // sessionStorage every animation frame), synchronous otherwise. Empty list
  // → clear the stored conversation.
  useEffect(() => {
    messagesRef.current = messages;
    if (isStreamingFlagRef.current) persist.schedule(messages);
    else persist.flushNow(messages);
  }, [messages, persist]);

  const cancel = useCallback(() => {
    session.cancel();
    isStreamingFlagRef.current = false;
    isStreamingRef.current = false;
    setIsStreaming(false);
  }, [session]);

  const reset = useCallback(() => {
    cancel();
    persist.flushNow([]);
    setMessages([]);
    setError(null);
  }, [cancel, persist]);

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
    const runId = ++runIdRef.current;
    isStreamingRef.current = true;
    isStreamingFlagRef.current = true;
    setError(null);
    setIsStreaming(true);

    const userMsg: ChatMessage = { id: uid(), role: "user", content: trimmed, citations: [], tools: [] };
    const assistantId = uid();
    const assistantMsg: ChatMessage = {
      id: assistantId, role: "assistant", content: "", citations: [], tools: [],
    };
    setMessages((prev) => [...prev, userMsg, assistantMsg]);

    const history: ChatHistoryTurn[] = messagesRef.current
      // Failed runs' partial assistant text is excluded: the model never saw
      // its own truncated output as context, and a half-answer can mislead
      // the next turn.
      .filter((m) => m.content && !m.error && (m.role === "user" || m.role === "assistant"))
      .slice(-8)
      .map((m) => ({ role: m.role, content: m.content }));

    const updateAssistant = (patch: ChatSessionPatch) => {
      setMessages((prev) =>
        prev.map((m) => (m.id === assistantId ? { ...m, ...patch } : m)),
      );
    };

    try {
      await session.run({
        message: trimmed,
        history,
        onPatch: updateAssistant,
        onError: (human) => setError(human),
      });
    } finally {
      // Persist synchronously at run end (the debounced writer covers the
      // per-frame tail; a pending debounce re-writes the final state if the
      // last patches landed after this read).
      persist.flushNow(messagesRef.current);
      // Shared streaming state only resets while this is still the current
      // run: in the cancel-then-send sequence this finally fires after a new
      // send has claimed them, and resetting would unlock the composer
      // mid-stream.
      if (runId === runIdRef.current) {
        isStreamingFlagRef.current = false;
        isStreamingRef.current = false;
        setIsStreaming(false);
      }
    }
  }, [session, persist]);

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