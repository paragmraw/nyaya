// Unit tests for normaliseMd (src/lib/markdown.ts).
//
// Run with:  npx tsx --test tests/normalise-md.test.ts
//
// These tests assert on the *normalised markdown text* — the contract is that
// after normalisation, GFM tables (header row + separator row + data rows)
// must appear on consecutive lines with no blank lines between them, and
// malformed headings like "##3." must be repaired to "## 3.".

import { test } from "node:test";
import assert from "node:assert/strict";
import { normaliseMd } from "../src/lib/markdown";

// A GFM table is a run of consecutive non-blank lines where:
//   line 0: header row (contains |, starts/ends with | or has | inside)
//   line 1: separator row (only |, -, :, spaces)
//   line 2+: data rows
// We detect a valid table block as: a separator line whose preceding line is
// a non-blank pipe line and whose following lines (until a blank line) are
// also pipe lines. Returns the count of valid table blocks.
function countValidTables(md: string): number {
  const lines = md.split("\n");
  const sepRe = /^\s*\|?\s*:?-{2,}(:?\s*\|\s*:?-{2,})*:?\s*\|?\s*$/;
  const pipeRe = /^\s*\|.*\|\s*$/;
  let count = 0;
  let i = 0;
  while (i < lines.length) {
    if (sepRe.test(lines[i].trim()) && i > 0 && pipeRe.test(lines[i - 1].trim())) {
      // Found a separator with a header above it; verify data rows below are
      // consecutive pipe lines (no blank line between header/sep/data).
      let j = i + 1;
      while (j < lines.length && lines[j].trim() !== "" && pipeRe.test(lines[j].trim())) j++;
      // Also ensure no blank line between header and separator.
      if (lines[i - 1].trim() !== "") count++;
      i = j;
    } else {
      i++;
    }
  }
  return count;
}

test("passes through a well-formed single table unchanged", () => {
  const src = [
    "Some intro text.",
    "",
    "| A | B |",
    "|---|---|",
    "| 1 | 2 |",
  ].join("\n");
  const out = normaliseMd(src);
  assert.equal(countValidTables(out), 1);
});

test("repairs malformed ##N. headings to ## N.", () => {
  const src = "Intro. --- ##3. Key terms";
  const out = normaliseMd(src);
  // After repair the heading should start a line with "## 3."
  assert.match(out, /(^|\n)## 3\. Key terms/);
  assert.doesNotMatch(out, /##3\./);
});

test("splits a table header jammed after a heading onto its own line", () => {
  // Model emits header glued to heading; separator on next line.
  const src = [
    "Some text.",
    "## 3. Key terms (plain-English explanations) | Term | Explanation (first appearance) |",
    "|------|---------------------------------|",
    "| Culpable homicide | Causing a person's death... |",
    "| Murder | Culpable homicide that is not covered... |",
  ].join("\n");
  const out = normaliseMd(src);
  // The header row "| Term | Explanation (first appearance) |" must be on its
  // own line, immediately followed by the separator on the next line.
  assert.match(
    out,
    /\| Term \| Explanation \(first appearance\) \|\n\|------\|---/,
  );
  assert.equal(countValidTables(out), 1);
});

test("renders multiple jammed tables (the original bug scenario)", () => {
  // Mimics the real model output: three tables, where the 2nd and 3rd have
  // their headers jammed after malformed headings on the same line as a
  // preceding horizontal rule and sentence text.
  const src = [
    "Short answer. All murders are culpable homicides. --- ##1. What the law says",
    "| Provision | Core definition |",
    "|---|---|",
    "| s.299 | Culpable homicide |",
    "",
    "Culpable homicide not amounting to murder → up to ten years. --- ##3. Key terms | Term | Explanation |",
    "|------|---------------------------------|",
    "| Culpable homicide | Causing death... |",
    "| Murder | Culpable homicide not covered... |",
    "",
    "Some text. --- ##4. Quick comparison table | Aspect | A | B |",
    "|---|---|---|",
    "| Mental state | intention | same + no exception |",
    "| Punishment | up to 10 years | death or life |",
  ].join("\n");
  const out = normaliseMd(src);
  // All three malformed headings must be repaired.
  assert.doesNotMatch(out, /##[0-9]\./);
  // All three tables must be recognised as valid GFM table blocks.
  assert.equal(countValidTables(out), 3, `expected 3 valid tables, got ${countValidTables(out)}\n---\n${out}`);
});

test("does not split a valid no-leading-pipe header row (repair pass only)", () => {
  // The header-repair pass should NOT split a header row like "Score | Result"
  // when the text before the first | is a single word (no heading marker, no
  // sentence-ending punctuation). NOTE: a no-leading-pipe GFM header row is
  // not recognised by the blank-line inserter's table-row detector (which
  // requires a leading |), so we only assert that the repair pass leaves the
  // header intact — the broader leading-pipe handling is a separate concern.
  const src = [
    "Intro.",
    "",
    "Score | Result",
    "|---|---|",
    "| 10 | win |",
  ].join("\n");
  const out = normaliseMd(src);
  // The header row should remain intact on one line (not split into
  // "Score" + "| Result").
  assert.match(out, /(^|\n)Score \| Result(\n|$)/);
  assert.doesNotMatch(out, /(^|\n)Score\n\| Result/);
});

test("leaves a heading mid-sentence split intact", () => {
  const src = "Some prose. ### Heading";
  const out = normaliseMd(src);
  assert.match(out, /(^|\n)### Heading$/m);
});

// Spaced dashes in legal prose ("death - or life imprisonment") must NOT be
// promoted to list bullets — only a dash after sentence-ending punctuation
// plausibly starts an item.
test("does not turn a mid-sentence prose dash into a list bullet", () => {
  const src = "Punishable with death - or life imprisonment - for the remainder.";
  const out = normaliseMd(src);
  assert.ok(!/(^|\n)- /.test(out), `prose dash became a bullet:\n${out}`);
});

test("still bullets a dash after sentence-ending punctuation", () => {
  const src = "The court held murder liable. - Intent was central.";
  const out = normaliseMd(src);
  assert.match(out, /(^|\n)- Intent was central/);
});

test("still bullets a dash after a colon", () => {
  const src = "Reasons: - first the intent - then the act";
  const out = normaliseMd(src);
  assert.match(out, /(^|\n)- first the intent/);
});

test("keeps genuine multi-line list markers on their own lines", () => {
  const src = "Reasons:\n- first\n- second";
  const out = normaliseMd(src);
  assert.match(out, /(^|\n)- first/);
  assert.match(out, /(^|\n)- second/);
});