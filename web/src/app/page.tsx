"use client";

import CapTable from "@/components/CapTable";
import ChatPanel from "@/components/ChatPanel";
import McpConfig from "@/components/McpConfig";
import { useHealthSummary, formatNumber } from "@/lib";

export default function HomePage() {
  const { data, error } = useHealthSummary();
  const counts = data?.counts;
  // Live counts with design-number fallbacks (1,278 / 9,142 / 61,300 from the export)
  const articles = counts?.articles ?? 1278;
  const sections = counts?.sections ?? 9142;
  const judgments = counts?.judgments ?? 61300;

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
              <div className="ls-lbl">Sections (CrPC/IPC/BNS)</div>
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
            <span className="tag">CrPC 1973</span>
            <span className="tag">BNS 2023</span>
            <span className="tag">BNSS 2023</span>
            <span className="tag">SC / HC e-SCR</span>
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