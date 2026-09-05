import type { Metadata } from "next";
import CorpusTable from "@/components/CorpusTable";
import StatCard from "@/components/StatCard";
import Breadcrumb from "@/components/Breadcrumb";
import StructuredData, { corpusSchema } from "@/components/StructuredData";
import corpusStats from "@/data/corpus-stats.json";
import type { CorpusStats } from "@/lib/api";
import { API_VERSION, pageOpenGraph } from "@/lib/site";

export const metadata: Metadata = {
  title: "Corpus",
  description:
    "Explore Nyaya's indexed legal corpus: Constitution of India, BNS/BNSS/BSA 2023, IPC, CrPC, commercial statutes, and Supreme Court landmark judgments.",
  openGraph: pageOpenGraph({
    title: "Nyaya Corpus - Indexed Indian Legal Sources",
    description:
      "Explore Nyaya's indexed legal corpus: Constitution of India, BNS/BNSS/BSA 2023, IPC, CrPC, commercial statutes, and Supreme Court landmark judgments.",
  }),
};

export default function CorpusPage() {
  // Single-sourced: the static snapshot that the generator
  // (scripts/generate-corpus-stats.ts) derives from CURATED — the same file
  // useCorpusStats() uses as its SWR fallbackData. Per-act `count` values are
  // parsed from the CURATED coverage strings, so no number is duplicated here.
  const snapshot = corpusStats as {
    counts: CorpusStats["counts"];
    as_of: string | null;
    acts: { short_name: string; name: string; type: string; coverage: string; status: string; count?: number }[];
  };
  const counts = snapshot.counts;
  const acts = snapshot.acts;
  const byShort = (sn: string) => acts.find((a) => a.short_name === sn);

  return (
    <>
      <StructuredData data={corpusSchema} />
      <main id="content" className="page">
        <Breadcrumb />
        <div className="container">
          {/* hero */}
          <section>
            <p className="eyebrow">Corpus · v{API_VERSION}</p>
            <h1>What Nyaya has indexed</h1>
            <p className="lead">
              Constitution, statutes, and reported case law, ingested from openly-licensed sources. Every number below is traceable to the source it was drawn from.
            </p>
          </section>

          {/* coverage stats */}
          <section className="section">
            <div className="stat-grid">
              <StatCard
                num={byShort("Constitution")?.count}
                label="Constitution of India, 1950"
                source="Articles · Vikhram-S/IndianConstitution (Apache-2.0)"
              />
              <StatCard
                num={byShort("BNS")?.count}
                label="Bharatiya Nyaya Sanhita, 2023"
                source="Sections · PRS Legislative Research (CC BY 4.0)"
              />
              <StatCard
                num={byShort("BNSS")?.count}
                label="Bharatiya Nagarik Suraksha Sanhita, 2023"
                source="Sections (replaces CrPC) · PRS Legislative Research (CC BY 4.0)"
              />
              <StatCard
                num={counts.judgments}
                label="Supreme Court landmark judgments"
                source="Curated from indiankanoon.org (public domain)"
              />
            </div>
          </section>

          {/* corpus table */}
          <section className="section">
            <div className="section-head">
              <h2>Indexed sources</h2>
              <p className="sec-desc">Click a column header to sort. Filter by status to narrow the view.</p>
            </div>
            <CorpusTable acts={acts} />
          </section>

          {/* refresh cadence */}
          <section className="section">
            <div className="section-head">
              <h2>Refresh cadence</h2>
              <p className="sec-desc">How the corpus is kept current. Ingestion is currently manual via the hydration notebook (<code>mcp/notebooks/hydrate.ipynb</code>).</p>
            </div>
            <div className="cadence">
              <div className="cadence-row">
                <span className="cad-when">Manual</span>
                <span className="cad-what">Constitution + BNS/BNSS/BSA + IPC/CrPC/IEA/CPC + commercial statutes, re-pulled from openly-licensed sources (Vikhram-S/IndianConstitution, PRS, civictech-India, mratanusarkar/Indian-Laws on HuggingFace) and diff-merged into the index on demand.</span>
              </div>
              <div className="cadence-row">
                <span className="cad-when">Manual</span>
                <span className="cad-what">Landmark Supreme Court judgments, curated from indiankanoon.org and embedded on ingestion. Not yet automated.</span>
              </div>
              <div className="cadence-row">
                <span className="cad-when">Planned</span>
                <span className="cad-what">High Court reported judgments from selected High Courts. Roadmap: automated nightly fetch once the pipeline is in place.</span>
              </div>
            </div>
          </section>

          {/* roadmap */}
          <section className="section">
            <div className="section-head">
              <h2>Coming next</h2>
              <p className="sec-desc">What is being indexed and when it is expected to go live.</p>
            </div>
            <div className="roadmap">
              <div className="roadmap-item">
                <div className="ri-name">All High Court reported judgments</div>
                <div className="ri-desc">Expanding beyond the current landmark-SC set to cover all 25 High Courts with neutral citations.</div>
                <div className="ri-eta">Planned</div>
              </div>
              <div className="roadmap-item">
                <div className="ri-name">Subordinate legislation & rules</div>
                <div className="ri-desc">Rules, notifications, and subordinate legislation under major statutes; the long tail of regulatory text.</div>
                <div className="ri-eta">Planned</div>
              </div>
              <div className="roadmap-item">
                <div className="ri-name">Pre-1950 Privy Council decisions</div>
                <div className="ri-desc">Judgments of the Privy Council (the apex court for British India until 1950); the historical layer of Indian case law.</div>
                <div className="ri-eta">Planned</div>
              </div>
            </div>
          </section>
        </div>
      </main>
    </>
  );
}