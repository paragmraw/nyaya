"use client";

import dynamic from "next/dynamic";

const CorpusTable = dynamic(() => import("@/components/CorpusTable").then((mod) => mod.default), {
  ssr: false,
  loading: () => <p className="meta">Loading corpus table…</p>,
});

export { CorpusTable };