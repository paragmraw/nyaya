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
        <strong>Model:</strong> nvidia/nemotron-3-embed-1b (2048-dim).<br />
        <strong>Latency target:</strong> &lt; 80ms p95.
      </>
    ),
  },
  {
    id: "pipe-retrieve",
    num: "02",
    title: "Retrieve",
    desc: "MCP tools fetch matching provisions and judgments from the indexed corpus (semantic search fuses embedding retrieval + reranking).",
    detail: (
      <>
        <strong>Store:</strong> pgvector + Postgres tsvector (GIN indexes).<br />
        <strong>Search:</strong> semantic_query (embedding retrieval + reranking), get_section, get_article, get_judgment.<br />
        <strong>Reranker:</strong> llama-nemotron-rerank-vl-1b-v2 (cross-encoder).<br />
        <strong>Corpus:</strong> Constitution, BNS/BNSS/BSA, IPC/CrPC/IEA/CPC, commercial statutes, SC judgments.
      </>
    ),
  },
  {
    id: "pipe-cite",
    num: "03",
    title: "Cite",
    desc: "Synthesis LLM drafts the answer with inline citations from retrieved passages.",
    detail: (
      <>
        <strong>Model:</strong> NVIDIA Nemotron-3.5 Lightning 30B.<br />
        <strong>Architecture:</strong> supervisor-synthesis LangGraph (supervisor plans, synthesis composes).<br />
        <strong>Constraint:</strong> no answer without a citation in the retrieved context.<br />
        <strong>Output:</strong> answer text + structured citation list.
      </>
    ),
  },
  {
    id: "pipe-mcp",
    num: "04",
    title: "MCP",
    desc: "get_section / get_article parse and fetch each reference from the corpus; MCP exposes 16 tools to editors.",
    detail: (
      <>
        <strong>Resolver:</strong> parse → match → fetch (built into get_section / get_article).<br />
        <strong>MCP server:</strong> HTTP endpoint at /mcp that exposes 16 tools + 11 resources.<br />
        <strong>Clients:</strong> Claude, Cursor, opencode.
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