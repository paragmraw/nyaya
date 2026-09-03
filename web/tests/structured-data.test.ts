// Structural validation of the site-identity + JSON-LD surface.
//
// Run with:  npx tsx --test tests/structured-data.test.ts
//
// Guards against the two regressions this task fixed:
//   1. schemas pointing at the phantom `nyaya.example.com` domain (or any
//      route that does not exist in src/app);
//   2. JSON-LD anchors (`/route/#fragment`) referencing DOM ids that are
//      never rendered — every anchor target must exist in component source.

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync, existsSync } from "node:fs";
import { join } from "node:path";

import {
  siteSchema,
  homeSchema,
  corpusSchema,
  citationsSchema,
  architectureSchema,
  faqSchema,
} from "../src/lib/schema";
import { SITE, OG_IMAGE } from "../src/lib/site";

const ROOT = join(import.meta.dirname, "..");
const SRC = join(ROOT, "src");

// ── walk src/app for real routes ────────────────────────────────────────
// A route exists iff it has a page.tsx: app/page.tsx -> "/", app/corpus -> "/corpus/".
function collectRoutes(dir: string, prefix: string): string[] {
  const routes: string[] = [];
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      routes.push(...collectRoutes(full, `${prefix}${entry}/`));
    } else if (entry === "page.tsx") {
      routes.push(`/${prefix}`);
    }
  }
  return routes;
}
const KNOWN_ROUTES = collectRoutes(join(SRC, "app"), "");

// ── collect literal DOM ids from component source ───────────────────────
function collectIds(dir: string, acc: Set<string>): void {
  for (const entry of readdirSync(dir)) {
    const full = join(dir, entry);
    if (statSync(full).isDirectory()) {
      collectIds(full, acc);
    } else if (/\.(tsx|ts)$/.test(entry)) {
      const src = readFileSync(full, "utf8");
      for (const m of src.matchAll(/id=\{?"([^"]+)"/g)) acc.add(m[1]);
    }
  }
}
const KNOWN_IDS = new Set<string>();
collectIds(SRC, KNOWN_IDS);

const SCHEMAS: Record<string, unknown> = {
  siteSchema,
  homeSchema,
  corpusSchema,
  citationsSchema,
  architectureSchema,
  faqSchema,
};

/** Recursively yield every [key, value] pair in a JSON-LD tree. */
function* pairs(value: unknown, key = ""): Generator<[string, string]> {
  if (typeof value === "string") yield [key, value];
  else if (Array.isArray(value)) for (const v of value) yield* pairs(v, key);
  else if (value && typeof value === "object")
    for (const [k, v] of Object.entries(value)) yield* pairs(v, k);
}

test("known routes exist exactly once each", () => {
  assert.deepEqual(
    [...KNOWN_ROUTES].sort(),
    ["/", "/architecture/", "/citations/", "/corpus/"],
  );
});

test("SITE is the canonical production domain", () => {
  assert.equal(SITE, "https://nyaya.parag.tech");
  assert.match(SITE, /^https:\/\//);
  assert.ok(!SITE.endsWith("/"), "SITE must not carry a trailing slash");
});

test("no schema references the retired example.com placeholder domain", () => {
  // Regex form of the substring check: same semantics as includes(), but
  // outside CodeQL's URL-substring-sanitization query, which misreads this
  // assert-absence test as a permissive URL validator (a false-positive
  // high alert failed PR CI once already).
  const retiredDomain = /example\.com/i;
  for (const [name, schema] of Object.entries(SCHEMAS)) {
    for (const [, s] of pairs(schema)) {
      assert.ok(!retiredDomain.test(s), `${name} references example.com: ${s}`);
    }
  }
});

test("every site-absolute URL in the schemas targets an existing route or public asset", () => {
  const publicAssets = readdirSync(join(ROOT, "public"));
  for (const [name, schema] of Object.entries(SCHEMAS)) {
    for (const [key, s] of pairs(schema)) {
      // `@id` values are JSON-LD node identifiers (cross-references between
      // nodes inside the same graph), not URLs users or crawlers follow.
      if (!s.startsWith(SITE) || key === "@id") continue;
      const rest = s.slice(SITE.length);
      const path = rest.split("#")[0] || "/"; // bare SITE = home route
      // Static assets (logo, screenshot) must exist in public/.
      if (/\.[a-z0-9]+$/i.test(path)) {
        assert.ok(
          publicAssets.includes(path.slice(1)),
          `${name} references missing public asset ${path}`,
        );
        continue;
      }
      assert.ok(
        KNOWN_ROUTES.includes(path),
        `${name} (key: ${key}) points at unknown route ${path} (url: ${s}); known: ${KNOWN_ROUTES}`,
      );
      // Fragment anchors must resolve to a DOM id that actually renders
      // on that route (guards the dead `/#corpus/#bns`-style anchors).
      const frag = rest.split("#")[1];
      if (frag !== undefined) {
        assert.ok(
          KNOWN_IDS.has(frag),
          `${name} has anchor #${frag} but no element renders id="${frag}"`,
        );
      }
    }
  }
});

test("home schema advertises the real OG image path, not a phantom asset", () => {
  const screenshot = (homeSchema as { screenshot: string }).screenshot;
  assert.equal(screenshot, `${SITE}${OG_IMAGE}`);
  assert.equal(OG_IMAGE, "/og-default.png");
});

// ── the OG asset itself must be a real 1200x630 PNG ─────────────────────
test("public/og-default.png exists and is a 1200x630 PNG", () => {
  const file = join(ROOT, "public", "og-default.png");
  assert.ok(existsSync(file), "public/og-default.png is missing");
  const buf = readFileSync(file);
  assert.ok(buf.length > 10_000, `suspiciously small (${buf.length} bytes)`);
  // PNG signature + IHDR width/height (big-endian at offsets 16/20).
  assert.deepEqual([...buf.subarray(0, 8)], [0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
  assert.equal(buf.readUInt32BE(16), 1200);
  assert.equal(buf.readUInt32BE(20), 630);
});

test("the phantom /og-default.svg and bare /logo.svg OG refs are gone from layout", () => {
  const layout = readFileSync(join(SRC, "app", "layout.tsx"), "utf8");
  assert.ok(layout.includes("@/lib/site"), "layout must source identity from lib/site");
  assert.ok(layout.includes("OG_IMAGE"), "layout must reference the OG_IMAGE constant");
  assert.ok(!layout.includes("/og-default"), "layout must not reference og-default directly");
  assert.ok(!layout.includes('"/logo.svg"'), "OG images must not be the 30px logo");
});