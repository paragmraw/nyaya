// Unit tests for the auto-scroller (chat/auto-scroll.ts).
//
// The old ChatPanel effect read scrollHeight/clientHeight on every message
// flush (forced layout per frame). createAutoScroller maintains a
// near-bottom flag from a PASSIVE scroll listener instead; onContentChanged
// only writes scrollTop when near bottom. Fake elements make it node-testable.
// Run with: npm test

import { test } from "node:test";
import assert from "node:assert/strict";
import { createAutoScroller, type ScrollElement } from "../src/lib/chat/auto-scroll";

function fakeEl(): ScrollElement & { listeners: Map<string, (ev?: unknown) => void>; setTop: (n: number) => void } {
  const el = {
    scrollHeight: 1000,
    scrollTop: 0,
    clientHeight: 500,
    listeners: new Map<string, (ev?: unknown) => void>(),
    addEventListener(type: string, cb: (ev?: unknown) => void) {
      el.listeners.set(type, cb);
    },
    removeEventListener(type: string) {
      el.listeners.delete(type);
    },
  };
  return el as unknown as typeof el;
}

function bottomOf(el: ReturnType<typeof fakeEl>): number {
  return el.scrollHeight - el.scrollTop - el.clientHeight; // 500 when at top
}

test("onContentChanged scrolls when the user is at the bottom", () => {
  const s = createAutoScroller();
  const el = fakeEl();
  s.attach(el);
  el.scrollTop = 500; // scrollHeight - scrollTop - clientHeight = 0 → near bottom
  s.onContentChanged();
  assert.equal(el.scrollTop, el.scrollHeight);
});

test("onContentChanged does NOT scroll when the user scrolled away", () => {
  const s = createAutoScroller();
  const el = fakeEl();
  s.attach(el);
  // User scrolled up: bottom gap > threshold. In the browser any scroll change
  // fires the scroll event, so the fake dispatches it too (the passive
  // listener is what maintains the flag — a bare property write would not).
  el.scrollTop = 100;
  el.listeners.get("scroll")!();
  assert.ok(bottomOf(el) > 120);
  s.onContentChanged();
  assert.equal(el.scrollTop, 100);
});

test("the near-bottom flag follows a real scroll event", () => {
  const s = createAutoScroller();
  const el = fakeEl();
  s.attach(el);
  // Simulate the user scrolling down to the bottom via the listener
  el.scrollTop = 495; // within 120px of the bottom
  el.listeners.get("scroll")!();
  s.onContentChanged();
  assert.equal(el.scrollTop, el.scrollHeight);
});

test("detach removes the scroll listener", () => {
  const s = createAutoScroller();
  const el = fakeEl();
  s.attach(el);
  s.detach();
  assert.equal(el.listeners.get("scroll"), undefined);
});

test("onContentChanged without attach is a no-op", () => {
  const s = createAutoScroller();
  assert.doesNotThrow(() => s.onContentChanged());
});

test("re-attach resets to near-bottom (default follows the stream)", () => {
  const s = createAutoScroller();
  const el = fakeEl();
  s.attach(el);
  el.scrollTop = 0; // scrolled away
  el.listeners.get("scroll")!();
  s.detach();
  s.attach(el);
  s.onContentChanged();
  assert.equal(el.scrollTop, el.scrollHeight);
});