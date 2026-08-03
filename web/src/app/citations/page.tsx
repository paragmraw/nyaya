"use client";

import { useCallback, useState } from "react";
import SourceCard from "@/components/SourceCard";

type FormatCard = {
  type: string;
  format: string;
  desc: string;
  url: string;
  urlLabel: string;
};

const FORMAT_CARDS: FormatCard[] = [
  { type: "Constitution", format: "Art. 21, Constitution of India", desc: "Article number from the Constitution of India, 1950, with the full title.", url: "https://legislative.gov.in", urlLabel: "→ legislative.gov.in" },
  { type: "Statute (BNS / BNSS / BSA)", format: "§ 358, BNS 2023", desc: "Section number from the named statute, with the year and short title.", url: "https://indiacode.nic.in", urlLabel: "→ indiacode.nic.in" },
  { type: "Case law", format: "K.S. Puttaswamy v. Union of India, (2017) 10 SCC 1", desc: "Party names, neutral or SCR citation, and volume/journal reference.", url: "https://scr.sci.gov.in", urlLabel: "→ scr.sci.gov.in" },
];

type PipeStep = { num: string; title: string; desc: string; eg: string };
const PIPE_STEPS: PipeStep[] = [
  { num: "01", title: "Parse", desc: "Extract citation strings from the draft answer — article numbers, section references, case names.", eg: "\"Art. 22(2)\" → {type: constitution, art: 22, sub: 2}" },
  { num: "02", title: "Match", desc: "Look up each parsed citation in the indexed corpus to confirm it exists and the text matches.", eg: "match(\"S. 41A\", \"CrPC 1973\") → found, section_text" },
  { num: "03", title: "Verify", desc: "For case law, check the citation status — is the judgment overruled, modified, or still good law?", eg: "Arnesh Kumar (2014) → good law, not overruled" },
  { num: "04", title: "Link", desc: "Attach the official source URL so the user can open the full text directly from the answer.", eg: "→ scr.sci.gov.in/case/arnesh-kumar-2014" },
];

type Limit = { mark: string; title: string; sub: string };
const LIMITS: Limit[] = [
  { mark: "01", title: "Unreported judgments", sub: "Orders and judgments not published in SCR or a reported journal are not in the corpus. Nyaya will say it cannot find a citation rather than invent one." },
  { mark: "02", title: "Subordinate legislation & rules", sub: "Notifications, rules, and subordinate legislation under major statutes are not yet indexed. Coming per the corpus roadmap." },
  { mark: "03", title: "Pre-1950 Privy Council decisions", sub: "Judgments of the Privy Council (the apex court for British India until 1950) are not yet indexed. Planned." },
];

export default function CitationsPage() {
  const [highlighted, setHighlighted] = useState<string | null>(null);

  const onCiteClick = useCallback((id: string) => {
    setHighlighted(id);
    // auto-clear the highlight after 2.5s (matches the design's JS)
    setTimeout(() => setHighlighted((cur) => (cur === id ? null : cur)), 2500);
  }, []);

  return (
    <main className="page">
      <div className="container">
        {/* hero */}
        <section>
          <p className="eyebrow">Citations</p>
          <h1>Every answer cites its source</h1>
          <p className="lead">
            Nyaya never answers without a verifiable reference. Here is how citations are structured, checked, and linked back to the official record.
          </p>
        </section>

        {/* citation anatomy */}
        <section className="section">
          <div className="section-head">
            <h2>Citation anatomy</h2>
            <p className="sec-desc">
              A real answer with citations linked to their source cards. Click a citation to highlight its source card; click a source card to expand the full URL.
            </p>
          </div>

          <div className="annotated">
            <div className="answer-bubble">
              <div className="ab-head">
                <span className="avatar">§</span>
                <span className="ab-title">Nyaya Assistant</span>
              </div>
              <p>
                Article 22 of the Constitution provides safeguards against arbitrary arrest and detention. Sub-clause (1) guarantees the right of an arrested person to be informed of the grounds of arrest and to consult a legal practitioner of their choice. Sub-clause (2) mandates that every arrested person must be produced before the nearest magistrate within 24 hours — excluding travel time.
              </p>
              <p>
                These protections operate alongside{" "}
                <button className="inline-cite" onClick={() => onCiteClick("sc-crpc")}>S. 41 &amp; 41A, CrPC 1973</button>
                , which restrict police power to arrest without warrant for offences punishable with up to seven years. The Supreme Court in{" "}
                <button className="inline-cite" onClick={() => onCiteClick("sc-arnesh")}>Arnesh Kumar v. State of Bihar (2014) 8 SCC 273</button>
                {" "}made prior notice under S. 41A mandatory before arrest in such cases.
              </p>
              <p>
                Preventive detention is permitted under{" "}
                <button className="inline-cite" onClick={() => onCiteClick("sc-constitution")}>Art. 22(3)–(7), Constitution of India</button>
                , but only under a law made by Parliament or a State Legislature, and subject to review by an Advisory Board within the maximum period stated in the statute.
              </p>
            </div>

            <SourceCard highlightedId={highlighted} />
          </div>
        </section>

        {/* citation formats */}
        <section className="section">
          <div className="section-head">
            <h2>Supported citation formats</h2>
            <p className="sec-desc">Every citation in a Nyaya answer follows one of these three canonical formats, each linked to an official source.</p>
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
            <p className="sec-desc">Every citation passes through four checks before it reaches your answer. Hover a step for technical detail.</p>
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
            <p className="sec-desc">Honest limits — we would rather tell you than guess.</p>
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
  );
}