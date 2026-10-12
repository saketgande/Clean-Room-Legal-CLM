// What can be done to a contract's lifecycle from the contract page, and why not.
//
// The stage rules live on the server (ALLOWED_TRANSITIONS, the open-redline gate,
// the approval-before-signature check). This file decides what to OFFER, from
// what the server reports: it never widens those rules.
//
// Approvals are only ever started by a workflow's Approval step, so there is no
// "move to Approval" button. Sending a contract for approval means completing the
// workflow's review step; the workflow then moves the contract and creates the
// approval chain.

import type { ContractLifecycleStage, ReviewStatusResponse, WorkflowRun, WorkflowRunStep } from "./types";
import { stepAfter, stepInFocus } from "./workflow-steps";

export const STAGES: ContractLifecycleStage[] = ["intake", "drafting", "review", "approval", "signature", "active", "closed"];

export const STAGE_LABEL: Record<ContractLifecycleStage, string> = {
  intake: "Intake",
  drafting: "Drafting",
  review: "Review",
  approval: "Approval",
  signature: "Signature",
  active: "Active",
  closed: "Closed",
};

const PRE_APPROVAL = new Set<ContractLifecycleStage>(["intake", "drafting", "review"]);
export const isPreApproval = (stage: ContractLifecycleStage) => PRE_APPROVAL.has(stage);

// ---- the workflow ----------------------------------------------------------

export type WorkflowView =
  | { kind: "none" }
  | { kind: "ended"; status: "complete" | "failed" | "cancelled"; error: string | null }
  | { kind: "person"; step: WorkflowRunStep; next: WorkflowRunStep | null }
  | { kind: "approvals"; step: WorkflowRunStep }
  | { kind: "working"; step: WorkflowRunStep | null };

export function workflowView(run: WorkflowRun | null | undefined): WorkflowView {
  if (!run) return { kind: "none" };
  if (run.status === "complete" || run.status === "failed" || run.status === "cancelled")
    return { kind: "ended", status: run.status, error: run.error };
  // Steps that run side by side: look at the one still waiting, not at the first of them (already done).
  const step = stepInFocus(run) ?? null;
  if (!step) return { kind: "working", step: null };
  if (step.status === "waiting_human") return { kind: "person", step, next: stepAfter(run, step) };
  if (step.type === "approval" && step.status === "waiting_job") return { kind: "approvals", step };
  return { kind: "working", step };
}

/** A new workflow can be started when none is running (a finished one is left alone). */
export const canStartWorkflow = (view: WorkflowView) =>
  view.kind === "none" || (view.kind === "ended" && view.status !== "complete");

export interface StepAction {
  label: string;
  stepIdx: number;
  stepName: string;
  /** The next step is an Approval step: completing this one creates the approval chain. */
  sendsForApproval: boolean;
  /** Completing this step takes the contract out of Review, so open suggestions block it. */
  leavesReview: boolean;
}

/** The button for a workflow step that is waiting on a person. */
export function stepAction(view: WorkflowView, stage: ContractLifecycleStage): StepAction | null {
  if (view.kind !== "person") return null;
  const { step, next } = view;
  const sendsForApproval = next?.type === "approval";
  const leavesReview = isPreApproval(stage) && (next?.type === "approval" || next?.type === "signature");
  const label = sendsForApproval
    ? `Complete "${step.name}" and send for approval`
    : leavesReview
      ? `Complete "${step.name}" and go to signing`
      : `Complete "${step.name}"`;
  return { label, stepIdx: step.idx, stepName: step.name, sendsForApproval, leavesReview };
}

export type Gate = { open: number; canOverride: boolean };

/** Open suggestions stand between this step and approval. */
export function redlineGate(action: StepAction | null, openRedlines: number, canOverride: boolean): Gate | null {
  if (!action?.leavesReview || openRedlines <= 0) return null;
  return { open: openRedlines, canOverride };
}

// ---- what to tell the person -----------------------------------------------------

const plural = (count: number, one: string, many = `${one}s`) => `${count} ${count === 1 ? one : many}`;
const whoHas = (step: WorkflowRunStep) => step.assignee_label || step.team_label || null;

export interface NextStepText {
  title: string;
  body: string;
}

export type VersionApproval = NonNullable<ReviewStatusResponse["version_approval"]>;

/** Approval belongs to a VERSION. When the version on screen has not been approved (it was edited after
 *  approval, is still with the approvers, or was rejected) it cannot be sent for signature: the server
 *  refuses it. Says so in words; null when signing is open. */
export function signingBlock(approval: VersionApproval | null | undefined): NextStepText | null {
  switch (approval) {
    case "changed":
      return {
        title: "Approval doesn't cover this version",
        body: "The contract was approved and then edited. Approval belongs to a version, so the version on screen has not been approved and cannot be sent for signature. Pull the contract back to Review below, then send it for approval again.",
      };
    case "pending":
      return { title: "Waiting for approval", body: "This version is with the approvers. It can be sent for signature once they have approved it." };
    case "rejected":
      return { title: "Approval was rejected", body: "This version was rejected, so it cannot be sent for signature. Pull the contract back to Review below, make the changes, and send it for approval again." };
    default:
      return null;
  }
}

export interface EditCaution {
  /** Shown above the text while it is being edited. */
  notice: string;
  /** What the toast says once the edit is saved. */
  saved: string;
}

/** What editing the text means once approval has started. Saving always makes a new version, and approval does
 *  not carry over to it, so a person who edits an approved contract should hear that before they type. */
export function editCaution(stage: ContractLifecycleStage | string | undefined, approval: VersionApproval | null | undefined): EditCaution | null {
  if (stage === "approval")
    return {
      notice: "This contract is with the approvers. Saving makes a new version that has not been sent for approval, so what they are deciding on will no longer be the current text.",
      saved: "Saved as a new version. It has not been sent for approval.",
    };
  if (stage === "signature" || stage === "active") {
    const unapproved = approval && approval !== "none";
    return {
      notice: unapproved
        ? `This contract is past approval. Saving makes a new version that nobody has approved: it cannot be sent for signature until it goes through approval again. A copy that was already sent for signing keeps the old text.`
        : `This contract is already at ${stage === "active" ? "the active" : "the signature"} stage. Saving makes a new version. A copy that was already sent for signing keeps the old text.`,
      saved: unapproved ? "Saved as a new version. It has not been approved." : "Saved as a new version.",
    };
  }
  return null;
}

/** An AI step waits for a person even after the AI is done: the page used to say nothing about that, so
 *  it looked as if the AI was still working. A step that only matched keywords is not a review. */
function aiStepLine(step: WorkflowRunStep): string | null {
  if (step.type !== "ai_task") return null;
  const result = step.result;
  if (result && result.source === "regex")
    return "No AI analysis ran here, only a keyword check of the request text. Look at the contract yourself before you finish this step.";
  const summary = result && typeof result.summary === "string" ? result.summary.trim().replace(/[.\s]+$/, "") : "";
  const warning =
    result && result.risk_band === "high"
      ? " High-risk findings are still open: settle them in the Review tab before you finish this step."
      : "";
  return `The AI has finished its part${summary ? `: ${summary}` : ""}. The step stays open until you confirm it.${warning}`;
}

/** The one thing to do now, in words. `gate` is set when open suggestions stand in the way. */
export function nextStepText(view: WorkflowView, action: StepAction | null, gate: Gate | null, approval?: VersionApproval | null): NextStepText {
  switch (view.kind) {
    case "none":
      return {
        title: "Start a workflow",
        body: "This contract has no workflow yet. A workflow is the list of steps it goes through, such as who reviews it and who approves it. It moves the contract from stage to stage for you.",
      };
    case "ended":
      if (view.status === "complete") return { title: "The workflow is finished", body: "Every step is done. Nothing here is waiting on you." };
      return {
        title: "The workflow stopped",
        body: `It ${view.status === "failed" ? "stopped with an error" : "was cancelled"}${view.error ? `: ${view.error.replace(/[.\s]+$/, "")}` : ""}. Start a new one to carry on.`,
      };
    case "working":
      // A signature step is out with the signers: calling that "working" next to a step that says "Waiting
      // for signatures" read as two different states (round 2, bug 18), and nothing said where signing starts.
      if (view.step?.type === "signature") {
        const blocked = signingBlock(approval);
        if (blocked) return blocked;
        return {
          title: "Waiting for signatures",
          body: `“${view.step.name}” is the workflow's current step. A contract is sent for signing from the Signatures page, and this step finishes when everyone has signed. This page updates when they have.`,
        };
      }
      return {
        title: "The workflow is working",
        body: `${view.step ? `It is on “${view.step.name}”. ` : ""}This page updates when it finishes.`,
      };
    case "approvals":
      return {
        title: "Waiting for the approvers",
        body: `“${view.step.name}” is with the approvers, who decide in their own queue.${view.step.authority_note ? ` ${view.step.authority_note}` : ""}`,
      };
    case "person": {
      const who = whoHas(view.step);
      if (gate) {
        const they = gate.open === 1 ? "It is" : "They are";
        const them = gate.open === 1 ? "it" : "them";
        return {
          title: `Decide ${plural(gate.open, "suggested change")} first`,
          body: `${they} still open. Accept or reject ${them} in the Review tab, so the version that goes on is the one you checked. Then you can finish “${view.step.name}”.`,
        };
      }
      const title = action?.sendsForApproval ? "Send it for approval" : action?.leavesReview ? "Send it on to signing" : `Finish “${view.step.name}”`;
      const effect = action?.sendsForApproval
        ? " Finishing it creates the approval chain and moves the contract to Approval."
        : action?.leavesReview
          ? " Finishing it moves the contract to Signature."
          : "";
      const aiLine = aiStepLine(view.step);
      return { title, body: `“${view.step.name}” is the workflow's current step${who ? `, assigned to ${who}` : ""}.${effect}${aiLine ? ` ${aiLine}` : ""}` };
    }
  }
}

// ---- the workflow's steps --------------------------------------------------------

export interface WorkflowStepRow {
  idx: number;
  name: string;
  tone: "done" | "now" | "todo" | "bad" | "skip";
  /** What kind of step, then where it is: "Approval · Waiting for the approvers". */
  line: string;
  /** Why it failed, when it did. */
  note: string | null;
}

const STEP_TYPE_LABEL: Record<string, string> = {
  ai_task: "AI task",
  human_task: "Person",
  clm_draft: "Drafting",
  approval: "Approval",
  signature: "Signature",
  counterparty: "Other side",
  notify: "Notification",
};

/** The workflow's steps as the person should read them. The server's step statuses are
 *  pending, running, waiting_human, waiting_job, done, skipped and failed. */
export function workflowSteps(run: WorkflowRun | null | undefined): WorkflowStepRow[] {
  if (!run) return [];
  return [...run.steps]
    .sort((a, b) => a.idx - b.idx)
    .map((s) => {
      const who = whoHas(s);
      let tone: WorkflowStepRow["tone"];
      let where: string;
      switch (s.status) {
        case "done":
          tone = "done";
          where = "Done";
          break;
        case "skipped":
          tone = "skip";
          where = "Skipped";
          break;
        case "failed":
          tone = "bad";
          where = "Failed";
          break;
        case "waiting_human":
          tone = "now";
          where = who ? `Waiting on ${who}` : "Waiting for a person";
          break;
        case "waiting_job":
          tone = "now";
          where = s.type === "approval" ? "Waiting for the approvers" : s.type === "signature" ? "Waiting for signatures" : "In progress";
          break;
        case "running":
          tone = "now";
          where = "Running";
          break;
        default:
          tone = "todo";
          where = s.planned_team_label ? `Not started · ${s.planned_team_label}` : "Not started";
      }
      return {
        idx: s.idx,
        name: s.name,
        tone,
        line: `${STEP_TYPE_LABEL[s.type] ?? s.type} · ${where}`,
        note: tone === "bad" ? s.note : null,
      };
    });
}

// ---- before it goes for approval ---------------------------------------------------

export interface ReadinessItem {
  key: string;
  text: string;
  note: string | null;
  state: "done" | "open" | "going";
  /** Required: the server stops it going on. Advisory: worth doing, never stops it. Optional: only if wanted. */
  need: "required" | "advisory" | "optional" | null;
}

/** The server's readiness checklist in plain sentences. The keys are the server's
 *  (contracts/service.py, compute_review_status); an unknown key is shown with the
 *  server's own label. "approval" is left out: the next-step card already says it. */
export function readinessItems(review: ReviewStatusResponse): ReadinessItem[] {
  const items: ReadinessItem[] = [];
  for (const c of review.checklist) {
    if (c.key === "approval") continue;
    const state: ReadinessItem["state"] = c.status === "done" ? "done" : c.status === "in_progress" ? "going" : "open";
    const open = state !== "done";
    switch (c.key) {
      case "ai_review":
        items.push({
          key: c.key,
          state,
          need: "advisory",
          text: open ? "Run an AI review to check the draft for risks and missing clauses" : "The AI has reviewed this contract",
          note: open ? null : c.detail,
        });
        break;
      case "issues": {
        // "No open issues" with a green tick is only true once a review has read the text. With none yet
        // (or one that was interrupted) it says what it does not know instead (round 3).
        const aiRow = review.checklist.find((x) => x.key === "ai_review");
        const notReviewed = !open && !!aiRow && aiRow.status !== "done";
        items.push({
          key: c.key,
          state: notReviewed ? "open" : state,
          need: "advisory",
          text: notReviewed
            ? "Open issues are not known yet: the AI has not reviewed this contract"
            : open ? `${plural(c.count, "issue")} from the review ${c.count === 1 ? "is" : "are"} still open` : "No open issues from the review",
          note: open && review.high_severity_issues > 0 ? `${review.high_severity_issues} high or critical` : null,
        });
        break;
      }
      case "redlines":
        items.push({
          key: c.key,
          state,
          need: "required",
          text: open ? `${plural(c.count, "suggested change")} still to accept or reject` : "Every suggested change is decided",
          note: null,
        });
        break;
      case "comments":
        items.push({
          key: c.key,
          state,
          need: "advisory",
          text: open ? `${plural(c.count, "comment")} still to resolve` : "No open comments",
          note: null,
        });
        break;
      case "blanks":
        items.push({
          key: c.key,
          state,
          need: "advisory",
          text: open ? `${plural(c.count, "blank")} still to fill in the contract text` : "No blanks left to fill in",
          note: open ? `${c.detail ?? ""}${c.detail ? ". " : ""}${c.count === 1 ? "It" : "They"} can't be sent for signature until ${c.count === 1 ? "it is" : "they are"} filled in.` : null,
        });
        break;
      case "counterparty":
        items.push({
          key: c.key,
          state,
          need: "optional",
          text: state === "going" ? "Shared with the other side. Waiting for their reply" : "Not shared with the other side yet",
          note: null,
        });
        break;
      default:
        items.push({ key: c.key, state, need: null, text: c.label, note: c.detail });
    }
  }
  return items;
}

// ---- moves the person makes themselves ---------------------------------------

export interface StageMove {
  to: ContractLifecycleStage;
  label: string;
  /** Pulling a contract back is recorded with a reason. */
  reasonRequired: boolean;
  /** Ask "are you sure" first. */
  confirm: boolean;
}

const MOVE_LABEL: Partial<Record<ContractLifecycleStage, Partial<Record<ContractLifecycleStage, string>>>> = {
  intake: { drafting: "Start drafting", review: "Move to Review" },
  drafting: { review: "Move to Review" },
  review: { drafting: "Back to Drafting" },
  approval: { review: "Send back to Review" },
  signature: { review: "Pull back to Review" },
  active: { closed: "Close contract" },
};

/** The stage moves to offer, from what the server says is allowed. Left out on
 *  purpose: Approval (the workflow makes that move), Signature from Review (skips
 *  approval) and Active from Signature (signing makes that move). */
export function stageMoves(stage: ContractLifecycleStage, allowed: ContractLifecycleStage[]): StageMove[] {
  const moves: StageMove[] = [];
  for (const to of allowed) {
    const label = MOVE_LABEL[stage]?.[to];
    if (!label) continue;
    moves.push({
      to,
      label,
      reasonRequired: stage === "approval" || stage === "signature",
      confirm: to === "closed",
    });
  }
  return moves;
}
