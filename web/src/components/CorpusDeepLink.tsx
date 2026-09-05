"use client";

// Deep-link landing for citation chips: /corpus/?act=IPC&ref=s.302.
//
// The corpus table's rows come from the curated sources list, not from the
// live acts snapshot, so a deep link is resolved against CURATED short_names
// (case-insensitive) and can only highlight the act's row — provision text
// itself is retrieved live through the chat API, never listed on this page.
// The query string is read from window.location.search inside useEffect:
// static export has no server-rendered search params, and useSearchParams
// would drag the page into a Suspense boundary.

import { useMemo, useState, useSyncExternalStore } from "react";
import CorpusTable from "./CorpusTable";
import { CURATED } from "@/lib";
import { parseCorpusDeepLink, type CorpusDeepLink } from "@/lib/deep-link";

type ActLike = {
  short_name: string;
  // Other snapshot fields pass through untouched; this wrapper never reads them.
} & Record<string, unknown>;

// The query string never changes without a navigation (which remounts the
// page), so the store subscription is a no-op; the server snapshot is the
// empty string, meaning "no deep link" — matching the static build output.
const subscribeNoop = () => () => {};
const getSearch = () => window.location.search;
const getSearchServer = () => "";

export default function CorpusDeepLink({ acts }: { acts: ActLike[] | undefined }) {
  const [dismissed, setDismissed] = useState(false);

  const search = useSyncExternalStore(subscribeNoop, getSearch, getSearchServer);
  const link = useMemo<CorpusDeepLink | null>(
    () => parseCorpusDeepLink(search),
    [search],
  );
  const matched = useMemo<string | null>(() => {
    if (!link) return null;
    const hit = CURATED.find(
      (c) => c.short_name.toLowerCase() === link.act.toLowerCase(),
    );
    return hit ? hit.short_name : null;
  }, [link]);

  const notice = (() => {
    if (!link || dismissed) return null;
    if (matched) {
      return (
        <div role="status" className="deep-link-notice" style={noticeStyle}>
          <span>
            Cited source <strong>{link.ref ? `${matched} ${link.ref}` : matched}</strong> —
            highlighted among the indexed sources below. Provision text is
            retrieved live through the chat API, not listed on this page.
          </span>
          <button type="button" style={closeStyle} onClick={() => setDismissed(true)} aria-label="Dismiss">
            ×
          </button>
        </div>
      );
    }
    return (
      <div role="status" className="deep-link-notice" style={noticeStyle}>
        <span>
          No indexed source matches <strong>{link.act}</strong>. The available
          sources are listed below; provision text is retrieved live through
          the chat API.
        </span>
        <button type="button" style={closeStyle} onClick={() => setDismissed(true)} aria-label="Dismiss">
          ×
        </button>
      </div>
    );
  })();

  return (
    <>
      {notice}
      <CorpusTable acts={acts} highlightAct={matched ?? undefined} />
    </>
  );
}

// Inline styles keep globals.css untouched (a concurrent refactor owns it).
// Tokens only, so both themes keep working.
const noticeStyle: React.CSSProperties = {
  display: "flex",
  alignItems: "flex-start",
  gap: 8,
  padding: "10px 12px",
  marginBottom: 12,
  borderRadius: 8,
  border: "1px solid var(--accent)",
  background: "color-mix(in oklch, var(--accent) 10%, transparent)",
  fontSize: "0.9em",
};

const closeStyle: React.CSSProperties = {
  marginLeft: "auto",
  border: "none",
  background: "none",
  color: "var(--fg-muted, inherit)",
  fontSize: "1.1em",
  lineHeight: 1,
  cursor: "pointer",
  padding: "0 2px",
};