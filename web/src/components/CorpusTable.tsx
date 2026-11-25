"use client";

import { useEffect, useMemo, useRef, useSyncExternalStore } from "react";
import { CURATED } from "@/lib";
import type { CorpusCategoryFilter, CorpusSourceStatus, CuratedRow } from "@/lib/types";
import {
  filterCorpusRows,
  getUrlQuery,
  getUrlQueryServer,
  parseViewParams,
  subscribeUrlQuery,
  writeUrlQuery,
} from "@/lib/url-query";

type ActLike = {
  short_name: string;
  as_of?: string | null;
};

type Row = {
  key: string;
  short_name: string;
  name: string;
  type: string;
  category: string;
  tier: string;
  coverage: string;
  license_spdx: string;
  commercial_safe: boolean;
  source: string;
  date: string;
  status: "live" | "beta" | "coming";
};

const CATEGORY_FILTERS: { key: CorpusCategoryFilter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "judiciary", label: "Judiciary" },
  { key: "legislation", label: "Legislation" },
  { key: "regulators", label: "Regulators" },
  { key: "tribunals", label: "Tribunals" },
  { key: "commercial_safe", label: "Commercial-Safe Only" },
];

const STATUS_FILTERS: { key: string; label: string }[] = [
  { key: "all", label: "All status" },
  { key: "live", label: "Live" },
  { key: "beta", label: "Beta" },
  { key: "coming", label: "Coming" },
];

const CATEGORY_LABELS: Record<string, string> = {
  judiciary: "Judiciary",
  legislation: "Legislation",
  regulators: "Regulators",
  tribunals: "Tribunals",
};

export type ResolvedSourceMetadata = {
  coverage: string;
  license_spdx: string;
  commercial_safe: boolean;
  date: string;
};

/** Index corpus sources by all known name/id aliases without assuming top-level name is set. */
export function indexSourcesByName(
  sources?: readonly CorpusSourceStatus[] | null,
): Map<string, CorpusSourceStatus> {
  const m = new Map<string, CorpusSourceStatus>();
  for (const s of sources ?? []) {
    if (!s) continue;
    const keys = [s.short_name, s.name, s.provenance?.source_name, s.source_id];
    for (const key of keys) {
      if (typeof key === "string" && key.length > 0) {
        m.set(key.toLowerCase(), s);
      }
    }
  }
  return m;
}

/** Resolve display metadata for a curated row from live source/act entries and curated fallbacks. */
export function resolveSourceMetadata(
  src: CorpusSourceStatus | undefined,
  curated: CuratedRow,
  act?: ActLike,
  snapshotDate?: string | null,
): ResolvedSourceMetadata {
  const date =
    (act?.as_of ??
      src?.as_of ??
      src?.provenance?.as_of ??
      curated.fallback_date ??
      (curated.status === "live" ? snapshotDate : "")) ||
    "N/A";
  return {
    coverage: src?.coverage ?? curated.coverage,
    license_spdx: src?.license_spdx ?? src?.provenance?.license_spdx ?? curated.license_spdx,
    commercial_safe: src?.commercial_safe ?? src?.provenance?.commercial_safe ?? curated.commercial_safe,
    date,
  };
}

// highlightAct: short_name (case-insensitive) of a row to highlight and scroll
// into view, driven by citation deep-links (/corpus/?act=IPC&ref=s.302).
export default function CorpusTable({
  acts,
  sources,
  snapshotDate,
  highlightAct,
}: {
  acts: ActLike[] | undefined;
  sources?: CorpusSourceStatus[] | undefined;
  snapshotDate?: string | null;
  highlightAct?: string;
}) {
  const query = useSyncExternalStore(subscribeUrlQuery, getUrlQuery, getUrlQueryServer);
  const view = useMemo(() => parseViewParams(query), [query]);
  const sortCol = view.sort;
  const sortDir = view.dir;
  const statusFilter = STATUS_FILTERS.some((f) => f.key === view.status) ? view.status : "all";
  const categoryFilter = CATEGORY_FILTERS.some((f) => f.key === view.category)
    ? (view.category as CorpusCategoryFilter)
    : "all";

  const actsByShort = useMemo(() => {
    const m = new Map<string, ActLike>();
    (acts ?? []).forEach((a) => {
      if (a?.short_name) m.set(String(a.short_name).toLowerCase(), a);
    });
    return m;
  }, [acts]);

  const sourcesByName = useMemo(() => indexSourcesByName(sources), [sources]);

  const rows: Row[] = useMemo(() => {
    return CURATED.map((c, i) => {
      const act = c.short_name ? actsByShort.get(c.short_name.toLowerCase()) : undefined;
      const src =
        (c.short_name ? sourcesByName.get(c.short_name.toLowerCase()) : undefined) ??
        (c.name ? sourcesByName.get(c.name.toLowerCase()) : undefined);
      const resolved = resolveSourceMetadata(src, c, act, snapshotDate);
      return {
        key: `${i}-${c.name}`,
        short_name: c.short_name ?? "",
        name: c.name,
        type: c.type,
        category: c.category,
        tier: c.tier,
        coverage: resolved.coverage,
        license_spdx: resolved.license_spdx,
        commercial_safe: resolved.commercial_safe,
        source: c.source,
        date: resolved.date,
        status: c.status,
      };
    });
  }, [actsByShort, sourcesByName, snapshotDate]);

  const filtered = useMemo(() => {
    const byCategory = filterCorpusRows(rows, categoryFilter);
    if (statusFilter === "all") return byCategory;
    return byCategory.filter((r) => r.status === statusFilter);
  }, [rows, categoryFilter, statusFilter]);

  const sorted = useMemo(() => {
    if (sortCol === -1) return filtered;
    const copy = [...filtered];
    copy.sort((a, b) => {
      const av = String([a.name, a.category, a.coverage, a.license_spdx, a.date, a.status][sortCol] ?? "").toLowerCase();
      const bv = String([b.name, b.category, b.coverage, b.license_spdx, b.date, b.status][sortCol] ?? "").toLowerCase();
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
  }, [highlightKey]);

  const headers = ["Source", "Category", "Coverage", "License", "Updated", "Status"];

  return (
    <>
      <div className="filter-controls">
        <div className="filter-bar" role="group" aria-label="Filter by category">
          {CATEGORY_FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              className={`filter-chip${categoryFilter === f.key ? " active" : ""}`}
              onClick={() => writeUrlQuery({ category: f.key })}
              aria-pressed={categoryFilter === f.key}
            >
              {f.label}
            </button>
          ))}
        </div>
        <div className="filter-bar filter-bar-status" role="group" aria-label="Filter by status">
          {STATUS_FILTERS.map((f) => (
            <button
              key={f.key}
              type="button"
              className={`filter-chip${statusFilter === f.key ? " active" : ""}`}
              onClick={() => writeUrlQuery({ status: f.key })}
              aria-pressed={statusFilter === f.key}
            >
              {f.label}
            </button>
          ))}
        </div>
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
                <td>
                  <span className="ct-name">{r.name}</span>
                  <span className="ct-sub">{r.tier} · {r.source}</span>
                </td>
                <td>{CATEGORY_LABELS[r.category] ?? r.type}</td>
                <td><span className="ct-cov">{r.coverage}</span></td>
                <td>
                  <div className="license-cell">
                    <span className="badge-license">{r.license_spdx}</span>
                    <span className={`badge-commercial ${r.commercial_safe ? "badge-safe" : "badge-nc"}`}>
                      {r.commercial_safe ? "Commercial-safe" : "Non-commercial"}
                    </span>
                  </div>
                </td>
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
