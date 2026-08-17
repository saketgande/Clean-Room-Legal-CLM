"use client";

// The `ideal` redesign, running live inside the app — a multi-screen workspace
// wired to the prototype engine (backend/app/ideal) through /ideal/*:
//
//   Legal Intake  — file/receive a request, see the AI recommendation + reasons,
//                   choose a workflow (this is what starts a run)
//   My Work       — every run, and the steps open right now
//   Run workspace — the workflow tracker, typed outcomes (with send-backs), the
//                   per-step activity, the CLM panel and the signing panel
//
// The backend store is in-memory, so a restart clears it. That is intentional
// for a prototype that must not touch the live schema. The inbox of not-yet-
// started requests is held in the browser until you choose a workflow.

import { useCallback, useEffect, useState } from "react";
import { apiFetch } from "@/lib/api";
import {
  Badge, Button, Card, CardBody, CardHeader, CardTitle, Field, Input, Modal, Select, Textarea,
} from "@/components/ui";

/* ---------- types (mirror the /ideal API shapes) ---------- */
type Notif = {
  ref: string; title: string; summary: string; suggested: string; confidence: number;
  because: string[]; read: string[]; alternatives: { workflow_key: string; why: string }[];
  missing_details: string[];
};
type ReqObj = { ref: string; name: string; channel: string; fields: Record<string, string>; missing: string[]; status: string };
type IntakeResult = { request: ReqObj; next_chase: { ask_for: string[] } | null; notification: Notif };
type Waiting = { step: string; step_id: string; asks: string; owner: string | null; with: string | null; sla_hours: number | null; round: number; kind?: string };
type StepVisit = {
  step: string; asks: string; owner: string | null; kind?: string | null; round: number;
  with: string | null; outcome: string | null; decided_by: string | null;
  comment: string | null; confidence: number | null; changes: { what: string; by: string }[];
};
type EventRow = { seq: number; kind: string; step: string; actor: string | null; outcome: string | null; reason: string | null; confidence: number | null; round: number; comment: string | null };
type RunView = {
  run_id: string; workflow: string; status: string; context: Record<string, unknown>;
  waiting_on: Waiting[]; steps: StepVisit[]; not_applicable: { step: string; reason: string }[]; events: EventRow[];
};
type RunSummary = { run_id: string; workflow: string; status: string; open: string[] };

/* ---------- constants ---------- */
const OUTCOME_LABEL: Record<string, string> = {
  approve: "Approve", approve_with_comments: "Approve with comments", request_changes: "Request changes",
  need_info: "Need info", escalate: "Escalate", reject: "Reject",
};
const NEEDS_COMMENT = new Set(["request_changes", "reject", "need_info"]);
const SAMPLES: { label: string; body: Record<string, string> }[] = [
  { label: "Mutual NDA (email)", body: {
    channel: "email", sender: "priya@acme.com", subject: "RE: FW: quick question — confidentiality",
    body: "Hi, please find our standard mutual NDA with Acme Corp attached. We'd like a 3-year term.",
    attachment_name: "acme-nda.pdf", attachment_text: "MUTUAL NON-DISCLOSURE AGREEMENT between Acme Corp and Dr. Reddy's Laboratories" } },
  { label: "CDMO services MSA (form)", body: {
    channel: "form", sender: "psai@drreddys.com", subject: "New MSA — Zenith Labs",
    body: "Master services agreement with Zenith Labs for GxP clinical manufacturing support, cross-border.",
    attachment_name: "zenith-msa-draft.docx", attachment_text: "MASTER SERVICES AGREEMENT for clinical manufacturing (GxP)" } },
  { label: "Data vendor MSA (chat)", body: {
    channel: "chat", sender: "@arjun.it", subject: "vendor agreement",
    body: "Services agreement with Nova Pharma covering patient data processing and systems access.",
    attachment_name: "", attachment_text: "" } },
];
const statusTone = (s: string) =>
  s === "done" ? "green" : s === "rejected" || s === "parked" ? "red" : s === "paused" ? "amber" : "blue";
const stepStateTone = (o: string | null) =>
  o === "approve" || o === "approve_with_comments" ? "green" : o === "request_changes" ? "amber"
    : o === "reject" || o === "parked" ? "red" : "slate";

/* ======================================================================= */
export default function IdealPage() {
  const [view, setView] = useState<"intake" | "mywork">("intake");
  const [inbox, setInbox] = useState<IntakeResult[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [openRun, setOpenRun] = useState<RunView | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const refreshRuns = useCallback(async () => {
    try { setRuns(await apiFetch<RunSummary[]>("/ideal/runs")); } catch (e) { setErr(String(e)); }
  }, []);
  useEffect(() => { refreshRuns(); }, [refreshRuns]);

  async function openRunById(id: string) {
    try { setOpenRun(await apiFetch<RunView>(`/ideal/runs/${id}`)); } catch (e) { setErr(String(e)); }
  }

  const totalOpen = runs.reduce((n, r) => n + r.open.length, 0);

  return (
    <div className="flex h-full min-h-0 flex-col">
      {/* header */}
      <div className="flex flex-wrap items-center gap-3 border-b border-slate-200 px-1 pb-3">
        <div>
          <h1 className="text-lg font-semibold tracking-tight text-slate-900">Legal 2.0 <span className="text-slate-400">·</span> <span className="text-slate-500">the ideal engine, live</span></h1>
          <p className="text-xs text-slate-500">Intake → recommend → confirm → work the steps. In-memory prototype; a backend restart clears it.</p>
        </div>
        <div className="ml-auto flex items-center gap-1 rounded-lg bg-slate-100 p-1">
          <Tab on={view === "intake" && !openRun} onClick={() => { setView("intake"); setOpenRun(null); }}>Legal Intake <Count n={inbox.length} /></Tab>
          <Tab on={view === "mywork" || !!openRun} onClick={() => { setView("mywork"); setOpenRun(null); refreshRuns(); }}>My Work <Count n={totalOpen} /></Tab>
        </div>
      </div>

      {err && <div className="mt-3 rounded-md border border-danger/30 bg-danger-subtle px-3 py-2 text-sm text-danger">{err} <button className="underline" onClick={() => setErr(null)}>dismiss</button></div>}

      <div className="min-h-0 flex-1 overflow-auto pt-4">
        {openRun ? (
          <RunWorkspace run={openRun} setRun={setOpenRun} onBack={() => { setOpenRun(null); refreshRuns(); }} setErr={setErr} />
        ) : view === "intake" ? (
          <IntakeScreen inbox={inbox} setInbox={setInbox} setErr={setErr}
            onStarted={(rv) => { setInbox((x) => x.filter((i) => i.request.ref !== rv.run_id)); refreshRuns(); setOpenRun(rv); }} />
        ) : (
          <MyWork runs={runs} onOpen={openRunById} />
        )}
      </div>
    </div>
  );
}

function Tab({ on, onClick, children }: { on: boolean; onClick: () => void; children: React.ReactNode }) {
  return <button onClick={onClick} className={`flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-semibold transition ${on ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-800"}`}>{children}</button>;
}
const Count = ({ n }: { n: number }) => n ? <span className="rounded bg-slate-200 px-1.5 text-xs tabular-nums text-slate-600">{n}</span> : null;

/* ======================= Legal Intake ======================= */
function IntakeScreen({ inbox, setInbox, onStarted, setErr }: {
  inbox: IntakeResult[]; setInbox: React.Dispatch<React.SetStateAction<IntakeResult[]>>;
  onStarted: (rv: RunView) => void; setErr: (s: string | null) => void;
}) {
  const [form, setForm] = useState<Record<string, string>>({ channel: "email", sender: "requester@drreddys.com", subject: "", body: "", attachment_name: "", attachment_text: "" });
  const [busy, setBusy] = useState(false);

  async function file() {
    setBusy(true); setErr(null);
    try {
      const r = await apiFetch<IntakeResult>("/ideal/intake", { method: "POST", body: form });
      setInbox((x) => [r, ...x.filter((i) => i.request.ref !== r.request.ref)]);
      setForm((f) => ({ ...f, subject: "", body: "", attachment_name: "", attachment_text: "" }));
    } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  }

  return (
    <div className="grid gap-5 lg:grid-cols-[360px_1fr]">
      {/* compose */}
      <Card className="h-fit">
        <CardHeader><CardTitle>A request arrives</CardTitle></CardHeader>
        <CardBody className="space-y-3">
          <div className="flex flex-wrap gap-1.5">
            {SAMPLES.map((s) => <Button key={s.label} size="sm" variant="secondary" onClick={() => setForm((f) => ({ ...f, ...s.body }))}>{s.label}</Button>)}
          </div>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Channel"><Select value={form.channel} onChange={(e) => setForm({ ...form, channel: e.target.value })}><option>email</option><option>form</option><option>chat</option></Select></Field>
            <Field label="From"><Input value={form.sender} onChange={(e) => setForm({ ...form, sender: e.target.value })} /></Field>
          </div>
          <Field label="Subject"><Input value={form.subject} onChange={(e) => setForm({ ...form, subject: e.target.value })} placeholder="whatever the sender wrote" /></Field>
          <Field label="Body"><Textarea rows={3} value={form.body} onChange={(e) => setForm({ ...form, body: e.target.value })} /></Field>
          <div className="grid grid-cols-2 gap-2">
            <Field label="Attachment"><Input value={form.attachment_name} onChange={(e) => setForm({ ...form, attachment_name: e.target.value })} placeholder="acme-nda.pdf" /></Field>
            <Field label="Extracted text"><Input value={form.attachment_text} onChange={(e) => setForm({ ...form, attachment_text: e.target.value })} /></Field>
          </div>
          <div className="flex justify-end"><Button onClick={file} loading={busy} disabled={!form.body && !form.subject}>Receive request</Button></div>
        </CardBody>
      </Card>

      {/* inbox */}
      <div className="space-y-3">
        <div className="flex items-center gap-2 rounded-lg border border-brand-200 bg-brand-50 px-3.5 py-2.5 text-sm text-brand-800">
          <span>◎</span><span><b>The AI reads each arrival — body and attachments — and proposes a workflow with its reasons.</b> It never starts anything; you choose below.</span>
        </div>
        {inbox.length === 0 ? (
          <div className="rounded-lg border border-dashed border-slate-300 p-10 text-center text-sm text-slate-400">Inbox empty. Load a sample on the left and receive it.</div>
        ) : inbox.map((it) => <InboxCard key={it.request.ref} it={it} setErr={setErr} onStarted={onStarted} onDrop={() => setInbox((x) => x.filter((i) => i.request.ref !== it.request.ref))} />)}
      </div>
    </div>
  );
}

function InboxCard({ it, onStarted, onDrop, setErr }: { it: IntakeResult; onStarted: (rv: RunView) => void; onDrop: () => void; setErr: (s: string | null) => void }) {
  const [req, setReq] = useState(it.request);
  const [reply, setReply] = useState<Record<string, string>>({});
  const [choice, setChoice] = useState(it.notification.suggested);
  const [busy, setBusy] = useState(false);
  const n = it.notification;

  async function applyReply() {
    setBusy(true);
    try { const r = await apiFetch<ReqObj>(`/ideal/intake/${req.ref}/reply`, { method: "POST", body: { fields: reply } }); setReq(r); } catch (e) { setErr(String(e)); } finally { setBusy(false); }
  }
  async function start() {
    setBusy(true); setErr(null);
    try { const rv = await apiFetch<RunView>(`/ideal/intake/${req.ref}/choose`, { method: "POST", body: { workflow_key: choice } }); onStarted(rv); } catch (e) { setErr(String(e)); setBusy(false); }
  }

  return (
    <Card>
      <CardBody className="space-y-3">
        <div className="flex items-start gap-3">
          <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-slate-100 text-slate-500">{req.channel === "email" ? "✉" : req.channel === "chat" ? "💬" : "▤"}</div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2"><Badge tone="slate">{req.ref}</Badge>{req.missing.length > 0 && <Badge tone="amber">needs {req.missing.join(", ")}</Badge>}</div>
            <h3 className="mt-0.5 font-semibold text-slate-900">{req.name}</h3>
            <p className="text-xs text-slate-500">via {req.channel}</p>
            <p className="mt-1.5 text-sm text-slate-600">{n.summary}</p>
            {Object.keys(req.fields).length > 0 && <div className="mt-2 flex flex-wrap gap-1.5">{Object.entries(req.fields).map(([k, v]) => <span key={k} className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-600"><span className="text-slate-400">{k}</span> <b>{v}</b></span>)}</div>}
          </div>
        </div>

        {req.missing.length > 0 && (
          <div className="rounded-md border border-amber-300 bg-amber-50 p-2.5">
            <p className="text-xs text-amber-800">Filed anyway — the clock is honest. Chase the sender:</p>
            <div className="mt-1.5 flex flex-wrap items-end gap-2">
              {req.missing.map((m) => <Field key={m} label={m}><Input className="h-8" value={reply[m] ?? ""} onChange={(e) => setReply({ ...reply, [m]: e.target.value })} /></Field>)}
              <Button size="sm" variant="secondary" onClick={applyReply} loading={busy}>Apply reply</Button>
            </div>
          </div>
        )}

        <div className="rounded-lg border border-slate-200 bg-slate-50 p-3">
          <div className="flex items-center justify-between"><span className="text-xs font-semibold uppercase tracking-wide text-slate-500">AI recommendation</span><Badge tone={n.confidence >= 0.75 ? "green" : "amber"}>{Math.round(n.confidence * 100)}% confident</Badge></div>
          <ul className="mt-1.5 space-y-0.5 text-xs text-slate-600">{n.because.map((b, i) => <li key={i}>• {b}</li>)}</ul>
          {n.read.length > 0 && <p className="mt-1.5 text-xs text-slate-400">read: {n.read.join(", ")}</p>}
          {n.alternatives.length > 0 && <p className="mt-1.5 text-xs text-slate-400">alternative: <b>{n.alternatives[0].workflow_key.toUpperCase()}</b> — {n.alternatives[0].why}</p>}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <Select value={choice} onChange={(e) => setChoice(e.target.value)} className="h-9">
            <option value="nda">NDA</option><option value="msa">Master Services Agreement</option>
          </Select>
          <Button onClick={start} loading={busy}>Choose &amp; start workflow</Button>
          <Button variant="ghost" size="sm" onClick={onDrop}>Dismiss</Button>
        </div>
      </CardBody>
    </Card>
  );
}

/* ======================= My Work ======================= */
function MyWork({ runs, onOpen }: { runs: RunSummary[]; onOpen: (id: string) => void }) {
  if (runs.length === 0) return <div className="rounded-lg border border-dashed border-slate-300 p-10 text-center text-sm text-slate-400">No matters yet. Start one from Legal Intake.</div>;
  return (
    <Card>
      <div className="divide-y divide-slate-200">
        {runs.map((r) => (
          <button key={r.run_id} onClick={() => onOpen(r.run_id)} className="flex w-full items-center gap-3 px-4 py-3.5 text-left hover:bg-slate-50">
            <div className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-slate-100 text-slate-500">▤</div>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2"><span className="font-mono text-xs text-slate-400">{r.run_id}</span><Badge tone={statusTone(r.status) as never}>{r.status}</Badge></div>
              <h3 className="font-semibold text-slate-900">{r.workflow}</h3>
              <p className="mt-0.5 text-xs text-slate-500">{r.open.length ? `Open: ${r.open.join(" + ")}` : "nothing open"}</p>
            </div>
            <span className="text-sm font-semibold text-brand-600">Open →</span>
          </button>
        ))}
      </div>
    </Card>
  );
}

/* ---- journey tracker (built from what the run reports) ---- */
function Tracker({ run }: { run: RunView }) {
  const open = new Set(run.waiting_on.map((w) => w.step));
  const nodes = run.steps.map((v) => {
    const state = open.has(v.step) && v.outcome == null ? "active"
      : v.outcome === "request_changes" ? "amber"
      : v.outcome === "reject" || v.outcome === "parked" ? "red"
      : v.outcome ? "done" : "active";
    return { name: v.step, who: v.with, round: v.round, state, kind: v.kind };
  });
  const border = (s: string) => s === "active" ? "border-brand-400 ring-2 ring-brand-100" : s === "done" ? "border-emerald-300" : s === "amber" ? "border-amber-300" : s === "red" ? "border-danger/40" : "border-slate-200";
  const label = (s: string) => s === "active" ? "open" : s === "done" ? "done" : s === "amber" ? "sent back" : s === "red" ? "stopped" : "";
  return (
    <div className="flex gap-2 overflow-x-auto pb-1">
      {nodes.map((n, i) => (
        <div key={i} className={`relative min-w-[140px] shrink-0 rounded-lg border bg-white px-3 py-2 ${border(n.state)}`}>
          <div className={`text-[9px] font-semibold uppercase tracking-wide ${n.state === "active" ? "text-brand-600" : n.state === "done" ? "text-emerald-600" : "text-slate-400"}`}>{label(n.state)}</div>
          <div className="mt-0.5 text-xs font-semibold leading-tight text-slate-800">{n.name}</div>
          {n.who && <div className="mt-1 text-[11px] text-slate-500">{n.who}</div>}
          {n.round > 1 && <span className="absolute -right-1.5 -top-1.5 rounded-full bg-amber-500 px-1.5 text-[9px] font-bold text-white">×{n.round}</span>}
        </div>
      ))}
      {run.not_applicable.map((na, i) => (
        <div key={`na${i}`} className="min-w-[130px] shrink-0 rounded-lg border border-dashed border-slate-200 bg-slate-50 px-3 py-2 opacity-60">
          <div className="text-[9px] font-semibold uppercase tracking-wide text-slate-400">not needed</div>
          <div className="mt-0.5 text-xs font-semibold leading-tight text-slate-500">{na.step}</div>
        </div>
      ))}
    </div>
  );
}

/* ======================= Run workspace ======================= */
function RunWorkspace({ run, setRun, onBack, setErr }: {
  run: RunView; setRun: (r: RunView) => void; onBack: () => void; setErr: (s: string | null) => void;
}) {
  const [busy, setBusy] = useState(false);
  const [modal, setModal] = useState<{ step_id: string; outcome: string; actor: string } | null>(null);
  const [tool, setTool] = useState<null | "clm" | "sign">(null);

  const decide = useCallback(async (step_id: string, outcome: string, actor: string, comment?: string) => {
    setBusy(true); setErr(null);
    try { const r = await apiFetch<RunView>(`/ideal/runs/${run.run_id}/decide`, { method: "POST", body: { step_id, outcome, actor, comment, confidence: null } }); setRun(r); setTool(null); }
    catch (e) { setErr(String(e).replace(/^Error:\s*/, "")); } finally { setBusy(false); setModal(null); }
  }, [run.run_id, setRun, setErr]);

  const onOutcome = (step_id: string, outcome: string, actor: string) =>
    NEEDS_COMMENT.has(outcome) ? setModal({ step_id, outcome, actor }) : decide(step_id, outcome, actor);

  const facts = Object.entries(run.context).filter(([, v]) => v !== false && v != null);
  const clmStep = run.waiting_on.find((w) => ["Prepare the working document", "Automated review", "Legal review", "With the counterparty"].includes(w.step));
  const signStep = run.waiting_on.find((w) => w.step === "Signature");

  return (
    <div className="space-y-4">
      <button onClick={onBack} className="text-sm font-semibold text-slate-500 hover:text-slate-800">← My Work</button>

      <Card><CardBody className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2"><span className="font-mono text-xs text-slate-400">{run.run_id}</span><Badge tone="slate">{run.workflow}</Badge></div>
          <h2 className="mt-0.5 text-xl font-semibold tracking-tight text-slate-900">{run.workflow}</h2>
        </div>
        <Badge tone={statusTone(run.status) as never}>{run.status}</Badge>
      </CardBody>
      {facts.length > 0 && <div className="flex flex-wrap gap-1.5 border-t border-slate-100 px-4 py-2.5">{facts.map(([k, v]) => <span key={k} className="rounded bg-slate-100 px-2 py-0.5 text-xs text-slate-600"><span className="text-slate-400">{k.replace(/_/g, " ")}</span> <b>{v === true ? "yes" : String(v)}</b></span>)}</div>}
      </Card>

      {/* tracker */}
      <Card><CardBody>
        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">Journey</p>
        <Tracker run={run} />
      </CardBody></Card>

      <div className="grid gap-4 lg:grid-cols-[1fr_360px]">
        {/* action / tool panel */}
        <div className="space-y-4">
          {tool === "clm" && clmStep ? <ClmPanel run={run} step={clmStep} busy={busy} onOutcome={onOutcome} onClose={() => setTool(null)} />
          : tool === "sign" && signStep ? <SignPanel run={run} busy={busy} onOutcome={onOutcome} onClose={() => setTool(null)} />
          : (
            <Card><CardBody className="space-y-3">
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Open now</p>
              {run.waiting_on.length === 0 ? (
                <div className={`rounded-md border p-3 text-sm ${run.status === "done" ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-slate-200 bg-slate-50 text-slate-600"}`}>
                  {run.status === "done" ? "Signed and complete — obligations are now tracked." : run.status === "rejected" ? "Rejected — the run is closed." : run.status === "parked" ? "Parked — passed the round ceiling; a human decides next." : "Nothing open."}
                </div>
              ) : run.waiting_on.map((w) => (
                <div key={w.step_id} className="rounded-lg border border-slate-200 p-3.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-semibold text-slate-900">{w.step}</span>
                    {w.round > 1 && <Badge tone="amber">round {w.round}</Badge>}
                    <span className="ml-auto text-xs text-slate-500">{w.owner} · with <b>{w.with ?? "unassigned"}</b>{w.sla_hours ? ` · ${w.sla_hours}h` : ""}</span>
                  </div>
                  <p className="mt-1 text-sm text-slate-500">{w.asks}</p>
                  {["Prepare the working document", "Automated review", "Legal review", "With the counterparty"].includes(w.step) && <Button size="sm" variant="secondary" className="mt-2" onClick={() => setTool("clm")}>Open in CLM editor</Button>}
                  {w.step === "Signature" && <Button size="sm" className="mt-2" onClick={() => setTool("sign")}>Open signing</Button>}
                  <OutcomeButtons w={w} busy={busy} onOutcome={onOutcome} />
                </div>
              ))}
              {run.waiting_on.length > 1 && <p className="text-xs text-slate-400">Two reviews run in parallel — if either sends it back, the most severe answer wins.</p>}
            </CardBody></Card>
          )}

          {run.not_applicable.length > 0 && (
            <Card><CardBody>
              <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Not required for this matter</p>
              <ul className="mt-1.5 space-y-0.5 text-xs text-slate-500">{run.not_applicable.map((na, i) => <li key={i}>✕ {na.step} — <span className="text-slate-400">{na.reason}</span></li>)}</ul>
            </CardBody></Card>
          )}
        </div>

        {/* activity + log */}
        <div className="space-y-4">
          <Card><CardHeader><CardTitle className="text-sm">Activity — who held it, what they did</CardTitle></CardHeader>
            <div className="divide-y divide-slate-100">
              {run.steps.map((v, i) => (
                <div key={i} className="px-4 py-2.5">
                  <div className="flex items-center gap-2"><span className="text-sm font-semibold text-slate-800">{v.step}</span>{v.round > 1 && <span className="font-mono text-xs text-slate-400">·r{v.round}</span>}
                    <span className="ml-auto">{v.outcome ? <Badge tone={stepStateTone(v.outcome) as never}>{OUTCOME_LABEL[v.outcome] ?? v.outcome}</Badge> : <Badge tone="blue">open</Badge>}</span></div>
                  <p className="mt-0.5 text-xs text-slate-500">{v.with ? `with ${v.with}` : ""}{v.decided_by ? ` · by ${v.decided_by}` : ""}{v.confidence != null ? ` · conf ${v.confidence}` : ""}</p>
                  {v.changes.map((c, j) => <p key={j} className="mt-0.5 text-xs text-slate-400">↳ {c.what} — {c.by}</p>)}
                  {v.comment && <p className="mt-0.5 text-xs italic text-slate-500">“{v.comment}”</p>}
                </div>
              ))}
            </div>
          </Card>
          <Card><CardHeader><CardTitle className="text-sm">Event log — the source of truth</CardTitle></CardHeader>
            <div className="max-h-72 overflow-auto px-2 py-1 font-mono text-[11px]">
              {run.events.map((e) => (
                <div key={e.seq} className="grid grid-cols-[22px_84px_1fr] gap-2 border-b border-slate-100 px-2 py-1 text-slate-600">
                  <span className="text-slate-400">{e.seq}</span><span>{e.kind}</span>
                  <span>{e.step}{e.outcome ? ` · ${e.outcome}` : ""}{e.actor && e.actor !== "—" ? ` · ${e.actor}` : ""}{e.reason ? ` · ${e.reason}` : ""}</span>
                </div>
              ))}
            </div>
          </Card>
        </div>
      </div>

      {modal && (
        <CommentModal title={`${OUTCOME_LABEL[modal.outcome]} · ${run.waiting_on.find((w) => w.step_id === modal.step_id)?.step ?? ""}`}
          prompt={modal.outcome === "request_changes" ? "What needs to change? This goes back with the work." : modal.outcome === "reject" ? "Why is this rejected?" : "What do you need to know?"}
          onCancel={() => setModal(null)} onConfirm={(c) => decide(modal.step_id, modal.outcome, modal.actor, c)} />
      )}
    </div>
  );
}

function OutcomeButtons({ w, busy, onOutcome }: { w: Waiting; busy: boolean; onOutcome: (s: string, o: string, a: string) => void }) {
  // The engine only accepts the outcomes this step offers; the API rejects the rest with a 422,
  // but we don't know them client-side, so we offer the full set and let the engine be the guard.
  const actor = w.with ?? w.owner ?? "someone";
  const set = ["approve", "approve_with_comments", "request_changes", "need_info", "escalate", "reject"];
  return (
    <div className="mt-2.5 flex flex-wrap gap-1.5">
      {set.map((o) => (
        <Button key={o} size="sm" disabled={busy}
          variant={o === "approve" || o === "approve_with_comments" ? "primary" : o === "reject" ? "danger" : "secondary"}
          onClick={() => onOutcome(w.step_id, o, actor)}>{OUTCOME_LABEL[o]}</Button>
      ))}
    </div>
  );
}

function CommentModal({ title, prompt, onCancel, onConfirm }: { title: string; prompt: string; onCancel: () => void; onConfirm: (c: string) => void }) {
  const [text, setText] = useState("");
  return (
    <Modal open onClose={onCancel} title={title}>
      <div className="space-y-3">
        <p className="text-sm text-slate-500">{prompt}</p>
        <Textarea rows={3} autoFocus value={text} onChange={(e) => setText(e.target.value)} placeholder="e.g. Cap liability at 12 months' fees" />
        <div className="flex justify-end gap-2"><Button variant="secondary" onClick={onCancel}>Cancel</Button><Button onClick={() => onConfirm(text.trim())} disabled={!text.trim()}>Confirm</Button></div>
      </div>
    </Modal>
  );
}

/* ---- CLM editor panel ---- */
const DEVIATIONS = [
  ["high", "Clause 7 · Liability", "Uncapped liability — our standard caps at 12 months' fees."],
  ["med", "Clause 11 · Assignment", "Free assignment to affiliates; we usually require consent."],
  ["low", "Clause 3 · Term", "3-year term vs our standard 2 years."],
];
function ClmPanel({ run, step, busy, onOutcome, onClose }: { run: RunView; step: Waiting; busy: boolean; onOutcome: (s: string, o: string, a: string) => void; onClose: () => void }) {
  const version = (run.steps.filter((v) => v.step === "Prepare the working document").length) || 1;
  const actor = step.with ?? step.owner ?? "someone";
  return (
    <Card>
      <div className="flex items-center justify-between border-b border-slate-200 px-4 py-2.5">
        <CardTitle className="text-sm">CLM editor — {run.workflow} · v{version}</CardTitle>
        <Button size="sm" variant="ghost" onClick={onClose}>Close</Button>
      </div>
      <div className="grid gap-0 md:grid-cols-[1fr_300px]">
        <div className="border-r border-slate-200 p-5" style={{ fontFamily: "Georgia, serif", lineHeight: 1.7 }}>
          <h4 className="mb-3 text-center font-sans text-sm font-semibold tracking-wide text-slate-800">MUTUAL NON-DISCLOSURE AGREEMENT</h4>
          <p className="text-sm text-slate-700">Between <b>Dr. Reddy&apos;s Laboratories Ltd.</b> and <b>{String(run.context.counterparty ?? "the Counterparty")}</b>.</p>
          <p className="mt-2 text-sm text-slate-700"><b>1. Purpose.</b> The Parties wish to explore a potential business relationship.</p>
          <p className="mt-2 text-sm text-slate-700"><b>3. Term.</b> <mark className="rounded bg-amber-100 px-1">In force for three (3) years from the Effective Date.</mark></p>
          <p className="mt-2 text-sm text-slate-700"><b>7. Liability.</b> <mark className="rounded bg-amber-100 px-1">Neither Party&apos;s liability shall be limited in any manner.</mark></p>
          <p className="mt-2 text-sm text-slate-700"><b>11. Assignment.</b> <mark className="rounded bg-amber-100 px-1">Either Party may assign to any affiliate without consent.</mark></p>
        </div>
        <div>
          <div className="border-b border-slate-200 px-3 py-2 text-xs font-semibold text-slate-500">Automated review · 3 deviations</div>
          {DEVIATIONS.map(([sev, cl, txt]) => (
            <div key={cl} className="border-b border-slate-100 px-3 py-2.5">
              <div className="flex items-center justify-between"><b className="text-xs text-slate-800">{cl}</b><span className={`text-[10px] font-semibold uppercase ${sev === "high" ? "text-danger" : sev === "med" ? "text-amber-600" : "text-emerald-600"}`}>{sev}</span></div>
              <p className="mt-0.5 text-xs text-slate-500">{txt}</p>
            </div>
          ))}
          <div className="p-3">
            <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-slate-400">This step: {step.step}</p>
            <OutcomeButtons w={step} busy={busy} onOutcome={onOutcome} />
            <p className="mt-2 text-xs text-slate-400">“Request changes” makes a new version and returns to drafting — the redline is the next round.</p>
          </div>
        </div>
      </div>
    </Card>
  );
}

/* ---- signing panel ---- */
function SignPanel({ run, busy, onOutcome, onClose }: { run: RunView; busy: boolean; onOutcome: (s: string, o: string, a: string) => void; onClose: () => void }) {
  const step = run.waiting_on.find((w) => w.step === "Signature")!;
  return (
    <Card>
      <div className="flex items-center justify-between border-b border-slate-200 px-4 py-2.5"><CardTitle className="text-sm">Signature</CardTitle><Button size="sm" variant="ghost" onClick={onClose}>Close</Button></div>
      <CardBody className="space-y-4">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">Recipients (in order)</p>
        <div className="divide-y divide-slate-200 rounded-lg border border-slate-200">
          <div className="flex items-center gap-3 px-3 py-2.5"><div className="grid h-7 w-7 place-items-center rounded-full bg-emerald-100 text-xs font-semibold text-emerald-700">1</div><div className="flex-1"><b className="text-sm text-slate-800">Erez Israeli</b> <span className="text-xs text-slate-400">· authorised signatory</span><p className="text-xs text-slate-500">internal · via Aegis</p></div><Badge tone="green">ready</Badge></div>
          <div className="flex items-center gap-3 px-3 py-2.5"><div className="grid h-7 w-7 place-items-center rounded-full bg-violet-100 text-xs font-semibold text-violet-700">2</div><div className="flex-1"><b className="text-sm text-slate-800">Counterparty signatory</b><p className="text-xs text-slate-500">external · secure link + passcode</p></div><Badge tone="violet">link, no account</Badge></div>
        </div>
        <div className="flex items-center gap-2 rounded-md border border-brand-200 bg-brand-50 px-3 py-2 text-xs text-brand-800">✉ The counterparty signs through a one-time secure link — they never get an account in your system.</div>
        <div className="flex gap-2"><Button onClick={() => onOutcome(step.step_id, "approve", step.with ?? "signatory")} loading={busy}>Send for signature</Button><Button variant="danger" onClick={() => onOutcome(step.step_id, "reject", step.with ?? "signatory")}>Withdraw</Button></div>
      </CardBody>
    </Card>
  );
}

export const dynamic = "force-static";
