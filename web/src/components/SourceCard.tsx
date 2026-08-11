"use client";

import { useCallback, useState } from "react";

type Source = {
  id: string;
  name: string;
  ref: string;
  desc: string;
  url: string;
  urlLabel: string;
};

const SOURCES: Source[] = [
  {
    id: "sc-puttaswamy",
    name: "K.S. Puttaswamy v. Union of India",
    ref: "(2017) 10 SCC 1",
    desc: "Supreme Court: right to privacy is a fundamental right under Article 21, read with Articles 14 and 19.",
    url: "https://indiankanoon.org",
    urlLabel: "indiankanoon.org → (2017) 10 SCC 1",
  },
  {
    id: "sc-constitution",
    name: "Constitution of India",
    ref: "Art. 21, Constitution of India, 1950",
    desc: "Right to life and personal liberty; the source of the privacy guarantee read into Part III.",
    url: "https://github.com/Vikhram-S/IndianConstitution",
    urlLabel: "Vikhram-S/IndianConstitution → Art. 21",
  },
];

export default function SourceCard({ highlightedId }: { highlightedId?: string | null }) {
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set());

  const toggle = useCallback((id: string) => {
    setExpanded((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  return (
    <div className="source-cards">
      {SOURCES.map((s) => {
        const isExpanded = expanded.has(s.id);
        const isHighlighted = highlightedId === s.id;
        return (
          <div
            key={s.id}
            id={s.id}
            className={`source-card${isExpanded ? " expanded" : ""}`}
            style={isHighlighted ? { borderColor: "var(--accent)" } : undefined}
          >
            <div className="sc-head">
              <span className="sc-name">{s.name}</span>
              <span className="sc-verified"><span className="status-dot" />Sourced</span>
            </div>
            <div className="sc-body">
              <div className="sc-ref">{s.ref}</div>
              <div className="sc-desc">{s.desc}</div>
              {isExpanded && (
                <div className="sc-url">
                  <a href={s.url} target="_blank" rel="noopener noreferrer">{s.urlLabel}</a>
                </div>
              )}
              <button className="sc-toggle" type="button" onClick={() => toggle(s.id)}>
                {isExpanded ? "Collapse ↑" : "Expand source →"}
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}