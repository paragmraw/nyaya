#!/usr/bin/env node
// Build-time script to generate corpus-stats.json
// Run via: npx tsx scripts/generate-corpus-stats.ts

import { writeFileSync, mkdirSync } from "fs";
import { join } from "path";

interface CorpusStats {
  articles: number;
  sections: number;
  judgments: number;
  lastUpdated: string;
  acts: ActInfo[];
}

interface ActInfo {
  short_name: string;
  name: string;
  type: string;
  coverage: string;
  status: "live" | "coming";
  fallback_date?: string;
}

// Static data from the CURATED array in data.ts and fallback values
const CORPUS_STATS: CorpusStats = {
  articles: 464,
  sections: 3257,
  judgments: 5,
  lastUpdated: new Date().toISOString(),
  acts: [
    { short_name: "Constitution", name: "Constitution of India", type: "Constitution", coverage: "All 464 articles + 12 schedules", status: "live" },
    { short_name: "BNS", name: "Bharatiya Nyaya Sanhita, 2023", type: "Statute", coverage: "All 358 sections", status: "live" },
    { short_name: "BNSS", name: "Bharatiya Nagarik Suraksha Sanhita, 2023", type: "Statute", coverage: "All 531 sections", status: "live" },
    { short_name: "BSA", name: "Bharatiya Sakshya Adhiniyam, 2023", type: "Statute", coverage: "All sections (replaces Evidence Act)", status: "live" },
    { short_name: "IPC", name: "Indian Penal Code, 1860", type: "Statute", coverage: "All 511 sections (legacy reference)", status: "live" },
    { short_name: "CrPC", name: "Code of Criminal Procedure, 1973", type: "Statute", coverage: "All 484 sections (legacy reference)", status: "live" },
    { short_name: "EvidenceAct", name: "Indian Evidence Act, 1872", type: "Statute", coverage: "All sections (legacy reference)", status: "live" },
    { short_name: "CPC", name: "Code of Civil Procedure, 1908", type: "Statute", coverage: "All sections (legacy reference)", status: "live" },
    { short_name: "Companies", name: "Companies Act, 2013", type: "Statute", coverage: "Commercial statute", status: "live" },
    { short_name: "IGST", name: "Integrated Goods and Services Tax Act, 2017", type: "Statute", coverage: "Commercial statute", status: "live" },
    { short_name: "CGST", name: "Central Goods and Services Tax Act, 2017", type: "Statute", coverage: "Commercial statute", status: "live" },
    { short_name: "ITAct", name: "Information Technology Act, 2000", type: "Statute", coverage: "Commercial statute", status: "live" },
    { short_name: "Arbitration", name: "Arbitration and Conciliation Act, 1996", type: "Statute", coverage: "Commercial statute", status: "live" },
    { short_name: "ConsumerProtection", name: "Consumer Protection Act, 2019", type: "Statute", coverage: "Commercial statute", status: "live" },
    { short_name: "", name: "Supreme Court landmark judgments", type: "Case law", coverage: "5 curated judgments (Kesavananda, Maneka, Puttaswamy, Shah Bano, Navtej Johar)", status: "live", fallback_date: "2026-07-01" },
    { short_name: "", name: "High Court reported judgments", type: "Case law", coverage: "Planned", status: "coming" },
    { short_name: "", name: "Subordinate legislation & rules", type: "Regulation", coverage: "Planned", status: "coming" },
    { short_name: "", name: "Pre-1950 Privy Council decisions", type: "Case law", coverage: "Planned", status: "coming" },
  ],
};

function main() {
  const publicDir = join(process.cwd(), "public");
  const srcDataDir = join(process.cwd(), "src", "data");
  const publicFile = join(publicDir, "corpus-stats.json");
  const srcDataFile = join(srcDataDir, "corpus-stats.json");

  try {
    mkdirSync(publicDir, { recursive: true });
    mkdirSync(srcDataDir, { recursive: true });
    const json = JSON.stringify(CORPUS_STATS, null, 2);
    writeFileSync(publicFile, json);
    writeFileSync(srcDataFile, json);
    console.log(`✅ Generated ${publicFile}`);
    console.log(`✅ Generated ${srcDataFile}`);
    console.log(`   Articles: ${CORPUS_STATS.articles}`);
    console.log(`   Sections: ${CORPUS_STATS.sections}`);
    console.log(`   Judgments: ${CORPUS_STATS.judgments}`);
    console.log(`   Acts: ${CORPUS_STATS.acts.length}`);
  } catch (error) {
    console.error("❌ Failed to generate corpus-stats.json:", error);
    process.exit(1);
  }
}

main();