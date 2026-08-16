"use client";

import dynamic from "next/dynamic";

const CitationAnatomy = dynamic(() => import("@/components/CitationAnatomy").then((mod) => mod.default), {
  ssr: false,
  loading: () => <p className="meta">Loading citation anatomy…</p>,
});

export { CitationAnatomy };