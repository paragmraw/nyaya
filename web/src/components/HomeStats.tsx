"use client";

import { useCorpusStatus } from "@/lib";
import { formatCompactCount } from "@/lib/api";

export default function HomeStats() {
  const { data } = useCorpusStatus();
  const c = data?.counts;

  return (
    <div className="left-stats">
      <div className="ls">
        <div className="ls-num num">{formatCompactCount(c?.sc_judgments ?? 35000)}</div>
        <div className="ls-lbl">SC Judgments</div>
      </div>
      <div className="ls">
        <div className="ls-num num">{formatCompactCount(c?.hc_judgments ?? 17770000)}</div>
        <div className="ls-lbl">HC Records</div>
      </div>
      <div className="ls">
        <div className="ls-num num">{formatCompactCount(c?.district_court_records ?? 81000000)}</div>
        <div className="ls-lbl">District (NC)</div>
      </div>
      <div className="ls">
        <div className="ls-num num">
          {c?.legislation_jurisdictions ?? 37} · {c?.regulators ?? 12} · {c?.tribunals ?? 15}
        </div>
        <div className="ls-lbl">Leg · Reg · Trib</div>
      </div>
    </div>
  );
}
