// Capabilities table — static content ported from the home page design.
// The 6 modules are product copy, not live data, so they stay hardcoded.
// Coverage numbers reflect the real corpus (395 articles, 511 IPC / 358 BNS
// sections, 484 CrPC sections, 5 landmark SC judgments). Drafting assist
// and the citation verifier are marked as planned — no such tool ships yet.

import { CAPS } from "@/lib";

export default function CapTable() {
  return (
    <table className="cap-table">
      <thead>
        <tr>
          <th className="col-code">Code</th>
          <th>Module</th>
          <th className="num-col">Coverage</th>
        </tr>
      </thead>
      <tbody>
        {CAPS.map((c) => (
          <tr key={c.code}>
            <td><span className="cap-code">{c.code}</span></td>
            <td>
              <span className="cap-name">{c.name}</span>
              <span className="cap-desc">{c.desc}</span>
            </td>
            <td className="num-col">{c.coverage}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}