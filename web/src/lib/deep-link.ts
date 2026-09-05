// Parse a citation deep-link from the corpus page's query string.
//
// Citation chips across the app link to /corpus/?act=IPC&ref=s.302 (see
// CITE_HREF_PREFIX in chat.ts and CitationChip.tsx). The corpus page indexes
// SOURCES (acts), not individual provisions, so the deep-link resolves to the
// matching act row: the ref is carried along for the notice copy only.
// Pure and DOM-free so it can be unit-tested with node:test and used from a
// client component's useEffect (static export has no server-side search
// params — useSearchParams would drag the page into a Suspense boundary).

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