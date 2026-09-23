// Unit tests for the extracted chat streaming state machine
// (chat/session.ts createChatSession).
//
// createChatSession owns everything between "user pressed send" and the final
// assistant patch: the fetch, the SSE read loop, rAF-batched accumulators,
// incremental citation stripping, tool events, the composing reset, the
// correction rebase, error bookends, stream timeout, and cancel. fetch, rAF,
// and timers are injected so the whole machine runs in node against fake SSE
// ReadableStreams. Run with: npm test

import { test } from "node:test";
import assert from "node:assert/strict";
import type { ChatMessage } from "../src/lib/api";
import { createChatSession, type ChatSessionPatch } from "../src/lib/chat/session";

// ─── Fakes ─────────────────────────────────────────────────────────

const encoder = new TextEncoder();

function block(event: string, data: unknown): Uint8Array {
  return encoder.encode(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
}

// A fully-buffered SSE response (all events arrive, stream closes).
function sseResponse(events: Array<[string, unknown]>): unknown {
  const chunks = events.map(([e, d]) => block(e, d));
  return {
    ok: true,
    body: new ReadableStream<Uint8Array>({
      start(c) {
        for (const ch of chunks) c.enqueue(ch);
        c.close();
      },
    }),
  };
}

function errorResponse(status: number, body: unknown): unknown {
  return { ok: false, status, statusText: String(status), json: () => Promise.resolve(body) };
}

// A stream whose bytes the test pushes and closes manually — lets us assert
// mid-stream state (batched patches) before the run settles.
function controlledStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>;
  const stream = new ReadableStream<Uint8Array>({ start(c) { controller = c; } });
  return {
    body: { stream },
    push: (event: string, data: unknown) => controller.enqueue(block(event, data)),
    error: (err: Error) => controller.error(err),
    close: () => controller.close(),
  };
}

// Manual rAF: callbacks queue until pump().
function manualRaf() {
  const queue: Array<() => void> = [];
  return {
    raf: (cb: () => void) => {
      queue.push(cb);
      return queue.length;
    },
    caf: () => {
      /* tests drive pumps manually */
    },
    pump() {
      const cbs = [...queue];
      queue.length = 0;
      for (const cb of cbs) cb();
    },
    get pending() {
      return queue.length;
    },
  };
}

function manualTimeouts() {
  let cb: (() => void) | null = null;
  return {
    setTimeoutFn: (f: () => void) => {
      cb = f;
      return 1;
    },
    clearTimeoutFn: () => {
      cb = null;
    },
    fire() {
      const f = cb;
      cb = null;
      f?.();
    },
  };
}

const tick = () => new Promise((r) => setTimeout(r, 5));

// Run against a closed stream; resolves with everything the run emitted.
async function runClosed(
  events: Array<[string, unknown]>,
  opts?: Parameters<typeof createChatSession>[0],
): Promise<{ patches: ChatSessionPatch[]; errors: Array<{ human: string; rid: string }> }> {
  const patches: ChatSessionPatch[] = [];
  const errors: Array<{ human: string; rid: string }> = [];
  const session = createChatSession({ fetchFn: async () => sseResponse(events), ...opts });
  await session.run({
    message: "q",
    history: [],
    onPatch: (p) => patches.push(p),
    onError: (human, rid) => errors.push({ human, rid }),
  });
  return { patches, errors };
}

const last = (patches: ChatSessionPatch[], key: keyof ChatMessage): unknown =>
  [...patches].reverse().find((p) => key in p)?.[key];

// ─── Token streaming & batching ─────────────────────────────────────

test("tokens accumulate and flush as one content patch per pumped frame", async () => {
  const raf = manualRaf();
  const ctl = controlledStream();
  const patches: ChatSessionPatch[] = [];
  const errors: string[] = [];
  const session = createChatSession({ fetchFn: async () => ({ ok: true, body: ctl.body.stream }), ...raf });
  const runP = session.run({
    message: "q",
    history: [],
    onPatch: (p) => patches.push(p),
    onError: (h) => errors.push(h),
  });
  ctl.push("token", { content: "Hel" });
  ctl.push("token", { content: "lo " });
  ctl.push("token", { content: "world" });
  await tick();
  assert.equal(raf.pending, 1, "one frame is pending regardless of token count");
  raf.pump();
  const contentPatches = patches.filter((p) => "content" in p);
  assert.equal(contentPatches.length, 1, "coalesced into one patch");
  assert.equal(contentPatches[0].content, "Hello world");
  ctl.push("done", {});
  ctl.close();
  await runP;
  assert.equal(errors.length, 0);
});

test("citation markers stream as chips and finalize as markdown links", async () => {
  const raf = manualRaf();
  const ctl = controlledStream();
  const patches: ChatSessionPatch[] = [];
  const session = createChatSession({ fetchFn: async () => ({ ok: true, body: ctl.body.stream }), ...raf });
  const runP = session.run({ message: "q", history: [], onPatch: (p) => patches.push(p), onError: () => {} });
  ctl.push("token", { content: "Murder [[act: IPC, ref: s. 302]]." });
  ctl.push("done", {});
  await tick();
  raf.pump();
  const streamingPatch = patches.find((p) => "content" in p && !("contentFinal" in p));
  assert.ok(streamingPatch, "a streaming-plain patch fired");
  assert.equal(streamingPatch!.content, "Murder [IPC · s. 302].");
  ctl.close();
  await runP;
  // The final flush (contentFinal) re-parses to markdown links + citations.
  const finalPatch = patches.find((p) => p.contentFinal);
  assert.ok(finalPatch);
  assert.match(finalPatch!.content as string, /\[IPC · s\. 302\]\(\/corpus\/\?act=IPC/);
  assert.deepEqual(finalPatch!.citations, [{ act: "IPC", ref: "s. 302" }]);
});

test("citation markers split across token deltas stream correctly", async () => {
  const raf = manualRaf();
  const ctl = controlledStream();
  const patches: ChatSessionPatch[] = [];
  const session = createChatSession({ fetchFn: async () => ({ ok: true, body: ctl.body.stream }), ...raf });
  const runP = session.run({ message: "q", history: [], onPatch: (p) => patches.push(p), onError: () => {} });
  ctl.push("token", { content: "See [[act: IPC, re" });
  ctl.push("token", { content: "f: s. 302]] now" });
  await tick();
  raf.pump();
  const streamingPatch = patches.find((p) => "content" in p && !("contentFinal" in p));
  assert.equal(streamingPatch!.content, "See [IPC · s. 302] now");
  ctl.close();
  await runP;
});

// ─── Multi-leg semantics ─────────────────────────────────────────────

test("a composing status resets the answer accumulator", async () => {
  const { patches } = await runClosed([
    ["token", { content: "stale leg-1 text" }],
    ["status", { msg: "composing", rid: "r1" }],
    ["token", { content: "fresh answer" }],
    ["done", {}],
  ]);
  const final = patches.find((p) => p.contentFinal);
  assert.ok(final);
  // The final content must NOT contain the stale leg-1 text.
  assert.ok(!(final!.content as string).includes("stale"));
  assert.equal(final!.content, "fresh answer");
});

test("plan and reasoning events patch their fields when a frame flushes", async () => {
  const raf = manualRaf();
  const ctl = controlledStream();
  const patches: ChatSessionPatch[] = [];
  const session = createChatSession({ fetchFn: async () => ({ ok: true, body: ctl.body.stream }), ...raf });
  const runP = session.run({ message: "q", history: [], onPatch: (p) => patches.push(p), onError: () => {} });
  ctl.push("plan", { content: "looking up IPC" });
  ctl.push("token", { content: "x" });
  ctl.push("reasoning", { content: "thinking" });
  ctl.push("status", { msg: "searching", rid: "r" });
  await tick();
  raf.pump();
  assert.equal(last(patches, "plan"), "looking up IPC");
  assert.equal(last(patches, "reasoning"), "thinking");
  assert.equal(last(patches, "status"), "searching");
  ctl.close();
  await runP;
});

test("tool events accumulate into a merged tools list", async () => {
  const { patches } = await runClosed([
    ["tool_start", { id: "t1", name: "semantic_query", args: { query: "murder" } }],
    ["tool_result", { id: "t1", name: "semantic_query", summary: "5 hits" }],
    ["done", {}],
  ]);
  const tools = last(patches, "tools") as ChatMessage["tools"];
  assert.equal(tools.length, 1);
  assert.equal(tools[0].state, "result");
  assert.equal(tools[0].summary, "5 hits");
  assert.deepEqual(tools[0].args, { query: "murder" });
});

test("citations events land as a citations patch", async () => {
  const { patches } = await runClosed([
    ["token", { content: "text" }],
    ["citations", { citations: [{ act: "IPC", ref: "s. 420" }] }],
    ["done", {}],
  ]);
  assert.deepEqual(last(patches, "citations"), [{ act: "IPC", ref: "s. 420" }]);
});

test("a correction rebases content onto the verified markdown", async () => {
  const raf = manualRaf();
  const { patches } = await runClosed([
    ["token", { content: "raw unverified" }],
    ["correction", { content: "verified [[act: IPC, ref: s. 302]]" }],
    ["done", {}],
  ], { ...raf });
  const rebase = patches.find((p) => p.contentFinal);
  assert.ok(rebase);
  assert.match(rebase!.content as string, /\[IPC · s\. 302\]/);
  assert.deepEqual(rebase!.citations, [{ act: "IPC", ref: "s. 302" }]);
  // The final flush must NOT re-apply the raw pre-correction tokens.
  const finalPatch = patches[patches.length - 1];
  assert.ok(!(finalPatch.content as string).includes("raw unverified"));
});

test("meta request id lands as a requestId patch", async () => {
  const { patches } = await runClosed([
    ["meta", { request_id: "req-42" }],
    ["done", {}],
  ]);
  assert.equal(last(patches, "requestId"), "req-42");
});

// ─── Failure paths ───────────────────────────────────────────────────

test("an error event humanizes and patches the bubble", async () => {
  const { patches, errors } = await runClosed([
    ["token", { content: "partial" }],
    ["error", { message: "timeout", detail: "too slow", rid: "r9" }],
    ["done", {}],
  ]);
  assert.equal(errors.length, 1);
  assert.match(errors[0].human, /too long to respond/);
  assert.match(last(patches, "error") as string, /too long to respond/);
  assert.equal(last(patches, "requestId"), "r9");
});

test("non-2xx responses surface the unified error shape", async () => {
  const { patches, errors } = await runClosed([], {
    fetchFn: async () => errorResponse(503, { message: "agent_unavailable", detail: "warming", rid: "rr" }),
  });
  assert.equal(errors.length, 1);
  assert.match(errors[0].human, /temporarily unavailable/);
  assert.match(last(patches, "error") as string, /temporarily unavailable/);
});

test("a connection drop before done marks the run interrupted", async () => {
  const ctl = controlledStream();
  const patches: ChatSessionPatch[] = [];
  const errors: string[] = [];
  const session = createChatSession({ fetchFn: async () => ({ ok: true, body: ctl.body.stream }) });
  const runP = session.run({ message: "q", history: [], onPatch: (p) => patches.push(p), onError: (h) => errors.push(h) });
  ctl.push("token", { content: "half an" });
  await tick();
  ctl.error(new Error("connection reset"));
  await runP;
  const finalPatch = patches[patches.length - 1];
  assert.ok(!finalPatch.contentFinal);
  assert.match(finalPatch.error as string, /interrupted/);
  assert.equal(errors.length, 1);
});

test("cancel mid-stream reads as cancelled, not interrupted", async () => {
  // A reader that never resolves on its own; the session's cancel() must end
  // the run regardless (it rejects the pending read directly).
  const session = createChatSession({
    fetchFn: async () => ({
      ok: true,
      body: { getReader: () => ({ read: () => new Promise(() => { /* never */ }) }) },
    }),
  });
  const patches: ChatSessionPatch[] = [];
  const errors: string[] = [];
  const runP = session.run({
    message: "q",
    history: [],
    onPatch: (p) => patches.push(p),
    onError: (h) => errors.push(h),
  });
  await new Promise((r) => setTimeout(r, 10));
  session.cancel();
  await runP;
  assert.match(errors[0], /cancelled/i);
  assert.match(patches[patches.length - 1].error as string, /cancelled/i);
});

test("the stream timeout fires the timeout copy", async () => {
  const timers = manualTimeouts();
  const session = createChatSession({
    streamTimeoutMs: 90_000,
    ...timers,
    fetchFn: async () => ({
      ok: true,
      body: { getReader: () => ({ read: () => new Promise(() => { /* never */ }) }) },
    }),
  });
  const patches: ChatSessionPatch[] = [];
  const errors: string[] = [];
  const runP = session.run({
    message: "q",
    history: [],
    onPatch: (p) => patches.push(p),
    onError: (h) => errors.push(h),
  });
  await new Promise((r) => setTimeout(r, 10));
  timers.fire();
  await runP;
  assert.match(errors[0], /too long to respond/);
  assert.match(patches[patches.length - 1].error as string, /too long to respond/);
});

test("empty stream with done and no tokens finalizes to empty content", async () => {
  const { patches } = await runClosed([["done", {}]]);
  const finalPatch = patches[patches.length - 1];
  assert.equal(finalPatch.contentFinal, true);
  assert.equal(finalPatch.content, "");
});