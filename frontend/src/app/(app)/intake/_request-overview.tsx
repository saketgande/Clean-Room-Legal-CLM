"use client";

// Request Overview — the request dossier, laid out "decision first": a one-line header and a progress strip,
// then a main column (decision, what was asked, risk, workflow, activity, comments) and a side column
// (counterparty, people, documents, Ask Aegis). This file owns every query, mutation, effect and typed draft; the
// cards in the sibling `_request-*.tsx` / `_decision-area.tsx` files are presentational and get props.
// Scoped under `.ro` (reuses the `.li-board` palette from the shell's CSS).

import { useEffect, useRef, useState, type ReactNode } from "react";
import Link from "next/link";
import { MessageSquarePlus, Sparkles, UserCog, FileText as FileIcon, Building2, LayoutDashboard, ListChecks, FileText, ShieldAlert, Activity, MessagesSquare, Link2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { intakeApi, workflowsApi, contractsApi, trademarksApi, obligationsApi } from "@/lib/endpoints";
import { useToast } from "@/components/toast";
import { LoadError, MessageBar, useConfirm } from "@/components/ui";
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
import { RequestHeader, ProgressStrip, StageTimeline, HEADER_CSS } from "./_request-header";
import { aiIsWorking } from "@/components/lifecycle-view";
import { canEditObligations } from "@/lib/obligation-access";
import { DecisionArea, DECISION_CSS, type HumanDecision } from "./_decision-area";
import { WhatWasAsked, ASKED_CSS } from "./_request-asked";
import { RiskReview, ReviewHistory, riskFacts, RISK_CSS } from "./_request-risk";
import { WorkflowCard, STEPS_CSS, type ExtraDetail, type FlowState, type StepRow } from "./_request-steps";
import { RequestSummarySection, SUMMARY_CSS } from "./_request-summary";
import { POLISH_CSS, TIMELINE_CSS } from "./_request-polish";
import { KpiStrip, buildKpis } from "./_request-kpis";
import { OverviewPane, OVERVIEW_PANE_CSS, type KeyDate } from "./_request-overview-pane";
import { kindOf } from "./_legal-intake";
import { ActivityCard, CommentsCard, CounterpartyCard, DocumentsCard, PeopleCard, CARDS_CSS } from "./_request-cards";
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
  const { confirm, dialog: confirmDialog } = useConfirm();
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
  const [reassignOpen, setReassignOpen] = useState(false);
  const [tab, setTab] = useState<"overview" | "workflow" | "documents" | "risk" | "ai" | "activity" | "comments" | "related">("overview");

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
    // While the AI is reading the signed copy (after Retry) keep looking until it settles.
    refetchInterval: (q) => (aiIsWorking(q.state.data) ? 4000 : false),
  });
  // Retry / Re-read on the Active stage's obligation row. The server decides who may; this only hides the button.
  const readObligations = canEditObligations(user?.permissions)
    ? async (action: { contract_id: string }) => {
        try {
          await obligationsApi.extract(action.contract_id);
        } finally {
          // Also when the server refuses (it answers 409 when the reading already succeeded): see the contract page.
          await qc.invalidateQueries({ queryKey: ["lifecycle-rows", id] });
        }
      }
    : undefined;
  // Activity and counterparty files each have their own load state, so one failing never blanks the page.
  // The activity key follows the run so a step finishing shows up; keepPreviousData keeps the card from blanking meanwhile.
  const activityQ = useQuery({
    queryKey: ["intake-activity", id, flowRun?.status ?? null, flowRun?.current_index ?? null, cid],
    queryFn: () => intakeApi.activity(id), placeholderData: keepPreviousData,
  });
  const counterpartyQ = useQuery({ queryKey: ["intake-counterparty", id], queryFn: () => intakeApi.counterparty(id) });
  // Own query too: a failing summary shows its Retry inside "What was asked" and never blanks the page.
  const summaryQ = useQuery({ queryKey: ["intake-summary", id], queryFn: () => intakeApi.requestSummary(id) });
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

  const commentRef = useRef<HTMLInputElement>(null);
  // The box lives in the Comments tab, which is inert while hidden: open it first, focus once it has rendered.
  const jumpToComment = () => { setTab("comments"); setTimeout(() => { commentRef.current?.scrollIntoView?.({ block: "center" }); commentRef.current?.focus(); }, 0); };
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
  // A note for the decision itself: saved on the step with Approve, never posted to the comment thread.
  const [decisionNote, setDecisionNote] = useState("");
  const [stepBlock, setStepBlock] = useState<string | null>(null);
  const completeStep = useMutation({
    // Always say which step: of two steps running side by side, the server must not guess.
    mutationFn: () => (owed
      ? workflowsApi.completeStep(flowRun?.id ?? "", undefined, current?.idx, undefined, reply.trim())
      : workflowsApi.completeStep(flowRun?.id ?? "", decisionNote.trim() || undefined, current?.idx)),
    onSuccess: (run) => { qc.setQueryData(["flow-run", id], run); setReply(""); setDecisionNote(""); setStepBlock(null); notify("Step completed", "success"); },
    // A refusal says what to do next, so it stays on the page next to the decision instead of vanishing like a toast.
    onError: (e) => setStepBlock(e instanceof Error ? e.message : "The step could not be completed."),
    onMutate: () => setStepBlock(null),
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
  const busy = reassign.isPending || draft.isPending || ingest.isPending || continueAsTrademark.isPending || startFlow.isPending || completeStep.isPending || checkStep.isPending || returnStep.isPending || retryReview.isPending;
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
  const stepTeam = current?.team_id ? (teams ?? []).find((t) => t.id === current.team_id) : undefined;
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
  // How the draft came about, said once for the drafting row and for the stage tracker.
  const paperChoice = (r.field_values as Record<string, unknown> | null)?.paper;
  const draftHow = paperChoice === "Our template" ? "Drafted from our template"
    : (docs ?? []).length > 0 || paperChoice === "Their paper" ? `Uploaded document provided${(docs ?? [])[0] ? `: ${(docs ?? [])[0].filename}` : ""}` : null;
  const baseRows: StepRow[] = hasRun
    ? runSteps.map((s, i) => {
        // A step the engine skipped (its condition was not met) was never done: it shows as skipped and is
        // not counted as work that happened, but it does not hold the workflow up either.
        // The drafting step is skipped when the contract already exists. For the requester that is not a skip: either
        // we drafted it from our own template, or the document they uploaded is the draft. Say which.
        // A skip with a reason of its own (a Processor DPA: no draft will be made) is a real skip and stays one.
        const draftProvided = s.type === "clm_draft" && s.status === "skipped" && !(s.result as { skipped?: string } | null)?.skipped;
        const draftNote = s.type !== "clm_draft" ? null : draftHow ?? (draftProvided ? "The contract was already in place" : null);
        const isSkipped = s.status === "skipped" && !draftProvided;
        const isDone = isSkipped || draftProvided || ["done", "complete"].includes(s.status);
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
          kind: meta.label, team: s.team_label ?? s.planned_team_label ?? undefined,
          sla: s.sla_hours ? `${s.sla_hours}h to complete` : undefined, updated: isDone || isCur ? s.updated_at : null,
          note: draftNote ?? s.note, parallel: !!s.parallel, conditional: !!s.cond,
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
  // A skipped step whose condition was not met does not apply to this request: it is out of the count (and out of sight).
  const wfSkipped = wf.filter((w) => w.skipped && !w.conditional).length;
  const wfNotApplicable = wf.filter((w) => w.skipped && w.conditional).length;
  const wfDone = wf.filter((w) => w.tone === "done" && !w.skipped).length;
  const wfActiveIdx = wf.findIndex((w) => w.tone === "cur" || w.tone === "warn");
  const stageNow = currentStage({ run: hasRun ? flowRun : null, contractStage: contractStatus?.lifecycle_stage, requestClosed: !open });
  const stageLabel = LIFECYCLE.find((x) => x.key === stageNow)?.label ?? r.stage;

  // --- Risk review: one set of numbers for the header pill, the decision warning and the review card ---
  const rf = riskFacts(risk, deviations, !!cid);

  // Approve the current human step, warning when critical risks are open or nobody reviewed the text.
  async function approveStep() {
    if (replyMissing) return;
    const warning = rf.unknown ? "The risk on this contract's text has not been reviewed."
      : rf.critCount > 0 ? `${rf.critCount} critical-severity risk${rf.critCount === 1 ? "" : "s"} still open on this contract.` : null;
    // The app's own dialog, not the browser's: a native alert reads as an error and can be switched off.
    if (warning && !(await confirm({ title: "Approve without a full review?", message: `${warning} Approve and advance anyway?`, confirmLabel: "Approve anyway" }))) return;
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

  // --- the Overview tab: a labelled sheet, the dates that matter ---
  const filedAt = r.created_at ?? r.submitted_at ?? null;
  const slaDue = filedAt && r.sla_hours > 0 ? new Date(new Date(filedAt).getTime() + r.sla_hours * 3_600_000) : null;
  const slaLeftH = slaDue ? Math.round((slaDue.getTime() - Date.now()) / 3_600_000) : null;
  const slaLeft = slaLeftH == null ? null : `${Math.abs(slaLeftH) < 48 ? `${Math.abs(slaLeftH)} hours` : `${Math.round(Math.abs(slaLeftH) / 24)} days`} ${slaLeftH < 0 ? "overdue" : "left"}`;
  const fmtD = (d: Date | string | null) => (d ? new Date(d).toLocaleDateString([], { day: "numeric", month: "short", year: "numeric" }) : "—");
  const detailRows: { label: string; value: string }[] = [
    { label: "Request title", value: r.subject || r.type_label },
    { label: "Request ID", value: r.ref },
    ...facts.map((f) => (f.label === "Type" ? { label: "Request type", value: f.value } : f)),
    { label: "Priority", value: r.priority },
    ...(r.department ? [{ label: "Requesting department", value: r.department }] : []),
    { label: "Requested by", value: r.requester_name ?? "—" },
    ...(r.filed_by_agent ? [{ label: "Filed by", value: `Agent · ${r.filed_by_agent_name ?? "unknown"}` }] : []),
    { label: "Owner", value: r.assigned_to_label ?? "Unassigned" },
    ...(r.description?.trim() ? [{ label: "Description", value: r.description.trim() }] : []),
  ];
  const nextStep = wf.find((w) => w.tone === "cur" || w.tone === "warn");
  const keyDates: KeyDate[] = [
    { label: "Request created", value: fmtD(filedAt), tone: "done" },
    ...(nextStep?.wait?.due ? [{ label: `${nextStep.label} due`, value: String(nextStep.wait.due), tone: (nextStep.wait.overdue ? "late" : "warn") as KeyDate["tone"] }] : []),
    ...(slaDue ? [{ label: "Response due", value: `${fmtD(slaDue)} · ${slaLeft}`, tone: (slaLeftH! < 0 ? "late" : "todo") as KeyDate["tone"] }] : []),
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
    sendBack: curIdx > 0 ? {
      options: wf.slice(0, curIdx).map((w) => ({ value: String(w.sidx), label: `${(w.sidx ?? 0) + 1}. ${w.label}` })),
      to: backTo, onTo: setBackTo, note: backNote, onNote: setBackNote,
      open: sendBackOpen,
      // The form lives in the Workflow tab: go there, open it, and put the cursor in the step picker once it is on screen.
      onToggle: () => { setTab("workflow"); focusSendBack.current = true; setSendBackOpen(true); setTimeout(() => { sendBackRef.current?.scrollIntoView?.({ block: "center" }); sendBackRef.current?.focus(); }, 0); },
      onSend: () => returnStep.mutate({ to_idx: Number(backTo), note: backNote || undefined }),
    } : null,
    reassign: canAct ? {
      teamName: stepTeam?.name ?? current.team_label ?? "this team",
      // The step's own team (Legal Counsel for a legal review); the wider owner list only when the team is unknown.
      options: (stepTeam ? stepTeam.members.filter((m) => m.active && m.user_id !== r.assigned_to_user_id).map((m) => ({ id: m.user_id, name: m.name ?? "Team member", detail: m.open_count != null ? `${m.open_count} open` : undefined }))
        : (assignees ?? []).filter((a) => a.id !== r.assigned_to_user_id).map((a) => ({ id: a.id, name: a.name }))),
      open: reassignOpen, onToggle: () => setReassignOpen((o) => !o), picked: reassignTo, onPick: setReassignTo,
      onConfirm: () => { reassign.mutate(reassignTo); setReassignOpen(false); },
    } : undefined,
    note: decisionNote, onNote: setDecisionNote,
    risks: [...rf.bySev.critical, ...rf.bySev.high].map((d) => ({ id: d.id, severity: d.severity.toLowerCase() === "critical" ? "critical" : "high", clause: d.clause_type.replace(/_/g, " "), issue: d.issue })),
    onSeeRisks: () => { setTab("risk"); setTimeout(() => document.getElementById("rt-risk")?.scrollIntoView?.({ block: "start", behavior: "smooth" }), 0); },
  } : null;

  // --- steps card extras: the approval chain sits on the approval step; negotiation rounds on the negotiate step for those who cannot act ---
  const extras: ExtraDetail[] = [];
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

  return (
    <div className="ro">
      {confirmDialog}
      <style dangerouslySetInnerHTML={{ __html: RO_CSS + HEADER_CSS + DECISION_CSS + ASKED_CSS + RISK_CSS + STEPS_CSS + CARDS_CSS + REVIEW_NOTICE_CSS + SUMMARY_CSS + OVERVIEW_PANE_CSS + POLISH_CSS + TIMELINE_CSS }} />

      {templateNotice?.id === id ? <MessageBar intent="warning" className="mb-3">{templateNotice.text}</MessageBar> : null}

      <RequestHeader title={r.subject || `${r.type_label}${cp ? ` — ${cp}` : ""}`} refCode={r.ref} requesterName={r.requester_name ?? null}
        raisedAt={r.created_at ?? r.submitted_at ?? null} ownerLabel={r.assigned_to_label ?? null}
        highRisk={!!cid && !!risk && rf.band === "high"} sla={r.sla_status ?? null} onBack={onBack} statusLabel={runComplete ? "Complete" : hasRun ? "In progress" : r.status === "open" ? "Open" : String(r.status)} chips={[cp, typeof fv.agreement_type === "string" ? fv.agreement_type : "", r.type_label.split(" · ")[0], current ? plainStepName(current.name, current.type) : stageLabel].filter((x): x is string => !!x)} icon={(() => { const K = kindOf(r).Icon; return <K />; })()}
        actions={(
          <>
            {hasRun && canAct ? <button type="button" className="btn act-ind" onClick={jumpToComment}><MessageSquarePlus className="ic" aria-hidden />Add comment</button> : null}
            {canAct ? <button type="button" className="btn act-vio" onClick={() => { const el = document.querySelector<HTMLSelectElement>('.ro select[aria-label="New owner"]'); el?.scrollIntoView({ block: "center" }); el?.focus(); }}><UserCog className="ic" aria-hidden />Reassign</button> : null}
            <button type="button" className="btn act-sky" onClick={() => window.dispatchEvent(new CustomEvent("aegis:ask", { detail: { contractId: r?.contract_id } }))}><Sparkles className="ic" aria-hidden />Ask Aegis</button>
            {cid ? <Link href={`/contracts/${cid}`} className="btn act-emr"><FileIcon className="ic" aria-hidden />Open contract</Link> : null}
          </>
        )} />
      <KpiStrip items={buildKpis({
        priority: r.priority, sla: r.sla_status ?? null, slaPct: typeof r.sla_pct === "number" ? r.sla_pct : null,
        risk: cid && risk ? { band: rf.band, unknown: rf.unknown, critical: rf.critCount, findings: rf.findingCount } : null,
        typeLabel: (typeof fv.agreement_type === "string" && fv.agreement_type) || r.type_label, value: value ?? null,
        dueDate: slaDue ? fmtD(slaDue) : null, dueNote: slaLeft, dueLate: slaLeftH != null && slaLeftH < 0,
      })} />

      <ProgressStrip line={line} tone={overdueNow ? "warn" : "info"} working={working} />

      <div className="rtabswrap">
        <div className="rtabs" role="tablist" aria-label="Request sections">
          <button type="button" role="tab" id="rt-overview" aria-selected={tab === "overview"} aria-controls="rp-overview" onClick={() => setTab("overview")}><LayoutDashboard className="ic" aria-hidden />Overview</button>
          <button type="button" role="tab" id="rt-workflow" aria-selected={tab === "workflow"} aria-controls="rp-workflow" onClick={() => setTab("workflow")}><ListChecks className="ic" aria-hidden />Workflow</button>
          <button type="button" role="tab" id="rt-documents" aria-selected={tab === "documents"} aria-controls="rp-documents" onClick={() => setTab("documents")}><FileText className="ic" aria-hidden />Documents {(docs ?? []).length ? <i>{(docs ?? []).length}</i> : null}</button>
          <button type="button" role="tab" id="rt-risk" aria-selected={tab === "risk"} aria-controls="rp-risk" onClick={() => setTab("risk")}><ShieldAlert className="ic" aria-hidden />Risk & compliance</button>
          <button type="button" role="tab" id="rt-ai" aria-selected={tab === "ai"} aria-controls="rp-ai" onClick={() => setTab("ai")}><Sparkles className="ic" aria-hidden />AI analysis</button>
          <button type="button" role="tab" id="rt-activity" aria-selected={tab === "activity"} aria-controls="rp-activity" onClick={() => setTab("activity")}><Activity className="ic" aria-hidden />Activity</button>
          <button type="button" role="tab" id="rt-comments" aria-selected={tab === "comments"} aria-controls="rp-comments" onClick={() => setTab("comments")}><MessagesSquare className="ic" aria-hidden />Comments {(flowRun?.comments ?? []).length ? <i>{(flowRun?.comments ?? []).length}</i> : null}</button>
          <button type="button" role="tab" id="rt-related" aria-selected={tab === "related"} aria-controls="rp-related" onClick={() => setTab("related")}><Link2 className="ic" aria-hidden />Related</button>
        </div>
      </div>

      <div className="body">
        <StageTimeline draftNote={draftHow} steps={plainRun?.steps ?? []} current={stageNow} comments={flowRun?.comments ?? []} extras={lifeRows ?? {}} onAction={readObligations} />
        <div className="col">
          <div role="tabpanel" id="rp-overview" aria-labelledby="rt-overview" className="rpane" data-on={tab === "overview" ? "1" : undefined} {...(tab === "overview" ? {} : { inert: true } as object)}>
          {canAct && negotiation ? <div className="negwrap">{negotiation}</div> : null}
          <OverviewPane rows={detailRows} docs={docs ?? []} activity={activityQ.data?.items ?? []} keyDates={keyDates} onTab={setTab} />
                    <WhatWasAsked part="main" sourceTag={ai.source === "form" ? "from the form" : "as the AI understood it"} facts={facts}
            priority={{ value: r.priority, aiSuggests: aiUrgency ?? null, canApply: canAct, applying: applyPriority.isPending, onApply: () => { if (aiUrgency && !applyPriority.isPending) applyPriority.mutate(aiUrgency); } }}
            description={r.description?.trim() || null}
            summary={<RequestSummarySection status={summaryQ.data ? "ready" : summaryQ.isError ? "error" : "loading"} data={summaryQ.data} onRetry={() => void summaryQ.refetch()} />}
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
          </div>
          <div role="tabpanel" id="rp-workflow" aria-labelledby="rt-workflow" className="rpane" data-on={tab === "workflow" ? "1" : undefined} {...(tab === "workflow" ? {} : { inert: true } as object)}>
          <WorkflowCard rows={wf}
            countText={wf.length ? `${wfDone} of ${wf.length - wfSkipped - wfNotApplicable} steps done${wfSkipped ? `, ${wfSkipped} skipped` : ""}` : null}
            flow={flow} error={flowRun?.error ?? null}
            check={checkLabel ? { label: checkLabel, onClick: () => checkStep.mutate(), busy } : null}
            extras={extras} trace={[]}
            sendBack={hasRun && !runComplete && canAct && curIdx > 0 ? {
              options: wf.slice(0, curIdx).map((w) => ({ value: String(w.sidx), label: `${(w.sidx ?? 0) + 1}. ${w.label}` })),
              to: backTo, note: backNote, onTo: setBackTo, onNote: setBackNote, busy, selectRef: sendBackRef,
              onSend: () => returnStep.mutate({ to_idx: Number(backTo), note: backNote || undefined }),
              open: sendBackOpen, onToggle: () => setSendBackOpen((o) => !o),
            } : null} />
          </div>
          <div role="tabpanel" id="rp-documents" aria-labelledby="rt-documents" className="rpane" data-on={tab === "documents" ? "1" : undefined} {...(tab === "documents" ? {} : { inert: true } as object)}>
          <DocumentsCard docs={docs ?? []} contractHref={r.contract_id ? `/contracts/${r.contract_id}` : null}
            contractActions={canAct && !r.contract_id && docType ? { docType, hasAttachment, busy, onIngest: () => ingest.mutate(), onDraft: () => draft.mutate() } : null}
            trademark={canAct && isTrademarkRequest(r) ? { busy, onContinue: () => continueAsTrademark.mutate() } : null} />
          </div>
          <div role="tabpanel" id="rp-risk" aria-labelledby="rt-risk" className="rpane" data-on={tab === "risk" ? "1" : undefined} {...(tab === "risk" ? {} : { inert: true } as object)}>
          {cid ? <RiskReview risk={risk} facts={rf} canRecompute={canManage} recomputing={rerunRisk.isPending} onRecompute={() => rerunRisk.mutate()} /> : null}
          {cid ? <ReviewHistory contractId={cid} /> : null}
          </div>
          <div role="tabpanel" id="rp-ai" aria-labelledby="rt-ai" className="rpane" data-on={tab === "ai" ? "1" : undefined} {...(tab === "ai" ? {} : { inert: true } as object)}>
          <WhatWasAsked part="ai" sourceTag={ai.source === "form" ? "from the form" : "as the AI understood it"} facts={facts}
            priority={{ value: r.priority, aiSuggests: aiUrgency ?? null, canApply: canAct, applying: applyPriority.isPending, onApply: () => { if (aiUrgency && !applyPriority.isPending) applyPriority.mutate(aiUrgency); } }}
            description={r.description?.trim() || null}
            summary={<RequestSummarySection status={summaryQ.data ? "ready" : summaryQ.isError ? "error" : "loading"} data={summaryQ.data} onRetry={() => void summaryQ.refetch()} />}
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
          </div>
          <div role="tabpanel" id="rp-activity" aria-labelledby="rt-activity" className="rpane" data-on={tab === "activity" ? "1" : undefined} {...(tab === "activity" ? {} : { inert: true } as object)}>
          <ActivityCard status={activityStatus} data={activityQ.data} onRetry={() => void activityQ.refetch()} />
          </div>
          <div role="tabpanel" id="rp-comments" aria-labelledby="rt-comments" className="rpane" data-on={tab === "comments" ? "1" : undefined} {...(tab === "comments" ? {} : { inert: true } as object)}>
          <CommentsCard me={user?.full_name ?? null}
            comments={(flowRun?.comments ?? []).map((c) => ({ actor: c.actor_name, at: c.at, text: c.text, kind: c.kind, stepLabel: c.idx != null && wf[c.idx] ? wf[c.idx].label : null }))}
            composer={hasRun && canAct ? { value: comment, onChange: setComment, onPost: () => postComment.mutate(comment.trim()), posting: postComment.isPending, inputRef: commentRef } : null} />
          </div>
          <div role="tabpanel" id="rp-related" aria-labelledby="rt-related" className="rpane" data-on={tab === "related" ? "1" : undefined} {...(tab === "related" ? {} : { inert: true } as object)}>
          <CounterpartyCard status={counterpartyStatus} data={counterpartyQ.data} parties={(r.parties ?? []) as { name: string }[]} onRetry={() => void counterpartyQ.refetch()} onOpenRequest={openRequest} />
          </div>
        </div>

        <div className="col side">
          {stepBlock ? (
            <div className="stepblock" role="alert">
              <span className="sb-ic" aria-hidden>!</span>
              <div className="sb-tx">
                <b>This step can't be completed yet</b>
                <p>{stepBlock}</p>
                {cid ? <a className="sb-go" href={`/contracts/${cid}`}>Open the contract to decide it</a> : null}
              </div>
              <button type="button" className="sb-x" aria-label="Dismiss" onClick={() => setStepBlock(null)}>×</button>
            </div>
          ) : null}
          <DecisionArea
            approval={pendingApproval ? <ApprovalCard requestId={id} rungs={approvals ?? []} runId={flowRun?.id ?? null} stepName={approvalStepName} subject={approvalSubject} /> : null}
            human={human}
            negotiation={null}
            signature={signature} />
          <PeopleCard stepPerson={(() => { const n = current?.approval?.approver_label ?? current?.assignee_label; return n ? { name: n, role: current?.approval ? "approver" : "current step" } : null; })()} owner={r.assigned_to_label ?? null} requester={r.requester_name ?? null} department={r.department ?? null}
            reassign={canAct ? { options: (assignees ?? []).filter((a) => a.id !== r.assigned_to_user_id).map((a) => ({ id: a.id, name: a.name })), value: reassignTo, onChange: setReassignTo, onReassign: () => reassign.mutate(reassignTo), busy } : null} />
          {counterpartyQ.data?.name ? (
            <div className="card tint-vio">
              <div className="ch"><div className="glyph g-vio"><Building2 className="ic" aria-hidden /></div><h2>About the counterparty</h2></div>
              <div className="cb cpmini">
                <div className="cpn"><i aria-hidden>{counterpartyQ.data.name.slice(0, 1).toUpperCase()}</i><b>{counterpartyQ.data.name}</b></div>
                <div className="cpc"><span><b>{counterpartyQ.data.summary.contracts}</b> contracts</span><span><b>{counterpartyQ.data.summary.active}</b> active</span><span><b>{counterpartyQ.data.summary.requests}</b> requests</span></div>
                <button type="button" className="lnk" onClick={() => setTab("related")}>See contracts and earlier requests</button>
              </div>
            </div>
          ) : null}

        </div>
      </div>
    </div>
  );
}

// Base rules for the page and the shared bits the cards use (.card .ch .cb .btn .sel .cin .composer .facts …).
// Each card's own rules live in its component file and are appended after these.
const RO_CSS = `
.ro .stepblock{display:flex;gap:12px;align-items:flex-start;padding:14px 14px 14px 16px;margin-bottom:14px;border-radius:16px;background:var(--warn-soft);box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--warn) 40%,var(--border));animation:sbin .35s cubic-bezier(.2,.8,.2,1) both}
@keyframes sbin{from{opacity:0;transform:translateY(-6px)}to{opacity:1;transform:none}}
.ro .sb-ic{flex:none;width:26px;height:26px;border-radius:50%;display:grid;place-items:center;background:var(--warn);color:var(--accent-ink);font:800 14px var(--sans)}
.ro .sb-tx{min-width:0;flex:1;font-size:12.5px;line-height:1.5;color:var(--ink-2)} .ro .sb-tx b{display:block;color:var(--ink);font-size:13px;margin-bottom:2px} .ro .sb-tx p{margin:0 0 6px;overflow-wrap:anywhere}
.ro .sb-go{font-weight:650;color:var(--accent);text-decoration:none} .ro .sb-go:hover{text-decoration:underline}
.ro .sb-x{flex:none;width:26px;height:26px;border-radius:50%;font-size:18px;line-height:1;color:var(--ink-3)} .ro .sb-x:hover{background:var(--surface);color:var(--ink)}
@media (prefers-reduced-motion:reduce){.ro .stepblock{animation:none}}
.ro{min-width:0;max-width:1280px;margin:0 auto}
.ro .lbl{font:600 10px var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3)}
.ro .ic{width:15px;height:15px;flex:none;stroke:currentColor;stroke-width:1.7;fill:none;stroke-linecap:round;stroke-linejoin:round}
.ro .btn{gap:7px;padding:0 14px;min-height:34px;border-radius:9px;font-weight:600;transition:background .12s,filter .12s,border-color .12s}
.ro .btn:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
.ro .btn.wide{width:100%}
.ro .body{display:grid;grid-template-columns:minmax(0,1fr) 332px;gap:18px;align-items:start;padding:16px 0 24px}
.ro .col{min-width:0;display:flex;flex-direction:column;gap:16px}
.ro .card{border:1px solid var(--border);border-radius:14px;background:var(--surface);box-shadow:0 1px 2px rgba(20,26,40,.04);transition:box-shadow .15s,border-color .15s}
.ro .card:hover{box-shadow:0 4px 18px -8px rgba(20,26,40,.14)}
.ro .card>.ch{display:flex;align-items:center;gap:10px;padding:13px 18px;border-bottom:1px solid var(--border)}
.ro .card>.ch h2{font-size:14px;font-weight:650;margin:0;letter-spacing:-.01em} .ro .card>.ch .cnt{font:700 10px var(--mono);background:var(--surface-2);color:var(--ink-2);border-radius:99px;padding:1px 7px}
.ro .card>.cb{padding:16px 18px}
.ro .glyph{width:28px;height:28px;border-radius:9px;display:grid;place-items:center;flex:none;background:var(--surface-2);color:var(--ink-2)}
.ro .glyph.warn{background:var(--warn-soft);color:var(--warn)} .ro .glyph.flow{background:var(--accent-soft);color:var(--accent)}
.ro .facts{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:10px 16px;margin-top:0}
.ro .facts .f .k{font:600 10.5px var(--sans);text-transform:uppercase;letter-spacing:.06em;color:var(--ink-3)} .ro .facts .f .v{font-weight:600;font-size:13.5px;margin-top:3px;overflow-wrap:anywhere;min-width:0}
.ro .pill{display:inline-flex;padding:1px 9px;border-radius:99px;font:600 11px var(--sans);white-space:nowrap}
.ro .pill.bad{background:var(--crit-soft);color:var(--crit)} .ro .pill.warn{background:var(--warn-soft);color:var(--warn)}
.ro .cin{min-height:36px;padding:8px 12px;border:1px solid var(--border-strong);border-radius:9px;background:var(--surface);color:var(--ink);font:500 12.5px var(--sans)}
.ro .sel{width:100%;min-height:36px;padding:8px 12px;border:1px solid var(--border-strong);border-radius:9px;background:var(--surface);color:var(--ink);font:600 12.5px var(--sans)}
.ro .composer{display:flex;gap:8px} .ro .composer .cin{flex:1;min-width:0;padding:8px 11px;border:1px solid var(--border-strong);border-radius:9px;background:var(--surface);color:var(--ink);font:500 12.5px var(--sans)}
.ro .spin{gap:7px;opacity:.75;pointer-events:none} .ro .spin .ic{animation:ro-spin 1s linear infinite}
@keyframes ro-spin{to{transform:rotate(360deg)}}
.ro .dim{color:var(--ink-3)}
.ro .analyzing{display:flex;align-items:center;gap:9px;font-size:12.5px;color:var(--ink-2)} .ro .analyzing .ic{width:15px;height:15px;color:var(--accent);animation:ro-spin 1s linear infinite}
.ro .lower{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px;align-items:start;padding:0 0 28px}
/* Von Restorff: whatever needs a decision is the one raised, accent-edged thing on the page. */
.ro .decision>*{border-radius:14px;box-shadow:0 0 0 1px color-mix(in srgb,var(--accent) 35%,var(--border)),0 10px 28px -14px color-mix(in srgb,var(--accent) 45%,transparent)}
.ro .decide{border-radius:12px}
@media (max-width:1180px){.ro .body,.ro .lower{grid-template-columns:minmax(0,1fr)}}
`;
