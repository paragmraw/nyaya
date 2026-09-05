"use client";

import { useEffect, useMemo, useRef, useSyncExternalStore } from "react";
import { CURATED } from "@/lib";
import {
  getUrlQuery,
  getUrlQueryServer,
  parseViewParams,
  subscribeUrlQuery,
  writeUrlQuery,
} from "@/lib/url-query";

// Curated metadata for the corpus table. The design ships these rows with
// status chips and coverage descriptions; we map live rows from /api/acts
// onto this metadata by short_name. Acts not in the curated list still
// render with a default "Live" status and the act's as_of date. Non-act rows
// (judgments, legislation) use a fallback date from the design export.

type ActLike = {
  short_name: string;
  as_of?: string | null;
};

type Row = {
  key: string;
  short_name: string;
  name: string;
  type: string;
  coverage: string;
  date: string;
  status: "live" | "beta" | "coming";
};

const STATUS_FILTERS = ["all", "live", "beta", "coming"] as const;
type StatusFilter = (typeof STATUS_FILTERS)[number];

// highlightAct: short_name (case-insensitive) of a row to highlight and scroll
// into view — driven by citation deep-links (/corpus/?act=IPC&ref=s.302).
export default function CorpusTable({
  acts,
  highlightAct,
}: {
  acts: ActLike[] | undefined;
  highlightAct?: string;
}) {
  // Sort/filter live in the URL (history.replaceState) so a configured view is
  // shareable and survives back/forward. The URL is the source of truth — the
  // view is DERIVED from the query snapshot rather than synced in an effect;
  // clicks write a patch and the store notifies, re-rendering.
  const query = useSyncExternalStore(subscribeUrlQuery, getUrlQuery, getUrlQueryServer);
  const view = useMemo(() => parseViewParams(query), [query]);
  const sortCol = view.sort;
  const sortDir = view.dir;
  const filter = (STATUS_FILTERS as readonly string[]).includes(view.status)
    ? (view.status as StatusFilter)
    : "all";

  const actsByShort = useMemo(() => {
    const m = new Map<string, ActLike>();
    (acts ?? []).forEach((a) => m.set(a.short_name, a));
    return m;
  }, [acts]);

  const rows: Row[] = useMemo(() => {
    return CURATED.map((c, i) => {
      const act = c.short_name ? actsByShort.get(c.short_name) : undefined;
      const date = (act?.as_of ?? c.fallback_date) || "N/A";
      return { key: `${i}-${c.name}`, short_name: c.short_name ?? "", name: c.name, type: c.type, coverage: c.coverage, date, status: c.status };
    });
  }, [actsByShort]);

  const filtered = useMemo(() => {
    if (filter === "all") return rows;
    return rows.filter((r) => r.status === filter);
  }, [rows, filter]);

  const sorted = useMemo(() => {
    if (sortCol === -1) return filtered; // unsorted: curated order
    const copy = [...filtered];
    copy.sort((a, b) => {
      const av = [a.name, a.type, a.coverage, a.date, a.status][sortCol]?.toLowerCase() ?? "";
      const bv = [b.name, b.type, b.coverage, b.date, b.status][sortCol]?.toLowerCase() ?? "";
      if (av < bv) return sortDir === "asc" ? -1 : 1;
      if (av > bv) return sortDir === "asc" ? 1 : -1;
      return 0;
    });
    return copy;
  }, [filtered, sortCol, sortDir]);

  const onHeaderClick = (col: number) => {
    if (sortCol === col) {
      writeUrlQuery({ dir: sortDir === "asc" ? "desc" : "asc" });
    } else {
      writeUrlQuery({ sort: col, dir: "asc" });
    }
  };

  // Deep-link highlight: resolve the act's row key, then scroll it into view
  // once. Instant scroll under prefers-reduced-motion (smooth otherwise).
  const highlightKey = useMemo(() => {
    if (!highlightAct) return null;
    const needle = highlightAct.toLowerCase();
    return rows.find((r) => r.short_name.toLowerCase() === needle)?.key ?? null;
  }, [rows, highlightAct]);
  const highlightRowRef = useRef<HTMLTableRowElement | null>(null);
  useEffect(() => {
    if (!highlightKey) return;
    highlightRowRef.current?.scrollIntoView({
      behavior: window.matchMedia("(prefers-reduced-motion: reduce)").matches
        ? "auto"
        : "smooth",
      block: "center",
    });
    // Rows are static (only sort/filter reorder them); run on highlight change.
  }, [highlightKey]);

  const headers = ["Source", "Type", "Coverage", "Last refreshed", "Status"];

  return (
    <>
      {/* Toggle group, not tabs: active state is exposed via aria-pressed, and
          buttons are natively keyboard-operable (Enter / Space). */}
      <div className="filter-bar" role="group" aria-label="Filter by status">
        {STATUS_FILTERS.map((f) => (
          <button
            key={f}
            type="button"
            className={`filter-chip${filter === f ? " active" : ""}`}
            onClick={() => writeUrlQuery({ status: f })}
            aria-pressed={filter === f}
          >
            {f}
          </button>
        ))}
      </div>
      <div className="corpus-wrap" style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-lg)", background: "var(--surface)" }}>
        <table className="corpus-table">
          <thead>
            <tr>
              {headers.map((h, i) => (
                <th
                  key={h}
                  className={sortCol === i ? `sorted-${sortDir}` : ""}
                  aria-sort={sortCol === i ? (sortDir === "asc" ? "ascending" : "descending") : "none"}
                >
                  {/* Real button inside the header cell: keyboard operable and
                      focusable by default (Enter/Space activate it), unlike a
                      click-only th. */}
                  <button
                    type="button"
                    className="th-sort"
                    onClick={() => onHeaderClick(i)}
                    aria-label={`Sort by ${h}${sortCol === i ? (sortDir === "asc" ? ", currently ascending" : ", currently descending") : ""}`}
                  >
                    {h}
                    <span className="sort-arrow" aria-hidden="true">▾</span>
                  </button>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => (
              <tr
                key={r.key}
                ref={r.key === highlightKey ? highlightRowRef : undefined}
                style={
                  r.key === highlightKey
                    ? {
                        background: "color-mix(in oklch, var(--accent) 12%, transparent)",
                        scrollMarginTop: "5rem",
                      }
                    : undefined
                }
              >
                <td><span className="ct-name">{r.name}</span></td>
                <td>{r.type}</td>
                <td><span className="ct-cov">{r.coverage}</span></td>
                <td><span className="ct-date">{r.date}</span></td>
                <td>
                  <span className={`chip chip-${r.status}`}>
                    {r.status === "live" ? "Live" : r.status === "beta" ? "Beta" : "Coming"}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}