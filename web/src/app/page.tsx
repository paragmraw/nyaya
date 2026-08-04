"use client";

import CapTable from "@/components/CapTable";
import ChatPanel from "@/components/ChatPanel";
import McpConfig from "@/components/McpConfig";
import { useHealthSummary, formatNumber } from "@/lib";

export default function HomePage() {
  const { data, error } = useHealthSummary();
  const counts = data?.counts;
  // Live counts with real corpus baselines as fallbacks (395 articles, 5 judgments).
  // Sections fallback is null (shows "—") because the total depends on ingestion
  // and cannot be stated accurately from static data.
  const articles = counts?.articles ?? 395;
  const sections = counts?.sections ?? null;
  const judgments = counts?.judgments ?? 5;

  return (
    <main id="content" className="frame">
      {/* LEFT: capabilities */}
      <section className="pane-left">
        <div className="left-head">
          <p className="eyebrow">CONVERSATIONAL AI · INDIAN LAW</p>
          <h1>Ask the Constitution, CrPC &amp; statute book — get cited answers.</h1>
          <p className="lead">
            A retrieval-grounded assistant for practicing lawyers. Every reply traces to a numbered article, section, or judgment — no paraphrased guesswork.
          </p>
          <div className="left-stats">
            <div className="ls">
              <div className="ls-num num">{formatNumber(articles)}</div>
              <div className="ls-lbl">Articles indexed</div>
            </div>
            <div className="ls">
              <div className="ls-num num">{formatNumber(sections)}</div>
              <div className="ls-lbl">Sections indexed</div>
            </div>
            <div className="ls">
              <div className="ls-num num">{formatNumber(judgments)}</div>
              <div className="ls-lbl">Judgments linked</div>
            </div>
          </div>
          {error && (
            <p className="meta" style={{ marginTop: 8, color: "var(--muted)" }}>
              Live counts unavailable — showing fallback numbers.
            </p>
          )}
        </div>

        <div className="cap-wrap">
          <div className="cap-title">
            <h2>Capabilities</h2>
            <span className="tag">6 modules</span>
          </div>
          <CapTable />

          <div className="src-chips" style={{ marginTop: "var(--gap-md)" }}>
            <span className="tag">Constitution of India</span>
            <span className="tag">IPC 1860</span>
            <span className="tag">CrPC 1973</span>
            <span className="tag">BNS 2023</span>
            <span className="tag">BNSS 2023</span>
            <span className="tag">SC judgments</span>
          </div>
        </div>

        <McpConfig variant="promo" />
      </section>

      {/* RIGHT: chat (out of scope — blurred) */}
      <section className="pane-right">
        <ChatPanel />
      </section>
    </main>
  );
}