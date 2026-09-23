// Unit tests for the incremental StreamingCitationStripper (chat/citation-stream.ts).
//
// The stripper replaces the per-frame full-text re-scan (stripCitationMarkers
// on the whole accumulator) with a linear-in-delta scan: it converts complete
// [[act: X, ref: Y]] markers to [X · Y] chips as text arrives, holds back a
// possible partial-marker tail, and flushes the tail literally at the end.
// Run with: npm test

import { test } from "node:test";
import assert from "node:assert/strict";
import { StreamingCitationStripper } from "../src/lib/chat/citation-stream";
import { stripCitationMarkers } from "../src/lib/chat/citations";

function feedAll(parts: string[]): { streamed: string; flushed: string } {
  const s = new StreamingCitationStripper();
  let streamed = "";
  for (const p of parts) streamed += s.feed(p);
  const flushed = s.flush();
  return { streamed, flushed };
}

test("plain text passes through with no holdback", () => {
  const { streamed, flushed } = feedAll(["hello world", " more text"]);
  assert.equal(streamed, "hello world more text");
  assert.equal(flushed, "");
});

test("a complete marker in one delta converts to a chip", () => {
  const { streamed } = feedAll(["[[act: IPC, ref: s. 302]]"]);
  assert.equal(streamed, "[IPC · s. 302]");
});

test("a marker split across deltas is held then converted", () => {
  const { streamed } = feedAll(["Answer [[act: IPC, re", "f: s. 302]] done."]);
  assert.equal(streamed, "Answer [IPC · s. 302] done.");
});

test("a trailing single [ is held for one feed", () => {
  const s = new StreamingCitationStripper();
  assert.equal(s.feed("abc["), "abc");
  assert.equal(s.feed("[act: IPC, ref: s. 302]]"), "[IPC · s. 302]");
});

test("a double bracket that never becomes a marker flushes literally", () => {
  const { streamed, flushed } = feedAll(["a [[ b", " more"]);
  assert.equal(streamed, "a [[ b more");
  assert.equal(flushed, "");
});

test("an incomplete marker tail is held and flushed literally", () => {
  const { streamed, flushed } = feedAll(["see [[act: IPC, ref: s. 302 and then"]);
  assert.equal(streamed, "see ");
  assert.equal(flushed, "[[act: IPC, ref: s. 302 and then");
});

test("a held prefix of [[act: resolves across the boundary", () => {
  const { streamed } = feedAll(["x [[ac", "t: A, ref: B]] y"]);
  assert.equal(streamed, "x [A · B] y");
});

test("an act section exceeding the 512-char bound is not a marker", () => {
  const long = "a".repeat(513);
  const { streamed } = feedAll([`[[act: ${long}]]`]);
  assert.equal(streamed, `[[act: ${long}]]`);
});

test("a ref section exceeding the 2048-char bound is not a marker", () => {
  const long = "r".repeat(2049);
  const { streamed } = feedAll([`[[act: IPC, ref: ${long}]]`]);
  assert.equal(streamed, `[[act: IPC, ref: ${long}]]`);
});

test("multiple markers in one delta all convert", () => {
  const { streamed } = feedAll(["[[act: A, ref: 1]] mid [[act: B, ref: 2]]"]);
  assert.equal(streamed, "[A · 1] mid [B · 2]");
});

test("chunked feeding equals the one-shot strip", () => {
  const text =
    "Para one [[act: IPC, ref: s. 302]].\n\nPara two [see [docs](http://x)] " +
    "and [[act: Constitution, ref: Art. 21]] plus a torn [[act: BNSS, ref: s. 1";
  const s = new StreamingCitationStripper();
  let streamed = "";
  for (let i = 0; i < text.length; i += 7) streamed += s.feed(text.slice(i, i + 7));
  streamed += s.flush();
  assert.equal(streamed, stripCitationMarkers(text));
});

// Review finding: when a feed boundary falls BETWEEN the two "]"s of a
// marker's close, the held candidate ends with exactly one "]" — that may be
// the marker's own first closing bracket, not a bracket inside the ref. The
// streamed display must still end up as a chip, equal to the one-shot strip.
test("a marker straddling the ']]' across deltas still converts", () => {
  const { streamed } = feedAll(["Murder [[act: IPC, ref: s. 302]", "] under IPC."]);
  assert.equal(streamed, "Murder [IPC · s. 302] under IPC.");
});

test("chunk sizes 1 and 2 equal the one-shot strip (fuzz-found boundary class)", () => {
  const text = "Answer [[act: IPC, ref: s. 302]]. More [[act: BNS, ref: s. 103(a)]].";
  for (const size of [1, 2, 3]) {
    const s = new StreamingCitationStripper();
    let streamed = "";
    for (let i = 0; i < text.length; i += size) streamed += s.feed(text.slice(i, i + size));
    streamed += s.flush();
    assert.equal(streamed, stripCitationMarkers(text), `chunk size ${size}`);
  }
});

test("a ref whose interior contains a real ']' is dead even with a trailing one", () => {
  const { streamed } = feedAll(["x [[act: IPC, ref: s] 302]", " more"]);
  assert.equal(streamed, "x [[act: IPC, ref: s] 302] more");
});

test("restart rebases the stripper onto corrected text", () => {
  const s = new StreamingCitationStripper();
  s.feed("raw [[act: IPC, ref: s. 302]]");
  s.restart("corrected [[act: BNS, ref: s. 103]] tail");
  assert.equal(s.feed(" more"), " more");
  assert.equal(s.flush(), "");
});

test("whitespace between comma and ref up to 16 chars is tolerated", () => {
  const { streamed } = feedAll(["[[act: IPC,      ref: s. 302]]"]);
  assert.equal(streamed, "[IPC · s. 302]");
});

test("whitespace beyond 16 chars after the comma kills the marker", () => {
  const tail = " ".repeat(17); // one past the \s{0,16} bound in CITE_RE
  const raw = `[[act: IPC,${tail}ref: s. 302]]`;
  const { streamed } = feedAll([raw]);
  assert.equal(streamed, raw);
});