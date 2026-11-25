import type { Metadata } from "next";
import Breadcrumb from "@/components/Breadcrumb";
import CitationAnatomy from "@/components/CitationAnatomy";
import StructuredData, { citationsSchema, faqSchema } from "@/components/StructuredData";
import { FORMAT_CARDS, LIMITS, PIPE_STEPS } from "@/lib";
import { pageOpenGraph } from "@/lib/site";

export const metadata: Metadata = {
  title: "Citations",
  description:
    "How Nyaya v2.0 parses, resolves, and audits Indian legal citations: 2017 INSC 801, 2024:DHC:1234, 16-char CNR, CPC O.7 R.11, IEA s.65B(4), statutory validity, and honest limits.",
  openGraph: pageOpenGraph({
    title: "Nyaya Citations - Deterministic Legal Verification",
    description:
      "Supported Indian legal citation formats (INSC, HC Neutral, CNR, AIR, SCC, O.7 R.11, s.65B(4)), StatutoryValidity audits, and unverified_automated transparency.",
  }),
};

const AUDIT_STATUSES = [
  { code: "VERIFIED", desc: "Exact canonical tuple resolved in corpus and in force." },
  { code: "VERIFIED_REPEALED", desc: "Resolved statute repealed (e.g. IPC/CrPC/IEA on 2024-07-01); includes successor & BNSS s.531 savings." },
  { code: "VERIFIED_STRUCK_DOWN_OR_READ_DOWN", desc: "Resolved provision invalidated, read down, or kept in abeyance by Supreme Court order." },
  { code: "QUOTE_VERIFIED / QUOTE_MISMATCH", desc: "Verbatim quote checked against indexed chunks or on-demand AWS S3 PDF via pypdfium2." },
  { code: "NOT_FOUND_IN_CORPUS", desc: "Citation syntax valid, but no matching record exists (eliminates AIR 1973 SC 9999 false matches)." },
  { code: "INVALID_STATUTORY_REF / AMBIGUOUS", desc: "Malformed statutory section/article reference, or multiple candidate judgments matched without a unique tuple." },
  { code: "COMMERCIAL_LICENSE_BLOCKED", desc: "Exact record belongs to CC-BY-NC DDL/ILDC while commercial_safe_only is active." },
];

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
            <h1>Every citation resolves to an exact tuple</h1>
            <p className="lead lead-wide">
              Nyaya&apos;s shared parser (<code>nyaya/citations.py</code>) resolves INSC neutral citations, High Court neutral citations, 16-char eCourts CNRs, reporter citations, CPC Orders/Rules, and statutory sub-sections without guessing.
            </p>
          </section>

          {/* citation anatomy */}
          <CitationAnatomy />

          {/* citation formats */}
          <section className="section">
            <div className="section-head">
              <h2>Supported citation formats</h2>
              <p className="sec-desc">
                Priority-ordered recognition across Supreme Court, 25 High Courts, District CNRs, statutes, and 12 regulators.
              </p>
            </div>
            <div className="format-grid">
              {FORMAT_CARDS.map((f) => (
                <div className="format-card" key={f.type}>
                  <div className="fc-type">{f.type}</div>
                  <div className="fc-format">{f.format}</div>
                  <div className="fc-desc">{f.desc}</div>
                  <div className="fc-url">
                    <a href={f.url} target="_blank" rel="noopener noreferrer">{f.urlLabel}</a>
                  </div>
                </div>
              ))}
            </div>
          </section>

          {/* verification pipeline */}
          <section className="section">
            <div className="section-head">
              <h2>Verification pipeline</h2>
              <p className="sec-desc">
                Every reference in <code>verify_citations</code> and <code>/chat</code> passes through four deterministic stages.
              </p>
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

            <div className="verify-statuses">
              {AUDIT_STATUSES.map((st) => (
                <div className="vs-row" key={st.code}>
                  <span className="vs-code">{st.code}</span>
                  <span className="vs-desc">{st.desc}</span>
                </div>
              ))}
            </div>
          </section>

          {/* limitations */}
          <section className="section">
            <div className="section-head">
              <h2>Honest scope &amp; verification limits</h2>
              <p className="sec-desc">
                Nyaya surfaces raw evidence and explicit caveats instead of fabricating confidence or legal-currency claims.
              </p>
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
