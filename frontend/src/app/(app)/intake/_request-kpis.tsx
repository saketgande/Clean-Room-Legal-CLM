"use client";

// The at-a-glance row of the request page: the six things a person scans for before reading anything else.
// Presentational only; every figure is computed in RequestOverview from data the page already holds.

import type { ReactNode } from "react";
import { ArrowUpRight, CalendarClock, Flag, Gauge, ShieldAlert, Wallet, Workflow } from "lucide-react";

export type KpiTone = "red" | "amber" | "green" | "ind" | "vio" | "sky" | "grey";
export interface Kpi { key: string; label: string; value: string; sub?: string; tone: KpiTone; icon: ReactNode; go: string; hint: string }

const ICONS = { priority: <Flag />, time: <Gauge />, risk: <ShieldAlert />, stage: <Workflow />, value: <Wallet />, due: <CalendarClock /> };

export function buildKpis(p: {
  priority: string; sla: string | null; slaPct: number | null;
  risk: { band: string; unknown: boolean; critical: number; findings: number } | null;
  typeLabel: string; value: string | null; dueDate: string | null; dueNote: string | null; dueLate: boolean;
}): Kpi[] {
  const pr = p.priority;
  const slaTone: KpiTone = p.sla === "overdue" ? "red" : p.sla === "at_risk" ? "amber" : "green";
  const slaWord = p.sla === "overdue" ? "Overdue" : p.sla === "at_risk" ? "At risk" : "On track";
  const rk = p.risk;
  const riskTone: KpiTone = !rk || rk.unknown ? "grey" : rk.band === "high" ? "red" : rk.band === "medium" ? "amber" : "green";
  return [
    { key: "priority", label: "Priority", value: pr, sub: "How urgent it is", tone: pr === "Critical" ? "red" : pr === "High" ? "amber" : pr === "Low" ? "green" : "ind", icon: ICONS.priority, go: "Request details", hint: "See the request" },
    { key: "due", label: "Due date", value: p.dueDate ?? "No date set", sub: p.dueNote ?? undefined, tone: p.dueLate ? "red" : "ind", icon: ICONS.due, go: "Your decision", hint: "Go to the decision" },
    { key: "time", label: "Time", value: slaWord, sub: p.slaPct != null ? `${Math.round(p.slaPct)}% of the time used` : undefined, tone: slaTone, icon: ICONS.time, go: "Workflow", hint: "See the steps" },
    { key: "risk", label: "Overall risk", value: !rk ? "No contract yet" : rk.unknown ? "Not reviewed" : rk.band.charAt(0).toUpperCase() + rk.band.slice(1), sub: rk && !rk.unknown ? `${rk.critical} critical · ${rk.findings} findings` : undefined, tone: riskTone, icon: ICONS.risk, go: "AI risk review", hint: "See the findings" },
    { key: "value", label: "Deal value", value: p.value ?? "Not stated", sub: p.value ? "As stated by the requester" : undefined, tone: "sky", icon: ICONS.value, go: "Request details", hint: "See the details" },
    { key: "type", label: "Request type", value: p.typeLabel, tone: "vio", icon: ICONS.stage, go: "Request details", hint: "See the details" },
  ];
}

/** Scroll to the card whose heading is `title` and flash it. A card in a tab that is not open is off screen, so
 *  open that tab first (the tab buttons are the page's own controls, found by the heading they hold). */
const TAB_OF: Record<string, string> = { Workflow: "workflow", "AI risk review": "risk", "Request details": "overview" };
function jump(title: string) {
  const tab = TAB_OF[title];
  if (tab) document.getElementById(`rt-${tab}`)?.click();
  window.setTimeout(() => {
    const card = Array.from(document.querySelectorAll<HTMLElement>(".ro .card")).find((c) => c.querySelector("h2")?.textContent?.trim() === title && !c.closest(".rpane:not([data-on])"));
    if (!card) return;
    card.scrollIntoView({ behavior: "smooth", block: "center" });
    card.classList.remove("flash");
    void card.offsetWidth;
    card.classList.add("flash");
    window.setTimeout(() => card.classList.remove("flash"), 1600);
  }, 60);
}

export function KpiStrip({ items }: { items: Kpi[] }) {
  return (
    <section className="kpis" aria-label="Key facts">
      {items.map((k, i) => (
        <button type="button" key={k.key} title={k.hint} onClick={() => jump(k.go)} className={`kpi k-${k.tone}`} style={{ "--k": i } as React.CSSProperties}>
          <span className="kic" aria-hidden>{k.icon}</span>
          <span className="ktx"><span className="kl">{k.label}</span><b className="kv">{k.value}</b>{k.sub ? <span className="ks">{k.sub}</span> : null}</span>
          <span className="kgo"><ArrowUpRight aria-hidden />{k.hint}</span>
        </button>
      ))}
    </section>
  );
}
