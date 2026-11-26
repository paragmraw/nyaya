// Unit tests for Nyaya v2.1 CorpusStatusResponse, CURATED metadata,
// category/commercial-safe filtering, and API client endpoints.
// Run with:  npm test

import { test } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { join } from "node:path";

import { api, formatCompactCount, type CorpusSourceStatus } from "../src/lib/api";
import { indexSourcesByName, resolveSourceMetadata } from "../src/components/CorpusTable";
import { BASELINE_COUNTS, CURATED, MCP_TOOLS_FALLBACK, MCP_RESOURCES, MCP_PROMPTS } from "../src/lib/data";
import { API_VERSION } from "../src/lib/site";
import { filterCorpusRows } from "../src/lib/url-query";

const ROOT = join(import.meta.dirname, "..");

const CANONICAL_COUNTS_KEYS = [
  "legislation_jurisdictions",
  "central_acts",
  "state_acts",
  "regulators",
  "tribunals",
  "provisions",
  "articles",
  "schedules",
  "amendments",
  "concordance_mappings",
  "judicial_invalidations",
  "sc_judgments",
  "hc_judgments",
  "tribunal_orders",
  "district_court_records",
  "citation_edges",
] as const;

test("corpus-stats.json snapshots match canonical CorpusStatusResponse counts keys", () => {
  for (const rel of ["src/data/corpus-stats.json", "public/corpus-stats.json"]) {
    const raw = JSON.parse(readFileSync(join(ROOT, rel), "utf8")) as {
      service_version?: string;
      counts: Record<string, unknown>;
      licensing?: { code_license?: string; nc_datasets?: string[] };
    };
    assert.equal(raw.service_version, API_VERSION, `${rel} must carry service_version=${API_VERSION}`);
    for (const k of CANONICAL_COUNTS_KEYS) {
      assert.equal(typeof raw.counts[k], "number", `${rel} counts.${k} must be a number`);
    }
    assert.equal(raw.licensing?.code_license, "Apache-2.0");
    assert.ok(raw.licensing?.nc_datasets?.includes("ddl_district_courts"));
    assert.ok(raw.licensing?.nc_datasets?.includes("ildc_research"));
  }
});

test("CURATED rows carry valid categories, SPDX licenses, and commercial_safe flags", () => {
  const validCategories = new Set(["judiciary", "legislation", "regulators", "tribunals"]);
  for (const row of CURATED) {
    assert.ok(validCategories.has(row.category), `invalid category on ${row.name}: ${row.category}`);
    assert.ok(row.license_spdx.length > 0, `missing license_spdx on ${row.name}`);
    assert.equal(typeof row.commercial_safe, "boolean", `missing commercial_safe on ${row.name}`);
  }
  const ddl = CURATED.find((r) => r.short_name === "DDL");
  const ildc = CURATED.find((r) => r.short_name === "ILDC");
  assert.ok(ddl && ddl.commercial_safe === false && ddl.license_spdx === "CC-BY-NC-SA-4.0");
  assert.ok(ildc && ildc.commercial_safe === false && ildc.license_spdx === "CC-BY-NC-4.0");
});

test("filterCorpusRows filters by category and commercial_safe", () => {
  assert.equal(filterCorpusRows(CURATED, "all").length, CURATED.length);
  const safeRows = filterCorpusRows(CURATED, "commercial_safe");
  assert.ok(safeRows.every((r) => r.commercial_safe));
  assert.ok(!safeRows.some((r) => r.short_name === "DDL" || r.short_name === "ILDC"));
  assert.ok(filterCorpusRows(CURATED, "judiciary").every((r) => r.category === "judiciary"));
  assert.ok(filterCorpusRows(CURATED, "legislation").every((r) => r.category === "legislation"));
  assert.ok(filterCorpusRows(CURATED, "regulators").every((r) => r.category === "regulators"));
  assert.ok(filterCorpusRows(CURATED, "tribunals").every((r) => r.category === "tribunals"));
});

test("API client exposes all v2 REST fetchers", () => {
  assert.equal(typeof api.corpusStatus, "function");
  assert.equal(typeof api.acts, "function");
  assert.equal(typeof api.judgments, "function");
  assert.equal(typeof api.analytics, "function");
  assert.equal(typeof api.tools, "function");
  assert.equal(typeof api.healthSummary, "function");
});

test("v2.1 MCP tools, resources, prompts, and baseline counts match backend", () => {
  assert.equal(MCP_TOOLS_FALLBACK.total, 15);
  assert.equal(MCP_TOOLS_FALLBACK.items.length, 15);
  assert.equal(MCP_RESOURCES.length, 8);
  assert.equal(MCP_PROMPTS.length, 4);
  assert.equal(BASELINE_COUNTS.tribunals, 24);
  assert.equal(BASELINE_COUNTS.concordance_mappings, 181);
});

test("formatCompactCount formats large Indian corpus counts legibly", () => {
  assert.equal(formatCompactCount(81_000_000), "81M+");
  assert.equal(formatCompactCount(17_770_000), "17.77M+");
  assert.equal(formatCompactCount(35_000), "35k+");
});

test("backend-shaped SourceStatus items without top-level name normalize safely", () => {
  const scRow = CURATED.find((r) => r.short_name === "SC");
  const ddlRow = CURATED.find((r) => r.short_name === "DDL");
  assert.ok(scRow && ddlRow);

  const backendSources: CorpusSourceStatus[] = [
    {
      source_id: "sc",
      record_count: 35_000,
      year_coverage: "1950-2026",
      text_availability: "indexed_bm25",
      provenance: {
        source_id: "sc",
        source_name: scRow.name,
        source_url: "https://aws.amazon.com/opendata/",
        license_spdx: "CC-BY-4.0",
        commercial_safe: true,
        terms_status: "verified",
        as_of: "2026-11-23",
      },
    },
    {
      source_id: "ddl_district_courts",
      record_count: 81_000_000,
      text_availability: "metadata_only",
      provenance: {
        source_id: "ddl_district_courts",
        source_name: ddlRow.name,
        license_spdx: "CC-BY-NC-SA-4.0",
        commercial_safe: false,
        as_of: "2018-12-31",
      },
    },
    {} as CorpusSourceStatus,
  ];

  const indexed = indexSourcesByName(backendSources);
  const resolvedScSrc = indexed.get(scRow.short_name.toLowerCase());
  const resolvedDdlSrc = indexed.get(ddlRow.name.toLowerCase());
  assert.equal(resolvedScSrc?.source_id, "sc");
  assert.equal(resolvedDdlSrc?.source_id, "ddl_district_courts");

  const scMeta = resolveSourceMetadata(resolvedScSrc, scRow, undefined, "2026-11-24");
  assert.equal(scMeta.license_spdx, "CC-BY-4.0");
  assert.equal(scMeta.commercial_safe, true);
  assert.equal(scMeta.date, "2026-11-23");
  assert.equal(scMeta.coverage, scRow.coverage);

  const ddlMeta = resolveSourceMetadata(resolvedDdlSrc, ddlRow, { short_name: "DDL", as_of: "2019-01-01" });
  assert.equal(ddlMeta.license_spdx, "CC-BY-NC-SA-4.0");
  assert.equal(ddlMeta.commercial_safe, false);
  assert.equal(ddlMeta.date, "2019-01-01");

  assert.equal(indexSourcesByName(undefined).size, 0);
});
