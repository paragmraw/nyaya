import type { Metadata } from "next";
import { CapTable, ChatPanel, McpConfig } from "@/components/HomePageClient";
import StructuredData, { homeSchema } from "@/components/StructuredData";

// Feature flag: enable/disable chat window
const CHAT_ENABLED = process.env.NEXT_PUBLIC_CHAT_ENABLED === "true";

export const metadata: Metadata = {
  title: "Home",
  description:
    "Ask the Constitution, CrPC & statute book. Get cited answers from a retrieval-grounded AI for Indian law.",
  openGraph: {
    title: "Nyaya - Indian Law AI Assistant",
    description:
      "Ask the Constitution, CrPC & statute book. Get cited answers from a retrieval-grounded AI for Indian law.",
    type: "website",
  },
};

function HomePageContent() {
  return (
    <main id="content" className="frame">
      {/* LEFT: capabilities */}
      <section className="pane-left">
        <div className="left-head">
          <p className="eyebrow">CONVERSATIONAL AI · INDIAN LAW</p>
          <h1>Ask the Constitution, CrPC & statute book. Get cited answers.</h1>
          <p className="lead">
            A retrieval-grounded assistant for practicing lawyers. Every reply traces to a numbered article, section, or judgment. No paraphrased guesswork.
          </p>
          <div className="left-stats">
            <div className="ls">
              <div className="ls-num num">464</div>
              <div className="ls-lbl">Articles indexed</div>
            </div>
            <div className="ls">
              <div className="ls-num num">3,257</div>
              <div className="ls-lbl">Sections indexed</div>
            </div>
            <div className="ls">
              <div className="ls-num num">5</div>
              <div className="ls-lbl">Judgments linked</div>
            </div>
          </div>
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

      {/* RIGHT: chat (can be disabled via NEXT_PUBLIC_CHAT_ENABLED) */}
      <section className="pane-right">
        <ChatPanel disabled={!CHAT_ENABLED} />
      </section>
    </main>
  );
}

export default function HomePage() {
  return (
    <>
      <StructuredData data={homeSchema} />
      <HomePageContent />
    </>
  );
}