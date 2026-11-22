"use client";

import StatCard from "./StatCard";
import { useCorpusStatus } from "@/lib";
import { formatCompactCount, formatNumber } from "@/lib/api";

export default function CorpusStatsGrid() {
  const { data } = useCorpusStatus();
  const counts = data?.counts;

  return (
    <div className="stat-grid">
      <StatCard
        num={counts?.sc_judgments}
        label="Supreme Court Judgments"
        source="Dattam Labs AWS Open Data + Vaquill (CC-BY-4.0)"
        prefix="~"
      />
      <StatCard
        num={counts?.hc_judgments}
        label={`High Court Orders (${formatCompactCount(counts?.hc_judgments)})`}
        source="25 High Courts / 45 Benches · DuckDB ZSTD Parquet (CC-BY-4.0)"
      />
      <StatCard
        num={counts?.tribunal_orders}
        label={`Tribunal Matters (${counts?.tribunals ?? 15} Forums)`}
        source="ITAT, NCLT, NGT, SAT, CAT, CESTAT… (CC-BY-4.0)"
      />
      <StatCard
        num={counts?.district_court_records}
        label={`District Court Metadata (${formatCompactCount(counts?.district_court_records)})`}
        source="DDL 2010–2018 · Non-Commercial (CC-BY-NC-SA-4.0)"
      />
      <StatCard
        num={counts?.legislation_jurisdictions}
        label="Legislation Jurisdictions"
        source={`${formatNumber(counts?.central_acts)} Central + ${formatNumber(counts?.state_acts)} State/UT Acts (CC-BY-4.0)`}
      />
      <StatCard
        num={counts?.regulators}
        label="Financial & Sectoral Regulators"
        source="554k+ provisions: SEBI, RBI, CBIC, MCA, TRAI, IRDAI…"
      />
      <StatCard
        num={counts?.articles}
        label="Constitution Articles"
        source={`${formatNumber(counts?.schedules)} Schedules · Amendments 1–${counts?.amendments ?? 106}`}
      />
      <StatCard
        num={counts?.citation_edges}
        label="Citation Graph Edges"
        source={`${formatNumber(counts?.concordance_mappings)} IPC↔BNS maps · ${formatNumber(counts?.judicial_invalidations)} invalidations`}
      />
    </div>
  );
}
