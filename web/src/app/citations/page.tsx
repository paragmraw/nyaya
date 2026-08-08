"use client";

import { useCallback, useRef, useState } from "react";
import SourceCard from "@/components/SourceCard";
import { FORMAT_CARDS, LIMITS, PIPE_STEPS } from "@/lib";

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