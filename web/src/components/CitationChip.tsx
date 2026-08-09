"use client";

import type { ChatCitation } from "@/lib";

// CitationChip: a small pill that links a [[act: X, ref: Y]] citation marker
// (emitted by the model per the system prompt) to the relevant corpus page.
// For v1 we link to the /corpus/ page; a future iteration can deep-link to the
// exact provision via the SPA's existing detail routes.
export default function CitationChip({ cite }: { cite: ChatCitation }) {
  const href = `/corpus/?act=${encodeURIComponent(cite.act)}&ref=${encodeURIComponent(cite.ref)}`;
  return (
    <a className="cite-chip" href={href} title={`${cite.act} · ${cite.ref}`}>
      <span className="dot" aria-hidden="true" />
      {cite.act} · {cite.ref}
    </a>
  );
}