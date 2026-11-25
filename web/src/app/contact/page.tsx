import type { Metadata } from "next";
import Breadcrumb from "@/components/Breadcrumb";
import { pageOpenGraph } from "@/lib/site";
import { CONTACT_EMAIL, REPO_URL } from "@/lib/legal-facts";

export const metadata: Metadata = {
  title: "Contact",
  description:
    "How to reach the nyaya contributors: feedback and error reports (include the request id or error digest), bad-citation and status corrections, data takedown or correction requests, security vulnerability reports, and GitHub issues.",
  openGraph: pageOpenGraph({
    title: "Contact the Nyaya project",
    description:
      "Email mail@parag.tech for feedback, corrections, takedown requests and security reports; open GitHub issues for bugs and contributions.",
  }),
};

export default function ContactPage() {
  return (
    <main id="content" className="page">
      <div className="container">
        <Breadcrumb />

        {/* hero */}
        <section>
          <p className="eyebrow">Contact</p>
          <h1>Reaching the project</h1>
          <p className="lead lead-wide">
            The primary channel is email: <code>{CONTACT_EMAIL}</code>. Send
            the purpose in the subject line and include the identifiers listed
            below: they cut the round-trips dramatically. For bugs and
            contributions, the repository&apos;s issue tracker is the right
            place:{" "}
            <a href={REPO_URL} target="_blank" rel="noopener noreferrer">
              {REPO_URL}
            </a>
            .
          </p>
        </section>

        {/* purposes */}
        <section className="section">
          <div className="section-head">
            <h2>What to send, and where</h2>
            <p className="sec-desc">One entry per purpose; all email goes to the same address.</p>
          </div>
          <div className="stack">
            <p>
              <strong>Wrong citation or wrong status.</strong> Include the
              citation or quote involved, the status the service returned (for
              example <code>QUOTE_MISMATCH</code> or{" "}
              <code>NOT_FOUND_IN_CORPUS</code> and its request id), and what
              the primary source says. The audit-status vocabulary documented
              on the <a href="/citations/">Citations page</a> is the right
              vocabulary for these reports.
            </p>
            <p>
              <strong>Corpus errors and takedown / correction requests.</strong>{" "}
              For a judgment with a bad extraction or an upstream error, or
              for content from the non-commercial packs (Development Data
              Lab, ILDC) or elsewhere that should be corrected or removed,
              include the document id from the corpus entry (shown in the
              corpus table and in deep links).
            </p>
            <p>
              <strong>Failed requests and chat errors.</strong> The chat and
              API error messages surface a request id (<code>rid</code>);
              the browser error screen shows a short error digest. Include
              either in your email; neither contains your message
              content, but both point straight at the incident in server logs.
            </p>
            <p>
              <strong>Feedback on these pages.</strong> Missing answers,
              unclear wording, accessibility barriers (see the{" "}
              <a href="/accessibility/">Accessibility statement</a>): same
              address, plain prose is fine.
            </p>
            <p>
              <strong>Security vulnerabilities.</strong> Email{" "}
              <code>{CONTACT_EMAIL}</code> with a description, reproduction
              steps, and impact. Do not open a public GitHub issue for
              security vulnerabilities. You should receive an acknowledgement
              within 48 hours and an investigated response with a fix timeline
              within 7 days (the commitments in the repository&apos;s
              SECURITY.md). Please do not disclose the issue publicly until a
              fix is released.
            </p>
            <p>
              <strong>Bugs and contributions, not security.</strong>{" "}
              For everything except security reports, the{" "}
              <a href={REPO_URL} target="_blank" rel="noopener noreferrer">
                GitHub repository
              </a>{" "}
              is faster than email: open an issue with steps to reproduce, or
              send a pull request.
            </p>
          </div>
        </section>

        {/* expectations */}
        <section className="section">
          <div className="section-head">
            <h2>What we can and cannot promise</h2>
          </div>
          <div className="stack">
            <p>
              Security reports carry the 48-hour / 7-day commitments above. For
              corrections and other email, we will read what you send and act
              on what is actionable; there is no service-level commitment
              beyond the security window above. If a fix ships, it will be
              visible in the published change history on GitHub.
            </p>
          </div>
        </section>
      </div>
    </main>
  );
}
