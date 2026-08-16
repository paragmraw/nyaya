"use client";

import { useCallback, useRef, useState } from "react";
import BalanceIcon from "@mui/icons-material/Balance";
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
          A real answer with citations linked to their source cards. Click a citation to highlight its source card; click a source card to expand the source.
        </p>
      </div>

      <div className="annotated">
        <div className="answer-bubble">
          <div className="ab-head">
            <span className="avatar" aria-hidden="true"><BalanceIcon fontSize="small" /></span>
            <span className="ab-title">Nyaya Assistant</span>
          </div>
          <p>
            Article 21 of the Constitution guarantees the right to life and personal liberty. The Supreme Court in{" "}
            <a href="#sc-puttaswamy" className="inline-cite" onClick={(e) => { e.preventDefault(); onCiteClick("sc-puttaswamy"); }}>K.S. Puttaswamy v. Union of India, (2017) 10 SCC 1</a>
            {" "}held that the right to privacy is a fundamental right protected under{" "}
            <a href="#sc-constitution" className="inline-cite" onClick={(e) => { e.preventDefault(); onCiteClick("sc-constitution"); }}>Art. 21, Constitution of India</a>
            {", read with Articles 14 and 19."}
          </p>
          <p>
            The Court traced the right through earlier decisions on personal liberty and due process, establishing privacy as an intrinsic part of the guarantees in{" "}
            <a href="#sc-constitution" className="inline-cite" onClick={(e) => { e.preventDefault(); onCiteClick("sc-constitution"); }}>Part III of the Constitution</a>
            {". The judgment is binding on all courts in India under Article 141."}
          </p>
        </div>

        <SourceCard highlightedId={highlighted} />
      </div>
    </section>
  );
}