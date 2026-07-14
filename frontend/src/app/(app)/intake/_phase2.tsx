"use client";

import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { MessageSquare, Search, Send } from "lucide-react";
import {
  Badge, Button, Card, CardBody, CardHeader, CardTitle, CenterSpinner, EmptyState,
  ErrorState, Input, StatCard, Table, TD, TH, THead, TR,
} from "@/components/ui";
import { intakeApi } from "@/lib/endpoints";
import { useToast } from "@/components/toast";
import { cn } from "@/lib/utils";
import type { CopilotTurn, IntakeKbArticle } from "@/lib/types";

// ======================= SELF-SERVICE =======================

export function SelfServiceTab({ onFileTopic }: { onFileTopic: (topic: string) => void }) {
  const { data, isLoading, error } = useQuery({ queryKey: ["intake-kb"], queryFn: intakeApi.kb });
  const [q, setQ] = useState("");
  const [cat, setCat] = useState<string | null>(null);
  const [open, setOpen] = useState<string | null>(null);

  const cats = useMemo(() => {
    const s = new Set<string>();
    (data ?? []).forEach((a) => a.tags.forEach((t) => s.add(t)));
    return [...s].sort();
  }, [data]);

  if (isLoading) return <CenterSpinner />;
  if (error) return <ErrorState error={error} />;
  const shown = (data ?? []).filter((a) => {
    if (cat && !a.tags.includes(cat)) return false;
    if (q && !(`${a.title} ${a.body}`.toLowerCase().includes(q.toLowerCase()))) return false;
    return true;
  });

  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-brand-200 bg-brand-50 px-4 py-3 text-sm text-brand-700">
        <b>Ask before you ticket.</b> Standard questions and self-serve docs — no lawyer needed for routine things.
      </div>
      <div className="grid grid-cols-2 gap-3 sm:max-w-md">
        <StatCard label="KB articles" value={String((data ?? []).length)} hint="from the legal playbook" tone="blue" />
        <StatCard label="Categories" value={String(cats.length)} hint="coverage areas" tone="slate" />
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <div className="relative flex-1 min-w-[200px]">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
          <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search the knowledge base — try 'nda', 'dpa', 'payment terms'…" className="pl-9" />
        </div>
      </div>
      <div className="flex flex-wrap gap-2">
        <button onClick={() => setCat(null)} className={chip(cat === null)}>All</button>
        {cats.map((c) => <button key={c} onClick={() => setCat(c)} className={chip(cat === c)}>{c}</button>)}
      </div>
      {shown.length === 0 ? (
        <Card><CardBody><EmptyState title="No articles" description="Nothing matches — file a request and legal will help." /></CardBody></Card>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {shown.map((a) => (
            <Card key={a.id}>
              <CardBody>
                <p className="text-[10px] font-bold uppercase tracking-wide text-brand-600">{a.tags[0] ?? "General"}</p>
                <h4 className="mt-1 text-sm font-semibold">{a.title}</h4>
                <p className={cn("mt-1 text-xs text-slate-600", open === a.id ? "" : "line-clamp-3")}>{a.body}</p>
                <div className="mt-3 flex gap-3 text-xs">
                  <button className="text-brand-600 hover:underline" onClick={() => setOpen(open === a.id ? null : a.id)}>
                    {open === a.id ? "Show less" : "Read more"}</button>
                  <button className="text-slate-500 hover:underline" onClick={() => onFileTopic(a.title)}>Still need help — file a request</button>
                </div>
              </CardBody>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
function chip(active: boolean) {
  return cn("rounded-full border px-3 py-1 text-xs font-semibold",
    active ? "border-brand-600 bg-brand-600 text-white" : "border-slate-200 bg-slate-100 text-slate-600 hover:bg-slate-200");
}

// ======================= POOL OPS =======================

export function PoolOpsTab() {
  const { data, isLoading, error } = useQuery({ queryKey: ["intake-poolops"], queryFn: () => intakeApi.poolOps() });
  if (isLoading) return <CenterSpinner />;
  if (error) return <ErrorState error={error} />;
  const o = data!;
  const mixTotal = o.complexity_mix.simple + o.complexity_mix.standard + o.complexity_mix.complex || 1;
  return (
    <div className="space-y-4">
      <p className="text-xs text-slate-500">Capacity by tier — the “senior counsel freed for strategic work” evidence, live.</p>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="Open" value={String(o.totals.open)} tone="blue" />
        <StatCard label="Overdue" value={String(o.totals.overdue)} tone="red" />
        <StatCard label="Closed (30d)" value={String(o.totals.closed_30d)} tone="green" />
        <StatCard label="Effort logged" value={`${Math.round(o.totals.effort_minutes / 60)}h`} tone="slate" />
      </div>
      <Card>
        <CardHeader><CardTitle>Complexity mix (open)</CardTitle></CardHeader>
        <CardBody>
          <div className="flex h-4 overflow-hidden rounded-full border border-slate-200">
            {(["simple", "standard", "complex"] as const).map((k, i) => (
              <div key={k} title={`${k}: ${o.complexity_mix[k]}`}
                style={{ width: `${(o.complexity_mix[k] / mixTotal) * 100}%`,
                  background: ["#0E7A0B", "#0F6CBD", "#9A6700"][i] }} />
            ))}
          </div>
          <div className="mt-2 flex gap-4 text-xs text-slate-500">
            <span>◼ Simple {o.complexity_mix.simple}</span><span>◼ Standard {o.complexity_mix.standard}</span><span>◼ Complex {o.complexity_mix.complex}</span>
          </div>
        </CardBody>
      </Card>
      {o.tiers.length === 0 ? (
        <Card><CardBody><EmptyState title="No pools" description="Create pools in the Teams tab to see capacity." /></CardBody></Card>
      ) : o.tiers.map((t) => (
        <Card key={t.id}>
          <CardHeader><CardTitle>{t.name}</CardTitle>
            <Badge tone="slate">{t.strategy === "least_loaded" ? "least-loaded" : "round-robin"}</Badge>
            {t.overflow_team_name && <Badge tone="violet">overflow → {t.overflow_team_name}</Badge>}
            <span className="ml-auto text-xs text-slate-400">{t.closed_30d} closed / 30d</span>
          </CardHeader>
          <CardBody className="p-0">
            <Table><THead><TR><TH>Member</TH><TH>Utilization</TH><TH>Open</TH><TH>Overdue</TH><TH>Effort</TH></TR></THead>
              <tbody>{t.members.map((m) => (
                <TR key={m.user_id}>
                  <TD className="font-medium">{m.name}</TD>
                  <TD>
                    {m.utilization === null ? <span className="text-xs text-slate-400">unbounded</span> : (
                      <div className="flex items-center gap-2">
                        <div className="h-2 w-24 overflow-hidden rounded-full bg-slate-200">
                          <div className="h-full rounded-full" style={{ width: `${Math.min(100, m.utilization)}%`,
                            background: m.utilization >= 100 ? "#B10E1C" : m.utilization >= 70 ? "#9A6700" : "#0E7A0B" }} />
                        </div>
                        <span className="tabular-nums text-xs">{m.utilization}%</span>
                      </div>
                    )}
                  </TD>
                  <TD className="tabular-nums">{m.open}</TD>
                  <TD>{m.overdue > 0 ? <Badge tone="red">{m.overdue}</Badge> : <span className="text-slate-400">0</span>}</TD>
                  <TD className="tabular-nums text-slate-500">{Math.round(m.effort / 60)}h</TD>
                </TR>
              ))}</tbody></Table>
          </CardBody>
        </Card>
      ))}
    </div>
  );
}

// ======================= COPILOT CHAT =======================

export function CopilotChat({ onFiled }: { onFiled: (id: string) => void }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const [messages, setMessages] = useState<{ role: string; content: string }[]>([
    { role: "assistant", content: "Hi — I can help you file a legal request. What do you need?" },
  ]);
  const [input, setInput] = useState("");
  const [state, setState] = useState<CopilotTurn | null>(null);
  const [busy, setBusy] = useState(false);

  async function send() {
    const msg = input.trim();
    if (!msg) return;
    const nextMsgs = [...messages, { role: "user", content: msg }];
    setMessages(nextMsgs);
    setInput("");
    setBusy(true);
    try {
      const turn = await intakeApi.copilotTurn(nextMsgs, msg);
      setMessages((m) => [...m, { role: "assistant", content: turn.reply }]);
      setState(turn);
    } catch (e) { notify(e instanceof Error ? e.message : "Copilot error", "error"); }
    finally { setBusy(false); }
  }

  async function file() {
    if (!state) return;
    setBusy(true);
    try {
      const desc = messages.filter((m) => m.role === "user").map((m) => m.content).join(" ");
      const fv = state.extracted.counterparty ? { counterparty: state.extracted.counterparty } : null;
      const r = await intakeApi.copilotFile({
        messages, type_label: state.suggested_type_label ?? "General request",
        description: desc, field_values: fv,
      });
      qc.invalidateQueries({ queryKey: ["intake-mine"] });
      qc.invalidateQueries({ queryKey: ["intake-list"] });
      notify(`Filed ${r.ref}`, "success");
      onFiled(r.id);
    } catch (e) { notify(e instanceof Error ? e.message : "File failed", "error"); }
    finally { setBusy(false); }
  }

  return (
    <Card className="max-w-2xl">
      <CardHeader><MessageSquare className="h-4 w-4 text-brand-600" /><CardTitle>Intake Copilot</CardTitle>
        <span className="text-xs text-slate-400">guided filing</span></CardHeader>
      <CardBody className="space-y-3">
        <div className="max-h-80 space-y-2 overflow-y-auto rounded-lg border border-slate-200 bg-slate-50 p-3">
          {messages.map((m, i) => (
            <div key={i} className={cn("flex", m.role === "user" ? "justify-end" : "justify-start")}>
              <div className={cn("max-w-[80%] rounded-lg px-3 py-2 text-sm",
                m.role === "user" ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-700 shadow-sm")}>{m.content}</div>
            </div>
          ))}
        </div>
        {state && Object.keys(state.extracted).length > 0 && (
          <div className="flex flex-wrap gap-1.5 text-xs">
            <span className="text-slate-400">Captured:</span>
            {Object.entries(state.extracted).map(([k, v]) => <Badge key={k} tone="slate">{k}: {v}</Badge>)}
          </div>
        )}
        <div className="flex gap-2">
          <Input value={input} onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter") send(); }} placeholder="Type your answer…" />
          <Button onClick={send} loading={busy} disabled={!input.trim()}><Send className="h-4 w-4" /></Button>
        </div>
        {state?.ready && (
          <Button className="w-full" onClick={file} loading={busy}>File this request →</Button>
        )}
      </CardBody>
    </Card>
  );
}

// ======================= AGENTS DIRECTORY =======================

export function AiOpsTab() {
  const { data, isLoading, error } = useQuery({ queryKey: ["intake-agent-metrics"], queryFn: intakeApi.agentMetrics });
  const { notify } = useToast();
  const [refreshing, setRefreshing] = useState(false);
  if (isLoading) return <CenterSpinner />;
  if (error) return <ErrorState error={error} />;
  const m = data!;
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-slate-500">The specialist agents that triage and draft every request — each an AI first-pass a named lawyer approves.</p>
        <Button variant="outline" size="sm" loading={refreshing} onClick={async () => {
          setRefreshing(true);
          try { const r = await intakeApi.sanctionsRefresh(); notify(`OFAC list refreshed — ${(r as { added?: number }).added ?? 0} added`, "success"); }
          catch (e) { notify(e instanceof Error ? e.message : "Refresh failed", "error"); }
          finally { setRefreshing(false); }
        }}>Refresh OFAC list</Button>
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="Agents" value={String(m.agents.length)} tone="blue" />
        <StatCard label="Recommendations" value={String(m.summary.recommendations)} tone="slate" />
        <StatCard label="Pending review" value={String(m.summary.pending_review)} tone="amber" />
        <StatCard label="Accept rate" value={m.summary.accept_rate === null ? "—" : `${Math.round(m.summary.accept_rate * 100)}%`} tone="green" />
      </div>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
        {m.agents.map((a) => (
          <Card key={a.agent_id} className="flex flex-col">
            <CardBody className="flex flex-1 flex-col gap-3">
              <div className="flex items-start gap-3">
                <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-md bg-brand-50 text-lg text-brand-600">
                  {a.icon}
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <h4 className="truncate text-sm font-semibold text-slate-900">{a.name}</h4>
                    {a.active
                      ? <Badge tone="green">Live</Badge>
                      : <Badge tone="amber">Demo</Badge>}
                  </div>
                  <p className="mt-1 text-xs leading-relaxed text-slate-500">{a.description}</p>
                </div>
              </div>
              <div className="mt-auto grid grid-cols-3 gap-2 border-t border-slate-100 pt-3 text-center">
                <Stat label="Handled" value={String(a.produced)} />
                <Stat label="Accept" value={a.accept_rate === null ? "—" : `${Math.round(a.accept_rate * 100)}%`} />
                <Stat label="Avg conf" value={a.avg_confidence === null ? "—" : a.avg_confidence.toFixed(2)}
                  tone={a.degraded_rate > 0 ? "amber" : undefined}
                  hint={a.degraded_rate > 0 ? `${Math.round(a.degraded_rate * 100)}% degraded` : undefined} />
              </div>
            </CardBody>
          </Card>
        ))}
      </div>
    </div>
  );
}

function Stat({ label, value, tone, hint }: { label: string; value: string; tone?: "amber"; hint?: string }) {
  return (
    <div title={hint}>
      <p className={cn("text-sm font-semibold tabular-nums", tone === "amber" ? "text-amber-600" : "text-slate-900")}>{value}</p>
      <p className="font-mono text-[9px] uppercase tracking-wide text-slate-400">{label}</p>
    </div>
  );
}
