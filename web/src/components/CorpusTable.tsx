"use client";

import { useMemo, useState } from "react";
import type { Act } from "@/lib/api";
import { CURATED } from "@/lib";

// Curated metadata for the corpus table. The design ships these rows with
// status chips and coverage descriptions; we map live rows from /api/acts
// onto this metadata by short_name. Acts not in the curated list still
// render with a default "Live" status and the act's as_of date. Non-act rows
// (judgments, legislation) use a fallback date from the design export.

type Row = {
  key: string;
  name: string;
  type: string;
  coverage: string;
  date: string;
  status: "live" | "beta" | "coming";
};

const STATUS_FILTERS = ["all", "live", "beta", "coming"] as const;
type StatusFilter = (typeof STATUS_FILTERS)[number];

export default function CorpusTable({ acts }: { acts: Act[] | undefined }) {
  // -1 means "unsorted" (curated order); clicking a header sets sortCol.
  const [sortCol, setSortCol] = useState<number>(-1);
  const [sortDir, setSortDir] = useState<"asc" | "desc">("asc");
  const [filter, setFilter] = useState<StatusFilter>("all");

  const actsByShort = useMemo(() => {
    const m = new Map<string, Act>();
    (acts ?? []).forEach((a) => m.set(a.short_name, a));
    return m;
  }, [acts]);

  const rows: Row[] = useMemo(() => {
    return CURATED.map((c, i) => {
      const act = c.short_name ? actsByShort.get(c.short_name) : undefined;
      const date = (act?.as_of ?? c.fallback_date) || "N/A";
      return { key: `${i}-${c.name}`, name: c.name, type: c.type, coverage: c.coverage, date, status: c.status };
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
      setSortDir(sortDir === "asc" ? "desc" : "asc");
    } else {
      setSortCol(col);
      setSortDir("asc");
    }
  };

  const headers = ["Source", "Type", "Coverage", "Last refreshed", "Status"];

  return (
    <>
      <div className="filter-bar" role="tablist" aria-label="Filter by status">
        {STATUS_FILTERS.map((f) => (
          <button
            key={f}
            className={`filter-chip${filter === f ? " active" : ""}`}
            onClick={() => setFilter(f)}
            role="tab"
            aria-selected={filter === f}
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
                  onClick={() => onHeaderClick(i)}
                >
                  {h}
                  <span className="sort-arrow" aria-hidden="true">▾</span>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {sorted.map((r) => (
              <tr key={r.key}>
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