// Query-string as component state: shareable view state (corpus sort/filter)
// lives in the URL, written with history.replaceState so navigating back and
// bookmarking preserve the view without polluting history.
//
// replaceState/pushState fire no event, so a useSyncExternalStore store needs
// its own notification: the history methods are wrapped at import time (this
// module is only imported from client components) to notify subscribers, and
// popstate is forwarded too. Components then DERIVE their view from the
// snapshot instead of syncing state in an effect (the set-state-in-effect
// anti-pattern).
//
// parse/serialize helpers are pure and node-testable; the window patch is kept
// as thin as possible.

export type ViewParams = {
  sort: number; // column index, -1 = unsorted (curated order)
  dir: "asc" | "desc";
  status: string; // filter key, "all" default
};

export function parseViewParams(search: string): ViewParams {
  const defaults: ViewParams = { sort: -1, dir: "asc", status: "all" };
  if (!search) return defaults;
  let params: URLSearchParams;
  try {
    params = new URLSearchParams(search.startsWith("?") ? search.slice(1) : search);
  } catch {
    return defaults;
  }
  const sortRaw = params.get("sort");
  const sort = sortRaw === null ? -1 : Number.parseInt(sortRaw, 10);
  const dir = params.get("dir");
  const status = params.get("status") || "all";
  return {
    // Out-of-range or non-numeric sort falls back to curated order rather
    // than crashing a hand-edited URL.
    sort: Number.isInteger(sort) && sort >= 0 && sort <= 4 ? sort : -1,
    dir: dir === "desc" ? "desc" : "asc",
    status,
  };
}

// Merge a patch into the params carried by `search` and serialize the new
// query string (without the leading "?"). Patches with default values REMOVE
// their keys, so a shared link stays minimal and clearing a filter cleans the
// URL.
export function serializeViewParams(
  search: string,
  patch: Partial<Omit<ViewParams, "sort"> & { sort?: number }>,
): string {
  const current = parseViewParams(search);
  const next: ViewParams = { ...current, ...patch };
  const params = new URLSearchParams();
  if (next.sort >= 0 && next.sort <= 4) {
    params.set("sort", String(next.sort));
    params.set("dir", next.dir);
  }
  if (next.status && next.status !== "all") params.set("status", next.status);
  return params.toString();
}

// ─── The store ────────────────────────────────────────────────────
// Client-only: components using this must render inside a client bundle that
// never evaluates the module during SSR. The getters guard anyway so a stray
// server evaluation degrades to an empty query instead of crashing.

let snapshot = "";
const listeners = new Set<() => void>();

function refreshSnapshot() {
  try {
    snapshot = globalThis.window ? window.location.search : "";
  } catch {
    snapshot = "";
  }
  listeners.forEach((notify) => notify());
}

if (typeof globalThis.window !== "undefined") {
  snapshot = window.location.search;
  // Wrap the SPA-relevant history methods so replaceState (this module's own
  // writes) and pushState notify subscribers. popstate covers back/forward.
  const origReplace = window.history.replaceState.bind(window.history);
  const origPush = window.history.pushState.bind(window.history);
  window.history.replaceState = (...args: Parameters<typeof origReplace>) => {
    origReplace(...args);
    refreshSnapshot();
  };
  window.history.pushState = (...args: Parameters<typeof origPush>) => {
    origPush(...args);
    refreshSnapshot();
  };
  window.addEventListener("popstate", refreshSnapshot);
}

export function subscribeUrlQuery(notify: () => void): () => void {
  listeners.add(notify);
  return () => listeners.delete(notify);
}

export function getUrlQuery(): string {
  try {
    return globalThis.window ? window.location.search : snapshot;
  } catch {
    return "";
  }
}

export function getUrlQueryServer(): string {
  return "";
}

// Write a patch to the URL (replaceState — no history entry) preserving the
// path and hash.
export function writeUrlQuery(patch: Partial<Omit<ViewParams, "sort"> & { sort?: number }>) {
  try {
    if (!globalThis.window) return;
    const query = serializeViewParams(window.location.search, patch);
    const hash = window.location.hash;
    window.history.replaceState(
      window.history.state,
      "",
      `${window.location.pathname}${query ? `?${query}` : ""}${hash}`,
    );
  } catch {
    /* navigation history unavailable (e.g. sandboxed frame) — view still works */
  }
}