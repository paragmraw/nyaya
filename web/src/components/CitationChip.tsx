"use client";

import type { ChatCitation } from "@/lib";

// CitationChip: a small pill that links a citation to the relevant corpus page.
// Citations may come from the structured-output synthesis node (with an optional
// quote) or from inline [[act: X, ref: Y]] markers parsed by the frontend.
export default function CitationChip({ cite }: { cite: ChatCitation }) {
  const href = `/corpus/?act=${encodeURIComponent(cite.act)}&ref=${encodeURIComponent(cite.ref)}`;
  const title = cite.quote || `${cite.act} · ${cite.ref}`;
  return (
    <a className="cite-chip" href={href} title={title}>
      <span className="dot" aria-hidden="true" />
      {cite.act} · {cite.ref}
    </a>
  );
}