import type { Metadata } from "next";
import Breadcrumb from "@/components/Breadcrumb";
import { pageOpenGraph } from "@/lib/site";
import { CONTACT_EMAIL } from "@/lib/legal-facts";

export const metadata: Metadata = {
  title: "Disclaimer",
  description:
    "What Nyaya's answers can and cannot tell you: unverified_automated precedent status, no automated Shepardizing, honest corpus gaps (scanned PDFs, post-cutoff rulings, commercial license blocks), and verification duties before filing.",
  openGraph: pageOpenGraph({
    title: "Nyaya Disclaimer - not legal advice, verify before filing",
    description:
      "Nyaya surfaces raw evidence and explicit caveats, not certainty: precedent status is unverified_automated, scanned PDFs are rejected, and every citation must be verified against primary texts.",
  }),
};

export default function DisclaimerPage() {
  return (
    <main id="content" className="page">
      <div className="container">
        <Breadcrumb />

        {/* hero */}
        <section>
          <p className="eyebrow">Disclaimer</p>
          <h1>Raw evidence, not certainty</h1>
          <p className="lead lead-wide">
            This is not legal advice; verify citations before filing. Nyaya is
            a legal-research and citation-auditing aid. Using it creates no
            attorney-client relationship, and nothing it outputs should be
            relied on without independent verification against primary texts
            and the official Gazette.
          </p>
        </section>

        {/* what nyaya is */}
        <section className="section">
          <div className="section-head">
            <h2>What Nyaya does not do</h2>
            <p className="sec-desc">
              The product&apos;s own limits, stated openly rather than hidden.
            </p>
          </div>
          <div className="stack">
            <p>
              <strong>No automated &quot;good law&quot; analysis.</strong> The
              precedent-tracing and citation-verification tools return raw
              citation-graph edges, coram bench-strength comparisons, and
              quoting excerpts, each labelled{" "}
              <code>precedent_status=&quot;unverified_automated&quot;</code>.
              Nyaya does not perform automated NLP Shepardizing. &quot;Edge
              exists&quot; is a machine observation, not a determination that
              a judgment is good law. Always confirm currency before relying
              on a precedent.
            </p>
            <p>
              <strong>Verification statuses are evidence, not verdicts.</strong>{" "}
              The citation auditor reports concrete outcomes &mdash; an exact
              canonical tuple resolved in the corpus (<code>VERIFIED</code>),
              a repealed or struck-down statute, a verbatim quote matched or
              mismatched, a syntax-valid citation with no matching record
              (<code>NOT_FOUND_IN_CORPUS</code>) &mdash; but a VERIFIED result
              means the tuple exists and is in force <em>in the indexed
              corpus</em>, and nothing more.
            </p>
            <p>
              <strong>Chat answers are generated, not guaranteed.</strong> Chat
              replies come from a language model working over retrieved corpus
              text. Retrieval grounding, citation stripping when verification
              fails, and a disclaimer appended to chat output reduce error,
              but the model can still be wrong. Ground every legal position in
              the underlying judgment or statute, not in an AI reply.
            </p>
          </div>
        </section>

        {/* corpus completeness */}
        <section className="section">
          <div className="section-head">
            <h2>Honest corpus gaps</h2>
            <p className="sec-desc">
              Known holes in coverage &mdash; the same limits the /citations
              page documents.
            </p>
          </div>
          <div className="stack">
            <p>
              <strong>Scanned, image-only PDFs are rejected, not
              OCR-guessed.</strong> Court documents that yield fewer than 50
              characters per page (common in pre-2010 scanned High Court
              orders) are returned as unavailable-for-extraction rather than
              garbled machine text. That protects accuracy and creates gaps.
            </p>
            <p>
              <strong>Post-cutoff and unreported decisions return
              NOT_FOUND_IN_CORPUS</strong> instead of a confident guess at a
              citation that is not in the data.
            </p>
            <p>
              <strong>Non-commercial datasets are metadata where licensed
              that way.</strong> The bundled district-case metadata (Development
              Data Lab, 2010&ndash;2018, CC-BY-NC-SA-4.0) and the ILDC
              annotations (CC-BY-NC-4.0) are restricted by their own licenses;
              commercial deployments must enable the{" "}
              <code>commercial_safe_only</code> exclusion, and targeted
              lookups into those datasets may then return{" "}
              <code>COMMERCIAL_LICENSE_BLOCKED</code>.
            </p>
            <p>
              <strong>Sealed and protected material is excluded at
              ingestion.</strong> Sealed and in-camera proceedings, victim-identifying
              orders (IPC s. 228A / BNS s. 72, POCSO, Juvenile Justice Act
              s. 74, HIV/AIDS Act s. 34), and proprietary reporter headnotes
              (per <em>Eastern Book Company v. D.B. Modak</em>) are blocked,
              so results can be incomplete by design.
            </p>
          </div>
        </section>

        {/* professional reliance */}
        <section className="section">
          <div className="section-head">
            <h2>Professional reliance</h2>
            <p className="sec-desc">The duty remains yours.</p>
          </div>
          <div className="stack">
            <p>
              If you practice law, you already know the standard: check the
              subsequent history of every case, read the primary text in the
              official source, confirm the current force of every statute
              (especially across the IPC&nbsp;/ CrPC&nbsp;/ IEA to
              BNS&nbsp;/ BNSS&nbsp;/ BSA transition of 2024-07-01, with BNSS
              s. 531 savings), and never file from a secondary description.
              Nyaya is built to make those checks faster &mdash; it cannot do
              them for you.
            </p>
            <p>
              The service is provided &ldquo;as is&rdquo; and &ldquo;as
              available&rdquo;. See the <a href="/terms/">Terms of use</a> for
              the warranty and liability provisions, the{" "}
              <a href="/faq/">FAQ</a> for common questions, and the{" "}
              <a href="/contact/">Contact page</a> &mdash; reports of a wrong
              quote or wrong status are welcome at{" "}
              <code>{CONTACT_EMAIL}</code>.
            </p>
          </div>
        </section>
      </div>
    </main>
  );
}
