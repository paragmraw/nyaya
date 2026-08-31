#!/usr/bin/env node
// Build-time script to generate corpus-stats.json.
//
// Run via:  npm run generate:corpus-stats  (also runs as part of `npm run build`)
//
// Single source of truth: CURATED in src/lib/data.ts. This script derives the
// acts table AND the headline counts from it (parsing "All N articles/sections"
// style coverage strings), writes the static JSON that
//  - useCorpusStats() (src/lib/swr.ts) uses as the SWR fallbackData, and
//  - the /corpus page (src/app/corpus/page.tsx) renders at build time,
// so no stat number is maintained in more than one place.

import { writeFileSync, mkdirSync } from "fs";
import { join } from "path";
import { CURATED } from "../src/lib/data";
import type { CorpusCounts, CorpusStats } from "../src/lib/api";

const SRC_DATA_DIR = join(process.cwd(), "src", "data");
const PUBLIC_DIR = join(process.cwd(), "public");

// Pull "464" out of "All 464 articles + 12 schedules", the first number of
// "5 curated judgments (Kesavananda, …)", etc.
const RE_ARTICLES = /All\s+(\d+)\s+articles/i;
const RE_SCHEDULES = /All\s+(\d+)\s+articles\s+\+\s+(\d+)\s+schedules/i;
const RE_SECTIONS = /All\s+(\d+)\s+sections/i;
const RE_JUDGMENTS = /(\d+)\s+curated\s+judgments/i;

function countFrom(coverage: string, re: RegExp): number | undefined {
  const m = re.exec(coverage);
  // Use the LAST capture group so multi-group patterns (e.g. the schedules
  // regex) yield the number they were written for.
  return m ? parseInt(m[m.length - 1], 10) : undefined;
}

const acts = CURATED.map((c) => {
  const row: {
    short_name: string;
    name: string;
    type: string;
    coverage: string;
    status: string;
    fallback_date?: string;
    count?: number;
  } = {
    short_name: c.short_name,
    name: c.name,
    type: c.type,
    coverage: c.coverage,
    status: c.status,
  };
  if (c.fallback_date) row.fallback_date = c.fallback_date;
  // Parseable per-act counts (articles for the Constitution, sections for
  // statutes, judgments for the landmark set) power the /corpus StatCards.
  const count =
    countFrom(c.coverage, RE_ARTICLES) ??
    countFrom(c.coverage, RE_SECTIONS) ??
    countFrom(c.coverage, RE_JUDGMENTS);
  if (count !== undefined) row.count = count;
  return row;
});

function sumOf(re: RegExp): number {
  return CURATED.reduce((total, c) => total + (countFrom(c.coverage, re) ?? 0), 0);
}

const counts: CorpusCounts = {
  acts: CURATED.filter((c) => c.status === "live" && c.short_name).length,
  articles: countFrom(CURATED[0]?.coverage ?? "", RE_ARTICLES) ?? 0,
  // Only sections/articles whose counts are printed in the CURATED coverage
  // strings are aggregated; laws whose coverage omits a number are not counted
  // rather than guessed.
  sections: sumOf(RE_SECTIONS),
  judgments: countFrom(CURATED.find((c) => c.type === "Case law" && c.status === "live")?.coverage ?? "", RE_JUDGMENTS) ?? 0,
  schedules: countFrom(CURATED[0]?.coverage ?? "", RE_SCHEDULES) ?? 0,
  amendments: 0,
  chapters: 0,
  cross_refs: 0,
};

const stats: CorpusStats & { acts: typeof acts } = {
  counts,
  as_of: new Date().toISOString().slice(0, 10),
  acts,
};

function main() {
  try {
    mkdirSync(PUBLIC_DIR, { recursive: true });
    mkdirSync(SRC_DATA_DIR, { recursive: true });
    const json = JSON.stringify(stats, null, 2);
    const srcDataFile = join(SRC_DATA_DIR, "corpus-stats.json");
    writeFileSync(srcDataFile, `${json}\n`);
    writeFileSync(join(PUBLIC_DIR, "corpus-stats.json"), json);
    console.log(`Generated ${srcDataFile}`);
    console.log(
      `  acts=${counts.acts} articles=${counts.articles} sections=${counts.sections} judgments=${counts.judgments} schedules=${counts.schedules}`,
    );
  } catch (error) {
    console.error("Failed to generate corpus-stats.json:", error);
    process.exit(1);
  }
}

main();