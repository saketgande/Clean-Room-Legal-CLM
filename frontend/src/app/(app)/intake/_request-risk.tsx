"use client";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Check, History, RefreshCw, ShieldCheck } from "lucide-react";
import type { ContractDeviation, ContractRiskSummary, RiskDriver } from "@/lib/types";
import { contractsApi } from "@/lib/endpoints";
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
      <div className="cb"><p className="hint">What the playbook found wrong in the contract text.</p>
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

const SRC: Record<string, string> = { upload: "Uploaded", manual_upload: "Uploaded again", playbook_redline: "Playbook changes", assistant_edit: "Aegis edit", counterparty_revision: "Counterparty version", editor_save: "Edited in Word", generated: "Drafted" };
const srcLabel = (s: string) => SRC[s] ?? s.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());
const dayOf = (iso: string) => new Date(iso).toLocaleDateString([], { day: "numeric", month: "short" });
const DECISION: Record<string, string> = { rejected: "Rejected", accepted: "Accepted", accepted_fallback: "Accepted fallback", waived: "Waived", escalated: "Escalated", fixed: "Fixed", dismissed: "Dismissed" };
const decLabel = (s: string) => DECISION[s] ?? s.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase());

/** What was reviewed and decided on each version, so a new version does not make the earlier work disappear. */
export function ReviewHistory({ contractId }: { contractId: string }) {
  const { data } = useQuery({ queryKey: ["review-history", contractId], queryFn: () => contractsApi.reviewHistory(contractId) });
  const [open, setOpen] = useState<Record<string, boolean>>({});
  if (!data) return null;
  const has = (v: (typeof data)[number]) => v.reviews.length > 0 || v.edits.length > 0;
  const current = data.find((v) => v.is_current);
  const lastReviewed = data.find((v) => v.reviews.length > 0);
  const firstOpen = data.find(has)?.version_id;
  // Suggested changes nobody has accepted or rejected yet, on ANY version: the Review stage will not let the contract leave while one is open.
  const waiting = data.flatMap((v) => v.edits.filter((e) => e.status === "proposed").map((e) => ({ e, v })));
  return (
    <>
    {waiting.length ? (
      <div className="card rh-wait">
        <div className="ch"><div className="glyph warn"><History className="ic" /></div><h2>Waiting for a decision</h2><span className="rh-n">{waiting.length}</span></div>
        <div className="cb">
          <p className="hint">These suggested changes are neither accepted nor rejected. The contract cannot leave Review while any is open{waiting.some((w) => !w.v.is_current) ? ", including ones made on an earlier version" : ""}.</p>
          {waiting.map(({ e, v }) => (
            <div key={e.id} className="rh-edit">
              <span className="rh-vn">v{v.version_number}</span>
              <div className="rh-ed">
                {e.original_text ? <del>{e.original_text}</del> : null}
                {e.replacement_text ? <ins>{e.replacement_text}</ins> : null}
                {!v.is_current ? <em className="rh-old">Made on version {v.version_number}; the current text is version {current?.version_number}.</em> : null}
              </div>
            </div>
          ))}
          <a className="rh-go" href={`/contracts/${contractId}`}>Open the contract to decide them</a>
        </div>
      </div>
    ) : null}
    <div className="card rh-card">
      <div className="ch"><div className="glyph"><History className="ic" /></div><h2>Earlier reviews and decisions</h2></div>
      <div className="cb">
        <p className="hint">Each version keeps what was reviewed and decided on it.</p>
        {current && lastReviewed && current.version_id !== lastReviewed.version_id && current.reviews.length === 0 ? (
          <div className="rh-note">The current text (version {current.version_number}) has not been reviewed yet. The last review was on version {lastReviewed.version_number}; its decisions are below.</div>
        ) : null}
        {data.map((v) => {
          const isOpen = open[v.version_id] ?? v.version_id === firstOpen;
          const rejected = v.edits.filter((e) => e.status === "rejected").length;
          const accepted = v.edits.filter((e) => e.status === "accepted").length;
          return (
            <section key={v.version_id} className={`rh-ver${v.is_current ? " cur" : ""}`}>
              <button className="rh-hd" aria-expanded={isOpen} onClick={() => setOpen((o) => ({ ...o, [v.version_id]: !isOpen }))}>
                <span className="rh-vn">v{v.version_number}</span>
                <span className="rh-tt"><b>{srcLabel(v.source)}</b><em>{dayOf(v.created_at)}</em></span>
                {v.is_current ? <span className="rh-cur">Current</span> : null}
                <span className="rh-sum">{has(v) ? [v.reviews.length ? `${v.reviews.reduce((n, r) => n + r.findings.length, 0)} findings` : "", v.edits.length ? `${v.edits.length} suggested changes` : ""].filter(Boolean).join(" · ") : "nothing decided"}</span>
                <span className="chev">{isOpen ? "▾" : "▸"}</span>
              </button>
              {isOpen ? (
                <div className="rh-body">
                  {!has(v) ? <div className="dim" style={{ fontSize: 12 }}>No review or decision was made on this version.</div> : null}
                  {v.reviews.map((r) => (
                    <div key={r.id} className="rh-run">
                      <div className="rh-rt">Playbook review · {dayOf(r.created_at)} · {r.status}</div>
                      {r.findings.map((f) => { const sv = sevOf(f.severity); return (
                        <div key={f.id} className={`risk ${sv === "critical" ? "crit" : sevCls(sv)}`}>
                          <div className="rh"><span className={`sevpill sev-${sevCls(sv)}`}>{sv}</span><span className="rt">{(f.clause_type || "clause").replace(/_/g, " ")}</span>
                            <span className={`rh-dec${f.decision || f.status !== "open" ? " done" : ""}`}>{f.decision ? `${decLabel(f.decision)}${f.decided_by ? ` by ${f.decided_by}` : ""}` : f.status === "open" ? "Not decided" : decLabel(f.status)}</span></div>
                          <div className="ri">{f.issue}</div>
                          {f.rationale ? <div className="rfix"><div><b>Reason:</b> {f.rationale}</div></div> : null}
                        </div>
                      ); })}
                    </div>
                  ))}
                  {v.edits.length ? (
                    <div className="rh-run">
                      <div className="rh-rt">Suggested changes · {[accepted ? `${accepted} accepted` : "", rejected ? `${rejected} rejected` : "", v.edits.length - accepted - rejected ? `${v.edits.length - accepted - rejected} pending` : ""].filter(Boolean).join(" · ")}</div>
                      {v.edits.map((e) => (
                        <div key={e.id} className="rh-edit">
                          <span className={`rh-dec ${e.status === "accepted" ? "ok" : e.status === "rejected" ? "no" : ""}`}>{decLabel(e.status)}</span>
                          <div className="rh-ed">
                            {e.original_text ? <del>{e.original_text}</del> : null}
                            {e.replacement_text ? <ins>{e.replacement_text}</ins> : null}
                          </div>
                        </div>
                      ))}
                    </div>
                  ) : null}
                </div>
              ) : null}
            </section>
          );
        })}
      </div>
    </div>
    </>
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
.ro .rh-note{font-size:12px;color:var(--ink-2);background:var(--warn-soft);border-radius:10px;padding:9px 12px;margin:2px 0 10px;line-height:1.5}
.ro .rh-ver{border-radius:12px;box-shadow:inset 0 0 0 1px var(--border);margin-bottom:8px;overflow:hidden;transition:box-shadow .2s}
.ro .rh-ver.cur{box-shadow:inset 0 0 0 1.5px var(--accent)}
.ro .rh-hd{display:flex;align-items:center;gap:10px;width:100%;padding:10px 12px;text-align:left;background:none;border:0;cursor:pointer;min-width:0}
.ro .rh-hd:hover{background:var(--surface-2)}
.ro .rh-vn{flex:none;font:700 11px var(--mono);padding:3px 8px;border-radius:99px;background:var(--accent-soft);color:var(--accent)}
.ro .rh-tt{display:flex;flex-direction:column;line-height:1.25;min-width:0} .ro .rh-tt b{font-size:12.5px} .ro .rh-tt em{font-style:normal;font-size:11px;color:var(--ink-3)}
.ro .rh-cur{flex:none;font:700 10px var(--sans);letter-spacing:.05em;text-transform:uppercase;color:var(--accent);background:var(--accent-soft);padding:2px 8px;border-radius:99px}
.ro .rh-sum{margin-left:auto;font-size:11.5px;color:var(--ink-3);text-align:right}
.ro .rh-hd .chev{color:var(--ink-3);font-size:11px}
.ro .rh-body{padding:4px 12px 12px;animation:rhin .25s cubic-bezier(.2,.8,.2,1) both}
@keyframes rhin{from{opacity:0;transform:translateY(-4px)}to{opacity:1;transform:none}}
.ro .rh-rt{font:650 11px var(--sans);letter-spacing:.04em;text-transform:uppercase;color:var(--ink-3);margin:8px 0 6px}
.ro .rh-dec{margin-left:auto;font:650 11px var(--sans);padding:2px 9px;border-radius:99px;background:var(--surface-2);color:var(--ink-2);white-space:nowrap}
.ro .rh-dec.done,.ro .rh-dec.ok{background:var(--good-soft);color:var(--good)} .ro .rh-dec.no{background:var(--crit-soft);color:var(--crit)}
.ro .rh-edit{display:flex;gap:10px;align-items:flex-start;padding:7px 0;border-top:1px solid var(--border)}
.ro .rh-edit .rh-dec{margin-left:0;flex:none}
.ro .rh-ed{font-size:12px;line-height:1.5;color:var(--ink-2);min-width:0;overflow-wrap:anywhere} .ro .rh-ed del{display:block;color:var(--crit);opacity:.8} .ro .rh-ed ins{display:block;text-decoration:none;color:var(--good)}
.ro .risk .rh .rh-dec{margin-left:auto}
@media (prefers-reduced-motion:reduce){.ro .rh-body{animation:none}}
.ro .rh-n{margin-left:auto;font:700 11px var(--sans);padding:2px 9px;border-radius:99px;background:var(--warn-soft);color:var(--warn)}
.ro .rh-old{display:block;font-style:normal;font-size:11px;color:var(--ink-3);margin-top:3px}
.ro .rh-go{display:inline-block;margin-top:10px;font-weight:650;font-size:12.5px;color:var(--accent);text-decoration:none} .ro .rh-go:hover{text-decoration:underline}
.ro .rh-wait .rh-edit .rh-vn{margin-top:1px}
`;
