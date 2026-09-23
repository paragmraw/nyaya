// The chat streaming state machine, extracted from the useChat hook so the
// whole turn — fetch, SSE read loop, rAF-batched accumulators, incremental
// citation stripping, tool events, the composing reset, the correction
// rebase, error bookends, stream timeout, and cancel — runs headless against
// injected fakes (node-testable, no React, no DOM).
//
// useChat stays a thin adapter: it owns the message list, history building,
// retry trimming, and persistence, and scopes this machine's patches to the
// streaming assistant bubble.
//
// SSE events consumed (contract: chat/nyaya_chat/streaming.py):
//   meta, status, plan, token, reasoning, tool_start, tool_result,
//   citations, correction, ping, error, done.

import type { ChatCitation, ChatHistoryTurn, ChatMessage, ChatToolEvent } from "../api";
import { humanizeError } from "./errors";
import { parseCitations, stripCitationMarkers } from "./citations";
import { StreamingCitationStripper } from "./citation-stream";
import { createFrameBatcher, parseSseBlock } from "./sse";
import { uid } from "./retry";

export type ChatSessionPatch = Partial<ChatMessage> & { requestId?: string };

// Structural minimum of a fetch Response the machine consumes (the injectable
// fetchFn lets tests hand back fake SSE bodies without the DOM's Response).
export type ChatFetchResult = {
  ok: boolean;
  status?: number;
  statusText?: string;
  body?: { getReader(): { read(): Promise<{ done: boolean; value?: Uint8Array }> } };
  json?: () => Promise<unknown>;
};
export type ChatFetch = (input: string, init?: RequestInit) => Promise<ChatFetchResult>;

export type RunParams = {
  message: string;
  history: ChatHistoryTurn[];
  onPatch: (patch: ChatSessionPatch) => void;
  onError: (human: string, rid: string) => void;
};

export type ChatSessionOptions = {
  fetchFn?: ChatFetch;
  raf?: (cb: () => void) => number;
  caf?: (handle: number) => void;
  setTimeoutFn?: (cb: () => void, ms: number) => unknown;
  clearTimeoutFn?: (handle: unknown) => void;
  streamTimeoutMs?: number;
};

export type ChatSession = {
  run: (params: RunParams) => Promise<void>;
  cancel: () => void;
};

const STREAM_TIMEOUT_MS = 90_000;

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

// Final assistant-message patch when the reader loop exits. With `done` seen
// (a `correction` counts — it already rebased the accumulator onto the
// authoritative text) the tokens become the verified answer: one full
// parseCitations pass, contentFinal flips the bubble to the markdown render.
// Without `done` the connection dropped before the stream was bookended, so
// the text must NOT be finalized (a truncated answer can contain half-finished
// constructs like an unclosed **bold** or a torn table): the partial text is
// kept in streaming-plain form, and when no `error` event arrived either the
// run is marked interrupted so the retry affordance appears. Exported for
// unit testing.
export function finalizeAssistantPatch(
  accContent: string,
  sawDone: boolean,
  sawError: boolean,
): { patch: ChatSessionPatch; interrupted: boolean } {
  if (sawDone) {
    const { text, citations } = parseCitations(accContent);
    return {
      patch: { content: text, citations, status: undefined, contentFinal: true },
      interrupted: false,
    };
  }
  return {
    patch: {
      content: stripCitationMarkers(accContent),
      status: undefined,
      ...(sawError ? {} : { error: humanizeError("interrupted") }),
    },
    interrupted: !sawError,
  };
}

export function createChatSession(options: ChatSessionOptions = {}): ChatSession {
  const fetchFn: ChatFetch =
    options.fetchFn ?? ((input, init) => fetch(input, init) as unknown as Promise<ChatFetchResult>);
  // rAF defaults: browsers always have requestAnimationFrame; node (tests,
  // headless runs) does not, so fall back to a timer frame. Same for cancel.
  const raf =
    options.raf ??
    (typeof requestAnimationFrame === "function"
      ? (cb: () => void) => requestAnimationFrame(cb)
      : (cb: () => void) => setTimeout(cb, 16) as unknown as number);
  const caf =
    options.caf ??
    (typeof cancelAnimationFrame === "function"
      ? (h: number) => cancelAnimationFrame(h)
      : (h: number) => clearTimeout(h as unknown as ReturnType<typeof setTimeout>));
  const setT =
    options.setTimeoutFn ?? ((cb: () => void, ms: number) => globalThis.setTimeout(cb, ms));
  const clearT =
    options.clearTimeoutFn ?? ((h: unknown) => globalThis.clearTimeout(h as ReturnType<typeof setTimeout>));
  const streamTimeoutMs = options.streamTimeoutMs ?? STREAM_TIMEOUT_MS;

  // Current run's abort plumbing: cancel() (and the stream timeout) abort the
  // fetch AND reject the pending reader.read() directly, so a run ends even
  // when a fake/real reader does not observe the abort signal itself. The
  // sequence guard keeps a finishing run's cleanup from clobbering a newer
  // run's plumbing (the cancel-then-send sequence: the old run's reader
  // rejection lands asynchronously, after the new run has claimed the refs).
  let abortController: AbortController | null = null;
  let rejectReadFn: ((err: Error) => void) | null = null;
  let timedOut = false;
  let timeoutHandle: unknown = null;
  let runSeq = 0;

  const clearStreamTimeout = () => {
    if (timeoutHandle !== null) {
      clearT(timeoutHandle);
      timeoutHandle = null;
    }
  };

  const resetStreamTimeout = () => {
    clearStreamTimeout();
    timeoutHandle = setT(() => {
      timedOut = true;
      abortController?.abort();
      rejectReadFn?.(Object.assign(new Error("stream timeout"), { name: "TimeoutError" }));
    }, streamTimeoutMs);
  };

  let tools: ChatToolEvent[] = [];

  async function run(params: RunParams): Promise<void> {
    const { message, history, onPatch, onError } = params;
    const myRun = ++runSeq;
    timedOut = false;

    // Token/reasoning/plan deltas accumulate and are flushed to onPatch at
    // most once per animation frame: per-token patches would re-render the
    // markdown bubble per token. `correction` and the final flush bypass the
    // batcher so the last state written is always the authoritative one.
    const stripper = new StreamingCitationStripper();
    let accContent = "";
    let accReasoning = "";
    let accPlan = "";
    let display = ""; // stripper output so far (streaming-plain text)
    let contentDirty = false;
    let reasoningDirty = false;
    let planDirty = false;
    // Bookend detection: the final flush below must only trust the text as
    // the verified answer when the stream was properly bookended. `done`
    // marks a complete stream; an `error` event means the failure was
    // reported; a `correction` already rebased the accumulator.
    let sawDone = false;
    let sawError = false;
    let sawCorrection = false;
    // The `citations` event carries the backend's VERIFIED citation list (the
    // verification pass may have stripped ungrounded markers, so it can
    // disagree with a re-parse of the answer text). It is authoritative: the
    // final flush below must not clobber it with a re-derived list — least
    // surprise rule, an explicit event beats a re-parse.
    let eventCitations: ChatCitation[] | null = null;
    // Set once the response is OK and reading has begun: an error thrown from
    // here on is a mid-stream failure (connection drop), which reads as an
    // interruption (the answer was partial and retryable) — not as the raw
    // transport error text.
    let streamOpen = false;

    const batcher = createFrameBatcher(
      () => {
        if (contentDirty) {
          contentDirty = false;
          onPatch({ content: display });
        }
        if (reasoningDirty) {
          reasoningDirty = false;
          onPatch({ reasoning: accReasoning });
        }
        if (planDirty) {
          planDirty = false;
          onPatch({ plan: accPlan });
        }
      },
      raf,
      caf,
    );

    const controller = new AbortController();
    abortController = controller;

    try {
      const res = await fetchFn("/chat/turn", {
        method: "POST",
        headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
        body: JSON.stringify({ message, history }),
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
          const body: unknown = await res.json?.();
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
      streamOpen = true;
      // Reject the pending read when cancel()/timeout fires even if the
      // underlying reader never observes the abort signal.
      const abortPromise = new Promise<never>((_, rej) => {
        rejectReadFn = rej;
      });
      abortPromise.catch(() => {}); // a late rejection after a clean finish is expected

      resetStreamTimeout();

      while (true) {
        const { done, value } = await Promise.race([reader.read(), abortPromise]);
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
              onPatch({ requestId: (payload.request_id as string) || "" });
              break;
            case "token": {
              const c = (payload.content as string) || "";
              accContent += c;
              display += stripper.feed(c);
              contentDirty = true;
              batcher.schedule();
              break;
            }
            case "reasoning": {
              accReasoning += (payload.content as string) || "";
              reasoningDirty = true;
              batcher.schedule();
              break;
            }
            case "plan": {
              accPlan += (payload.content as string) || "";
              planDirty = true;
              batcher.schedule();
              break;
            }
            case "tool_start": {
              const ev: ChatToolEvent = {
                id: (payload.id as string) || uid(),
                name: (payload.name as string) || "",
                args: payload.args as Record<string, unknown> | undefined,
                state: "start",
              };
              tools = [...tools.filter((t) => t.id !== ev.id), ev];
              onPatch({ tools });
              break;
            }
            case "tool_result": {
              const ev: ChatToolEvent = {
                id: (payload.id as string) || uid(),
                name: (payload.name as string) || "",
                summary: (payload.summary as string) || "",
                state: "result",
              };
              tools = [
                ...tools.filter((t) => t.id !== ev.id),
                { ...(tools.find((t) => t.id === ev.id) ?? ev), ...ev },
              ];
              onPatch({ tools });
              break;
            }
            case "status":
              // A second answer round ("composing") starts a FRESH answer:
              // the earlier round's text was already replaced by its verified
              // correction (or superseded). Without this reset the new
              // round's tokens append to the old round's text and the answer
              // body shows both glued together.
              if ((payload.msg as string) === "composing" && accContent) {
                accContent = "";
                display = "";
                stripper.restart("");
                contentDirty = true;
                batcher.schedule();
              }
              onPatch({ status: (payload.msg as string) || "" });
              break;
            case "ping":
              break;
            case "citations": {
              const cites = (payload.citations as ChatCitation[]) || [];
              if (cites.length > 0) {
                eventCitations = cites;
                onPatch({ citations: cites });
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
                sawCorrection = true;
                accContent = correctedText;
                display = correctedText;
                stripper.restart(correctedText);
                const { text, citations } = parseCitations(correctedText);
                // The corrected text is authoritative — final markdown render
                // starts here even though the stream is still open (done
                // follows immediately).
                onPatch({ content: text, citations, status: undefined, contentFinal: true });
              }
              break;
            }
            case "error": {
              sawError = true;
              // Unified error shape: {message, detail, rid}.
              const code = (payload.message as string) || "agent_error";
              const detail = (payload.detail as string) || "";
              const rid = (payload.rid as string) || "";
              const human = humanizeError(code, detail);
              onError(human, rid);
              onPatch({
                // The human phrasing is what gets rendered; the request id
                // (rid) is surfaced next to it for support/debugging.
                error: human,
                ...(rid ? { requestId: rid } : {}),
              });
              break;
            }
            case "done":
              sawDone = true;
              break;
            default:
              break;
          }
        }
      }
      // Stream complete: flush the final accumulated text authoritatively.
      // With `done` seen, ONE full parseCitations pass (markdown links +
      // citations list) — the bubble switches from streaming-plain to markdown
      // render. Without `done` the connection dropped before the bookend:
      // finalizeAssistantPatch keeps the partial text plain and marks the run
      // interrupted (retryable) instead of rendering truncated markdown.
      batcher.cancel();
      const { patch, interrupted } = finalizeAssistantPatch(accContent, sawDone || sawCorrection, sawError);
      if (eventCitations && !(patch.citations && patch.citations.length > 0)) {
        patch.citations = eventCitations;
      }
      if (interrupted) {
        onError(patch.error as string, "");
      }
      onPatch(patch);
    } catch (err) {
      // Humanize the failure for both the footer note and the failed assistant
      // bubble: stream timeouts (the deliberate abort must keep the timeout
      // copy, not read as a user cancellation), user stops, network errors,
      // non-2xx RequestErrors (message + detail + rid), and anything else.
      // A failure AFTER the stream opened is a dropped connection mid-answer:
      // it reads as an interruption (partial, retryable) rather than surfacing
      // the raw transport error text.
      const isAbort = err instanceof Error && err.name === "AbortError";
      const isDrop = streamOpen && !(err instanceof RequestError);
      const human = timedOut
        ? humanizeError("stream_timeout")
        : isAbort
          ? humanizeError("cancelled")
          : isDrop
            ? humanizeError("interrupted")
            : err instanceof RequestError
              ? humanizeError(err.message, err.detail)
              : humanizeError(err instanceof Error ? err.message : "request_failed");
      const rid = err instanceof RequestError ? err.rid : "";
      onError(human, rid);
      onPatch({
        error: human,
        ...(rid ? { requestId: rid } : {}),
      });
    } finally {
      // No pending frame may fire after abort/error/timeout: a late flush would
      // write partial accumulated text into the aborted assistant bubble.
      batcher.cancel();
      contentDirty = false;
      reasoningDirty = false;
      planDirty = false;
      // Shared abort plumbing only resets while this is still the current
      // run — see the sequence guard above.
      if (myRun === runSeq) {
        clearStreamTimeout();
        abortController = null;
        rejectReadFn = null;
      }
    }
  }

  return {
    run(params) {
      tools = []; // each run starts with its own tool list
      return run(params);
    },
    cancel() {
      clearStreamTimeout();
      abortController?.abort();
      abortController = null;
      rejectReadFn?.(Object.assign(new Error("cancelled"), { name: "AbortError" }));
    },
  };
}