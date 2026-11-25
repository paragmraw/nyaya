import type { Metadata } from "next";
import Breadcrumb from "@/components/Breadcrumb";
import { pageOpenGraph } from "@/lib/site";
import {
  BODY_CAP_BYTES,
  CONTACT_EMAIL,
  EFFECTIVE_DATE,
  REPO_URL,
  RATE_LIMITS,
} from "@/lib/legal-facts";

export const metadata: Metadata = {
  title: "Terms",
  description:
    "Terms of use for nyaya.parag.tech and its MCP service: as-is availability, not legal advice, Apache-2.0 and CC-BY corpus licensing, acceptable use, and rate limits.",
  openGraph: pageOpenGraph({
    title: "Nyaya Terms of Use",
    description:
      "By using the site you accept these terms: the service is provided as-is, outputs are not legal advice, and the corpus carries its upstream licenses.",
  }),
};

export default function TermsPage() {
  return (
    <main id="content" className="page">
      <div className="container">
        <Breadcrumb />

        {/* hero */}
        <section>
          <p className="eyebrow">Terms</p>
          <h1>Terms of use</h1>
          <p className="lead lead-wide">
            These terms govern your use of the Nyaya website, its REST API,
            and its Model Context Protocol (MCP) server. By using any of them
            you accept these terms. Effective{" "}
            <time dateTime="2026-11-25">{EFFECTIVE_DATE}</time>. The service
            is operated by the nyaya contributors; reach us at{" "}
            <code>{CONTACT_EMAIL}</code> or on{" "}
            <a href={REPO_URL} target="_blank" rel="noopener noreferrer">
              GitHub
            </a>
            .
          </p>
        </section>

        {/* the service */}
        <section className="section">
          <div className="section-head">
            <h2>The service</h2>
            <p className="sec-desc">What is on offer, plainly.</p>
          </div>
          <div className="stack">
            <p>
              Nyaya is a legal research and citation-auditing tool: read-only
              search over an indexed corpus of Indian statutes, regulators,
              tribunals, and court judgments; deterministic citation
              verification; optional AI chat grounded in retrieved corpus
              text; and MCP tools for integration clients. The service is
              provided free of charge, as is and as available, without
              accounts.
            </p>
            <p>
              Nothing in the output constitutes legal advice. Nyaya does not
              create an attorney-client relationship, does not act for you,
              and does not check facts, filings, or deadlines on your behalf.
              See the <a href="/disclaimer/">Disclaimer</a> for the complete
              statement of output limits.
            </p>
          </div>
        </section>

        {/* licensing */}
        <section className="section">
          <div className="section-head">
            <h2>Licensing</h2>
            <p className="sec-desc">
              Who owns what, and the licenses that carry it upstream.
            </p>
          </div>
          <div className="stack">
            <p>
              <strong>Code</strong> is open source under the Apache License
              2.0 (see the repository NOTICE file), for{" "}
              <code>mcp/</code>, <code>chat/</code>, and <code>web/</code>{" "}
              alike.
            </p>
            <p>
              <strong>Legislative and judicial text</strong> carries its
              upstream licenses: packs are CC-BY-4.0 or CC0-1.0 where they are
              open-data releases, and the reproduction of statutes and
              judgments rests on Indian Copyright Act 1957{" "}
              <strong>s. 52(1)(q)</strong> (Acts of the Legislature and
              judgments/orders of courts and tribunals, unless their
              publication is prohibited by that authority). Proprietary
              reporter headnotes are excluded, consistent with{" "}
              <em>Eastern Book Company v. D.B. Modak</em>. Upstream
              attribution is preserved per record in provenance metadata
              (license, source, checksum); see the{" "}
              <a href="/corpus/">Corpus page</a> for the per-source list.
            </p>
            <p>
              <strong>Restricted packs.</strong> The district-court metadata
              (2010&ndash;2018, CC-BY-NC-SA-4.0) and ILDC annotations
              (CC-BY-NC-4.0) are licensed for non-commercial use only; they
              are tagged <code>commercial_safe=false</code>, and deployment
              configurations that must exclude them can set the{" "}
              <code>commercial_safe_only</code> flag. If your use of the
              hosted service is commercial and you need guaranteed exclusion
              of non-commercial data, use a self-hosted deployment with that
              flag enabled.
            </p>
            <p>
              Your queries, and any content you paste into chat, remain
              yours; we claim no rights in what you submit (see the{" "}
              <a href="/privacy/">Privacy statement</a> for how it is
              handled).
            </p>
          </div>
        </section>

        {/* acceptable use */}
        <section className="section">
          <div className="section-head">
            <h2>Acceptable use</h2>
            <p className="sec-desc">The house rules; violations may be blocked.</p>
          </div>
          <div className="stack">
            <p>
              The service is rate-limited per client IP: {RATE_LIMITS.chat}{" "}
              chat turns, {RATE_LIMITS.rest} REST requests,{" "}
              {RATE_LIMITS.mcp} MCP calls, and {RATE_LIMITS.analytics}{" "}
              analytics requests per minute, with a{" "}
              {Math.round(BODY_CAP_BYTES / 1024 / 1024)} MB streaming body
              cap. Do not attempt to circumvent these limits, spoof
              forwarding headers, or send oversized or malicious payloads.
            </p>
            <p>
              Use the service lawfully and for its intended purpose: legal
              research, citation verification, and integrations. Do not use
              chat to ask for legal conclusions you intend to rely on for
              filing without independent verification, do not probe for
              protected or sealed material (it is excluded by design), and do
              not use the service in violation of applicable law.
            </p>
          </div>
        </section>

        {/* no warranty / liability */}
        <section className="section">
          <div className="section-head">
            <h2>No warranty, and what that means</h2>
            <p className="sec-desc">Plain-language disclaimers.</p>
          </div>
          <div className="stack">
            <p>
              The service, its corpus, and its outputs are provided &ldquo;as
              is&rdquo; and &ldquo;as available&rdquo;, without warranties of
              any kind, express or implied, including accuracy,
              completeness, availability, fitness for a particular purpose,
              and non-infringement. The corpus is a snapshot of upstream
              open-data releases and is updated by published releases, not in
              real time.
            </p>
            <p>
              To the maximum extent permitted by law, the nyaya contributors
              are not liable for loss or damage arising from your use of, or
              reliance on, the service or its outputs. For legal
              work, the value of the service is as a research aid: verify
              everything against primary texts before filing.
            </p>
          </div>
        </section>

        {/* availability / changes */}
        <section className="section">
          <div className="section-head">
            <h2>Availability, changes, and governing law</h2>
          </div>
          <div className="stack">
            <p>
              <strong>Availability.</strong> The service runs as a single
              hosted container and may be unavailable, degraded, or
              discontinued at any time; there is no uptime commitment. The
              code is open source, so a self-hosted deployment is always
              available to you as an alternative dependency.
            </p>
            <p>
              <strong>Changes.</strong> The service, corpus, and these terms
              may change; material changes to the terms will be reflected on
              this page with a new effective date. Continuing to use the
              service after a change means accepting the updated terms.
            </p>
            <p>
              <strong>Governing law.</strong> These terms are governed by the
              laws of India, and disputes arising from use of the hosted
              service are subject to the jurisdiction of the Indian courts.
            </p>
          </div>
        </section>
      </div>
    </main>
  );
}
