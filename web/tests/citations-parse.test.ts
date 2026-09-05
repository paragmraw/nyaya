// Unit tests for the inline citation marker pipeline (chat.ts).
//
// The old `title="ic"` attribute sentinel was replaced by the link's
// corpus-page href (CITE_HREF_PREFIX): a marker that survives the markdown
// pipeline unambiguously and needs no magic attribute text. ChatMessage.tsx
// recognises chips via isCitationHref. Run with:  npm test

import { test } from "node:test";
import assert from "node:assert/strict";
import { CITE_HREF_PREFIX, isCitationHref, parseCitations, stripCitationMarkers } from "../src/lib/chat";

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

// The final-text whitespace collapse must be horizontal-only: collapsing
// `\s{2,}` used to destroy `\n\n` paragraph breaks and list indentation
// before the bubble's markdown render.
test("parseCitations preserves paragraph breaks and blank lines in final text", () => {
  const src = "Murder is culpable homicide.\n\nPunishment may extend to life imprisonment.";
  const { text } = parseCitations(src);
  assert.ok(text.includes("homicide.\n\nPunishment"), `paragraph break lost:\n${text}`);
});

test("parseCitations preserves list indentation in final text", () => {
  const src = "Exceptions:\n- top level\n  - nested sub-item\n  - another";
  const { text } = parseCitations(src);
  assert.ok(text.includes("\n  - nested"), `sub-list indentation lost:\n${text}`);
});

test("parseCitations still collapses runs of spaces and tabs", () => {
  const { text } = parseCitations("A   B\t\tC");
  assert.equal(text, "A B C");
});

// stripCitationMarkers is the streaming-plain path: markers become compact
// plain-text chips without the markdown conversion parseCitations does. The
// full parseCitations pass runs once on the final text.
test("stripCitationMarkers emits plain chips, no markdown links", () => {
  const text = stripCitationMarkers("Offence under [[act: IPC, ref: s.302]] punished.");
  assert.equal(text, "Offence under [IPC · s.302] punished.");
  assert.ok(!text.includes("]("), "no markdown link in streaming-plain output");
  assert.ok(!text.includes("[["), "no raw markers left");
});

test("stripCitationMarkers leaves non-citation text untouched", () => {
  assert.equal(stripCitationMarkers("Plain answer, no markers."), "Plain answer, no markers.");
  assert.equal(stripCitationMarkers(""), "");
});

test("stripCitationMarkers handles multiple markers", () => {
  const text = stripCitationMarkers("[[act: A, ref: 1]] and [[act: B, ref: 2]]");
  assert.equal(text, "[A · 1] and [B · 2]");
});