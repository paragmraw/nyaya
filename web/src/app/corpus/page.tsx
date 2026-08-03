"use client";

import CorpusTable from "@/components/CorpusTable";
import StatCard from "@/components/StatCard";
import { useCorpusStats, useActs, formatNumber } from "@/lib";

export default function CorpusPage() {
  const { data } = useCorpusStats();
  const { data: acts } = useActs();
  const counts = data?.counts ?? {};

  return (
    <main className="page">
      <div className="container">
        {/* hero */}
        <section>
          <p className="eyebrow">Corpus · v0.9</p>
          <h1>What Nyaya has indexed</h1>
          <p className="lead">
            Constitution, statutes, and reported case law — refreshed weekly from official sources. Every number below is traceable to the source it was drawn from.
          </p>
        </section>

        {/* coverage stats */}
        <section className="section">
          <div className="stat-grid">
            <StatCard
              num={counts.articles ?? 470}
              label="Constitution of India, 1950"
              source="Articles + Schedules · legislative.gov.in"
            />
            <StatCard
              num={counts.sections ?? 358}
              label="Bharatiya Nyaya Sanhita, 2023"
              source="Sections · indiacode.nic.in"
            />
            <StatCard
              num={counts.sections ?? 531}
              label="Bharatiya Nagarik Suraksha Sanhita, 2023"
              source="Sections (replaces CrPC) · indiacode.nic.in"
            />
            <StatCard
              num={counts.judgments ?? 38400}
              label="Supreme Court reported judgments"
              source="SCR 1950–present · scr.sci.gov.in"
              prefix="~"
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
            <p className="sec-desc">How often each source is re-pulled and re-indexed.</p>
          </div>
          <div className="cadence">
            <div className="cadence-row">
              <span className="cad-when">Weekly</span>
              <span className="cad-what">Constitution + BNS/BNSS/IPC/CrPC — full re-pull from official gazette sources, diff-merged into the index.</span>
            </div>
            <div className="cadence-row">
              <span className="cad-when">Nightly</span>
              <span className="cad-what">New Supreme Court reported judgments — fetched from scr.sci.gov.in and embedded within 24 hours of publication.</span>
            </div>
            <div className="cadence-row">
              <span className="cad-when">Quarterly</span>
              <span className="cad-what">High Court reported judgments (beta) — selected High Courts, expanded each quarter. Roadmap: daily by Q1 2026.</span>
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
              <div className="ri-name">Bharatiya Sakshya Adhiniyam, 2023</div>
              <div className="ri-desc">New evidence Act replacing the Indian Evidence Act, 1872. All 170 sections being parsed and cross-linked.</div>
              <div className="ri-eta">Q4 2025</div>
            </div>
            <div className="roadmap-item">
              <div className="ri-name">All High Court reported judgments</div>
              <div className="ri-desc">Expanding beyond the current beta set to cover all 25 High Courts with neutral citations.</div>
              <div className="ri-eta">2026</div>
            </div>
            <div className="roadmap-item">
              <div className="ri-name">Subordinate legislation &amp; rules</div>
              <div className="ri-desc">Rules, notifications, and subordinate legislation under major statutes — the long tail of regulatory text.</div>
              <div className="ri-eta">Planned</div>
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}