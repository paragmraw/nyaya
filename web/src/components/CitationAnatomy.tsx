"use client";

import { useCallback, useRef, useState } from "react";
import { BalanceIcon } from "@/components/icons";
import SourceCard from "@/components/SourceCard";

export default function CitationAnatomy() {
  const [highlighted, setHighlighted] = useState<string | null>(null);
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const onCiteClick = useCallback((id: string) => {
    setHighlighted(id);
    if (timeoutRef.current) clearTimeout(timeoutRef.current);
    timeoutRef.current = setTimeout(() => setHighlighted((cur) => (cur === id ? null : cur)), 2500);
  }, []);

  return (
    <section className="section">
      <div className="section-head">
        <h2>Citation anatomy</h2>
        <p className="sec-desc">
          Every inline reference resolves through <code>nyaya/citations.py</code> to an exact canonical tuple, license metadata, and statutory validity envelope. Click a citation to highlight its source card.
        </p>
      </div>

      <div className="annotated">
        <div className="answer-bubble">
          <div className="ab-head">
            <span className="avatar" aria-hidden="true"><BalanceIcon /></span>
            <span className="ab-title">Nyaya v2.1 · Citation Audit Example</span>
          </div>
          <p>
            The Supreme Court in{" "}
            <a href="#sc-puttaswamy" className="inline-cite" onClick={(e) => { e.preventDefault(); onCiteClick("sc-puttaswamy"); }}>
              2017 INSC 801 ((2017) 10 SCC 1)
            </a>{" "}
            affirmed privacy as an intrinsic facet of{" "}
            <a href="#sc-constitution" className="inline-cite" onClick={(e) => { e.preventDefault(); onCiteClick("sc-constitution"); }}>
              Art. 21, Constitution of India
            </a>
            . High Court neutral citations such as{" "}
            <a href="#hc-dhc" className="inline-cite" onClick={(e) => { e.preventDefault(); onCiteClick("hc-dhc"); }}>
              2024:DHC:1234
            </a>{" "}
            and 16-char eCourts CNRs like{" "}
            <a href="#cnr-sample" className="inline-cite" onClick={(e) => { e.preventDefault(); onCiteClick("cnr-sample"); }}>
              MHAU019999992015
            </a>{" "}
            resolve directly without bare integer ambiguity.
          </p>
          <p>
            Statutory references parse sub-sections and Orders/Rules, e.g.{" "}
            <a href="#statute-iea-cpc" className="inline-cite" onClick={(e) => { e.preventDefault(); onCiteClick("statute-iea-cpc"); }}>
              IEA s.65B(4) (repealed → BSA s.63) &amp; CPC O.7 R.11
            </a>
            , surfacing automatic repeal, savings (BNSS s.531), and invalidation flags alongside raw citation-graph edges (<code>unverified_automated</code>).
          </p>
        </div>

        <SourceCard highlightedId={highlighted} />
      </div>
    </section>
  );
}
