// Unit tests for the pre-paint route body class script (src/lib/route.ts).
//
// Run with:  npx tsx --test tests/route-prepaint.test.ts
//
// The script is inlined as the first child of <body> and must stamp
// body.home / body.info before first paint (home locks the viewport — a
// post-paint application visibly reflows). Assertions mirror theme.test.ts.

import { test } from "node:test";
import assert from "node:assert/strict";
import { routePrepaintScript } from "../src/lib/route";

test("route pre-paint script distinguishes home from info routes", () => {
  const script = routePrepaintScript();
  assert.ok(script.includes("location.pathname"), "script must read the current path");
  assert.ok(script.includes("classList.toggle('home'"), "script must toggle the home class");
  assert.ok(script.includes("classList.toggle('info'"), "script must toggle the info class");
  // Home is exactly "/" or "" — anything else (corpus, citations, …) is info.
  assert.ok(script.includes("'/'"), "home test is the root path");
});

test("route pre-paint script guards a missing body (never breaks first paint)", () => {
  const script = routePrepaintScript();
  assert.ok(script.includes("if(!b)return;"), "a null body must bail out, not throw");
});

test("route pre-paint script is syntactically valid JavaScript (parse only)", () => {
  assert.doesNotThrow(() => new Function(routePrepaintScript()));
});

test("route pre-paint script executes correctly against a fake DOM", () => {
  // Minimal document/location/body stand-in; run the script's logic for both
  // route shapes and assert the resulting class lists.
  function runFor(pathname: string) {
    const classes = new Set<string>(["preexisting"]);
    const document = {
      body: {
        classList: {
          toggle: (name: string, on: boolean) => {
            if (on) classes.add(name);
            else classes.delete(name);
          },
        },
      },
    };
    const location = { pathname };
    const fn = new Function("document", "location", routePrepaintScript());
    fn(document, location);
    return classes;
  }
  assert.deepEqual([...runFor("/")].sort(), ["home", "preexisting"]);
  assert.deepEqual([...runFor("")].sort(), ["home", "preexisting"]);
  assert.deepEqual([...runFor("/corpus/")].sort(), ["info", "preexisting"]);
  assert.deepEqual([...runFor("/citations/")].sort(), ["info", "preexisting"]);
  assert.deepEqual([...runFor("/architecture/")].sort(), ["info", "preexisting"]);
});

test("route pre-paint script bails out without throwing when body is missing", () => {
  const fn = new Function("document", "location", routePrepaintScript());
  assert.doesNotThrow(() => fn({ body: null }, { pathname: "/" }));
});