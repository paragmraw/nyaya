// Capabilities table — static content ported from the home page design.
// The 6 modules are product copy, not live data, so they stay hardcoded.
// Coverage numbers reflect the real corpus (395 articles, 511 IPC / 358 BNS
// sections, 484 CrPC sections, 5 landmark SC judgments). Drafting assist and
// the citation verifier are marked as planned — no such tool ships yet.

type Cap = {
  code: string;
  name: string;
  desc: string;
  coverage: string;
};

const CAPS: Cap[] = [
  { code: "CON-01", name: "Constitutional lookup", desc: "Parts I–XXII, Fundamental Rights & DPSP, with amendment history.", coverage: "395 arts" },
  { code: "CRP-02", name: "CrPC procedure tracer", desc: "Bail, FIR, charge, trial stages — maps a query to the exact section.", coverage: "484 secs" },
  { code: "PEN-03", name: "Penal code (IPC / BNS)", desc: "Legacy IPC + new BNS, 2023 — offence → section → punishment range.", coverage: "511 / 358" },
  { code: "PRC-04", name: "Precedent retrieval", desc: "Supreme Court & High Court rulings, cited inline with neutral citations.", coverage: "5 curated" },
  { code: "DFT-05", name: "Drafting assist", desc: "Notices, affidavits, vakalatnama skeletons from a one-line brief.", coverage: "planned" },
  { code: "CTR-06", name: "Citation resolver", desc: "Paste a citation — resolve it to the matching provision in the corpus.", coverage: "static" },
];

export default function CapTable() {
  return (
    <table className="cap-table">
      <thead>
        <tr>
          <th style={{ width: 78 }}>Code</th>
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