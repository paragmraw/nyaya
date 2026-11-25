import type { Metadata } from "next";
import Breadcrumb from "@/components/Breadcrumb";
import StructuredData from "@/components/StructuredData";
// Import the schema from the pure-data module: a named export read off a
// "use client" module is a client reference in a server component, so
// faqSchema.mainEntity would be undefined at prerender time.
import { faqSchema } from "@/lib/schema";
import { pageOpenGraph } from "@/lib/site";

export const metadata: Metadata = {
  title: "FAQ",
  description:
    "Questions about Nyaya: good-law status handling, scanned PDFs, commercial-safe licenses, sealed-order exclusions, conversation storage, corpus refresh cadence, accounts, and usage limits.",
  openGraph: pageOpenGraph({
    title: "Nyaya FAQ",
    description:
      "How Nyaya handles precedent currency, scanned court PDFs, commercial licensing, sealed orders, conversation storage, corpus updates, accounts, and usage limits.",
  }),
};

export default function FaqPage() {
  return (
    <>
      {/* Single-sourced with the visible list below: the questions and
          answers rendered here are the same objects the JSON-LD serialises. */}
      <StructuredData data={faqSchema} />
      <main id="content" className="page">
        <div className="container">
          <Breadcrumb />

          {/* hero */}
          <section>
            <p className="eyebrow">FAQ</p>
            <h1>Frequently asked questions</h1>
            <p className="lead lead-wide">
              The short answers to what people ask. For the operator&apos;s
              own limits in full, see the <a href="/disclaimer/">Disclaimer</a>{" "}
              and <a href="/privacy/">Privacy</a> pages; for formats and
              pipeline mechanics, see <a href="/citations/">Citations</a>.
            </p>
          </section>

          {/* visible Q/A list, rendered from faqSchema.mainEntity */}
          <section className="section">
            {faqSchema.mainEntity.map((qa, idx) => (
              <div className="section" key={qa.name}>
                <div className="section-head">
                  <h2>
                    <span className="meta num">
                      {String(idx + 1).padStart(2, "0")}&nbsp;&nbsp;
                    </span>
                    {qa.name}
                  </h2>
                </div>
                <p>{qa.acceptedAnswer.text}</p>
              </div>
            ))}
          </section>

          {/* still stuck */}
          <section className="section">
            <div className="section-head">
              <h2>Not covered here?</h2>
              <p className="sec-desc">Where the remaining answers live.</p>
            </div>
            <p>
              The <a href="/architecture/">Architecture</a> page documents the
              retrieval pipeline, storage tiers, and the MCP tool surface in
              depth; the <a href="/contact/">Contact page</a> lists where to
              send anything the docs cannot answer.
            </p>
          </section>
        </div>
      </main>
    </>
  );
}
