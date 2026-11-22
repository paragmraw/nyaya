// Unit tests for the shareable-view query helpers (lib/url-query.ts).
// The corpus table derives sort/filter from the URL; these cover the pure
// parse/serialize layer. Run with:  npm test

import { test } from "node:test";
import assert from "node:assert/strict";
import { parseViewParams, serializeViewParams, filterCorpusRows } from "../src/lib/url-query";

test("parseViewParams defaults to curated order, asc, no filter", () => {
  assert.deepEqual(parseViewParams(""), { sort: -1, dir: "asc", status: "all", category: "all" });
  assert.deepEqual(parseViewParams("?"), { sort: -1, dir: "asc", status: "all", category: "all" });
});

test("parseViewParams reads sort, dir, status, and category", () => {
  assert.deepEqual(parseViewParams("?sort=3&dir=desc&status=live&category=judiciary"), {
    sort: 3,
    dir: "desc",
    status: "live",
    category: "judiciary",
  });
});

test("out-of-range or non-numeric sort falls back to curated order", () => {
  assert.equal(parseViewParams("?sort=9").sort, -1);
  assert.equal(parseViewParams("?sort=-2").sort, -1);
  assert.equal(parseViewParams("?sort=abc").sort, -1);
});

test("invalid dir falls back to asc", () => {
  assert.equal(parseViewParams("?dir=down").dir, "asc");
});

// serializeViewParams: patches merge, defaults are dropped so shared links
// stay minimal and clearing a filter cleans the URL.
test("serializeViewParams writes a full sort patch", () => {
  assert.equal(serializeViewParams("", { sort: 1, dir: "desc" }), "sort=1&dir=desc");
});

test("serializeViewParams merges into existing params", () => {
  assert.equal(
    serializeViewParams("?sort=1&dir=asc&status=beta", { dir: "desc" }),
    "sort=1&dir=desc&status=beta",
  );
});

test("serializeViewParams drops defaults: clearing the filter cleans the URL", () => {
  assert.equal(serializeViewParams("?sort=2&dir=asc&status=live", { status: "all" }), "sort=2&dir=asc");
});

test("serializeViewParams removing the sort drops sort and dir together", () => {
  assert.equal(serializeViewParams("?sort=2&dir=desc&status=coming", { sort: -1 }), "status=coming");
});

test("serializeViewParams round-trips through parseViewParams", () => {
  const q = serializeViewParams("", { sort: 4, dir: "desc", status: "beta" });
  assert.deepEqual(parseViewParams(`?${q}`), { sort: 4, dir: "desc", status: "beta", category: "all" });
  const q5 = serializeViewParams("", { sort: 5, dir: "asc", status: "live", category: "legislation" });
  assert.deepEqual(parseViewParams(`?${q5}`), { sort: 5, dir: "asc", status: "live", category: "legislation" });
});

test("parseViewParams and serializeViewParams handle category filter cleanly", () => {
  assert.equal(typeof filterCorpusRows, "function");
  const q = serializeViewParams("", { category: "judiciary" });
  assert.equal(q, "category=judiciary");
  assert.equal(parseViewParams(`?${q}`).category, "judiciary");
  assert.equal(serializeViewParams(`?${q}`, { category: "all" }), "");
});