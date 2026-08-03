"use client";

import { useCallback, useState } from "react";

type Stage = {
  id: string;
  num: string;
  title: string;
  desc: string;
  detail: React.ReactNode;
};

const STAGES: Stage[] = [
  {
    id: "pipe-query",
    num: "01",
    title: "Query",
    desc: "User question is parsed, normalised, and embedded for retrieval.",
    detail: (
      <>
        <strong>Input:</strong> natural-language legal question.<br />
        <strong>Output:</strong> normalised query + embedding vector.<br />
        <strong>Model:</strong> bge-m3 (multilingual, 1024-dim).<br />
        <strong>Latency target:</strong> &lt; 80ms p95.
      </>
    ),
  },
  {
    id: "pipe-retrieve",
    num: "02",
    title: "Retrieve",
    desc: "Vector store returns top-k matching passages from the indexed corpus.",
    detail: (
      <>
        <strong>Store:</strong> pgvector (pgvector extension on PostgreSQL).<br />
        <strong>k:</strong> top-20 passages, reranked to top-8.<br />
        <strong>Reranker:</strong> bge-reranker-v2.<br />
        <strong>Corpus:</strong> Constitution, BNS/BNSS/IPC/CrPC, SC judgments.
      </>
    ),
  },
  {
    id: "pipe-cite",
    num: "03",
    title: "Cite",
    desc: "LLM drafts the answer with inline citations from retrieved passages.",
    detail: (
      <>
        <strong>Model:</strong> instruction-tuned LLM (configuration TBD).<br />
        <strong>Constraint:</strong> no answer without a citation in the retrieved context.<br />
        <strong>Output:</strong> answer text + structured citation list.
      </>
    ),
  },
  {
    id: "pipe-mcp",
    num: "04",
    title: "MCP",
    desc: "Citation verifier checks each reference against the corpus before display; MCP exposes the pipeline to editors.",
    detail: (
      <>
        <strong>Verifier:</strong> parse → match → verify → link (see Citations).<br />
        <strong>MCP server:</strong> HTTP endpoint at /mcp — exposes search_law, get_article, verify_citation.<br />
        <strong>Clients:</strong> Claude, Cursor, Windsurf.
      </>
    ),
  },
];

export default function PipelineStage() {
  const [expanded, setExpanded] = useState<string | null>(null);

  const toggle = useCallback((id: string) => {
    setExpanded((cur) => (cur === id ? null : id));
  }, []);

  const onKey = useCallback((id: string) => (e: React.KeyboardEvent) => {
    if (e.key === "Enter" || e.key === " " || e.key === "Spacebar") {
      e.preventDefault();
      toggle(id);
    }
  }, [toggle]);

  return (
    <div className="pipeline">
      {STAGES.map((s, i) => (
        <div
          key={s.id}
          className={`pipe-card${expanded === s.id ? " expanded" : ""}`}
          tabIndex={0}
          role="button"
          aria-expanded={expanded === s.id}
          onClick={() => toggle(s.id)}
          onKeyDown={onKey(s.id)}
          id={s.id}
        >
          <div className="pc-num">{s.num}</div>
          <div className="pc-title">{s.title}</div>
          <div className="pc-desc">{s.desc}</div>
          {expanded === s.id && <div className="pc-detail">{s.detail}</div>}
          {i < STAGES.length - 1 && <span className="pc-arrow" aria-hidden="true">→</span>}
        </div>
      ))}
    </div>
  );
}