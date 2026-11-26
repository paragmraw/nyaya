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
    desc: "Normalizes Indian statute aliases, bridges IPC/CrPC/IEA ↔ BNS/BNSS/BSA and ITAct1961 ↔ ITAct2025, and parses exact citation tuples.",
    detail: (
      <>
        <strong>Input:</strong> natural-language query, CNR, INSC, 25 HC Neutral codes, AIR/SCC, or draft brief.<br />
        <strong>Parser:</strong> priority-ordered <code>nyaya/citations.py</code> (no bare first-number matching).<br />
        <strong>Expansion:</strong> 148 criminal (IPC↔BNS, CrPC↔BNSS, IEA↔BSA) + 33 Income-tax 1961↔2025 concordances.<br />
        <strong>Retrieval mode:</strong> pure lexical BM25 by default, with opt-in hybrid BM25+vector RRF (<code>NYAYA_HYBRID_SEARCH=1</code>).
      </>
    ),
  },
  {
    id: "pipe-retrieve",
    num: "02",
    title: "Tiered Retrieve",
    desc: "Deterministic RRF (k=60) across SQLite FTS5, 5 LanceDB BM25 tables, DuckDB ZSTD Parquet, and on-demand S3 PDFs.",
    detail: (
      <>
        <strong>Baked Core:</strong> read-only SQLite3 FTS5 + 181 concordances + 58 CPC Orders + invalidations + citation graph + cause lists.<br />
        <strong>Bucket Warm/Cold:</strong> 5 LanceDB native Rust BM25 tables (<code>sc_chunks</code>, <code>hc_hot_chunks</code> [<code>hot_hc_limit=500,000</code>], <code>state_acts_regulators</code>, <code>tribunal_chunks</code>, <code>cad_debates</code>) + compacted DuckDB ZSTD Parquet (256 MB / 10s cap).<br />
        <strong>Direct S3 PDFs:</strong> anonymous HTTPS reads from AWS Open Data via Apache-2.0 <code>pypdfium2</code> (15 MB / 500p cap + 256 MB zstd LRU) with opt-in Apache-2.0 OCR (<code>NYAYA_ENABLE_OCR=1</code>).
      </>
    ),
  },
  {
    id: "pipe-verify",
    num: "03",
    title: "Verify & Frame",
    desc: "Attaches StatutoryValidity alerts, point-in-time as_of state, raw citation-graph edges, license filtering, and OWASP <corpus_text> isolation.",
    detail: (
      <>
        <strong>Validity:</strong> flags repealed (2024-07-01), struck-down (IT Act s.66A, IPC s.497), abeyance provisions, and point-in-time <code>as_of</code> reconstruction.<br />
        <strong>Precedent Graph:</strong> raw edges + coram comparison with mandatory <code>precedent_status=&quot;unverified_automated&quot;</code>.<br />
        <strong>OWASP LLM01:</strong> strips Unicode Tags (<code>U+E0000–U+E007F</code>), escapes breakout delimiters, enforces s. 228A / BNS s. 72 &amp; <code>commercial_safe_only</code>.
      </>
    ),
  },
  {
    id: "pipe-expose",
    num: "04",
    title: "Expose & Chat",
    desc: "Serves 15 MCP tools, 8 nyaya:// resources, and 4 prompts via uvx stdio or Streamable HTTP (/mcp), plus optional /chat.",
    detail: (
      <>
        <strong>Local stdio:</strong> <code>uvx nyaya-mcp</code> auto-fetches verified packs via <code>nyaya-mcp sync</code> when <code>NYAYA_MANIFEST_URL</code> is configured (zero secrets).<br />
        <strong>Hosted HTTP:</strong> single <code>python:3.12-slim-bookworm</code> container on Railway (&lt;1 GB RAM, sleeps when idle) + optional BYO live-docket adapter.<br />
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
