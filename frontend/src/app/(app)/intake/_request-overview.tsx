"use client";

// Request Overview — a port of Screen 3: the request dossier. Header + pinned
// stage tracker, the request summary + AI understanding, governance checks, the
// workflow stages, and an at-a-glance / documents / people / activity rail.
// Wired to intakeApi (get + documents + handoffs). CLM editor is a linked page.
// Scoped under `.ro` (reuses the `.li-board` palette from the shell's CSS).

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { intakeApi, workflowsApi, brainApi, contractsApi } from "@/lib/endpoints";
import { useToast } from "@/components/toast";
import { stepMeta } from "./_governance-ladder";
import { NegotiationPanel } from "./_negotiation-panel";
import type { IntakeRequest, WorkflowSuggestion, WorkflowRun, WorkflowRunStep, ContractDeviation, RiskDriver } from "@/lib/types";

const SEV_ORDER = ["critical", "high", "medium", "low"] as const;
type Sev = typeof SEV_ORDER[number];
const sevOf = (s: string): Sev => { const v = (s || "").toLowerCase(); return (SEV_ORDER as readonly string[]).includes(v) ? v as Sev : "low"; };

// One display row for the workflow — unified over the live WorkflowRun engine (when
// a run exists) and the static intake stage-spine fallback (before one starts).
type WfStep = { label: string; tone: "done" | "cur" | "warn" | "todo"; node: string; badge?: string; badgeTone: "done" | "cur" | "warn"; sub?: string; sidx?: number; assign?: string; result?: Record<string, unknown> | null };
const isWaiting = (s?: WorkflowRunStep) => !!s && s.status.startsWith("waiting");
// The "doorway" for a step: the real tool the owner opens to actually DO the
// step's work, with the contract in context — not just a "mark done" button.
const STEP_DOORWAY: Record<string, { label: string; icon: string }> = {
  clm_draft: { label: "Open the draft", icon: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>' },
  human_task: { label: "Review & redline", icon: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>' },
  counterparty: { label: "Open to negotiate", icon: '<path d="M17 1l4 4-4 4"/><path d="M3 11V9a4 4 0 0 1 4-4h14M7 23l-4-4 4-4"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/>' },
};
const assignOf = (s: WorkflowRunStep): string | undefined => {
  const who = s.assignee_label;
  const grp = s.team_label ?? (s.role ? s.role.replace(/_/g, " ") : undefined);
  if (who && grp) return `${who} · ${grp}`;
  return who ?? grp ?? undefined;
};
// The meaningful outputs an AI/subsystem step produced, as label→value pairs.
function resultRows(res: Record<string, unknown> | null | undefined): [string, string][] {
  if (!res) return [];
  const pct = (v: unknown) => `${Math.round(Number(v) * 100)}%`;
  const rows: [string, string][] = [];
  const push = (k: string, v: unknown) => { if (v != null && v !== "") rows.push([k, String(v)]); };
  if (res.risk_band || res.risk_score != null) push("Risk", [res.risk_band, res.risk_score != null ? `(${res.risk_score})` : ""].filter(Boolean).join(" "));
  if (res.confidence != null) push("Confidence", pct(res.confidence));
  push("Category", res.category);
  push("Matter", res.matter_type);
  push("Severity", res.severity);
  if (res.sanctions_status) push("Sanctions", res.sanctions_status);
  if (res.notification_required != null) push("Breach notice", res.notification_required ? "required" : "not required");
  push("Summary", res.summary);
  const drivers = res.top_drivers ?? res.affected_data_categories ?? res.statutory_deadlines;
  if (Array.isArray(drivers) && drivers.length) push("Drivers", drivers.map((d) => (typeof d === "string" ? d : (d as { label?: string; name?: string }).label ?? (d as { name?: string }).name ?? JSON.stringify(d))).slice(0, 4).join(", "));
  return rows;
}

const svg = (p: string) => <svg className="ic" viewBox="0 0 24 24" dangerouslySetInnerHTML={{ __html: p }} />;
const ini = (n: string) => n.split(/[ /]/).map((w) => w[0]).slice(0, 2).join("").toUpperCase();

// Which doc type this request can auto-draft (mirrors the intake service's
// heuristic). null ⇒ not a draft-able contract request.
function draftableDocType(r: IntakeRequest): string | null {
  const t = `${r.type_label} ${r.description ?? ""}`.toLowerCase();
  if (/nda|non-disclosure|non disclosure/.test(t)) return "NDA";
  if (/\b(dpa|data processing|data protection|gdpr)\b/.test(t)) return "DPA";
  if (/\b(msa|master service|master services|services agreement|statement of work|sow)\b/.test(t)) return "MSA";
  if (/vendor|supplier|procurement/.test(t)) return "Vendor agreement";
  return null;
}

function counterpartyOf(r: IntakeRequest): string | null {
  const parties = (r.parties ?? []) as { name?: string; role?: string }[];
  const cp = parties.find((p) => /counter|vendor|supplier|opposing|third|other/i.test(p.role ?? "")) ?? parties[0];
  if (cp?.name) return cp.name;
  const fv = (r.field_values ?? {}) as Record<string, unknown>;
  for (const k of ["counterparty", "company", "vendor", "party"]) if (typeof fv[k] === "string" && fv[k]) return fv[k] as string;
  return null;
}

export function RequestOverview({ id, onBack, canManage }: { id: string; onBack: () => void; canManage: boolean }) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { data: r, isLoading } = useQuery({ queryKey: ["intake-request", id], queryFn: () => intakeApi.get(id), refetchInterval: 20_000 });
  const { data: docs } = useQuery({ queryKey: ["intake-docs", id], queryFn: () => intakeApi.documents(id) });
  const { data: handoffs } = useQuery({ queryKey: ["intake-handoffs", id], queryFn: () => intakeApi.handoffs(id) });
  const { data: assignees } = useQuery({ queryKey: ["intake-assignees"], queryFn: intakeApi.assignees, enabled: canManage });
  const [reassignTo, setReassignTo] = useState("");

  const cid = r?.contract_id ?? null;
  // AI Risk Review data — the analysis the page used to hide. Poll while the risk
  // score hasn't landed yet (drafting kicks off a background clause+risk job).
  const { data: risk } = useQuery({
    queryKey: ["contract-risk", cid], queryFn: () => contractsApi.risk(cid!), enabled: !!cid,
    refetchInterval: (q) => ((q.state.data as { score?: number | null } | undefined)?.score == null ? 4000 : false),
  });
  const { data: deviations } = useQuery({ queryKey: ["contract-devs", cid], queryFn: () => contractsApi.deviations(cid!), enabled: !!cid });
  const { data: approvals } = useQuery({ queryKey: ["intake-approval-chain", id], queryFn: () => intakeApi.approvalChain(id) });

  // The live workflow engine — poll while an agent step is mid-beat so the ladder
  // animates; idle (waiting on a human / complete) runs don't poll.
  const { data: flowRun } = useQuery({
    queryKey: ["flow-run", id],
    queryFn: () => workflowsApi.runForRequest(id),
    refetchInterval: (q) => {
      const run = q.state.data as WorkflowRun | null | undefined;
      if (!run) return false;
      const working = (run.steps ?? []).some((s) => s.status === "running" || s.status === "waiting_job");
      return run.status === "running" || working ? 1500 : false;
    },
  });
  // Pump a mid-beat "running" step once: hold ~1s so the beat shows, then resume
  // the executor so the agent runs for real and the flow advances (back-and-forth).
  const pumpedRef = useRef<string | null>(null);
  useEffect(() => {
    if (!flowRun || flowRun.status !== "running") return;
    const key = `${flowRun.id}:${flowRun.current_index}`;
    if (pumpedRef.current === key) return;
    pumpedRef.current = key;
    const runId = flowRun.id;
    const t = setTimeout(() => { workflowsApi.refreshRun(runId).finally(() => qc.invalidateQueries({ queryKey: ["flow-run", id] })); }, 1000);
    return () => clearTimeout(t);
  }, [flowRun, id, qc]);

  // Ask-Aegis chat, scoped to this request's contract when it has one.
  const [chat, setChat] = useState<{ role: "you" | "ai"; text: string; cites?: number }[]>([]);
  const [q, setQ] = useState("");
  const [openSevs, setOpenSevs] = useState<Record<Sev, boolean>>({ critical: true, high: true, medium: false, low: false });
  const commentRef = useRef<HTMLInputElement>(null);

  // Every write refreshes the request in place + the boards that list it.
  const done = (msg: string) => (updated: IntakeRequest) => {
    qc.setQueryData(["intake-request", id], updated);
    ["intake-list", "intake-my-work", "intake-mine", "intake-handoffs"].forEach((k) => qc.invalidateQueries({ queryKey: k === "intake-handoffs" ? [k, id] : [k] }));
    notify(msg, "success");
  };
  const fail = (e: unknown) => notify(e instanceof Error ? e.message : "Action failed", "error");
  const refreshRunQ = () => qc.invalidateQueries({ queryKey: ["flow-run", id] });

  const reassign = useMutation({ mutationFn: (uid: string) => intakeApi.triage(id, { action: "reassigned", assignee_user_id: uid }), onSuccess: (u) => { setReassignTo(""); done("Owner reassigned")(u); }, onError: fail });
  const draft = useMutation({ mutationFn: () => intakeApi.draftContract(id), onSuccess: done("Drafting — review running"), onError: fail });
  const ingest = useMutation({ mutationFn: () => intakeApi.ingestAttachment(id), onSuccess: done("Using the attachment — review running"), onError: fail });
  // Engine actions — start the workflow, complete a human step, or poll an approval/signature.
  const startFlow = useMutation({ mutationFn: () => workflowsApi.startFlow(id), onSuccess: (run) => { qc.setQueryData(["flow-run", id], run); notify("Workflow started", "success"); }, onError: fail });
  const completeStep = useMutation({ mutationFn: () => workflowsApi.completeStep(flowRun?.id ?? ""), onSuccess: (run) => { qc.setQueryData(["flow-run", id], run); notify("Step completed", "success"); }, onError: fail });
  const checkStep = useMutation({ mutationFn: () => workflowsApi.refreshRun(flowRun?.id ?? ""), onSuccess: (run) => { qc.setQueryData(["flow-run", id], run); notify("Refreshed", "success"); }, onError: fail });
  // Dynamic edges: send a step back for rework, and post questions/comments.
  const [backTo, setBackTo] = useState("");
  const [backNote, setBackNote] = useState("");
  const [comment, setComment] = useState("");
  const returnStep = useMutation({ mutationFn: (p: { to_idx?: number; note?: string }) => workflowsApi.returnStep(flowRun?.id ?? "", p.to_idx, p.note), onSuccess: (run) => { qc.setQueryData(["flow-run", id], run); setBackTo(""); setBackNote(""); notify("Sent back for rework", "success"); }, onError: fail });
  const postComment = useMutation({ mutationFn: (text: string) => workflowsApi.comment(flowRun?.id ?? "", text), onSuccess: (run) => { qc.setQueryData(["flow-run", id], run); setComment(""); }, onError: fail });
  const rerunRisk = useMutation({ mutationFn: () => contractsApi.computeRisk(cid ?? ""), onSuccess: (rs) => { qc.setQueryData(["contract-risk", cid], rs); qc.invalidateQueries({ queryKey: ["contract-devs", cid] }); notify("Re-running the AI review…", "success"); }, onError: fail });
  const ask = useMutation({
    mutationFn: (question: string) => brainApi.ask({ question, query_scope: r?.contract_id ? "contract" : "portfolio", contract_id: r?.contract_id ?? undefined }),
    onSuccess: (res) => setChat((c) => [...c, { role: "ai", text: res.answer, cites: res.citations?.length ?? 0 }]),
    onError: (e) => setChat((c) => [...c, { role: "ai", text: e instanceof Error ? e.message : "Couldn't answer that." }]),
  });
  const busy = reassign.isPending || draft.isPending || ingest.isPending || startFlow.isPending || completeStep.isPending || checkStep.isPending || returnStep.isPending;
  function sendChat() {
    const text = q.trim();
    if (!text || ask.isPending) return;
    setChat((c) => [...c, { role: "you", text }]);
    setQ("");
    ask.mutate(text);
  }

  const steps = r?.workflow ?? [];
  if (isLoading || !r) return <div className="ro"><div style={{ padding: 40, color: "var(--ink-3)" }}>Loading request…</div></div>;

  const cp = counterpartyOf(r);
  const ai = (r.ai_triage ?? {}) as { confidence?: number; complexity?: string };
  const fs = (r.ai_triage as { flow_suggestion?: WorkflowSuggestion } | null)?.flow_suggestion;
  const missingInfo = ((r.ai_triage as { understanding?: { missing_info?: string[] } } | null)?.understanding?.missing_info) ?? [];
  const triageDegraded = (r.ai_triage as { degraded?: boolean; degraded_reason?: string } | null) ?? {};
  const stuck = r.sla_status === "overdue";
  const open = r.status !== "closed" && r.status !== "approved";
  const docType = draftableDocType(r);
  const hasAttachment = (docs ?? []).some((d) => d.extracted_chars > 0);
  const canAct = canManage && open;
  const gates = r.gates?.effective ?? [];

  // --- the live workflow, unified over the engine + the static fallback -------
  const hasRun = !!flowRun && ["running", "waiting", "complete", "failed", "cancelled"].includes(flowRun.status);
  const runComplete = flowRun?.status === "complete";
  const runFailed = flowRun?.status === "failed" || flowRun?.status === "cancelled";
  const curIdx = flowRun?.current_index ?? 0;
  const current: WorkflowRunStep | undefined = hasRun ? (flowRun!.steps ?? [])[curIdx] : undefined;
  const wf: WfStep[] = hasRun
    ? (flowRun!.steps ?? []).map((s, i) => {
        const isDone = ["done", "complete", "skipped"].includes(s.status);
        const isCur = i === curIdx && !runComplete;
        const failedHere = runFailed && i === curIdx;
        const meta = stepMeta(s.type);
        return {
          label: s.name,
          tone: isDone ? "done" : failedHere ? "warn" : isCur ? "cur" : "todo",
          node: isDone ? "✓" : failedHere ? "!" : String(i + 1),
          badge: isDone ? "done" : isCur ? (failedHere ? "failed" : s.status === "running" ? meta.running : isWaiting(s) ? meta.wait : "here now") : undefined,
          badgeTone: isDone ? "done" : failedHere ? "warn" : "cur",
          sub: meta.label,
          sidx: i,
          assign: assignOf(s),
          result: s.result,
        } as WfStep;
      })
    : steps.map((s, i) => ({
        label: s.label,
        tone: s.done ? "done" : s.active ? (stuck ? "warn" : "cur") : "todo",
        node: s.done ? "✓" : s.active && stuck ? "!" : String(i + 1),
        badge: s.done ? "done" : s.active ? (stuck ? "overdue" : "here now") : undefined,
        badgeTone: s.done ? "done" : stuck ? "warn" : "cur",
      } as WfStep));
  const wfDone = wf.filter((w) => w.tone === "done").length;
  const wfActiveIdx = wf.findIndex((w) => w.tone === "cur" || w.tone === "warn");
  const activeLabel = hasRun ? (runComplete ? "Workflow complete" : current?.name ?? "In progress") : (wf[wfActiveIdx]?.label ?? "In progress");
  const flowTitle = hasRun ? flowRun!.flow_name : fs?.flow_name;

  // The single primary action, engine-aware: run a human step, poll an
  // approval/signature, or start the workflow when none is running yet.
  type Act = { label: string; onClick: () => void; icon: string };
  let engineAction: Act | null = null;
  if (canAct) {
    if (hasRun && !runComplete && current) {
      if (current.status === "waiting_human") engineAction = { label: "Complete this step", onClick: () => completeStep.mutate(), icon: '<path d="M20 6 9 17l-5-5"/>' };
      else if (current.type === "approval" || current.type === "signature") engineAction = { label: `Check ${current.type} status`, onClick: () => checkStep.mutate(), icon: '<path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 3v5h-5"/>' };
    } else if (!hasRun) {
      engineAction = { label: "Start workflow", onClick: () => startFlow.mutate(), icon: '<path d="M5 3l14 9-14 9z"/>' };
    }
  }
  // --- AI Risk Review: group deviations by severity, enrich with driver quotes -
  const drivers: RiskDriver[] = risk?.drivers ?? [];
  const quoteFor = (ct: string): string | undefined => drivers.find((d) => d.clause_type === ct && d.quote)?.quote ?? undefined;
  const openDevs = (deviations ?? []).filter((d) => (d.status || "open") === "open");
  const bySev: Record<Sev, ContractDeviation[]> = { critical: [], high: [], medium: [], low: [] };
  openDevs.forEach((d) => bySev[sevOf(d.severity)].push(d));
  // Fall back to the risk-score drivers as findings when there are no playbook deviations.
  const useDrivers = openDevs.length === 0 && drivers.length > 0;
  const riskScore = risk?.score ?? null;
  const riskBand = (risk?.band ?? "unknown") as string;
  const analyzing = !!cid && riskScore == null;
  const bandTone = riskBand === "high" ? "crit" : riskBand === "medium" ? "warn" : riskBand === "low" ? "good" : "ink-3";
  const critCount = useDrivers ? drivers.filter((d) => d.risk === "high").length : bySev.critical.length;
  const gateCount = critCount + (useDrivers ? 0 : bySev.high.length);
  const findingCount = useDrivers ? drivers.length : openDevs.length;

  // Approve the current human step, warning when critical risks are still open.
  function approveStep() {
    if (critCount > 0 && !window.confirm(`${critCount} ${useDrivers ? "high" : "critical"}-severity risk${critCount === 1 ? "" : "s"} still open on this contract.\n\nApprove and advance anyway?`)) return;
    completeStep.mutate();
  }
  const atReviewGate = hasRun && !runComplete && canAct && current?.status === "waiting_human";

  const fv = (r.field_values ?? {}) as Record<string, unknown>;
  const value = ["value", "contract_value", "spend", "amount"].map((k) => fv[k]).find((v) => typeof v === "string" && v) as string | undefined;

  const facts: [string, string][] = [
    ["Counterparty", cp ?? "—"],
    ["Type", r.type_label],
    ...(value ? [["Value", value] as [string, string]] : []),
    ["Priority", r.priority],
  ];

  return (
    <div className="ro">
      <style dangerouslySetInnerHTML={{ __html: RO_CSS }} />

      {/* header */}
      <div className="rohd">
        <div className="crumb"><a onClick={onBack} style={{ cursor: "pointer", color: "var(--accent)" }}>← Legal Intake</a><span>/</span><span>{r.ref}</span></div>
        <div className="hrow">
          <h1>{r.subject || `${r.type_label}${cp ? ` — ${cp}` : ""}`}</h1><span className="ref">{r.ref}</span>
          <div className="badges">
            <span className="bdg type">{r.type_label}</span>
            {value ? <span className="bdg val">{value}</span> : null}
            <span className="bdg phase">{r.stage}{wf.length ? ` · ${wfDone}/${wf.length}` : ""}</span>
            {riskScore != null ? <span className={`bdg ${bandTone === "crit" ? "warn" : bandTone === "warn" ? "warn" : "val"}`}>⚑ risk {riskScore} · {riskBand}</span> : null}
            {stuck ? <span className="bdg warn">⚠ overdue</span> : null}
          </div>
          <div style={{ flex: 1 }} />
          {r.contract_id ? <Link href={`/contracts/${r.contract_id}`} className="btn">{svg('<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>')}Open CLM editor →</Link> : null}
          {engineAction ? (
            <button className="btn pri" disabled={busy} onClick={engineAction.onClick}>{svg(engineAction.icon)}{busy ? "Working…" : engineAction.label}</button>
          ) : hasRun && !runComplete && current && (current.status === "running" || current.status === "waiting_job") ? (
            <span className="btn spin" aria-live="polite">{svg('<circle cx="12" cy="12" r="9" opacity=".3"/><path d="M12 3a9 9 0 0 1 9 9"/>')}Agent working…</span>
          ) : null}
        </div>
        {/* pinned tracker — the live workflow position */}
        {wf.length > 0 && (
          <div className="flowstrip">
            <div className={`youare${stuck || runFailed ? " blocked" : ""}`}>
              <div className="ya-ic">{svg(stuck || runFailed ? '<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 2 18a2 2 0 0 0 1.7 3h16.6a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>' : '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>')}</div>
              <div><div className="k">{runComplete ? "Done" : "You are here"}</div>
                <div className="v">{activeLabel}{!runComplete && wfActiveIdx >= 0 ? ` · ${wfActiveIdx + 1} of ${wf.length}` : ""}</div>
                <div className="m">{hasRun ? (current && isWaiting(current) ? stepMeta(current.type).wait : flowTitle ?? "in progress") : (r.assigned_to_label ? `with ${r.assigned_to_label}` : "not started")}{stuck ? " · overdue" : ""}</div></div>
            </div>
            <div className="ptrack">
              {wf.map((w, i) => (
                <div key={i} className="ph" style={{ display: "flex" }}>
                  <div className={`ph ${w.tone === "done" ? "done" : w.tone === "warn" ? "cur blk" : w.tone === "cur" ? "cur" : "todo"}`}>
                    <div className="phn">{w.node}</div><div className="phl">{w.label}</div>
                  </div>
                  {i < wf.length - 1 ? <div className={`phc ${w.tone === "done" ? "done" : ""}`} /> : null}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* body */}
      <div className="body">
        <div className="col">
          <div className="card"><div className="ch"><div className="glyph">{svg('<rect x="4" y="3" width="16" height="18" rx="2"/><path d="M8 8h8M8 12h8M8 16h5"/>')}</div><h2>The request</h2></div>
            <div className="cb">
              <div className="quote">{r.description || "No description provided."}<br /><span className="dim" style={{ fontSize: 11.5 }}>— {r.source ?? "submitted"} by {r.requester_name ?? "requester"}</span></div>
              {missingInfo.length > 0 ? (
                <div className="needsinfo">
                  {svg('<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 2 18a2 2 0 0 0 1.7 3h16.6a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>')}
                  <div>
                    <b>Needs info before drafting</b>
                    <div className="ni-items">{missingInfo.map((m, i) => <span key={i} className="ni-chip">{m}</span>)}</div>
                    <div className="ni-hint">Auto-drafting is held until these are provided — add them to the request or proceed manually.</div>
                  </div>
                </div>
              ) : null}
              {triageDegraded.degraded ? (
                <div className="needsinfo" style={{ borderColor: "var(--warn)" }}>
                  {svg('<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 2 18a2 2 0 0 0 1.7 3h16.6a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>')}
                  <div>
                    <b>AI triage unavailable — review the routing</b>
                    <div className="ni-hint">{triageDegraded.degraded_reason || "This request was auto-classified by keyword rules, not a model reading. Confirm the category and routing before it advances."}</div>
                  </div>
                </div>
              ) : null}
              <div className="lbl" style={{ margin: "16px 0 0", fontSize: 10, fontWeight: 600, letterSpacing: ".08em", textTransform: "uppercase", color: "var(--ink-3)" }}>What the AI understood</div>
              <div className="facts">{facts.map(([k, v]) => <div key={k} className="f"><div className="k">{k}</div><div className="v">{v}</div></div>)}</div>
              {ai.confidence != null && (
                <div className="confline"><span>AI extraction confidence</span><div className="track"><div className="fill" style={{ width: `${Math.round(Number(ai.confidence) * 100)}%`, background: Number(ai.confidence) >= 0.8 ? "var(--good)" : "var(--warn)" }} /></div><b>{Math.round(Number(ai.confidence) * 100)}%</b>{ai.complexity ? <span className="dim"> · {ai.complexity} complexity</span> : null}</div>
              )}
            </div></div>

          {/* AI Risk Review — the analysis the page used to hide */}
          {cid && (
            <div className="card"><div className="ch"><div className="glyph warn">{svg('<path d="M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z"/><path d="M9.5 12l2 2 3-4"/>')}</div><h2>AI Risk Review</h2>{risk?.clause_count ? <span className="cnt">{risk.clause_count} clauses</span> : null}
              {canManage ? <button className="ministep" style={{ marginLeft: "auto", background: "var(--surface)", color: "var(--ink-2)", borderColor: "var(--border-strong)" }} disabled={rerunRisk.isPending} onClick={() => rerunRisk.mutate()}>{svg('<path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 3v5h-5"/>')}Re-run</button> : null}</div>
              <div className="cb">
                {analyzing ? (
                  <div className="analyzing">{svg('<circle cx="12" cy="12" r="9" opacity=".3"/><path d="M12 3a9 9 0 0 1 9 9"/>')}<span>Analyzing the contract… the AI review runs in the background after drafting.</span></div>
                ) : riskScore == null ? (
                  <div className="dim" style={{ fontSize: 12.5 }}>No analysis yet.{canManage ? <> <a onClick={() => rerunRisk.mutate()} style={{ color: "var(--accent)", cursor: "pointer", fontWeight: 600 }}>Run it →</a></> : null}</div>
                ) : (
                  <>
                    <div className="risktop">
                      <div className="rgauge" style={{ background: bandTone === "crit" ? "var(--crit)" : bandTone === "warn" ? "var(--warn)" : bandTone === "good" ? "var(--good)" : "var(--ink-3)" }}>{riskScore}</div>
                      <div className="rmeta"><div className="rb" style={{ textTransform: "capitalize" }}>{riskBand} risk</div><div className="rc">{findingCount} finding{findingCount === 1 ? "" : "s"} · {risk?.counts ? `${risk.counts.high} high · ${risk.counts.medium} medium · ${risk.counts.low} low` : ""}</div></div>
                    </div>
                    {risk?.summary ? <div className="rsum">{risk.summary}</div> : null}
                    {findingCount === 0 ? <div className="dim" style={{ fontSize: 12.5 }}>No open deviations — the contract is clean against the playbook.</div> :
                      useDrivers ? (
                        <div>{drivers.slice().sort((a, b) => (b.contribution ?? 0) - (a.contribution ?? 0)).map((d, i) => (
                          <div key={i} className={`risk ${d.risk === "high" ? "high" : d.risk === "medium" ? "med" : "low"}`}>
                            <div className="rh"><span className={`sevpill sev-${d.risk === "high" ? "high" : d.risk === "medium" ? "med" : "low"}`}>{d.risk}</span><span className="rt">{d.label}</span></div>
                            <div className="ri">{d.rationale}</div>{d.quote ? <div className="rq">“{d.quote}”</div> : null}
                          </div>
                        ))}</div>
                      ) : (
                        SEV_ORDER.filter((s) => bySev[s].length).map((s) => (
                          <div key={s} className="sevgroup">
                            <button className="sevhead" onClick={() => setOpenSevs((o) => ({ ...o, [s]: !o[s] }))}>
                              <span className={`sevpill sev-${s === "medium" ? "med" : s}`}>{bySev[s].length} {s}</span>
                              <span className="chev">{openSevs[s] ? "▾" : "▸"}</span>
                            </button>
                            {openSevs[s] ? bySev[s].map((d) => { const qt = quoteFor(d.clause_type); return (
                              <div key={d.id} className={`risk ${s === "critical" ? "crit" : s === "high" ? "high" : s === "medium" ? "med" : "low"}`}>
                                <div className="rh"><span className={`sevpill sev-${s === "medium" ? "med" : s}`}>{s}</span><span className="rt">{(d.clause_type || "clause").replace(/_/g, " ")}</span></div>
                                <div className="ri">{d.issue}</div>
                                {qt ? <div className="rq">“{qt}”</div> : null}
                                {d.suggested_fix ? <div className="rfix">{svg('<path d="M20 6 9 17l-5-5"/>')}<div><b>Fix:</b> {d.suggested_fix}</div></div> : null}
                              </div>
                            );}) : null}
                          </div>
                        ))
                      )}
                  </>
                )}
              </div></div>
          )}

          {gates.length > 0 && (
            <div className="card"><div className="ch"><div className="glyph warn">{svg('<path d="M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z"/><path d="M9.5 12l2 2 3-4"/>')}</div><h2>Governance checks</h2><span className="cnt">{gates.length}</span></div>
              <div className="cb"><div className="flowmeta">This request triggers approval gates that must clear before it can execute:</div>
                <div className="cactions" style={{ marginTop: 10 }}>{gates.map((g, i) => <span key={i} className="bdg warn">{g.label}</span>)}</div></div></div>
          )}

          <div className="card"><div className="ch"><div className="glyph flow">{svg('<circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="12" r="2.5"/><path d="M8.5 6H14a2 2 0 0 1 2 2v2M8.5 18H14a2 2 0 0 0 2-2v-2"/>')}</div><h2>Workflow &amp; stages</h2>{wf.length ? <span className="cnt">{wfDone}/{wf.length}</span> : null}{hasRun ? <span className={`bdg ${runComplete ? "phase" : runFailed ? "warn" : "type"}`} style={{ marginLeft: "auto" }}>{runComplete ? "complete" : runFailed ? flowRun!.status : "live"}</span> : null}</div>
            <div className="cb">
              {hasRun ? <div className="flowmeta"><b>Running:</b> {flowRun!.flow_name}. <span className="dim">The workflow engine drives each step — agent runs, approvals, and signatures move it forward and back.</span></div>
                : fs?.flow_name ? <div className="flowmeta"><b>Suggested:</b> {fs.flow_name}. <span className="dim">{fs.reasoning}</span>{canAct ? <> — <a onClick={() => startFlow.mutate()} style={{ color: "var(--accent)", cursor: "pointer", fontWeight: 600 }}>start it →</a></> : null}</div>
                : <div className="flowmeta">No workflow running yet.{canAct ? <> <a onClick={() => startFlow.mutate()} style={{ color: "var(--accent)", cursor: "pointer", fontWeight: 600 }}>Start workflow →</a></> : null}</div>}
              {flowRun?.error ? <div className="flowmeta" style={{ color: "var(--warn)", marginTop: 6 }}>⚠ {flowRun.error}</div> : null}
              <div className="steps" style={{ marginTop: 12 }}>
                {wf.length === 0 ? <div className="dim" style={{ fontSize: 12.5 }}>No stages defined.</div> :
                  wf.map((w, i) => {
                    const rows = resultRows(w.result);
                    return (
                    <div key={i} className={`step ${w.tone}`}>
                      <div className="snode">{w.node}</div>
                      <div className="shd"><span className="sname">{w.label}</span>
                        {w.sub ? <span className="stype">{w.sub}</span> : null}
                        {w.badge ? <span className={`sbadge ${w.badgeTone}`}>{w.badge}</span> : null}
                        {hasRun && i === curIdx && current?.status === "waiting_human" && canAct ? (() => {
                          const doc = flowRun?.contract_id ?? cid;
                          const dw = current ? STEP_DOORWAY[current.type] : undefined;
                          return (
                            <>
                              {doc && dw ? <Link href={`/contracts/${doc}`} className="ministep doorway">{svg(dw.icon)}{dw.label}</Link> : null}
                              <button className={`ministep${doc && dw ? " ghost" : ""}`} disabled={busy} onClick={() => completeStep.mutate()}>{svg('<path d="M20 6 9 17l-5-5"/>')}{current?.type === "human_task" || current?.type === "counterparty" ? "Mark done" : "Complete"}</button>
                            </>
                          );
                        })() : null}</div>
                      {w.assign ? <div className="sassign">{svg('<path d="M20 21a8 8 0 0 0-16 0"/><circle cx="12" cy="7" r="4"/>')}Assigned to <b>{w.assign}</b></div> : null}
                      {rows.length > 0 ? <div className="sresult">{rows.map(([k, v]) => <div key={k} className="rr"><span className="rk">{k}</span><span className="rv">{v}</span></div>)}</div> : null}
                    </div>
                  );})}
              </div>

              {/* Multi-agent hand-off trace — what each agent established, in order */}
              {(flowRun?.brief ?? []).length > 0 ? (
                <div style={{ marginTop: 14, padding: "12px 14px", border: "1px solid var(--border)", borderRadius: 10, background: "var(--surface)" }}>
                  <div className="lbl" style={{ fontSize: 10, fontWeight: 600, letterSpacing: ".08em", textTransform: "uppercase", color: "var(--ink-3)", marginBottom: 8 }}>Agent hand-off trace</div>
                  <ol style={{ display: "flex", flexDirection: "column", gap: 8, margin: 0, padding: 0, listStyle: "none" }}>
                    {(flowRun?.brief ?? []).map((b, i) => (
                      <li key={i} style={{ display: "flex", gap: 10, fontSize: 13 }}>
                        <span style={{ marginTop: 6, height: 6, width: 6, flexShrink: 0, borderRadius: 999, background: "var(--accent)" }} />
                        <div style={{ minWidth: 0 }}>
                          <span style={{ fontWeight: 600, color: "var(--ink-1)" }}>{(b.agent || b.type).replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}</span>
                          <span style={{ color: "var(--ink-3)" }}> · {b.step}</span>
                          <div style={{ color: "var(--ink-2)" }}>{b.summary}</div>
                        </div>
                      </li>
                    ))}
                  </ol>
                </div>
              ) : null}

              {/* Guided negotiation round-trip — shows on a negotiate step */}
              {hasRun && !runComplete && current?.type === "counterparty" && (flowRun?.contract_id ?? cid) ? (
                <div style={{ marginTop: 12 }}>
                  <NegotiationPanel contractId={(flowRun?.contract_id ?? cid)!} canAct={canAct} busy={busy} onConverged={() => completeStep.mutate()} />
                </div>
              ) : null}

              {/* Dynamic edge: send an earlier step back for rework */}
              {hasRun && !runComplete && canAct && curIdx > 0 ? (
                <div className="sendback">
                  <div className="lbl">Send back for rework</div>
                  <select className="sel" value={backTo} onChange={(e) => setBackTo(e.target.value)}>
                    <option value="">Pick the step to return to…</option>
                    {wf.slice(0, curIdx).map((w) => <option key={w.sidx} value={String(w.sidx)}>{(w.sidx ?? 0) + 1}. {w.label}</option>)}
                  </select>
                  <input className="cin" value={backNote} placeholder="Reason / question for the owner…" onChange={(e) => setBackNote(e.target.value)} />
                  <button className="btn" disabled={busy || backTo === ""} onClick={() => returnStep.mutate({ to_idx: Number(backTo), note: backNote || undefined })}>{svg('<path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 5 5v3"/>')}Send back</button>
                </div>
              ) : null}

              {/* Comments & questions on the workflow */}
              {hasRun ? (
                <div className="wfcomments">
                  <div className="lbl">Comments &amp; questions</div>
                  {(flowRun!.comments ?? []).length === 0 ? <div className="dim" style={{ fontSize: 12 }}>No comments yet — leave a note or ask a question about a stage.</div> :
                    <ul className="clist">{(flowRun!.comments ?? []).map((c, i) => (
                      <li key={i} className={c.kind === "return" ? "ret" : ""}>
                        <div className="cmeta"><b>{c.actor_name}</b>{c.idx != null && wf[c.idx] ? <span className="dim"> · on {wf[c.idx].label}</span> : null}{c.kind === "return" ? <span className="cret"> sent back</span> : null}</div>
                        <div className="ctext">{c.text}</div>
                      </li>
                    ))}</ul>}
                  {canAct ? (
                    <div className="composer">
                      <input className="cin" ref={commentRef} value={comment} placeholder="Add a comment or question…" onChange={(e) => setComment(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter" && comment.trim()) postComment.mutate(comment.trim()); }} />
                      <button className="btn" disabled={!comment.trim() || postComment.isPending} onClick={() => postComment.mutate(comment.trim())}>Post</button>
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div></div>

          {/* Ask Aegis — request-scoped chatbot */}
          <div className="card"><div className="ch"><div className="glyph flow">{svg('<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>')}</div><h2>Ask Aegis</h2><span className="cnt">{r.contract_id ? "this contract" : "portfolio"}</span></div>
            <div className="cb chat">
              <div className="log">
                {chat.length === 0 ? (
                  <div className="hint">Ask about this request — its terms, risks, obligations, or how it compares to precedent.
                    <div className="chips">{["What are the key risks here?", "What obligations does this create?", "How does this compare to our standard terms?"].map((s) => <button key={s} className="chip" disabled={ask.isPending} onClick={() => { setChat((c) => [...c, { role: "you", text: s }]); ask.mutate(s); }}>{s}</button>)}</div>
                  </div>
                ) : chat.map((m, i) => (
                  <div key={i} className={`msg ${m.role}`}>
                    <div className="bub">{m.text}{m.role === "ai" && m.cites ? <span className="cites">· {m.cites} source{m.cites === 1 ? "" : "s"}</span> : null}</div>
                  </div>
                ))}
                {ask.isPending ? <div className="msg ai"><div className="bub dim">Thinking…</div></div> : null}
              </div>
              <div className="composer">
                <input className="cin" value={q} placeholder="Ask a question…" onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => { if (e.key === "Enter") sendChat(); }} />
                <button className="btn pri" disabled={!q.trim() || ask.isPending} onClick={sendChat}>{svg('<path d="M22 2 11 13M22 2l-7 20-4-9-9-4z"/>')}</button>
              </div>
            </div></div>
        </div>

        {/* right rail */}
        <div className="col rail2">
          {/* Your decision — the current step's call, risk-aware */}
          {atReviewGate && (
            <div className="card"><div className="ch"><div className="glyph flow">{svg('<circle cx="12" cy="12" r="9"/><path d="m9 12 2 2 4-4"/>')}</div><h2>Your decision</h2></div><div className="cb">
              <div className={`decide${gateCount > 0 ? " warn" : " ok"}`}>
                {gateCount > 0 ? <div className="dw">{svg('<path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9 2 18a2 2 0 0 0 1.7 3h16.6a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/>')}{critCount > 0 ? `${critCount} ${useDrivers ? "high" : "critical"} + ` : ""}{useDrivers ? "" : `${bySev.high.length} high `}risk{gateCount === 1 ? "" : "s"} open — review before approving</div>
                  : <div className="dw ok">{svg('<path d="M20 6 9 17l-5-5"/>')}No blocking risks — clear to advance</div>}
                <div className="db">
                  <button className="btn pri wide" disabled={busy} onClick={approveStep}>{svg('<path d="M20 6 9 17l-5-5"/>')}Approve &amp; advance →</button>
                  {curIdx > 0 ? <button className="btn wide" disabled={busy} onClick={() => returnStep.mutate({ note: backNote || "Sent back for rework" })}>{svg('<path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 5 5v3"/>')}Send back</button> : null}
                  <button className="btn wide" onClick={() => { commentRef.current?.focus(); commentRef.current?.scrollIntoView({ block: "center" }); }}>{svg('<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>')}Request changes / comment</button>
                </div>
              </div>
            </div></div>
          )}

          {/* Approval chain */}
          {(approvals ?? []).length > 0 && (
            <div className="card"><div className="ch"><h2>Approval chain</h2><span className="cnt">{(approvals ?? []).filter((a) => a.status === "approved").length}/{(approvals ?? []).length}</span></div><div className="cb">
              {(approvals ?? []).slice().sort((a, b) => a.step_order - b.step_order).map((a, i) => {
                const st = (a.status || "pending").toLowerCase();
                const cls = st === "approved" ? "ok" : st === "rejected" ? "no" : "pend";
                return (
                  <div key={a.approval_request_id ?? `rung-${i}`} className="appr"><div className="an">{ini(a.approver_label || "?")}</div>
                    <div className="al"><div className="n">{a.approver_label}</div><div className="r">step {a.step_order}{a.mode === "all" && a.needed ? ` · ${a.approvals ?? 0}/${a.needed}` : ""}</div></div>
                    <span className={`as ${cls}`}>{st === "approved" ? "approved" : st === "rejected" ? "rejected" : "waiting"}</span></div>
                );
              })}
            </div></div>
          )}

          {/* Screening */}
          {(() => {
            const sc = (r.screening ?? {}) as { counterparty?: string; sanctions?: { status?: string }; conflicts?: unknown[] };
            if (!sc || !sc.counterparty) return null;
            const sanc = sc.sanctions?.status ?? "unavailable";
            const conf = Array.isArray(sc.conflicts) ? sc.conflicts.length : 0;
            const good = (v: boolean) => (v ? "var(--good)" : "var(--crit)");
            return (
              <div className="card"><div className="ch"><h2>Screening</h2></div><div className="cb glance">
                <div className="row"><span className="k">Counterparty</span><span className="v">{sc.counterparty}</span></div>
                <div className="row"><span className="k">Sanctions</span><span className="v" style={{ color: sanc === "clear" ? "var(--good)" : sanc === "hit" ? "var(--crit)" : "var(--warn)" }}>{sanc}</span></div>
                <div className="row"><span className="k">Conflicts</span><span className="v" style={{ color: good(conf === 0) }}>{conf === 0 ? "none" : `${conf} flagged`}</span></div>
              </div></div>
            );
          })()}

          {canAct && (
            <div className="card"><div className="ch"><div className="glyph flow">{svg('<circle cx="12" cy="12" r="9"/><path d="m9 12 2 2 4-4"/>')}</div><h2>Actions</h2></div><div className="cb actions">
              {/* Turn the request into a contract (or adopt an attached draft). */}
              {!r.contract_id && docType ? (
                <div className="act">
                  <div className="lbl">Contract</div>
                  {hasAttachment ? (
                    <>
                      <button className="btn pri wide" disabled={busy} onClick={() => ingest.mutate()}>Use attached as the {docType} →</button>
                      <button className="btn wide" disabled={busy} onClick={() => draft.mutate()}>Approve &amp; draft fresh</button>
                    </>
                  ) : (
                    <button className="btn pri wide" disabled={busy} onClick={() => draft.mutate()}>Approve &amp; draft the {docType} →</button>
                  )}
                </div>
              ) : null}
              {/* Reassign the owner. */}
              <div className="act">
                <div className="lbl">Owner</div>
                <select className="sel" value={reassignTo} onChange={(e) => setReassignTo(e.target.value)}>
                  <option value="">{r.assigned_to_label ? `Current: ${r.assigned_to_label}` : "Unassigned — pick an owner"}</option>
                  {(assignees ?? []).filter((a) => a.id !== r.assigned_to_user_id).map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </select>
                <button className="btn wide" disabled={busy || !reassignTo} onClick={() => reassign.mutate(reassignTo)}>Reassign owner</button>
              </div>
            </div></div>
          )}

          <div className="card"><div className="ch"><h2>At a glance</h2></div><div className="cb glance">
            <div className="row"><span className="k">Raised by</span><span className="v">{r.requester_name ?? "—"}</span></div>
            <div className="row"><span className="k">Department</span><span className="v">{r.department ?? "—"}</span></div>
            <div className="row"><span className="k">Owner</span><span className="v">{r.assigned_to_label ?? "Unassigned"}</span></div>
            <div className="row"><span className="k">Stage</span><span className="v">{r.stage}</span></div>
            <div className="row"><span className="k">Priority</span><span className="v">{r.priority}</span></div>
            <div className="row"><span className="k">SLA</span><span className="v" style={{ color: stuck ? "var(--crit)" : r.sla_status === "at_risk" ? "var(--warn)" : "var(--good)" }}>{stuck ? "overdue" : r.sla_status === "at_risk" ? "at risk" : "on track"}</span></div>
          </div></div>

          <div className="card"><div className="ch"><h2>Documents</h2></div><div className="cb">
            {(docs ?? []).length === 0 ? <div className="dim" style={{ fontSize: 12 }}>No documents attached.</div> :
              (docs ?? []).map((d) => (
                <div key={d.id} className="docrow"><div className={`x ${/pdf/i.test(d.mime_type) ? "pdf" : "doc"}`}>{/pdf/i.test(d.mime_type) ? "PDF" : "DOC"}</div>
                  <div style={{ flex: 1, minWidth: 0 }}><div className="n" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{d.filename}</div><div className="m">{d.extracted_chars > 0 ? "parsed" : "attached"}</div></div></div>
              ))}
            {r.contract_id ? <Link href={`/contracts/${r.contract_id}`} className="btn wide" style={{ marginTop: 12 }}>{svg('<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>')}Open in CLM editor →</Link> : null}
          </div></div>

          <div className="card"><div className="ch"><h2>People</h2></div><div className="cb">
            <div className="person"><div className="av">{ini(r.requester_name ?? "R")}</div><div><div className="n">{r.requester_name ?? "Requester"}</div><div className="r">requester</div></div></div>
            {r.assigned_to_label ? <div className="person"><div className="av">{ini(r.assigned_to_label)}</div><div><div className="n">{r.assigned_to_label}</div><div className="r">owner</div></div></div> : null}
          </div></div>

          <div className="card"><div className="ch"><h2>Activity</h2></div><div className="cb">
            <ul className="trail">
              <li className="you"><span className="w">{r.requester_name ?? "Requester"}</span> · raised this request<div className="t">{r.created_at ?? r.submitted_at ?? ""}</div></li>
              {(handoffs ?? []).map((h) => (
                <li key={h.id} className={h.actor_type === "agent" ? "" : "cp"}><span className="w">{h.to_label ?? h.to_holder}</span> · {h.reason ?? "took custody"}<div className="t">{h.created_at ?? ""}</div></li>
              ))}
            </ul>
          </div></div>
        </div>
      </div>
    </div>
  );
}

const RO_CSS = `
.ro .lbl{font:600 10px var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3)}
.ro .ic{width:15px;height:15px;flex:none;stroke:currentColor;stroke-width:1.7;fill:none;stroke-linecap:round;stroke-linejoin:round}
.ro .rohd{padding:4px 0 14px;border-bottom:1px solid var(--border);margin-bottom:4px}
.ro .crumb{display:flex;align-items:center;gap:7px;font-size:12px;color:var(--ink-3);margin-bottom:8px}
.ro .crumb a{text-decoration:none;font-weight:600}
.ro .hrow{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.ro .hrow h1{font-size:17px;font-weight:660;margin:0;letter-spacing:-.015em}
.ro .ref{font:600 11px var(--mono);color:var(--ink-3)}
.ro .badges{display:flex;gap:6px;flex-wrap:wrap}
.ro .bdg{display:inline-flex;align-items:center;gap:5px;padding:2px 9px;border-radius:99px;font:600 11px var(--sans)}
.ro .bdg.type{background:var(--ext-soft);color:var(--ext)} .ro .bdg.val{background:var(--surface-2);color:var(--ink-2)}
.ro .bdg.phase{background:var(--accent-soft);color:var(--accent)} .ro .bdg.warn{background:var(--warn-soft);color:var(--warn)}
.ro .needsinfo{display:flex;gap:9px;margin-top:12px;padding:10px 12px;border-radius:9px;background:var(--warn-soft);border:1px solid color-mix(in srgb,var(--warn) 30%,var(--border))}
.ro .needsinfo .ic{width:16px;height:16px;color:var(--warn);flex:none;margin-top:1px}
.ro .needsinfo b{font:600 12.5px var(--sans);color:var(--warn)}
.ro .ni-items{display:flex;flex-wrap:wrap;gap:5px;margin-top:5px}
.ro .ni-chip{font:600 11px var(--sans);padding:2px 8px;border-radius:999px;background:var(--bg);border:1px solid color-mix(in srgb,var(--warn) 40%,var(--border));color:var(--ink-1)}
.ro .ni-hint{font:11px var(--sans);color:var(--ink-3);margin-top:5px}
.ro .btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;padding:8px 13px;border-radius:9px;border:1px solid var(--border-strong);background:var(--surface);color:var(--ink);font-weight:600;font-size:12.5px;text-decoration:none}
.ro .btn:hover{background:var(--surface-2)} .ro .btn.pri{background:var(--accent);border-color:var(--accent);color:var(--accent-ink)} .ro .btn.wide{width:100%}
.ro .flowstrip{margin-top:14px;display:flex;align-items:center;gap:18px;flex-wrap:wrap}
.ro .youare{flex:none;display:flex;align-items:center;gap:11px;padding:8px 13px;background:var(--accent-soft);border:1px solid color-mix(in srgb,var(--accent) 30%,var(--border));border-radius:11px}
.ro .youare.blocked{background:var(--warn-soft);border-color:color-mix(in srgb,var(--warn) 34%,var(--border))}
.ro .youare .ya-ic{width:30px;height:30px;border-radius:8px;background:var(--accent);color:var(--accent-ink);display:grid;place-items:center;flex:none} .ro .youare.blocked .ya-ic{background:var(--warn)}
.ro .youare .k{font:600 9.5px var(--sans);letter-spacing:.07em;text-transform:uppercase;color:var(--accent)} .ro .youare.blocked .k{color:var(--warn)}
.ro .youare .v{font-weight:700;font-size:14px;letter-spacing:-.01em} .ro .youare .m{font-size:11.5px;color:var(--ink-2)}
.ro .ptrack{display:flex;align-items:center;overflow-x:auto;padding:2px 0}
.ro .ptrack>.ph{gap:0}
.ro .ph{display:flex;align-items:center;gap:8px;flex:none}
.ro .phn{width:22px;height:22px;border-radius:50%;display:grid;place-items:center;flex:none;font:700 9px var(--mono);background:var(--surface);border:2px solid var(--border-strong);color:var(--ink-3)}
.ro .ph.done .phn{background:var(--good);border-color:var(--good);color:#fff}
.ro .ph.cur .phn{background:var(--accent);border-color:var(--accent);color:#fff;box-shadow:0 0 0 4px var(--accent-soft)}
.ro .ph.cur.blk .phn{background:var(--warn);border-color:var(--warn);box-shadow:0 0 0 4px var(--warn-soft)}
.ro .phl{font-size:11.5px;font-weight:600;color:var(--ink-3);white-space:nowrap}
.ro .ph.done .phl,.ro .ph.cur .phl{color:var(--ink)} .ro .ph.cur .phl{color:var(--accent)}
.ro .phc{width:26px;height:2px;background:var(--border);margin:0 9px;flex:none} .ro .phc.done{background:var(--good)}
.ro .body{display:grid;grid-template-columns:1fr 316px;gap:20px;padding:18px 0 20px}
.ro .col{min-width:0;display:flex;flex-direction:column;gap:16px}
.ro .card{border:1px solid var(--border);border-radius:13px;background:var(--surface);box-shadow:var(--shadow)}
.ro .card>.ch{display:flex;align-items:center;gap:9px;padding:12px 16px;border-bottom:1px solid var(--border)}
.ro .card>.ch h2{font-size:13px;font-weight:660;margin:0} .ro .card>.ch .cnt{font:700 10px var(--mono);background:var(--surface-2);color:var(--ink-2);border-radius:99px;padding:1px 7px}
.ro .card>.cb{padding:15px 16px}
.ro .glyph{width:26px;height:26px;border-radius:7px;display:grid;place-items:center;flex:none;background:var(--surface-2);color:var(--ink-2)}
.ro .glyph.warn{background:var(--warn-soft);color:var(--warn)} .ro .glyph.flow{background:var(--accent-soft);color:var(--accent)}
.ro .quote{padding:12px 14px;background:var(--inset);border:1px solid var(--border);border-left:3px solid var(--border-strong);border-radius:9px;color:var(--ink-2);font-size:12.5px;line-height:1.65}
.ro .facts{display:grid;grid-template-columns:1fr 1fr;gap:11px 18px;margin-top:14px}
.ro .facts .f .k{font:600 10px var(--sans);text-transform:uppercase;letter-spacing:.05em;color:var(--ink-3)} .ro .facts .f .v{font-weight:600;margin-top:2px}
.ro .confline{display:flex;align-items:center;gap:8px;margin-top:12px;font-size:12px;color:var(--ink-2)}
.ro .track{flex:none;height:5px;width:90px;border-radius:3px;background:var(--surface-2);overflow:hidden} .ro .track .fill{height:100%}
.ro .flowmeta{font-size:12.5px;color:var(--ink-2);line-height:1.6}
.ro .steps{margin-top:4px}
.ro .step{position:relative;padding:0 0 4px 30px}
.ro .step::before{content:"";position:absolute;left:9px;top:22px;bottom:-4px;width:2px;background:var(--border)} .ro .step:last-child::before{display:none}
.ro .snode{position:absolute;left:2px;top:3px;width:18px;height:18px;border-radius:50%;display:grid;place-items:center;font:700 9px var(--mono);background:var(--surface);border:2px solid var(--border-strong);color:var(--ink-3)}
.ro .step.done .snode{background:var(--good);border-color:var(--good);color:#fff}
.ro .step.cur .snode{background:var(--accent);border-color:var(--accent);color:#fff;box-shadow:0 0 0 3px var(--accent-soft)}
.ro .step.warn .snode{background:var(--warn);border-color:var(--warn);color:#fff}
.ro .shd{display:flex;align-items:center;gap:9px;padding:2px 0 10px}
.ro .sname{font-weight:650;font-size:13px} .ro .step.todo .sname{color:var(--ink-3);font-weight:600}
.ro .sbadge{font:600 10px var(--sans);padding:1px 8px;border-radius:99px}
.ro .sbadge.done{background:var(--good-soft);color:var(--good)} .ro .sbadge.cur{background:var(--accent-soft);color:var(--accent)} .ro .sbadge.warn{background:var(--warn-soft);color:var(--warn)}
.ro .stype{font:600 10px var(--sans);color:var(--ink-3);text-transform:uppercase;letter-spacing:.05em}
.ro .sassign{display:flex;align-items:center;gap:6px;margin:2px 0 6px;font-size:11.5px;color:var(--ink-2)} .ro .sassign .ic{width:12px;height:12px;color:var(--ink-3)} .ro .sassign b{font-weight:650;color:var(--ink)}
.ro .sresult{margin:2px 0 8px;padding:9px 11px;border:1px solid var(--border);border-radius:9px;background:var(--inset);display:grid;grid-template-columns:auto 1fr;gap:4px 12px}
.ro .sresult .rr{display:contents} .ro .sresult .rk{font:600 10px var(--sans);text-transform:uppercase;letter-spacing:.04em;color:var(--ink-3);white-space:nowrap} .ro .sresult .rv{font-size:12px;color:var(--ink)}
.ro .cin{padding:8px 11px;border:1px solid var(--border-strong);border-radius:9px;background:var(--surface);color:var(--ink);font:500 12.5px var(--sans)}
.ro .sendback{margin-top:14px;padding:12px;border:1px dashed var(--border-strong);border-radius:10px;display:flex;flex-direction:column;gap:8px}
.ro .sendback .lbl,.ro .wfcomments .lbl{font:600 10px var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3)}
.ro .wfcomments{margin-top:14px;border-top:1px solid var(--border);padding-top:12px;display:flex;flex-direction:column;gap:8px}
.ro .wfcomments .clist{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:9px}
.ro .wfcomments .clist li{padding:8px 11px;border:1px solid var(--border);border-radius:9px;background:var(--surface-2)} .ro .wfcomments .clist li.ret{border-color:color-mix(in srgb,var(--warn) 34%,var(--border));background:var(--warn-soft)}
.ro .wfcomments .cmeta{font-size:11px;color:var(--ink-2);margin-bottom:3px} .ro .wfcomments .cmeta b{color:var(--ink)} .ro .wfcomments .cret{color:var(--warn);font-weight:600}
.ro .wfcomments .ctext{font-size:12.5px;color:var(--ink);line-height:1.55;white-space:pre-wrap}
.ro .composer{display:flex;gap:8px} .ro .composer .cin{flex:1;padding:8px 11px;border:1px solid var(--border-strong);border-radius:9px;background:var(--surface);color:var(--ink);font:500 12.5px var(--sans)}
.ro .ministep{margin-left:auto;display:inline-flex;align-items:center;gap:5px;padding:3px 9px;border-radius:7px;border:1px solid var(--accent);background:var(--accent);color:var(--accent-ink);font:600 11px var(--sans);cursor:pointer}
.ro .ministep[disabled]{opacity:.5;pointer-events:none} .ro .ministep .ic{width:12px;height:12px}
.ro .ministep.doorway{text-decoration:none}
.ro .ministep.ghost{margin-left:6px;background:transparent;color:var(--accent)}
.ro .spin{gap:7px;opacity:.75;pointer-events:none} .ro .spin .ic{animation:ro-spin 1s linear infinite}
@keyframes ro-spin{to{transform:rotate(360deg)}}
.ro .chat{display:flex;flex-direction:column;gap:10px}
.ro .chat .log{display:flex;flex-direction:column;gap:9px;max-height:320px;overflow-y:auto}
.ro .chat .hint{font-size:12.5px;color:var(--ink-2);line-height:1.6}
.ro .chat .chips{display:flex;flex-direction:column;gap:6px;margin-top:10px}
.ro .chat .chip{text-align:left;padding:7px 11px;border:1px solid var(--border);border-radius:9px;background:var(--surface-2);color:var(--ink-2);font:500 12px var(--sans);cursor:pointer}
.ro .chat .chip:hover{background:var(--accent-soft);color:var(--accent);border-color:color-mix(in srgb,var(--accent) 30%,var(--border))}
.ro .chat .msg{display:flex} .ro .chat .msg.you{justify-content:flex-end}
.ro .chat .bub{max-width:82%;padding:9px 12px;border-radius:12px;font-size:12.5px;line-height:1.6;white-space:pre-wrap}
.ro .chat .msg.you .bub{background:var(--accent);color:var(--accent-ink);border-bottom-right-radius:4px}
.ro .chat .msg.ai .bub{background:var(--surface-2);color:var(--ink);border:1px solid var(--border);border-bottom-left-radius:4px}
.ro .chat .bub.dim{color:var(--ink-3)} .ro .chat .cites{margin-left:6px;font-size:10.5px;color:var(--ink-3)}
.ro .chat .composer{display:flex;gap:8px}
.ro .chat .cin{flex:1;padding:9px 12px;border:1px solid var(--border-strong);border-radius:9px;background:var(--surface);color:var(--ink);font:500 12.5px var(--sans)}
.ro .chat .composer .btn.pri{padding:9px 12px} .ro .chat .composer .btn .ic{margin:0}
.ro .cactions{display:flex;gap:8px;flex-wrap:wrap}
.ro .actions{display:flex;flex-direction:column;gap:16px}
.ro .act{display:flex;flex-direction:column;gap:8px}
.ro .act .lbl{font:600 10px var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3)}
.ro .btn[disabled]{opacity:.5;pointer-events:none}
.ro .sel{width:100%;padding:8px 11px;border:1px solid var(--border-strong);border-radius:9px;background:var(--surface);color:var(--ink);font:600 12.5px var(--sans)}
.ro .glance .row{display:flex;justify-content:space-between;gap:10px;padding:8px 0;border-bottom:1px solid var(--border);font-size:12.5px} .ro .glance .row:last-child{border-bottom:0}
.ro .glance .k{color:var(--ink-3)} .ro .glance .v{font-weight:600;text-align:right}
.ro .person{display:flex;align-items:center;gap:9px;padding:8px 0}
.ro .person .av{width:28px;height:28px;border-radius:50%;background:var(--accent-soft);color:var(--accent);display:grid;place-items:center;font-weight:700;font-size:11px;flex:none}
.ro .person .n{font-weight:600;font-size:12.5px} .ro .person .r{font-size:11px;color:var(--ink-3)}
.ro .docrow{display:flex;align-items:center;gap:9px;padding:9px 0;border-bottom:1px solid var(--border)} .ro .docrow:last-of-type{border-bottom:0}
.ro .docrow .x{width:28px;height:28px;border-radius:7px;display:grid;place-items:center;font:700 8.5px var(--sans);flex:none}
.ro .docrow .x.pdf{background:var(--crit-soft);color:var(--crit)} .ro .docrow .x.doc{background:var(--accent-soft);color:var(--accent)}
.ro .docrow .n{font-weight:600;font-size:12.5px} .ro .docrow .m{font-size:11px;color:var(--ink-3)}
.ro .trail{list-style:none;margin:0;padding:0}
.ro .trail li{position:relative;padding:0 0 13px 20px;font-size:12px}
.ro .trail li::before{content:"";position:absolute;left:4px;top:3px;width:8px;height:8px;border-radius:50%;background:var(--accent);box-shadow:0 0 0 3px var(--surface)}
.ro .trail li::after{content:"";position:absolute;left:7.5px;top:11px;bottom:0;width:1px;background:var(--border)} .ro .trail li:last-child::after{display:none}
.ro .trail li.you::before{background:var(--good)} .ro .trail li.cp::before{background:var(--ext)}
.ro .trail .w{font-weight:650;color:var(--ink)} .ro .trail .t{color:var(--ink-3);font-size:10.5px}
.ro .dim{color:var(--ink-3)}
.ro .analyzing{display:flex;align-items:center;gap:9px;font-size:12.5px;color:var(--ink-2)} .ro .analyzing .ic{width:15px;height:15px;color:var(--accent);animation:ro-spin 1s linear infinite}
.ro .risktop{display:flex;align-items:center;gap:14px;padding:2px 0 8px}
.ro .rgauge{flex:none;width:50px;height:50px;border-radius:50%;display:grid;place-items:center;font:800 16px var(--mono);color:#fff}
.ro .rmeta .rb{font-weight:700;font-size:14px} .ro .rmeta .rc{font-size:11.5px;color:var(--ink-2);margin-top:1px}
.ro .rsum{padding:11px 13px;background:var(--inset);border:1px solid var(--border);border-radius:9px;font-size:12.5px;line-height:1.6;color:var(--ink);margin:0 0 12px}
.ro .sevgroup{margin-bottom:8px}
.ro .sevhead{display:flex;align-items:center;gap:8px;width:100%;background:none;border:0;padding:4px 0;cursor:pointer} .ro .sevhead .chev{color:var(--ink-3);font-size:11px}
.ro .sevpill{display:inline-flex;align-items:center;gap:5px;padding:3px 10px;border-radius:99px;font:700 11px var(--sans);text-transform:capitalize}
.ro .sev-critical{background:#fdecec;color:#b42318} .ro .sev-high{background:var(--crit-soft);color:var(--crit)} .ro .sev-med{background:var(--warn-soft);color:var(--warn)} .ro .sev-low{background:var(--surface-2);color:var(--ink-2)}
.ro .risk{border:1px solid var(--border);border-left:3px solid var(--border-strong);border-radius:9px;padding:10px 12px;margin:0 0 8px}
.ro .risk.crit{border-left-color:#b42318} .ro .risk.high{border-left-color:var(--crit)} .ro .risk.med{border-left-color:var(--warn)}
.ro .risk .rh{display:flex;align-items:center;gap:8px;margin-bottom:5px} .ro .risk .rt{font-weight:650;font-size:12.5px;text-transform:capitalize}
.ro .risk .ri{font-size:12px;color:var(--ink-2);line-height:1.55}
.ro .risk .rq{margin:7px 0;padding:7px 10px;border-left:2px solid var(--border-strong);background:var(--inset);font-size:11.5px;font-style:italic;color:var(--ink-2);line-height:1.5}
.ro .risk .rfix{display:flex;gap:6px;font-size:12px;color:var(--good);line-height:1.5} .ro .risk .rfix .ic{width:13px;height:13px;flex:none;margin-top:2px} .ro .risk .rfix b{color:var(--ink);font-weight:600}
.ro .decide{border-radius:10px;padding:12px} .ro .decide.warn{border:1px solid color-mix(in srgb,var(--warn) 34%,var(--border));background:var(--warn-soft)} .ro .decide.ok{border:1px solid color-mix(in srgb,var(--good) 30%,var(--border));background:var(--good-soft)}
.ro .decide .dw{display:flex;align-items:flex-start;gap:7px;font-size:12.5px;color:var(--warn);font-weight:600;margin-bottom:10px;line-height:1.4} .ro .decide .dw.ok{color:var(--good)} .ro .decide .dw .ic{width:15px;height:15px;flex:none;margin-top:1px}
.ro .decide .db{display:flex;flex-direction:column;gap:7px}
.ro .appr{display:flex;align-items:center;gap:9px;padding:8px 0;border-bottom:1px solid var(--border)} .ro .appr:last-child{border-bottom:0}
.ro .appr .an{width:26px;height:26px;border-radius:50%;background:var(--surface-2);display:grid;place-items:center;font:700 10px var(--mono);color:var(--ink-2);flex:none}
.ro .appr .al{flex:1;min-width:0} .ro .appr .al .n{font-weight:600;font-size:12.5px} .ro .appr .al .r{font-size:11px;color:var(--ink-3)}
.ro .appr .as{font:600 10px var(--sans);padding:2px 8px;border-radius:99px;flex:none} .ro .as.ok{background:var(--good-soft);color:var(--good)} .ro .as.pend{background:var(--surface-2);color:var(--ink-3)} .ro .as.no{background:var(--crit-soft);color:var(--crit)}
@media (max-width:1080px){.ro .body{grid-template-columns:1fr}.ro .rail2{order:-1}}
`;
