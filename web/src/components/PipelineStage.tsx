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
    title: "Query & Parse",
    desc: "Normalizes Indian statute aliases, bridges IPC/CrPC/IEA ↔ BNS/BNSS/BSA, and parses exact citation tuples.",
    detail: (
      <>
        <strong>Input:</strong> natural-language query, CNR, INSC, HC Neutral, AIR/SCC, or draft brief.<br />
        <strong>Parser:</strong> priority-ordered <code>nyaya/citations.py</code> (no bare first-number matching).<br />
        <strong>Expansion:</strong> pari-materia IPC↔BNS, CrPC↔BNSS, IEA↔BSA equivalence.<br />
        <strong>Embeddings:</strong> zero dense vectors or GPU dependencies in the v2.0 lexical pipeline.
      </>
    ),
  },
  {
    id: "pipe-retrieve",
    num: "02",
    title: "Tiered Retrieve",
    desc: "Deterministic RRF (k=60) across SQLite FTS5, LanceDB native BM25, DuckDB ZSTD Parquet, and on-demand S3 PDFs.",
    detail: (
      <>
        <strong>Baked Core:</strong> read-only SQLite3 FTS5 + concordance + invalidations + citation graph.<br />
        <strong>Bucket Warm/Cold:</strong> 3 LanceDB native Rust BM25 tables + 5 compacted DuckDB ZSTD Parquet layouts (256 MB / 10s cap).<br />
        <strong>Direct S3 PDFs:</strong> anonymous HTTPS reads from AWS Open Data via Apache-2.0 <code>pypdfium2</code> (15 MB / 500p cap + 256 MB zstd LRU).
      </>
    ),
  },
  {
    id: "pipe-verify",
    num: "03",
    title: "Verify & Frame",
    desc: "Attaches StatutoryValidity alerts, raw citation-graph edges, license filtering, and OWASP <corpus_text> isolation.",
    detail: (
      <>
        <strong>Validity:</strong> flags repealed (2024-07-01), struck-down (IT Act s.66A, IPC s.497), and abeyance provisions.<br />
        <strong>Precedent Graph:</strong> raw edges + coram comparison with mandatory <code>precedent_status=&quot;unverified_automated&quot;</code>.<br />
        <strong>OWASP LLM01:</strong> strips Unicode Tags (<code>U+E0000–U+E007F</code>), escapes breakout delimiters, enforces s. 228A / BNS s. 72 &amp; <code>commercial_safe_only</code>.
      </>
    ),
  },
  {
    id: "pipe-expose",
    num: "04",
    title: "Expose & Chat",
    desc: "Serves 12 MCP tools, 8 nyaya:// resources, and 4 prompts via uvx stdio or Streamable HTTP (/mcp), plus optional /chat.",
    detail: (
      <>
        <strong>Local stdio:</strong> <code>uvx nyaya-mcp</code> auto-fetches verified packs when <code>NYAYA_MANIFEST_URL</code> is configured (zero secrets).<br />
        <strong>Hosted HTTP:</strong> single <code>python:3.12-slim-bookworm</code> container on Railway (&lt;1 GB RAM, sleeps when idle).<br />
        <strong>Optional /chat:</strong> stateless FastAPI + LangGraph agent mounted only when <code>NVIDIA_API_KEY</code> is configured.
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
