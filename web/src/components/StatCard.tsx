"use client";

import { formatNumber } from "@/lib/api";

type Props = {
  num: number | null | undefined;
  label: string;
  source: string;
  prefix?: string; // e.g. "~" for approximate
};

export default function StatCard({ num, label, source, prefix = "" }: Props) {
  return (
    <div className="stat-card">
      <div className="sc-num num">
        {num == null ? "—" : `${prefix}${formatNumber(num)}`}
      </div>
      <div className="sc-lbl">{label}</div>
      <div className="sc-src">{source}</div>
    </div>
  );
}