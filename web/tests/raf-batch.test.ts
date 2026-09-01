// Unit tests for createFrameBatcher (src/lib/chat.ts) — the rAF token-batching
// contract behind plan user-decision 4.
//
// Run with:  npx tsx --test tests/raf-batch.test.ts
//
// node has no requestAnimationFrame, so the batcher's frame scheduler is
// injected: a fake "frame clock" that models browser frame cadence. The core
// guarantee under test: N schedule() calls between two frames coalesce into
// exactly ONE flush() when the frame fires — i.e. per-token DOM work becomes
// per-frame work.

import { test } from "node:test";
import assert from "node:assert/strict";
import { createFrameBatcher } from "../src/lib/chat";

// A deterministic frame clock: schedule() queues a callback for the next
// tick(); each tick() runs all queued callbacks exactly once.
function makeFrameClock() {
  let queued: Array<() => void> = [];
  let nextId = 1;
  const raf = (cb: () => void) => {
    queued.push(cb);
    return nextId++;
  };
  const caf = (handle: number) => {
    queued = queued.filter((_, i) => i !== handle - 1);
  };
  const tick = () => {
    const run = queued;
    queued = [];
    for (const cb of run) cb();
    return run.length;
  };
  return { raf, caf, tick };
}

test("N schedules between frames coalesce into a single flush", () => {
  const clock = makeFrameClock();
  let flushes = 0;
  const b = createFrameBatcher(() => { flushes++; }, clock.raf, clock.caf);

  // Simulate 1000 streamed tokens arriving before the next frame paints.
  for (let i = 0; i < 1000; i++) b.schedule();
  assert.equal(flushes, 0, "nothing flushes before the frame fires");
  const ran = clock.tick();
  assert.equal(flushes, 1, "one frame → exactly one flush for 1000 tokens");
  assert.equal(ran, 1, "the frame ran exactly one queued callback");
});

test("flushes once per frame across consecutive frames", () => {
  const clock = makeFrameClock();
  let flushes = 0;
  const b = createFrameBatcher(() => { flushes++; }, clock.raf, clock.caf);

  b.schedule(); b.schedule();
  clock.tick();
  b.schedule(); b.schedule(); b.schedule();
  clock.tick();
  clock.tick(); // idle frame: nothing queued
  assert.equal(flushes, 2, "two frames with work → two flushes; idle frame flushes nothing");
});

test("cancel prevents the pending flush", () => {
  const clock = makeFrameClock();
  let flushes = 0;
  const b = createFrameBatcher(() => { flushes++; }, clock.raf, clock.caf);

  b.schedule();
  b.cancel();
  clock.tick();
  assert.equal(flushes, 0, "cancelled batch never flushes");
  // The batcher remains usable after cancel.
  b.schedule();
  clock.tick();
  assert.equal(flushes, 1);
});

test("cancel is a no-op with nothing pending", () => {
  const clock = makeFrameClock();
  const b = createFrameBatcher(() => {}, clock.raf, clock.caf);
  assert.doesNotThrow(() => b.cancel());
});

test("a flush during the frame can schedule the next frame (no deadlock)", () => {
  const clock = makeFrameClock();
  let flushes = 0;
  const b = createFrameBatcher(() => {
    flushes++;
    if (flushes < 3) b.schedule(); // re-schedule from inside flush
  }, clock.raf, clock.caf);

  b.schedule();
  clock.tick(); // flush 1, re-schedules
  clock.tick(); // flush 2, re-schedules
  clock.tick(); // flush 3, stops
  assert.equal(flushes, 3);
});