"use client";

// Request Overview — the request dossier, laid out "decision first": a one-line header and a progress strip,
// then a main column (decision, what was asked, risk, workflow, activity, comments) and a side column
// (counterparty, people, documents, Ask Aegis). This file owns every query, mutation, effect and typed draft; the
// cards in the sibling `_request-*.tsx` / `_decision-area.tsx` files are presentational and get props.
// Scoped under `.ro` (reuses the `.li-board` palette from the shell's CSS).

import { useEffect, useRef, useState, type ReactNode } from "react";
import { useRouter } from "next/navigation";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { intakeApi, workflowsApi, brainApi, contractsApi, trademarksApi } from "@/lib/endpoints";
import { useToast } from "@/components/toast";
import { LoadError, MessageBar } from "@/components/ui";
import { REVIEW_NOTICE_CSS, StepReviewNotice } from "@/components/review-notice";
import { interruptedReviewStep, reviewPollInterval } from "@/lib/review-retry";
import { currentStage, LIFECYCLE, stepFacts, stepStage } from "@/components/lifecycle-view";
import { beforeWorkflow, isBeingSetUp, termFact } from "@/lib/request-facts";
import { openSteps, overdueWords, plainStepName, readableResult, stepInFocus } from "@/lib/workflow-steps";
import { stepMeta, waitLabel } from "./_governance-ladder";
import { NegotiationPanel } from "./_negotiation-panel";
import { RequestAnswers } from "./_request-answers";
import { ApprovalCard } from "./_approval-card";
import { SignatureCard } from "./_signature-card";
import { RequestHeader, ProgressStrip, HEADER_CSS } from "./_request-header";
import { DecisionArea, DECISION_CSS, type HumanDecision } from "./_decision-area";
import { WhatWasAsked, ASKED_CSS } from "./_request-asked";
import { RiskReview, riskFacts, RISK_CSS } from "./_request-risk";
import { ApprovalChain, WorkflowCard, STEPS_CSS, type ExtraDetail, type FlowState, type StepRow } from "./_request-steps";
import { ActivityCard, AskAegisCard, CommentsCard, CounterpartyCard, DocumentsCard, PeopleCard, CARDS_CSS } from "./_request-cards";
import type { IntakeRequest, WorkflowSuggestion, WorkflowRun, WorkflowRunStep } from "@/lib/types";
import { useAuth } from "@/lib/auth";
import { fmtDateTime, riskBand, RISK_UNKNOWN_LABEL } from "@/lib/utils";

const isWaiting = (s?: WorkflowRunStep) => !!s && s.status.startsWith("waiting");
// The "doorway" for a step: the real tool the owner opens to actually DO the
// step's work, with the contract in context — not just a "mark done" button.
const STEP_DOORWAY = {
  clm_draft: "Open the draft",
  human_task: "Review & redline",
  counterparty: "Open to negotiate",
} as const;
const assignOf = (s: WorkflowRunStep): string | undefined => {
  const who = s.assignee_label;
  const grp = s.team_label ?? undefined;
  if (who && grp) return `${who} · ${grp}`;
  return who ?? grp ?? undefined;
};

// Which doc type this request can auto-draft (mirrors the intake service's
// heuristic). null ⇒ not a draft-able contract request.
// The server decides what (if anything) a request drafts — see resolve_doc_type.
const draftableDocType = (r: IntakeRequest): string | null => r.draftable_doc_label ?? null;

// Mirrors draftableDocType's shape — a request whose subject reads as a
// trademark matter, eligible for "Continue as Trademark".
function isTrademarkRequest(r: IntakeRequest): boolean {
  const t = `${r.type_label} ${r.description ?? ""}`.toLowerCase();
  return /trademark|\btm\b|brand name|logo mark/.test(t);
}

function counterpartyOf(r: IntakeRequest): string | null {
  const parties = (r.parties ?? []) as { name?: string; role?: string }[];
  const cp = parties.find((p) => /counter|vendor|supplier|opposing|third|other/i.test(p.role ?? "")) ?? parties[0];
  if (cp?.name) return cp.name;
  const fv = (r.field_values ?? {}) as Record<string, unknown>;
  for (const k of ["counterparty", "company", "vendor", "party"]) if (typeof fv[k] === "string" && fv[k]) return fv[k] as string;
  return null;
}

export function RequestOverview({ id, onBack, canManage, onOpenRequest }: { id: string; onBack: () => void; canManage: boolean; onOpenRequest?: (id: string) => void }) {
  const qc = useQueryClient();
  const router = useRouter();
  const { notify } = useToast();
  const { user } = useAuth();
  // Poll only while the AI triage is still running (its result lands a few
  // seconds after filing); otherwise refetch on focus — you're almost always
  // the only one looking at a ticket, and your own actions invalidate it.
  const { data: r, isLoading, error: requestError, refetch: refetchRequest } = useQuery({ queryKey: ["intake-request", id], queryFn: () => intakeApi.get(id), refetchOnWindowFocus: true, refetchInterval: (q) => ((q.state.data?.ai_triage as { status?: string } | null)?.status === "pending" || isBeingSetUp(q.state.data, qc.getQueryData(["flow-run", id])) ? 3000 : false) });
  const { data: docs } = useQuery({ queryKey: ["intake-docs", id], queryFn: () => intakeApi.documents(id) });
  const { data: assignees } = useQuery({ queryKey: ["intake-assignees"], queryFn: intakeApi.assignees, enabled: canManage });
  const [reassignTo, setReassignTo] = useState("");

  const cid = r?.contract_id ?? null;
  // The risk band from the playbook review. Poll while it's unknown (drafting starts
  // a background review) — capped, since a contract can stay unreviewed for good.
  const { data: risk } = useQuery({
    queryKey: ["contract-risk", cid], queryFn: () => contractsApi.risk(cid!), enabled: !!cid,
    refetchInterval: (q) => (riskBand(q.state.data) === "unknown" && q.state.dataUpdateCount < 30 ? 4000 : false),
  });
  const { data: deviations } = useQuery({ queryKey: ["contract-devs", cid], queryFn: () => contractsApi.deviations(cid!), enabled: !!cid });
  const { data: approvals } = useQuery({ queryKey: ["intake-approval-chain", id], queryFn: () => intakeApi.approvalChain(id) });
  const { data: teams } = useQuery({ queryKey: ["intake-teams"], queryFn: intakeApi.teams });
  // While a review is running, ask again every few seconds, so the step below moves on when it ends.
  const { data: contractStatus } = useQuery({ queryKey: ["contract-review", cid], queryFn: () => contractsApi.reviewStatus(cid!), enabled: !!cid, refetchInterval: (q) => reviewPollInterval(q.state.data?.auto_review) });

  // The live workflow engine — poll while an agent step is mid-beat so the ladder
  // animates; idle (waiting on a human / complete) runs don't poll. Capped: if
  // the current step hasn't actually progressed server-side in 45s, stop
  // auto-polling instead of hammering every 1.5s forever — a stuck ai_task
  // otherwise polls indefinitely (see the AI Risk Review incident). The
  // "Check status" manual action below takes over once the cap kicks in.
  const { data: flowRun } = useQuery({
    queryKey: ["flow-run", id],
    queryFn: () => workflowsApi.runForRequest(id),
    refetchInterval: (q) => {
      const run = q.state.data as WorkflowRun | null | undefined;
      // A request just filed is set up in the background; its workflow starts a few seconds later.
      if (!run) return isBeingSetUp(qc.getQueryData<IntakeRequest>(["intake-request", id]), run) ? 3000 : false;
      const working = (run.steps ?? []).some((s) => s.status === "running" || s.status === "waiting_job");
      if (run.status !== "running" && !working) return false;
      // Steps that run side by side: the latest progress of any of them counts.
      const lastProgress = Math.max(0, ...openSteps(run).map((s) => (s.updated_at ? new Date(s.updated_at).getTime() : 0)));
      return Date.now() - lastProgress < 45_000 ? 1500 : false;
    },
  });
  // Request events, signers, obligations, renewal — refetched as the run moves.
  const { data: lifeRows } = useQuery({
    queryKey: ["lifecycle-rows", id, cid, flowRun?.id, flowRun?.status, flowRun?.current_index],
    queryFn: () => workflowsApi.lifecycle({ request_id: id }),
  });
  // Activity and counterparty files each have their own load state, so one failing never blanks the page.
  // The activity key follows the run so a step finishing shows up; keepPreviousData keeps the card from blanking meanwhile.
  const activityQ = useQuery({
    queryKey: ["intake-activity", id, flowRun?.status ?? null, flowRun?.current_index ?? null, cid],
    queryFn: () => intakeApi.activity(id), placeholderData: keepPreviousData,
  });
  const counterpartyQ = useQuery({ queryKey: ["intake-counterparty", id], queryFn: () => intakeApi.counterparty(id) });
  // Pump a mid-beat "running" step once: hold ~1s so the beat shows, then resume
  // the executor so the agent runs for real and the flow advances (back-and-forth).
  const pumpedRef = useRef<string | null>(null);
  // A review that was running and no longer is has just ended: run the workflow on from it, and fetch what it wrote.
  const reviewPending = !!contractStatus?.auto_review;
  const reviewWasPending = useRef(false);
  const flowRunId = flowRun?.id;
  useEffect(() => {
    if (reviewWasPending.current && !reviewPending && flowRunId) {
      workflowsApi.refreshRun(flowRunId)
        .then((run) => qc.setQueryData(["flow-run", id], run))
        .catch(() => qc.invalidateQueries({ queryKey: ["flow-run", id] }));
      for (const k of [["contract-risk", cid], ["contract-devs", cid], ["contract-review", cid]]) qc.invalidateQueries({ queryKey: k });
    }
    reviewWasPending.current = reviewPending;
  }, [reviewPending, flowRunId, id, cid, qc]);
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
  const commentRef = useRef<HTMLInputElement>(null);
  const sendBackRef = useRef<HTMLSelectElement>(null);
  // Owned here so the decision area's "Send back for rework…" can open the workflow card's form and focus it.
  const [sendBackOpen, setSendBackOpen] = useState(false);
  const focusSendBack = useRef(false);
  useEffect(() => {
    if (!sendBackOpen || !focusSendBack.current) return;
    focusSendBack.current = false;
    sendBackRef.current?.scrollIntoView({ block: "center" });
    sendBackRef.current?.focus();
  }, [sendBackOpen]);

  // Every write refreshes the request in place + the boards that list it.
  const done = (msg: string) => (updated: IntakeRequest) => {
    qc.setQueryData(["intake-request", id], updated);
    ["intake-list", "intake-my-work", "intake-mine"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
    qc.invalidateQueries({ queryKey: ["intake-activity", id] });
    notify(msg, "success");
  };
  const fail = (e: unknown) => notify(e instanceof Error ? e.message : "Action failed", "error");

  const reassign = useMutation({ mutationFn: (uid: string) => intakeApi.triage(id, { action: "reassigned", assignee_user_id: uid }), onSuccess: (u) => { setReassignTo(""); done("Owner reassigned")(u); }, onError: fail });
  // The AI's urgency is only a suggestion; a person applies it (PATCH audits the change).
  const applyPriority = useMutation({ mutationFn: (p: string) => intakeApi.update(id, { priority: p }), onSuccess: done("Priority updated"), onError: fail });
  // Drafting is never held back for a template that doesn't pass its playbook; the server says so in
  // `template_notice` and we show it above the page. Keyed by request so it can't follow a person to another one.
  const [templateNotice, setTemplateNotice] = useState<{ id: string; text: string } | null>(null);
  const draft = useMutation({
    mutationFn: () => intakeApi.draftContract(id),
    onSuccess: (u) => { done("Drafting — review running")(u); setTemplateNotice(u.template_notice ? { id, text: u.template_notice } : null); },
    onError: fail,
  });
  const ingest = useMutation({ mutationFn: () => intakeApi.ingestAttachment(id), onSuccess: done("Using the attachment — review running"), onError: fail });
  const continueAsTrademark = useMutation({
    mutationFn: () => trademarksApi.continueFromIntake(id, {}),
    onSuccess: (tm) => { notify("Continued as a trademark", "success"); router.push(`/trademarks/${tm.id}`); },
    onError: fail,
  });
  // Engine actions — start the workflow, complete a human step, or poll an approval/signature.
  const { data: allFlows } = useQuery({ queryKey: ["flows"], queryFn: workflowsApi.listFlows, enabled: canManage });
  // Start the flow shown as "Suggested" by passing its id: the backend's own
  // matcher can't read an untyped workflow's free-text AI condition, so without
  // the id it could land on a different flow than the one this page shows.
  const startFlow = useMutation({ mutationFn: (flowId?: string) => workflowsApi.startFlow(id, flowId), onSuccess: (run) => { qc.setQueryData(["flow-run", id], run); notify("Workflow started", "success"); }, onError: fail });
  // After a rejected approval the server wants a written reply before the step can go on.
  const [reply, setReply] = useState("");
  const owed = flowRun?.rework ?? null;
  const replyMissing = !!owed && !reply.trim();
  const completeStep = useMutation({
    // Always say which step: of two steps running side by side, the server must not guess.
    mutationFn: () => (owed
      ? workflowsApi.completeStep(flowRun?.id ?? "", undefined, current?.idx, undefined, reply.trim())
      : workflowsApi.completeStep(flowRun?.id ?? "", undefined, current?.idx)),
    onSuccess: (run) => { qc.setQueryData(["flow-run", id], run); setReply(""); notify("Step completed", "success"); },
    onError: fail,
  });
  // The review step stopped because its AI review was interrupted: run the review again and set the step going.
  const retryReview = useMutation({
    mutationFn: () => workflowsApi.retryReview(flowRun?.id ?? ""),
    onSuccess: (run) => {
      qc.setQueryData(["flow-run", id], run);
      for (const k of [["contract-review", cid], ["contract-risk", cid], ["contract-devs", cid]]) qc.invalidateQueries({ queryKey: k });
      notify("Review restarted. The workflow carries on when it finishes.", "success");
    },
    onError: fail,
  });
  const checkStep = useMutation({ mutationFn: () => workflowsApi.refreshRun(flowRun?.id ?? ""), onSuccess: (run) => { qc.setQueryData(["flow-run", id], run); notify("Refreshed", "success"); }, onError: fail });
  // Dynamic edges: send a step back for rework, and post questions/comments.
  const [backTo, setBackTo] = useState("");
  const [backNote, setBackNote] = useState("");
  const [comment, setComment] = useState("");
  const returnStep = useMutation({ mutationFn: (p: { to_idx?: number; note?: string }) => workflowsApi.returnStep(flowRun?.id ?? "", p.to_idx, p.note), onSuccess: (run) => { qc.setQueryData(["flow-run", id], run); qc.invalidateQueries({ queryKey: ["intake-activity", id] }); setBackTo(""); setBackNote(""); notify("Sent back for rework", "success"); }, onError: fail });
  const postComment = useMutation({ mutationFn: (text: string) => workflowsApi.comment(flowRun?.id ?? "", text), onSuccess: (run) => { qc.setQueryData(["flow-run", id], run); qc.invalidateQueries({ queryKey: ["intake-activity", id] }); setComment(""); }, onError: fail });
  const rerunRisk = useMutation({ mutationFn: () => contractsApi.computeRisk(cid ?? ""), onSuccess: (rs) => { qc.setQueryData(["contract-risk", cid], rs); qc.invalidateQueries({ queryKey: ["contract-devs", cid] }); notify("Risk recomputed from the playbook review", "success"); }, onError: fail });
  const ask = useMutation({
    mutationFn: (question: string) => brainApi.ask({ question, query_scope: r?.contract_id ? "contract" : "portfolio", contract_id: r?.contract_id ?? undefined }),
    onSuccess: (res) => setChat((c) => [...c, { role: "ai", text: res.answer, cites: res.citations?.length ?? 0 }]),
    onError: (e) => setChat((c) => [...c, { role: "ai", text: e instanceof Error ? e.message : "Couldn't answer that." }]),
  });
  const busy = reassign.isPending || draft.isPending || ingest.isPending || continueAsTrademark.isPending || startFlow.isPending || completeStep.isPending || checkStep.isPending || returnStep.isPending || retryReview.isPending;
  function sendChat() {
    const text = q.trim();
    if (!text || ask.isPending) return;
    setChat((c) => [...c, { role: "you", text }]);
    setQ("");
    ask.mutate(text);
  }

  const steps = r?.workflow ?? [];
  // A request that could not be opened (removed, wrong link, no access) says so; it used to sit on "Loading request…" for ever.
  if (!r && requestError) return <div className="ro"><div style={{ padding: 24 }}><LoadError error={requestError} what="this request" onRetry={() => void refetchRequest()} onBack={onBack} backLabel="← Back to Legal Intake" /></div></div>;
  if (isLoading || !r) return <div className="ro"><div style={{ padding: 40, color: "var(--ink-3)" }}>Loading request…</div></div>;


  const cp = counterpartyOf(r);
  const ai = (r.ai_triage ?? {}) as { confidence?: number; complexity?: string; source?: string };
  // A form request is decided by its form; the model only leaves this note for the owner.
  const read = (r.ai_triage as { read?: { summary?: string; mismatches?: string[]; bespoke_asks?: string[]; negotiation_points?: string[] } | null } | null)?.read;
  const fs = (r.ai_triage as { flow_suggestion?: WorkflowSuggestion } | null)?.flow_suggestion;
  // The model's urgency, offered as a suggestion. Only when the model produced it (form
  // triage copies the requester's priority, which would come back as a fake "AI" hint after
  // an edit), and never against Critical: the model can't say Critical, so "apply" would
  // only lower an escalation.
  const triage = r.ai_triage as { source?: string; understanding?: { urgency?: string } | null } | null;
  const aiUrgency = triage?.source === "llm" && r.priority !== "Critical" ? triage.understanding?.urgency : undefined;
  const missingInfo = ((r.ai_triage as { understanding?: { missing_info?: string[] } } | null)?.understanding?.missing_info) ?? [];
  const triageDegraded = (r.ai_triage as { degraded?: boolean; degraded_reason?: string } | null) ?? {};
  const stuck = r.sla_status === "overdue";
  const open = r.status !== "closed" && r.status !== "approved";
  const docType = draftableDocType(r);
  const hasAttachment = (docs ?? []).some((d) => d.extracted_chars > 0);
  const canAct = canManage && open;

  // --- the live workflow, unified over the engine + the static fallback -------
  const hasRun = !!flowRun && ["running", "waiting", "complete", "failed", "cancelled"].includes(flowRun.status);
  const settingUp = !hasRun && !fs?.flow_name && isBeingSetUp(r, flowRun);
  const runComplete = flowRun?.status === "complete";
  const runFailed = flowRun?.status === "failed" || flowRun?.status === "cancelled";
  const curIdx = flowRun?.current_index ?? 0;
  const interruptedStep = hasRun ? interruptedReviewStep(flowRun) : null;
  // The step to show and act on. Steps flagged "parallel" run side by side and `current_index` stays on the
  // first of them, so once that one is done the step still waiting is another one.
  const current: WorkflowRunStep | undefined = hasRun ? stepInFocus(flowRun, user?.id) : undefined;
  const openIdx = new Set(hasRun ? openSteps(flowRun).map((s) => s.idx) : []);
  // The shared stage bar and the Next-step card read step names as saved ("Clm Draft"); hand them plain ones.
  const plainRun: WorkflowRun | null = flowRun ? { ...flowRun, steps: (flowRun.steps ?? []).map((s) => ({ ...s, name: plainStepName(s.name, s.type) })) } : null;
  // Which approval this is: the open approval step's plain name, and what it is about (type · counterparty).
  const openApproval = hasRun ? openSteps(flowRun).find((s) => s.type === "approval") : undefined;
  const approvalStepName = openApproval ? plainStepName(openApproval.name, openApproval.type) : undefined;
  const agreementType = (r.field_values as Record<string, unknown> | null)?.agreement_type;
  const approvalSubject = [(typeof agreementType === "string" && agreementType) || r.type_label, cp].filter(Boolean).join(" · ") || undefined;
  const runSteps = hasRun ? flowRun!.steps ?? [] : [];
  // Who an open tile waits on, the authority note or team members, and when it is due.
  const waitOf = (s: WorkflowRunStep): NonNullable<StepRow["wait"]> => {
    const f = stepFacts(s, teams);
    return {
      who: f.who, extra: f.authorityNote ?? (f.members.length ? f.members.join(", ") : null),
      due: f.overdue ? overdueWords(f.due ?? new Date()) : f.due ? `Due ${fmtDateTime(f.due.toISOString())}` : null,
      overdue: f.overdue,
    };
  };
  const baseRows: StepRow[] = hasRun
    ? runSteps.map((s, i) => {
        // A step the engine skipped (its condition was not met) was never done: it shows as skipped and is
        // not counted as work that happened, but it does not hold the workflow up either.
        const isSkipped = s.status === "skipped";
        const isDone = isSkipped || ["done", "complete"].includes(s.status);
        const isCur = (openIdx.has(s.idx) || i === curIdx) && !isDone && !runComplete;
        const failedHere = runFailed && i === curIdx;
        const meta = stepMeta(s.type);
        const doc = flowRun?.contract_id ?? cid;
        const doorLabel = STEP_DOORWAY[s.type as keyof typeof STEP_DOORWAY];
        return {
          label: plainStepName(s.name, s.type),
          tone: isDone ? "done" : failedHere ? "warn" : isCur ? "cur" : "todo",
          node: isSkipped ? "–" : isDone ? "✓" : failedHere ? "!" : String(i + 1),
          badge: isSkipped ? "skipped" : isDone ? "done" : isCur ? (failedHere ? (flowRun?.status === "cancelled" ? "cancelled" : "failed") : s.status === "running" ? meta.running : isWaiting(s) ? waitLabel(s, user?.id) : "here now") : undefined,
          badgeTone: isSkipped ? "skip" : isDone ? "done" : failedHere ? "warn" : "cur",
          skipped: isSkipped,
          sidx: i,
          stage: stepStage(s),
          assign: assignOf(s),
          wait: isCur && !failedHere ? waitOf(s) : undefined,
          focus: s.idx === current?.idx,
          detail: readableResult(s.result),
          doorway: canAct && i === current?.idx && current?.status === "waiting_human" && doc && doorLabel ? { href: `/contracts/${doc}`, label: doorLabel } : undefined,
          notice: interruptedStep?.idx === i ? <StepReviewNotice auto={contractStatus?.auto_review} canAct={canAct} busy={busy} onRetry={() => retryReview.mutate()} /> : undefined,
        } satisfies StepRow;
      })
    // No run started yet — preview the auto-chosen workflow's own steps
    // (all "todo") rather than the generic per-request-type stage ladder,
    // so the ticket shows what will actually run once Start is clicked.
    : fs?.steps?.length
    ? fs.steps.map((label, i): StepRow => ({ label: plainStepName(label), tone: "todo", node: String(i + 1), badgeTone: "cur", detail: [] }))
    : steps.map((s, i): StepRow => ({
        label: s.label,
        tone: s.done ? "done" : s.active ? (stuck ? "warn" : "cur") : "todo",
        node: s.done ? "✓" : s.active && stuck ? "!" : String(i + 1),
        badge: s.done ? "done" : s.active ? (stuck ? "overdue" : "here now") : undefined,
        badgeTone: s.done ? "done" : stuck ? "warn" : "cur",
        detail: [],
      }));
  // "Next" sits on the first upcoming tile after the last open one.
  const lastOpen = baseRows.reduce((m, w, i) => (w.tone === "cur" || w.tone === "warn" ? i : m), -1);
  const nextIdx = hasRun && lastOpen >= 0 ? baseRows.findIndex((w, i) => i > lastOpen && w.tone === "todo") : -1;
  const wf: StepRow[] = baseRows.map((w, i) => (i === nextIdx ? { ...w, next: true } : w));
  const wfSkipped = wf.filter((w) => w.skipped).length;
  const wfDone = wf.filter((w) => w.tone === "done" && !w.skipped).length;
  const wfActiveIdx = wf.findIndex((w) => w.tone === "cur" || w.tone === "warn");
  const stageNow = currentStage({ run: hasRun ? flowRun : null, contractStage: contractStatus?.lifecycle_stage, requestClosed: !open });
  const stageLabel = LIFECYCLE.find((x) => x.key === stageNow)?.label ?? r.stage;

  // --- Risk review: one set of numbers for the header pill, the decision warning and the review card ---
  const rf = riskFacts(risk, deviations, !!cid);

  // Approve the current human step, warning when critical risks are open or nobody reviewed the text.
  function approveStep() {
    if (replyMissing) return;
    const warning = rf.unknown ? "The risk on this contract's text has not been reviewed."
      : rf.critCount > 0 ? `${rf.critCount} critical-severity risk${rf.critCount === 1 ? "" : "s"} still open on this contract.` : null;
    if (warning && !window.confirm(`${warning}\n\nApprove and advance anyway?`)) return;
    completeStep.mutate();
  }
  // A draft that can't be made yet: a Statement of Work without its deliverable rows, a Consultancy or DPA without
  // the answers its template needs, or a DPA that came by email with no form to take them from (`result.held` says
  // which). The step carries on by itself once the answers are saved, so there is nothing for a person to approve
  // (the server refuses a manual complete with 409) and the reason is shown with the answers instead.
  const held = current?.type === "clm_draft" && current.status === "waiting_human"
    && !!(current.result as { held?: string } | null)?.held;
  const atReviewGate = hasRun && !runComplete && canAct && current?.status === "waiting_human" && !held;

  const fv = (r.field_values ?? {}) as Record<string, unknown>;
  // Form numbers arrive typed (validated server-side) with the currency picked beside them.
  const rawValue = ["value", "new_value", "renew_value", "contract_value", "amount"].map((k) => fv[k]).find((v) => (typeof v === "string" && v) || typeof v === "number");
  const cur = typeof fv.currency === "string" ? fv.currency : fv.request_form ? "INR" : "";
  const value = typeof rawValue === "number" || (typeof rawValue === "string" && /^[\d.,]+$/.test(rawValue))
    ? `${cur ? `${cur} ` : ""}${Number(String(rawValue).replace(/,/g, "")).toLocaleString(cur === "INR" ? "en-IN" : "en-US")}`
    : (rawValue as string | undefined);

  const termText = termFact(fv);
  const facts: { label: string; value: string }[] = [
    { label: "Counterparty", value: cp ?? "—" },
    { label: "Type", value: (typeof fv.agreement_type === "string" && fv.agreement_type) || r.type_label },
    ...(value ? [{ label: "Value", value }] : []),
    ...(termText ? [{ label: "Term", value: termText }] : []),
  ];

  // --- the status line under the stage bar ---
  const waitingNow = !!current && isWaiting(current);
  const overdueNow = !!current?.approval?.overdue;
  let line: string;
  if (settingUp) line = "Workflow being set up";
  else if (!hasRun) line = `${fs?.flow_name ? "Not started" : "No workflow yet"} · ${beforeWorkflow(r).note}`;
  else if (runComplete) line = "Workflow complete";
  else if (runFailed) line = `Workflow ${flowRun!.status}`;
  else {
    const names = openSteps(flowRun).map((s) => plainStepName(s.name, s.type)).join(" + ") || (current ? plainStepName(current.name, current.type) : "In progress");
    line = `${stageLabel} · ${names}${wfActiveIdx >= 0 ? ` · step ${wfActiveIdx + 1} of ${wf.length}` : ""}`
      + (current && waitingNow ? ` · ${waitLabel(current, user?.id)}` : current?.status === "running" ? ` · ${stepMeta(current.type).running}` : "")
      + (overdueNow ? " · overdue" : "");
  }
  const working = hasRun && !runComplete && !!current && (current.status === "running" || (current.status === "waiting_job" && current.type !== "approval" && current.type !== "signature"));
  // Approval/signature steps sit in waiting_job while they wait on PEOPLE, so they are not an agent at work.

  // --- decision area ---
  // The same rule ApprovalCard uses to pick its rung (_approval-card.tsx): if they drift, the area would hold a
  // card that renders nothing, or hide an approval that is pending.
  const pendingApproval = (approvals ?? []).some((a) => a.can_decide || (a.status === "pending" && !!a.approval_request_id));
  const negotiateCid = hasRun && !runComplete && current?.type === "counterparty" ? (flowRun?.contract_id ?? cid) : null;
  const negotiation = negotiateCid ? <NegotiationPanel contractId={negotiateCid} canAct={canAct} busy={busy} onConverged={() => completeStep.mutate()} /> : null;
  // Waiting at the signature step: whoever can act sends the approved version out from here.
  const signCid = hasRun && !runComplete && current?.type === "signature" ? (flowRun?.contract_id ?? cid) : null;
  const signature = signCid && canAct ? <SignatureCard contractId={signCid} rows={lifeRows?.signature ?? []} stepName={plainStepName(current!.name, current!.type)} /> : null;
  const human: HumanDecision | null = atReviewGate && current ? {
    stepName: plainStepName(current.name, current.type),
    warning: rf.unknown ? { tone: "warn", text: `${RISK_UNKNOWN_LABEL}${risk?.note ? ` — ${risk.note}` : ""}` }
      : rf.gateCount > 0 ? { tone: "warn", text: `${rf.critCount > 0 ? `${rf.critCount} critical + ` : ""}${rf.gateCount - rf.critCount} high risk${rf.gateCount === 1 ? "" : "s"} open — review before approving` }
      : rf.band === "high" ? { tone: "warn", text: "Overall risk is high — accepted issues still count" }
      : { tone: "ok", text: "No blocking risks — clear to advance" },
    rework: owed ? { reason: owed.reason } : null,
    reply, onReply: setReply, replyMissing, busy, onApprove: approveStep,
    canSendBack: curIdx > 0,
    onJumpToSendBack: () => {
      if (sendBackOpen) { sendBackRef.current?.scrollIntoView({ block: "center" }); sendBackRef.current?.focus(); return; }
      focusSendBack.current = true; setSendBackOpen(true);
    },
    onJumpToComment: () => { commentRef.current?.focus(); commentRef.current?.scrollIntoView({ block: "center" }); },
  } : null;

  // --- steps card extras: the approval chain sits on the approval step; negotiation rounds on the negotiate step for those who cannot act ---
  const extras: ExtraDetail[] = [];
  if ((approvals ?? []).length > 0) {
    extras.push({ id: "approval-chain", label: "Approval chain", node: <ApprovalChain rungs={approvals ?? []} /> });
  }
  if (negotiation && !canAct && current) extras.push({ id: "negotiation", label: "Negotiation rounds", node: negotiation });

  const flow: FlowState = hasRun ? { kind: "running", name: flowRun!.flow_name, status: flowRun!.status }
    : fs?.flow_name ? { kind: "suggested", name: fs.flow_name, reasoning: fs.reasoning ?? "", canStart: canAct, starting: busy, onStart: () => startFlow.mutate(fs.flow_id ?? undefined) }
    : settingUp ? { kind: "setting_up" }
    : { kind: "none", reasoning: fs?.reasoning ?? null, canPick: canAct, flows: (allFlows ?? []).filter((f) => f.enabled).map((f) => ({ id: f.id, name: f.name })), picking: startFlow.isPending, onPick: (flowId) => startFlow.mutate(flowId) };

  // The one status button: poll an approval/signature, or nudge an AI step once the auto-poll cap above gave up.
  // A step waiting on a person is finished from "Your decision", never from a second button here.
  const checkLabel = canAct && hasRun && !runComplete && current
    ? (current.type === "approval" || current.type === "signature" ? `Check ${current.type} status`
      : current.type === "ai_task" && current.status === "running" ? "Check AI review status" : null)
    : null;

  const activityStatus = activityQ.data ? "ready" : activityQ.isError ? "error" : "loading";
  const counterpartyStatus = counterpartyQ.data ? "ready" : counterpartyQ.isError ? "error" : "loading";
  const openRequest = (rid: string) => { onOpenRequest?.(rid); window.scrollTo({ top: 0 }); };
  const askAegis = (question: string) => { setChat((c) => [...c, { role: "you", text: question }]); ask.mutate(question); };

  return (
    <div className="ro">
      <style dangerouslySetInnerHTML={{ __html: RO_CSS + HEADER_CSS + DECISION_CSS + ASKED_CSS + RISK_CSS + STEPS_CSS + CARDS_CSS + REVIEW_NOTICE_CSS }} />

      {templateNotice?.id === id ? <MessageBar intent="warning" className="mb-3">{templateNotice.text}</MessageBar> : null}

      <RequestHeader title={r.subject || `${r.type_label}${cp ? ` — ${cp}` : ""}`} refCode={r.ref} requesterName={r.requester_name ?? null}
        raisedAt={r.created_at ?? r.submitted_at ?? null} ownerLabel={r.assigned_to_label ?? null}
        highRisk={!!cid && !!risk && rf.band === "high"} sla={r.sla_status ?? null} onBack={onBack} />
      <ProgressStrip steps={plainRun?.steps ?? []} current={stageNow} comments={flowRun?.comments ?? []} extras={lifeRows ?? {}} line={line} tone={overdueNow ? "warn" : "info"} working={working} />

      <div className="body">
        <div className="col">
          <DecisionArea
            approval={pendingApproval ? <ApprovalCard requestId={id} rungs={approvals ?? []} runId={flowRun?.id ?? null} stepName={approvalStepName} subject={approvalSubject} /> : null}
            human={human}
            negotiation={canAct ? negotiation : null}
            signature={signature} />
          <WhatWasAsked sourceTag={ai.source === "form" ? "from the form" : "as the AI understood it"} facts={facts}
            priority={{ value: r.priority, aiSuggests: aiUrgency ?? null, canApply: canAct, applying: applyPriority.isPending, onApply: () => { if (aiUrgency && !applyPriority.isPending) applyPriority.mutate(aiUrgency); } }}
            description={r.description?.trim() || null}
            // The answers a Statement of Work, Consultancy or DPA draft is made from; nothing for any other request. They
            // are saved on the request, the held draft step (if any) is asked to look again, then the page fetches both afresh.
            answers={(
              <RequestAnswers request={r} canEdit={canAct && !r.contract_id}
                heldNote={held ? current?.note : null} heldRunId={held ? flowRun?.id : null}
                onSaved={(u) => { done(fv.request_form === "sow" ? "Deliverables saved" : "Answers saved")(u); qc.invalidateQueries({ queryKey: ["intake-request", id] }); }} />
            )}
            missingInfo={missingInfo}
            degraded={triageDegraded.degraded ? { reason: triageDegraded.degraded_reason ?? null } : null}
            read={read ?? null}
            confidence={ai.confidence != null && ai.source !== "form" ? { value: Number(ai.confidence), complexity: ai.complexity ?? null } : null} />
          {cid ? <RiskReview risk={risk} facts={rf} canRecompute={canManage} recomputing={rerunRisk.isPending} onRecompute={() => rerunRisk.mutate()} /> : null}
          <WorkflowCard rows={wf}
            countText={wf.length ? `${wfDone} of ${wf.length - wfSkipped} steps done${wfSkipped ? `, ${wfSkipped} skipped` : ""}` : null}
            flow={flow} error={flowRun?.error ?? null}
            check={checkLabel ? { label: checkLabel, onClick: () => checkStep.mutate(), busy } : null}
            extras={extras} trace={flowRun?.brief ?? []}
            sendBack={hasRun && !runComplete && canAct && curIdx > 0 ? {
              options: wf.slice(0, curIdx).map((w) => ({ value: String(w.sidx), label: `${(w.sidx ?? 0) + 1}. ${w.label}` })),
              to: backTo, note: backNote, onTo: setBackTo, onNote: setBackNote, busy, selectRef: sendBackRef,
              onSend: () => returnStep.mutate({ to_idx: Number(backTo), note: backNote || undefined }),
              open: sendBackOpen, onToggle: () => setSendBackOpen((o) => !o),
            } : null} />
          <ActivityCard status={activityStatus} data={activityQ.data} onRetry={() => void activityQ.refetch()} />
          <CommentsCard
            comments={(flowRun?.comments ?? []).map((c) => ({ actor: c.actor_name, at: c.at, text: c.text, kind: c.kind, stepLabel: c.idx != null && wf[c.idx] ? wf[c.idx].label : null }))}
            composer={hasRun && canAct ? { value: comment, onChange: setComment, onPost: () => postComment.mutate(comment.trim()), posting: postComment.isPending, inputRef: commentRef } : null} />
        </div>

        <div className="col">
          <CounterpartyCard status={counterpartyStatus} data={counterpartyQ.data} parties={(r.parties ?? []) as { name: string }[]} onRetry={() => void counterpartyQ.refetch()} onOpenRequest={openRequest} />
          <PeopleCard owner={r.assigned_to_label ?? null} requester={r.requester_name ?? null} department={r.department ?? null}
            reassign={canAct ? { options: (assignees ?? []).filter((a) => a.id !== r.assigned_to_user_id).map((a) => ({ id: a.id, name: a.name })), value: reassignTo, onChange: setReassignTo, onReassign: () => reassign.mutate(reassignTo), busy } : null} />
          <DocumentsCard docs={docs ?? []} contractHref={r.contract_id ? `/contracts/${r.contract_id}` : null}
            contractActions={canAct && !r.contract_id && docType ? { docType, hasAttachment, busy, onIngest: () => ingest.mutate(), onDraft: () => draft.mutate() } : null}
            trademark={canAct && isTrademarkRequest(r) ? { busy, onContinue: () => continueAsTrademark.mutate() } : null} />
          <AskAegisCard scope={r.contract_id ? "this contract" : "portfolio"} chat={chat} pending={ask.isPending} q={q} onQ={setQ} onSend={sendChat} onAsk={askAegis} />
        </div>
      </div>
    </div>
  );
}

// Base rules for the page and the shared bits the cards use (.card .ch .cb .btn .sel .cin .composer .facts …).
// Each card's own rules live in its component file and are appended after these.
const RO_CSS = `
.ro{min-width:0}
.ro .lbl{font:600 10px var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3)}
.ro .ic{width:15px;height:15px;flex:none;stroke:currentColor;stroke-width:1.7;fill:none;stroke-linecap:round;stroke-linejoin:round}
.ro .btn{gap:7px;padding:8px 13px}
.ro .btn.wide{width:100%}
.ro .body{display:grid;grid-template-columns:minmax(0,1fr) 300px;gap:16px;align-items:start;padding:16px 0 20px}
.ro .col{min-width:0;display:flex;flex-direction:column;gap:14px}
.ro .card{border:1px solid var(--border);border-radius:13px;background:var(--surface);box-shadow:var(--shadow)}
.ro .card>.ch{display:flex;align-items:center;gap:9px;padding:12px 16px;border-bottom:1px solid var(--border)}
.ro .card>.ch h2{font-size:13px;font-weight:660;margin:0} .ro .card>.ch .cnt{font:700 10px var(--mono);background:var(--surface-2);color:var(--ink-2);border-radius:99px;padding:1px 7px}
.ro .card>.cb{padding:15px 16px}
.ro .glyph{width:26px;height:26px;border-radius:7px;display:grid;place-items:center;flex:none;background:var(--surface-2);color:var(--ink-2)}
.ro .glyph.warn{background:var(--warn-soft);color:var(--warn)} .ro .glyph.flow{background:var(--accent-soft);color:var(--accent)}
.ro .facts{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px 16px;margin-top:0}
.ro .facts .f .k{font:600 10px var(--sans);text-transform:uppercase;letter-spacing:.05em;color:var(--ink-3)} .ro .facts .f .v{font-weight:600;margin-top:2px;overflow-wrap:anywhere;min-width:0}
.ro .pill{display:inline-flex;padding:1px 9px;border-radius:99px;font:600 11px var(--sans);white-space:nowrap}
.ro .pill.bad{background:var(--crit-soft);color:var(--crit)} .ro .pill.warn{background:var(--warn-soft);color:var(--warn)}
.ro .cin{padding:8px 11px;border:1px solid var(--border-strong);border-radius:9px;background:var(--surface);color:var(--ink);font:500 12.5px var(--sans)}
.ro .sel{width:100%;padding:8px 11px;border:1px solid var(--border-strong);border-radius:9px;background:var(--surface);color:var(--ink);font:600 12.5px var(--sans)}
.ro .composer{display:flex;gap:8px} .ro .composer .cin{flex:1;min-width:0;padding:8px 11px;border:1px solid var(--border-strong);border-radius:9px;background:var(--surface);color:var(--ink);font:500 12.5px var(--sans)}
.ro .spin{gap:7px;opacity:.75;pointer-events:none} .ro .spin .ic{animation:ro-spin 1s linear infinite}
@keyframes ro-spin{to{transform:rotate(360deg)}}
.ro .dim{color:var(--ink-3)}
.ro .analyzing{display:flex;align-items:center;gap:9px;font-size:12.5px;color:var(--ink-2)} .ro .analyzing .ic{width:15px;height:15px;color:var(--accent);animation:ro-spin 1s linear infinite}
@media (max-width:1080px){.ro .body{grid-template-columns:minmax(0,1fr)}}
`;
