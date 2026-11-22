// Unit tests for parseCorpusDeepLink (lib/deep-link.ts).
//
// Citation chips link to /corpus/?act=IPC&ref=s.302; the corpus page resolves
// that to the act's row. The parser is pure so it can be tested with node:test.
// Run with:  npm test

import { test } from "node:test";
import assert from "node:assert/strict";
import { parseCorpusDeepLink, resolveDeepLinkRow } from "../src/lib/deep-link";

test("parses act and ref from a canonical deep-link search string", () => {
  assert.deepEqual(parseCorpusDeepLink("?act=IPC&ref=s.302"), {
    act: "IPC",
    ref: "s.302",
  });
});

test("accepts the search string with or without the leading question mark", () => {
  assert.deepEqual(parseCorpusDeepLink("act=IPC&ref=Art.21"), {
    act: "IPC",
    ref: "Art.21",
  });
});

test("act without ref yields a null ref (notice names the act alone)", () => {
  assert.deepEqual(parseCorpusDeepLink("?act=Constitution"), {
    act: "Constitution",
    ref: null,
  });
});

test("empty ref param is normalised to null, not empty string", () => {
  assert.deepEqual(parseCorpusDeepLink("?act=IPC&ref="), { act: "IPC", ref: null });
});

test("URL-encoded act/ref values are decoded", () => {
  assert.deepEqual(parseCorpusDeepLink("?act=IPC&ref=s.%20302%20proviso"), {
    act: "IPC",
    ref: "s. 302 proviso",
  });
});

test("surrounding whitespace on params is trimmed", () => {
  assert.deepEqual(parseCorpusDeepLink("?act=%20IPC%20&ref=%20s.302%20"), {
    act: "IPC",
    ref: "s.302",
  });
});

test("no act param (or empty) is not a deep link", () => {
  assert.equal(parseCorpusDeepLink(""), null);
  assert.equal(parseCorpusDeepLink("?ref=s.302"), null);
  assert.equal(parseCorpusDeepLink("?act=&ref=s.302"), null);
  assert.equal(parseCorpusDeepLink("?utm_source=chat"), null);
});

test("parses v2 INSC, HC neutral, CNR, CPC O.7 R.11, and s.65B(4) deep-links", () => {
  assert.deepEqual(parseCorpusDeepLink("?act=SC&ref=2017%20INSC%20801"), {
    act: "SC",
    ref: "2017 INSC 801",
  });
  assert.deepEqual(parseCorpusDeepLink("?act=DHC&ref=2024%3ADHC%3A1234"), {
    act: "DHC",
    ref: "2024:DHC:1234",
  });
  assert.deepEqual(parseCorpusDeepLink("?act=CNR&ref=MHAU019999992015"), {
    act: "CNR",
    ref: "MHAU019999992015",
  });
  assert.deepEqual(parseCorpusDeepLink("?act=CPC&ref=O.7%20R.11"), {
    act: "CPC",
    ref: "O.7 R.11",
  });
  assert.deepEqual(parseCorpusDeepLink("?act=EvidenceAct&ref=s.65B(4)"), {
    act: "EvidenceAct",
    ref: "s.65B(4)",
  });
});

test("resolveDeepLinkRow matches exact short_names and aliases case-insensitively", () => {
  const rows = [
    { short_name: "IPC" },
    { short_name: "EvidenceAct", aliases: ["IEA", "Indian Evidence Act"] },
    { short_name: "SC", aliases: ["Supreme Court"] },
    { short_name: "HC", aliases: ["DHC", "BHC", "High Court"] },
    { short_name: "DDL", aliases: ["CNR", "District Court"] },
  ];
  assert.equal(resolveDeepLinkRow("ipc", rows), "IPC");
  assert.equal(resolveDeepLinkRow("IEA", rows), "EvidenceAct");
  assert.equal(resolveDeepLinkRow("DHC", rows), "HC");
  assert.equal(resolveDeepLinkRow("CNR", rows), "DDL");
  assert.equal(resolveDeepLinkRow("UnknownAct", rows), null);
});