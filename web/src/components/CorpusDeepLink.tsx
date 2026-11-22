"use client";

// Deep-link landing + live SWR hydration for /corpus/:
// Citation chips link to /corpus/?act=IPC&ref=s.302 or /corpus/?act=SC&ref=2017%20INSC%20801.
// Resolves deep links against CURATED short_names and aliases, and merges live
// /api/acts and /api/corpus-status responses onto the build-time snapshot.

import { useMemo, useState, useSyncExternalStore } from "react";
import CorpusTable from "./CorpusTable";
import { CURATED, useActs, useCorpusStatus } from "@/lib";
import { parseCorpusDeepLink, resolveDeepLinkRow, type CorpusDeepLink } from "@/lib/deep-link";

type ActLike = {
  short_name: string;
} & Record<string, unknown>;

const subscribeNoop = () => () => {};
const getSearch = () => window.location.search;
const getSearchServer = () => "";

export default function CorpusDeepLink({ acts }: { acts: ActLike[] | undefined }) {
  const [dismissed, setDismissed] = useState(false);
  const { data: liveStatus } = useCorpusStatus();
  const { data: liveActs } = useActs({ limit: 200 });

  const search = useSyncExternalStore(subscribeNoop, getSearch, getSearchServer);
  const link = useMemo<CorpusDeepLink | null>(
    () => parseCorpusDeepLink(search),
    [search],
  );
  const matched = useMemo<string | null>(() => {
    if (!link) return null;
    return resolveDeepLinkRow(link.act, CURATED);
  }, [link]);

  const effectiveActs = useMemo(() => {
    if (liveActs?.items && liveActs.items.length > 0) {
      return liveActs.items;
    }
    return acts;
  }, [liveActs, acts]);

  const notice = (() => {
    if (!link || dismissed) return null;
    if (matched) {
      return (
        <div role="status" className="deep-link-notice" style={noticeStyle}>
          <span>
            Cited source <strong>{link.ref ? `${matched} · ${link.ref}` : matched}</strong> —
            highlighted among the indexed sources below. Provision and judgment text is
            retrieved via MCP or the chat API.
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
          No curated table row directly matches <strong>{link.act}</strong>. Use the category filters below
          or query the source live through MCP (<code>get_provision</code> / <code>get_judgment</code>).
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
      <CorpusTable
        acts={effectiveActs}
        sources={liveStatus?.sources}
        snapshotDate={liveStatus?.as_of}
        highlightAct={matched ?? undefined}
      />
    </>
  );
}

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
