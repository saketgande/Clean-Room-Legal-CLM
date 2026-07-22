"use client";

import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Trash2 } from "lucide-react";
import {
  Badge, Button, Card, CardBody, CardHeader, CardTitle, CenterSpinner, EmptyState,
  ErrorState, Field, Input, Modal, Select, StatCard, Table, TD, TH, THead, TR, Textarea,
} from "@/components/ui";
import { intakeApi } from "@/lib/endpoints";
import { useToast } from "@/components/toast";
import { cn, titleCase } from "@/lib/utils";
import {
  POSTURE_TONE, PRIORITY_TONE, STATUS_LABEL, STATUS_TONE,
} from "@/lib/intake";
import type { IntakeRequest, IntakeSlaLeg } from "@/lib/types";

const AUTO_SEND = 0.75;

function Pill({ r }: { r: IntakeRequest }) {
  return <Badge tone={STATUS_TONE[r.status] as never}>{STATUS_LABEL[r.status]}</Badge>;
}

// ======================= TRIAGE COCKPIT =======================

export function CockpitTab({ onOpen }: { onOpen: (id: string) => void }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { data, isLoading, error } = useQuery({ queryKey: ["intake-list"], queryFn: () => intakeApi.list() });
  const queue = useMemo(
    () => (data ?? []).filter((r) => r.status !== "closed" && r.status !== "approved" && r.triage_action !== "snoozed"),
    [data],
  );
  const [idx, setIdx] = useState(0);
  const cur = queue[idx];
  const { data: rec } = useQuery({
    queryKey: ["intake-rec", cur?.id],
    queryFn: () => intakeApi.recommendation(cur!.id),
    enabled: !!cur,
  });
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [busy, setBusy] = useState(false);

  useEffect(() => { setEditing(false); }, [cur?.id]);
  useEffect(() => { if (idx >= queue.length && queue.length) setIdx(0); }, [queue.length, idx]);

  async function verdict(action: string, extra: Record<string, unknown> = {}) {
    if (!cur) return;
    setBusy(true);
    try {
      await intakeApi.triage(cur.id, { action, ...extra });
      qc.invalidateQueries({ queryKey: ["intake-list"] });
      qc.invalidateQueries({ queryKey: ["intake-mywork"] });
      notify(`${titleCase(action.replace("_", " "))} · ${cur.ref}`, "success");
      setEditing(false);
    } catch (e) { notify(e instanceof Error ? e.message : "Action failed", "error"); }
    finally { setBusy(false); }
  }
  const next = () => setIdx((i) => Math.min(i + 1, queue.length - 1));
  const prev = () => setIdx((i) => Math.max(i - 1, 0));

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.target as HTMLElement)?.matches?.("input,textarea,select")) return;
      const k = e.key.toLowerCase();
      if (k === "j") { next(); }
      else if (k === "k") { prev(); }
      else if (k === "a" && rec?.status === "pending") verdict("approved");
      else if (k === "e" && rec?.status === "pending") { setDraft(rec.drafted_response); setEditing(true); }
      else if (k === "x" && rec?.status === "pending") verdict("rejected");
      else if (k === "s" && cur) verdict("escalate");
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [rec, queue.length]); // eslint-disable-line

  if (isLoading) return <CenterSpinner label="Loading the cockpit…" />;
  if (error) return <ErrorState error={error} />;
  if (!queue.length)
    return <Card><CardBody><EmptyState title="Queue clear 🎉" description="Nothing awaiting triage right now." /></CardBody></Card>;

  return (
    <div className="space-y-4">
      {/* shortcut legend — the cockpit's keyboard-first feel */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-500">
        <span className="font-medium uppercase tracking-[0.06em] text-slate-500">Shortcuts</span>
        <span><Kbd>J</Kbd>/<Kbd>K</Kbd> move</span>
        <span><Kbd>A</Kbd> approve</span>
        <span><Kbd>E</Kbd> edit</span>
        <span><Kbd>X</Kbd> reject</span>
        <span><Kbd>S</Kbd> escalate</span>
      </div>
      {/* queue strip */}
      <div className="flex gap-2 overflow-x-auto pb-1">
        {queue.slice(0, 12).map((r, i) => (
          <button key={r.id} onClick={() => setIdx(i)}
            className={cn("shrink-0 rounded border px-3 py-2 text-left",
              i === idx ? "border-brand-600 ring-2 ring-brand-200" : "border-slate-200 hover:border-brand-300")}>
            <div className="font-mono text-[10px] text-slate-500">{r.ref}</div>
            <div className="max-w-[150px] truncate text-[13px] font-medium text-slate-900">{r.type_label}</div>
          </button>
        ))}
      </div>

      <div className="grid gap-4 lg:grid-cols-[1.1fr_1fr]">
        {/* left: request detail */}
        <Card>
          <CardHeader>
            <span className="font-mono text-xs text-slate-500">{cur.ref}</span>
            <CardTitle>{cur.type_label}</CardTitle>
            <div className="ml-auto flex items-center gap-2"><Pill r={cur} />
              <Badge tone={POSTURE_TONE[cur.sla_status] as never}>{cur.sla_pct}% SLA</Badge></div>
          </CardHeader>
          <CardBody className="space-y-4 text-[13px]">
            <dl className="grid grid-cols-[110px_1fr] gap-y-1.5">
              <dt className="text-slate-500">Requester</dt><dd>{cur.requester_name}</dd>
              <dt className="text-slate-500">Priority</dt><dd><Badge tone={PRIORITY_TONE[cur.priority] as never}>{cur.priority}</Badge></dd>
              <dt className="text-slate-500">Assignee</dt><dd>{cur.assigned_to_label ?? "Unassigned"}</dd>
              <dt className="text-slate-500">Holder</dt><dd className="capitalize">{cur.handoff_holder}</dd>
            </dl>
            {cur.ai_triage && (
              <div>
                <p className="mb-1 text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">AI triage</p>
                <div className="flex flex-wrap gap-1.5 text-xs">
                  <Badge tone="blue">{String((cur.ai_triage as Record<string, unknown>).category)}</Badge>
                  <Badge tone="slate">Complexity: {String((cur.ai_triage as Record<string, unknown>).complexity)}</Badge>
                  <Badge tone="slate">Risk: {String((cur.ai_triage as Record<string, unknown>).risk_flag)}</Badge>
                </div>
              </div>
            )}
            {(cur.fired_rules as { summaries?: { name: string; actions: string[] }[] } | null)?.summaries?.length ? (
              <div>
                <p className="mb-1 text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">Routing rules fired</p>
                <ul className="space-y-1 text-xs text-slate-600">
                  {(cur.fired_rules as { summaries: { name: string; actions: string[] }[] }).summaries.map((s, i) => (
                    <li key={i}>▸ <b>{s.name}</b> — {s.actions.join(", ")}</li>
                  ))}
                </ul>
              </div>
            ) : null}
            <button className="text-xs text-brand-700 hover:underline" onClick={() => onOpen(cur.id)}>Open full detail →</button>
          </CardBody>
        </Card>

        {/* right: recommendation */}
        <div>
          {cur.approval_gate_user_id && (
            <div className="mb-2 rounded-md border border-brand-200 bg-brand-50 px-3 py-2 text-xs text-brand-700">
              🔒 Approval is gated to a specific person for this request. Others’ approvals are refused &amp; logged.
            </div>
          )}
          {!rec ? (
            <Card><CardBody><EmptyState title="No recommendation" description="No agent matched — handle manually below." /></CardBody></Card>
          ) : (
            <Card className={cn("border-2", rec.can_auto_send ? "border-brand-500" : "border-warning/40")}>
              <CardHeader className={rec.can_auto_send ? "bg-brand-50" : "bg-warning-subtle"}>
                <span>🤖</span>
                <CardTitle>{titleCase(rec.agent_id.replace(/_/g, " "))}{rec.status !== "pending" ? ` · ${rec.status}` : ""}</CardTitle>
                <span className={cn("ml-auto rounded-full px-2 py-0.5 text-xs font-bold",
                  rec.confidence >= AUTO_SEND ? "bg-success-subtle text-success" : "bg-warning-subtle text-warning")}>
                  {rec.confidence.toFixed(2)} confidence
                </span>
              </CardHeader>
              <CardBody className="space-y-3">
                {!rec.can_auto_send && rec.status === "pending" && (
                  <div className="rounded bg-warning-subtle px-3 py-2 text-xs text-warning">
                    Confidence below {AUTO_SEND} — the agent did <b>not</b> auto-send. Your review decides.
                  </div>
                )}
                <div>
                  <p className="mb-1 text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">
                    {rec.suggested_action === "approve_and_send" ? "Suggested · approve & send" : "Flagged for human review"}
                  </p>
                  {editing ? (
                    <Textarea value={draft} onChange={(e) => setDraft(e.target.value)} rows={7} />
                  ) : (
                    <div className="whitespace-pre-wrap rounded border border-dashed border-slate-300 bg-slate-50 p-3 text-[13px]">{rec.drafted_response}</div>
                  )}
                </div>
                <div><p className="mb-1 text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">Reasoning</p>
                  <p className="text-xs text-slate-600">{rec.reasoning}</p></div>
                {rec.concerns.length > 0 && (
                  <div><p className="mb-1 text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">Concerns</p>
                    <ul className="space-y-0.5 text-xs text-slate-600">{rec.concerns.map((c, i) => <li key={i}>⚠ {c}</li>)}</ul></div>
                )}
                {rec.citations.length > 0 && (
                  <p className="text-xs text-slate-500">Sources: {rec.citations.map((c) => c.title).join(" · ")}</p>
                )}
                {rec.status === "pending" && (
                  <div className="flex flex-wrap gap-2 pt-1">
                    {editing ? (
                      <>
                        <Button size="sm" loading={busy} onClick={() => verdict("edited_approved", { edited_response: draft })}>Save &amp; approve</Button>
                        <Button size="sm" variant="outline" onClick={() => setEditing(false)}>Cancel</Button>
                      </>
                    ) : (
                      <>
                        <Button size="sm" loading={busy} onClick={() => verdict("approved")}><Kbd>A</Kbd> Approve</Button>
                        <Button size="sm" variant="outline" onClick={() => { setDraft(rec.drafted_response); setEditing(true); }}><Kbd>E</Kbd> Edit</Button>
                        <Button size="sm" variant="outline" loading={busy} onClick={() => verdict("rejected")}><Kbd>X</Kbd> Reject</Button>
                        <Button size="sm" variant="ghost" loading={busy} onClick={() => verdict("manual_close")}>Close</Button>
                      </>
                    )}
                  </div>
                )}
              </CardBody>
            </Card>
          )}
          <p className="mt-3 text-xs text-slate-400">Keyboard: <Kbd>J</Kbd>/<Kbd>K</Kbd> next/prev · <Kbd>A</Kbd> approve · <Kbd>E</Kbd> edit · <Kbd>X</Kbd> reject. Approval commits with its audit row in one transaction.</p>
        </div>
      </div>
    </div>
  );
}

function Kbd({ children }: { children: React.ReactNode }) {
  return <kbd className="rounded border border-slate-300 border-b-2 bg-slate-100 px-1.5 font-mono text-[10px] text-slate-600">{children}</kbd>;
}

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
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-slate-500">Queue health · custody-based SLA legs. Escalation fires on the overdue edge.</p>
        {isAdmin && <Button size="sm" variant="outline" loading={busy} onClick={scan}>Run breach scan</Button>}
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <StatCard label="Open" value={String(o.open_total)} tone="blue" />
        <StatCard label="At risk" value={String(o.at_risk)} tone="amber" />
        <StatCard label="Overdue" value={String(o.overdue)} tone="red" />
        <StatCard label="Breaches (7d)" value={String(o.breaches_7d)} tone="slate" />
      </div>

      {breachRow && legs && (
        <Card>
          <CardHeader><CardTitle>SLA custody legs</CardTitle>
            <span className="ml-auto font-mono text-xs text-slate-500">{breachRow.ref}</span></CardHeader>
          <CardBody>
            <SlaLegsBar legs={legs.legs} breached={legs.breached} />
          </CardBody>
        </Card>
      )}

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Attorney workload</CardTitle></CardHeader>
          <CardBody className="p-0">
            {o.workload.length === 0 ? <p className="px-4 py-3 text-xs text-slate-400">No assigned open work.</p> : (
              <Table><tbody>{o.workload.map((w) => (
                <TR key={w.user_id}><TD>{w.name}</TD>
                  <TD className="tabular-nums text-slate-500">{w.open} open</TD>
                  <TD>{w.overdue > 0 && <Badge tone="red">{w.overdue} overdue</Badge>}</TD></TR>
              ))}</tbody></Table>
            )}
          </CardBody>
        </Card>
        <Card>
          <CardHeader><CardTitle>Routing-rule effectiveness</CardTitle></CardHeader>
          <CardBody className="p-0">
            {o.rule_effectiveness.length === 0 ? <p className="px-4 py-3 text-xs text-slate-400">No rules configured.</p> : (
              <Table><tbody>{o.rule_effectiveness.map((r) => (
                <TR key={r.id}><TD className="font-medium">{r.name}</TD>
                  <TD className="tabular-nums text-slate-500">fired {r.times_fired}×</TD></TR>
              ))}</tbody></Table>
            )}
          </CardBody>
        </Card>
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

// ======================= SMART ROUTING =======================

export function RoutingTab({ isAdmin }: { isAdmin: boolean }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { data, isLoading, error } = useQuery({ queryKey: ["intake-rules"], queryFn: intakeApi.rules });
  const [creating, setCreating] = useState(false);
  async function del(id: string, name: string) {
    if (!window.confirm(`Delete rule "${name}"?`)) return;
    try { await intakeApi.deleteRule(id); qc.invalidateQueries({ queryKey: ["intake-rules"] }); notify("Rule deleted", "success"); }
    catch (e) { notify(e instanceof Error ? e.message : "Delete failed", "error"); }
  }
  if (isLoading) return <CenterSpinner />;
  if (error) return <ErrorState error={error} />;
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-slate-500">No-code when → then, evaluated in order inside the save chokepoint. Rules never override a human.</p>
        {isAdmin && <Button onClick={() => setCreating(true)}><Plus className="h-4 w-4" />New rule</Button>}
      </div>
      <Card>
        {(data ?? []).length === 0 ? <CardBody><EmptyState title="No routing rules" description="Add a rule to auto-route requests." /></CardBody> : (
          <Table>
            <THead><TR><TH>#</TH><TH>Rule</TH><TH>When</TH><TH>Then</TH><TH>Fired</TH><TH></TH></TR></THead>
            <tbody>{(data ?? []).map((r) => (
              <TR key={r.id}>
                <TD className="tabular-nums text-slate-400">{r.eval_order}</TD>
                <TD className="font-medium">{r.name}{!r.enabled && <span className="ml-1 text-xs text-slate-400">(off)</span>}</TD>
                <TD className="text-xs text-slate-500">{ruleWhen(r)}</TD>
                <TD className="text-xs text-slate-500">{ruleThen(r)}</TD>
                <TD className="tabular-nums text-slate-500">{r.times_fired}</TD>
                <TD className="text-right">{isAdmin && <Button variant="ghost" size="sm" onClick={() => del(r.id, r.name)}><Trash2 className="h-3.5 w-3.5" /></Button>}</TD>
              </TR>
            ))}</tbody>
          </Table>
        )}
      </Card>
      {creating && <RuleEditor onClose={() => setCreating(false)}
        onSaved={() => { qc.invalidateQueries({ queryKey: ["intake-rules"] }); setCreating(false); }} />}
    </div>
  );
}

function ruleWhen(r: import("@/lib/types").IntakeRule): string {
  const p = [];
  if (r.match_type) p.push(`type = ${r.match_type}`);
  if (r.match_priority) p.push(`priority = ${r.match_priority}`);
  if (r.match_department) p.push(`dept = ${r.match_department}`);
  if (r.match_keyword) p.push(`keyword “${r.match_keyword}”`);
  if (r.match_complexity) p.push(`complexity = ${r.match_complexity}`);
  return p.join(" · ") || "—";
}
function ruleThen(r: import("@/lib/types").IntakeRule): string {
  const p = [];
  if (r.set_assignee_name) p.push(`→ ${r.set_assignee_name}`);
  if (r.set_team_name) p.push(`→ pool ${r.set_team_name}`);
  if (r.set_priority) p.push(`priority ${r.set_priority}`);
  if (r.set_sla_hours) p.push(`SLA ${r.set_sla_hours}h`);
  if (r.escalate_to_name) p.push(`escalate ${r.escalate_to_name}`);
  if (r.require_approval_from_name) p.push(`gate ${r.require_approval_from_name}`);
  return p.join(" · ") || "—";
}

function RuleEditor({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const { notify } = useToast();
  const { data: types } = useQuery({ queryKey: ["intake-types"], queryFn: () => intakeApi.listTypes() });
  const { data: assignees } = useQuery({ queryKey: ["intake-assignees"], queryFn: intakeApi.assignees });
  const { data: teams } = useQuery({ queryKey: ["intake-teams"], queryFn: intakeApi.teams });
  const [f, setF] = useState<Record<string, string>>({ name: "", eval_order: "100" });
  const [busy, setBusy] = useState(false);
  const set = (k: string, v: string) => setF((s) => ({ ...s, [k]: v }));

  async function save() {
    setBusy(true);
    try {
      const body: Record<string, unknown> = { name: f.name, eval_order: Number(f.eval_order) || 100 };
      for (const k of ["match_type", "match_priority", "match_keyword", "match_complexity",
        "set_assignee_user_id", "set_priority", "set_team_id"]) if (f[k]) body[k] = f[k];
      if (f.set_sla_hours) body.set_sla_hours = Number(f.set_sla_hours);
      if (f.require_approval_from_user_id) body.require_approval_from_user_id = f.require_approval_from_user_id;
      await intakeApi.createRule(body);
      notify("Rule created", "success"); onSaved();
    } catch (e) { notify(e instanceof Error ? e.message : "Create failed", "error"); }
    finally { setBusy(false); }
  }

  return (
    <Modal open onClose={onClose} title="New routing rule" size="lg">
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-[1fr_100px]">
          <Field label="Name"><Input value={f.name} onChange={(e) => set("name", e.target.value)} placeholder="Standard NDA fast-lane" /></Field>
          <Field label="Order"><Input type="number" value={f.eval_order} onChange={(e) => set("eval_order", e.target.value)} /></Field>
        </div>
        <p className="text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">When (conditions AND)</p>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Request type"><Select value={f.match_type ?? ""} onChange={(e) => set("match_type", e.target.value)}>
            <option value="">Any</option>{(types ?? []).map((t) => <option key={t.id} value={`${t.name} Request`}>{t.name} Request</option>)}</Select></Field>
          <Field label="Complexity"><Select value={f.match_complexity ?? ""} onChange={(e) => set("match_complexity", e.target.value)}>
            <option value="">Any</option>{["simple", "standard", "complex"].map((c) => <option key={c}>{c}</option>)}</Select></Field>
          <Field label="Priority"><Select value={f.match_priority ?? ""} onChange={(e) => set("match_priority", e.target.value)}>
            <option value="">Any</option>{["Low", "Medium", "High", "Critical"].map((c) => <option key={c}>{c}</option>)}</Select></Field>
          <Field label="Keyword in description"><Input value={f.match_keyword ?? ""} onChange={(e) => set("match_keyword", e.target.value)} /></Field>
        </div>
        <p className="text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">Then (actions)</p>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label="Assign to"><Select value={f.set_assignee_user_id ?? ""} onChange={(e) => set("set_assignee_user_id", e.target.value)}>
            <option value="">—</option>{(assignees ?? []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</Select></Field>
          <Field label="Or route to pool"><Select value={f.set_team_id ?? ""} onChange={(e) => set("set_team_id", e.target.value)}>
            <option value="">—</option>{(teams ?? []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}</Select></Field>
          <Field label="Set priority"><Select value={f.set_priority ?? ""} onChange={(e) => set("set_priority", e.target.value)}>
            <option value="">—</option>{["Low", "Medium", "High", "Critical"].map((c) => <option key={c}>{c}</option>)}</Select></Field>
          <Field label="Set SLA (hours)"><Input type="number" value={f.set_sla_hours ?? ""} onChange={(e) => set("set_sla_hours", e.target.value)} /></Field>
          <Field label="Require approval from"><Select value={f.require_approval_from_user_id ?? ""} onChange={(e) => set("require_approval_from_user_id", e.target.value)}>
            <option value="">—</option>{(assignees ?? []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</Select></Field>
        </div>
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={save} loading={busy} disabled={!f.name.trim()}>Create rule</Button>
        </div>
      </div>
    </Modal>
  );
}

// ======================= TEAMS =======================

export function TeamsTab({ isAdmin }: { isAdmin: boolean }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { data, isLoading, error } = useQuery({ queryKey: ["intake-teams"], queryFn: intakeApi.teams });
  const [creating, setCreating] = useState(false);
  async function del(id: string, name: string) {
    if (!window.confirm(`Delete pool "${name}"?`)) return;
    try { await intakeApi.deleteTeam(id); qc.invalidateQueries({ queryKey: ["intake-teams"] }); notify("Pool deleted", "success"); }
    catch (e) { notify(e instanceof Error ? e.message : "Delete failed", "error"); }
  }
  if (isLoading) return <CenterSpinner />;
  if (error) return <ErrorState error={error} />;
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <p className="text-xs text-slate-500">Load-balanced pools / tiers. Routing rules point at these; the engine balances the work.</p>
        {isAdmin && <Button onClick={() => setCreating(true)}><Plus className="h-4 w-4" />New pool</Button>}
      </div>
      {(data ?? []).length === 0 ? <Card><CardBody><EmptyState title="No pools" description="Create a pool to route work to a tier." /></CardBody></Card> :
        (data ?? []).map((t) => (
          <Card key={t.id}>
            <CardHeader><CardTitle>{t.name}</CardTitle>
              <Badge tone="slate">{t.strategy === "least_loaded" ? "least-loaded" : "round-robin"}</Badge>
              {t.overflow_team_name && <Badge tone="violet">overflow → {t.overflow_team_name}</Badge>}
              {isAdmin && <Button className="ml-auto" variant="ghost" size="sm" onClick={() => del(t.id, t.name)}><Trash2 className="h-3.5 w-3.5" /></Button>}
            </CardHeader>
            <CardBody className="p-0">
              <Table><THead><TR><TH>Member</TH><TH>Capacity</TH><TH>Open now</TH><TH>Active</TH></TR></THead>
                <tbody>{t.members.map((m) => (
                  <TR key={m.id ?? m.user_id}><TD>{m.name}</TD>
                    <TD className="tabular-nums">{m.capacity === 0 ? "∞" : m.capacity}</TD>
                    <TD className="tabular-nums">{m.open_count ?? 0}</TD>
                    <TD>{(m.open_count ?? 0) >= m.capacity && m.capacity > 0 ? <Badge tone="amber">at capacity</Badge> : <Badge tone="green">active</Badge>}</TD></TR>
                ))}</tbody></Table>
            </CardBody>
          </Card>
        ))}
      {creating && <TeamEditor onClose={() => setCreating(false)}
        onSaved={() => { qc.invalidateQueries({ queryKey: ["intake-teams"] }); setCreating(false); }} />}
    </div>
  );
}

function TeamEditor({ onClose, onSaved }: { onClose: () => void; onSaved: () => void }) {
  const { notify } = useToast();
  const { data: assignees } = useQuery({ queryKey: ["intake-assignees"], queryFn: intakeApi.assignees });
  const [name, setName] = useState("");
  const [key, setKey] = useState("");
  const [strategy, setStrategy] = useState("least_loaded");
  const [members, setMembers] = useState<{ user_id: string; capacity: number }[]>([]);
  const [busy, setBusy] = useState(false);
  async function save() {
    setBusy(true);
    try {
      await intakeApi.createTeam({ key: key.trim().toLowerCase(), name: name.trim(), strategy,
        members: members.filter((m) => m.user_id).map((m) => ({ ...m, active: true })) });
      notify("Pool created", "success"); onSaved();
    } catch (e) { notify(e instanceof Error ? e.message : "Create failed", "error"); }
    finally { setBusy(false); }
  }
  return (
    <Modal open onClose={onClose} title="New pool" size="lg">
      <div className="space-y-4">
        <div className="grid gap-3 sm:grid-cols-3">
          <Field label="Name"><Input value={name} onChange={(e) => { setName(e.target.value); if (!key) setKey(e.target.value.toLowerCase().replace(/[^a-z0-9]+/g, "-")); }} placeholder="Tier 1 · Paralegals" /></Field>
          <Field label="Key"><Input value={key} onChange={(e) => setKey(e.target.value)} placeholder="tier1" /></Field>
          <Field label="Strategy"><Select value={strategy} onChange={(e) => setStrategy(e.target.value)}>
            <option value="least_loaded">Least loaded</option><option value="round_robin">Round robin</option></Select></Field>
        </div>
        <div>
          <div className="mb-2 flex items-center justify-between">
            <p className="text-[11px] font-medium uppercase tracking-[0.06em] text-slate-500">Members</p>
            <Button variant="outline" size="sm" onClick={() => setMembers((s) => [...s, { user_id: "", capacity: 5 }])}>Add member</Button>
          </div>
          <div className="space-y-2">{members.map((m, i) => (
            <div key={i} className="grid grid-cols-[1fr_100px_auto] items-center gap-2">
              <Select value={m.user_id} onChange={(e) => setMembers((s) => s.map((x, j) => j === i ? { ...x, user_id: e.target.value } : x))}>
                <option value="">Select…</option>{(assignees ?? []).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}</Select>
              <Input type="number" value={String(m.capacity)} onChange={(e) => setMembers((s) => s.map((x, j) => j === i ? { ...x, capacity: Number(e.target.value) } : x))} placeholder="capacity" />
              <Button variant="ghost" size="sm" onClick={() => setMembers((s) => s.filter((_, j) => j !== i))}><Trash2 className="h-3.5 w-3.5" /></Button>
            </div>
          ))}</div>
        </div>
        <div className="flex justify-end gap-2">
          <Button variant="outline" onClick={onClose}>Cancel</Button>
          <Button onClick={save} loading={busy} disabled={!name.trim() || !key.trim()}>Create pool</Button>
        </div>
      </div>
    </Modal>
  );
}
