"use client";

import { BASELINE_COUNTS, useCorpusStatus } from "@/lib";
import { formatCompactCount } from "@/lib/api";

export default function HomeStats() {
  const { data } = useCorpusStatus();
  const c = data?.counts;
  const districtCount = c?.district_court_records ?? BASELINE_COUNTS.district_court_records;

  return (
    <div className="left-stats">
      <div className="ls">
        <div className="ls-num num">{formatCompactCount(c?.sc_judgments ?? BASELINE_COUNTS.sc_judgments)}</div>
        <div className="ls-lbl">SC Judgments</div>
      </div>
      <div className="ls">
        <div className="ls-num num">{formatCompactCount(c?.hc_judgments ?? BASELINE_COUNTS.hc_judgments)}</div>
        <div className="ls-lbl">HC Records</div>
      </div>
      <div className="ls">
        {districtCount > 0 ? (
          <>
            <div className="ls-num num">{formatCompactCount(districtCount)}</div>
            <div className="ls-lbl">District (NC)</div>
          </>
        ) : (
          <>
            <div className="ls-num num">{formatCompactCount(c?.tribunal_orders ?? BASELINE_COUNTS.tribunal_orders)}</div>
            <div className="ls-lbl">Tribunal Orders</div>
          </>
        )}
      </div>
      <div className="ls">
        <div className="ls-num num">
          {c?.legislation_jurisdictions ?? BASELINE_COUNTS.legislation_jurisdictions} · {c?.regulators ?? BASELINE_COUNTS.regulators} · {c?.tribunals ?? BASELINE_COUNTS.tribunals}
        </div>
        <div className="ls-lbl">Leg · Reg · Trib</div>
      </div>
    </div>
  );
}
