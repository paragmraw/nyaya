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
    id: "sc-crpc",
    name: "Code of Criminal Procedure, 1973",
    ref: "§ 41 & § 41A, CrPC 1973",
    desc: "Arrest without warrant — safeguards and notice of appearance.",
    url: "https://indiacode.nic.in",
    urlLabel: "indiacode.nic.in → CrPC S.41",
  },
  {
    id: "sc-arnesh",
    name: "Arnesh Kumar v. State of Bihar",
    ref: "(2014) 8 SCC 273",
    desc: "Supreme Court — prior notice under S. 41A CrPC mandatory before arrest for offences punishable ≤7 years.",
    url: "https://scr.sci.gov.in",
    urlLabel: "scr.sci.gov.in → (2014) 8 SCC 273",
  },
  {
    id: "sc-constitution",
    name: "Constitution of India",
    ref: "Art. 22(3)–(7), Constitution of India, 1950",
    desc: "Preventive detention safeguards — Advisory Board review and maximum detention period.",
    url: "https://legislative.gov.in",
    urlLabel: "legislative.gov.in → Art. 22",
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
              <span className="sc-verified"><span className="status-dot" />Verified</span>
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