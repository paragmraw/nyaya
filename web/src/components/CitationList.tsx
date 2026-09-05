"use client";

// The "Citations:" chip list at the foot of a grounded assistant answer.
// Extracted from ChatMessage.tsx; chips deep-link to the corpus page
// (/corpus/?act=…&ref=…), which resolves to the highlighted act row.

import CitationChip from "./CitationChip";
import type { ChatCitation } from "@/lib/api";

export default function CitationList({ citations }: { citations: ChatCitation[] }) {
  if (citations.length === 0) return null;
  return (
    <div className="cite">
      <strong>Citations:</strong>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 4, marginTop: 4 }}>
        {citations.map((c, i) => (
          <CitationChip key={`${c.act}-${c.ref}-${i}`} cite={c} />
        ))}
      </div>
    </div>
  );
}