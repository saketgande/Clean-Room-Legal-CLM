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
// Reference "Ask before you ticket" deflection surface: 4 KPIs, an Ask-Aegis
// FAQ box (best-match over the KB — resolve it without a ticket), a searchable
// knowledge base with category chips, and a list → article-detail split view.

function kbScore(a: IntakeKbArticle, query: string): number {
  const words = query.toLowerCase().split(/[^a-z0-9]+/).filter((w) => w.length > 2);
  if (!words.length) return 0;
  const hay = `${a.title} ${a.body} ${a.tags.join(" ")}`.toLowerCase();
  return words.reduce((s, w) => s + (hay.includes(w) ? 1 : 0), 0);
}

type AegisMsg = { role: "user" | "aegis"; text: string; source?: string; decline?: boolean; draft?: string };

// Ask Aegis — a best-match FAQ over the KB the page already loaded. No ticket,
// no round-trip: it reads the query and returns the closest playbook answer, or
// declines to a ticket when nothing scores.
function AskAegis({ articles, onFileTopic }: { articles: IntakeKbArticle[]; onFileTopic: (t: string) => void }) {
  const [input, setInput] = useState("");
  const [msgs, setMsgs] = useState<AegisMsg[]>([]);

  function send() {
    const query = input.trim();
    if (!query) return;
    setInput("");
    const best = articles.map((a) => ({ a, s: kbScore(a, query) })).sort((x, y) => y.s - x.s)[0];
    const user: AegisMsg = { role: "user", text: query };
    const reply: AegisMsg = !best || best.s === 0
      ? { role: "aegis", text: "I couldn't find a playbook answer for that one — file a ticket and an attorney will pick it up.", decline: true, draft: query }
      : { role: "aegis", text: best.a.body, source: best.a.source_ref };
    setMsgs((m) => [...m, user, reply]);
  }

  return (
    <div className="rounded-xl border border-brand-200 bg-brand-50/40 px-4 py-3.5">
      <div className="mb-2.5 flex items-center gap-2">
        <span className="rounded border border-brand-300 px-1.5 py-0.5 font-mono text-[8.5px] font-bold uppercase tracking-[0.12em] text-brand-600">◎ AI</span>
        <span className="text-[13.5px] text-slate-700">Have a quick legal question? <span className="font-medium text-brand-700">Ask Aegis.</span></span>
      </div>
      {msgs.length > 0 && (
        <div className="mb-2.5 max-h-72 space-y-2 overflow-y-auto rounded-lg bg-slate-100 p-2.5">
          {msgs.map((m, i) => (
            <div key={i} className={cn("text-[12.5px]", m.role === "user" && "text-right")}>
              <div className={cn("inline-block max-w-[85%] rounded-lg px-3 py-2 text-left",
                m.role === "user" ? "bg-brand-600 text-white" : "bg-slate-50 text-slate-700 ring-1 ring-slate-200")}>
                <p className="whitespace-pre-wrap leading-relaxed">{m.text}</p>
                {m.source && <p className="mt-1 font-mono text-[10px] text-slate-400">Source · {m.source}</p>}
                {m.decline && <button onClick={() => onFileTopic(m.draft ?? "")} className="mt-1 text-[11px] font-medium text-brand-700 underline">File a ticket →</button>}
              </div>
            </div>
          ))}
        </div>
      )}
      <div className="flex gap-2">
        <Input value={input} onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); send(); } }}
          placeholder={"Ask anything: “What's our standard NDA term?”"} className="h-9" />
        <Button onClick={send} disabled={!input.trim()}><Send className="h-3.5 w-3.5" /> Send</Button>
      </div>
    </div>
  );
}

export function SelfServiceTab({ onFileTopic }: { onFileTopic: (topic: string) => void }) {
  const { data, isLoading, error } = useQuery({ queryKey: ["intake-kb"], queryFn: intakeApi.kb });
  const [q, setQ] = useState("");
  const [cat, setCat] = useState<string | null>(null);
  const [sel, setSel] = useState<IntakeKbArticle | null>(null);

  const articles = useMemo(() => data ?? [], [data]);
  const cats = useMemo(() => {
    const s = new Set<string>();
    articles.forEach((a) => a.tags.forEach((t) => s.add(t)));
    return [...s].sort();
  }, [articles]);

  if (isLoading) return <CenterSpinner />;
  if (error) return <ErrorState error={error} />;

  const shown = articles.filter((a) => {
    if (cat && !a.tags.includes(cat)) return false;
    if (q && !`${a.title} ${a.body} ${a.tags.join(" ")} ${a.source_ref}`.toLowerCase().includes(q.toLowerCase())) return false;
    return true;
  });
  return (
    <div className="space-y-4">
      {/* KPI strip */}
      <div className="grid grid-cols-2 gap-3">
        <StatCard label="KB articles" value={String(articles.length)} hint="from the legal playbook" tone="blue" />
        <StatCard label="Categories" value={String(cats.length)} hint="coverage areas" tone="violet" />
      </div>

      {/* Ask Aegis — quick FAQ, no ticket */}
      <AskAegis articles={articles} onFileTopic={onFileTopic} />

      {/* Ask before you ticket — search + category chips */}
      <Card>
        <CardBody className="space-y-3">
          <p className="font-mono text-[11px] font-semibold uppercase tracking-[0.08em] text-brand-600">◎ Ask before you ticket</p>
          <div className="relative">
            <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <Input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search the legal knowledge base — try 'nda', 'sanctions', 'payment terms'…" className="pl-9" />
          </div>
          <div className="flex flex-wrap gap-2">
            <button onClick={() => setCat(null)} className={chip(cat === null)}>All</button>
            {cats.map((c) => <button key={c} onClick={() => setCat(cat === c ? null : c)} className={chip(cat === c)}>{c}</button>)}
          </div>
          <p className="font-mono text-[10.5px] text-slate-400">{shown.length} article{shown.length === 1 ? "" : "s"} · Aegis reads your query and returns the best match</p>
        </CardBody>
      </Card>

      {/* KB list → article detail split */}
      <div className={cn("grid gap-4", sel ? "lg:grid-cols-[1fr_1.25fr]" : "grid-cols-1")}>
        <Card>
          <CardBody className="space-y-2">
            <p className="font-mono text-[11px] font-semibold uppercase tracking-[0.08em] text-slate-500">▤ Knowledge base articles</p>
            {shown.length === 0 ? (
              <EmptyState title="No matches" description="Nothing matches — file a request and legal will help." />
            ) : shown.map((a) => (
              <button key={a.id} onClick={() => setSel(a)}
                className={cn("block w-full rounded-lg border px-3 py-2.5 text-left transition-colors",
                  sel?.id === a.id ? "border-brand-300 bg-brand-50" : "border-slate-200 bg-slate-100 hover:border-slate-300")}>
                <div className="flex items-center justify-between gap-2">
                  <Badge tone="violet">{a.tags[0] ?? "General"}</Badge>
                  <span className="font-mono text-[9.5px] font-semibold text-slate-400">{a.source_ref}</span>
                </div>
                <p className="mt-1.5 text-[12.5px] font-semibold text-slate-900">{a.title}</p>
                <p className="mt-0.5 line-clamp-2 text-[11px] text-slate-500">{a.body}</p>
              </button>
            ))}
          </CardBody>
        </Card>

        {sel && (
          <Card className="border-l-2 border-l-brand-600">
            <CardBody className="space-y-3">
              <div className="flex items-center justify-between">
                <p className="font-mono text-[11px] font-semibold uppercase tracking-[0.08em] text-brand-600">◎ Article detail</p>
                <button onClick={() => setSel(null)} className="text-slate-400 hover:text-slate-700">✕</button>
              </div>
              <div><Badge tone="violet">{sel.tags[0] ?? "General"}</Badge></div>
              <h3 className="text-[17px] font-semibold leading-snug text-slate-900">{sel.title}</h3>
              <div className="rounded-lg border-l-2 border-l-success bg-success-subtle/40 px-3.5 py-3">
                <p className="font-mono text-[9px] font-semibold uppercase tracking-[0.1em] text-success">Answer</p>
                <p className="mt-1 text-[13px] leading-relaxed text-slate-700">{sel.body}</p>
              </div>
              <div className="grid grid-cols-2 gap-2">
                <div className="rounded-lg bg-slate-100 px-3 py-2 text-center">
                  <p className="font-mono text-[9px] uppercase tracking-[0.1em] text-slate-400">Category</p>
                  <p className="mt-0.5 text-[12px] text-brand-700">{sel.tags[0] ?? "General"}</p>
                </div>
                <div className="rounded-lg bg-slate-100 px-3 py-2 text-center">
                  <p className="font-mono text-[9px] uppercase tracking-[0.1em] text-slate-400">Source</p>
                  <p className="mt-0.5 font-mono text-[11px] text-slate-700">{sel.source_ref}</p>
                </div>
              </div>
              <p className="text-[11.5px] text-slate-500">Still need help? <button onClick={() => onFileTopic(sel.title)} className="font-medium text-brand-700 underline">File a ticket</button> — the FAQ agent will answer from this same playbook entry.</p>
            </CardBody>
          </Card>
        )}
      </div>
    </div>
  );
}

function chip(active: boolean) {
  return cn("rounded-full border px-3 py-1 font-mono text-[10px] uppercase tracking-[0.04em] transition-colors",
    active ? "border-brand-400 bg-brand-50 text-brand-700" : "border-slate-200 bg-slate-100 text-slate-500 hover:text-slate-800");
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
      <p className="text-[13px] text-slate-500">Capacity by tier — the “senior counsel freed for strategic work” evidence, live.</p>
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
        <div className="max-h-80 space-y-2 overflow-y-auto rounded-md border border-slate-200 bg-slate-50 p-3">
          {messages.map((m, i) => (
            <div key={i} className={cn("flex", m.role === "user" ? "justify-end" : "justify-start")}>
              <div className={cn("max-w-[80%] rounded-md px-3 py-2 text-[13px]",
                m.role === "user" ? "bg-brand-600 text-white" : "bg-slate-100 text-slate-700")}>{m.content}</div>
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

