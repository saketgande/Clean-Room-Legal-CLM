import { describe, expect, it } from "vitest";
import {
  canStartWorkflow,
  editCaution,
  nextStepText,
  readinessItems,
  redlineGate,
  signingBlock,
  stageMoves,
  stepAction,
  workflowSteps,
  workflowView,
} from "./lifecycle-actions";
import type { ReviewStatusResponse, WorkflowRun, WorkflowRunStep } from "./types";

const step = (idx: number, type: WorkflowRunStep["type"], status: string, name = `Step ${idx}`): WorkflowRunStep => ({
  idx,
  type,
  name,
  status,
  assignee_user_id: null,
  assignee_label: null,
  team_id: null,
  team_label: null,
  note: null,
  result: null,
  updated_at: null,
});

const run = (steps: WorkflowRunStep[], over: Partial<WorkflowRun> = {}): WorkflowRun => ({
  id: "run-1",
  request_id: "req-1",
  flow_id: "flow-1",
  flow_name: "MSA",
  status: "waiting",
  current_index: 0,
  contract_id: "c-1",
  error: null,
  steps,
  comments: [],
  ...over,
});

describe("workflowView", () => {
  it("has nothing to show when no workflow ever ran", () => {
    expect(workflowView(null)).toEqual({ kind: "none" });
    expect(workflowView(undefined)).toEqual({ kind: "none" });
  });

  it("tells a finished workflow from a failed or cancelled one", () => {
    expect(workflowView(run([], { status: "complete" }))).toEqual({ kind: "ended", status: "complete", error: null });
    expect(workflowView(run([], { status: "failed", error: "boom" }))).toEqual({ kind: "ended", status: "failed", error: "boom" });
  });

  it("sees a step waiting on a person, and the step after it", () => {
    const v = workflowView(run([step(0, "human_task", "waiting_human", "Legal review"), step(1, "approval", "pending")]));
    expect(v).toMatchObject({ kind: "person", step: { name: "Legal review" }, next: { type: "approval" } });
  });

  it("finds the step still waiting when steps run side by side and the first is done", () => {
    const steps = [
      step(0, "human_task", "done", "Quality review"),
      { ...step(1, "human_task", "waiting_human", "Privacy review"), parallel: true },
      step(2, "approval", "pending", "Finance approval"),
    ];
    const v = workflowView(run(steps));
    expect(v).toMatchObject({ kind: "person", step: { name: "Privacy review" }, next: { type: "approval" } });
    expect(stepAction(v, "review")).toMatchObject({ stepIdx: 1, sendsForApproval: true });
  });

  it("does not say a step sends the contract for approval while another step beside it is still open", () => {
    const steps = [
      step(0, "human_task", "waiting_human", "Quality review"),
      { ...step(1, "human_task", "waiting_human", "Privacy review"), parallel: true },
      step(2, "approval", "pending", "Finance approval"),
    ];
    const v = workflowView(run(steps));
    expect(v).toMatchObject({ kind: "person", step: { name: "Quality review" }, next: { name: "Privacy review" } });
    expect(stepAction(v, "review")?.sendsForApproval).toBe(false);
  });

  it("sees approvals being decided", () => {
    expect(workflowView(run([step(0, "approval", "waiting_job")]))).toMatchObject({ kind: "approvals" });
  });

  it("calls anything else the workflow working", () => {
    expect(workflowView(run([step(0, "ai_task", "running")]))).toMatchObject({ kind: "working" });
    expect(workflowView(run([step(0, "ai_task", "done")], { current_index: 5 }))).toEqual({ kind: "working", step: null });
  });
});

describe("canStartWorkflow", () => {
  it("allows a start when there is none, or the last one failed or was cancelled", () => {
    expect(canStartWorkflow({ kind: "none" })).toBe(true);
    expect(canStartWorkflow({ kind: "ended", status: "failed", error: null })).toBe(true);
    expect(canStartWorkflow({ kind: "ended", status: "cancelled", error: null })).toBe(true);
  });
  it("does not start another while one runs, or after one completed", () => {
    expect(canStartWorkflow({ kind: "ended", status: "complete", error: null })).toBe(false);
    expect(canStartWorkflow({ kind: "working", step: null })).toBe(false);
  });
});

describe("stepAction", () => {
  const person = (nextType: WorkflowRunStep["type"] | null) =>
    workflowView(run([step(0, "human_task", "waiting_human", "Legal review"), ...(nextType ? [step(1, nextType, "pending", "Finance")] : [])]));

  it("offers 'send for approval' when the next step is an approval", () => {
    const a = stepAction(person("approval"), "review")!;
    expect(a.label).toBe('Complete "Legal review" and send for approval');
    expect(a).toMatchObject({ sendsForApproval: true, leavesReview: true, stepIdx: 0 });
  });

  it("says so when the next step is signing", () => {
    const a = stepAction(person("signature"), "review")!;
    expect(a.label).toBe('Complete "Legal review" and go to signing');
    expect(a).toMatchObject({ sendsForApproval: false, leavesReview: true });
  });

  it("is a plain completion when more review follows", () => {
    const a = stepAction(person("human_task"), "review")!;
    expect(a.label).toBe('Complete "Legal review"');
    expect(a.leavesReview).toBe(false);
  });

  it("does not treat a later stage as leaving Review", () => {
    expect(stepAction(person("signature"), "approval")!.leavesReview).toBe(false);
  });

  it("offers nothing unless a person is being waited on", () => {
    expect(stepAction({ kind: "none" }, "review")).toBeNull();
    expect(stepAction({ kind: "working", step: null }, "review")).toBeNull();
  });
});

describe("redlineGate", () => {
  const action = stepAction(workflowView(run([step(0, "human_task", "waiting_human"), step(1, "approval", "pending")])), "review");

  it("blocks leaving Review while suggestions are open", () => {
    expect(redlineGate(action, 3, false)).toEqual({ open: 3, canOverride: false });
    expect(redlineGate(action, 1, true)).toEqual({ open: 1, canOverride: true });
  });
  it("lets it through when none are open", () => {
    expect(redlineGate(action, 0, true)).toBeNull();
  });
  it("does not gate a step that stays in Review", () => {
    const stays = stepAction(workflowView(run([step(0, "human_task", "waiting_human"), step(1, "human_task", "pending")])), "review");
    expect(redlineGate(stays, 5, true)).toBeNull();
    expect(redlineGate(null, 5, true)).toBeNull();
  });
});

describe("stageMoves", () => {
  const labels = (stage: Parameters<typeof stageMoves>[0], allowed: Parameters<typeof stageMoves>[1]) => stageMoves(stage, allowed).map((m) => m.label);

  it("offers the moves the server allows within Intake, Drafting and Review", () => {
    expect(labels("intake", ["drafting", "review"])).toEqual(["Start drafting", "Move to Review"]);
    expect(labels("drafting", ["review"])).toEqual(["Move to Review"]);
    expect(labels("review", ["drafting", "approval", "signature"])).toEqual(["Back to Drafting"]);
  });

  it("never offers Approval, or skipping approval, as a button", () => {
    const to = stageMoves("review", ["drafting", "approval", "signature"]).map((m) => m.to);
    expect(to).not.toContain("approval");
    expect(to).not.toContain("signature");
  });

  it("offers a pull-back from Approval and Signature, with a reason", () => {
    expect(stageMoves("approval", ["signature", "review"])).toEqual([
      { to: "review", label: "Send back to Review", reasonRequired: true, confirm: false },
    ]);
    expect(stageMoves("signature", ["active", "review"])[0]).toMatchObject({ to: "review", reasonRequired: true });
  });

  it("leaves activation to signing, and asks before closing", () => {
    expect(stageMoves("signature", ["active", "review"]).map((m) => m.to)).toEqual(["review"]);
    expect(stageMoves("active", ["closed"])).toEqual([
      { to: "closed", label: "Close contract", reasonRequired: false, confirm: true },
    ]);
  });

  it("offers nothing from a closed contract or an unknown stage", () => {
    expect(stageMoves("closed", [])).toEqual([]);
    expect(stageMoves("intake", ["closed"])).toEqual([]);
  });
});

// ---- the words ---------------------------------------------------------------------

const person = (over: Partial<WorkflowRunStep> = {}) => ({ ...step(0, "human_task", "waiting_human", "Legal review"), ...over });
const toApproval = run([person(), step(1, "approval", "pending", "Finance")]);

describe("nextStepText", () => {
  const text = (r: WorkflowRun | null, stage: Parameters<typeof stepAction>[1] = "review", open = 0) => {
    const view = workflowView(r);
    const action = stepAction(view, stage);
    return nextStepText(view, action, redlineGate(action, open, false));
  };

  it("tells a contract with no workflow what a workflow is", () => {
    const t = text(null);
    expect(t.title).toBe("Start a workflow");
    expect(t.body).toContain("list of steps");
  });

  it("says a finished workflow needs nothing", () => {
    expect(text(run([], { status: "complete" })).title).toBe("The workflow is finished");
  });

  it("says why a workflow stopped, without doubling the full stop", () => {
    expect(text(run([], { status: "failed", error: "No approver found." })).body).toBe("It stopped with an error: No approver found. Start a new one to carry on.");
    expect(text(run([], { status: "cancelled" })).body).toBe("It was cancelled. Start a new one to carry on.");
  });

  it("names the step the workflow is working on", () => {
    expect(text(run([step(0, "ai_task", "running", "AI review")])).body).toContain("“AI review”");
    expect(text(run([]), "review").title).toBe("The workflow is working");
  });

  it("calls a signature step 'Waiting for signatures' and says where signing is sent from", () => {
    const t = text(run([step(0, "signature", "waiting_job", "Signature")]));
    expect(t.title).toBe("Waiting for signatures");
    expect(t.body).toContain("sent for signing from the Signatures page");
    expect(t.title).not.toBe("The workflow is working");
  });

  describe("a signature step whose version was not approved (round 2, bug 17)", () => {
    const signing = run([step(0, "signature", "waiting_job", "Signature")]);
    const withApproval = (approval: Parameters<typeof nextStepText>[3]) => {
      const view = workflowView(signing);
      return nextStepText(view, stepAction(view, "signature"), null, approval);
    };

    it("still says 'Waiting for signatures' when the version is approved, or never needed approval", () => {
      expect(withApproval("approved").title).toBe("Waiting for signatures");
      expect(withApproval("none").title).toBe("Waiting for signatures");
      expect(withApproval(undefined).title).toBe("Waiting for signatures");
    });

    it("says approval does not cover the version after an edit, and what to do", () => {
      const t = withApproval("changed");
      expect(t.title).toBe("Approval doesn't cover this version");
      expect(t.body).toContain("cannot be sent for signature");
      expect(t.body).toContain("Review");
    });

    it("says it is waiting for approval, or that approval was rejected", () => {
      expect(withApproval("pending").title).toBe("Waiting for approval");
      expect(withApproval("rejected").title).toBe("Approval was rejected");
    });

    it("leaves other steps alone: the approval state only matters at the signature step", () => {
      const view = workflowView(run([step(0, "ai_task", "running", "AI review")]));
      expect(nextStepText(view, stepAction(view, "review"), null, "changed").title).toBe("The workflow is working");
    });
  });

  it("waits for approvers, with the authority note when there is one", () => {
    const r = run([{ ...step(0, "approval", "waiting_job", "Finance"), authority_note: "Needs a finance approver." }]);
    const t = text(r, "approval");
    expect(t.title).toBe("Waiting for the approvers");
    expect(t.body).toContain("Needs a finance approver.");
  });

  it("offers to send for approval, and says what that does", () => {
    const t = text(toApproval);
    expect(t.title).toBe("Send it for approval");
    expect(t.body).toContain("creates the approval chain");
  });

  describe("an AI step that waits for a person", () => {
    const aiStep = (result: Record<string, unknown> | null, note: string | null = null) => ({ ...step(0, "ai_task", "waiting_human", "AI review"), result, note });

    it("says the AI has finished and that the step waits for a person to confirm it", () => {
      const t = text(run([aiStep({ source: "playbook_review", risk_band: "medium", summary: "0 high, 2 medium, 1 low unresolved finding(s)" })]));
      expect(t.body).toContain("The AI has finished its part: 0 high, 2 medium, 1 low unresolved finding(s).");
      expect(t.body).toContain("The step stays open until you confirm it.");
    });

    it("warns that high-risk findings are still open before the step is finished", () => {
      const high = text(run([aiStep({ source: "playbook_review", risk_band: "high", summary: "3 high, 1 medium, 0 low unresolved finding(s)" })]));
      expect(high.body).toContain("High-risk findings are still open: settle them in the Review tab before you finish this step.");
      const medium = text(run([aiStep({ source: "playbook_review", risk_band: "medium", summary: "0 high, 2 medium, 1 low unresolved finding(s)" })]));
      expect(medium.body).not.toContain("High-risk");
    });

    it("says the AI has finished even when it left no summary", () => {
      expect(text(run([aiStep(null)])).body).toContain("The AI has finished its part. The step stays open until you confirm it.");
    });

    it("does not call a keyword check a review", () => {
      const t = text(run([aiStep({ source: "regex", confidence: 0.94 })]));
      expect(t.body).toContain("No AI analysis ran here, only a keyword check of the request text.");
      expect(t.body).not.toContain("The AI has finished");
    });

    it("says nothing extra about a step that is not an AI step", () => {
      expect(text(run([{ ...step(0, "human_task", "waiting_human", "Legal review"), result: { source: "regex" } }])).body).not.toMatch(/AI|keyword/);
    });
  });

  it("says who the step is assigned to", () => {
    expect(text(run([person({ assignee_label: "Priya N" }), step(1, "approval", "pending")])).body).toContain("assigned to Priya N");
    expect(text(run([person({ team_label: "Legal" }), step(1, "approval", "pending")])).body).toContain("assigned to Legal");
  });

  it("offers to go on to signing when the next step is a signature", () => {
    const t = text(run([person(), step(1, "signature", "pending")]));
    expect(t.title).toBe("Send it on to signing");
    expect(t.body).toContain("moves the contract to Signature");
  });

  it("names a step that keeps the contract in Review", () => {
    const t = text(run([person(), step(1, "human_task", "pending")]));
    expect(t.title).toBe("Finish “Legal review”");
  });

  it("puts the open suggestions first, in the singular and the plural", () => {
    expect(text(toApproval, "review", 1)).toEqual({
      title: "Decide 1 suggested change first",
      body: expect.stringContaining("It is still open. Accept or reject it in the Review tab"),
    });
    expect(text(toApproval, "review", 3).title).toBe("Decide 3 suggested changes first");
    expect(text(toApproval, "review", 3).body).toContain("They are still open");
  });
});

describe("workflowSteps", () => {
  it("is empty without a workflow", () => {
    expect(workflowSteps(null)).toEqual([]);
    expect(workflowSteps(undefined)).toEqual([]);
  });

  it("puts the steps in order and says where each one is", () => {
    const rows = workflowSteps(
      run([
        step(2, "approval", "pending", "Finance"),
        step(0, "ai_task", "done", "Playbook review"),
        person({ idx: 1, assignee_label: "Priya N" }),
      ]),
    );
    expect(rows.map((r) => [r.name, r.tone, r.line])).toEqual([
      ["Playbook review", "done", "AI task · Done"],
      ["Legal review", "now", "Person · Waiting on Priya N"],
      ["Finance", "todo", "Approval · Not started"],
    ]);
  });

  it("covers every status the server uses", () => {
    const line = (type: WorkflowRunStep["type"], status: string) => workflowSteps(run([step(0, type, status)]))[0];
    expect(line("human_task", "waiting_human").line).toBe("Person · Waiting for a person");
    expect(line("approval", "waiting_job").line).toBe("Approval · Waiting for the approvers");
    expect(line("signature", "waiting_job").line).toBe("Signature · Waiting for signatures");
    expect(line("counterparty", "waiting_job").line).toBe("Other side · In progress");
    expect(line("ai_task", "running")).toMatchObject({ tone: "now", line: "AI task · Running" });
    expect(line("notify", "skipped")).toMatchObject({ tone: "skip", line: "Notification · Skipped" });
    expect(line("clm_draft", "failed")).toMatchObject({ tone: "bad", line: "Drafting · Failed" });
  });

  it("shows why a step failed, and only then", () => {
    expect(workflowSteps(run([{ ...step(0, "approval", "failed"), note: "Approval rejected." }]))[0].note).toBe("Approval rejected.");
    expect(workflowSteps(run([{ ...step(0, "approval", "done"), note: "ignored" }]))[0].note).toBeNull();
  });

  it("names the team a step is planned for before it starts", () => {
    expect(workflowSteps(run([{ ...step(0, "approval", "pending"), planned_team_label: "Finance" }]))[0].line).toBe("Approval · Not started · Finance");
  });
});

// The checklist as the server builds it (contracts/service.py, compute_review_status).
const status = (over: Partial<ReviewStatusResponse> = {}, items: Partial<Record<string, [string, number, string | null]>> = {}): ReviewStatusResponse => {
  const base: Record<string, [string, number, string | null]> = {
    ai_review: ["done", 0, null],
    issues: ["todo", 7, "2 high/critical · advisory"],
    redlines: ["todo", 6, null],
    comments: ["done", 0, null],
    counterparty: ["todo", 0, "Optional — send for negotiation"],
    approval: ["blocked", 0, "Decide open redlines first"],
    ...items,
  };
  return {
    contract_id: "c-1", lifecycle_stage: "review", ai_reviewed: true, open_issues: 7, high_severity_issues: 2, pending_redlines: 6,
    open_comments: 0, counterparty_active: false, ready_for_approval: false, next_step: "", next_action: null,
    checklist: Object.entries(base).map(([key, [st, count, detail]]) => ({ key, label: key, status: st as never, count, detail })),
    ...over,
  };
};

describe("readinessItems", () => {
  it("says each open item as a sentence and tags what it is", () => {
    expect(readinessItems(status()).map((i) => [i.key, i.text, i.need, i.state])).toEqual([
      ["ai_review", "The AI has reviewed this contract", "advisory", "done"],
      ["issues", "7 issues from the review are still open", "advisory", "open"],
      ["redlines", "6 suggested changes still to accept or reject", "required", "open"],
      ["comments", "No open comments", "advisory", "done"],
      ["counterparty", "Not shared with the other side yet", "optional", "open"],
    ]);
  });

  it("does not tick 'no open issues' when the AI has not reviewed the contract", () => {
    const items = readinessItems(status({}, { ai_review: ["todo", 0, null], issues: ["done", 0, null] }));
    const issues = items.find((i) => i.key === "issues");
    expect(issues).toMatchObject({ state: "open", text: "Open issues are not known yet: the AI has not reviewed this contract" });
    expect(items.find((i) => i.key === "ai_review")).toMatchObject({ state: "open" });
  });

  it("still ticks 'no open issues' once the AI has reviewed it and found none", () => {
    const issues = readinessItems(status({}, { ai_review: ["done", 0, null], issues: ["done", 0, null] })).find((i) => i.key === "issues");
    expect(issues).toMatchObject({ state: "done", text: "No open issues from the review" });
  });

  it("still lists issues that are open even if the AI review did not finish", () => {
    const issues = readinessItems(status({}, { ai_review: ["todo", 0, null], issues: ["todo", 3, null] })).find((i) => i.key === "issues");
    expect(issues?.text).toBe("3 issues from the review are still open");
  });

  it("lists blanks left in the text as advice, and says what they stop", () => {
    const open = readinessItems(status({}, { blanks: ["todo", 2, "[INSERT CORRECT EXPIRY DATE], [●]"] })).find((i) => i.key === "blanks");
    expect(open).toMatchObject({ state: "open", need: "advisory", text: "2 blanks still to fill in the contract text" });
    expect(open?.note).toBe("[INSERT CORRECT EXPIRY DATE], [●]. They can't be sent for signature until they are filled in.");
    const one = readinessItems(status({}, { blanks: ["todo", 1, "[TBD]"] })).find((i) => i.key === "blanks");
    expect(one?.text).toBe("1 blank still to fill in the contract text");
    expect(one?.note).toBe("[TBD]. It can't be sent for signature until it is filled in.");
    const clean = readinessItems(status({}, { blanks: ["done", 0, null] })).find((i) => i.key === "blanks");
    expect(clean).toMatchObject({ state: "done", text: "No blanks left to fill in", note: null });
  });

  it("leaves out the server's approval row: the next-step card already says it", () => {
    expect(readinessItems(status()).some((i) => i.key === "approval")).toBe(false);
  });

  it("only marks the redlines Required, because they are the only thing the server enforces", () => {
    const required = readinessItems(status()).filter((i) => i.need === "required").map((i) => i.key);
    expect(required).toEqual(["redlines"]);
  });

  it("uses the singular for one", () => {
    const t = readinessItems(status({}, { issues: ["todo", 1, null], redlines: ["todo", 1, null], comments: ["todo", 1, null] })).map((i) => i.text);
    expect(t).toContain("1 issue from the review is still open");
    expect(t).toContain("1 suggested change still to accept or reject");
    expect(t).toContain("1 comment still to resolve");
  });

  it("says how many open issues are high or critical, from the number, not the server's wording", () => {
    expect(readinessItems(status()).find((i) => i.key === "issues")?.note).toBe("2 high or critical");
    expect(readinessItems(status({ high_severity_issues: 0 })).find((i) => i.key === "issues")?.note).toBeNull();
  });

  it("asks for an AI review when there is none, and keeps the server's follow-up once there is", () => {
    const none = readinessItems(status({}, { ai_review: ["todo", 0, "Analyzes risks & clauses in one click"] })).find((i) => i.key === "ai_review");
    expect(none).toMatchObject({ state: "open", text: "Run an AI review to check the draft for risks and missing clauses", note: null });
    const done = readinessItems(status({}, { ai_review: ["done", 0, "Analysis done — run a playbook for a deeper standards check"] })).find((i) => i.key === "ai_review");
    expect(done?.note).toBe("Analysis done — run a playbook for a deeper standards check");
  });

  it("shows a counterparty round that is under way", () => {
    const c = readinessItems(status({}, { counterparty: ["in_progress", 0, "Shared — awaiting response"] })).find((i) => i.key === "counterparty");
    expect(c).toMatchObject({ state: "going", text: "Shared with the other side. Waiting for their reply" });
  });

  it("shows an item it does not know by the server's own label, with no tag", () => {
    const r = status();
    r.checklist.push({ key: "legal_hold", label: "Legal hold check", status: "todo", count: 0, detail: "Ask legal" });
    expect(readinessItems(r).at(-1)).toEqual({ key: "legal_hold", text: "Legal hold check", note: "Ask legal", state: "open", need: null });
  });
});

describe("signingBlock", () => {
  it("blocks signing for every version that is not approved, and only those", () => {
    expect(signingBlock("changed")).not.toBeNull();
    expect(signingBlock("pending")).not.toBeNull();
    expect(signingBlock("rejected")).not.toBeNull();
    expect(signingBlock("approved")).toBeNull();
    expect(signingBlock("none")).toBeNull();
    expect(signingBlock(undefined)).toBeNull();
    expect(signingBlock(null)).toBeNull();
  });
});

describe("editCaution (round 2, bug 17)", () => {
  it("says nothing while the contract is still being drafted or reviewed", () => {
    expect(editCaution("intake", "none")).toBeNull();
    expect(editCaution("drafting", "none")).toBeNull();
    expect(editCaution("review", "none")).toBeNull();
    expect(editCaution(undefined, undefined)).toBeNull();
  });

  it("warns that the approvers are deciding on text that will no longer be the current one", () => {
    const c = editCaution("approval", "pending");
    expect(c?.notice).toContain("with the approvers");
    expect(c?.notice).toContain("new version");
    expect(c?.saved).toContain("not been sent for approval");
  });

  it("warns at the signature stage that the new version has to be approved again", () => {
    const c = editCaution("signature", "approved");
    expect(c?.notice).toContain("nobody has approved");
    expect(c?.notice).toContain("already sent for signing keeps the old text");
    expect(c?.saved).toBe("Saved as a new version. It has not been approved.");
  });

  it("does not claim approval is lost when the contract never went through approval", () => {
    const c = editCaution("active", "none");
    expect(c?.notice).toContain("the active stage");
    expect(c?.notice).not.toContain("nobody has approved");
    expect(c?.saved).toBe("Saved as a new version.");
  });
});
