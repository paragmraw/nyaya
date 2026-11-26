#!/usr/bin/env node
// Build-time script to generate corpus-stats.json (Nyaya v2.1 CorpusStatusResponse).
//
// Run via:  npm run generate:corpus-stats  (also runs as part of `npm run build`)
// Optional: tsx scripts/generate-corpus-stats.ts --from-manifest path/to/corpus-status.json
//
// Single source of truth: BASELINE_COUNTS & CURATED in src/lib/data.ts +
// API_VERSION in src/lib/site.ts. Emits the static JSON snapshot that
// useCorpusStatus() (src/lib/swr.ts) uses as fallbackData and that / and
// /corpus/ render at build time.

import { existsSync, mkdirSync, readFileSync, writeFileSync } from "fs";
import { join } from "path";
import { BASELINE_COUNTS, CURATED } from "../src/lib/data";
import type { CorpusCounts, CorpusStatusResponse } from "../src/lib/api";
import { API_VERSION } from "../src/lib/site";

const SRC_DATA_DIR = join(process.cwd(), "src", "data");
const PUBLIC_DIR = join(process.cwd(), "public");

function parseManifestArg(argv: string[]): Partial<CorpusStatusResponse> | null {
  const idx = argv.indexOf("--from-manifest");
  if (idx === -1 || !argv[idx + 1]) return null;
  const target = argv[idx + 1];
  if (!existsSync(target)) {
    console.warn(`--from-manifest path not found (${target}); using curated defaults.`);
    return null;
  }
  try {
    return JSON.parse(readFileSync(target, "utf8")) as Partial<CorpusStatusResponse>;
  } catch (err) {
    console.warn(`Failed to parse ${target}; using curated defaults:`, err);
    return null;
  }
}

const override = parseManifestArg(process.argv.slice(2));

const counts: CorpusCounts = {
  ...BASELINE_COUNTS,
  ...(override?.counts ?? {}),
};

const acts = CURATED.map((c) => ({
  short_name: c.short_name,
  aliases: c.aliases,
  name: c.name,
  type: c.type,
  category: c.category,
  tier: c.tier,
  coverage: c.coverage,
  status: c.status,
  license_spdx: c.license_spdx,
  commercial_safe: c.commercial_safe,
  source: c.source,
  ...(c.fallback_date ? { fallback_date: c.fallback_date } : {}),
}));

const sources = CURATED.map((c) => ({
  source_id: (c.short_name || c.name).toLowerCase().replace(/[^a-z0-9]+/g, "_"),
  short_name: c.short_name,
  name: c.name,
  category: c.category,
  coverage: c.coverage,
  license_spdx: c.license_spdx,
  commercial_safe: c.commercial_safe,
  as_of: c.fallback_date || null,
}));

const statusPayload: CorpusStatusResponse & { acts: typeof acts } = {
  version: API_VERSION,
  service_version: API_VERSION,
  retrieval_mode: "lexical_metadata_graph",
  commercial_safe_locked: override?.commercial_safe_locked ?? false,
  as_of: override?.as_of ?? new Date().toISOString().slice(0, 10),
  tiers: override?.tiers ?? {
    sqlite_core: "baked_readonly_fts5",
    lance_fts: "railway_bucket_rust_bm25",
    parquet_cold: "duckdb_zstd_parquet",
    s3_pdf: "aws_opendata_pypdfium2",
  },
  counts,
  licensing: override?.licensing ?? {
    code_license: "Apache-2.0",
    commercial_safe_default: false,
    nc_datasets: ["ddl_district_courts", "ildc_research"],
  },
  sources: override?.sources ?? sources,
  acts,
};

function main() {
  try {
    mkdirSync(PUBLIC_DIR, { recursive: true });
    mkdirSync(SRC_DATA_DIR, { recursive: true });
    const json = JSON.stringify(statusPayload, null, 2);
    const srcDataFile = join(SRC_DATA_DIR, "corpus-stats.json");
    writeFileSync(srcDataFile, `${json}\n`);
    writeFileSync(join(PUBLIC_DIR, "corpus-stats.json"), `${json}\n`);
    console.log(`Generated ${srcDataFile} (v${API_VERSION})`);
    console.log(
      `  jurisdictions=${counts.legislation_jurisdictions} central=${counts.central_acts} regulators=${counts.regulators} tribunals=${counts.tribunals} sc=${counts.sc_judgments} hc=${counts.hc_judgments} ddl=${counts.district_court_records}`,
    );
  } catch (error) {
    console.error("Failed to generate corpus-stats.json:", error);
    process.exit(1);
  }
}

main();
