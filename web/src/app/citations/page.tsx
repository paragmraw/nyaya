import type { Metadata } from "next";
import { CitationAnatomy } from "@/components/CitationsPageClient";
import Breadcrumb from "@/components/Breadcrumb";
import { FORMAT_CARDS, LIMITS, PIPE_STEPS } from "@/lib";
import StructuredData, { citationsSchema, faqSchema } from "@/components/StructuredData";

export const metadata: Metadata = {
  title: "Citations",
  description:
    "How Nyaya structures, verifies, and links citations. Supported formats: Constitution articles, statute sections, and case law with neutral citations.",
  openGraph: {
    title: "Nyaya Citations - Verifiable Legal References",
    description:
      "How Nyaya structures, verifies, and links citations. Supported formats: Constitution articles, statute sections, and case law with neutral citations.",
    type: "website",
  },
};

export default function CitationsPage() {
  return (
    <>
      <StructuredData data={citationsSchema} />
      <StructuredData data={faqSchema} />
      <main id="content" className="page">
        <Breadcrumb />
        <div className="container">
          {/* hero */}
          <section>
            <p className="eyebrow">Citations</p>
            <h1>Every answer cites its source</h1>
            <p className="lead">
              Nyaya never answers without a verifiable reference. Here is how citations are structured, resolved, and linked back to the source.
            </p>
          </section>

          {/* citation anatomy */}
          <CitationAnatomy />

          {/* citation formats */}
          <section className="section">
            <div className="section-head">
              <h2>Supported citation formats</h2>
              <p className="sec-desc">Every citation in a Nyaya answer follows one of these three canonical formats, each linked to its source.</p>
            </div>
            <div className="format-grid">
              {FORMAT_CARDS.map((f) => (
                <div className="format-card" key={f.type}>
                  <div className="fc-type">{f.type}</div>
                  <div className="fc-format">{f.format}</div>
                  <div className="fc-desc">{f.desc}</div>
                  <div className="fc-url"><a href={f.url} target="_blank" rel="noopener noreferrer">{f.urlLabel}</a></div>
                </div>
              ))}
            </div>
          </section>

          {/* verification pipeline */}
          <section className="section">
            <div className="section-head">
              <h2>Verification pipeline</h2>
              <p className="sec-desc">Every citation passes through these steps before it reaches your answer. Hover a step for detail.</p>
            </div>
            <div className="pipe-grid">
              {PIPE_STEPS.map((s) => (
                <div className="pipe-step" key={s.num}>
                  <div className="ps-num">{s.num}</div>
                  <div className="ps-title">{s.title}</div>
                  <div className="ps-desc">{s.desc}</div>
                  <div className="ps-eg">{s.eg}</div>
                </div>
              ))}
            </div>
          </section>

          {/* limitations */}
          <section className="section">
            <div className="section-head">
              <h2>What Nyaya does not cite yet</h2>
              <p className="sec-desc">Honest limits. We would rather tell you than guess.</p>
            </div>
            <div className="limits">
              {LIMITS.map((l) => (
                <div className="limit-row" key={l.mark}>
                  <span className="lim-mark">{l.mark}</span>
                  <div className="lim-text">
                    {l.title}
                    <div className="lim-sub">{l.sub}</div>
                  </div>
                </div>
              ))}
            </div>
          </section>
        </div>
      </main>
    </>
  );
}