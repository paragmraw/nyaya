import type { Metadata } from "next";
import Breadcrumb from "@/components/Breadcrumb";
import CorpusDeepLink from "@/components/CorpusDeepLink";
import CorpusStatsGrid from "@/components/CorpusStatsGrid";
import StructuredData, { corpusSchema } from "@/components/StructuredData";
import corpusStats from "@/data/corpus-stats.json";
import { API_VERSION, pageOpenGraph } from "@/lib/site";

export const metadata: Metadata = {
  title: "Corpus",
  description:
    "Explore Nyaya's v2.1 Open-India-Law corpus: Constitution & Amendments 1–106, CAD debates, 37 Central & State/UT legislatures, 58 CPC Orders, 12 regulators, 24 tribunals & Consumer Commissions, 38k+ SC judgments, and 20.7M+ HC orders.",
  openGraph: pageOpenGraph({
    title: "Nyaya Corpus - Open-India-Law Pack & Licensing",
    description:
      "19 trusted sources across 37 legislatures, 12 regulators, 24 tribunals & Consumer Commissions, 38k+ SC, and 20.7M+ HC records with explicit SPDX provenance.",
  }),
};

export default function CorpusPage() {
  const snapshot = corpusStats as {
    acts: { short_name: string }[];
  };
  const acts = snapshot.acts ?? [];

  return (
    <>
      <StructuredData data={corpusSchema} />
      <main id="content" className="page">
        <div className="container">
          <Breadcrumb />
          {/* hero */}
          <section>
            <p className="eyebrow">Corpus · v{API_VERSION}</p>
            <h1>Open-India-Law Pack</h1>
            <p className="lead lead-wide">
              Three judicial tiers, 37 legislative jurisdictions, 58 CPC Orders, 12 financial and sectoral regulators, and 24 tribunals &amp; Consumer Commissions across 19 sources in <code>sources.lock.yaml</code>; stored in baked SQLite FTS5, 5 LanceDB BM25 tables (incl. <code>tribunal_chunks</code>, <code>cad_debates</code>, and <code>hot_hc_limit=500,000</code>), DuckDB ZSTD Parquet, and on-demand AWS Open Data PDFs.
            </p>
          </section>

          {/* coverage stats (live-hydrated from /api/corpus-status with JSON fallback) */}
          <section className="section">
            <CorpusStatsGrid />
          </section>

          {/* academic / non-commercial attribution & license posture */}
          <section className="section">
            <div className="nc-notice" role="region" aria-label="Academic and Non-Commercial Attribution Notice">
              <div className="nc-head">
                <span className="pill">Licensing &amp; Attribution</span>
                <h2>Academic / Non-Commercial Attribution Notice</h2>
              </div>
              <p className="nc-body">
                Nyaya&apos;s server code (<code>nyaya-mcp</code>) is licensed under <strong>Apache-2.0</strong> (using Apache-2.0 <code>pypdfium2</code>; zero GPL/AGPL dependencies). Core statutory, regulatory, tribunal, Supreme Court, and High Court packs are cleared under <strong>CC-BY-4.0</strong>, <strong>Apache-2.0</strong>, and <strong>Indian Copyright Act 1957 s. 52(1)(q)</strong>.
              </p>
              <p className="nc-body">
                Optional academic research packs (declared in <code>sources.lock.yaml</code>) carry <code>commercial_safe=false</code>:{" "}
                <strong>Development Data Lab (DDL) District Court Metadata (2010–2018)</strong> (Ash, Asher, Bhowmick, Chen, Novosad, Tan · <strong><code>CC-BY-NC-SA-4.0</code></strong>, ML religion/caste prediction columns dropped) and{" "}
                <strong>ILDC Supreme Court Research Annotations</strong> (<strong><code>CC-BY-NC-4.0</code></strong>). For commercial deployments, set <code>NYAYA_COMMERCIAL_SAFE_ONLY=1</code> or pass <code>commercial_safe_only=true</code> to exclude all non-commercial sources.
              </p>
            </div>
          </section>

          {/* corpus table */}
          <section className="section">
            <div className="section-head">
              <h2>Indexed sources</h2>
              <p className="sec-desc">
                Filter by category or commercial-safe license. Click any column header to sort; live updates hydrate from <code>/api/corpus-status</code> and <code>/api/acts</code>.
              </p>
            </div>
            <CorpusDeepLink acts={acts} />
          </section>

          {/* refresh cadence */}
          <section className="section">
            <div className="section-head">
              <h2>Ingestion &amp; refresh cadence</h2>
              <p className="sec-desc">
                Reproducible via the <code>nyaya-ingest</code> CLI and gated by 8 automated legal integrity checks before atomic blue/green publishing.
              </p>
            </div>
            <div className="cadence">
              <div className="cadence-row">
                <span className="cad-when">Daily (--tier daily)</span>
                <span className="cad-what">
                  Scheduled GitHub Actions workflow polls AWS Open Data SC &amp; HC S3 ETags (<code>s3://indian-supreme-court-judgments</code>, <code>s3://indian-high-court-judgments</code>), inspects <code>data.index.json</code> <code>superseded_files</code>, pulls PRS CC-BY-4.0 statutory deltas, NCDRC/NCLAT orders, e-Gazette rules, and SCI cause lists, runs <code>nyaya-ingest verify-gate</code>, and atomically swaps <code>current/manifest.json</code> on the Railway Storage Bucket and GitHub Releases.
                </span>
              </div>
              <div className="cadence-row">
                <span className="cad-when">Static (--tier static)</span>
                <span className="cad-what">
                  19 sources in <code>sources.lock.yaml</code>: Vaquill Open India Law <code>v2026.08.1</code> (Central, 36 States/UTs, 12 Regulators, appellate tribunals) plus Consumer Commissions (<code>NCDRC</code>/<code>SCDRC</code>/<code>DCDRC</code>), <code>DRAT</code>, <code>AFT</code>, <code>GSTAT</code>, <code>RCT</code>, <code>CIC</code>, <code>SEBI_ADJ</code> (24 total tribunals), 58 CPC Orders, 148 criminal &amp; 33 Income-tax 1961↔2025 concordances, Constitution + Amendments 1–106, Constituent Assembly Debates (<code>cad_debates</code>), DDL 81M (2010–2018), and ILDC verified against pinned SHA-256 manifests.
                </span>
              </div>
              <div className="cadence-row">
                <span className="cad-when">On-demand PDFs</span>
                <span className="cad-what">
                  Full judgment text for uncached SC/HC decisions streams directly over anonymous HTTPS from AWS Open Data (<code>ap-south-1</code>) via <code>pypdfium2</code> with <code>%PDF-</code> validation, 15 MB / 500-page caps, a 256 MB disk zstd LRU cache, and opt-in Apache-2.0 OCR (<code>NYAYA_ENABLE_OCR=1</code>).
                </span>
              </div>
            </div>
          </section>

          {/* roadmap */}
          <section className="section">
            <div className="section-head">
              <h2>Capabilities &amp; Roadmap</h2>
              <p className="sec-desc">Opt-in retrieval upgrades live in v2.1 alongside post-v2.1 historical and curation additions.</p>
            </div>
            <div className="roadmap">
              <div className="roadmap-item">
                <div className="ri-name">Opt-in hybrid BM25 + vector RRF</div>
                <div className="ri-desc">Pluggable <code>RetrievalEngine</code> supporting hashed legal n-gram or OpenAI-compatible dense embeddings fused via RRF when <code>NYAYA_HYBRID_SEARCH=1</code>.</div>
                <div className="ri-eta">Opt-in (v2.1)</div>
              </div>
              <div className="roadmap-item">
                <div className="ri-name">Opt-in Apache-2.0 scanned PDF OCR</div>
                <div className="ri-desc">Tesseract &amp; RapidOCR adapters for legacy High Court image-only PDFs when <code>NYAYA_ENABLE_OCR=1</code> (flagged as <code>scanned_image_only</code> by default).</div>
                <div className="ri-eta">Opt-in (v2.1)</div>
              </div>
              <div className="roadmap-item">
                <div className="ri-name">Pre-1950 Privy Council &amp; Federal Court</div>
                <div className="ri-desc">Historical apex judgments predating 1950 under Indian Copyright Act s. 52(1)(q) provenance.</div>
                <div className="ri-eta">Planned</div>
              </div>
            </div>
          </section>
        </div>
      </main>
    </>
  );
}
