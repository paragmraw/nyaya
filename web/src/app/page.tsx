import type { Metadata } from "next";
import CapTable from "@/components/CapTable";
import HomeStats from "@/components/HomeStats";
import McpConfig from "@/components/McpConfig";
import { ChatPanel } from "@/components/HomePageClient";
import StructuredData, { homeSchema } from "@/components/StructuredData";
import { API_VERSION, pageOpenGraph } from "@/lib/site";

// Feature flag: enable/disable optional /chat window
const CHAT_ENABLED = process.env.NEXT_PUBLIC_CHAT_ENABLED === "true";

export const metadata: Metadata = {
  title: "Home",
  description:
    "Database-free Indian law MCP server & assistant. Search 37 legislatures, 12 regulators, 15 tribunals, 38k+ Supreme Court judgments, and 20.7M+ High Court orders.",
  openGraph: pageOpenGraph({
    title: "Nyaya v2.0 - Indian Law MCP & Legal Research Assistant",
    description:
      "Pure-lexical & citation-graph legal MCP server covering Constitution, BNS/BNSS/BSA, Central & State Acts, Regulators, Tribunals, SC, and High Courts.",
  }),
};

function HomePageContent() {
  return (
    <main id="content" className="frame">
      {/* LEFT: capabilities & local/remote MCP setup */}
      <section className="pane-left">
        <div className="left-head">
          <p className="eyebrow">INDIAN LAW MCP v{API_VERSION} · PURE LEXICAL &amp; CITATION GRAPH</p>
          <h1>Verify Indian statutes, transitions &amp; case law.</h1>
          <p className="lead">
            A database-free legal MCP server and research assistant. Every reply resolves to numbered articles, sections, neutral citations, or judgments with license and validity metadata.
          </p>
          <HomeStats />
        </div>

        <div className="cap-wrap">
          <div className="cap-title">
            <h2>Capabilities</h2>
            <span className="tag">12 MCP tools</span>
          </div>
          <CapTable />

          <div className="src-chips" style={{ marginTop: "var(--gap-md)" }}>
            <span className="tag">Constitution · 106 Amds</span>
            <span className="tag">BNS / BNSS / BSA 2023</span>
            <span className="tag">IPC / CrPC / IEA (Legacy)</span>
            <span className="tag">36 State/UT Acts</span>
            <span className="tag">12 Regulators</span>
            <span className="tag">15 Tribunals</span>
            <span className="tag">SC &amp; 25 High Courts</span>
          </div>
        </div>

        <McpConfig variant="promo" />
      </section>

      {/* RIGHT: optional chat (disabled when NEXT_PUBLIC_CHAT_ENABLED !== "true") */}
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
