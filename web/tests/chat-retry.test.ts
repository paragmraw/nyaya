// Unit tests for the chat retry-trim logic and the sessionStorage
// conversation persistence (chat.ts).
//
// The `oldTrimForRetry` helper below is a verbatim port of the pre-Task-8
// implementation (git show HEAD:web/src/lib/chat.ts — the retry() callback).
// The "repro" tests pin the two bugs it caused; the tests against the real
// exported trimForRetry pin the fix. Run with:  npm test

import { test } from "node:test";
import assert from "node:assert/strict";
import type { ChatMessage } from "../src/lib/api";
import { deserializeMessages, serializeMessages, trimForRetry } from "../src/lib/chat";

function msg(id: string, role: "user" | "assistant", content = "", extra: Partial<ChatMessage> = {}): ChatMessage {
  return { id, role, content, citations: [], tools: [], ...extra };
}

const U1 = msg("u1", "user", "first question");
const A1_OK = msg("a1", "assistant", "an answer with content");
const U2 = msg("u2", "user", "second question");
const A2_FAIL = msg("a2", "assistant", "", { error: "Something went wrong" });

// Verbatim port of the pre-fix retry trim (side-effect-free subset: the ref
// mutation inside the updater was the other half of the bug and cannot be
// expressed in a pure helper — see the strict-mode notes in chat.ts).
function oldTrimForRetry(prev: ChatMessage[]): ChatMessage[] {
  const lastUser = [...prev].reverse().find((m) => m.role === "user");
  if (lastUser) {
    const lastAssistantIdx = [...prev].reverse().findIndex((m) => m.role === "assistant");
    return lastAssistantIdx === -1
      ? prev
      : prev.slice(0, prev.length - 1 - lastAssistantIdx);
  }
  return prev;
}

test("repro: old logic retained the trailing user message, which send() then re-appends (duplicate)", () => {
  const { trimmed } = { trimmed: oldTrimForRetry([U1, A1_OK, U2, A2_FAIL]) };
  // After the trim, useChat.send(retryText) appends the same user message
  // again, so the old behavior produced [U1, A1_OK, U2, U2] + assistant.
  assert.equal(trimmed.length, 3);
  assert.equal(trimmed[2].content, "second question");
  // Correct expectation (what trimForRetry returns):
  assert.deepEqual(trimForRetry([U1, A1_OK, U2, A2_FAIL]).trimmed, [U1, A1_OK]);
});

test("repro: old logic erased earlier successful turns when the failed run had no assistant bubble", () => {
  // A mid-stream refresh followed by retry: the interrupted assistant bubble
  // is gone, so "the last assistant anywhere" is A1_OK and the trim cut into
  // the successful first turn.
  const trimmedOld = oldTrimForRetry([U1, A1_OK, U2]);
  assert.equal(trimmedOld.length, 1, "old logic dropped A1_OK, the turn being retried survived via retryText");
  // Correct expectation: only the trailing unanswered run is removed.
  assert.deepEqual(trimForRetry([U1, A1_OK, U2]).trimmed, [U1, A1_OK]);
});

test("trimForRetry removes the trailing failed run (user + assistant)", () => {
  const { trimmed, text } = trimForRetry([U1, A1_OK, U2, A2_FAIL]);
  assert.deepEqual(trimmed, [U1, A1_OK]);
  assert.equal(text, "second question");
});

test("trimForRetry with no assistant after the last user keeps earlier turns", () => {
  const { trimmed, text } = trimForRetry([U1, A1_OK, U2]);
  assert.deepEqual(trimmed, [U1, A1_OK]);
  assert.equal(text, "second question");
});

test("trimForRetry when the only user message's run failed", () => {
  const { trimmed, text } = trimForRetry([U1, msg("a1", "assistant", "", { error: "x" })]);
  assert.deepEqual(trimmed, []);
  assert.equal(text, "first question");
});

test("trimForRetry is a no-op when there is no user message to resend", () => {
  const onlyBot = [msg("a", "assistant", "hello")];
  const { trimmed, text } = trimForRetry(onlyBot);
  assert.equal(text, null);
  assert.deepEqual(trimmed, onlyBot);
});

test("message list round-trips through sessionStorage serialization", () => {
  const messages = [U1, A1_OK, U2, { ...A2_FAIL, tools: [{ id: "t1", name: "get_section", state: "result" as const, summary: "{}" }] }];
  const restored = deserializeMessages(serializeMessages(messages));
  assert.equal(restored.length, 4);
  assert.deepEqual(restored[0], U1);
  assert.equal(restored[3].tools[0].name, "get_section");
  assert.equal(restored[3].error, "Something went wrong");
});

test("deserializeMessages marks a trailing interrupted (contentless) assistant run as retryable", () => {
  const raw = serializeMessages([U1, A1_OK, U2, msg("a2", "assistant", "")]);
  const restored = deserializeMessages(raw);
  assert.match(restored[3].error ?? "", /interrupted/i);
  // A contentless user message is never marked.
  const raw2 = serializeMessages([U1, msg("u2", "user", "")]);
  assert.equal(deserializeMessages(raw2)[1].error, undefined);
});

test("deserializeMessages rejects garbage payloads", () => {
  assert.deepEqual(deserializeMessages(null), []);
  assert.deepEqual(deserializeMessages(""), []);
  assert.deepEqual(deserializeMessages("not json"), []);
  assert.deepEqual(deserializeMessages(JSON.stringify({ v: 1 })), []);
  assert.deepEqual(deserializeMessages(JSON.stringify({ messages: [42, "x", null] })), []);
  assert.deepEqual(deserializeMessages(JSON.stringify({ messages: [{ role: "assistant", content: "ok" }] })), []);
});