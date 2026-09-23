// Citation-marker parsing: [[act: X, ref: Y]] → markdown corpus links plus a
// de-duplicated citation list. Verbatim move from the old lib/chat.ts.

import type { ChatCitation } from "../api";

// Linear-time by construction (CodeQL js/polynomial-redos): every quantifier
// is BOUNDED, so no unbounded repetition exists for CodeQL's superlinear
// backtracking analysis to pump, and per-start-position cost is capped —
// total cost is O(bound × length). Two independent quadratic shapes are
// covered:
//   1. intra-marker: whitespace runs split across adjacent quantifiers
//      (fixed by disjoint classes; whitespace tolerance moved to .trim()).
//   2. inter-position (pump): the marker prefix "[[act:" recurs inside a
//      comma-free stream, and each occurrence re-ran the unbounded act class
//      to the end of the string before failing — quadratic even with (1)
//      fixed. Bounding the classes caps each failed attempt.
// Bounds are generous vs real markers (~30–60 chars): act ≤ 512, ref ≤ 2048,
// ≤ 16 whitespace chars between "," and "ref:".
export const CITE_RE = /\[\[act:([^,\]]{1,512}),\s{0,16}ref:([^\]]{1,2048})\]\]/g;

// Inline citations are rendered as normal markdown links whose href points
// back at the corpus page. That href prefix doubles as the citation marker:
// it is the only place ChatMessage.tsx needs to look to recognise a citation
// chip, and (unlike the old `title="ic"` attribute it replaces) it survives
// the markdown pipeline unambiguously and needs no special-cased title text.
// parseCitations is the only producer of such links.
export const CITE_HREF_PREFIX = "/corpus/?act=";

export function isCitationHref(href: string | undefined): boolean {
  return !!href && href.startsWith(CITE_HREF_PREFIX);
}

function citeToMarkdown(act: string, ref: string): string {
  const href = `/corpus/?act=${encodeURIComponent(act)}&ref=${encodeURIComponent(ref)}`;
  return `[${act} · ${ref}](${href})`;
}

// Convert the raw [[act: X, ref: Y]] markers emitted by the backend into
// markdown citation links (see CITE_HREF_PREFIX) plus a de-duplicated list of
// citation pairs. Exported for unit testing.
export function parseCitations(text: string): { text: string; citations: ChatCitation[] } {
  const citations: ChatCitation[] = [];
  const seen = new Set<string>();
  const re = new RegExp(CITE_RE);
  let m: RegExpExecArray | null;
  while ((m = re.exec(text)) !== null) {
    const act = m[1].trim();
    const ref = m[2].trim();
    const key = `${act}|${ref}`;
    if (!seen.has(key)) {
      seen.add(key);
      citations.push({ act, ref });
    }
  }
  // The whitespace collapse is horizontal-only and never touches line-leading
  // indentation: `\s{2,}` would destroy `\n\n` paragraph breaks, and a plain
  // `[ \t]{2,}` would still eat the leading 2+ spaces of a nested list item —
  // both mangle the markdown the bubble is about to render. Anchoring the run
  // to a preceding non-newline char collapses runs between words but keeps
  // line-leading indentation (the char before it is `\n` or start-of-text).
  const cleaned = text
    .replace(re, (_, act: string, ref: string) => citeToMarkdown(act.trim(), ref.trim()))
    .replace(/([^\n])[ \t]{2,}/g, "$1 ")
    .trim();
  return { text: cleaned, citations };
}

// Streaming-plain display: convert [[act: X, ref: Y]] markers to a compact
// plain-text chip ([X · Y]) — no markdown link conversion, no citations list,
// no whitespace collapse. The full parseCitations pass runs ONCE on the
// authoritative final text (the correction event or the post-done flush); the
// per-frame streaming path uses the incremental StreamingCitationStripper
// (chat/citation-stream.ts) instead of re-scanning the whole accumulator.
// Exported for unit testing.
export function stripCitationMarkers(text: string): string {
  return text.replace(CITE_RE, (_, act: string, ref: string) => `[${act.trim()} · ${ref.trim()}]`);
}