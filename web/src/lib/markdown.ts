// Markdown normalisation for streamed chat content.
//
// Extracted from components/ChatMessage.tsx (Task 10, item 1): the streamed
// token path re-runs this on every render, so it lives in a plain module with
// an input-keyed memo cache — re-renders that pass the same accumulated text
// (tool-chip clicks, toggles, sibling updates) cost a Map lookup, and the
// O(n) normalisation runs only when the text actually grew.

// Memoize normaliseMd by exact input. The cache is bounded by total cached
// bytes (not entry count): during a long stream every flush produces a new,
// longer key, so an entry-count cap would end up holding the N largest
// accumulated prefixes of the answer (~copies of the whole text). A byte
// budget evicts the oldest entries FIFO until the new entry fits, keeping the
// most recent text (the one live re-renders hit) without the O(answer × cap)
// transient memory.
const NORMALISE_CACHE_CAP_BYTES = 512 * 1024;
const normaliseCache = new Map<string, string>();
let normaliseCacheBytes = 0;

// Normalise streamed markdown so block-level constructs (headings, blockquotes,
// lists, tables, code fences) are recognised by CommonMark even when the model
// emits them without the required preceding blank line. Also collapses 3+
// newlines to a paragraph break, and splits jammed-together block elements
// (e.g. "### Heading- list item") onto separate lines so they parse correctly.
// Exported for unit testing (see tests/normalise-md.test.ts).
export function normaliseMd(src: string): string {
  if (!src) return src;
  const cached = normaliseCache.get(src);
  if (cached !== undefined) return cached;

  const result = normaliseMdUncached(src);
  const cost = src.length + result.length;
  if (cost <= NORMALISE_CACHE_CAP_BYTES) {
    // Map iterates in insertion order — evict oldest (FIFO) until the new
    // entry fits within the byte budget.
    while (normaliseCache.size > 0 && normaliseCacheBytes + cost > NORMALISE_CACHE_CAP_BYTES) {
      const oldest = normaliseCache.keys().next().value;
      if (oldest === undefined) break;
      normaliseCacheBytes -= oldest.length + (normaliseCache.get(oldest)?.length ?? 0);
      normaliseCache.delete(oldest);
    }
    normaliseCacheBytes += cost;
    normaliseCache.set(src, result);
  }
  return result;
}

function normaliseMdUncached(src: string): string {
  // Collapse runs of 3+ newlines to exactly two (a single blank line).
  let s = src.replace(/\n{3,}/g, "\n\n");

  // Fix jammed emphasis markers: when the model emits "**text1****text2**",
  // the inner "****" is a closing-then-opening bold delimiter with no space.
  // Insert a space so it becomes "**text1** **text2**" and both parse as bold.
  // Only matches exactly 4 * (not 6+) to avoid touching *** (bold+italic).
  s = s.replace(/\*\*\*\*(?!\*)/g, "** **");
  s = s.replace(/____(?!_)/g, "__ __");

  // Normalise malformed ATX headings the model emits as "##3." (no space
  // between the hashes and the number) → "## 3." so CommonMark recognises
  // them as headings rather than leaving them glued to surrounding text.
  // Only fires when the digits are immediately followed by "." (a heading
  // number like "##1.", "##3."), avoiding false positives like "#1" in prose.
  s = s.replace(/(#{1,6})(\d+\.)/g, "$1 $2");

  // Split jammed-together block elements that the model emits on a single line.
  // The model frequently emits headings, list items, table rows, blockquotes,
  // and horizontal rules all on the same line without newline separators.
  //
  // We insert a newline before any block-start marker that appears mid-line.
  // This runs BEFORE the line-based blank-line insertion below.
  //
  // Patterns to split (insert \n before the marker):
  //  1. "text ### Heading"  /  "text - item"  /  "text > quote"  (whitespace before marker)
  //     NOTE: Only - and * as list markers after whitespace, NOT + (which
  //     appears in regular text like "imprisonment + fine").
  //  2. "word- Capital"  /  "word.- Capital"  /  "word.- **Bold"  (list marker jammed after word/punct)
  //  3. "**bold**| table"  /  "text)| table"  /  "**bold**- list"  /  "**bold**1. ordered"
  //  4. "text.> quote"  /  "text)> quote"  (blockquote jammed after punctuation, no space)
  //  5. "text.---"  /  "text ---"  (horizontal rule jammed after text)
  s = s.replace(/(\s)(#{1,6}\s|>\s?|[-*]\s)/g, "$1\n$2");
  s = s.replace(/([a-zA-Z.,\)])(- (?:[A-Z*]|\*\*))/g, "$1\n$2");
  s = s.replace(/(\*\*)(\|)/g, "$1\n$2");
  s = s.replace(/(\*\*)(- (?:[A-Z*]|\*\*))/g, "$1\n$2");
  s = s.replace(/(\*\*)(\d+\.\s)/g, "$1\n$2");
  // Split | from any preceding non-whitespace, non-pipe character (table row start).
  // The regex requires at least one | in the captured row and the text before
  // the first | must NOT be only dashes/colons (which would be a GFM separator
  // row like |---|---|). We use a negative-lookahead-ish approach: match a
  // non-pipe/non-space char followed by |, but only if what follows the | is
  // not just dashes and pipes (i.e., it's a real data row with text).
  // To keep it simple and avoid breaking separator rows, we only split when
  // the character before | is NOT a dash or colon.
  s = s.replace(/([a-zA-Z0-9\)\.’“”"'])\|/g, "$1\n|");
  // Split > from preceding punctuation (blockquote after sentence end)
  s = s.replace(/([.,\)])(>)/g, "$1\n$2");
  // Split --- (horizontal rule) from preceding text on the same line.
  // Only match when --- is at the start of what looks like a standalone
  // horizontal rule (preceded by whitespace or sentence-ending punctuation),
  // NOT when --- is part of a GFM table separator row (|---|---|).
  s = s.replace(/([.,])(---)/g, "$1\n$2");
  s = s.replace(/(\s)(---\s*$)/g, "$1\n$2");
  // Split ATX headings from following text on the same line. The model emits
  // "### Heading titleParagraph text..." with no newline after the title.
  // We detect a lowercase letter immediately followed by an uppercase letter
  // within a heading line and split there (e.g. "saysSection" → "says\nSection").
  // This runs after the earlier heading split, so it only applies to lines
  // that already start with ###.
  s = s.replace(/(#{1,6} .+?[a-z])([A-Z][a-z])/g, "$1\n$2");

  // Shared GFM table-row detectors (used by the header-repair pass below and
  // the blank-line inserter after it).
  //  - separator row: a line of |, -, :, and spaces only (|---|---|…)
  //  - table data row: a line containing | that starts and ends with |
  const tableSeparator = /^\s*\|?\s*:?-{2,}(:?\s*\|\s*:?-{2,})*:?\s*\|?\s*$/;
  const tableDataRow = /^\s*\|.*\|\s*$/;

  // Repair GFM table headers jammed onto the same line as preceding text
  // (commonly a heading or sentence-end). The model frequently emits:
  //   "## 3. Key terms (…) | Term | Explanation |"
  // with the table header glued to the heading, and the separator on the NEXT
  // line. GFM requires the header row and separator on consecutive lines, so
  // we split the current line before its first `|` to give the header its own
  // line. We only split when the text before the first `|` looks like a heading
  // (contains #) or a sentence (contains whitespace and ends with sentence
  // punctuation), so valid no-leading-pipe header rows like "Score | Result"
  // are left intact.
  {
    const lines = s.split("\n");
    for (let i = 0; i + 1 < lines.length; i++) {
      if (!tableSeparator.test(lines[i + 1].trim())) continue;
      const pipeIdx = lines[i].indexOf("|");
      if (pipeIdx <= 0) continue; // already starts with |, or no pipe at all
      const before = lines[i].slice(0, pipeIdx).trim();
      if (!before) continue;
      const looksLikeHeadingOrSentence =
        /#/.test(before) ||
        (/\s/.test(before) && /[\])!?:."']$/.test(before));
      if (!looksLikeHeadingOrSentence) continue;
      lines.splice(i, 1, before, lines[i].slice(pipeIdx));
    }
    s = lines.join("\n");
  }

  // Ensure a blank line before block-start markers when they follow text on
  // the previous line. Matches: ATX headings (#…), blockquotes (>), unordered
  // list items (- * +), ordered list items (1.), fenced code (```), tables
  // (a line starting & ending with |), and horizontal rules (--- / *** / ___).
  //
  // EXCEPTION: GFM table rows must NOT be separated from each other — GFM
  // requires the header, separator, and data rows on consecutive lines.
  const blockStart =
    /^(#{1,6}\s|>\s?|[-*+]\s|\d+[.)]\s|```| {4,}|\|.*\|\s*$|([-*_]\s?){3,}$)/;
  const lines = s.split("\n");
  const out: string[] = [];
  for (let i = 0; i < lines.length; i++) {
    const cur = lines[i];
    const prev = out[out.length - 1];
    // Insert a blank line before a block-start marker, but not between
    // consecutive GFM table rows (header, separator, data) — those must
    // stay on consecutive lines for GFM to recognise the table.
    const isTableSep = tableSeparator.test(cur.trim());
    const isTableData = tableDataRow.test(cur.trim());
    const prevIsTable = prev !== undefined && (tableDataRow.test(prev.trim()) || tableSeparator.test(prev.trim()));
    const isTableLine = (isTableSep || isTableData) && prevIsTable;
    if (
      prev !== undefined && prev.trim() !== "" && cur.trim() !== "" &&
      blockStart.test(cur) && !isTableLine
    ) {
      out.push(""); // insert blank line separator
    }
    out.push(cur);
  }
  return out.join("\n");
}
