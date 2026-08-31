// Unit tests for the inline citation marker pipeline (chat.ts).
//
// The old `title="ic"` attribute sentinel was replaced by the link's
// corpus-page href (CITE_HREF_PREFIX): a marker that survives the markdown
// pipeline unambiguously and needs no magic attribute text. ChatMessage.tsx
// recognises chips via isCitationHref. Run with:  npm test

import { test } from "node:test";
import assert from "node:assert/strict";
import { CITE_HREF_PREFIX, isCitationHref, parseCitations } from "../src/lib/chat";

test("parseCitations converts [[act, ref]] markers into corpus hrefs", () => {
  const { text, citations } = parseCitations("Offence under [[act: IPC, ref: s.302]].");
  assert.ok(text.startsWith("Offence under"));
  assert.ok(text.includes(`${CITE_HREF_PREFIX}IPC&ref=s.302`));
  assert.deepEqual(citations, [{ act: "IPC", ref: "s.302" }]);
});

test("citation links no longer carry the title=\"ic\" attribute sentinel", () => {
  const { text } = parseCitations("[[act: Constitution, ref: Art.21]]");
  assert.ok(!text.includes('"ic"'), "sentinel must be gone from the emitted markdown");
  assert.ok(!text.includes("title="));
  assert.ok(text.includes(`${CITE_HREF_PREFIX}Constitution&ref=Art.21`));
});

test("duplicate citation markers are de-duplicated in the pairs list", () => {
  const { citations } = parseCitations("[[act: IPC, ref: s.302]] again [[act: IPC, ref: s.302]]");
  assert.deepEqual(citations, [{ act: "IPC", ref: "s.302" }]);
});

test("isCitationHref recognises exactly the produced hrefs", () => {
  assert.ok(isCitationHref(`${CITE_HREF_PREFIX}IPC&ref=s.302`));
  assert.ok(!isCitationHref("/corpus/"));
  assert.ok(!isCitationHref("https://example.com"));
  assert.ok(!isCitationHref(undefined));
});

test("act/ref values containing spaces are URL-encoded but keep the prefix marker", () => {
  const { text } = parseCitations("[[act: IPC, ref: s. 302 proviso]]");
  assert.ok(text.includes(`${CITE_HREF_PREFIX}IPC&ref=s.%20302%20proviso`));
});