"use client";

// The Lifecycle tab on the contract page: what to do next, what is in the way,
// and the workflow's steps. The stage itself is shown once, in the stage bar at the
// top of the page, so this tab does not repeat it.
//
// Three ways a contract changes stage, and this tab offers each one honestly:
//   1. Complete the workflow's current step. The workflow then makes the move
//      itself, and for an Approval step it creates the approval chain. This is the
//      only way to "send for approval".
//   2. Move it by hand between the stages the server allows (Intake, Drafting and
//      Review, and pulling a contract back). The server decides what is allowed.
//   3. Override the open-suggestions gate with a reason, if the person is allowed to.
//
// `LifecyclePanel` only shows things and reports clicks; `LifecycleTab` owns the
// data and the server calls.

import Link from "next/link";
import { useEffect, useState, type ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { contractsApi, workflowsApi } from "@/lib/endpoints";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/toast";
import { ContractReviewNotice } from "@/components/review-notice";
import { reviewPollInterval } from "@/lib/review-retry";
import { fmtDateTime, fmtRelative } from "@/lib/utils";
import {
  canStartWorkflow,
  isPreApproval,
  nextStepText,
  readinessItems,
  redlineGate,
  signingBlock,
  STAGE_LABEL,
  stageMoves,
  stepAction,
  workflowSteps,
  workflowView,
  type StageMove,
  type StepAction,
} from "@/lib/lifecycle-actions";
import type {
  ContractLifecycleStage,
  ContractStageHistoryResponse,
  LifecycleOptionsResponse,
  ReviewStatusResponse,
  WorkflowRun,
} from "@/lib/types";

export const OVERRIDE_PERMISSION = "contract:lifecycle_override";

const stageName = (s: string | null | undefined) => (s && s in STAGE_LABEL ? STAGE_LABEL[s as ContractLifecycleStage] : (s ?? "—"));

// ---------------------------------------------------------------------------
// The view
// ---------------------------------------------------------------------------

export interface LifecyclePanelProps {
  /** Shown first: the state of the contract's AI review, when it is running or was interrupted. */
  notice?: ReactNode;
  stage: ContractLifecycleStage;
  options: LifecycleOptionsResponse | undefined;
  review: ReviewStatusResponse | undefined;
  history: ContractStageHistoryResponse[] | undefined;
  run: WorkflowRun | null | undefined;
  canOverride: boolean;
  busy: boolean;
  onCompleteStep: (action: StepAction, overrideReason?: string, reworkReply?: string) => void;
  onStartWorkflow: () => void;
  onMove: (move: StageMove, reason?: string) => void;
  onOpenReview: () => void;
  /** Opens the Comments tab. When given, the "comments still to resolve" line gets a button that goes there. */
  onOpenComments?: () => void;
}

export function LifecyclePanel(p: LifecyclePanelProps) {
  const view = workflowView(p.run);
  const action = stepAction(view, p.stage);
  const openRedlines = p.review?.pending_redlines ?? 0;
  const gate = redlineGate(action, openRedlines, p.canOverride);
  const moves = stageMoves(p.stage, p.options?.allowed_transitions ?? []);
  const next = nextStepText(view, action, gate, p.review?.version_approval);
  const steps = workflowSteps(p.run);
  const ready = p.review && isPreApproval(p.stage) ? readinessItems(p.review) : [];

  const [pending, setPending] = useState<StageMove | null>(null);
  const [moveReason, setMoveReason] = useState("");
  const [overriding, setOverriding] = useState(false);
  const [overrideReason, setOverrideReason] = useState("");
  // After a rejected approval the server wants a written reply before the step can go on.
  const [reply, setReply] = useState("");
  const owed = p.run?.rework ?? null;
  const replyMissing = !!owed && !reply.trim();
  useEffect(() => { if (!owed) setReply(""); }, [owed]);

  const startMove = (m: StageMove) => {
    if (m.reasonRequired || m.confirm) {
      setPending(m);
      setMoveReason("");
    } else p.onMove(m);
  };
  const confirmMove = () => {
    if (!pending) return;
    if (pending.reasonRequired && !moveReason.trim()) return;
    p.onMove(pending, moveReason.trim() || undefined);
    setPending(null);
    setMoveReason("");
  };

  return (
    <div className="list lcx">
      {p.notice}
      {/* the one thing to do now */}
      <section className="lcx-card lcx-next" aria-label="What to do next">
        <div className="lcx-k">What to do next</div>
        <h3 className="lcx-title">{next.title}</h3>
        <p>{next.body}</p>

        {(view.kind === "none" || (view.kind === "ended" && canStartWorkflow(view))) && (
          <button className="btn sm pri" disabled={p.busy} onClick={p.onStartWorkflow}>
            {view.kind === "none" ? "Start workflow" : "Start a new workflow"}
          </button>
        )}

        {view.kind === "working" && view.step?.type === "signature" && !signingBlock(p.review?.version_approval) && (
          <Link href="/signatures" className="btn sm pri">Open Signatures</Link>
        )}

        {view.kind === "person" && action && owed ? (
          <div className="lcx-ovr">
            <label htmlFor="lcx-rework-reply">Reply to the rejection</label>
            <div className="lcx-d">Rejected: “{owed.reason}”</div>
            <textarea id="lcx-rework-reply" rows={3} maxLength={2000} value={reply} onChange={(e) => setReply(e.target.value)}
              placeholder="What did you change, or why does it stand? The approver sees this." />
          </div>
        ) : null}

        {view.kind === "person" && action && (
          gate ? (
            <>
              <div className="lcx-row">
                <button className="btn sm pri" onClick={p.onOpenReview}>Go to Review</button>
                {gate.canOverride && !overriding ? <button className="btn sm" onClick={() => setOverriding(true)}>Send anyway…</button> : null}
              </div>
              {gate.canOverride && overriding ? (
                <div className="lcx-ovr">
                  <label htmlFor="lcx-ovr-reason">Why are you sending it with open changes? This is recorded.</label>
                  <textarea id="lcx-ovr-reason" rows={2} value={overrideReason} onChange={(e) => setOverrideReason(e.target.value)} />
                  <div className="lcx-row">
                    <button className="btn sm pri" disabled={p.busy || !overrideReason.trim() || replyMissing} onClick={() => (owed ? p.onCompleteStep(action, overrideReason.trim(), reply.trim()) : p.onCompleteStep(action, overrideReason.trim()))}>
                      {action.label}
                    </button>
                    <button className="btn sm" onClick={() => { setOverriding(false); setOverrideReason(""); }}>Cancel</button>
                  </div>
                </div>
              ) : null}
            </>
          ) : (
            <button className="btn sm pri" disabled={p.busy || replyMissing} onClick={() => (owed ? p.onCompleteStep(action, undefined, reply.trim()) : p.onCompleteStep(action))}>{action.label}</button>
          )
        )}
      </section>

      {/* what is in the way: only while the contract is before approval */}
      {ready.length > 0 && (
        <section className="lcx-card" aria-label="Before it goes for approval">
          <div className="lcx-k">Before it goes for approval</div>
          <ul className="lcx-list">
            {ready.map((c) => (
              <li key={c.key} className={`st-${c.state}`}>
                <span className="lcx-dot" aria-hidden="true" />
                <span className="lcx-txt">
                  <span className="lcx-t">{c.text}</span>
                  {c.note ? <span className="lcx-d">{c.note}</span> : null}
                </span>
                {c.need && c.state !== "done" ? <span className={`lcx-need ${c.need}`}>{c.need}</span> : null}
                {c.key === "comments" && c.state !== "done" && p.onOpenComments ? (
                  <button type="button" className="btn sm" style={{ marginTop: 0 }} onClick={p.onOpenComments}>Open comments</button>
                ) : null}
              </li>
            ))}
          </ul>
          <p className="lcx-d">Required items must be done first. Advisory ones are worth doing but won’t stop it.</p>
        </section>
      )}

      {/* the workflow: the steps that move the contract from stage to stage */}
      {p.run && steps.length > 0 && (
        <section className="lcx-card" aria-label="The workflow">
          <div className="lcx-k">The workflow</div>
          <div className="lcx-wf">{p.run.flow_name}</div>
          <p className="lcx-d">The stage bar at the top shows where the contract is. The workflow is the list of steps below. Finishing them is what moves the contract from stage to stage.</p>
          <ol className="lcx-steps">
            {steps.map((st) => (
              <li key={st.idx} className={`s-${st.tone}`} aria-current={st.tone === "now" ? "step" : undefined}>
                <span className="lcx-mark" aria-hidden="true" />
                <span className="lcx-txt">
                  <span className="lcx-t">{st.name}</span>
                  <span className="lcx-d">{st.line}</span>
                  {st.note ? <span className="lcx-d bad">{st.note}</span> : null}
                </span>
              </li>
            ))}
          </ol>
        </section>
      )}

      {/* notes written on the request's workflow: internal, apart from the contract's own comments */}
      {p.run && (p.run.comments ?? []).length > 0 && (
        <section className="lcx-card" aria-label="Notes on the workflow">
          <div className="lcx-k">Notes on the workflow</div>
          <p className="lcx-d">Written on the request. They stay inside your team. The Comments tab holds the comments you can share with the other side.</p>
          <ul className="lcx-notes">
            {[...(p.run.comments ?? [])].reverse().slice(0, 5).map((c, i) => (
              <li key={`${c.at}-${i}`}>
                <span className="lcx-t">{c.actor_name}{c.kind === "return" ? " sent it back" : c.kind === "reply" ? " replied to the rejection" : ""}</span>
                <span className="lcx-d">{c.text}</span>
                <span className="lcx-d">{fmtDateTime(c.at)}</span>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* manual moves */}
      {moves.length > 0 && (
        <section className="lcx-card" aria-label="Change the stage by hand">
          <div className="lcx-k">Change the stage by hand</div>
          <p>The workflow normally moves the contract for you. Use these to go back a stage, or when no workflow is running.</p>
          <div className="lcx-row">
            {moves.map((m) => (
              <button key={m.to} className="btn sm" disabled={p.busy} onClick={() => startMove(m)}>{m.label}</button>
            ))}
          </div>
          {pending ? (
            <div className="lcx-ovr">
              {pending.reasonRequired ? (
                <>
                  <label htmlFor="lcx-move-reason">Why are you moving it back to {STAGE_LABEL[pending.to]}? This is recorded.</label>
                  <textarea id="lcx-move-reason" rows={2} value={moveReason} onChange={(e) => setMoveReason(e.target.value)} />
                  <p className="lcx-d">Going back stops any {p.stage === "approval" ? "approval" : "signing"} that is in progress.</p>
                </>
              ) : (
                <p>Close this contract? It stays searchable, but it is no longer tracked for obligations and renewals.</p>
              )}
              <div className="lcx-row">
                <button className="btn sm pri" disabled={p.busy || (pending.reasonRequired && !moveReason.trim())} onClick={confirmMove}>
                  {pending.confirm ? "Yes, close it" : pending.label}
                </button>
                <button className="btn sm" onClick={() => { setPending(null); setMoveReason(""); }}>Cancel</button>
              </div>
            </div>
          ) : null}
        </section>
      )}

      {/* history */}
      <section className="lcx-card" aria-label="Stage history">
        <div className="lcx-k">History</div>
        {!p.history ? (
          <p className="lcx-d">Loading…</p>
        ) : p.history.length === 0 ? (
          <p className="lcx-d">No stage changes yet.</p>
        ) : (
          <ul className="lcx-list hist">
            {[...p.history].reverse().map((h) => (
              <li key={h.id}>
                <span className="lcx-dot" aria-hidden="true" />
                <span>
                  {h.from_stage ? `${stageName(h.from_stage)} → ${stageName(h.to_stage)}` : `Started in ${stageName(h.to_stage)}`}
                  {h.override_used ? <span className="lcx-tag" title="This move skipped a normal stage rule. The system does this too, for example when a contract is sent back after a rejection or finishes approval. The reason, if there is one, is shown beside it."> outside the usual order</span> : null}
                  <span className="lcx-d"> {fmtRelative(h.changed_at)}</span>
                  {h.reason ? <span className="lcx-reason">“{h.reason}”</span> : null}
                </span>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

// ---------------------------------------------------------------------------
// The tab: data and server calls
// ---------------------------------------------------------------------------

/** What to refetch after anything that can change a contract's stage. */
export const lifecycleQueryKeys = (id: string) => [
  ["contract", id],
  ["contract-lifecycle", id],
  ["review-status", id],
  ["contract-stage-history", id],
  ["flow-run-contract", id],
  ["lifecycle-rows-contract", id],
  ["contract-edits", id],
];

export function LifecycleTab({ contractId, contractStage, run, onOpenReview, onOpenComments }: {
  contractId: string;
  contractStage: ContractLifecycleStage;
  run: WorkflowRun | null | undefined;
  onOpenReview: () => void;
  onOpenComments?: () => void;
}) {
  const { user } = useAuth();
  const { notify } = useToast();
  const qc = useQueryClient();

  const options = useQuery({ queryKey: ["contract-lifecycle", contractId], queryFn: () => contractsApi.lifecycleOptions(contractId) });
  const review = useQuery({ queryKey: ["review-status", contractId], queryFn: () => contractsApi.reviewStatus(contractId), refetchInterval: (q) => reviewPollInterval(q.state.data?.auto_review) });
  const history = useQuery({ queryKey: ["contract-stage-history", contractId], queryFn: () => contractsApi.stageHistory(contractId) });

  const refresh = () => {
    for (const queryKey of lifecycleQueryKeys(contractId)) qc.invalidateQueries({ queryKey });
  };
  const fail = (fallback: string) => (e: unknown) => {
    notify(e instanceof Error && e.message ? e.message : fallback, "error");
    // The server may have moved things before refusing: show what is true now.
    refresh();
  };

  const move = useMutation({
    mutationFn: ({ move, reason }: { move: StageMove; reason?: string }) => contractsApi.transition(contractId, move.to, reason ? { reason } : {}),
    onSuccess: (_c, { move }) => { notify(`Moved to ${STAGE_LABEL[move.to]}`, "success"); refresh(); },
    onError: fail("Couldn't move the contract"),
  });
  const complete = useMutation({
    mutationFn: ({ action, overrideReason, reworkReply }: { action: StepAction; overrideReason?: string; reworkReply?: string }) =>
      reworkReply
        ? workflowsApi.completeStep(run?.id ?? "", undefined, action.stepIdx, overrideReason, reworkReply)
        : workflowsApi.completeStep(run?.id ?? "", undefined, action.stepIdx, overrideReason),
    onSuccess: (r, { action }) => {
      qc.setQueryData(["flow-run-contract", contractId], r);
      notify(action.sendsForApproval ? "Sent for approval" : `Completed “${action.stepName}”`, "success");
      refresh();
    },
    onError: fail("Couldn't complete the step"),
  });
  const start = useMutation({
    mutationFn: () => workflowsApi.startForContract(contractId),
    onSuccess: (r) => { qc.setQueryData(["flow-run-contract", contractId], r); notify(`Workflow started: ${r.flow_name}`, "success"); refresh(); },
    onError: fail("Couldn't start a workflow"),
  });

  return (
    <LifecyclePanel
      notice={<ContractReviewNotice contractId={contractId} auto={review.data?.auto_review} byHand={workflowView(run).kind === "person"} />}
      stage={options.data?.current_stage ?? contractStage}
      options={options.data}
      review={review.data}
      history={history.data}
      run={run}
      canOverride={!!user && (user.permissions.includes(OVERRIDE_PERMISSION) || user.permissions.includes("*"))}
      busy={move.isPending || complete.isPending || start.isPending}
      onCompleteStep={(action, overrideReason, reworkReply) => complete.mutate({ action, overrideReason, reworkReply })}
      onStartWorkflow={() => start.mutate()}
      onMove={(m, reason) => move.mutate({ move: m, reason })}
      onOpenReview={onOpenReview}
      onOpenComments={onOpenComments}
    />
  );
}

export const LIFECYCLE_TAB_CSS = `
.clm .lcx{display:flex;flex-direction:column;gap:12px}
.clm .lcx p{margin:6px 0 0;font-size:12.5px;line-height:1.5;color:var(--ink-2)}
.clm .lcx .lcx-k{font:600 10px var(--sans);letter-spacing:.08em;text-transform:uppercase;color:var(--ink-3)}
.clm .lcx-card{border:1px solid var(--border);border-radius:12px;padding:13px 14px;background:var(--surface)}
.clm .lcx-title{margin:5px 0 0;font-size:15px;font-weight:650;letter-spacing:-.005em;color:var(--ink)}
.clm .lcx-next{border-color:color-mix(in srgb,var(--accent) 30%,var(--border))}
.clm .lcx-card .btn{margin-top:10px}
.clm .lcx-next>.btn.pri{width:100%;height:38px;font-size:13px}
.clm .lcx-row{display:flex;gap:7px;flex-wrap:wrap;margin-top:10px} .clm .lcx-row .btn{margin-top:0}
.clm .lcx-next>.lcx-row>.btn.pri{flex:1;height:38px;font-size:13px}
.clm .lcx-ovr{margin-top:10px;display:flex;flex-direction:column;gap:6px;font-size:12px;color:var(--ink-2)}
.clm .lcx-ovr textarea{padding:7px 10px;border:1px solid var(--border-strong);border-radius:8px;background:var(--surface);color:var(--ink);font:12.5px var(--sans);outline:none;resize:vertical} .clm .lcx-ovr textarea:focus{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.clm .lcx-notes{list-style:none;margin:6px 0 0;padding:0;display:flex;flex-direction:column}
.clm .lcx-notes li{display:flex;flex-direction:column;gap:2px;font-size:12.5px;line-height:1.45;color:var(--ink);padding:9px 0;border-top:1px solid var(--border)} .clm .lcx-notes li:first-child{border-top:0}
.clm .lcx-list,.clm .lcx-steps{list-style:none;margin:6px 0 0;padding:0;display:flex;flex-direction:column}
.clm .lcx-list li,.clm .lcx-steps li{display:flex;gap:10px;font-size:12.5px;line-height:1.45;color:var(--ink);padding:9px 0;border-top:1px solid var(--border)} .clm .lcx-list li:first-child,.clm .lcx-steps li:first-child{border-top:0}
.clm .lcx-dot,.clm .lcx-mark{width:9px;height:9px;border-radius:50%;margin-top:4px;flex:none;background:var(--ink-3)}
.clm .lcx-list:not(.hist) .lcx-dot,.clm .lcx-mark{width:16px;height:16px;margin-top:1px;background:none;border:2px solid var(--border-strong);position:relative}
.clm .lcx-list .st-done .lcx-dot,.clm .lcx-steps .s-done .lcx-mark{background:var(--good);border-color:var(--good)}
.clm .lcx-list .st-done .lcx-dot::after,.clm .lcx-steps .s-done .lcx-mark::after{content:"";position:absolute;left:4px;top:1px;width:4px;height:8px;border:solid #fff;border-width:0 2px 2px 0;transform:rotate(45deg)}
.clm .lcx-list .st-open .lcx-dot{border-color:var(--risk-med)}
.clm .lcx-list .st-going .lcx-dot,.clm .lcx-steps .s-now .lcx-mark{border-color:var(--accent);background:var(--accent-soft)}
.clm .lcx-steps .s-bad .lcx-mark{border-color:var(--crit)}
.clm .lcx-steps .s-skip .lcx-mark{border-style:dashed}
.clm .lcx-steps .s-todo .lcx-t,.clm .lcx-steps .s-skip .lcx-t{color:var(--ink-2)}
.clm .lcx-list.hist .lcx-dot{background:var(--border-strong)}
.clm .lcx-wf{margin-top:4px;font-size:13.5px;font-weight:600;color:var(--ink)}
.clm .lcx-txt{min-width:0}
.clm .lcx-t{font-weight:600}
.clm .lcx .lcx-d{font-size:12px;color:var(--ink-3)} .clm .lcx-list:not(.hist) .lcx-d,.clm .lcx-steps .lcx-d{display:block} .clm .lcx-d.bad{color:var(--crit)}
.clm .lcx-need{flex:none;margin-left:auto;align-self:flex-start;margin-top:2px;font:600 9px var(--sans);padding:2px 7px;border-radius:99px;text-transform:uppercase;letter-spacing:.04em;background:var(--surface-2);color:var(--ink-3)}
.clm .lcx-need.required{background:var(--warn-soft);color:var(--warn)}
.clm .lcx-tag{font:600 10px var(--sans);padding:1px 7px;border-radius:99px;background:var(--surface-2);color:var(--ink-3);cursor:help}
.clm .lcx-reason{display:block;font-size:12px;color:var(--ink-2);margin-top:2px}
`;
