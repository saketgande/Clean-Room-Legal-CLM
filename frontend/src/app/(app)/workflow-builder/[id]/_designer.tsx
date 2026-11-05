"use client";

// Workflow Designer (Screen 6) — a full-screen visual flow builder. A vertical
// node canvas (each step: icon + assigned team + SLA + outcome chips, with
// per-step conditions) and a right inspector to edit the selected step or the
// flow's settings. Wired to workflowsApi (list / get / create / update) + rolesApi.
// Scoped under `.wf`. The engine is sequential, so steps render linearly.

import { Fragment, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { intakeApi, workflowsApi, usersApi } from "@/lib/endpoints";

import { useToast } from "@/components/toast";
import type { LifecycleStage, Workflow, WorkflowCondition, WorkflowStepType } from "@/lib/types";
import { useRequestForms } from "../../intake/_agreement-forms";
import { AMOUNT_OPS, CHOICE_OPS, DATE_OPS, OP_LABEL as COND_OP_LABEL, overlaps, pinnedRequestTypes, questionsFor, typeKey, typeOptions, whenText } from "@/lib/workflow-types";

const ICON = {
  back: '<path d="M19 12H5M12 19l-7-7 7-7"/>', up: '<path d="m18 15-6-6-6 6"/>', down: '<path d="m6 9 6 6 6-6"/>',
  copy: '<rect x="9" y="9" width="13" height="13" rx="2"/><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"/>',
  warn: '<path d="M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/><path d="M12 9v4M12 17h.01"/>',
  parallel: '<path d="M8 3v3a2 2 0 0 1-2 2H3M16 3v3a2 2 0 0 0 2 2h3M8 21v-3a2 2 0 0 0-2-2H3M16 21v-3a2 2 0 0 1 2-2h3"/>',
  plus: '<path d="M12 5v14M5 12h14"/>', trash: '<path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/>',
  clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', user: '<path d="M20 21a8 8 0 0 0-16 0"/><circle cx="12" cy="7" r="4"/>',
  back2: '<path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 5 5v3"/>',
};
const svg = (p: string) => <svg className="ic" viewBox="0 0 24 24" dangerouslySetInnerHTML={{ __html: p }} />;

type Cond = { field: string; op: string; value: string };
type Step = { id?: string; type: WorkflowStepType; name: string; config: Record<string, unknown>; parallel?: boolean; cond?: Cond | null; stage?: LifecycleStage };

// The contract lifecycle every workflow's steps sit under (mirrors backend
// app/workflows/stages.py, which has the final say when a workflow is saved).
const STAGES: { key: LifecycleStage; label: string }[] = [
  { key: "intake", label: "Intake" }, { key: "drafting", label: "Drafting" }, { key: "review", label: "Review" },
  { key: "approval", label: "Approval" }, { key: "signature", label: "Signature" }, { key: "active", label: "Active" },
  { key: "closed", label: "Closed" },
];
/** What happens in each stage whatever the workflow says: the app does these itself (see workflows/lifecycle_rows.py).
 *  They are shown beside the workflow's own steps so the stage reads as the whole story. */
type SysStep = { name: string; kind: string; icon: string; detail: string; note?: string };
const SYSTEM_STEPS: Partial<Record<LifecycleStage, { where: "before" | "after"; steps: SysStep[] }>> = {
  intake: { where: "before", steps: [
    { name: "Request filed", kind: "Form", icon: '<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>', detail: "The requester's form, email or chat message becomes a request with its own reference number." },
    { name: "AI triage", kind: "AI", icon: '<path d="M13 2L4 14h7l-2 8 9-12h-7z"/>', detail: "Aegis reads the request for its category, complexity and risk. For a form request this is advice only: the form decides the routing.", note: "Free-text requests (email, chat) are routed by the AI and confirmed by a person." },
    { name: "Owner assigned", kind: "Automatic", icon: '<path d="M20 21a8 8 0 0 0-16 0"/><circle cx="12" cy="7" r="4"/>', detail: "The intake team's rule (least loaded, or taking turns) gives the request an owner. The requester never owns their own request.", note: "Set the team and its rule in Admin → Teams." },
    { name: "Workflow chosen", kind: "Automatic", icon: '<circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="12" r="2.5"/><path d="M8.5 6H14a2 2 0 0 1 2 2v2M8.5 18H14a2 2 0 0 0 2-2v-2"/>', detail: "The workflow for this agreement type whose conditions fit is started: this one, when its conditions match.", note: "That is decided by the settings on the right when no step is selected." },
  ] },
  signature: { where: "after", steps: [
    { name: "Sent for signature", kind: "E-signature", icon: '<path d="M22 2 11 13"/><path d="M22 2l-7 20-4-9-9-4z"/>', detail: "The executed agreement goes out as an envelope to the signers on the counterparty and entity records." },
    { name: "Both sides sign", kind: "Signer", icon: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>', detail: "The counterparty and our authorised signatory each sign. A decline is recorded and sends the workflow back." },
  ] },
  active: { where: "after", steps: [
    { name: "Obligations read", kind: "AI", icon: '<path d="M9 11l3 3L22 4"/><path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11"/>', detail: "Signing queues the reading of the contract's duties. The Active stage shows how it went, and a failed read can be retried.", note: "The workflow's last step, “Activate”, only starts this; it never waits for it." },
    { name: "Renewal watch", kind: "Renewal", icon: '<path d="M21 12a9 9 0 1 1-3-6.7"/><path d="M21 3v6h-6"/>', detail: "The notice date and renewal window are counted from the contract's term. The owner decides to renew, renegotiate or terminate." },
  ] },
  closed: { where: "after", steps: [
    { name: "Contract ends", kind: "Automatic", icon: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>', detail: "On expiry, on termination, or after a declined renewal." },
    { name: "Retention", kind: "Records", icon: '<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>', detail: "Kept read-only and searchable for the retention period." },
  ] },
};
const STAGE_RANK = Object.fromEntries(STAGES.map((s, i) => [s.key, i])) as Record<LifecycleStage, number>;
const FIXED_STAGE: Partial<Record<WorkflowStepType, LifecycleStage>> = { clm_draft: "drafting", approval: "approval", signature: "signature" };
const allowedStages = (t: WorkflowStepType): LifecycleStage[] =>
  FIXED_STAGE[t] ? [FIXED_STAGE[t]!] : t === "counterparty" ? ["drafting", "review"] : STAGES.map((s) => s.key);
const stageLabel = (k?: LifecycleStage) => STAGES.find((s) => s.key === k)?.label ?? "—";
/** Keep steps in lifecycle order (stable), and break a parallel pair that now
 * spans two stages — parallel steps must share one. */
const inStageOrder = (ss: Step[]): Step[] =>
  ss.map((s, i) => ({ s, i }))
    .sort((a, b) => (STAGE_RANK[a.s.stage ?? "review"] - STAGE_RANK[b.s.stage ?? "review"]) || a.i - b.i)
    .map(({ s }) => s)
    .map((s, i, arr) => (s.parallel && (i === 0 || arr[i - 1].stage !== s.stage) ? { ...s, parallel: false } : s));
const OP_LABEL: Record<string, string> = { eq: "is", ne: "is not" };

const TYPE_META: Record<WorkflowStepType, { label: string; tone: string; icon: string; outcomes: string[] }> = {
  clm_draft: { label: "Prepare / draft", tone: "accent", icon: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>', outcomes: ["Complete"] },
  human_task: { label: "Review", tone: "ink", icon: '<circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/>', outcomes: ["Approve", "Send back"] },
  ai_task: { label: "Automated", tone: "warn", icon: '<path d="M13 2L4 14h7l-2 8 9-12h-7z"/>', outcomes: ["Auto-advance", "Escalate"] },
  approval: { label: "Approval", tone: "good", icon: '<path d="M12 3l8 4v5c0 5-3.5 8-8 9-4.5-1-8-4-8-9V7z"/><path d="M9.5 12l2 2 3-4"/>', outcomes: ["Approve", "Reject", "Escalate"] },
  signature: { label: "Signature", tone: "ext", icon: '<path d="M12 20h9"/><path d="M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4z"/>', outcomes: ["Signed"] },
  counterparty: { label: "Negotiation", tone: "ext", icon: '<path d="M17 1l4 4-4 4"/><path d="M3 11V9a4 4 0 0 1 4-4h14M7 23l-4-4 4-4"/><path d="M21 13v2a4 4 0 0 1-4 4H3"/>', outcomes: ["Approve", "Send back"] },
  notify: { label: "Info request", tone: "warn", icon: '<circle cx="12" cy="12" r="9"/><path d="M12 8h.01M11 12h1v4h1"/>', outcomes: ["Sent"] },
};
const TYPES = Object.keys(TYPE_META) as WorkflowStepType[];
const TYPE_DESC: Record<WorkflowStepType, string> = {
  clm_draft: "Someone produces a document — usually the first draft from a template.",
  human_task: "A reviewer reads the contract and can approve, send it back for changes, or ask for info.",
  ai_task: "No human — the system does it: an AI agent runs, or the workflow just moves on.",
  approval: "A decision-maker gives a yes/no — e.g. Finance signing off on value.",
  signature: "The named signatory signs the executed agreement.",
  counterparty: "Redlines go back and forth with the counterparty until both sides agree.",
  notify: "Pauses to collect a missing detail before the workflow continues.",
};

// Configurable per-step outcomes — the buttons a reviewer sees. "Send it back"
// outcomes drive the to-and-fro (return) edge, executed by the engine's
// return_run. Stored on config.outcomes; config.return_to sets where a
// send-back lands ("previous" or a specific earlier step index).
const OUTCOME_LABEL: Record<string, string> = {
  approve: "Approve", approve_c: "Approve with comments", sign: "Sign", complete: "Mark complete",
  request_changes: "Request changes (send back)", need_info: "Need info", reject: "Reject", decline: "Decline", escalate: "Escalate",
};
// Short labels for the compact chips on the step cards.
const OUTCOME_SHORT: Record<string, string> = {
  approve: "Approve", approve_c: "Approve+", sign: "Sign", complete: "Complete",
  request_changes: "Send back", need_info: "Need info", reject: "Reject", decline: "Decline", escalate: "Escalate",
};
const OUTCOME_GROUPS: { label: string; keys: string[] }[] = [
  { label: "What they can approve with", keys: ["approve", "approve_c", "sign", "complete"] },
  { label: "Send it back", keys: ["request_changes", "need_info"] },
  { label: "Stop or escalate", keys: ["reject", "decline", "escalate"] },
];
// Assignment model: a team from Admin → Teams (config.team_id) + how the person
// is picked, with a plain-English preview. The engine reads exactly these —
// there is no department list or name lookup behind them.
const ASSIGN_BY = ["Auto — least-loaded in team", "Auto — team head", "Specific person", "The requester"];
// An approval is decided by the team, not handed to one member.
const APPROVE_BY = ["Any one member", "All members must approve", "Specific person", "The requester"];
const ASSIGN_DESC: Record<string, string> = {
  "Auto — least-loaded in team": "Shared by the team’s own rule (fewest open items, or taking turns) in",
  "Auto — team head": "Always goes to the head of",
  "Any one member": "Any one member can approve —",
  "All members must approve": "Every member must approve —",
  "Specific person": "Always handled by the person you name.",
  "The requester": "Returns to whoever raised the request.",
};
const assignOptions = (t: WorkflowStepType) => (t === "approval" ? APPROVE_BY : ASSIGN_BY);
const OUTCOME_DEFAULTS: Record<WorkflowStepType, string[]> = {
  human_task: ["approve", "request_changes", "need_info", "escalate"],
  approval: ["approve", "reject", "escalate"],
  signature: ["sign", "decline"],
  clm_draft: ["complete"],
  counterparty: ["approve", "request_changes"],
  ai_task: [],
  notify: ["complete"],
};
const BACK_OUTCOMES = new Set(["request_changes", "need_info", "reject", "decline"]);
const outcomesOf = (s: Step): string[] => {
  const c = s.config.outcomes;
  return Array.isArray(c) ? (c as string[]) : OUTCOME_DEFAULTS[s.type] ?? [];
};
const hasBack = (s: Step) => outcomesOf(s).some((k) => k === "request_changes" || k === "need_info");

const cfgStr = (c: Record<string, unknown>, k: string) => (typeof c[k] === "string" || typeof c[k] === "number" ? String(c[k]) : "");
const condLabel = (s: Step): string | null => {
  const c = s.cond;
  if (!c || !c.field) return null;
  return `only when ${c.field} ${OP_LABEL[c.op] ?? c.op} ${c.value}`;
};

export function WorkflowDesigner({ flow, isNew }: { flow: Workflow | null; isNew: boolean }) {
  const router = useRouter();
  const qc = useQueryClient();
  const { notify } = useToast();
  const { data: allFlows } = useQuery({ queryKey: ["flows"], queryFn: workflowsApi.listFlows });
  const { data: users } = useQuery({ queryKey: ["users"], queryFn: () => usersApi.list() });
  const { data: teams } = useQuery({ queryKey: ["intake-teams"], queryFn: intakeApi.teams });
  const teamName = (id: string) => (teams ?? []).find((t) => t.id === id)?.name ?? "";

  const [name, setName] = useState(flow?.name ?? "");
  const [enabled, setEnabled] = useState(flow?.enabled ?? false);
  const [matchType, setMatchType] = useState(flow?.criteria.match_type ?? "");
  const [matchKeyword, setMatchKeyword] = useState(flow?.criteria.match_keyword ?? "");
  const [aiCondition, setAiCondition] = useState(flow?.criteria.ai_condition ?? "");
  // The agreement type this workflow is for, and when a request of that type gets it.
  const { data: requestForms } = useRequestForms();
  const types = typeOptions(requestForms);
  const [typeSel, setTypeSel] = useState<string>(flow ? typeKey(flow) ?? "" : "");
  useEffect(() => {
    // "+ New NDA workflow" on the list page opens the builder with its type set.
    if (!flow) { const t = new URLSearchParams(window.location.search).get("type"); if (t) setTypeSel(t); }
  }, [flow]);
  const [conds, setConds] = useState<WorkflowCondition[]>(flow?.criteria.conditions ?? []);
  const [addOpen, setAddOpen] = useState(false);
  const typeOpt = types.find((t) => t.key === typeSel) ?? null;
  const questions = typeOpt ? questionsFor(requestForms, typeOpt.form, typeOpt.agreement_type) : [];
  const qLabels = Object.fromEntries(questions.map((q) => [q.field, q.label]));
  const siblings = (allFlows ?? []).filter((f) => f.id !== flow?.id && f.enabled && typeKey(f) === typeSel && typeSel);
  const clash = siblings.find((f) => overlaps(conds, f.criteria.conditions));
  const freshCond = (field: string): WorkflowCondition => {
    const q = questions.find((x) => x.field === field)!;
    if (q.date) return { field, op: "within_days", value: 7 };
    return q.amount ? { field, op: "under", value: 1000000, currency: "INR" } : { field, op: "is", value: q.options[0] };
  };
  const patchCond = (i: number, p: Partial<WorkflowCondition>) => setConds((cs) => cs.map((c, j) => (j === i ? { ...c, ...p } : c)));
  const [evalOrder, setEvalOrder] = useState(String(flow?.eval_order ?? 100));
  const [steps, setSteps] = useState<Step[]>((flow?.steps ?? []).map((s) => ({ id: s.id, type: s.type, name: s.name, config: { ...s.config }, parallel: s.parallel ?? false, cond: s.cond ?? null, stage: s.stage })));
  const [sel, setSel] = useState<number | null>(null);
  const [tab, setTab] = useState<"setup" | "assign" | "outcomes" | "rules">("setup");
  const [busy, setBusy] = useState(false);
  // Where the "add a step" menu is open: the index the step would take, and its stage.
  const [picker, setPicker] = useState<{ at: number; stage: LifecycleStage; key: string } | null>(null);
  // What was last saved, to tell when there is something unsaved.
  const [baseline, setBaseline] = useState<string | null>(null);

  const patchStep = (i: number, p: Partial<Step>) => setSteps((ss) => ss.map((x, idx) => (idx === i ? { ...x, ...p } : x)));
  const patchCfg = (i: number, k: string, v: unknown) => setSteps((ss) => ss.map((x, idx) => (idx === i ? { ...x, config: { ...x.config, [k]: v } } : x)));
  // A new step joins the stage of the step above it (Review at the very start).
  const addAt = (i: number, stage: LifecycleStage, type: WorkflowStepType = "human_task") => {
    setSteps((ss) => { const n = [...ss]; n.splice(i, 0, { type, name: type === "human_task" ? "New step" : TYPE_META[type].label, config: {}, stage }); return n; });
    setSel(i); setTab("setup"); setPicker(null);
  };
  // Parallel steps share a stage: running beside the step above puts it in that
  // step's stage, if its type may sit there.
  const toggleParallel = (i: number) => {
    const s = steps[i], prev = steps[i - 1];
    if (s.parallel || !prev) { patchStep(i, { parallel: false }); return; }
    if (!allowedStages(s.type).includes(prev.stage ?? "review")) {
      notify(`${TYPE_META[s.type].label} steps can't run in ${stageLabel(prev.stage)} beside “${prev.name}”.`, "error");
      return;
    }
    patchStep(i, { parallel: true, stage: prev.stage });
  };
  // Changing a step's stage (or a type that fixes it) moves it into that stage's section.
  const moveToStage = (i: number, p: Partial<Step>) => setSteps((ss) => {
    const moved = { ...ss[i], ...p };
    const next = inStageOrder(ss.map((x, idx) => (idx === i ? moved : x)));
    setSel(next.indexOf(moved));
    return next;
  });
  const del = (i: number) => { setSteps((ss) => ss.filter((_, idx) => idx !== i)); setSel(null); };

  function body(pub?: boolean) {
    return {
      name: name.trim() || "Untitled workflow",
      description: flow?.description ?? null,
      enabled: pub ?? enabled,
      eval_order: Number(evalOrder) || 100,
      criteria: {
        ...flow?.criteria,
        // A typed workflow is picked by its type and conditions only; words and the
        // AI condition are for untyped (email/chat) ones, so neither can override a type pick.
        match_type: typeOpt ? null : matchType.trim() || null, match_keyword: typeOpt ? null : matchKeyword.trim() || null,
        ai_condition: typeOpt ? null : aiCondition.trim() || null,
        // Untyped workflows keep any request-type pin (e.g. General Legal Question);
        // choosing an agreement type replaces it.
        used_for: typeOpt
          ? [{ form: typeOpt.form, agreement_type: typeOpt.agreement_type }]
          : pinnedRequestTypes(flow).map((t) => ({ type_label: t })),
        conditions: typeOpt ? conds : [],
      },
      steps: steps.filter((s) => s.name.trim()).map((s, i) => ({ ...(s.id ? { id: s.id } : {}), type: s.type, name: s.name.trim(), config: s.config ?? {}, ...(s.parallel && i > 0 ? { parallel: true } : {}), ...(s.stage ? { stage: s.stage } : {}), ...(s.cond && s.cond.field.trim() ? { cond: { field: s.cond.field.trim(), op: s.cond.op || "eq", value: s.cond.value } } : {}) })),
    };
  }
  async function save(pub?: boolean) {
    if (!name.trim()) { notify("Give the workflow a name first.", "error"); return; }
    setBusy(true);
    try {
      const b = body(pub);
      if (pub) setEnabled(true);
      const saved = flow ? await workflowsApi.updateFlow(flow.id, b) : await workflowsApi.createFlow(b);
      qc.invalidateQueries({ queryKey: ["flows"] });
      setBaseline(JSON.stringify(b));
      notify(pub ? "Workflow published" : "Workflow saved", "success");
      if (isNew) router.replace(`/workflow-builder/${saved.id}`);
    } catch (e) { notify(e instanceof Error ? e.message : "Save failed", "error"); }
    finally { setBusy(false); }
  }

  const cur = sel != null ? steps[sel] : null;

  // Group consecutive parallel-flagged steps so they render as one
  // "Parallel · all must respond" block, matching the engine's execution model.
  const groups: number[][] = [];
  steps.forEach((s, i) => {
    if (s.parallel && i > 0 && groups.length) groups[groups.length - 1].push(i);
    else groups.push([i]);
  });

  const returnTargetIdx = (s: Step, i: number): number => {
    const rt = s.config.return_to;
    if (typeof rt === "number" && rt >= 0 && rt < i) return rt;
    return Math.max(0, i - 1); // "previous"
  };
  const returnLabel = (s: Step, i: number): string => {
    const idx = returnTargetIdx(s, i);
    return steps[idx]?.name || `step ${idx + 1}`;
  };

  // ---- helpers the canvas uses -------------------------------------------------
  const moveStep = (i: number, d: -1 | 1) => setSteps((ss) => {
    const j = i + d;
    if (j < 0 || j >= ss.length || (ss[j].stage ?? "review") !== (ss[i].stage ?? "review")) return ss;
    const n = [...ss];
    [n[i], n[j]] = [n[j], n[i]];
    n[0] = { ...n[0], parallel: false };
    setSel(j);
    return n;
  });
  const duplicate = (i: number) => {
    setSteps((ss) => { const n = [...ss]; const c = ss[i]; n.splice(i + 1, 0, { ...c, id: undefined, name: `${c.name} (copy)`, config: { ...c.config }, parallel: false }); return n; });
    setSel(i + 1); setTab("setup");
  };
  const canMove = (i: number, d: -1 | 1) => { const j = i + d; return j >= 0 && j < steps.length && (steps[j].stage ?? "review") === (steps[i].stage ?? "review"); };

  // Things worth fixing before this goes live, each tied to the step it is about.
  const issues: { i: number | null; msg: string }[] = [];
  steps.forEach((s, i) => {
    if (s.type === "approval" && !cfgStr(s.config, "team_id")) issues.push({ i, msg: `“${s.name || "Approval"}” has no team, so it would stall.` });
    if (!s.name.trim()) issues.push({ i, msg: `Step ${i + 1} has no name and will be dropped when you save.` });
    if (i === 0 && hasBack(s)) issues.push({ i, msg: `“${s.name}” can send back, but nothing comes before it.` });
    if (s.cond && !s.cond.field.trim()) issues.push({ i, msg: `“${s.name}” has a condition with no field.` });
    if (cfgStr(s.config, "assign_by") === "Specific person" && !cfgStr(s.config, "assignee_user_id")) issues.push({ i, msg: `“${s.name}” is set to a specific person, but none is chosen.` });
  });
  if (!steps.length) issues.push({ i: null, msg: "There are no steps yet." });
  if (clash) issues.push({ i: null, msg: `Could be picked together with “${clash.name}”: change a condition so they don't overlap.` });
  const issueAt = (i: number) => issues.filter((x) => x.i === i);
  const [showIssues, setShowIssues] = useState(false);
  // The side panel's width, set by dragging its edge; remembered between visits.
  const [inspW, setInspW] = useState(410);
  useEffect(() => { try { const v = Number(localStorage.getItem("wf-insp-w")); if (v >= 320 && v <= 680) setInspW(v); } catch { /* storage blocked */ } }, []);
  const workRef = useRef<HTMLDivElement>(null);
  const [dragging, setDragging] = useState(false);
  const widthFrom = (clientX: number) => { const r = workRef.current?.getBoundingClientRect(); return r ? Math.min(680, Math.max(320, Math.round(r.right - clientX))) : 410; };
  const saveW = (w: number) => { try { localStorage.setItem("wf-insp-w", String(w)); } catch { /* storage blocked */ } };
  // Stages the person has folded away.
  const [shut, setShut] = useState<Set<LifecycleStage>>(new Set());
  const [zoom, setZoom] = useState(1);
  const nudgeZoom = (d: number) => setZoom((z) => Math.min(1.4, Math.max(0.6, Math.round((z + d) * 10) / 10)));
  const toggleLane = (k: LifecycleStage) => setShut((p) => { const n = new Set(p); if (n.has(k)) n.delete(k); else n.add(k); return n; });
  // A built-in step the person clicked, to read what it does.
  const [sys, setSys] = useState<{ stage: LifecycleStage; i: number } | null>(null);
  useEffect(() => { if (sel != null) setSys(null); }, [sel]);

  const jsonNow = JSON.stringify(body());
  // Taken once the request forms have loaded, since the saved body depends on the agreement type they name.
  useEffect(() => { if (requestForms) setBaseline((b) => b ?? jsonNow); }, [jsonNow, requestForms]);
  const dirty = baseline !== null && jsonNow !== baseline;
  const saveRef = useRef(save);
  saveRef.current = save;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "s") { e.preventDefault(); void saveRef.current(); } };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  useEffect(() => {
    if (!dirty) return;
    const warn = (e: BeforeUnloadEvent) => { e.preventDefault(); };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [dirty]);

  const hotStage = cur ? (cur.stage ?? "review") : sys ? sys.stage : null;
  const slaTotal = steps.reduce((t, s) => t + (Number(cfgStr(s.config, "sla_hours")) || 0), 0);
  const approvalsN = steps.filter((s) => s.type === "approval").length;
  const filledStages = new Set(steps.map((s) => s.stage ?? "review")).size;

  /** The "add a step" button between two steps, and the menu of step kinds it opens. */
  const adder = (at: number, stage: LifecycleStage, key: string, small?: boolean) => {
    const open = picker?.key === key;
    const kinds = TYPES.filter((t) => allowedStages(t).includes(stage));
    return (
      <div className={`adder${small ? " sm" : ""}${open ? " open" : ""}`}>
        <button className="addbtn" aria-label={`Add a step in ${stageLabel(stage)}`} aria-expanded={open} onClick={() => setPicker(open ? null : { at, stage, key })}>{svg(ICON.plus)}</button>
        {open && (
          <>
            <div className="pickscrim" onClick={() => setPicker(null)} />
            <div className="picker" role="menu">
              <div className="pickhd">Add a step to {stageLabel(stage)}</div>
              {kinds.map((t) => (
                <button key={t} role="menuitem" className="pickrow" onClick={() => addAt(at, stage, t)}>
                  <span className={`tic t-${TYPE_META[t].tone}`}>{svg(TYPE_META[t].icon)}</span>
                  <span className="pt"><b>{TYPE_META[t].label}</b><em>{TYPE_DESC[t]}</em></span>
                </button>
              ))}
            </div>
          </>
        )}
      </div>
    );
  };

  /** The built-in steps of a stage: not the workflow's own, so they look different and move differently. */
  const sysBox = (stage: LifecycleStage, where: "before" | "after") => {
    const d = SYSTEM_STEPS[stage];
    if (!d || d.where !== where) return null;
    return (
      <div className={`sysbox ${where}`}>
        <div className="syshd">{svg('<circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .3 1.8l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-1.8-.3 1.7 1.7 0 0 0-1 1.5V21a2 2 0 0 1-4 0v-.1a1.7 1.7 0 0 0-1.1-1.5 1.7 1.7 0 0 0-1.8.3l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1a1.7 1.7 0 0 0 .3-1.8 1.7 1.7 0 0 0-1.5-1H3a2 2 0 0 1 0-4h.1a1.7 1.7 0 0 0 1.5-1.1 1.7 1.7 0 0 0-.3-1.8l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1a1.7 1.7 0 0 0 1.8.3H9a1.7 1.7 0 0 0 1-1.5V3a2 2 0 0 1 4 0v.1a1.7 1.7 0 0 0 1 1.5 1.7 1.7 0 0 0 1.8-.3l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1a1.7 1.7 0 0 0-.3 1.8V9a1.7 1.7 0 0 0 1.5 1H21a2 2 0 0 1 0 4h-.1a1.7 1.7 0 0 0-1.5 1z"/>')}Built-in · runs by itself</div>
        <div className="sysrows">
          {d.steps.map((x, k) => (
            <button key={x.name} className={`sysrow${sys?.stage === stage && sys.i === k ? " on" : ""}`} style={{ animationDelay: `${k * 90}ms` }} onClick={() => { setSel(null); setSys({ stage, i: k }); }}>
              <span className="sysic">{svg(x.icon)}</span>
              <span className="sysnm">{x.name}</span>
              <em>{x.kind}</em>
              <i className="sysdot" aria-hidden />
            </button>
          ))}
        </div>
      </div>
    );
  };

  const cardJsx = (i: number) => {
    const s = steps[i];
    const m = TYPE_META[s.type];
    const role = teamName(cfgStr(s.config, "team_id"));
    const sla = cfgStr(s.config, "sla_hours");
    const cl = condLabel(s);
    const warns = issueAt(i);
    return (
      <div className={`stepcard t-${m.tone}${sel === i ? " sel" : ""}${warns.length ? " warn" : ""}`} onClick={() => setSel(i)} role="button" tabIndex={0}
        onKeyDown={(e) => { if (e.key === "Enter") setSel(i); }}>
        <div className="ctrls" onClick={(e) => e.stopPropagation()}>
          <button title="Move up" disabled={!canMove(i, -1)} onClick={() => moveStep(i, -1)}>{svg(ICON.up)}</button>
          <button title="Move down" disabled={!canMove(i, 1)} onClick={() => moveStep(i, 1)}>{svg(ICON.down)}</button>
          {i > 0 && <button className={s.parallel ? "on" : ""} title={s.parallel ? "Make sequential" : "Run in parallel with the step above"} onClick={() => toggleParallel(i)}>{svg(ICON.parallel)}</button>}
          <button title="Duplicate" onClick={() => duplicate(i)}>{svg(ICON.copy)}</button>
          <button className="del" title="Delete" onClick={() => del(i)}>{svg(ICON.trash)}</button>
        </div>
        {cl ? <div className="cond">{svg(ICON.parallel)}{cl}</div> : null}
        <div className="top">
          <div className={`tic t-${m.tone}`}>{svg(m.icon)}</div>
          <div className="snm"><span className="nm">{s.name || "Untitled step"}</span><span className="kind">{m.label}</span></div>
          <span className="idx">{i + 1}</span>
        </div>
        <div className="meta">
          {role ? <span className="chip dept">{svg(ICON.user)}{role}</span> : (s.type !== "ai_task" ? <span className="chip nodept">{svg(ICON.user)}Request owner</span> : null)}
          {sla ? <span className="chip sla">{svg(ICON.clock)}{sla}h</span> : null}
          {outcomesOf(s).map((k) => <span key={k} className={`chip out${BACK_OUTCOMES.has(k) ? " neg" : ""}`}>{OUTCOME_SHORT[k] ?? k}</span>)}
        </div>
        {hasBack(s) ? <div className="backedge">{svg(ICON.back2)}sends back to {returnLabel(s, i)}</div> : null}
        {warns.length > 0 && <div className="swarn">{svg(ICON.warn)}{warns[0].msg}</div>}
      </div>
    );
  };

  const TAB_KEYS = ["setup", "assign", "outcomes", "rules"] as const;

  return (
    <div className="wf">
      <style dangerouslySetInnerHTML={{ __html: WF_CSS }} />
      <div className="wfapp">
        <div className="wfmain">
          {/* top bar */}
          <div className="topbar">
            <button className="backbtn" onClick={() => router.push("/workflow-builder")} title="Back to all workflows">{svg(ICON.back)}<span>Workflows</span></button>
            <span className="tsep" />
            <select className="flowsel" aria-label="Switch workflow" value={flow?.id ?? "new"} onChange={(e) => router.push(`/workflow-builder/${e.target.value}`)}>
              {isNew ? <option value="new">New workflow…</option> : null}
              {(allFlows ?? []).map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
            </select>
            <input className="wftitle" value={name} placeholder="Name this workflow" onChange={(e) => setName(e.target.value)} aria-label="Workflow name" />
            {flow ? <span className="bdg ver">v{flow.version}</span> : null}
            <span className={`bdg ${enabled ? "active" : "draft"}`}><i />{enabled ? "Published" : "Draft"}</span>
            {dirty && <span className="bdg unsaved"><i />Unsaved changes</span>}
            <button className={`chkpill${issues.length ? " bad" : " ok"}`} onClick={() => setShowIssues((v) => !v)} aria-expanded={showIssues}>
              {svg(issues.length ? ICON.warn : '<path d="M20 6 9 17l-5-5"/>')}{issues.length ? `${issues.length} to fix` : "All checks pass"}
            </button>
            <button className="tbtn" disabled={busy} onClick={() => notify("Test run needs a sample request — start one from a ticket.", "success")}>{svg('<path d="M5 3l14 9-14 9z"/>')}Test run</button>
            <button className="tbtn" disabled={busy || !dirty} onClick={() => save()} title="Save (Ctrl/⌘ S)">{svg('<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/>')}{busy ? "Saving…" : "Save"}</button>
            <button className="tbtn pri" disabled={busy} onClick={() => save(true)}>{svg('<path d="M20 6 9 17l-5-5"/>')}{enabled && !dirty ? "Published" : "Publish"}</button>
          </div>
          {showIssues && (
            <div className="issues" role="status">
              {issues.length === 0 ? <p className="allgood">{svg('<path d="M20 6 9 17l-5-5"/>')}Everything checks out: every approval has a team and nothing overlaps.</p> : issues.map((x, k) => (
                <button key={k} className="issue" onClick={() => { if (x.i != null) { setSel(x.i); setTab(x.msg.includes("team") || x.msg.includes("person") ? "assign" : x.msg.includes("condition") ? "rules" : "setup"); } else setSel(null); setShowIssues(false); }}>{svg(ICON.warn)}<span>{x.msg}</span><em>{x.i != null ? `Step ${x.i + 1}` : "Settings"}</em></button>
              ))}
            </div>
          )}

          <div className="work" ref={workRef} style={{ ["--iw" as string]: `${inspW}px` }}>
            {/* canvas */}
            <div className="canvas" onClick={(e) => { if (e.target === e.currentTarget) setSel(null); }}>
              <div className="sumbar">
                <span><b>{steps.length}</b> step{steps.length === 1 ? "" : "s"}</span>
                <span><b>{filledStages}</b> of {STAGES.length} stages</span>
                <span><b>{approvalsN}</b> approval{approvalsN === 1 ? "" : "s"}</span>
                <span><b>{slaTotal}</b>h of SLA in all</span>
              </div>
              <div className="flowcol" style={{ zoom }} onClick={(e) => { if (e.target === e.currentTarget) setSel(null); }}>
                <div className="node start">{svg('<circle cx="12" cy="12" r="9"/><path d="m9 12 2 2 4-4"/>')}Request confirmed</div>
                {/* Every stage shows, empty or not, so a step can be added to any of them. */}
                {STAGES.map((st, si) => {
                  const gs = groups.filter((g) => (steps[g[0]].stage ?? "review") === st.key);
                  const endOfStage = steps.filter((x) => STAGE_RANK[x.stage ?? "review"] <= si).length;
                  return (
                    <Fragment key={st.key}>
                      <div className={`lane${gs.length || SYSTEM_STEPS[st.key] ? "" : " empty"}${shut.has(st.key) ? " shut" : ""}${hotStage === st.key ? " hot" : ""}`} style={{ animationDelay: `${si * 70}ms`, ["--si" as string]: si }}>
                        <button type="button" className="stagehd" onClick={() => toggleLane(st.key)} aria-expanded={!shut.has(st.key)} title={shut.has(st.key) ? "Show this stage" : "Fold this stage away"}>
                          <span className="num">{si + 1}</span><b>{st.label}</b>
                          <span className="cnt">{(() => { const n = gs.reduce((t, g) => t + g.length, 0); const d = SYSTEM_STEPS[st.key]?.steps.length ?? 0; return `${n ? `${n} step${n === 1 ? "" : "s"}` : "no steps"}${d ? ` · ${d} built-in` : ""}`; })()}</span>
                          <span className="chev">{svg(ICON.down)}</span>
                        </button>
                        <div className="lanebody"><div className="lanein">
                          {sysBox(st.key, "before")}
                          {gs.map((g) => (
                            <Fragment key={`g${steps[g[0]].id ?? g[0]}`}>
                              {adder(g[0], st.key, `b${g[0]}`, true)}
                              {g.length === 1 ? <div className="cardwrap">{cardJsx(g[0])}</div> : (
                                <div className="pgroup">
                                  <div className="pghd">{svg(ICON.parallel)}Parallel · all must respond</div>
                                  <div className="pgsteps">{g.map((i) => <Fragment key={steps[i].id ?? i}>{cardJsx(i)}</Fragment>)}</div>
                                </div>
                              )}
                            </Fragment>
                          ))}
                          {adder(endOfStage, st.key, `e${st.key}`, gs.length > 0)}
                          {sysBox(st.key, "after")}
                        </div></div>
                      </div>
                    </Fragment>
                  );
                })}
                <div className="node end">{svg('<path d="M20 6 9 17l-5-5"/>')}Complete</div>
              </div>
              <div className="dock" role="toolbar" aria-label="Canvas tools">
                <button onClick={() => nudgeZoom(-0.1)} disabled={zoom <= 0.6} aria-label="Zoom out" title="Zoom out">{svg('<path d="M5 12h14"/>')}</button>
                <span className="zv" aria-live="polite">{Math.round(zoom * 100)}%</span>
                <button onClick={() => nudgeZoom(0.1)} disabled={zoom >= 1.4} aria-label="Zoom in" title="Zoom in">{svg(ICON.plus)}</button>
                <button onClick={() => setZoom(1)} aria-label="Reset zoom" title="Reset zoom">{svg('<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>')}</button>
                <i className="sep" />
                <button className={shut.size ? "on" : ""} onClick={() => setShut(shut.size ? new Set() : new Set(STAGES.map((x) => x.key)))} aria-label={shut.size ? "Open every stage" : "Fold every stage"} title={shut.size ? "Open every stage" : "Fold every stage"}>{svg('<path d="m7 15 5 5 5-5M7 9l5-5 5 5"/>')}</button>
                <i className="sep" />
                <button className={`dchk${issues.length ? " bad" : ""}`} onClick={() => setShowIssues((v) => !v)} title="Checks">{svg(issues.length ? ICON.warn : '<path d="M20 6 9 17l-5-5"/>')}<span>{issues.length ? `${issues.length} to fix` : "All good"}</span></button>
              </div>
            </div>

            <div
              className={`resizer${dragging ? " on" : ""}`}
              role="separator" aria-orientation="vertical" aria-label="Resize the side panel" aria-valuenow={inspW} aria-valuemin={320} aria-valuemax={680} tabIndex={0}
              title="Drag to resize · double-click to reset"
              onPointerDown={(e) => { (e.currentTarget as HTMLElement).setPointerCapture(e.pointerId); setDragging(true); }}
              onPointerMove={(e) => { if (dragging) setInspW(widthFrom(e.clientX)); }}
              onPointerUp={(e) => { (e.currentTarget as HTMLElement).releasePointerCapture(e.pointerId); setDragging(false); saveW(widthFrom(e.clientX)); }}
              onDoubleClick={() => { setInspW(410); saveW(410); }}
              onKeyDown={(e) => { if (e.key === "ArrowLeft" || e.key === "ArrowRight") { e.preventDefault(); const w = Math.min(680, Math.max(320, inspW + (e.key === "ArrowLeft" ? 16 : -16))); setInspW(w); saveW(w); } }}
            ><span className="grip" aria-hidden /></div>

            {/* inspector */}
            <div className="insp">
              {cur ? (
                <div key={`step-${sel}`} className="inspin">
                  <div className="ih">
                    <div className={`tic t-${TYPE_META[cur.type].tone}`}>{svg(TYPE_META[cur.type].icon)}</div>
                    <div className="iht"><b>{TYPE_META[cur.type].label} step</b><em>Step {sel! + 1} · {stageLabel(cur.stage)}</em></div>
                    <button className="xclose" onClick={() => setSel(null)} aria-label="Close step configuration" title="Close">{svg('<path d="M18 6 6 18M6 6l12 12"/>')}</button>
                  </div>
                  <div className="titlefld"><input className="inp" value={cur.name} onChange={(e) => patchStep(sel!, { name: e.target.value })} placeholder="Step name" aria-label="Step name" /></div>
                  <div className="itabs" style={{ ["--ti" as string]: TAB_KEYS.indexOf(tab) }}>
                    <span className="itab-bar" aria-hidden />
                    {TAB_KEYS.map((k) => {
                      const label = { setup: "Setup", assign: "Assignment", outcomes: "Outcomes", rules: "Rules" }[k];
                      const dot = (k === "outcomes" && outcomesOf(cur).length > 0) || (k === "rules" && (!!cur.parallel || !!cur.cond?.field));
                      return <button key={k} className={`itab${tab === k ? " on" : ""}`} onClick={() => setTab(k)}>{label}{dot ? <span className="tdot" /> : null}</button>;
                    })}
                  </div>
                  <div key={tab} className="ibody tabin">
                    {tab === "setup" && (
                      <>
                        <div className="fld"><label className="lab">What kind of step is this?</label>
                          <select className="inp" value={cur.type} onChange={(e) => {
                            const t = e.target.value as WorkflowStepType;
                            const ok = allowedStages(t);
                            moveToStage(sel!, { type: t, stage: cur.stage && ok.includes(cur.stage) ? cur.stage : ok[ok.length === 1 ? 0 : ok.indexOf("review") >= 0 ? ok.indexOf("review") : 0] });
                          }}>
                            {TYPES.map((t) => <option key={t} value={t}>{TYPE_META[t].label}</option>)}
                          </select>
                          <div className="typedesc">{TYPE_DESC[cur.type]}</div>
                        </div>
                        <div className="fld"><label className="lab" htmlFor="wf-stage">Lifecycle stage</label>
                          <select id="wf-stage" className="inp" value={cur.stage ?? "review"} disabled={allowedStages(cur.type).length === 1}
                            onChange={(e) => moveToStage(sel!, { stage: e.target.value as LifecycleStage })}>
                            {allowedStages(cur.type).map((k) => <option key={k} value={k}>{stageLabel(k)}</option>)}
                          </select>
                          <div className="hint">{allowedStages(cur.type).length === 1
                            ? `${/^[aeiou]/i.test(TYPE_META[cur.type].label) ? "An" : "A"} ${TYPE_META[cur.type].label.toLowerCase()} step always sits in ${stageLabel(allowedStages(cur.type)[0])}.`
                            : "Where this step shows on the contract's lifecycle. Steps stay in lifecycle order."}</div>
                        </div>
                        <div className="fld"><label className="lab">Time allowed (SLA, in hours)</label>
                          <input className="inp" type="number" value={cfgStr(cur.config, "sla_hours")} onChange={(e) => patchCfg(sel!, "sla_hours", e.target.value ? Number(e.target.value) : undefined)} placeholder="e.g. 48" />
                          <div className="hint">The assignee is chased once this runs out. Use 0 for automated steps.</div>
                        </div>
                        {cur.type === "ai_task" && (
                          <div className="fld"><label className="lab">AI agent</label>
                            <select className="inp" value={cfgStr(cur.config, "agent")} onChange={(e) => patchCfg(sel!, "agent", e.target.value)}>
                              <option value="">None — system step (nothing is checked)</option>
                              {["nda-agent", "contract-review-agent", "litigation-agent", "vendor-intake-agent", "privacy-assessment-agent", "trademark-agent"].map((a) => <option key={a} value={a}>{a}</option>)}
                            </select></div>
                        )}
                        <div className="fld"><label className="lab">Instructions for the assignee</label>
                          <textarea className="inp ta" rows={3} value={cfgStr(cur.config, "instructions")} onChange={(e) => patchCfg(sel!, "instructions", e.target.value || undefined)} placeholder="e.g. Draft from the MSA template." />
                        </div>
                      </>
                    )}
                    {tab === "assign" && (
                      <>
                        <div className="ihelp">Who lands this step when a request reaches it.</div>
                        <div className="fld"><label className="lab">Team</label>
                          <select className="inp" value={cfgStr(cur.config, "team_id")} onChange={(e) => patchCfg(sel!, "team_id", e.target.value || undefined)}>
                            <option value="">{cur.type === "approval" ? "Choose a team…" : "No team — the request owner"}</option>
                            {(teams ?? []).map((t) => <option key={t.id} value={t.id}>{t.name}{t.members.length ? "" : " (no members)"}</option>)}
                          </select>
                        </div>
                        <div className="fld"><label className="lab">{cur.type === "approval" ? "Who approves" : "Pick the person by…"}</label>
                          <select className="inp" value={assignOptions(cur.type).includes(cfgStr(cur.config, "assign_by")) ? cfgStr(cur.config, "assign_by") : assignOptions(cur.type)[0]} onChange={(e) => patchCfg(sel!, "assign_by", e.target.value)}>
                            {assignOptions(cur.type).map((a) => <option key={a} value={a}>{a}</option>)}
                          </select>
                        </div>
                        {cur.type === "approval" && (
                          <div className="fld"><label className="lab">What the approver sees</label>
                            <select className="inp" value={cfgStr(cur.config, "summary") || "general"} onChange={(e) => patchCfg(sel!, "summary", e.target.value)}>
                              <option value="general">General: value, term, overall risk, top issues</option>
                              <option value="finance">Finance: value, term, payment terms, liability, renewal</option>
                              <option value="legal">Legal: playbook risk, indemnity, termination, governing law, non-standard clauses</option>
                            </select>
                          </div>
                        )}
                        {cfgStr(cur.config, "assign_by") === "Specific person" && (
                          <div className="fld"><label className="lab">Person</label>
                            <select className="inp" value={cfgStr(cur.config, "assignee_user_id")} onChange={(e) => patchCfg(sel!, "assignee_user_id", e.target.value || undefined)}>
                              <option value="">Choose a person…</option>
                              {(users ?? []).map((u) => <option key={u.id} value={u.id}>{u.full_name || u.email}</option>)}
                            </select>
                          </div>
                        )}
                        {cur.type === "approval" && (() => {
                          // Authority (Admin → Authority) is checked when someone decides; show it here so
                          // a team whose members can't approve the contract's value is caught while building.
                          const team = (teams ?? []).find((t) => t.id === cfgStr(cur.config, "team_id"));
                          const people = (team?.members ?? []).filter((m) => m.active);
                          if (!people.length) return null;
                          if (!people.some((m) => m.approve_limit)) {
                            return <div className="hint">No approval authority limits are set (Admin → Authority), so any member can approve any value.</div>;
                          }
                          return (
                            <div className="fld"><label className="lab">Approval authority</label>
                              <div className="authlist">{people.map((m) => (
                                <div key={m.user_id} className={m.approve_limit === "no authority" ? "none" : ""}><span>{m.name ?? m.user_id}</span><b>{m.approve_limit ?? "—"}</b></div>
                              ))}</div>
                              <div className="hint">An approver can only decide contracts within their limit. Set limits in Admin → Authority.</div>
                            </div>
                          );
                        })()}
                        <div className="assignprev">{svg('<circle cx="12" cy="8" r="4"/><path d="M4 20a8 8 0 0 1 16 0"/>')}<div>{ASSIGN_DESC[assignOptions(cur.type).includes(cfgStr(cur.config, "assign_by")) ? cfgStr(cur.config, "assign_by") : assignOptions(cur.type)[0]]} <b>{teamName(cfgStr(cur.config, "team_id")) || (cur.type === "approval" ? "no team chosen — this approval would stall" : "the request owner")}</b></div></div>
                      </>
                    )}
                    {tab === "outcomes" && (
                      <>
                        <div className="ihelp">The buttons the assignee sees on this step. Picking a type already sets sensible defaults — only change these if you need to.</div>
                        {OUTCOME_GROUPS.map((grp) => (
                          <div key={grp.label} className="ogrp">
                            <div className="ogrplbl">{grp.label}</div>
                            <div className="orows">
                              {grp.keys.map((k) => {
                                const on = outcomesOf(cur).includes(k);
                                const back = BACK_OUTCOMES.has(k);
                                return (
                                  <button key={k} type="button" className={`orow${on ? " on" : ""}${back ? " back" : ""}`}
                                    onClick={() => { const now = outcomesOf(cur); patchCfg(sel!, "outcomes", on ? now.filter((x) => x !== k) : [...now, k]); }}>
                                    <span className="ock">{on ? svg('<path d="M20 6 9 17l-5-5"/>') : null}</span>{OUTCOME_LABEL[k]}
                                  </button>
                                );
                              })}
                            </div>
                          </div>
                        ))}
                        {hasBack(cur) && sel! > 0 && (
                          <div className="fld" style={{ marginTop: 14 }}><label className="lab">When they send back, it returns to…</label>
                            <select className="inp" value={typeof cur.config.return_to === "number" ? String(cur.config.return_to) : "previous"} onChange={(e) => patchCfg(sel!, "return_to", e.target.value === "previous" ? undefined : Number(e.target.value))}>
                              <option value="previous">Previous step</option>
                              {steps.slice(0, sel!).map((st, idx) => <option key={idx} value={idx}>{`${idx + 1}. ${st.name || "Step"}`}</option>)}
                            </select>
                          </div>
                        )}
                      </>
                    )}
                    {tab === "rules" && (
                      <>
                        <div className="ihelp">Optional — most steps just run in order. Use these for parallel review or steps that only apply sometimes.</div>
                        {sel! > 0 ? (
                          <>
                            <button type="button" className={`toggle${cur.parallel ? " on" : ""}`} onClick={() => toggleParallel(sel!)}><span className="sw" />Run in parallel with the step above</button>
                            <div className="ihelp" style={{ margin: "6px 0 14px" }}>Both open at once (a &ldquo;Parallel · all must respond&rdquo; group); the workflow waits for all of them.</div>
                          </>
                        ) : (
                          <div className="ihelp" style={{ marginBottom: 14 }}>The first step can&rsquo;t run in parallel — add a step after it, then turn this on there.</div>
                        )}
                        <button type="button" className={`toggle${cur.cond?.field ? " on" : ""}`} onClick={() => patchStep(sel!, { cond: cur.cond?.field ? null : { field: "", op: "eq", value: "" } })}><span className="sw" />Only run when a condition is met</button>
                        <div className="ihelp" style={{ margin: "6px 0 0" }}>Skip this step unless the request matches a rule — e.g. only do Finance approval when value is over ₹5 cr.</div>
                        {cur.cond && (
                          <div className="condrow" style={{ marginTop: 12 }}>
                            <input className="inp" value={cur.cond.field} onChange={(e) => patchStep(sel!, { cond: { field: e.target.value, op: cur.cond?.op || "eq", value: cur.cond?.value ?? "" } })} placeholder="field (e.g. gxp)" />
                            <select className="inp op" value={cur.cond.op} onChange={(e) => patchStep(sel!, { cond: { field: cur.cond?.field ?? "", op: e.target.value, value: cur.cond?.value ?? "" } })}>
                              <option value="eq">is</option>
                              <option value="ne">is not</option>
                              <option value="lt">is less than</option>
                              <option value="lte">is at most</option>
                              <option value="gt">is more than</option>
                              <option value="gte">is at least</option>
                            </select>
                            <input className="inp" value={cur.cond.value} onChange={(e) => patchStep(sel!, { cond: { field: cur.cond?.field ?? "", op: cur.cond?.op || "eq", value: e.target.value } })} placeholder="value (e.g. true)" />
                          </div>
                        )}
                      </>
                    )}
                  </div>
                  <div className="ifoot">
                    <button className="fb dng" onClick={() => del(sel!)}>{svg(ICON.trash)}Delete step</button>
                    <button className="fb pri" onClick={() => setSel(null)}>{svg('<path d="M20 6 9 17l-5-5"/>')}Done</button>
                  </div>
                </div>
              ) : sys && SYSTEM_STEPS[sys.stage]?.steps[sys.i] ? (() => {
                const x = SYSTEM_STEPS[sys.stage]!.steps[sys.i];
                return (
                  <div key={`sys-${sys.stage}-${sys.i}`} className="inspin">
                    <div className="ih"><div className="tic t-ink sysic2">{svg(x.icon)}</div><div className="iht"><b>{x.name}</b><em>Built-in · {stageLabel(sys.stage)}</em></div><button className="delx plain" onClick={() => setSys(null)}>Close</button></div>
                    <div className="ibody">
                      <div className="sysnote"><span className="sysbadge">{svg('<rect x="3" y="11" width="18" height="11" rx="2"/><path d="M7 11V7a5 5 0 0 1 10 0v4"/>')}Built-in step</span><p>This happens for every request that reaches the {stageLabel(sys.stage)} stage, whatever this workflow does. It can&rsquo;t be edited or removed here.</p></div>
                      <div className="card"><div className="cardhd">What it does</div><p className="sysdesc">{x.detail}</p>{x.note && <p className="syshint">{svg('<circle cx="12" cy="12" r="9"/><path d="M12 8h.01M11 12h1v4h1"/>')}{x.note}</p>}</div>
                      <div className="card"><div className="cardhd">Kind</div><span className="chip dept">{x.kind}</span></div>
                    </div>
                  </div>
                );
              })() : (
                <div className="inspin">
                  <div className="ih"><div className="tic t-accent">{svg('<circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="12" r="2.5"/><path d="M8.5 6H14a2 2 0 0 1 2 2v2M8.5 18H14a2 2 0 0 0 2-2v-2"/>')}</div><div className="iht"><b>Workflow settings</b><em>When this workflow is picked</em></div>
                    <button type="button" className={`toggle sm${enabled ? " on" : ""}`} onClick={() => setEnabled(!enabled)} aria-pressed={enabled} title={enabled ? "Enabled: requests can get this workflow" : "Disabled: nothing is routed here"}><span className="sw" /><span>{enabled ? "On" : "Off"}</span></button>
                  </div>
                  <div className="ibody cards">

                    <section className="card">
                      <div className="cardhd">{svg('<path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/>')}Agreement type</div>
                      <select className="inp" value={typeSel} onChange={(e) => { setTypeSel(e.target.value); setConds([]); }}>
                        <option value="">None: email and chat requests only</option>
                        {types.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
                      </select>
                      <div className="hint">{typeOpt ? `Only ${typeOpt.label} requests can get this workflow.` : "Form requests never get an untyped workflow; the AI may suggest it for email and chat requests."}</div>
                      {!typeOpt && pinnedRequestTypes(flow).length > 0 && (
                        <div className="hint">Always used for {pinnedRequestTypes(flow).map((t) => `“${t}”`).join(", ")} requests (except ones triage marks as litigation).</div>
                      )}
                    </section>

                    {typeOpt ? (
                      <section className="card">
                        <div className="cardhd">{svg('<path d="M3 4h18l-7 8v6l-4 2v-8z"/>')}Chosen when<span className="cnt">{conds.length || "any"}</span></div>
                        <div key={whenText(conds, qLabels)} className="sentence">{svg('<path d="M12 3v3M12 18v3M5 12H2M22 12h-3M6 6 4 4M20 20l-2-2M18 6l2-2M4 20l2-2"/>')}<span>Used for {typeOpt.label} requests {conds.length ? `when ${whenText(conds, qLabels)}.` : `when no other ${typeOpt.short} workflow fits.`}</span></div>
                        {conds.map((c, i) => {
                          const q = questions.find((x) => x.field === c.field);
                          const amount = AMOUNT_OPS.includes(c.op);
                          const choice = !q?.date && !amount;
                          return (
                            <div key={c.field + i} className="condcard">
                              <div className="condtop">
                                <b className="condq">{q?.label ?? c.field}</b>
                                {choice && <span className="opt" role="group" aria-label="Operator">{CHOICE_OPS.map((o) => <button key={o} type="button" className={c.op === o ? "on" : ""} aria-pressed={c.op === o} onClick={() => patchCond(i, { op: o })}>{COND_OP_LABEL[o]}</button>)}</span>}
                                <button className="xbtn" title="Remove this condition" aria-label="Remove condition" onClick={() => setConds(conds.filter((_, j) => j !== i))}>{svg('<path d="M18 6 6 18M6 6l12 12"/>')}</button>
                              </div>
                              {choice ? (
                                <>
                                  <div className="chips" role="group" aria-label="Answer">
                                    {(q?.options ?? []).map((o) => <button key={o} type="button" className={`chipb${String(c.value) === o ? " on" : ""}`} aria-pressed={String(c.value) === o} onClick={() => patchCond(i, { value: o })}>{o}</button>)}
                                  </div>
                                </>
                              ) : (
                                <div className="condmain">
                                  <select className="inp" aria-label="Operator" value={c.op}
                                    onChange={(e) => patchCond(i, { op: e.target.value as WorkflowCondition["op"], ...(e.target.value === "between" && !c.value2 ? { value2: Number(c.value || 0) * 5 || 5000000 } : {}) })}>
                                    {(q?.date ? DATE_OPS : AMOUNT_OPS).map((o) => <option key={o} value={o}>{COND_OP_LABEL[o]}</option>)}
                                  </select>
                                  {q?.date ? (
                                    <div className="valrow">
                                      <input className="inp" inputMode="numeric" aria-label="Days" value={String(c.value ?? "")} onChange={(e) => patchCond(i, { value: e.target.value.replace(/[^0-9]/g, "") })} />
                                      <span className="hint">days of the request</span>
                                    </div>
                                  ) : (
                                    <div className="valrow">
                                      <select className="inp cur" aria-label="Currency" value={c.currency ?? "INR"} onChange={(e) => patchCond(i, { currency: e.target.value })}>
                                        {["INR", "USD", "EUR", "GBP"].map((x) => <option key={x}>{x}</option>)}
                                      </select>
                                      <input className="inp" inputMode="numeric" aria-label="Amount" value={String(c.value ?? "")} onChange={(e) => patchCond(i, { value: e.target.value.replace(/[^0-9]/g, "") })} />
                                      {c.op === "between" && <><span className="hint">and</span>
                                        <input className="inp" inputMode="numeric" aria-label="Upper amount" value={String(c.value2 ?? "")} onChange={(e) => patchCond(i, { value2: Number(e.target.value.replace(/[^0-9]/g, "")) })} /></>}
                                    </div>
                                  )}
                                </div>
                              )}
                            </div>
                          );
                        })}
                        {questions.length ? (
                          (() => {
                            const free = questions.filter((x) => !conds.some((c) => c.field === x.field));
                            return free.length ? (
                              <div className="addq">
                                <button type="button" className="condadd" aria-expanded={addOpen} onClick={() => setAddOpen((o) => !o)}>{svg(ICON.plus)}{conds.length ? "Add another condition" : "Add a condition"}<span className="cnt">{free.length}</span></button>
                                {addOpen && (
                                  <ul className="addmenu" aria-label="Questions to narrow by">
                                    {free.map((x) => <li key={x.field}><button type="button" onClick={() => { setConds([...conds, freshCond(x.field)]); setAddOpen(false); }}>{x.label}<small>{x.amount ? "amount" : x.date ? "date" : `${x.options.length} choices`}</small></button></li>)}
                                  </ul>
                                )}
                              </div>
                            ) : null;
                          })()
                        ) : <div className="hint">This type&apos;s form asks nothing a condition can use.</div>}
                        <div className="hint">A request with a question left blank, or a value in another currency, doesn&apos;t meet a condition on it.</div>
                      </section>
                    ) : (
                      <section className="card">
                        <div className="cardhd">{svg('<circle cx="11" cy="11" r="7"/><path d="M21 21l-4-4"/>')}How email and chat requests find it</div>
                        <label className="lab">Match words</label>
                        <input className="inp" value={matchType} onChange={(e) => setMatchType(e.target.value)} placeholder="type word, e.g. litigation, notice" />
                        <input className="inp" value={matchKeyword} onChange={(e) => setMatchKeyword(e.target.value)} placeholder="keyword in the request" />
                        <label className="lab">When should AI choose this workflow?</label>
                        <textarea className="inp ta" rows={3} value={aiCondition} onChange={(e) => setAiCondition(e.target.value)} placeholder="e.g. Use this for any litigation hold notice from outside counsel." />
                        <div className="hint">Free text read by the AI that picks a workflow for email and chat requests, in addition to the match words.</div>
                      </section>
                    )}

                    {typeOpt ? (
                      <section className="card">
                        <div className="cardhd">{svg('<circle cx="9" cy="9" r="6"/><circle cx="15" cy="15" r="6"/>')}Other {typeOpt.short} workflows<span className="cnt">{siblings.length}</span></div>
                        {siblings.length ? siblings.map((f) => (
                          <div key={f.id} className="sib"><b>{f.name}</b><span>Chosen when {whenText(f.criteria.conditions)}</span></div>
                        )) : <div className="hint">None: this is the only one.</div>}
                        {clash && <div className="clash">{svg(ICON.warn)}<span>A request could fit both this and “{clash.name}”. Change a condition so they don&apos;t overlap — otherwise the one with more conditions wins.</span></div>}
                      </section>
                    ) : null}

                    <section className="card">
                      <div className="cardhd">{svg('<path d="M3 6h13M3 12h9M3 18h5M17 8l4 4-4 4"/>')}Priority order</div>
                      <div className="stepper">
                        <button type="button" aria-label="Lower" onClick={() => setEvalOrder(String(Math.max(0, (Number(evalOrder) || 100) - 10)))}>−</button>
                        <input className="inp" type="number" value={evalOrder} onChange={(e) => setEvalOrder(e.target.value)} aria-label="Priority order" />
                        <button type="button" aria-label="Higher" onClick={() => setEvalOrder(String((Number(evalOrder) || 100) + 10))}>+</button>
                      </div>
                      <div className="hint">Only breaks a tie when two workflows both fit a request: lower wins.</div>
                    </section>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

const WF_CSS = `
.wf{--bg:#f4f6f9;--ext:#75589f;--ext-soft:#efe8f7;--shadow:0 1px 2px rgba(20,26,40,.05),0 8px 22px rgba(20,26,40,.06);--mono:ui-monospace,SFMono-Regular,Menlo,monospace;--sans:var(--font-sans);height:100%;background:var(--bg);color:var(--ink);font:400 13px/1.5 var(--sans)}
.dark .wf{--bg:#0c0f16;--ext:#ab90d2;--ext-soft:#221b31;--shadow:0 1px 2px rgba(0,0,0,.4),0 10px 26px rgba(0,0,0,.4)}
.wf *{box-sizing:border-box} .wf .dim{color:var(--ink-3)}
.wf .ic{width:15px;height:15px;flex:none;stroke:currentColor;stroke-width:1.8;fill:none;stroke-linecap:round;stroke-linejoin:round}
.wf button{cursor:pointer;border:0;background:none;color:inherit;font:inherit} .wf b{font-weight:660}
@keyframes wfrise{from{opacity:0;transform:translateY(14px) scale(.985)}to{opacity:1;transform:none}}
@keyframes wfpop{from{opacity:0;transform:translateY(-6px) scale(.96)}to{opacity:1;transform:none}}
@keyframes wfslide{from{opacity:0;transform:translateX(16px)}to{opacity:1;transform:none}}
.wf .wfapp{display:grid;grid-template-columns:1fr;height:100%}
.wf .wfmain{min-width:0;display:flex;flex-direction:column;min-height:0;position:relative}

/* top bar */
.wf .topbar{flex:none;display:flex;align-items:center;gap:8px;padding:10px 16px;border-bottom:1px solid var(--border);background:var(--surface);flex-wrap:nowrap;overflow:hidden}
.wf .backbtn,.wf .chkpill,.wf .tbtn,.wf .bdg{flex:none;white-space:nowrap}
.wf .backbtn{display:inline-flex;align-items:center;gap:7px;height:34px;padding:0 12px 0 9px;border-radius:10px;font-weight:600;font-size:12.5px;color:var(--ink-2);transition:background .15s,transform .15s}
.wf .backbtn:hover{background:var(--surface-2);color:var(--ink)} .wf .backbtn:hover .ic{transform:translateX(-3px)} .wf .backbtn .ic{transition:transform .2s}
.wf .tsep{width:1px;height:22px;background:var(--border)}
.wf .flowsel{height:34px;padding:0 10px;border:1px solid var(--border-strong);border-radius:10px;background:var(--surface);color:var(--ink);font-weight:600;font-size:12.5px;max-width:160px;min-width:0;flex:0 1 160px}
.wf .wftitle{border:0;background:none;font-weight:680;font-size:16px;color:var(--ink);outline:none;min-width:90px;flex:1 1 200px;padding:4px 2px;border-bottom:2px solid transparent;transition:border-color .2s} .wf .wftitle:focus{border-bottom-color:var(--accent)}
.wf .bdg{display:inline-flex;align-items:center;gap:6px;font:600 10.5px var(--sans);padding:3px 10px;border-radius:99px} .wf .bdg i{width:6px;height:6px;border-radius:50%;background:currentColor}
.wf .bdg.ver{background:var(--surface-2);color:var(--ink-2)} .wf .bdg.draft{background:var(--warn-soft);color:var(--warn)} .wf .bdg.active{background:var(--good-soft);color:var(--good)}
.wf .bdg.unsaved{background:var(--accent-soft);color:var(--accent);animation:wfpop .25s ease both} .wf .bdg.unsaved i{animation:wfblink 1.6s ease-in-out infinite}
@keyframes wfblink{50%{opacity:.3}}
.wf .tbtn{display:inline-flex;align-items:center;gap:7px;height:36px;padding:0 14px;border-radius:10px;border:1px solid var(--border-strong);background:var(--surface);color:var(--ink);font-weight:600;font-size:12.5px;transition:transform .15s,background .15s,box-shadow .2s}
.wf .tbtn:hover:not([disabled]){background:var(--surface-2);transform:translateY(-1px)} .wf .tbtn:active:not([disabled]){transform:scale(.97)}
.wf .tbtn.pri{background:var(--accent);border-color:var(--accent);color:var(--accent-ink)} .wf .tbtn.pri:hover:not([disabled]){background:var(--accent);box-shadow:0 8px 18px -8px var(--accent)}
.wf .tbtn[disabled]{opacity:.45;cursor:not-allowed}
.wf .chkpill{display:inline-flex;align-items:center;gap:7px;height:34px;padding:0 13px;border-radius:99px;font:600 12px var(--sans);transition:transform .15s}
.wf .chkpill:hover{transform:translateY(-1px)}
.wf .chkpill.ok{background:var(--good-soft);color:var(--good)} .wf .chkpill.bad{background:var(--warn-soft);color:var(--warn)}
.wf .issues{position:absolute;right:16px;top:58px;z-index:40;width:380px;max-width:calc(100% - 32px);padding:6px;border-radius:16px;background:var(--surface);border:1px solid var(--border);box-shadow:0 20px 44px -18px rgba(20,26,40,.45);animation:wfpop .18s cubic-bezier(.2,.8,.2,1) both}
.wf .issue{display:flex;align-items:flex-start;gap:10px;width:100%;text-align:left;padding:9px 10px;border-radius:11px;font-size:12px;color:var(--ink-2);transition:background .15s}
.wf .issue:hover{background:var(--inset)} .wf .issue .ic{color:var(--warn);margin-top:2px} .wf .issue span{flex:1} .wf .issue em{font-style:normal;font:600 10.5px var(--sans);color:var(--ink-3);white-space:nowrap}
.wf .allgood{display:flex;gap:9px;align-items:center;margin:0;padding:10px;font-size:12.5px;color:var(--good)}

/* canvas */
.wf .work{flex:1;min-height:0;display:grid;grid-template-columns:1fr 10px var(--iw,410px)}
.wf .resizer{position:relative;cursor:col-resize;display:grid;place-items:center;touch-action:none;outline:none;background:var(--surface)}
.wf .resizer::before{content:"";position:absolute;inset:0 4px;background:var(--border);transition:background .15s,inset .15s}
.wf .resizer:hover::before,.wf .resizer.on::before,.wf .resizer:focus-visible::before{background:var(--accent);inset:0 3px}
.wf .resizer .grip{position:relative;width:4px;height:36px;border-radius:4px;background:var(--border-strong);box-shadow:0 0 0 3px var(--surface);transition:background .15s,height .15s}
.wf .resizer:hover .grip,.wf .resizer.on .grip{background:var(--accent);height:52px}
.wf .work:has(.resizer.on){user-select:none;cursor:col-resize}
.wf .canvas{overflow:auto;padding:18px 26px 40px;background:var(--bg);background-image:radial-gradient(var(--border) 1px,transparent 1px);background-size:22px 22px}
.wf .sumbar{display:flex;gap:8px;flex-wrap:wrap;justify-content:center;margin:0 auto 14px}
.wf .sumbar span{padding:5px 12px;border-radius:99px;background:var(--surface);box-shadow:0 0 0 1px var(--border);font-size:12px;color:var(--ink-3)} .wf .sumbar b{color:var(--ink);margin-right:3px}
.wf .flowcol{display:flex;flex-direction:column;align-items:stretch;max-width:600px;margin:0 auto}
.wf .node{align-self:center;display:inline-flex;align-items:center;gap:8px;padding:9px 17px;border-radius:99px;font-weight:650;font-size:12.5px;box-shadow:var(--shadow);animation:wfrise .5s both}
.wf .node.start{background:var(--ink);color:var(--bg)} .wf .node.end{background:var(--good);color:#fff}
/* between stages: a line that flows downward, with an arrowhead that pulses after it */
@keyframes wfflow{from{background-position:0 0}to{background-position:0 12px}}
@keyframes wfarrow{0%,100%{opacity:.35;bottom:-5px}50%{opacity:1;bottom:-9px}}
.wf .lane{position:relative;padding:12px 14px 6px;border-radius:20px;background:color-mix(in srgb,var(--surface) 70%,transparent);box-shadow:0 0 0 1px var(--border);display:flex;flex-direction:column;align-items:stretch;opacity:0;animation:wfrise .55s cubic-bezier(.2,.8,.2,1) forwards;transition:box-shadow .2s}
.wf .lane:hover{box-shadow:0 0 0 1px var(--border-strong)}
.wf .lane.empty{background:transparent;border:1.5px dashed var(--border-strong);box-shadow:none;padding-bottom:10px}
.wf .stagehd{display:flex;align-items:center;gap:10px;margin-bottom:2px;width:100%;text-align:left;padding:2px 0;border-radius:10px}
.wf .stagehd:hover .num{transform:scale(1.12)}
.wf .stagehd .chev{margin-left:6px;color:var(--ink-3);transition:transform .3s cubic-bezier(.3,.8,.2,1)} .wf .lane.shut .stagehd .chev{transform:rotate(-90deg)}
.wf .lanebody{display:grid;grid-template-rows:1fr;transition:grid-template-rows .4s cubic-bezier(.4,0,.2,1)}
.wf .lane.shut .lanebody{grid-template-rows:0fr}
.wf .lanein{min-height:0;display:flex;flex-direction:column;align-items:stretch;transition:opacity .25s}
.wf .lane.shut .lanein{overflow:hidden;opacity:0}
/* a pulse that travels down the stage numbers, one after another, like a request moving through */
.wf .stagehd .num{position:relative;transition:transform .2s,background .25s,color .25s;animation:wfwave 5.4s ease-in-out infinite;animation-delay:calc(var(--si,0) * .55s + 1s)}
@keyframes wfwave{0%,14%,100%{box-shadow:0 0 0 0 transparent}6%{box-shadow:0 0 0 7px color-mix(in srgb,var(--accent) 22%,transparent);transform:scale(1.14)}}
.wf .lane.hot{box-shadow:0 0 0 1.5px var(--accent),0 16px 34px -22px color-mix(in srgb,var(--accent) 60%,transparent)}
.wf .lane.hot .stagehd .num{background:var(--accent);color:var(--accent-ink)}
.wf .lane.empty .stagehd .num{animation-name:none}
.wf .flowcol{position:relative}
.wf .lane,.wf .node{position:relative;z-index:1}
.wf .stagehd .num{width:22px;height:22px;border-radius:50%;display:grid;place-items:center;background:var(--accent-soft);color:var(--accent);font:700 11px var(--sans)}
.wf .lane.empty .stagehd .num{background:var(--surface-2);color:var(--ink-3)}
.wf .stagehd b{font-size:13px;letter-spacing:-.005em} .wf .lane.empty .stagehd b{color:var(--ink-3)}
.wf .stagehd .cnt{margin-left:auto;font:600 11px var(--sans);color:var(--ink-3)}

.wf .adder{position:relative;align-self:center;display:flex;justify-content:center;padding:6px 0}
.wf .adder.sm{padding:2px 0}
.wf .addbtn{width:26px;height:26px;border-radius:50%;border:1.5px dashed var(--border-strong);color:var(--ink-3);display:grid;place-items:center;background:var(--surface);transition:all .2s}
.wf .addbtn:hover,.wf .adder.open .addbtn{border-color:var(--accent);border-style:solid;color:var(--accent);background:var(--accent-soft);transform:rotate(90deg) scale(1.1)}
.wf .adder.open .addbtn{transform:rotate(45deg) scale(1.1)}
.wf .pickscrim{position:fixed;inset:0;z-index:30}
.wf .picker{position:absolute;top:100%;left:50%;transform:translateX(-50%);z-index:31;width:340px;padding:6px;border-radius:16px;background:var(--surface);border:1px solid var(--border);box-shadow:0 22px 48px -16px rgba(20,26,40,.5);animation:wfpop .18s cubic-bezier(.2,.8,.2,1) both}
.wf .pickhd{padding:8px 10px 6px;font:600 10px var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3)}
.wf .pickrow{display:flex;align-items:center;gap:11px;width:100%;text-align:left;padding:8px 10px;border-radius:11px;transition:background .15s,transform .15s}
.wf .pickrow:hover{background:var(--inset);transform:translateX(2px)}
.wf .pickrow .pt{display:flex;flex-direction:column;min-width:0} .wf .pickrow b{font-size:12.5px} .wf .pickrow em{font-style:normal;font-size:11px;color:var(--ink-3);line-height:1.35}

/* step cards */
.wf .cardwrap{animation:wfrise .4s cubic-bezier(.2,.8,.2,1) both}
.wf .stepcard{--tc:var(--ink-3);position:relative;border-radius:16px;background:var(--surface);box-shadow:0 1px 2px rgba(20,26,40,.04),0 0 0 1px var(--border);padding:12px 14px 12px 18px;cursor:pointer;transition:transform .2s cubic-bezier(.2,.8,.2,1),box-shadow .2s;overflow:hidden;animation:wfrise .4s cubic-bezier(.2,.8,.2,1) both}
.wf .stepcard.t-good{--tc:var(--good)} .wf .stepcard.t-warn{--tc:var(--warn)} .wf .stepcard.t-accent{--tc:var(--accent)} .wf .stepcard.t-ext{--tc:var(--ext)} .wf .stepcard.t-ink{--tc:var(--ink-3)}
.wf .stepcard:hover{transform:translateY(-2px);box-shadow:0 16px 30px -22px rgba(20,26,40,.5),0 0 0 1px var(--border-strong)}
.wf .stepcard.sel{box-shadow:0 0 0 2px var(--accent),0 18px 34px -20px color-mix(in srgb,var(--accent) 55%,transparent);animation:wfrise .4s both,wfring 2.4s 0.4s ease-in-out infinite}
@keyframes wfring{50%{box-shadow:0 0 0 2px var(--accent),0 0 0 7px color-mix(in srgb,var(--accent) 14%,transparent),0 18px 34px -20px color-mix(in srgb,var(--accent) 55%,transparent)}}
.wf .stepcard .top{display:flex;align-items:center;gap:11px}
.wf .tic{width:34px;height:34px;border-radius:11px;display:grid;place-items:center;flex:none;transition:transform .25s}
.wf .stepcard:hover .tic{transform:rotate(-6deg) scale(1.06)}
.wf .tic.t-good{background:var(--good-soft);color:var(--good)} .wf .tic.t-warn{background:var(--warn-soft);color:var(--warn)} .wf .tic.t-accent{background:var(--accent-soft);color:var(--accent)} .wf .tic.t-ext{background:var(--ext-soft);color:var(--ext)} .wf .tic.t-ink{background:var(--surface-2);color:var(--ink-2)}
.wf .snm{display:flex;flex-direction:column;min-width:0;flex:1;line-height:1.25} .wf .snm .nm{font-weight:660;font-size:13.5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis} .wf .snm .kind{font-size:11px;color:var(--ink-3)}
.wf .idx{font:700 10.5px var(--mono);color:var(--ink-3);background:var(--surface-2);min-width:22px;height:22px;border-radius:7px;display:grid;place-items:center;padding:0 5px}
.wf .meta{display:flex;gap:6px;flex-wrap:wrap;margin-top:10px}
.wf .chip{display:inline-flex;align-items:center;gap:5px;padding:2px 9px;border-radius:99px;font:600 10.5px var(--sans);background:var(--surface-2);color:var(--ink-2)} .wf .chip .ic{width:11px;height:11px}
.wf .chip.dept{background:var(--accent-soft);color:var(--accent)} .wf .chip.nodept{color:var(--ink-3)} .wf .chip.sla{background:var(--warn-soft);color:var(--warn)}
.wf .chip.out{background:transparent;box-shadow:inset 0 0 0 1px var(--border);color:var(--ink-2)} .wf .chip.out.neg{box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--crit) 40%,var(--border));color:var(--crit)}
.wf .stepcard .ctrls{position:absolute;top:8px;right:8px;display:flex;gap:2px;padding:3px;border-radius:11px;background:var(--surface);box-shadow:0 0 0 1px var(--border),0 8px 18px -10px rgba(20,26,40,.4);opacity:0;transform:translateY(-4px);pointer-events:none;transition:opacity .15s,transform .15s}
.wf .stepcard:hover .ctrls,.wf .stepcard.sel .ctrls,.wf .stepcard:focus-within .ctrls{opacity:1;transform:none;pointer-events:auto}
.wf .stepcard .ctrls button{width:26px;height:26px;border-radius:8px;color:var(--ink-2);display:grid;place-items:center;transition:background .15s,color .15s}
.wf .stepcard .ctrls button:hover:not([disabled]){background:var(--accent-soft);color:var(--accent)} .wf .stepcard .ctrls button[disabled]{opacity:.3;cursor:default} .wf .stepcard .ctrls button.on{background:var(--accent-soft);color:var(--accent)} .wf .stepcard .ctrls button.del:hover{background:var(--crit-soft);color:var(--crit)}
.wf .cond{margin-bottom:9px;display:inline-flex;align-items:center;gap:6px;font:600 10.5px var(--sans);color:var(--warn);background:var(--warn-soft);padding:3px 10px;border-radius:99px} .wf .cond .ic{width:12px;height:12px}
.wf .backedge{margin-top:9px;display:inline-flex;align-items:center;gap:6px;font:600 10.5px var(--sans);color:var(--crit);background:var(--crit-soft);padding:3px 10px;border-radius:99px} .wf .backedge .ic{width:12px;height:12px}
.wf .swarn{margin-top:9px;display:flex;align-items:flex-start;gap:7px;font-size:11.5px;color:var(--warn);background:var(--warn-soft);padding:6px 10px;border-radius:10px} .wf .swarn .ic{width:13px;height:13px;margin-top:1px}
.wf .stepcard.warn{box-shadow:0 0 0 1px color-mix(in srgb,var(--warn) 45%,var(--border))}
.wf .pgroup{align-self:stretch;border:1.5px dashed var(--border-strong);border-radius:16px;background:color-mix(in srgb,var(--accent) 5%,var(--surface));padding:10px 11px 12px;animation:wfrise .4s both}
.wf .pghd{display:inline-flex;align-items:center;gap:6px;margin:0 0 9px;font:600 10px var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--accent);background:var(--accent-soft);padding:3px 10px;border-radius:99px} .wf .pghd .ic{width:12px;height:12px}
.wf .pgsteps{display:flex;flex-direction:column;gap:9px}

/* inspector */
.wf .insp{background:var(--surface);overflow:auto;display:flex;flex-direction:column;min-height:0}
.wf .inspin{animation:wfslide .3s cubic-bezier(.2,.8,.2,1) both;display:flex;flex-direction:column;min-height:0}
.wf .tabin{animation:wfrise .3s cubic-bezier(.2,.8,.2,1) both}
.wf .insp .ih{padding:14px 16px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:11px}
.wf .insp .ih .tic{width:34px;height:34px}
.wf .iht{display:flex;flex-direction:column;line-height:1.25;min-width:0} .wf .iht b{font-size:13.5px} .wf .iht em{font-style:normal;font-size:11px;color:var(--ink-3)}
.wf .insp .ih .delx{margin-left:auto;display:inline-flex;align-items:center;gap:6px;height:32px;padding:0 11px;border:1px solid color-mix(in srgb,var(--crit) 45%,var(--border));border-radius:9px;color:var(--crit);font-weight:600;font-size:12px;background:var(--surface);transition:background .15s,transform .15s} .wf .insp .ih .delx:hover{background:var(--crit-soft);transform:translateY(-1px)}
.wf .titlefld{padding:12px 16px 0} .wf .titlefld .inp{font-weight:660;font-size:14px}
.wf .itabs{position:relative;display:grid;grid-template-columns:repeat(4,1fr);padding:10px 12px 0;border-bottom:1px solid var(--border)}
.wf .itab{position:relative;padding:9px 4px 11px;font-weight:600;font-size:12.5px;color:var(--ink-3);transition:color .2s;text-align:center} .wf .itab:hover{color:var(--ink-2)} .wf .itab.on{color:var(--accent)}
.wf .itab-bar{position:absolute;bottom:-1px;left:12px;height:2px;width:calc((100% - 24px) / 4);background:var(--accent);border-radius:2px;transform:translateX(calc(var(--ti,0) * 100%));transition:transform .3s cubic-bezier(.3,.8,.2,1)}
.wf .itab .tdot{display:inline-block;width:5px;height:5px;border-radius:50%;background:var(--accent);margin-left:5px;vertical-align:middle}
.wf .typedesc{padding:10px 12px;border-radius:12px;background:var(--inset);color:var(--ink-2);font-size:12px;line-height:1.5}
.wf .inp.ta{resize:vertical;min-height:58px;line-height:1.5}
.wf .ibody{padding:15px 16px 24px;display:flex;flex-direction:column;gap:14px}
.wf .emptyhint{font-size:12px;color:var(--ink-2);line-height:1.55;padding:10px 12px;border-radius:12px;background:var(--accent-soft)}
.wf .fld{display:flex;flex-direction:column;gap:6px} .wf .fld .lab{font-weight:600;font-size:12px;color:var(--ink)}
.wf .inp{width:100%;padding:8px 11px;border:1px solid var(--border-strong);border-radius:10px;background:var(--surface);color:var(--ink);font:500 12.5px var(--sans);transition:border-color .15s,box-shadow .15s} .wf .inp:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.wf .hint{font-size:11px;color:var(--ink-3);line-height:1.4}
.wf .condbox{display:flex;flex-direction:column;gap:6px;padding:10px;border-radius:12px;background:var(--surface-2);animation:wfrise .25s both}
.wf .condrow{display:flex;gap:6px;align-items:center} .wf .condrow .inp.cur{width:84px;flex:none} .wf .condrow .op{min-width:72px}
.wf .sentence{padding:10px 12px;border-radius:12px;background:var(--accent-soft);color:var(--accent);font-size:12px;line-height:1.5}
.wf .sib{display:flex;flex-direction:column;gap:2px;padding:9px 11px;border-radius:12px;box-shadow:0 0 0 1px var(--border);font-size:12px} .wf .sib span{color:var(--ink-2)}
.wf .clash{padding:10px 12px;border-radius:12px;background:var(--crit-soft);color:var(--crit);font-size:12px;line-height:1.5;animation:wfrise .25s both}
.wf .authlist{display:flex;flex-direction:column;border-radius:12px;overflow:hidden;box-shadow:0 0 0 1px var(--border)}
.wf .authlist>div{display:flex;justify-content:space-between;gap:10px;padding:7px 11px;font-size:12px;border-top:1px solid var(--border)} .wf .authlist>div:first-child{border-top:0}
.wf .authlist b{font-weight:600} .wf .authlist .none b{color:var(--warn)}
.wf .ogrp{margin-top:15px} .wf .ogrp:first-of-type{margin-top:0} .wf .ogrplbl{font:600 10px var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--ink-3);margin-bottom:8px}
.wf .orows{display:flex;flex-direction:column;gap:6px}
.wf .orow{display:flex;align-items:center;gap:10px;text-align:left;width:100%;padding:10px 12px;border-radius:12px;background:var(--surface);box-shadow:inset 0 0 0 1px var(--border);color:var(--ink-2);font:600 12.5px var(--sans);transition:background .15s,box-shadow .15s,transform .15s}
.wf .orow:hover{background:var(--surface-2);transform:translateX(2px)}
.wf .orow .ock{width:18px;height:18px;border-radius:6px;display:grid;place-items:center;box-shadow:inset 0 0 0 1.5px var(--border-strong);flex:none;transition:all .2s} .wf .orow .ock .ic{width:12px;height:12px;stroke-width:3}
.wf .orow.on{background:var(--accent-soft);box-shadow:inset 0 0 0 1.5px var(--accent);color:var(--accent)} .wf .orow.on .ock{background:var(--accent);color:var(--accent-ink);box-shadow:none}
.wf .orow.back.on{background:var(--crit-soft);box-shadow:inset 0 0 0 1.5px var(--crit);color:var(--crit)} .wf .orow.back.on .ock{background:var(--crit)}
.wf .ihelp{font-size:11.5px;color:var(--ink-3);line-height:1.5;margin:0 0 13px}
.wf .assignprev{font-size:11.5px;color:var(--accent);background:var(--accent-soft);border-radius:12px;padding:10px 12px;margin-top:4px;display:flex;gap:9px;align-items:flex-start;line-height:1.5} .wf .assignprev .ic{width:14px;height:14px;flex:none;margin-top:1px} .wf .assignprev b{color:var(--accent)}
.wf .toggle{display:inline-flex;align-items:center;gap:11px;font-size:12.5px;font-weight:600;color:var(--ink);cursor:pointer;background:none;border:0;padding:0} .wf .toggle .sw{width:40px;height:23px;border-radius:99px;background:var(--border-strong);position:relative;transition:background .2s;flex:none} .wf .toggle.on .sw{background:var(--accent)} .wf .toggle .sw::after{content:"";position:absolute;top:2px;left:2px;width:19px;height:19px;border-radius:50%;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.3);transition:left .22s cubic-bezier(.3,1.3,.5,1)} .wf .toggle.on .sw::after{left:19px}
.wf .chk{display:flex;align-items:center;gap:9px;font-size:12.5px;color:var(--ink);cursor:pointer;padding:10px 12px;border-radius:12px;background:var(--surface-2)} .wf .chk input{width:16px;height:16px;accent-color:var(--accent)}
.wf .lnk{color:var(--accent);font-weight:600}
/* built-in steps: striped and drifting, so they read as the app's own and not the workflow's */
.wf .sysbox{margin:8px 0;padding:10px 12px 8px;border-radius:14px;border:1.5px dashed var(--border-strong);background:repeating-linear-gradient(135deg,transparent 0 9px,color-mix(in srgb,var(--ink-3) 7%,transparent) 9px 10px);background-size:28px 28px;animation:wfdrift 3s linear infinite}
@keyframes wfdrift{to{background-position:28px 0}}
.wf .syshd{display:flex;align-items:center;gap:7px;margin-bottom:8px;font:600 10px var(--sans);letter-spacing:.07em;text-transform:uppercase;color:var(--ink-3)}
.wf .syshd .ic{width:13px;height:13px;animation:wfspin 8s linear infinite}
@keyframes wfspin{to{transform:rotate(360deg)}}
.wf .sysrows{display:flex;flex-direction:column;gap:5px}
.wf .sysrow{position:relative;display:flex;align-items:center;gap:10px;width:100%;text-align:left;padding:7px 10px;border-radius:11px;background:color-mix(in srgb,var(--surface) 82%,transparent);box-shadow:inset 0 0 0 1px var(--border);color:var(--ink-2);opacity:0;animation:wfsys .5s cubic-bezier(.2,.8,.2,1) forwards;overflow:hidden;transition:transform .15s,background .15s,box-shadow .15s}
@keyframes wfsys{from{opacity:0;transform:translateX(-10px)}to{opacity:1;transform:none}}
.wf .sysrow::after{content:"";position:absolute;inset:0;background:linear-gradient(100deg,transparent 30%,color-mix(in srgb,var(--accent) 14%,transparent) 50%,transparent 70%);transform:translateX(-100%);animation:wfsheen 3.6s ease-in-out infinite}
@keyframes wfsheen{0%,55%{transform:translateX(-100%)}100%{transform:translateX(100%)}}
.wf .sysrow:hover{background:var(--surface);transform:translateX(3px)}
.wf .sysrow.on{box-shadow:inset 0 0 0 1.5px var(--accent);background:var(--accent-soft);color:var(--accent)}
.wf .sysic{width:24px;height:24px;border-radius:8px;display:grid;place-items:center;background:var(--surface-2);color:var(--ink-3);flex:none;position:relative;z-index:1} .wf .sysic .ic{width:13px;height:13px}
.wf .sysnm{font-weight:600;font-size:12.5px;flex:1;position:relative;z-index:1} .wf .sysrow em{font-style:normal;font:600 10px var(--sans);color:var(--ink-3);text-transform:uppercase;letter-spacing:.05em;position:relative;z-index:1}
.wf .sysdot{width:6px;height:6px;border-radius:50%;background:var(--good);position:relative;z-index:1;animation:wfblink 2s ease-in-out infinite}
.wf .sysnote{padding:12px 13px;border-radius:14px;background:var(--surface-2)} .wf .sysnote p{margin:6px 0 0;font-size:12px;color:var(--ink-2);line-height:1.5}
.wf .sysbadge{display:inline-flex;align-items:center;gap:6px;font:700 10px var(--sans);letter-spacing:.07em;text-transform:uppercase;color:var(--ink-3)} .wf .sysbadge .ic{width:12px;height:12px}
.wf .sysdesc{margin:6px 0 0;font-size:12.5px;line-height:1.55;color:var(--ink-2)}
.wf .syshint{display:flex;gap:8px;margin:10px 0 0;padding:9px 11px;border-radius:12px;background:var(--accent-soft);color:var(--accent);font-size:11.5px;line-height:1.5} .wf .syshint .ic{width:14px;height:14px;margin-top:1px}
.wf .delx.plain{border-color:var(--border-strong)!important;color:var(--ink-2)!important} .wf .delx.plain:hover{background:var(--surface-2)!important}
/* settings as cards */
.wf .ibody.cards{gap:14px;padding:16px 16px 32px;background:var(--inset)}
.wf .emptyhint{display:flex;gap:9px;align-items:flex-start;background:var(--accent-soft);color:var(--accent);padding:12px 14px;border-radius:14px;font-size:11.5px;animation:wfrise .4s both} .wf .emptyhint .ic{margin-top:2px}
.wf .card{position:relative;display:flex;flex-direction:column;gap:11px;padding:16px;border-radius:18px;background:var(--surface);box-shadow:0 0 0 1px var(--border),0 6px 18px -14px rgba(0,0,0,.35);animation:wfrise .5s cubic-bezier(.2,.8,.2,1) both;min-width:0;overflow:hidden;transition:transform .25s cubic-bezier(.3,.8,.2,1),box-shadow .25s}
.wf .card:hover,.wf .card:focus-within{transform:translateY(-2px);box-shadow:0 0 0 1px color-mix(in srgb,var(--accent) 45%,var(--border)),0 16px 30px -18px color-mix(in srgb,var(--accent) 55%,transparent)}
.wf .ibody.cards>.card:nth-child(2){animation-delay:.06s}.wf .ibody.cards>.card:nth-child(3){animation-delay:.12s}.wf .ibody.cards>.card:nth-child(4){animation-delay:.18s}.wf .ibody.cards>.card:nth-child(5){animation-delay:.24s}.wf .ibody.cards>.card:nth-child(6){animation-delay:.3s}
.wf .ibody.cards .card:nth-of-type(2){animation-delay:60ms} .wf .ibody.cards .card:nth-of-type(3){animation-delay:120ms} .wf .ibody.cards .card:nth-of-type(4){animation-delay:180ms}
.wf .cardhd{display:flex;align-items:center;gap:8px;font:650 12.5px var(--sans);color:var(--ink)} .wf .cardhd .ic{color:var(--accent);width:28px;height:28px;padding:6px;border-radius:9px;background:var(--accent-soft);box-sizing:border-box;transition:transform .3s cubic-bezier(.3,1.4,.5,1),background .2s,color .2s} .wf .card:hover .cardhd .ic{transform:rotate(-8deg) scale(1.1);background:var(--accent);color:var(--accent-ink)}
.wf .cardhd .cnt{margin-left:auto;min-width:22px;height:20px;padding:0 7px;border-radius:99px;background:var(--surface-2);color:var(--ink-2);font:700 10.5px var(--sans);display:grid;place-items:center}
.wf .card .lab{font-weight:600;font-size:11.5px;color:var(--ink-2)}
.wf .condcard{display:flex;flex-direction:column;gap:6px;padding:11px;border-radius:14px;background:var(--surface-2);box-shadow:inset 0 0 0 1px transparent;transition:box-shadow .2s,transform .2s;animation:wfrise .3s both} .wf .condcard:hover,.wf .condcard:focus-within{box-shadow:inset 0 0 0 1px var(--accent);transform:translateX(2px)}
.wf .condtop{display:flex;gap:6px;align-items:center} .wf .condtop .inp{flex:1}
.wf .condmain{display:grid;grid-template-columns:minmax(0,1fr);gap:6px}
.wf .valrow{display:flex;gap:6px;align-items:center;min-width:0} .wf .valrow .inp{min-width:0} .wf .valrow .inp.cur{width:80px;flex:none}
.wf .xbtn{width:32px;height:32px;border-radius:10px;display:grid;place-items:center;flex:none;color:var(--ink-3);background:var(--surface);box-shadow:inset 0 0 0 1px var(--border);transition:all .15s} .wf .xbtn:hover{color:var(--crit);box-shadow:inset 0 0 0 1px var(--crit);transform:rotate(90deg)} .wf .xbtn:active{transform:rotate(90deg) scale(.88)} .wf .xbtn:hover{background:var(--crit-soft);color:var(--crit);transform:rotate(90deg)}
.wf .addcond{display:inline-flex;align-items:center;justify-content:center;gap:7px;height:36px;border-radius:11px;border:1.5px dashed var(--border-strong);color:var(--accent);font-weight:650;font-size:12.5px;transition:all .15s} .wf .addcond:hover{border-color:var(--accent);background:var(--accent-soft)}
.wf .sentence{display:flex;gap:9px;align-items:flex-start;background:var(--accent-soft);color:var(--accent);padding:10px 12px;border-radius:12px;font-size:12px;animation:wfrise .35s both} .wf .sentence .ic{width:14px;height:14px;flex:none;margin-top:2px}
.wf .clash{display:flex;gap:9px;align-items:flex-start;animation:wfshake .5s both} .wf .clash .ic{width:14px;height:14px;flex:none;margin-top:2px}
@keyframes wfshake{0%,100%{transform:none}25%{transform:translateX(-4px)}50%{transform:translateX(4px)}75%{transform:translateX(-2px)}}
.wf .stepper{display:grid;grid-template-columns:36px 1fr 36px;gap:6px} .wf .stepper button{height:36px;border-radius:10px;background:var(--surface-2);font-size:17px;font-weight:600;color:var(--ink-2);transition:background .15s,transform .15s} .wf .stepper button:hover{background:var(--accent-soft);color:var(--accent)} .wf .stepper button:active{transform:scale(.92)} .wf .stepper .inp{text-align:center;font-weight:700}
.wf .toggle.sm{margin-left:auto;gap:8px;font-size:11.5px} .wf .toggle.sm .sw{width:34px;height:20px} .wf .toggle.sm .sw::after{width:16px;height:16px} .wf .toggle.sm.on .sw::after{left:16px}
.wf .inp,.wf select.inp,.wf .fld,.wf .condmain,.wf .condtop{min-width:0;max-width:100%}
/* softer fields: a tinted fill instead of a box with an outline */
.wf .insp .inp{background:var(--surface-2);border-color:transparent;border-radius:12px;transition:background .15s,border-color .15s,box-shadow .15s}
.wf .insp .inp:hover{background:color-mix(in srgb,var(--surface-2) 70%,var(--accent-soft))}
.wf .insp .inp:focus{background:var(--surface);border-color:var(--accent);box-shadow:0 0 0 4px color-mix(in srgb,var(--accent) 16%,transparent)}
.wf .insp .btn,.wf .insp .abtn{transition:transform .15s,box-shadow .2s,background .2s} .wf .insp .btn:hover,.wf .insp .abtn:hover{transform:translateY(-1px);box-shadow:0 8px 16px -10px var(--accent)} .wf .insp .btn:active,.wf .insp .abtn:active{transform:scale(.96)}
.wf .sib{background:var(--surface-2);padding:8px 12px;border-radius:12px;transition:transform .2s,background .2s} .wf .sib:hover{background:var(--accent-soft);transform:translateX(3px)}
.wf .clash{background:var(--crit-soft);color:var(--crit);padding:9px 12px;border-radius:12px}
.wf .typedesc{background:var(--accent-soft);padding:10px 12px;border-radius:12px}
.wf .assignprev{background:var(--surface-2);border-radius:12px;padding:10px 12px}
.wf .chk{background:var(--surface-2);padding:9px 12px;border-radius:12px}
.wf .orow{box-shadow:none;background:var(--surface-2)} .wf .orow:hover{background:color-mix(in srgb,var(--surface-2) 70%,var(--accent-soft))}
.wf .orow.on{box-shadow:none;background:var(--accent-soft)} .wf .orow.back.on{box-shadow:none;background:var(--crit-soft)}
.wf .ibody{padding-left:18px;padding-right:18px}
.wf .sysnote{background:var(--surface-2);border-radius:12px;padding:10px 12px}
@media (max-width:1100px){.wf .work{grid-template-columns:1fr}.wf .resizer{display:none}.wf .insp{border-top:1px solid var(--border);max-height:55vh}}
/* ---- timeline canvas: stages sit on one rail, no boxes inside boxes ---- */
.wf .canvas{padding:22px 32px 56px}
.wf .flowcol{max-width:720px;margin:0 auto;padding-bottom:6px}
.wf .flowcol::before{content:"";position:absolute;left:16px;top:20px;bottom:20px;width:3px;border-radius:3px;background:linear-gradient(var(--accent) 50%,transparent 0) 0 0/3px 14px repeat-y;opacity:.55;animation:wfrail 1s linear infinite;z-index:0}
@keyframes wfrail{to{background-position:0 14px}}
.wf .node{align-self:flex-start;margin:0}
.wf .node.start{margin-bottom:24px} .wf .node.end{margin-top:6px}
.wf .lane{margin-bottom:26px;padding:0 0 0 58px;background:none;box-shadow:none;border-radius:0;border:0}
.wf .lane:hover{box-shadow:none}
.wf .lane.empty{border:0;background:none;padding-bottom:0}
.wf .lane.hot{box-shadow:none}
.wf .stagehd{min-height:34px;margin-bottom:8px;padding:0;gap:10px}
.wf .stagehd .num{position:absolute;left:0;top:0;width:34px;height:34px;font-size:13px;background:var(--surface);color:var(--accent);box-shadow:0 0 0 2px var(--accent-soft),0 6px 14px -8px rgba(20,26,40,.5);z-index:2}
.wf .lane.empty .stagehd .num{background:var(--surface);color:var(--ink-3);box-shadow:0 0 0 2px var(--border)}
.wf .lane.hot .stagehd .num{background:var(--accent);color:var(--accent-ink);box-shadow:0 0 0 4px color-mix(in srgb,var(--accent) 25%,transparent)}
.wf .stagehd b{font-size:15px;letter-spacing:-.01em}
.wf .stagehd .cnt{padding:3px 10px;border-radius:99px;background:var(--surface);box-shadow:0 0 0 1px var(--border)}
.wf .stagehd:hover b{color:var(--accent)}
.wf .lanein{gap:0}
/* ---- how-to strip ---- */
.wf .howto{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:10px}
.wf .howto li{display:flex;gap:11px;align-items:flex-start;padding:12px 14px;border-radius:16px;background:var(--surface);box-shadow:0 0 0 1px var(--border);font-size:12px;color:var(--ink-2);line-height:1.45;animation:wfrise .5s cubic-bezier(.2,.8,.2,1) both;transition:transform .2s,box-shadow .2s}
.wf .howto li:nth-child(2){animation-delay:.07s}.wf .howto li:nth-child(3){animation-delay:.14s}
.wf .howto li:hover{transform:translateX(3px);box-shadow:0 0 0 1px var(--accent)}
.wf .howto li>span{flex:none;width:24px;height:24px;border-radius:50%;display:grid;place-items:center;background:var(--accent-soft);color:var(--accent);font:700 11.5px var(--sans)}
.wf .howto b{display:block;color:var(--ink);font-size:12.5px;margin-bottom:1px}
.wf .howto .pl{font-style:normal;display:inline-grid;place-items:center;width:16px;height:16px;border-radius:50%;border:1.5px dashed var(--accent);color:var(--accent);font-weight:700;line-height:1;vertical-align:-3px}
/* ---- conditions: question on top, then "is / value" side by side ---- */
.wf .condcard{gap:8px;padding:12px;border-radius:16px;background:var(--surface-2)}
.wf .condtop{gap:8px}
.wf .condmain{grid-template-columns:minmax(0,.8fr) minmax(0,1.2fr);gap:8px;align-items:center}
.wf .condcard .inp{height:38px;padding:0 12px;font-size:12.5px}
.wf .condcard .valrow{display:flex;gap:6px;align-items:center;min-width:0} .wf .condcard .valrow .inp{flex:1;min-width:0} .wf .condcard .valrow .cur{flex:none;width:68px}
.wf .condcard .valrow .hint{white-space:nowrap;font-size:11px}
.wf .insp .inp{height:38px;padding:0 12px}
/* ---- node-graph canvas: a centred flow of nodes on one animated line, floating tools underneath ---- */
.wf .canvas{padding:20px 24px 24px;display:flex;flex-direction:column}
.wf .flowcol{max-width:none;width:100%;padding:0 0 28px}
.wf .flowcol::before{left:50%;margin-left:-1.5px;top:18px;bottom:46px;opacity:.5}
.wf .node{align-self:center}
.wf .node.start{margin-bottom:10px}.wf .node.end{margin-top:12px}
.wf .lane{width:100%;margin:0;padding:0;align-items:center}
.wf .stagehd{width:auto;align-self:center;margin:8px 0 2px;padding:4px 12px 4px 4px;border-radius:99px;background:var(--surface);box-shadow:0 0 0 1px var(--border),0 8px 18px -12px rgba(20,26,40,.45);transition:box-shadow .2s,transform .2s}
.wf .stagehd:hover{transform:translateY(-1px);box-shadow:0 0 0 1px var(--accent),0 12px 22px -12px color-mix(in srgb,var(--accent) 55%,transparent)}
.wf .stagehd .num{position:relative;left:auto;top:auto;width:26px;height:26px;font-size:12px;box-shadow:none;background:var(--accent-soft);color:var(--accent)}
.wf .lane.empty .stagehd .num{background:var(--surface-2);box-shadow:none}
.wf .lane.hot .stagehd{box-shadow:0 0 0 1.5px var(--accent),0 12px 24px -12px color-mix(in srgb,var(--accent) 60%,transparent)}
.wf .stagehd b{font-size:13px}.wf .stagehd:hover b{color:var(--ink)}
.wf .stagehd .cnt{margin-left:4px;padding:0;background:none;box-shadow:none;font-size:11px}
.wf .stagehd .chev{margin-left:2px}
.wf .lanebody{width:100%}
.wf .lanein{align-items:center;width:100%}
.wf .cardwrap,.wf .sysbox{width:min(400px,100%)}
.wf .pgroup{width:min(860px,100%)}
.wf .pgsteps{flex-direction:row;flex-wrap:wrap}.wf .pgsteps>*{flex:1 1 250px;min-width:0}
.wf .stepcard{overflow:visible;padding:14px 16px;border-radius:16px}
.wf .stepcard::before,.wf .stepcard::after{content:"";position:absolute;left:50%;width:9px;height:9px;margin-left:-4.5px;border-radius:50%;background:var(--surface);z-index:2}
.wf .stepcard::before{top:-5px;border:2px solid var(--good)}
.wf .stepcard::after{bottom:-5px;border:2px solid var(--accent);transition:background .25s}
.wf .stepcard:hover::after,.wf .stepcard.sel::after{background:var(--accent)}
.wf .stepcard .tic{width:40px;height:40px;border-radius:12px}
.wf .stepcard .nm{font-size:14px}
.wf .adder{padding:9px 0}.wf .adder.sm{padding:9px 0}
/* floating tools */
.wf .dock{position:sticky;bottom:4px;align-self:center;margin-top:8px;display:flex;align-items:center;gap:2px;padding:6px;width:max-content;max-width:100%;border-radius:18px;background:var(--surface);box-shadow:0 0 0 1px var(--border),0 20px 44px -16px rgba(20,26,40,.55);z-index:6;animation:wfrise .55s .35s cubic-bezier(.2,.8,.2,1) both}
.wf .dock button{min-width:36px;height:36px;border-radius:12px;display:inline-flex;align-items:center;justify-content:center;gap:7px;color:var(--ink-2);font:650 12px var(--sans);transition:background .15s,color .15s,transform .15s}
.wf .dock button:hover:not([disabled]){background:var(--accent-soft);color:var(--accent)} .wf .dock button:active:not([disabled]){transform:scale(.9)} .wf .dock button[disabled]{opacity:.35;cursor:default} .wf .dock button.on{background:var(--accent-soft);color:var(--accent)}
.wf .dock .zv{min-width:50px;text-align:center;font:700 12px var(--sans);color:var(--ink)}
.wf .dock .sep{width:1px;height:20px;background:var(--border);margin:0 5px}
.wf .dock .dchk{padding:0 12px;color:var(--good)} .wf .dock .dchk.bad{color:var(--warn);background:var(--warn-soft)}
.wf .dock svg{width:16px;height:16px}
/* ---- inspector: header, segmented tabs, pinned footer ---- */
.wf .insp{overflow:hidden}
.wf .inspin{flex:1;min-height:0}
.wf .ibody{overflow:auto;flex:1;min-height:0}
.wf .insp .ih{padding:16px 18px}
.wf .xclose{margin-left:auto;width:34px;height:34px;border-radius:50%;display:grid;place-items:center;color:var(--ink-3);transition:background .15s,color .15s,transform .25s} .wf .xclose:hover{background:var(--surface-2);color:var(--ink);transform:rotate(90deg)}
.wf .itabs{margin:4px 18px 0;padding:4px;border:0;border-radius:13px;background:var(--surface-2)}
.wf .itab{z-index:1;padding:8px 4px;border-radius:10px} .wf .itab.on{color:var(--ink)}
.wf .itab-bar{left:4px;top:4px;bottom:auto;height:calc(100% - 8px);width:calc((100% - 8px) / 4);border-radius:10px;background:var(--surface);box-shadow:0 1px 3px rgba(20,26,40,.18),0 0 0 1px var(--border)}
.wf .ifoot{display:flex;gap:10px;padding:14px 18px;border-top:1px solid var(--border);background:var(--surface);animation:wfrise .4s both}
.wf .ifoot .fb{flex:1;height:42px;border-radius:12px;display:inline-flex;align-items:center;justify-content:center;gap:8px;font:650 13px var(--sans);transition:transform .15s,box-shadow .2s,background .2s}
.wf .ifoot .fb svg{width:15px;height:15px}
.wf .ifoot .fb.dng{background:var(--surface);color:var(--crit);box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--crit) 40%,var(--border))} .wf .ifoot .fb.dng:hover{background:var(--crit-soft)}
.wf .ifoot .fb.pri{background:var(--accent);color:var(--accent-ink)} .wf .ifoot .fb.pri:hover{box-shadow:0 10px 20px -10px var(--accent);transform:translateY(-1px)}
.wf .ifoot .fb:active{transform:scale(.96)}
/* ---- inspector, drawn like a form: bold labels, tall white fields, quiet sections, solid footer buttons ---- */
.wf .insp,.wf .insp .ibody,.wf .insp .ibody.cards{background:var(--surface)}
.wf .insp .ih{padding:20px 22px 12px;border-bottom:0;align-items:center}
.wf .insp .ih .tic{width:38px;height:38px;border-radius:11px}
.wf .insp .iht b{font-size:17px;letter-spacing:-.01em} .wf .insp .iht em{font-size:12px}
.wf .titlefld{padding:6px 22px 0} .wf .titlefld::before{content:"Step name";display:block;margin-bottom:7px;font:650 13px var(--sans);color:var(--ink)}
.wf .itabs{margin:16px 22px 0}
.wf .insp .ibody{padding:20px 22px 26px;gap:22px}
.wf .fld{gap:8px} .wf .fld .lab,.wf .card .lab,.wf .insp label.lab{font:650 13px var(--sans);color:var(--ink)}
.wf .insp .inp{height:44px;padding:0 14px;border-radius:12px;background:var(--surface);border:1px solid var(--border-strong);font-size:14px;color:var(--ink)}
.wf .insp textarea.inp{height:auto;min-height:104px;padding:12px 14px;line-height:1.5;resize:vertical}
.wf .insp .inp:hover{background:var(--surface);border-color:color-mix(in srgb,var(--accent) 45%,var(--border-strong))}
.wf .insp .inp:focus{background:var(--surface);border-color:var(--accent);box-shadow:0 0 0 4px color-mix(in srgb,var(--accent) 14%,transparent)}
.wf .typedesc{background:none;padding:0;color:var(--ink-3);font-size:12px}
.wf .hint{font-size:12px}
.wf .insp .ibody.cards{gap:26px;padding-top:12px}
.wf .insp .card{padding:0;gap:11px;border-radius:0;background:none;box-shadow:none;overflow:visible;transform:none!important}
.wf .insp .card::before{display:none}
.wf .insp .card:hover,.wf .insp .card:focus-within{box-shadow:none;transform:none}
.wf .insp .cardhd{font:650 13px var(--sans)} .wf .insp .cardhd .ic{width:26px;height:26px;padding:6px}
.wf .insp .card:hover .cardhd .ic{transform:none;background:var(--accent-soft);color:var(--accent)}
.wf .condcard{background:var(--surface);box-shadow:inset 0 0 0 1px var(--border);padding:14px;border-radius:14px}
.wf .condcard:hover,.wf .condcard:focus-within{transform:none;box-shadow:inset 0 0 0 1px var(--accent)}
.wf .condcard .inp{height:42px}
.wf .xbtn{border-radius:10px;width:38px;height:38px;box-shadow:inset 0 0 0 1px var(--border-strong)}
.wf .addcond{height:42px;border-radius:12px;font-size:13px}
.wf .ifoot{padding:16px 22px 18px;gap:12px}
.wf .ifoot .fb{height:46px;border-radius:12px;font-size:14px}
.wf .ifoot .fb.dng{color:var(--ink);box-shadow:inset 0 0 0 1px var(--border-strong)} .wf .ifoot .fb.dng:hover{background:var(--crit-soft);color:var(--crit);box-shadow:inset 0 0 0 1px var(--crit)}
.wf .howto li{box-shadow:inset 0 0 0 1px var(--border);background:var(--surface)}
.wf .toggle.sm{font-size:12px}
/* conditions: pick the question from chips, answer by tapping, no dropdown hunting */
.wf .condq{font:650 13.5px var(--sans);color:var(--ink);flex:1;min-width:0}
.wf .condtop{justify-content:space-between}
.wf .condcard .xbtn{width:30px;height:30px;border-radius:50%}
.wf .seg{display:grid;grid-auto-flow:column;grid-auto-columns:1fr;gap:3px;padding:3px;border-radius:11px;background:var(--surface-2)}
.wf .seg button{height:32px;border-radius:9px;font:600 12.5px var(--sans);color:var(--ink-3);transition:background .2s,color .2s,box-shadow .2s}
.wf .seg button:hover{color:var(--ink)}
.wf .seg button.on{background:var(--surface);color:var(--ink);box-shadow:0 1px 3px rgba(20,26,40,.18),0 0 0 1px var(--border)}
.wf .chips{display:flex;flex-wrap:wrap;gap:7px}
.wf .chipb{display:inline-flex;align-items:center;gap:6px;min-height:34px;padding:0 13px;border-radius:99px;font:600 12.5px var(--sans);color:var(--ink-2);background:var(--surface);box-shadow:inset 0 0 0 1px var(--border-strong);transition:transform .15s,background .2s,color .2s,box-shadow .2s;animation:wfpop .25s both}
.wf .chipb:hover{box-shadow:inset 0 0 0 1px var(--accent);color:var(--accent);transform:translateY(-1px)}
.wf .chipb:active{transform:scale(.94)}
.wf .chipb.on{background:var(--accent);color:var(--accent-ink);box-shadow:0 8px 16px -10px var(--accent)}
.wf .chipb.add{border-style:dashed;box-shadow:inset 0 0 0 1.5px var(--border-strong);color:var(--accent)} .wf .chipb.add svg{width:13px;height:13px}
.wf .addq{display:flex;flex-direction:column;gap:9px;padding-top:2px}
/* conditions v3: one compact row per condition, inline is / is not, one add menu */
.wf .opt{display:inline-flex;padding:2px;border-radius:99px;background:var(--surface);box-shadow:inset 0 0 0 1px var(--border)}
.wf .opt button{height:24px;padding:0 10px;border-radius:99px;font:600 11.5px var(--sans);color:var(--ink-3);transition:background .2s,color .2s}
.wf .opt button.on{background:var(--accent);color:var(--accent-ink)}
.wf .opt button:not(.on):hover{color:var(--ink)}
.wf .condcard .chipb{min-height:30px;padding:0 11px;font-size:12px}
.wf .condadd{display:flex;align-items:center;gap:8px;width:100%;height:40px;padding:0 14px;border-radius:12px;font:600 13px var(--sans);color:var(--accent);box-shadow:inset 0 0 0 1.5px var(--border-strong);border-style:dashed;transition:background .2s,box-shadow .2s}
.wf .condadd:hover{background:var(--accent-soft);box-shadow:inset 0 0 0 1.5px var(--accent)}
.wf .condadd svg{width:14px;height:14px} .wf .condadd .cnt{margin-left:auto}
.wf .addmenu{list-style:none;margin:0;padding:4px;border-radius:12px;background:var(--surface);box-shadow:0 0 0 1px var(--border),0 12px 28px -14px rgba(20,26,40,.35);display:flex;flex-direction:column;animation:wfrise .2s both}
.wf .addmenu button{display:flex;align-items:center;justify-content:space-between;gap:10px;width:100%;min-height:38px;padding:0 12px;border-radius:9px;text-align:left;font:600 13px var(--sans);color:var(--ink);transition:background .15s}
.wf .addmenu button:hover{background:var(--surface-2);color:var(--accent)}
.wf .addmenu small{font:500 11px var(--sans);color:var(--ink-3)}
@media (prefers-reduced-motion:reduce){.wf *{animation:none!important;transition:none!important}.wf .lane,.wf .sysrow{opacity:1!important}.wf .stepcard .ctrls{opacity:1}}
`;
