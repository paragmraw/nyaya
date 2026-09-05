// Unit tests for finalizeAssistantPatch (src/lib/chat.ts).
//
// The reader-loop exit decision: with a proper bookend (`done`, or a
// `correction` which rebases the accumulator onto authoritative text) the
// accumulated tokens become the verified answer — full parseCitations pass,
// contentFinal flips the bubble to the markdown render. Without `done` the
// connection dropped before the bookend: the partial text stays in
// streaming-plain form (never markdown-parsed — a truncated answer can carry
// half-finished constructs), and when no `error` event arrived either the run
// is marked interrupted so the retry affordance appears. Run with:  npm test

import { test } from "node:test";
import assert from "node:assert/strict";
import { finalizeAssistantPatch, humanizeError } from "../src/lib/chat";
import { CITE_HREF_PREFIX } from "../src/lib/chat";

test("with done, tokens become the verified answer (markdown + contentFinal)", () => {
  const { patch, interrupted } = finalizeAssistantPatch(
    "Answer [[act: IPC, ref: s.302]].",
    true,
    false,
  );
  assert.equal(interrupted, false);
  assert.equal(patch.contentFinal, true);
  assert.ok(patch.content!.includes(`${CITE_HREF_PREFIX}IPC&ref=s.302`));
  assert.deepEqual(patch.citations, [{ act: "IPC", ref: "s.302" }]);
  assert.equal(patch.error, undefined);
});

test("without done and without error, the run is interrupted and stays plain", () => {
  const acc = "Half a **bold answer that never finished";
  const { patch, interrupted } = finalizeAssistantPatch(acc, false, false);
  assert.equal(interrupted, true);
  assert.equal(patch.contentFinal, undefined, "truncated text must not be finalized");
  assert.equal(patch.content, acc, "partial tokens preserved verbatim (plain form)");
  assert.equal(patch.error, humanizeError("interrupted"));
});

test("without done but with an error event, no duplicate interrupted marker", () => {
  const acc = "Partial text before the failure";
  const { patch, interrupted } = finalizeAssistantPatch(acc, false, true);
  assert.equal(interrupted, false);
  assert.equal(patch.contentFinal, undefined);
  assert.equal(patch.content, acc, "partial tokens preserved (plain form)");
  assert.equal(patch.error, undefined, "the SSE error row already carries the failure");
});

test("an empty stream that lost done still gets an interrupted error", () => {
  const { patch, interrupted } = finalizeAssistantPatch("", false, false);
  assert.equal(interrupted, true);
  assert.equal(patch.error, humanizeError("interrupted"));
  assert.equal(patch.contentFinal, undefined);
});