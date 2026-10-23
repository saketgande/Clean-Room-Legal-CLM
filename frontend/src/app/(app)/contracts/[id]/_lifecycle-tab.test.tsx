import { describe, expect, it, vi, beforeEach } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { LifecyclePanel, LifecycleTab, type LifecyclePanelProps } from "./_lifecycle-tab";
import type { ContractStageHistoryResponse, ReviewStatusResponse, WorkflowRun, WorkflowRunStep } from "@/lib/types";

// ---- fixtures ---------------------------------------------------------------

const step = (idx: number, type: WorkflowRunStep["type"], status: string, name = `Step ${idx}`): WorkflowRunStep => ({
  idx, type, name, status,
  assignee_user_id: null, assignee_label: null, team_id: null, team_label: null,
  note: null, result: null, updated_at: null,
});

const run = (steps: WorkflowRunStep[], over: Partial<WorkflowRun> = {}): WorkflowRun => ({
  id: "run-1", request_id: "req-1", flow_id: "flow-1", flow_name: "MSA flow",
  status: "waiting", current_index: 0, contract_id: "c-1", error: null, steps, comments: [], ...over,
});

const reviewRun = run([step(0, "human_task", "waiting_human", "Legal review"), step(1, "approval", "pending", "Finance")]);

const review = (over: Partial<ReviewStatusResponse> = {}): ReviewStatusResponse => ({
  contract_id: "c-1", lifecycle_stage: "review", ai_reviewed: true, open_issues: 3, high_severity_issues: 1,
  pending_redlines: 0, open_comments: 0, counterparty_active: false, ready_for_approval: true,
  next_step: "Ready to send for approval.", next_action: "submit_approval",
  checklist: [
    { key: "ai_review", label: "AI review", status: "done", count: 0, detail: null },
    { key: "issues", label: "Review flagged issues", status: "todo", count: 3, detail: "1 high/critical · advisory" },
    { key: "redlines", label: "Resolve redlines", status: "todo", count: 2, detail: null },
    { key: "comments", label: "Resolve comments", status: "done", count: 0, detail: null },
    { key: "counterparty", label: "Counterparty round", status: "todo", count: 0, detail: "Optional — send for negotiation" },
    { key: "approval", label: "Submit for approval", status: "blocked", count: 0, detail: "Decide open redlines first" },
  ],
  ...over,
});

const history: ContractStageHistoryResponse[] = [
  { id: "h1", contract_id: "c-1", from_stage: null, to_stage: "intake", reason: null, changed_by_user_id: null, changed_at: new Date().toISOString(), override_used: false },
  { id: "h2", contract_id: "c-1", from_stage: "review", to_stage: "approval", reason: "Deadline today", changed_by_user_id: "u-1", changed_at: new Date().toISOString(), override_used: true },
];

function panel(over: Partial<LifecyclePanelProps> = {}) {
  const props: LifecyclePanelProps = {
    stage: "review",
    options: { current_stage: "review", days_in_stage: 2, stage_sla_days: 5, sla_breached: false, allowed_transitions: ["drafting", "approval", "signature"] },
    review: review(),
    history,
    run: reviewRun,
    canOverride: false,
    busy: false,
    onCompleteStep: vi.fn(),
    onStartWorkflow: vi.fn(),
    onMove: vi.fn(),
    onOpenReview: vi.fn(),
    ...over,
  };
  render(<LifecyclePanel {...props} />);
  return props;
}

const section = (name: string) => within(screen.getByRole("region", { name }));

// ---- the view -----------------------------------------------------------------

describe("LifecyclePanel", () => {
  it("does not repeat the stage: the stage bar at the top of the page shows it", () => {
    panel();
    expect(document.querySelector(".lcx-stage")).toBeNull();
    expect(screen.queryByRole("list", { name: "Contract stages" })).toBeNull();
    expect(screen.queryByText(/Day 2 in this stage/)).toBeNull();
  });

  it("leads with what to do next, then what is in the way, then the workflow", () => {
    panel();
    const names = screen.getAllByRole("region").map((r) => r.getAttribute("aria-label"));
    expect(names).toEqual(["What to do next", "Before it goes for approval", "The workflow", "Change the stage by hand", "Stage history"]);
  });

  it("has exactly one main button", () => {
    panel();
    const main = screen.getAllByRole("button").filter((b) => b.className.includes("pri"));
    expect(main.map((b) => b.textContent)).toEqual(['Complete "Legal review" and send for approval']);
  });

  describe("a contract out for signature", () => {
    it("says it is waiting for signatures and offers the way to the Signatures page", () => {
      panel({ stage: "signature", run: run([step(0, "signature", "waiting_job", "Signature")]), options: { current_stage: "signature", allowed_transitions: ["active", "review"] } });
      const next = section("What to do next");
      expect(next.getByRole("heading", { name: "Waiting for signatures" })).toBeInTheDocument();
      expect(next.getByRole("link", { name: "Open Signatures" })).toHaveAttribute("href", "/signatures");
    });

    // Round 2, bug 17: approval belongs to a version. A contract approved and then edited sits at the signature
    // stage with a version nobody approved; the server refuses to send it, so the page must not point at the
    // Signatures page as if it could.
    const signing = (approval: ReviewStatusResponse["version_approval"]) =>
      panel({
        stage: "signature",
        run: run([step(0, "signature", "waiting_job", "Signature")]),
        options: { current_stage: "signature", allowed_transitions: ["active", "review"] },
        review: review({ lifecycle_stage: "signature", version_approval: approval }),
      });

    it("says approval does not cover a version that was edited after approval, and hides the way to Signatures", () => {
      signing("changed");
      const next = section("What to do next");
      expect(next.getByRole("heading", { name: "Approval doesn't cover this version" })).toBeInTheDocument();
      expect(next.queryByRole("heading", { name: "Waiting for signatures" })).toBeNull();
      expect(next.queryByRole("link", { name: "Open Signatures" })).toBeNull();
    });

    it("says it is waiting for approval, or rejected, and offers no link to Signatures either", () => {
      signing("pending");
      expect(section("What to do next").getByRole("heading", { name: "Waiting for approval" })).toBeInTheDocument();
      expect(section("What to do next").queryByRole("link", { name: "Open Signatures" })).toBeNull();
    });

    it("keeps the way to Signatures when the version on screen is the approved one", () => {
      signing("approved");
      const next = section("What to do next");
      expect(next.getByRole("heading", { name: "Waiting for signatures" })).toBeInTheDocument();
      expect(next.getByRole("link", { name: "Open Signatures" })).toBeInTheDocument();
    });
  });

  describe("notes written on the request's workflow", () => {
    const comments = [
      { idx: 0, kind: "comment", text: "Please check clause 9", actor_id: "u-1", actor_name: "Erin Legal", at: "2026-10-08T09:10:00Z" },
      { idx: 0, kind: "return", text: "redo the draft", actor_id: "u-1", actor_name: "Sam Counsel", at: "2026-10-08T09:20:00Z" },
    ];

    it("shows them, newest first, with who wrote them, and says they stay inside the team", () => {
      panel({ run: run(reviewRun.steps, { comments }) });
      const notes = section("Notes on the workflow");
      const items = notes.getAllByRole("listitem");
      expect(items).toHaveLength(2);
      expect(items[0]).toHaveTextContent("Sam Counsel sent it back");
      expect(items[0]).toHaveTextContent("redo the draft");
      expect(items[1]).toHaveTextContent("Erin Legal");
      expect(notes.getByText(/stay inside your team/)).toBeInTheDocument();
    });

    it("shows nothing when there are no notes", () => {
      panel({ run: run(reviewRun.steps, { comments: [] }) });
      expect(screen.queryByRole("region", { name: "Notes on the workflow" })).not.toBeInTheDocument();
    });
  });

  describe("what to do next", () => {
    it("offers to start a workflow when there is none, and says what one is", () => {
      const p = panel({ run: null });
      expect(screen.getByRole("heading", { name: "Start a workflow" })).toBeTruthy();
      expect(screen.getByText(/list of steps it goes through/)).toBeTruthy();
      fireEvent.click(section("What to do next").getByRole("button", { name: "Start workflow" }));
      expect(p.onStartWorkflow).toHaveBeenCalledTimes(1);
    });

    it("offers a new workflow after a failed one, and shows why it failed", () => {
      const p = panel({ run: run([], { status: "failed", error: "No approver found" }) });
      expect(screen.getByText(/stopped with an error: No approver found\. Start a new one/)).toBeTruthy();
      fireEvent.click(screen.getByRole("button", { name: "Start a new workflow" }));
      expect(p.onStartWorkflow).toHaveBeenCalled();
    });

    it("does not offer another workflow once one is complete", () => {
      panel({ run: run([], { status: "complete" }) });
      expect(screen.getByRole("heading", { name: "The workflow is finished" })).toBeTruthy();
      expect(screen.queryByRole("button", { name: /workflow/i })).toBeNull();
    });

    it("sends for approval by completing the step, passing no override", () => {
      const p = panel({ review: review({ pending_redlines: 0 }) });
      fireEvent.click(screen.getByRole("button", { name: 'Complete "Legal review" and send for approval' }));
      expect(p.onCompleteStep).toHaveBeenCalledWith(expect.objectContaining({ stepIdx: 0, sendsForApproval: true }));
      expect((p.onCompleteStep as ReturnType<typeof vi.fn>).mock.calls[0]).toHaveLength(1);
    });

    it("explains that an approval chain is created", () => {
      panel();
      expect(screen.getByText(/creates the approval chain/i)).toBeTruthy();
    });

    it("shows who the step is waiting on", () => {
      const r = run([{ ...step(0, "human_task", "waiting_human", "Legal review"), assignee_label: "Priya N" }, step(1, "approval", "pending")]);
      panel({ run: r });
      expect(screen.getByText(/assigned to Priya N/)).toBeTruthy();
    });

    it("only waits while approvals are being decided", () => {
      panel({ stage: "approval", run: run([step(0, "approval", "waiting_job", "Finance")]), options: { current_stage: "approval", allowed_transitions: ["review", "signature"] } });
      expect(screen.getByRole("heading", { name: "Waiting for the approvers" })).toBeTruthy();
      expect(screen.queryByRole("button", { name: /Complete/ })).toBeNull();
    });

    it("says the workflow is working when it is", () => {
      panel({ run: run([step(0, "ai_task", "running", "AI review")]) });
      expect(screen.getByText(/The workflow is working/)).toBeTruthy();
    });
  });

  describe("open suggestions", () => {
    it("blocks sending for approval and points to Review", () => {
      const p = panel({ review: review({ pending_redlines: 2 }) });
      expect(screen.getByRole("heading", { name: "Decide 2 suggested changes first" })).toBeTruthy();
      expect(screen.queryByRole("button", { name: /send for approval/i })).toBeNull();
      expect(screen.queryByRole("button", { name: /Send anyway/ })).toBeNull();
      fireEvent.click(screen.getByRole("button", { name: "Go to Review" }));
      expect(p.onOpenReview).toHaveBeenCalled();
    });

    it("uses singular wording for one open change", () => {
      panel({ review: review({ pending_redlines: 1 }) });
      expect(screen.getByRole("heading", { name: "Decide 1 suggested change first" })).toBeTruthy();
    });

    it("lets a person with the permission send anyway, but only with a reason", () => {
      const p = panel({ review: review({ pending_redlines: 2 }), canOverride: true });
      fireEvent.click(screen.getByRole("button", { name: "Send anyway…" }));
      const send = screen.getByRole("button", { name: 'Complete "Legal review" and send for approval' }) as HTMLButtonElement;
      expect(send.disabled).toBe(true);
      fireEvent.change(screen.getByLabelText(/Why are you sending it/), { target: { value: "  Client deadline  " } });
      expect(send.disabled).toBe(false);
      fireEvent.click(send);
      expect(p.onCompleteStep).toHaveBeenCalledWith(expect.objectContaining({ stepIdx: 0 }), "Client deadline");
    });

    it("can back out of the override", () => {
      panel({ review: review({ pending_redlines: 2 }), canOverride: true });
      fireEvent.click(screen.getByRole("button", { name: "Send anyway…" }));
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
      expect(screen.queryByLabelText(/Why are you sending it/)).toBeNull();
      expect(screen.getByRole("button", { name: "Send anyway…" })).toBeTruthy();
    });

    it("does not block a step that keeps the contract in Review", () => {
      const stays = run([step(0, "human_task", "waiting_human", "Legal review"), step(1, "human_task", "pending", "Privacy review")]);
      const p = panel({ run: stays, review: review({ pending_redlines: 4 }) });
      expect(screen.queryByRole("heading", { name: /Decide/ })).toBeNull();
      fireEvent.click(screen.getByRole("button", { name: 'Complete "Legal review"' }));
      expect(p.onCompleteStep).toHaveBeenCalled();
    });
  });

  describe("changing the stage by hand", () => {
    it("says when to use it", () => {
      panel();
      expect(section("Change the stage by hand").getByText(/workflow normally moves the contract for you/)).toBeTruthy();
    });

    it("never styles a move as the main button", () => {
      panel({ stage: "intake", options: { current_stage: "intake", allowed_transitions: ["drafting", "review"] } });
      const buttons = section("Change the stage by hand").getAllByRole("button");
      expect(buttons.map((b) => b.textContent)).toEqual(["Start drafting", "Move to Review"]);
      expect(buttons.some((b) => b.className.includes("pri"))).toBe(false);
    });

    it("warns that going back stops the approval in progress", () => {
      panel({ stage: "approval", run: null, options: { current_stage: "approval", allowed_transitions: ["review", "signature"] } });
      fireEvent.click(screen.getByRole("button", { name: "Send back to Review" }));
      expect(screen.getByText("Going back stops any approval that is in progress.")).toBeTruthy();
    });

    it("warns that going back stops the signing in progress", () => {
      panel({ stage: "signature", run: null, options: { current_stage: "signature", allowed_transitions: ["active", "review"] } });
      fireEvent.click(screen.getByRole("button", { name: "Pull back to Review" }));
      expect(screen.getByText("Going back stops any signing that is in progress.")).toBeTruthy();
    });

    it("offers no button for Approval or for skipping it", () => {
      panel();
      const names = section("Change the stage by hand").getAllByRole("button").map((b) => b.textContent);
      expect(names).toEqual(["Back to Drafting"]);
    });

    it("moves between early stages in one click", () => {
      const p = panel({ stage: "drafting", options: { current_stage: "drafting", allowed_transitions: ["review"] } });
      fireEvent.click(screen.getByRole("button", { name: "Move to Review" }));
      expect(p.onMove).toHaveBeenCalledWith(expect.objectContaining({ to: "review" }));
    });

    it("needs a reason to pull a contract back from Approval", () => {
      const p = panel({ stage: "approval", run: null, options: { current_stage: "approval", allowed_transitions: ["review", "signature"] } });
      fireEvent.click(screen.getByRole("button", { name: "Send back to Review" }));
      expect(p.onMove).not.toHaveBeenCalled();
      const confirm = screen.getAllByRole("button", { name: "Send back to Review" }).at(-1) as HTMLButtonElement;
      expect(confirm.disabled).toBe(true);
      fireEvent.change(screen.getByLabelText(/Why are you moving it back to Review/), { target: { value: "Terms changed" } });
      fireEvent.click(confirm);
      expect(p.onMove).toHaveBeenCalledWith(expect.objectContaining({ to: "review" }), "Terms changed");
    });

    it("asks before closing", () => {
      const p = panel({ stage: "active", run: null, options: { current_stage: "active", allowed_transitions: ["closed"] } });
      fireEvent.click(screen.getByRole("button", { name: "Close contract" }));
      expect(p.onMove).not.toHaveBeenCalled();
      fireEvent.click(screen.getByRole("button", { name: "Yes, close it" }));
      expect(p.onMove).toHaveBeenCalledWith(expect.objectContaining({ to: "closed" }), undefined);
    });

    it("can cancel a move that asked for a reason", () => {
      const p = panel({ stage: "signature", run: null, options: { current_stage: "signature", allowed_transitions: ["active", "review"] } });
      fireEvent.click(screen.getByRole("button", { name: "Pull back to Review" }));
      fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
      expect(screen.queryByLabelText(/Why are you moving it back/)).toBeNull();
      expect(p.onMove).not.toHaveBeenCalled();
    });

    it("is hidden when no move is available", () => {
      panel({ stage: "closed", run: null, options: { current_stage: "closed", allowed_transitions: [] } });
      expect(screen.queryByRole("region", { name: "Change the stage by hand" })).toBeNull();
    });

    it("disables the buttons while a request is running", () => {
      panel({ stage: "drafting", run: null, busy: true, options: { current_stage: "drafting", allowed_transitions: ["review"] } });
      expect((screen.getByRole("button", { name: "Move to Review" }) as HTMLButtonElement).disabled).toBe(true);
      expect((screen.getByRole("button", { name: "Start workflow" }) as HTMLButtonElement).disabled).toBe(true);
    });
  });

  describe("before it goes for approval", () => {
    it("says each item as a sentence, tagged Required, Advisory or Optional when it is still open", () => {
      panel();
      const items = section("Before it goes for approval").getAllByRole("listitem").map((li) => li.textContent);
      expect(items).toEqual([
        "The AI has reviewed this contract",
        "3 issues from the review are still open1 high or criticaladvisory",
        "2 suggested changes still to accept or rejectrequired",
        "No open comments",
        "Not shared with the other side yetoptional",
      ]);
    });

    describe("the comments line", () => {
      const withOpenComments = () =>
        review({ open_comments: 2, checklist: review().checklist.map((c) => (c.key === "comments" ? { ...c, status: "todo", count: 2 } : c)) });

      it("has a button that opens the Comments tab while comments are still open", () => {
        const { onOpenComments } = panel({ review: withOpenComments(), onOpenComments: vi.fn() });
        const line = section("Before it goes for approval").getByText("2 comments still to resolve").closest("li") as HTMLElement;
        fireEvent.click(within(line).getByRole("button", { name: "Open comments" }));
        expect(onOpenComments).toHaveBeenCalledTimes(1);
      });

      it("has no such button once every comment is resolved", () => {
        panel({ onOpenComments: vi.fn() });
        expect(screen.queryByRole("button", { name: "Open comments" })).toBeNull();
      });

      it("has no such button where there is no Comments tab to open", () => {
        panel({ review: withOpenComments() });
        expect(screen.queryByRole("button", { name: "Open comments" })).toBeNull();
      });
    });

    it("explains the tags", () => {
      panel();
      expect(section("Before it goes for approval").getByText(/Required items must be done first/)).toBeTruthy();
    });

    it("does not repeat the server's Submit for approval row", () => {
      panel();
      expect(section("Before it goes for approval").queryByText(/Submit for approval/)).toBeNull();
    });

    it("is hidden once the contract is past Review", () => {
      panel({ stage: "approval", run: null, options: { current_stage: "approval", allowed_transitions: ["review"] } });
      expect(screen.queryByRole("region", { name: "Before it goes for approval" })).toBeNull();
    });

    it("waits for the server's answer", () => {
      panel({ review: undefined });
      expect(screen.queryByRole("region", { name: "Before it goes for approval" })).toBeNull();
    });
  });

  describe("the workflow", () => {
    it("lists the steps and where each one is, and says how it relates to the stage", () => {
      panel();
      const w = section("The workflow");
      expect(w.getByText("MSA flow")).toBeTruthy();
      expect(w.getByText(/stage bar at the top shows where the contract is/)).toBeTruthy();
      const rows = w.getAllByRole("listitem");
      expect(rows.map((r) => r.textContent)).toEqual(["Legal reviewPerson · Waiting for a person", "FinanceApproval · Not started"]);
      expect(rows[0].getAttribute("aria-current")).toBe("step");
      expect(rows[1].getAttribute("aria-current")).toBeNull();
    });

    it("is not shown when there is no workflow", () => {
      panel({ run: null });
      expect(screen.queryByRole("region", { name: "The workflow" })).toBeNull();
    });

    it("shows a failed step with the reason", () => {
      panel({ run: run([{ ...step(0, "approval", "failed", "Finance"), note: "Approval rejected." }], { status: "failed", error: "Approval rejected." }) });
      expect(section("The workflow").getByText("Approval rejected.")).toBeTruthy();
    });
  });

  it("shows the history newest first, marking an override and its reason", () => {
    panel();
    const items = section("Stage history").getAllByRole("listitem");
    expect(items[0].textContent).toContain("Review → Approval");
    // Plain words and no alarm: the system makes such moves too (a send-back after a rejection, the
    // approval finishing), so a red "OVERRIDE" read as if someone had broken a rule.
    expect(items[0].textContent).toContain("outside the usual order");
    expect(items[0].textContent).not.toMatch(/override/i);
    expect(within(items[0]).getByText("outside the usual order")).toHaveAttribute("title", expect.stringContaining("skipped a normal stage rule"));
    expect(items[0].textContent).toContain("Deadline today");
    expect(items[1].textContent).toContain("Started in Intake");
  });

  it("says so when nothing has changed stage yet", () => {
    panel({ history: [] });
    expect(screen.getByText("No stage changes yet.")).toBeTruthy();
  });
});

// ---- the tab: what it asks the server -------------------------------------------

const mocks = vi.hoisted(() => ({
  lifecycleOptions: vi.fn(),
  reviewStatus: vi.fn(),
  stageHistory: vi.fn(),
  transition: vi.fn(),
  completeStep: vi.fn(),
  startForContract: vi.fn(),
  retryReview: vi.fn(),
  notify: vi.fn(),
  permissions: [] as string[],
}));

vi.mock("@/lib/endpoints", () => ({
  contractsApi: {
    lifecycleOptions: mocks.lifecycleOptions,
    reviewStatus: mocks.reviewStatus,
    stageHistory: mocks.stageHistory,
    transition: mocks.transition,
    retryReview: mocks.retryReview,
  },
  workflowsApi: { completeStep: mocks.completeStep, startForContract: mocks.startForContract },
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { permissions: mocks.permissions } }) }));
vi.mock("@/components/toast", () => ({ useToast: () => ({ notify: mocks.notify }) }));

function mountTab(r: WorkflowRun | null = reviewRun) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
  const onOpenReview = vi.fn();
  render(
    <QueryClientProvider client={client}>
      <LifecycleTab contractId="c-1" contractStage="review" run={r} onOpenReview={onOpenReview} />
    </QueryClientProvider>,
  );
  return { client, onOpenReview };
}

describe("LifecycleTab", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.permissions = [];
    mocks.lifecycleOptions.mockResolvedValue({ current_stage: "review", days_in_stage: 1, allowed_transitions: ["drafting", "approval"] });
    mocks.reviewStatus.mockResolvedValue(review());
    mocks.stageHistory.mockResolvedValue(history);
    mocks.transition.mockResolvedValue({});
    mocks.completeStep.mockResolvedValue(reviewRun);
    mocks.startForContract.mockResolvedValue(run([], { flow_name: "NDA flow" }));
  });

  it("completes the workflow step to send for approval, with no override", async () => {
    mountTab();
    fireEvent.click(await screen.findByRole("button", { name: 'Complete "Legal review" and send for approval' }));
    await waitFor(() => expect(mocks.completeStep).toHaveBeenCalledWith("run-1", undefined, 0, undefined));
    await waitFor(() => expect(mocks.notify).toHaveBeenCalledWith("Sent for approval", "success"));
    expect(mocks.transition).not.toHaveBeenCalled();
  });

  it("never moves to Approval through a direct stage change", async () => {
    mountTab();
    // The server says Approval is an allowed next stage, but the only way there is the workflow step.
    await screen.findByRole("button", { name: "Back to Drafting" });
    const moves = within(screen.getByRole("region", { name: "Change the stage by hand" })).getAllByRole("button");
    expect(moves.map((b) => b.textContent)).toEqual(["Back to Drafting"]);
  });

  it("sends the reason when overriding open suggestions", async () => {
    mocks.permissions = ["contract:lifecycle_override"];
    mocks.reviewStatus.mockResolvedValue(review({ pending_redlines: 3 }));
    mountTab();
    fireEvent.click(await screen.findByRole("button", { name: "Send anyway…" }));
    fireEvent.change(screen.getByLabelText(/Why are you sending it/), { target: { value: "Client deadline" } });
    fireEvent.click(screen.getByRole("button", { name: 'Complete "Legal review" and send for approval' }));
    await waitFor(() => expect(mocks.completeStep).toHaveBeenCalledWith("run-1", undefined, 0, "Client deadline"));
  });

  it("does not offer an override to someone without the permission", async () => {
    mocks.reviewStatus.mockResolvedValue(review({ pending_redlines: 3 }));
    mountTab();
    await screen.findByRole("heading", { name: "Decide 3 suggested changes first" });
    expect(screen.queryByRole("button", { name: "Send anyway…" })).toBeNull();
    expect(mocks.completeStep).not.toHaveBeenCalled();
  });

  it("treats the all-permissions grant as allowed to override", async () => {
    mocks.permissions = ["*"];
    mocks.reviewStatus.mockResolvedValue(review({ pending_redlines: 1 }));
    mountTab();
    expect(await screen.findByRole("button", { name: "Send anyway…" })).toBeTruthy();
  });

  it("moves a contract by hand through the lifecycle endpoint", async () => {
    mountTab();
    fireEvent.click(await screen.findByRole("button", { name: "Back to Drafting" }));
    await waitFor(() => expect(mocks.transition).toHaveBeenCalledWith("c-1", "drafting", {}));
    await waitFor(() => expect(mocks.notify).toHaveBeenCalledWith("Moved to Drafting", "success"));
  });

  it("refetches what is true after a move", async () => {
    mountTab();
    fireEvent.click(await screen.findByRole("button", { name: "Back to Drafting" }));
    await waitFor(() => expect(mocks.lifecycleOptions.mock.calls.length).toBeGreaterThan(1));
    expect(mocks.reviewStatus.mock.calls.length).toBeGreaterThan(1);
    expect(mocks.stageHistory.mock.calls.length).toBeGreaterThan(1);
  });

  it("shows the server's reason when the move is refused, and refetches", async () => {
    mocks.transition.mockRejectedValue(new Error("Invalid lifecycle transition from review to drafting"));
    mountTab();
    fireEvent.click(await screen.findByRole("button", { name: "Back to Drafting" }));
    await waitFor(() => expect(mocks.notify).toHaveBeenCalledWith("Invalid lifecycle transition from review to drafting", "error"));
    await waitFor(() => expect(mocks.lifecycleOptions.mock.calls.length).toBeGreaterThan(1));
  });

  it("shows the server's reason when completing the step is refused", async () => {
    mocks.completeStep.mockRejectedValue(new Error("1 open redline(s) must be accepted or rejected"));
    mountTab();
    fireEvent.click(await screen.findByRole("button", { name: /send for approval/ }));
    await waitFor(() => expect(mocks.notify).toHaveBeenCalledWith("1 open redline(s) must be accepted or rejected", "error"));
  });

  it("starts a workflow when there is none", async () => {
    mountTab(null);
    fireEvent.click(await screen.findByRole("button", { name: "Start workflow" }));
    await waitFor(() => expect(mocks.startForContract).toHaveBeenCalledWith("c-1"));
    await waitFor(() => expect(mocks.notify).toHaveBeenCalledWith("Workflow started: NDA flow", "success"));
  });

  it("uses the server's stage over the one it was given", async () => {
    // Given Review, the tab would offer "Back to Drafting"; the server says Approval, so it offers the pull-back.
    mocks.lifecycleOptions.mockResolvedValue({ current_stage: "approval", allowed_transitions: ["review"] });
    mountTab(run([step(0, "approval", "waiting_job", "Finance")]));
    expect(await screen.findByRole("button", { name: "Send back to Review" })).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Back to Drafting" })).toBeNull();
  });
});

describe("LifecycleTab: the AI review that was asked for", () => {
  const interrupted = { state: "interrupted" as const, reason: "stalled", since: null, job_id: "j-1" };

  beforeEach(() => {
    vi.clearAllMocks();
    mocks.permissions = ["contract:redline", "playbook:run"];
    mocks.lifecycleOptions.mockResolvedValue({ current_stage: "review", days_in_stage: 1, allowed_transitions: ["drafting", "approval"] });
    mocks.stageHistory.mockResolvedValue(history);
    mocks.reviewStatus.mockResolvedValue(review());
  });

  it("says nothing about the review when none is pending", async () => {
    mountTab();
    await screen.findByRole("region", { name: "What to do next" });
    expect(screen.queryByTestId("review-notice")).toBeNull();
  });

  it("says the review was interrupted, and runs it again from the Retry button", async () => {
    mocks.reviewStatus.mockResolvedValue(review({ ai_reviewed: false, auto_review: interrupted }));
    mocks.retryReview.mockResolvedValue({ status: "queued", job_id: "j-2", workflow_resumed: false });
    mountTab();
    expect(await screen.findByRole("alert")).toHaveTextContent("The AI review was interrupted");
    fireEvent.click(screen.getByRole("button", { name: "Retry review" }));
    await waitFor(() => expect(mocks.retryReview).toHaveBeenCalledWith("c-1"));
    await waitFor(() => expect(mocks.notify).toHaveBeenCalledWith(expect.stringMatching(/restarted/i), "success"));
    // What is true now is asked for again.
    await waitFor(() => expect(mocks.reviewStatus.mock.calls.length).toBeGreaterThan(1));
  });

  it("offers reviewing by hand where a workflow step is waiting on the person", async () => {
    mocks.reviewStatus.mockResolvedValue(review({ auto_review: interrupted }));
    mountTab();
    expect(await screen.findByRole("alert")).toHaveTextContent("review the contract yourself and complete the step");
  });

  it("does not suggest that when no workflow step is waiting", async () => {
    mocks.reviewStatus.mockResolvedValue(review({ auto_review: interrupted }));
    mountTab(null);
    expect(await screen.findByRole("alert")).not.toHaveTextContent("yourself");
  });

  it("says a review is running, with no button", async () => {
    mocks.reviewStatus.mockResolvedValue(review({ auto_review: { state: "running", reason: null, since: new Date().toISOString(), job_id: "j-1" } }));
    mountTab();
    expect(await screen.findByRole("status")).toHaveTextContent("AI review in progress");
    expect(screen.queryByRole("button", { name: "Retry review" })).toBeNull();
  });

  it("does not offer Retry to someone without the rights to redline and run playbooks", async () => {
    mocks.permissions = [];
    mocks.reviewStatus.mockResolvedValue(review({ auto_review: interrupted }));
    mountTab();
    expect(await screen.findByRole("alert")).toHaveTextContent("Ask someone who can redline and run playbooks");
    expect(screen.queryByRole("button", { name: "Retry review" })).toBeNull();
  });

  it("keeps asking while the review runs, so the notice clears by itself when it ends", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      mocks.reviewStatus.mockResolvedValue(review({ auto_review: { state: "running", reason: null, since: new Date().toISOString(), job_id: "j-1" } }));
      mountTab();
      expect(await screen.findByRole("status")).toHaveTextContent("AI review in progress");
      mocks.reviewStatus.mockResolvedValue(review());
      await act(async () => { await vi.advanceTimersByTimeAsync(5_100); });
      await waitFor(() => expect(screen.queryByTestId("review-notice")).toBeNull());
    } finally {
      vi.useRealTimers();
    }
  });
});

// ---- after a rejected approval: the reply the approver is owed ------------------

describe("LifecyclePanel: sent back after a rejected approval", () => {
  const reason = "term dates are impossible";
  const owed = run(
    [step(0, "human_task", "waiting_human", "Legal review"), step(1, "approval", "pending", "Finance")],
    { rework: { reply_required: true, reason } },
  );
  const sendButton = () => screen.getByRole("button", { name: 'Complete "Legal review" and send for approval' });

  it("shows why it was rejected and holds the button until there is a reply", () => {
    panel({ run: owed });
    expect(screen.getByText(new RegExp(reason))).toBeInTheDocument();
    expect(sendButton()).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Reply to the rejection"), { target: { value: "   " } });
    expect(sendButton()).toBeDisabled();
    fireEvent.change(screen.getByLabelText("Reply to the rejection"), { target: { value: "Fixed the dates." } });
    expect(sendButton()).toBeEnabled();
  });

  it("hands the trimmed reply to the step", () => {
    const props = panel({ run: owed });
    fireEvent.change(screen.getByLabelText("Reply to the rejection"), { target: { value: "  Fixed the dates.  " } });
    fireEvent.click(sendButton());
    expect(props.onCompleteStep).toHaveBeenCalledWith(expect.objectContaining({ stepIdx: 0 }), undefined, "Fixed the dates.");
  });

  it("asks for nothing when no approval was rejected", () => {
    const props = panel({ run: reviewRun });
    expect(screen.queryByLabelText("Reply to the rejection")).toBeNull();
    fireEvent.click(sendButton());
    expect(props.onCompleteStep).toHaveBeenCalledTimes(1);
    expect((props.onCompleteStep as ReturnType<typeof vi.fn>).mock.calls[0]).toHaveLength(1);
  });
});

describe("LifecycleTab: sent back after a rejected approval", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.permissions = [];
    mocks.lifecycleOptions.mockResolvedValue({ current_stage: "review", days_in_stage: 1, allowed_transitions: ["drafting", "approval"] });
    mocks.reviewStatus.mockResolvedValue(review());
    mocks.stageHistory.mockResolvedValue(history);
    mocks.completeStep.mockResolvedValue(reviewRun);
  });

  it("sends the reply to the server with the step", async () => {
    const owed = run(
      [step(0, "human_task", "waiting_human", "Legal review"), step(1, "approval", "pending", "Finance")],
      { rework: { reply_required: true, reason: "term dates are impossible" } },
    );
    mountTab(owed);
    fireEvent.change(await screen.findByLabelText("Reply to the rejection"), { target: { value: "Fixed the dates." } });
    fireEvent.click(screen.getByRole("button", { name: 'Complete "Legal review" and send for approval' }));
    await waitFor(() => expect(mocks.completeStep).toHaveBeenCalledWith("run-1", undefined, 0, undefined, "Fixed the dates."));
  });
});
