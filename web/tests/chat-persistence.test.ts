// Unit tests for the debounced persistence writer (chat/persistence.ts).
//
// The old hook wrote sessionStorage on EVERY messages change — per animation
// frame during streaming. createPersistWriter coalesces those writes into one
// trailing-debounce write (500ms default), with an immediate flushNow for run
// end / reset. Timers and the sink are injected so this runs in node.
// Run with: npm test

import { test } from "node:test";
import assert from "node:assert/strict";
import { createPersistWriter } from "../src/lib/chat/persistence";
import type { ChatMessage } from "../src/lib/api";

function msg(content: string): ChatMessage {
  return { id: content, role: "user", content, citations: [], tools: [] };
}

function manualTimers() {
  const pending = new Map<unknown, () => void>();
  let next = 1;
  return {
    setTimeoutFn: (cb: () => void) => {
      const h = next++;
      pending.set(h, cb);
      return h;
    },
    clearTimeoutFn: (h: unknown) => {
      pending.delete(h as number);
    },
    fireAll() {
      for (const cb of [...pending.values()]) cb();
      pending.clear();
    },
    get size() {
      return pending.size;
    },
  };
}

test("schedule coalesces many calls into one trailing write", () => {
  const t = manualTimers();
  const writes: (string | null)[] = [];
  const w = createPersistWriter({
    delayMs: 500,
    ...t,
    write: (raw) => writes.push(raw),
  });
  w.schedule([msg("a")]);
  w.schedule([msg("a"), msg("b")]);
  w.schedule([msg("a"), msg("b"), msg("c")]);
  assert.equal(writes.length, 0);
  t.fireAll();
  assert.equal(writes.length, 1);
  assert.equal(writes[0], JSON.stringify({ v: 1, messages: [msg("a"), msg("b"), msg("c")] }));
});

test("flushNow writes immediately and cancels the pending debounce", () => {
  const t = manualTimers();
  const writes: (string | null)[] = [];
  const w = createPersistWriter({ delayMs: 500, ...t, write: (raw) => writes.push(raw) });
  w.schedule([msg("a")]);
  w.flushNow([msg("final")]);
  assert.equal(writes.length, 1);
  assert.match(writes[0]!, /"final"/);
  t.fireAll();
  assert.equal(writes.length, 1); // debounce was cancelled
});

test("an empty list writes null (clears storage)", () => {
  const t = manualTimers();
  const writes: (string | null)[] = [];
  const w = createPersistWriter({ delayMs: 500, ...t, write: (raw) => writes.push(raw) });
  w.flushNow([]);
  assert.deepEqual(writes, [null]);
});

test("cancel drops the pending write without flushing", () => {
  const t = manualTimers();
  const writes: (string | null)[] = [];
  const w = createPersistWriter({ delayMs: 500, ...t, write: (raw) => writes.push(raw) });
  w.schedule([msg("a")]);
  w.cancel();
  t.fireAll();
  assert.equal(writes.length, 0);
});

// Review finding: the debounced write must also defer SERIALIZATION — the old
// code stringified the whole transcript on every schedule() call, so streaming
// still paid one full JSON.stringify per frame patch even though the write
// itself was debounced.
test("schedule does not serialize until the timer fires", () => {
  const t = manualTimers();
  let serializes = 0;
  const w = createPersistWriter({
    delayMs: 500,
    ...t,
    write: () => {},
    serialize: (messages) => {
      serializes += 1;
      return JSON.stringify({ v: 1, messages });
    },
  });
  w.schedule([msg("a")]);
  w.schedule([msg("a"), msg("b")]);
  assert.equal(serializes, 0, "no serialization before the timer fires");
  t.fireAll();
  assert.equal(serializes, 1, "exactly one serialization at fire time");
});