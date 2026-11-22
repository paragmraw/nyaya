// Parse a citation deep-link from the corpus page's query string.
//
// Citation chips across the app link to /corpus/?act=IPC&ref=s.302 or
// /corpus/?act=SC&ref=2017%20INSC%20801 (see CITE_HREF_PREFIX in
// chat/citations.ts and CitationChip.tsx). The corpus page indexes sources
// rather than individual provisions, so the deep-link resolves to the
// matching source row via short_name or aliases.
// Pure and DOM-free so it can be unit-tested with node:test.

export type CorpusDeepLink = {
  act: string;
  ref: string | null;
};

export function parseCorpusDeepLink(search: string): CorpusDeepLink | null {
  if (!search) return null;
  let params: URLSearchParams;
  try {
    params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  } catch {
    return null;
  }
  const act = (params.get("act") || "").trim();
  if (!act) return null;
  const ref = (params.get("ref") || "").trim();
  return { act, ref: ref || null };
}

export function resolveDeepLinkRow(
  act: string,
  rows: readonly { short_name: string; aliases?: readonly string[] }[],
): string | null {
  const needle = act.trim().toLowerCase();
  if (!needle) return null;
  for (const row of rows) {
    if (row.short_name && row.short_name.toLowerCase() === needle) {
      return row.short_name;
    }
  }
  for (const row of rows) {
    if (row.short_name && row.aliases?.some((a) => a.toLowerCase() === needle)) {
      return row.short_name;
    }
  }
  return null;
}
