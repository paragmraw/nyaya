// Unit tests for parseSseBlock (chat.ts).
//
// Run with:  npx tsx --test tests/sse-parse.test.ts
//
// The SSE spec says: multiple `data:` lines are joined with a literal "\n",
// and only the single leading space after `data:` is stripped. The backend's
// encoder emits single-line JSON payloads, so this is a hardening — but the
// parser must not blind-trim data (that would corrupt payloads with
// meaningful leading/trailing whitespace inside the JSON string).

import { test } from "node:test";
import assert from "node:assert/strict";
import { parseSseBlock } from "../src/lib/chat";

test("parses a single-line data payload", () => {
  const evt = parseSseBlock('event: token\ndata: {"content": "hi"}');
  assert.deepEqual(evt, { event: "token", data: '{"content": "hi"}' });
});

test("strips only the single leading space after data:", () => {
  // "data:  two" has two spaces: one is the field separator, the second is data.
  const evt = parseSseBlock("data:  two");
  assert.equal(evt?.data, " two");
});

test("does not trim trailing or interior whitespace from data", () => {
  const evt = parseSseBlock('data: {"content": " padded "}  ');
  assert.equal(evt?.data, '{"content": " padded "}  ');
});

test("joins multiple data lines with a newline (SSE spec)", () => {
  const evt = parseSseBlock("event: token\ndata: line 1\ndata: line 2");
  assert.equal(evt?.event, "token");
  assert.equal(evt?.data, "line 1\nline 2");
});

test("defaults the event name to message", () => {
  const evt = parseSseBlock("data: {}");
  assert.equal(evt?.event, "message");
  assert.equal(evt?.data, "{}");
});

test("returns null when the block has no data line", () => {
  assert.equal(parseSseBlock("event: done"), null);
  assert.equal(parseSseBlock(""), null);
});