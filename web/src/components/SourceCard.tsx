"use client";

import { useCallback, useState } from "react";

type Source = {
  id: string;
  name: string;
  ref: string;
  badge: string;
  commercialSafe: boolean;
  desc: string;
  url: string;
  urlLabel: string;
};

const SOURCES: Source[] = [
  {
    id: "sc-puttaswamy",
    name: "K.S. Puttaswamy v. Union of India",
    ref: "2017 INSC 801 · (2017) 10 SCC 1",
    badge: "CC-BY-4.0 · SC",
    commercialSafe: true,
    desc: "9-Judge Constitution Bench on Art. 21 privacy. Subsequent edges carry precedent_status=\"unverified_automated\".",
    url: "https://registry.opendata.aws/indian-supreme-court-judgments/",
    urlLabel: "Dattam Labs AWS Open Data → SC PDFs",
  },
  {
    id: "hc-dhc",
    name: "Delhi High Court (Neutral Citation)",
    ref: "2024:DHC:1234",
    badge: "CC-BY-4.0 · 25 HCs",
    commercialSafe: true,
    desc: "Resolved across 17.77M+ High Court Parquet rows + on-demand AWS S3 PDFs extracted via pypdfium2.",
    url: "https://registry.opendata.aws/indian-high-court-judgments/",
    urlLabel: "Dattam Labs AWS Open Data → HC Parquet & PDFs",
  },
  {
    id: "sc-constitution",
    name: "Constitution of India",
    ref: "Art. 21, Constitution of India, 1950",
    badge: "IN-S52-1-q · Core",
    commercialSafe: true,
    desc: "506 articles, 12 schedules, and audited Amendments 1–106 in baked SQLite FTS5.",
    url: "https://legislative.gov.in",
    urlLabel: "Legislative Dept., Govt. of India → Constitution",
  },
  {
    id: "statute-iea-cpc",
    name: "Statutes, Sub-Sections & Validity",
    ref: "IEA s.65B(4) (repealed 2024-07-01 → BSA s.63) · CPC O.7 R.11",
    badge: "CC-BY-4.0 · Concordance",
    commercialSafe: true,
    desc: "StatutoryValidity flags repeal date, successor provision, and struck-down/abeyance judgments.",
    url: "https://oss-data-in.vaquill.ai",
    urlLabel: "Vaquill Open India Law v2026.08.1 → Statutes",
  },
  {
    id: "cnr-sample",
    name: "eCourts 16-Character CNR Docket",
    ref: "MHAU019999992015",
    badge: "CC-BY-NC-SA-4.0 · Metadata Only",
    commercialSafe: false,
    desc: "DDL 81M District Court metadata record (non-commercial; excluded when NYAYA_COMMERCIAL_SAFE_ONLY=1).",
    url: "https://www.devdatalab.org/judicial-data",
    urlLabel: "Development Data Lab → District Court Metadata",
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
              <span className={`sc-verified ${s.commercialSafe ? "" : "sc-nc"}`}>
                <span className="status-dot" />
                {s.badge}
              </span>
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
                {isExpanded ? "Collapse ↑" : "Expand provenance →"}
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}
