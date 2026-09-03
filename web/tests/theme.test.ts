// Unit tests for the dark-mode plumbing (src/lib/theme.ts).
//
// Run with:  npx tsx --test tests/theme.test.ts
//
// The pre-paint script is the FOUC-critical piece: a stored choice must win
// over the OS setting, an invalid/missing stored value must fall back to the
// OS, and nothing may ever throw (storage can be blocked).

import { test } from "node:test";
import assert from "node:assert/strict";
import {
  THEME_STORAGE_KEY,
  resolveTheme,
  themePrepaintScript,
} from "../src/lib/theme";

test("resolveTheme: an explicit valid stored choice always wins", () => {
  assert.equal(resolveTheme("dark", false), "dark");
  assert.equal(resolveTheme("light", true), "light");
  assert.equal(resolveTheme("dark", true), "dark");
  assert.equal(resolveTheme("light", false), "light");
});

test("resolveTheme: invalid or missing stored values fall back to the OS", () => {
  assert.equal(resolveTheme(null, true), "dark");
  assert.equal(resolveTheme(null, false), "light");
  assert.equal(resolveTheme(undefined, true), "dark");
  assert.equal(resolveTheme("", true), "dark");
  assert.equal(resolveTheme("system", true), "dark");
  assert.equal(resolveTheme("blue", false), "light");
});

test("storage key is stable (persisted choices would silently reset otherwise)", () => {
  assert.equal(THEME_STORAGE_KEY, "nyaya-theme");
});

test("pre-paint script prefers the stored theme and falls back to matchMedia", () => {
  const script = themePrepaintScript();
  assert.ok(script.includes(THEME_STORAGE_KEY), "script must read the canonical key");
  assert.ok(script.includes("prefers-color-scheme: dark"), "script must consult the OS setting");
  assert.ok(script.includes("localStorage.getItem"), "script must read the stored choice");
  assert.ok(script.includes("setAttribute('data-theme'"), "script must stamp data-theme on <html>");
  // Never break first paint, even with storage blocked.
  assert.ok(script.includes("try{") && script.includes("catch("), "storage access must be guarded");
});

test("pre-paint script is syntactically valid JavaScript (parse only, no execution)", () => {
  assert.doesNotThrow(() => new Function(themePrepaintScript()));
});