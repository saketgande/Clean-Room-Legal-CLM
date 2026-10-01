"use client";

import { useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { CenterSpinner, ErrorState } from "@/components/ui";
import { intakeApi } from "@/lib/endpoints";
import { useToast } from "@/components/toast";
import type { IntakeSlaLeg } from "@/lib/types";


// ======================= SLA DASHBOARD =======================

export function SlaDashboardTab({ isAdmin }: { isAdmin: boolean }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { data: ops, isLoading, error } = useQuery({ queryKey: ["intake-slaops"], queryFn: intakeApi.slaOps });
  const { data: overdue } = useQuery({ queryKey: ["intake-list"], queryFn: () => intakeApi.list() });
  const [busy, setBusy] = useState(false);
  const breachRow = (overdue ?? []).find((r) => r.sla_status === "overdue" && r.status !== "closed");
  const { data: legs } = useQuery({
    queryKey: ["intake-sla", breachRow?.id],
    queryFn: () => intakeApi.slaLegs(breachRow!.id), enabled: !!breachRow,
  });

  async function scan() {
    setBusy(true);
    try {
      const r = await intakeApi.slaScan();
      qc.invalidateQueries({ queryKey: ["intake-slaops"] });
      qc.invalidateQueries({ queryKey: ["intake-list"] });
      notify(`Scan: ${r.escalated} escalated, ${r.breached} breached`, "success");
    } catch (e) { notify(e instanceof Error ? e.message : "Scan failed", "error"); }
    finally { setBusy(false); }
  }

  if (isLoading) return <CenterSpinner />;
  if (error) return <ErrorState error={error} />;
  const o = ops!;
  return (
    <div>
      <div className="toprow">
        <p className="dim">Queue health · custody-based SLA legs. Escalation fires on the overdue edge.</p>
        {isAdmin && <button className="btn" disabled={busy} onClick={scan}>{busy ? "Scanning…" : "Run breach scan"}</button>}
      </div>
      <div className="statcard">
        <div className="tiles">
          <div className="tile a"><div className="tn">{o.open_total}</div><div className="tl">Open</div></div>
          <div className="tile w"><div className="tn">{o.at_risk}</div><div className="tl">At risk</div></div>
          <div className="tile c"><div className="tn">{o.overdue}</div><div className="tl">Overdue</div></div>
          <div className="tile"><div className="tn">{o.breaches_7d}</div><div className="tl">Breaches (7d)</div></div>
        </div>
      </div>

      {breachRow && legs && (
        <div className="card">
          <div className="cardhd"><h3>SLA custody legs</h3><span className="ref">{breachRow.ref}</span></div>
          <div className="cardbd">
            <SlaLegsBar legs={legs.legs} breached={legs.breached} />
          </div>
        </div>
      )}

      <div className="cols">
        <div className="card">
          <div className="cardhd"><h3>Attorney workload</h3></div>
          <div className="cardbd nopad">
            {o.workload.length === 0 ? <p className="emptyline">No assigned open work.</p> : (
              <table><tbody>{o.workload.map((w) => (
                <tr key={w.user_id}><td>{w.name}</td>
                  <td className="num">{w.open} open</td>
                  <td>{w.overdue > 0 && <span className="badge c">{w.overdue} overdue</span>}</td></tr>
              ))}</tbody></table>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

export function SlaLegsBar({ legs, breached }: { legs: IntakeSlaLeg[]; breached: boolean }) {
  const total = legs.reduce((s, l) => s + l.elapsed_ms, 0) || 1;
  const color = (h: string) => (h === "agent" ? "#7A3EA6" : h === "human" ? "#0F6CBD" : "#8A8A8A");
  return (
    <div>
      <div className="flex h-6 overflow-hidden rounded-lg border border-slate-200">
        {legs.map((l, i) => (
          <div key={i} title={`${l.holder_label} · ${l.pct_of_sla}%`}
            className="flex items-center justify-center text-[10px] font-bold text-white"
            style={{ width: `${(l.elapsed_ms / total) * 100}%`, background: l.breached_during_leg ? "#B10E1C" : color(l.holder) }}>
            {(l.elapsed_ms / total) > 0.12 ? l.holder_label : ""}
          </div>
        ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-3 text-xs text-slate-500">
        {legs.map((l, i) => (
          <span key={i} className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded" style={{ background: l.breached_during_leg ? "#B10E1C" : color(l.holder) }} />
            {l.holder_label} · {l.pct_of_sla}%{l.breached_during_leg && <b className="text-danger"> ⚠ breach here</b>}
          </span>
        ))}
      </div>
      <p className="mt-2 text-xs text-slate-400">One SLA window, partitioned by who held the baton{breached ? " — breached" : ""}. A hand-off can’t hide a breach.</p>
    </div>
  );
}
