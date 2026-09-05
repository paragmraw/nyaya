// Unit tests for the shareable-view query helpers (lib/url-query.ts).
// The corpus table derives sort/filter from the URL; these cover the pure
// parse/serialize layer. Run with:  npm test

import { test } from "node:test";
import assert from "node:assert/strict";
import { parseViewParams, serializeViewParams } from "../src/lib/url-query";

test("parseViewParams defaults to curated order, asc, no filter", () => {
  assert.deepEqual(parseViewParams(""), { sort: -1, dir: "asc", status: "all" });
  assert.deepEqual(parseViewParams("?"), { sort: -1, dir: "asc", status: "all" });
});

test("parseViewParams reads sort, dir, and status", () => {
  assert.deepEqual(parseViewParams("?sort=3&dir=desc&status=live"), {
    sort: 3,
    dir: "desc",
    status: "live",
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
  assert.deepEqual(parseViewParams(`?${q}`), { sort: 4, dir: "desc", status: "beta" });
});