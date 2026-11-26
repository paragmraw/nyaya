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
      />
      <StatCard
        num={counts?.hc_judgments}
        label={`High Court Orders (${formatCompactCount(counts?.hc_judgments)})`}
        source="25 High Courts / 45 Benches · DuckDB ZSTD Parquet (CC-BY-4.0)"
      />
      <StatCard
        num={counts?.tribunal_orders}
        label={`Tribunal Matters (${counts?.tribunals ?? 24} Forums)`}
        source="NCDRC/SCDRC/DCDRC, ITAT, NCLT, NGT, SAT, GSTAT… (CC-BY-4.0 / s.52(1)(q))"
      />
      {counts?.district_court_records && counts.district_court_records > 0 ? (
        <StatCard
          num={counts.district_court_records}
          label={`District Court Metadata (${formatCompactCount(counts.district_court_records)})`}
          source="DDL 2010–2018 · Non-Commercial (CC-BY-NC-SA-4.0)"
        />
      ) : (
        <StatCard
          num={counts?.provisions}
          label="Indexed Statutory Provisions"
          source="Central Acts, Constitution & curated bare acts (CC-BY-4.0)"
        />
      )}
      <StatCard
        num={counts?.legislation_jurisdictions}
        label="Legislation Jurisdictions"
        source={`${formatNumber(counts?.central_acts)} Central + ${formatNumber(counts?.state_acts)} State/UT Acts (CC-BY-4.0)`}
      />
      <StatCard
        num={counts?.regulators}
        label="Financial & Sectoral Regulators"
        source="12,152 instruments: SEBI, RBI, CBIC, MCA, TRAI, IRDAI…"
      />
      {counts?.articles && counts.articles > 0 ? (
        <StatCard
          num={counts.articles}
          label="Constitution Articles"
          source={`${formatNumber(counts?.schedules)} Schedules · Amendments 1–${counts?.amendments ?? 106}`}
        />
      ) : (
        <StatCard
          num={counts?.amendments ?? 106}
          label="Constitutional Amendments"
          source={`${formatNumber(counts?.schedules)} Schedules · Verified Amendments 1–${counts?.amendments ?? 106}`}
        />
      )}
      <StatCard
        num={counts?.citation_edges}
        label="Citation Graph Edges"
        source={`${formatNumber(counts?.concordance_mappings)} Criminal & Tax maps · ${formatNumber(counts?.judicial_invalidations)} invalidations`}
      />
    </div>
  );
}
