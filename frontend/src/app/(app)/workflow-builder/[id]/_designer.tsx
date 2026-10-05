"use client";

// Workflow Designer (Screen 6) — a full-screen visual flow builder. A vertical
// node canvas (each step: icon + assigned team + SLA + outcome chips, with
// per-step conditions) and a right inspector to edit the selected step or the
// flow's settings. Wired to workflowsApi (list / get / create / update) + rolesApi.
// Scoped under `.wf`. The engine is sequential, so steps render linearly.

import { Fragment, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { intakeApi, workflowsApi, usersApi } from "@/lib/endpoints";

import { useToast } from "@/components/toast";
import type { LifecycleStage, Workflow, WorkflowCondition, WorkflowStepType } from "@/lib/types";
import { useRequestForms } from "../../intake/_agreement-forms";
import { AMOUNT_OPS, CHOICE_OPS, DATE_OPS, OP_LABEL as COND_OP_LABEL, overlaps, pinnedRequestTypes, questionsFor, typeKey, typeOptions, whenText } from "@/lib/workflow-types";

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
  ai_task: "No human — the system does it (file the contract, start tracking).",
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

  const patchStep = (i: number, p: Partial<Step>) => setSteps((ss) => ss.map((x, idx) => (idx === i ? { ...x, ...p } : x)));
  const patchCfg = (i: number, k: string, v: unknown) => setSteps((ss) => ss.map((x, idx) => (idx === i ? { ...x, config: { ...x.config, [k]: v } } : x)));
  // A new step joins the stage of the step above it (Review at the very start).
  const addAt = (i: number, stage: LifecycleStage) => { setSteps((ss) => { const n = [...ss]; n.splice(i, 0, { type: "human_task", name: "New step", config: {}, stage }); return n; }); setSel(i); };
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

  const cardJsx = (i: number) => {
    const s = steps[i];
    const m = TYPE_META[s.type];
    const role = teamName(cfgStr(s.config, "team_id"));
    const sla = cfgStr(s.config, "sla_hours");
    const cl = condLabel(s);
    return (
      <div className={`stepcard ${sel === i ? "sel" : ""}`} onClick={() => setSel(i)}>
        <div className="ctrls">
          {i > 0 && <button className={s.parallel ? "on" : ""} title={s.parallel ? "Make sequential" : "Run in parallel with the step above"} onClick={(e) => { e.stopPropagation(); toggleParallel(i); }}>{svg('<path d="M8 3v3a2 2 0 0 1-2 2H3M16 3v3a2 2 0 0 0 2 2h3M8 21v-3a2 2 0 0 0-2-2H3M16 21v-3a2 2 0 0 1 2-2h3"/>')}</button>}
          <button title="Delete" onClick={(e) => { e.stopPropagation(); del(i); }}>{svg('<path d="M18 6 6 18M6 6l12 12"/>')}</button>
        </div>
        {cl ? <div className="cond">{svg('<path d="M8 3v3a2 2 0 0 1-2 2H3M16 3v3a2 2 0 0 0 2 2h3"/>')}{cl}</div> : null}
        <div className="top">
          <div className={`tic t-${m.tone}`}>{svg(m.icon)}</div>
          <div className="snm">{s.name || "Untitled step"} <span className="idx">{i + 1}</span></div>
        </div>
        <div className="meta">
          {role ? <span className="chip dept">{svg('<path d="M20 21a8 8 0 0 0-16 0"/><circle cx="12" cy="7" r="4"/>')}{role}</span> : null}
          {sla ? <span className="chip sla">SLA {sla}h</span> : null}
          {outcomesOf(s).map((k) => <span key={k} className={`chip out${BACK_OUTCOMES.has(k) ? " neg" : ""}`}>{OUTCOME_SHORT[k] ?? k}</span>)}
        </div>
        {hasBack(s) ? <div className="backedge">{svg('<path d="M9 14 4 9l5-5"/><path d="M4 9h11a5 5 0 0 1 5 5v3"/>')}sends back to {returnLabel(s, i)}</div> : null}
      </div>
    );
  };

  return (
    <div className="wf">
      <style dangerouslySetInnerHTML={{ __html: WF_CSS }} />
      <div className="wfapp">

        <div className="wfmain">
          {/* topbar */}
          <div className="topbar">
            <select className="flowsel" value={flow?.id ?? "new"} onChange={(e) => router.push(`/workflow-builder/${e.target.value}`)}>
              {isNew ? <option value="new">New workflow…</option> : null}
              {(allFlows ?? []).map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}
            </select>
            <input className="wftitle" value={name} placeholder="Untitled workflow" onChange={(e) => setName(e.target.value)} />
            {flow ? <span className="bdg ver">v{flow.version}</span> : null}
            <span className={`bdg ${enabled ? "active" : "draft"}`}>{enabled ? "Published" : "Draft"}</span>
            <div style={{ flex: 1 }} />
            <button className="tbtn" disabled={busy} onClick={() => notify("Test run needs a sample request — start one from a ticket.", "success")}>{svg('<path d="M5 3l14 9-14 9z"/>')}Test run</button>
            <button className="tbtn" disabled={busy} onClick={() => save()}>{svg('<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/>')}Save</button>
            <button className="tbtn pri" disabled={busy} onClick={() => save(true)}>{svg('<path d="M20 6 9 17l-5-5"/>')}Publish</button>
          </div>

          <div className="work">
            {/* canvas */}
            <div className="canvas">
              <div className="flowcol">
                <div className="node start">{svg('<circle cx="12" cy="12" r="9"/><path d="m9 12 2 2 4-4"/>')}Request confirmed</div>
                {/* Every stage shows, empty or not, so a step can be added to any of them. */}
                {STAGES.map((st, si) => {
                  const gs = groups.filter((g) => (steps[g[0]].stage ?? "review") === st.key);
                  const endOfStage = steps.filter((x) => STAGE_RANK[x.stage ?? "review"] <= si).length;
                  return (
                    <Fragment key={st.key}>
                      <div className={`stagehd${gs.length ? "" : " empty"}`}>
                        <span className="num">Stage {si + 1}</span>{st.label}
                        {gs.length ? null : <span className="none">no steps</span>}
                      </div>
                      {gs.map((g) => (
                        <Fragment key={`g${steps[g[0]].id ?? g[0]}`}>
                          <div className="conn" />
                          <button className="addbtn" title={`Add a step here (${st.label})`} onClick={() => addAt(g[0], st.key)}>{svg('<path d="M12 5v14M5 12h14"/>')}</button>
                          <div className="conn" />
                          {g.length === 1 ? cardJsx(g[0]) : (
                            <div className="pgroup">
                              <div className="pghd">{svg('<path d="M8 3v3a2 2 0 0 1-2 2H3M16 3v3a2 2 0 0 0 2 2h3M8 21v-3a2 2 0 0 0-2-2H3M16 21v-3a2 2 0 0 1 2-2h3"/>')}Parallel · all must respond</div>
                              <div className="pgsteps">{g.map((i) => <Fragment key={steps[i].id ?? i}>{cardJsx(i)}</Fragment>)}</div>
                            </div>
                          )}
                        </Fragment>
                      ))}
                      <div className="conn" />
                      <button className="addbtn" title={`Add a step in ${st.label}`} onClick={() => addAt(endOfStage, st.key)}>{svg('<path d="M12 5v14M5 12h14"/>')}</button>
                    </Fragment>
                  );
                })}
                <div className="conn" />
                <div className="node end">{svg('<path d="M20 6 9 17l-5-5"/>')}Complete</div>
              </div>
            </div>

            {/* inspector */}
            <div className="insp">
              {cur ? (
                <>
                  <div className="ih">
                    <div className={`tic t-${TYPE_META[cur.type].tone}`}>{svg(TYPE_META[cur.type].icon)}</div>
                    <b>{TYPE_META[cur.type].label} step</b>
                    <button className="delx" onClick={() => del(sel!)}>Delete</button>
                  </div>
                  <div className="titlefld"><input className="inp" value={cur.name} onChange={(e) => patchStep(sel!, { name: e.target.value })} placeholder="Step name" /></div>
                  <div className="itabs">
                    {(["setup", "assign", "outcomes", "rules"] as const).map((k) => {
                      const label = { setup: "Setup", assign: "Assignment", outcomes: "Outcomes", rules: "Rules" }[k];
                      const dot = (k === "outcomes" && outcomesOf(cur).length > 0) || (k === "rules" && (!!cur.parallel || !!cur.cond?.field));
                      return <button key={k} className={`itab${tab === k ? " on" : ""}`} onClick={() => setTab(k)}>{label}{dot ? <span className="tdot" /> : null}</button>;
                    })}
                  </div>
                  <div className="ibody">
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
                              <option value="">Auto-pick by request type</option>
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
                                    {OUTCOME_LABEL[k]}
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
                </>
              ) : (
                <>
                  <div className="ih"><div className="tic t-accent">{svg('<circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="12" r="2.5"/><path d="M8.5 6H14a2 2 0 0 1 2 2v2M8.5 18H14a2 2 0 0 0 2-2v-2"/>')}</div><b>Workflow settings</b></div>
                  <div className="ibody">
                    <div className="emptyhint">Select a step on the canvas to edit it, or use a <b>+</b> to add one. These settings decide when this workflow is picked.</div>
                    <div className="fld"><label className="lab">Agreement type</label>
                      <select className="inp" value={typeSel} onChange={(e) => { setTypeSel(e.target.value); setConds([]); }}>
                        <option value="">None: email and chat requests only</option>
                        {types.map((t) => <option key={t.key} value={t.key}>{t.label}</option>)}
                      </select>
                      <div className="hint">{typeOpt ? `Only ${typeOpt.label} requests can get this workflow.` : "Form requests never get an untyped workflow; the AI may suggest it for email and chat requests."}</div>
                      {!typeOpt && pinnedRequestTypes(flow).length > 0 && (
                        <div className="hint">Always used for {pinnedRequestTypes(flow).map((t) => `“${t}”`).join(", ")} requests (except ones triage marks as litigation).</div>
                      )}
                    </div>
                    {typeOpt ? (
                      <div className="fld"><label className="lab">Chosen when</label>
                        {conds.map((c, i) => {
                          const q = questions.find((x) => x.field === c.field);
                          const amount = AMOUNT_OPS.includes(c.op);
                          return (
                            <div key={i} className="condbox">
                              <div className="condrow">
                                <select className="inp" aria-label="Question" value={c.field} onChange={(e) => patchCond(i, freshCond(e.target.value))}>
                                  {questions.map((x) => <option key={x.field} value={x.field}>{x.label}</option>)}
                                </select>
                                <button className="tbtn" title="Remove condition" onClick={() => setConds(conds.filter((_, j) => j !== i))}>{svg('<path d="M18 6 6 18M6 6l12 12"/>')}</button>
                              </div>
                              <select className="inp" aria-label="Operator" value={c.op}
                                onChange={(e) => patchCond(i, { op: e.target.value as WorkflowCondition["op"], ...(e.target.value === "between" && !c.value2 ? { value2: Number(c.value || 0) * 5 || 5000000 } : {}) })}>
                                {(q?.date ? DATE_OPS : q?.amount ? AMOUNT_OPS : CHOICE_OPS).map((o) => <option key={o} value={o}>{COND_OP_LABEL[o]}</option>)}
                              </select>
                              {q?.date ? (
                                <div className="condrow">
                                  <input className="inp" inputMode="numeric" aria-label="Days" value={String(c.value ?? "")} onChange={(e) => patchCond(i, { value: e.target.value.replace(/[^0-9]/g, "") })} />
                                  <span className="hint">days of the request</span>
                                </div>
                              ) : amount ? (
                                <div className="condrow">
                                  <select className="inp cur" aria-label="Currency" value={c.currency ?? "INR"} onChange={(e) => patchCond(i, { currency: e.target.value })}>
                                    {["INR", "USD", "EUR", "GBP"].map((x) => <option key={x}>{x}</option>)}
                                  </select>
                                  <input className="inp" inputMode="numeric" aria-label="Amount" value={String(c.value ?? "")} onChange={(e) => patchCond(i, { value: e.target.value.replace(/[^0-9]/g, "") })} />
                                  {c.op === "between" && <><span className="hint">and</span>
                                    <input className="inp" inputMode="numeric" aria-label="Upper amount" value={String(c.value2 ?? "")} onChange={(e) => patchCond(i, { value2: Number(e.target.value.replace(/[^0-9]/g, "")) })} /></>}
                                </div>
                              ) : (
                                <select className="inp" aria-label="Answer" value={String(c.value)} onChange={(e) => patchCond(i, { value: e.target.value })}>
                                  {(q?.options ?? []).map((o) => <option key={o}>{o}</option>)}
                                </select>
                              )}
                            </div>
                          );
                        })}
                        {questions.length ? (
                          <button className="tbtn" onClick={() => setConds([...conds, freshCond((questions.find((x) => !conds.some((c) => c.field === x.field)) ?? questions[0]).field)])}>+ Add a condition</button>
                        ) : <div className="hint">This type&apos;s form asks nothing a condition can use.</div>}
                        <div className="sentence">Used for {typeOpt.label} requests {conds.length ? `when ${whenText(conds, qLabels)}.` : `when no other ${typeOpt.short} workflow fits.`}</div>
                        <div className="hint">A request with a question left blank, or a value in another currency, doesn&apos;t meet a condition on it.</div>
                      </div>
                    ) : (
                      <div className="fld"><label className="lab">Match words (email and chat requests)</label>
                        <input className="inp" value={matchType} onChange={(e) => setMatchType(e.target.value)} placeholder="type word, e.g. litigation, notice" />
                        <input className="inp" value={matchKeyword} onChange={(e) => setMatchKeyword(e.target.value)} placeholder="keyword in the request" />
                        <label className="lab">When should AI choose this workflow?</label>
                        <textarea
                          className="inp"
                          rows={3}
                          value={aiCondition}
                          onChange={(e) => setAiCondition(e.target.value)}
                          placeholder="e.g. Use this for any litigation hold notice from outside counsel."
                          style={{ resize: "vertical", fontFamily: "inherit" }}
                        />
                        <div className="hint">Free text read by the AI that picks a workflow for email and chat requests, in addition to the match words.</div>
                      </div>
                    )}
                    {typeOpt ? (
                      <div className="fld"><label className="lab">Other {typeOpt.short} workflows</label>
                        {siblings.length ? siblings.map((f) => (
                          <div key={f.id} className="sib"><b>{f.name}</b><span>Chosen when {whenText(f.criteria.conditions)}</span></div>
                        )) : <div className="hint">None: this is the only one.</div>}
                        {clash && <div className="clash">A request could fit both this and “{clash.name}”. Change a condition so they don&apos;t overlap — otherwise the one with more conditions wins.</div>}
                      </div>
                    ) : null}
                    <div className="fld"><label className="lab">Priority order</label><input className="inp" type="number" value={evalOrder} onChange={(e) => setEvalOrder(e.target.value)} /><div className="hint">Only breaks a tie when two workflows both fit a request: lower wins.</div></div>
                    <label className="chk"><input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />Enabled (available to pick)</label>
                  </div>
                </>
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
.wf *{box-sizing:border-box} .wf .dim{color:var(--ink-3)} .wf .lbl{font:600 10px var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3)}
.wf .ic{width:15px;height:15px;flex:none;stroke:currentColor;stroke-width:1.7;fill:none;stroke-linecap:round;stroke-linejoin:round}
.wf button{cursor:pointer;border:0;background:none;color:inherit;font:inherit} .wf b{font-weight:660}
.wf .wfapp{display:grid;grid-template-columns:1fr;height:100%}
.wf .rail{background:var(--surface);border-right:1px solid var(--border);display:flex;flex-direction:column;min-height:0}
.wf .brand{display:flex;align-items:center;gap:9px;padding:14px 16px 12px;border-bottom:1px solid var(--border)} .wf .brand .mark{width:26px;height:26px;border-radius:8px;background:var(--accent);color:var(--accent-ink);display:grid;place-items:center;font-weight:700} .wf .brand b{font-size:15px}
.wf .nav{padding:8px;display:flex;flex-direction:column;gap:1px;overflow:auto;flex:1} .wf .nav .sec{padding:11px 8px 4px}
.wf .nav a{display:flex;align-items:center;gap:10px;padding:7px 9px;border-radius:8px;color:var(--ink-2);font-weight:500;text-decoration:none}
.wf .nav a:hover{background:var(--surface-2);color:var(--ink)} .wf .nav a.on{background:var(--accent-soft);color:var(--accent);font-weight:600}
.wf .me{border-top:1px solid var(--border);padding:10px 13px;display:flex;align-items:center;gap:9px} .wf .me .av{width:28px;height:28px;border-radius:50%;background:var(--accent-soft);color:var(--accent);display:grid;place-items:center;font-weight:700;font-size:12px}
.wf .wfmain{min-width:0;display:flex;flex-direction:column;min-height:0}
.wf .topbar{flex:none;display:flex;align-items:center;gap:10px;padding:10px 16px;border-bottom:1px solid var(--border);background:var(--surface)}
.wf .flowsel{padding:6px 10px;border:1px solid var(--border-strong);border-radius:8px;background:var(--surface);color:var(--ink);font-weight:600;font-size:12.5px;max-width:210px}
.wf .wftitle{border:0;background:none;font-weight:660;font-size:15px;color:var(--ink);outline:none;min-width:120px;flex:0 1 auto} .wf .wftitle:focus{border-bottom:1px solid var(--accent)}
.wf .bdg{font:600 10px var(--sans);padding:2px 9px;border-radius:99px} .wf .bdg.ver{background:var(--surface-2);color:var(--ink-2)} .wf .bdg.draft{background:var(--warn-soft);color:var(--warn)} .wf .bdg.active{background:var(--good-soft);color:var(--good)}
.wf .tbtn{display:inline-flex;align-items:center;gap:6px;padding:7px 12px;border-radius:8px;border:1px solid var(--border-strong);background:var(--surface);color:var(--ink);font-weight:600;font-size:12.5px} .wf .tbtn:hover{background:var(--surface-2)} .wf .tbtn.pri{background:var(--accent);border-color:var(--accent);color:var(--accent-ink)} .wf .tbtn[disabled]{opacity:.5;pointer-events:none}
.wf .work{flex:1;min-height:0;display:grid;grid-template-columns:1fr 320px}
.wf .canvas{overflow:auto;padding:26px;background:var(--bg);background-image:radial-gradient(var(--border) 1px,transparent 1px);background-size:22px 22px}
.wf .flowcol{display:flex;flex-direction:column;align-items:stretch;max-width:520px;margin:0 auto}
.wf .node{align-self:center;display:inline-flex;align-items:center;gap:8px;padding:8px 15px;border-radius:99px;font-weight:650;font-size:12.5px;box-shadow:var(--shadow)} .wf .node.start{background:var(--ink);color:var(--bg)} .wf .node.end{background:var(--good);color:#fff} .wf .node .ic{width:15px;height:15px}
.wf .conn{align-self:center;width:2px;height:16px;background:var(--border-strong)}
.wf .addbtn{align-self:center;width:26px;height:26px;border-radius:50%;border:1.5px dashed var(--border-strong);color:var(--ink-3);display:grid;place-items:center;background:var(--surface)} .wf .addbtn:hover{border-color:var(--accent);color:var(--accent)} .wf .addbtn .ic{width:14px;height:14px}
.wf .stepcard{border:1px solid var(--border);border-radius:12px;background:var(--surface);box-shadow:var(--shadow);padding:12px 13px;cursor:pointer;position:relative;transition:.1s}
.wf .stepcard:hover{border-color:var(--border-strong)} .wf .stepcard.sel{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft),var(--shadow)}
.wf .stepcard .top{display:flex;align-items:center;gap:10px}
.wf .stepcard .tic{width:30px;height:30px;border-radius:8px;display:grid;place-items:center;flex:none}
.wf .tic.t-good{background:var(--good-soft);color:var(--good)} .wf .tic.t-warn{background:var(--warn-soft);color:var(--warn)} .wf .tic.t-accent{background:var(--accent-soft);color:var(--accent)} .wf .tic.t-ext{background:var(--ext-soft);color:var(--ext)} .wf .tic.t-ink{background:var(--surface-2);color:var(--ink-2)}
.wf .stepcard .snm{font-weight:660;font-size:13px} .wf .stepcard .idx{font:600 10px var(--mono);color:var(--ink-3)}
.wf .stepcard .meta{display:flex;gap:6px;flex-wrap:wrap;margin-top:9px}
.wf .cond{margin-bottom:8px;display:inline-flex;align-items:center;gap:6px;font:600 10.5px var(--sans);color:var(--warn);background:var(--warn-soft);padding:3px 9px;border-radius:8px} .wf .cond .ic{width:12px;height:12px}
.wf .pgroup{align-self:stretch;max-width:520px;border:1.5px dashed var(--border-strong);border-radius:14px;background:color-mix(in srgb,var(--accent) 5%,var(--surface));padding:10px 11px 12px}
.wf .authlist{display:flex;flex-direction:column;border:1px solid var(--border);border-radius:9px;overflow:hidden}
.wf .authlist>div{display:flex;justify-content:space-between;gap:10px;padding:6px 10px;font-size:12px;border-top:1px solid var(--border)} .wf .authlist>div:first-child{border-top:0}
.wf .authlist b{font-weight:600} .wf .authlist .none b{color:var(--warn)}
.wf .stagehd.empty{color:var(--ink-3)} .wf .stagehd .none{margin-left:auto;font:500 11px var(--sans);color:var(--ink-3)}
.wf .stagehd{align-self:stretch;display:flex;align-items:baseline;gap:10px;margin:18px 0 2px;padding-bottom:6px;border-bottom:1px solid var(--border);font:600 13px var(--sans);color:var(--ink)} .wf .stagehd .num{font:600 10px var(--mono,var(--sans));letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3)}
.wf .pghd{display:inline-flex;align-items:center;gap:6px;margin:0 0 9px;font:600 10px var(--sans);letter-spacing:.06em;text-transform:uppercase;color:var(--accent);background:var(--accent-soft);padding:3px 9px;border-radius:99px} .wf .pghd .ic{width:12px;height:12px}
.wf .pgsteps{display:flex;flex-direction:column;gap:9px}
.wf .condrow{display:grid;grid-template-columns:1fr auto 1fr;gap:6px} .wf .condrow .op{min-width:72px}
.wf .lnk{color:var(--accent);font-weight:600}
.wf .chip{display:inline-flex;align-items:center;gap:5px;padding:2px 8px;border-radius:99px;font:600 10.5px var(--sans);background:var(--surface-2);color:var(--ink-2)} .wf .chip .ic{width:11px;height:11px}
.wf .chip.dept{background:var(--accent-soft);color:var(--accent)} .wf .chip.out{background:transparent;border:1px solid var(--border);color:var(--ink-2)} .wf .chip.out.neg{border-color:color-mix(in srgb,var(--crit) 40%,var(--border));color:var(--crit)}
.wf .stepcard .ctrls{position:absolute;top:9px;right:9px;display:none;gap:3px} .wf .stepcard:hover .ctrls,.wf .stepcard.sel .ctrls{display:flex}
.wf .stepcard .ctrls button{width:24px;height:24px;border-radius:6px;background:var(--surface-2);color:var(--ink-2);display:grid;place-items:center} .wf .stepcard .ctrls button:hover{background:var(--accent-soft);color:var(--accent)}
.wf .stepcard .ctrls button.on{background:var(--accent-soft);color:var(--accent)}
.wf .stepcard .ctrls button:last-child:hover{background:var(--crit-soft);color:var(--crit)}
.wf .insp{border-left:1px solid var(--border);background:var(--surface);overflow:auto;display:flex;flex-direction:column;min-height:0}
.wf .insp .ih{padding:13px 16px;border-bottom:1px solid var(--border);display:flex;align-items:center;gap:9px} .wf .insp .ih .tic{width:28px;height:28px;border-radius:8px;display:grid;place-items:center;flex:none} .wf .insp .ih b{font-size:13px}
.wf .insp .ih .delx{margin-left:auto;padding:5px 12px;border:1px solid color-mix(in srgb,var(--crit) 45%,var(--border));border-radius:8px;color:var(--crit);font-weight:600;font-size:12px;background:var(--surface)} .wf .insp .ih .delx:hover{background:var(--crit-soft)}
.wf .titlefld{padding:12px 16px 0} .wf .titlefld .inp{font-weight:660;font-size:14px}
.wf .itabs{display:flex;gap:2px;padding:11px 12px 0;border-bottom:1px solid var(--border)}
.wf .itab{position:relative;padding:8px 11px 10px;font-weight:600;font-size:12.5px;color:var(--ink-3);border-bottom:2px solid transparent;margin-bottom:-1px} .wf .itab:hover{color:var(--ink-2)} .wf .itab.on{color:var(--accent);border-bottom-color:var(--accent)}
.wf .itab .tdot{display:inline-block;width:5px;height:5px;border-radius:50%;background:var(--accent);margin-left:5px;vertical-align:middle}
.wf .typedesc{padding:9px 11px;border:1px solid var(--border);border-radius:9px;background:var(--inset);color:var(--ink-2);font-size:12px;line-height:1.5}
.wf .inp.ta{resize:vertical;min-height:58px;line-height:1.5}
.wf .ibody{padding:15px 16px;display:flex;flex-direction:column;gap:14px}
.wf .emptyhint{font-size:12px;color:var(--ink-2);line-height:1.55;padding:2px 0}
.wf .fld{display:flex;flex-direction:column;gap:6px} .wf .fld .lab{font-weight:600;font-size:12px;color:var(--ink)}
.wf .inp{width:100%;padding:8px 11px;border:1px solid var(--border-strong);border-radius:9px;background:var(--surface);color:var(--ink);font:500 12.5px var(--sans)} .wf .inp:focus{outline:none;border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.wf .hint{font-size:11px;color:var(--ink-3);line-height:1.4}
.wf .condbox{display:flex;flex-direction:column;gap:6px;padding:9px;border:1px solid var(--border);border-radius:10px;background:var(--surface-2)}
.wf .condrow{display:flex;gap:6px;align-items:center} .wf .condrow .inp.cur{width:84px;flex:none}
.wf .sentence{padding:9px 11px;border-radius:9px;background:var(--accent-soft);color:var(--accent);font-size:12px;line-height:1.5}
.wf .sib{display:flex;flex-direction:column;gap:2px;padding:8px 10px;border:1px solid var(--border);border-radius:9px;font-size:12px} .wf .sib span{color:var(--ink-2)}
.wf .clash{padding:9px 11px;border-radius:9px;border:1px solid var(--crit);color:var(--crit);font-size:12px;line-height:1.5}
.wf .uflist{display:flex;flex-wrap:wrap;gap:6px}
.wf .ufchip{display:inline-flex;align-items:center;gap:4px;padding:4px 6px 4px 10px;border-radius:999px;background:var(--accent-soft);color:var(--accent);font-size:12px;font-weight:500}
.wf .ufchip b{font-weight:600} .wf .ufchip button{display:inline-flex;padding:2px;border-radius:999px} .wf .ufchip button:hover{background:var(--surface)} .wf .ufchip .ic{width:12px;height:12px}
.wf .outrow{display:flex;gap:6px;flex-wrap:wrap}
.wf .backedge{margin-top:9px;display:inline-flex;align-items:center;gap:5px;font:600 10px var(--sans);color:var(--crit);background:var(--crit-soft);padding:3px 9px;border-radius:8px} .wf .backedge .ic{width:12px;height:12px}
.wf .ogrp{margin-top:15px} .wf .ogrp:first-of-type{margin-top:0} .wf .ogrplbl{font:600 10px var(--sans);letter-spacing:.05em;text-transform:uppercase;color:var(--ink-3);margin-bottom:8px}
.wf .orows{display:flex;flex-direction:column;gap:6px}
.wf .orow{display:flex;align-items:center;text-align:left;width:100%;padding:9px 11px;border:1px solid var(--border);border-radius:9px;background:var(--surface);color:var(--ink-2);font:600 12.5px var(--sans);cursor:pointer} .wf .orow:hover{background:var(--surface-2)}
.wf .orow.on{border-color:var(--accent);background:var(--accent-soft);color:var(--accent)}
.wf .orow.back.on{border-color:var(--crit);background:var(--crit-soft);color:var(--crit)}
.wf .ihelp{font-size:11.5px;color:var(--ink-3);line-height:1.5;margin:0 0 13px}
.wf .assignprev{font-size:11.5px;color:var(--accent);background:var(--accent-soft);border-radius:9px;padding:9px 11px;margin-top:4px;display:flex;gap:8px;align-items:flex-start;line-height:1.5} .wf .assignprev .ic{width:14px;height:14px;flex:none;margin-top:1px} .wf .assignprev b{color:var(--accent)}
.wf .toggle{display:inline-flex;align-items:center;gap:10px;font-size:12.5px;font-weight:600;color:var(--ink);cursor:pointer;background:none;border:0;padding:0} .wf .toggle .sw{width:38px;height:22px;border-radius:99px;background:var(--border-strong);position:relative;transition:.15s;flex:none} .wf .toggle.on .sw{background:var(--accent)} .wf .toggle .sw::after{content:"";position:absolute;top:2px;left:2px;width:18px;height:18px;border-radius:50%;background:#fff;transition:.15s} .wf .toggle.on .sw::after{left:18px}
.wf .chk{display:flex;align-items:center;gap:8px;font-size:12.5px;color:var(--ink);cursor:pointer} .wf .chk input{width:16px;height:16px;accent-color:var(--accent)}
.wf .delbtn{display:inline-flex;align-items:center;justify-content:center;gap:7px;margin-top:4px;padding:8px 12px;border:1px solid var(--crit-soft);border-radius:9px;background:var(--crit-soft);color:var(--crit);font-weight:600;font-size:12.5px} .wf .delbtn:hover{filter:brightness(.97)}
@media (max-width:1040px){.wf .work{grid-template-columns:1fr}.wf .insp{border-left:0;border-top:1px solid var(--border);max-height:50vh}}
@media (max-width:820px){.wf .wfapp{grid-template-columns:56px 1fr}.wf .brand b,.wf .nav .sec,.wf .nav a span,.wf .me>div{display:none}}
`;
