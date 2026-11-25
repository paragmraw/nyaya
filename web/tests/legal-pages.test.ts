// Structural + truthfulness guards for the six legal/info info routes.
//
// Run with:  npx tsx --test tests/legal-pages.test.ts
//
// Guards introduced with the legal-pages task set:
//   1. legal-facts.ts constants must match the code-verified values (fact
//      dossier, workflow wf_b107376e-611) so prose can never silently drift;
//   2. every new route exists, and every footer site-link points at a real
//      route;
//   3. legal copy never overclaims data handling ("we do not log", "nothing
//      is stored", ...): platform/log retention is hedged, not claimed away;
//   4. each page carries its load-bearing disclosure (processor on privacy,
//      channels on contact, licensing posture on terms, the not-legal-advice
//      anchor on disclaimer, single-sourced FAQ rendering, accessibility
//      honesty).

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync, readdirSync, statSync } from "node:fs";
import { join } from "node:path";

const ROOT = join(import.meta.dirname, "..");
const APP = join(ROOT, "src", "app");

// A route exists iff it has a page.tsx (same collect pattern as
// structured-data.test.ts).
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

const NEW_ROUTES = [
  "/privacy/",
  "/terms/",
  "/disclaimer/",
  "/faq/",
  "/contact/",
  "/accessibility/",
] as const;

const PAGE_PATH: Record<string, string> = {
  "/privacy/": "privacy",
  "/terms/": "terms",
  "/disclaimer/": "disclaimer",
  "/faq/": "faq",
  "/contact/": "contact",
  "/accessibility/": "accessibility",
};

// Overclaim guards: the privacy page must hedge platform/provider retention,
// never claim it away. Case-insensitive substring checks on page source.
const FORBIDDEN_CLAIMS = [
  "we do not log",
  "nothing is stored",
  "we never store",
  "no data is collected",
  "we do not collect any",
];

const pageSrc = (route: string): string =>
  readFileSync(join(APP, PAGE_PATH[route], "page.tsx"), "utf8");

test("legal-facts constants match verified values", async () => {
  const facts = await import("../src/lib/legal-facts");
  assert.equal(facts.EFFECTIVE_DATE, "2026-11-25");
  assert.equal(facts.CONTACT_EMAIL, "mail@parag.tech");
  assert.equal(facts.REPO_URL, "https://github.com/paragmraw/nyaya");
  assert.deepEqual(facts.RATE_LIMITS, { chat: 15, rest: 120, mcp: 30, analytics: 20 });
  assert.equal(facts.BODY_CAP_BYTES, 1_048_576);
  assert.deepEqual(facts.STORAGE, {
    chatTranscript: "nyaya.chat.v1",
    chatDraft: "nyaya.chat.draft",
    theme: "nyaya-theme",
  });
  assert.equal(facts.CHAT_MODEL, "nvidia/nemotron-3.5-lightning-30b-a3b");
  assert.equal(facts.HISTORY_TURNS, 8);
  assert.equal(facts.MESSAGE_MAX_CHARS, 4000);
  assert.equal(facts.CORPUS_BASELINE, "corpus-v2026.11.25-p1");
});

test("all six info routes exist", () => {
  const routes = collectRoutes(APP, "");
  for (const route of NEW_ROUTES) {
    assert.ok(routes.includes(route), `missing route: ${route}`);
  }
});

test("footer exposes site links that resolve to real routes", async () => {
  const mod = await import("../src/components/Footer");
  const routes = collectRoutes(APP, "");
  const links = [...mod.FOOTER_LINKS, ...mod.SITE_LINKS];
  assert.ok(links.length > 0, "no exported footer links at all");
  for (const link of links) {
    if (link && typeof link === "object" && "external" in link) continue;
    assert.ok(routes.includes(link.href), `footer link ${link.href} is not a real route`);
  }
  const hrefs = new Set(links.map((l) => l.href));
  for (const route of NEW_ROUTES) {
    assert.ok(hrefs.has(route), `footer missing link to ${route}`);
  }
});

test("legal pages avoid overclaims about data handling", () => {
  for (const route of NEW_ROUTES) {
    const text = pageSrc(route).toLowerCase();
    for (const claim of FORBIDDEN_CLAIMS) {
      assert.ok(!text.includes(claim), `${route} contains forbidden claim: "${claim}"`);
    }
  }
});

test("contact page names the real channels", () => {
  const src = pageSrc("/contact/");
  // The page single-sources identifiers from legal-facts.ts; the guard
  // asserts the constants are actually used (their values are pinned by
  // the constants test above).
  assert.ok(
    /CONTACT_EMAIL/.test(src) && /REPO_URL/.test(src),
    "contact page must reach out via legal-facts CONTACT_EMAIL and REPO_URL",
  );
  // Security reporting must NOT invite public issue disclosure (SECURITY.md).
  const lower = src.toLowerCase();
  assert.ok(
    lower.includes("do not open a public github issue"),
    "contact page must tell reporters not to open public security issues",
  );
});

test("privacy page discloses the LLM processor and browser storage", () => {
  const src = pageSrc("/privacy/");
  assert.ok(/NVIDIA/.test(src), "privacy page must name the LLM processor");
  assert.ok(src.includes("sessionStorage"), "privacy page must describe browser storage");
  // The page sources the key from legal-facts.ts (single-sourced), so the
  // guard asserts the constant's usage rather than the hand-copied string.
  assert.ok(
    /STORAGE\.chatTranscript/.test(src),
    "privacy page must disclose the transcript key via legal-facts STORAGE",
  );
  assert.ok(
    /governed by\s+NVIDIA/i.test(src),
    "NVIDIA data handling must be framed as governed by NVIDIA's terms",
  );
});

test("disclaimer carries the not-legal-advice anchor", () => {
  const src = pageSrc("/disclaimer/");
  assert.ok(
    src.includes("not legal advice") || src.toLowerCase().includes("not legal advice"),
    "disclaimer page must state it is not legal advice",
  );
  assert.ok(
    /unverified_automated/.test(src),
    "disclaimer page must disclose the unverified_automated precedent status",
  );
});

test("terms page states the licensing posture", () => {
  const src = pageSrc("/terms/");
  assert.ok(src.includes("Apache-2.0"), "terms page must state the code license");
  assert.ok(src.includes("CC-BY-4.0"), "terms page must state the corpus license family");
  assert.ok(
    src.includes("s. 52(1)(q)") || src.includes("s.52(1)(q)") || src.toLowerCase().includes("s 52(1)(q)"),
    "terms page must state the s.52(1)(q) statutory basis",
  );
});

test("faq page renders from the faq schema, not re-typed copy", () => {
  const src = pageSrc("/faq/");
  assert.ok(/faqSchema/.test(src), "faq page must reference faqSchema");
  assert.ok(/mainEntity/.test(src), "faq page must map faqSchema.mainEntity");
});

test("accessibility page is honest about conformance posture", () => {
  const src = pageSrc("/accessibility/");
  // Statement cross-checks the features it names exist in source, so
  // deleting the feature (not just the prose) fails the guard.
  const layout = readFileSync(join(APP, "layout.tsx"), "utf8");
  assert.ok(
    layout.includes('className="skip-link"') && layout.includes('href="#content"'),
    "skip-link feature referenced by the accessibility statement is missing from layout",
  );
  const topnav = readFileSync(join(APP, "..", "components", "Topnav.tsx"), "utf8");
  assert.ok(
    /Escape/.test(topnav) && /focus\(\)/.test(topnav) && /preventDefault/.test(topnav),
    "focus-trap/Escape feature referenced by the accessibility statement is missing from Topnav",
  );
  const architecture = readFileSync(join(APP, "architecture", "page.tsx"), "utf8");
  assert.ok(
    architecture.includes("PipelineStage"),
    "Enter/Space cards referenced by the accessibility statement must live on /architecture",
  );
  assert.ok(
    /prefers-color-scheme|prefers-color/i.test(src),
    "accessibility page must mention OS color-scheme honoring",
  );
  assert.ok(
    /not (been|commissioned|performed)|no formal/i.test(src),
    "accessibility page must state no formal conformance audit exists",
  );
});
