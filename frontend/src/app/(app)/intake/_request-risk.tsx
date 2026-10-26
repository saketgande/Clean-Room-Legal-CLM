"use client";
import { useState } from "react";
import { Check, RefreshCw, ShieldCheck } from "lucide-react";
import type { ContractDeviation, ContractRiskSummary, RiskDriver } from "@/lib/types";
import { riskBand, RISK_UNKNOWN_LABEL, type RiskBand } from "@/lib/utils";

const SEV_ORDER = ["critical", "high", "medium", "low"] as const;
export type Sev = typeof SEV_ORDER[number];
const sevOf = (s: string): Sev => { const v = (s || "").toLowerCase(); return (SEV_ORDER as readonly string[]).includes(v) ? v as Sev : "low"; };
const sevCls = (s: Sev) => (s === "medium" ? "med" : s);

export interface RiskFacts {
  band: RiskBand;
  unknown: boolean;
  critCount: number; gateCount: number;
  findHigh: number; findMedium: number; findLow: number; findingCount: number;
  openDevs: ContractDeviation[]; bySev: Record<Sev, ContractDeviation[]>;
  conceded: RiskDriver[]; decidedCount: number; devsLoaded: boolean;
}

/** The one place the risk numbers are worked out; header pill, decision warning and RiskReview all read it. */
export function riskFacts(risk: ContractRiskSummary | null | undefined, deviations: ContractDeviation[] | undefined, hasContract: boolean): RiskFacts {
  // needs_review is a finding a person still has to check: as open as "open".
  const openDevs = (deviations ?? []).filter((d) => ["open", "needs_review"].includes(d.status || "open"));
  const bySev: Record<Sev, ContractDeviation[]> = { critical: [], high: [], medium: [], low: [] };
  openDevs.forEach((d) => bySev[sevOf(d.severity)].push(d));
  // Accepted/waived issues keep their wording, so they still count toward the band.
  const conceded = (risk?.drivers ?? []).filter((d) => d.conceded);
  const band = riskBand(risk);
  const rc = risk?.counts ?? { critical: 0, high: 0, medium: 0, low: 0 };
  const critCount = rc.critical ?? 0;
  const gateCount = critCount + (rc.high ?? 0);
  const findHigh = gateCount; // critical folds into "high" so the 3 buckets sum to findingCount
  const findMedium = rc.medium ?? 0;
  const findLow = rc.low ?? 0;
  const findingCount = findHigh + findMedium + findLow;
  const decidedCount = deviations ? Math.max(0, findingCount - openDevs.length) : 0;
  return { band, unknown: hasContract && band === "unknown", critCount, gateCount, findHigh, findMedium, findLow, findingCount, openDevs, bySev, conceded, decidedCount, devsLoaded: !!deviations };
}

const gaugeBg = (b: RiskBand) => (b === "high" ? "var(--crit)" : b === "medium" ? "var(--warn)" : b === "low" ? "var(--good)" : "var(--ink-3)");

export function RiskReview({ risk, facts: f, canRecompute, recomputing, onRecompute }: {
  risk: ContractRiskSummary | null | undefined; facts: RiskFacts; canRecompute: boolean; recomputing: boolean; onRecompute: () => void;
}) {
  const [open, setOpen] = useState<Record<Sev, boolean>>({ critical: true, high: true, medium: false, low: false });
  return (
    <div className="card">
      <div className="ch">
        <div className="glyph warn"><ShieldCheck className="ic" /></div><h2>AI risk review</h2>
        {canRecompute ? <button className="ministep" style={{ marginLeft: "auto" }} disabled={recomputing} onClick={onRecompute}><RefreshCw className="ic" />Recompute</button> : null}
      </div>
      <div className="cb">
        {!risk ? (
          <div className="analyzing"><span>Loading the risk review…</span></div>
        ) : f.unknown ? (
          <div className="dim" style={{ fontSize: 12.5 }}><b>{RISK_UNKNOWN_LABEL}.</b> {risk.note ?? "No playbook review of this text yet."}</div>
        ) : (
          <>
            <div className="risktop">
              <div className="rgauge" style={{ background: gaugeBg(f.band) }}>{f.band.charAt(0).toUpperCase()}</div>
              <div className="rmeta">
                <div className="rb" style={{ textTransform: "capitalize" }}>{f.band} risk</div>
                <div className="rc">{f.findingCount} finding{f.findingCount === 1 ? " counts" : "s count"} toward this · {f.findHigh} high · {f.findMedium} medium · {f.findLow} low{f.devsLoaded ? ` · ${f.openDevs.length} still to decide` : ""}{f.decidedCount ? ` · ${f.decidedCount} decided, still counted` : ""}</div>
              </div>
            </div>
            {f.band === "low" && f.findingCount === 0 ? <div className="dim" style={{ fontSize: 12.5 }}>No open issues.</div> : null}
            {SEV_ORDER.filter((s) => f.bySev[s].length).map((s) => (
              <div key={s} className="sevgroup">
                <button className="sevhead" aria-expanded={open[s]} onClick={() => setOpen((o) => ({ ...o, [s]: !o[s] }))}>
                  <span className={`sevpill sev-${sevCls(s)}`}>{f.bySev[s].length} {s}</span>
                  <span className="chev">{open[s] ? "▾" : "▸"}</span>
                </button>
                {open[s] ? f.bySev[s].map((d) => (
                  <div key={d.id} className={`risk ${s === "critical" ? "crit" : sevCls(s)}`}>
                    <div className="rh"><span className={`sevpill sev-${sevCls(s)}`}>{s}</span><span className="rt">{(d.clause_type || "clause").replace(/_/g, " ")}</span></div>
                    <div className="ri">{d.issue}</div>
                    {d.suggested_fix ? <div className="rfix"><Check className="ic" /><div><b>Fix:</b> {d.suggested_fix}</div></div> : null}
                  </div>
                )) : null}
              </div>
            ))}
            {f.conceded.map((d) => { const s = sevOf(d.severity); return (
              <div key={d.deviation_id} className={`risk ${s === "critical" ? "crit" : sevCls(s)}`}>
                <div className="rh"><span className={`sevpill sev-${sevCls(s)}`}>{s}</span><span className="rt">accepted as-is — still counts</span></div>
                <div className="ri">{d.issue}</div>
              </div>
            ); })}
          </>
        )}
      </div>
    </div>
  );
}

// Long clause text wraps and clamps (4 lines) so a row never balloons. Colours are palette variables only (light + dark).
export const RISK_CSS = `
.ro .risktop{display:flex;align-items:center;gap:14px;padding:2px 0 8px}
.ro .rgauge{flex:none;width:50px;height:50px;border-radius:50%;display:grid;place-items:center;font:800 16px var(--mono);color:var(--accent-ink)}
.ro .rmeta{min-width:0} .ro .rmeta .rb{font-weight:700;font-size:14px} .ro .rmeta .rc{font-size:11.5px;color:var(--ink-2);margin-top:1px;overflow-wrap:anywhere}
.ro .sevgroup{margin-bottom:8px}
.ro .sevhead{display:flex;align-items:center;gap:8px;width:100%;background:none;border:0;padding:4px 0;cursor:pointer} .ro .sevhead .chev{color:var(--ink-3);font-size:11px}
.ro .sevpill{display:inline-flex;align-items:center;gap:5px;padding:3px 10px;border-radius:99px;font:700 11px var(--sans);text-transform:capitalize;white-space:nowrap}
.ro .sev-critical{background:var(--crit);color:var(--accent-ink)} .ro .sev-high{background:var(--crit-soft);color:var(--crit)} .ro .sev-med{background:var(--warn-soft);color:var(--warn)} .ro .sev-low{background:var(--surface-2);color:var(--ink-2)}
.ro .risk{min-width:0;border:1px solid var(--border);border-left:3px solid var(--border-strong);border-radius:9px;padding:10px 12px;margin:0 0 8px}
.ro .risk.crit{border-left-color:var(--crit)} .ro .risk.high{border-left-color:var(--crit)} .ro .risk.med{border-left-color:var(--warn)}
.ro .risk .rh{display:flex;align-items:center;gap:8px;margin-bottom:5px} .ro .risk .rt{font-weight:650;font-size:12.5px;text-transform:capitalize;min-width:0;overflow-wrap:anywhere}
.ro .risk .ri{font-size:12px;color:var(--ink-2);line-height:1.55;overflow-wrap:anywhere;display:-webkit-box;-webkit-line-clamp:4;-webkit-box-orient:vertical;overflow:hidden}
.ro .risk .rfix{display:flex;gap:6px;font-size:12px;color:var(--good);line-height:1.5;margin-top:4px;min-width:0;overflow-wrap:anywhere} .ro .risk .rfix .ic{width:13px;height:13px;flex:none;margin-top:2px} .ro .risk .rfix b{color:var(--ink);font-weight:600}
`;
