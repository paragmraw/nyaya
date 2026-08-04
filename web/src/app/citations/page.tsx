"use client";

import { useCallback, useRef, useState } from "react";
import SourceCard from "@/components/SourceCard";

type FormatCard = {
  type: string;
  format: string;
  desc: string;
  url: string;
  urlLabel: string;
};

const FORMAT_CARDS: FormatCard[] = [
  { type: "Constitution", format: "Art. 21, Constitution of India", desc: "Article number from the Constitution of India, 1950, with the full title.", url: "https://github.com/Vikhram-S/IndianConstitution", urlLabel: "→ Vikhram-S/IndianConstitution" },
  { type: "Statute (BNS / BNSS / BSA)", format: "§ 358, BNS 2023", desc: "Section number from the named statute, with the year and short title.", url: "https://prsindia.org", urlLabel: "→ prsindia.org (CC BY 4.0)" },
  { type: "Case law", format: "K.S. Puttaswamy v. Union of India, (2017) 10 SCC 1", desc: "Party names, neutral or SCC citation, and volume/journal reference.", url: "https://indiankanoon.org", urlLabel: "→ indiankanoon.org" },
];

type PipeStep = { num: string; title: string; desc: string; eg: string };
const PIPE_STEPS: PipeStep[] = [
  { num: "01", title: "Parse", desc: "Extract citation strings from the draft answer — article numbers, section references, case names.", eg: "\"Art. 21\" → {type: constitution, art: 21}" },
  { num: "02", title: "Match", desc: "Look up each parsed citation in the indexed corpus to confirm it exists and the text matches.", eg: "match(\"S. 41A\", \"CrPC 1973\") → found, section_text" },
  { num: "03", title: "Fetch", desc: "Pull the full provision or judgment text from the corpus so the citation can be displayed inline.", eg: "fetch(\"Art. 21\") → Constitution article text" },
  { num: "04", title: "Display", desc: "Render the citation as a link the user can click to open the source card with the full text and provenance.", eg: "→ Source card: Constitution of India, Art. 21" },
];

type Limit = { mark: string; title: string; sub: string };
const LIMITS: Limit[] = [
  { mark: "01", title: "Unreported judgments", sub: "Orders and judgments not published in a reported journal are not in the corpus. Nyaya will say it cannot find a citation rather than invent one." },
  { mark: "02", title: "Subordinate legislation & rules", sub: "Notifications, rules, and subordinate legislation under major statutes are not yet indexed. Coming per the corpus roadmap." },
  { mark: "03", title: "Pre-1950 Privy Council decisions", sub: "Judgments of the Privy Council (the apex court for British India until 1950) are not yet indexed. Planned." },
  { mark: "04", title: "Overruled / good-law status", sub: "Nyaya does not yet track whether a judgment has been overruled or modified by a later bench. Always confirm currency before filing." },
];

export default function CitationsPage() {
  const [highlighted, setHighlighted] = useState<string | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const onCiteClick = useCallback((id: string) => {
    setHighlighted(id);
    // Clear any pending timeout from a previous click to prevent races.
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    // Auto-clear the highlight after 2.5s (matches the design's JS).
    timeoutRef.current = setTimeout(() => setHighlighted((cur) => (cur === id ? null : cur)), 2500);
  }, []);

  return (
    <main className="page">
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
        <section className="section">
          <div className="section-head">
            <h2>Citation anatomy</h2>
            <p className="sec-desc">
              A real answer with citations linked to their source cards. Click a citation to highlight its source card; click a source card to expand the source.
            </p>
          </div>

          <div className="annotated">
            <div className="answer-bubble">
              <div className="ab-head">
                <span className="avatar">§</span>
                <span className="ab-title">Nyaya Assistant</span>
              </div>
              <p>
                Article 21 of the Constitution guarantees the right to life and personal liberty. The Supreme Court in{" "}
                <button className="inline-cite" onClick={() => onCiteClick("sc-puttaswamy")}>K.S. Puttaswamy v. Union of India, (2017) 10 SCC 1</button>
                {" "}held that the right to privacy is a fundamental right protected under{" "}
                <button className="inline-cite" onClick={() => onCiteClick("sc-constitution")}>Art. 21, Constitution of India</button>
                {", read with Articles 14 and 19."}
              </p>
              <p>
                The Court traced the right through earlier decisions on personal liberty and due process, establishing privacy as an intrinsic part of the guarantees in{" "}
                <button className="inline-cite" onClick={() => onCiteClick("sc-constitution")}>Part III of the Constitution</button>
                {". The judgment is binding on all courts in India under Article 141."}
              </p>
            </div>

            <SourceCard highlightedId={highlighted} />
          </div>
        </section>

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