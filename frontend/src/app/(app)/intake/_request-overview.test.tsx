import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RequestOverview } from "./_request-overview";
import { fmtDate } from "@/lib/utils";
import type { IntakeRequest, ReviewStatusResponse, WorkflowRun, WorkflowRunStep } from "@/lib/types";
import { HttpError } from "@/lib/api";

// The request page's workflow ladder: what it shows when the step it is on stopped because the contract's
// AI review was interrupted, and what its Retry button does. The rest of the page is stood in for or left
// to return nothing, so that only this wiring shows.

const h = vi.hoisted(() => ({
  notify: vi.fn(),
  permissions: ["contract:redline", "playbook:run"] as string[],
  intakeApi: { get: vi.fn(), documents: vi.fn(), handoffs: vi.fn(), assignees: vi.fn(), approvalChain: vi.fn(), teams: vi.fn() },
  workflowsApi: { runForRequest: vi.fn(), lifecycle: vi.fn(), listFlows: vi.fn(), refreshRun: vi.fn(), retryReview: vi.fn(), completeStep: vi.fn(), startFlow: vi.fn(), returnStep: vi.fn() },
  contractsApi: { risk: vi.fn(), deviations: vi.fn(), reviewStatus: vi.fn() },
  brainApi: { ask: vi.fn() },
}));

vi.mock("@/lib/endpoints", () => ({ intakeApi: h.intakeApi, workflowsApi: h.workflowsApi, contractsApi: h.contractsApi, brainApi: h.brainApi }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { full_name: "Legal One", permissions: h.permissions } }) }));
vi.mock("@/components/toast", () => ({ useToast: () => ({ notify: h.notify }) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));
vi.mock("./_negotiation-panel", () => ({ NegotiationPanel: () => null }));
vi.mock("@/components/lifecycle-view", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/components/lifecycle-view")>()),
  LifecycleView: () => null,
  NextStepCard: () => null,
}));
vi.mock("next/link", () => ({ default: (p: { href: string; children: ReactNode; className?: string }) => <a href={p.href} className={p.className}>{p.children}</a> }));

const request = {
  id: "q-1", ref: "REQ-1", source: "form", requester_user_id: "u-9", requester_name: "Asha", department: "Sales",
  type_label: "MSA", subject: "Acme MSA", description: "Please review the Acme MSA.", field_values: {},
  priority: "Medium", status: "in_review", stage: "review", work_status: null,
  assigned_to_user_id: "u-1", assigned_to_label: "Legal One", approval_gate_user_id: null,
  sla_hours: 48, sla_status: "on_track", sla_pct: 10, submitted_at: null, closed_at: null,
  triaged_by_user_id: null, triage_action: null, ai_triage: null, parties: [], handoff_holder: null, handoff_user_id: null,
  contract_id: "c-1", workflow: [], created_at: null,
} as unknown as IntakeRequest;

const step = (idx: number, type: WorkflowRunStep["type"], status: string, name: string, over: Partial<WorkflowRunStep> = {}): WorkflowRunStep => ({
  idx, type, name, status, assignee_user_id: null, assignee_label: null, team_id: null, team_label: null,
  note: null, result: null, updated_at: null, ...over,
});

const interruptedStep = (over: Partial<WorkflowRunStep> = {}) =>
  step(1, "ai_task", "waiting_human", "AI risk review", {
    note: "The AI review didn't finish — it was interrupted. Retry it, or review the contract yourself.",
    result: { review_state: "interrupted", risk_band: "unknown" },
    ...over,
  });

const runOf = (steps: WorkflowRunStep[], over: Partial<WorkflowRun> = {}): WorkflowRun => ({
  id: "run-1", request_id: "q-1", flow_id: "f-1", flow_name: "Standard MSA", status: "waiting", current_index: 1,
  contract_id: "c-1", error: null, steps, comments: [], ...over,
});

const stuckRun = () => runOf([step(0, "clm_draft", "done", "Draft"), interruptedStep(), step(2, "human_task", "pending", "Legal review")]);

const reviewState = (auto: unknown) => ({ contract_id: "c-1", lifecycle_stage: "review", checklist: [], auto_review: auto }) as unknown as ReviewStatusResponse;
const interrupted = { state: "interrupted", reason: "stalled", since: null, job_id: "j-1" };
const keysOf = (spy: { mock: { calls: unknown[][] } }) => spy.mock.calls.map((c) => (c[0] as { queryKey: unknown[] }).queryKey);

function mount(canManage = true) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(qc, "invalidateQueries");
  render(
    <QueryClientProvider client={qc}>
      <RequestOverview id="q-1" onBack={() => {}} canManage={canManage} />
    </QueryClientProvider>,
  );
  return { qc, invalidate };
}

const ladder = () => document.querySelector(".steps") as HTMLElement;

beforeEach(() => {
  vi.resetAllMocks();
  h.permissions = ["contract:redline", "playbook:run"];
  h.intakeApi.get.mockResolvedValue(request);
  h.intakeApi.documents.mockResolvedValue([]);
  h.intakeApi.handoffs.mockResolvedValue([]);
  h.intakeApi.assignees.mockResolvedValue([]);
  h.intakeApi.approvalChain.mockResolvedValue([]);
  h.intakeApi.teams.mockResolvedValue([]);
  h.workflowsApi.runForRequest.mockResolvedValue(stuckRun());
  h.workflowsApi.lifecycle.mockResolvedValue({});
  h.workflowsApi.listFlows.mockResolvedValue([]);
  h.contractsApi.risk.mockResolvedValue(null);
  h.contractsApi.deviations.mockResolvedValue([]);
  h.contractsApi.reviewStatus.mockResolvedValue(reviewState(null));
});

describe("a workflow step that stopped on an interrupted AI review", () => {
  it("says so on that step, with a Retry button, and still lets the person finish the step themselves", async () => {
    mount();
    const notice = await within(await screen.findByRole("heading", { name: "Acme MSA" }).then(() => ladder())).findByRole("alert");
    expect(notice).toHaveTextContent("The AI review was interrupted");
    expect(notice).toHaveTextContent("review the contract yourself and complete the step");
    expect(within(notice).getByRole("button", { name: "Retry review" })).toBeEnabled();
    // The notice sits on the AI step, not on the steps around it.
    const stepEl = notice.closest(".step") as HTMLElement;
    expect(within(stepEl).getByText("AI risk review")).toBeInTheDocument();
    // One way to finish it, in "Your decision": not a button on the step, one in the header and one in the card.
    expect(within(stepEl).queryByRole("button", { name: /Complete|Mark done/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Complete this step" })).not.toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: /Approve & advance/ })).toHaveLength(1);
  });

  it("says why it stopped when the contract's review state knows", async () => {
    h.contractsApi.reviewStatus.mockResolvedValue(reviewState({ ...interrupted, reason: "failed" }));
    mount();
    await waitFor(() => expect(within(ladder() ?? document.body).getByRole("alert")).toHaveTextContent("It stopped with an error before it finished."));
  });

  it("restarts the review for this run and shows the step working again", async () => {
    h.workflowsApi.retryReview.mockResolvedValue(runOf([step(0, "clm_draft", "done", "Draft"), step(1, "ai_task", "running", "AI risk review"), step(2, "human_task", "pending", "Legal review")], { status: "running" }));
    const { qc, invalidate } = mount();
    fireEvent.click(await screen.findByRole("button", { name: "Retry review" }));
    await waitFor(() => expect(h.workflowsApi.retryReview).toHaveBeenCalledWith("run-1"));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith("Review restarted. The workflow carries on when it finishes.", "success"));
    // The run the server returned replaces the stuck one: the notice is gone.
    await waitFor(() => expect(screen.queryByRole("button", { name: "Retry review" })).toBeNull());
    expect((qc.getQueryData(["flow-run", "q-1"]) as WorkflowRun).status).toBe("running");
    // And what the review writes is fetched again as it lands.
    expect(keysOf(invalidate)).toContainEqual(["contract-review", "c-1"]);
    expect(keysOf(invalidate)).toContainEqual(["contract-risk", "c-1"]);
    expect(keysOf(invalidate)).toContainEqual(["contract-devs", "c-1"]);
  });

  it("cannot be pressed twice while the restart is on its way", async () => {
    let resolve!: (v: unknown) => void;
    h.workflowsApi.retryReview.mockReturnValue(new Promise((r) => { resolve = r; }));
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Retry review" }));
    const busy = await screen.findByRole("button", { name: "Restarting…" });
    expect(busy).toBeDisabled();
    fireEvent.click(busy);
    expect(h.workflowsApi.retryReview).toHaveBeenCalledTimes(1);
    resolve(stuckRun());
    await waitFor(() => expect(h.notify).toHaveBeenCalled());
  });

  it("shows the server's refusal and leaves the step as it was", async () => {
    h.workflowsApi.retryReview.mockRejectedValue(new Error("This workflow isn't waiting on an interrupted AI review."));
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Retry review" }));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith("This workflow isn't waiting on an interrupted AI review.", "error"));
    expect(screen.getByRole("button", { name: "Retry review" })).toBeInTheDocument();
  });

  it("offers no button to someone who may only look, nor to someone without the rights", async () => {
    mount(false);
    await screen.findByRole("heading", { name: "Acme MSA" });
    expect(screen.queryByRole("button", { name: "Retry review" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Complete" })).toBeNull();
  });

  it("says who can retry when this person lacks the rights", async () => {
    h.permissions = ["intake:read"];
    mount();
    expect(await screen.findByText(/Ask someone who can redline and run playbooks/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry review" })).toBeNull();
  });
});

describe("a workflow step that is waiting for another reason", () => {
  it("has no review notice: a person's own step, or an AI step that is waiting on a person for another reason", async () => {
    h.workflowsApi.runForRequest.mockResolvedValue(runOf([
      step(0, "clm_draft", "done", "Draft"),
      step(1, "ai_task", "waiting_human", "AI risk review", { result: { risk_band: "high", confidence: 0.4 } }),
      step(2, "human_task", "pending", "Legal review"),
    ]));
    mount();
    await screen.findByRole("heading", { name: "Acme MSA" });
    await within(ladder()).findByText("AI risk review");
    expect(screen.queryByTestId("review-notice")).toBeNull();
    expect(screen.queryByRole("button", { name: "Retry review" })).toBeNull();
  });
});

describe("when the review ends", () => {
  it("runs the workflow on from it and fetches what the review wrote", async () => {
    h.contractsApi.reviewStatus.mockResolvedValue(reviewState({ state: "running", reason: null, since: new Date().toISOString(), job_id: "j-1" }));
    h.workflowsApi.refreshRun.mockResolvedValue(runOf([step(0, "clm_draft", "done", "Draft"), step(1, "ai_task", "done", "AI risk review"), step(2, "human_task", "waiting_human", "Legal review")], { current_index: 2 }));
    const { qc, invalidate } = mount();
    await waitFor(() => expect(h.contractsApi.reviewStatus).toHaveBeenCalled());
    await screen.findByRole("heading", { name: "Acme MSA" });
    expect(h.workflowsApi.refreshRun).not.toHaveBeenCalled();

    invalidate.mockClear();
    await act(async () => { qc.setQueryData(["contract-review", "c-1"], reviewState(null)); });
    await waitFor(() => expect(h.workflowsApi.refreshRun).toHaveBeenCalledWith("run-1"));
    await waitFor(() => expect((qc.getQueryData(["flow-run", "q-1"]) as WorkflowRun).current_index).toBe(2));
    expect(keysOf(invalidate)).toContainEqual(["contract-risk", "c-1"]);
    expect(keysOf(invalidate)).toContainEqual(["contract-devs", "c-1"]);
  });

  it("does not touch the workflow when no review was ever pending", async () => {
    mount();
    await screen.findByRole("heading", { name: "Acme MSA" });
    await waitFor(() => expect(h.contractsApi.reviewStatus).toHaveBeenCalled());
    await act(async () => { await new Promise((r) => setTimeout(r, 50)); });
    expect(h.workflowsApi.refreshRun).not.toHaveBeenCalled();
  });
});

describe("a request sent back after a rejected approval", () => {
  const reason = "term dates are impossible, end is before start";
  const sentBack = (over: Partial<WorkflowRun> = {}) =>
    runOf(
      [step(0, "clm_draft", "done", "Draft"), step(1, "human_task", "waiting_human", "Legal review"), step(2, "approval", "pending", "Finance approval")],
      { rework: { reply_required: true, reason }, ...over },
    );
  const approve = () => screen.getByRole("button", { name: /Approve & advance/ });
  let restoreConfirm = () => {};

  beforeEach(() => {
    const spy = vi.spyOn(window, "confirm").mockReturnValue(true);
    restoreConfirm = () => spy.mockRestore();
    h.workflowsApi.runForRequest.mockResolvedValue(sentBack());
  });
  afterEach(() => restoreConfirm());

  it("shows why it was rejected and holds the Approve button until there is a reply", async () => {
    mount();
    const box = await screen.findByLabelText("Reply to the rejection");
    expect(screen.getByText(new RegExp(reason))).toBeInTheDocument();
    expect(approve()).toBeDisabled();
    fireEvent.change(box, { target: { value: "   " } });
    expect(approve()).toBeDisabled();
    fireEvent.change(box, { target: { value: "Fixed the dates." } });
    expect(approve()).toBeEnabled();
  });

  it("sends the reply with the step, and the box goes once the server accepts it", async () => {
    h.workflowsApi.completeStep.mockResolvedValue(sentBack({ rework: null, current_index: 2 }));
    mount();
    fireEvent.change(await screen.findByLabelText("Reply to the rejection"), { target: { value: "  Fixed the dates.  " } });
    fireEvent.click(approve());
    await waitFor(() => expect(h.workflowsApi.completeStep).toHaveBeenCalledWith("run-1", undefined, 1, undefined, "Fixed the dates."));
    await waitFor(() => expect(screen.queryByLabelText("Reply to the rejection")).toBeNull());
  });

  it("shows the server's refusal and keeps what was typed", async () => {
    h.workflowsApi.completeStep.mockRejectedValue(new Error("The approval was rejected: term dates are impossible. Write a reply to that before sending it on: say what you changed, or why it stands."));
    mount();
    const box = await screen.findByLabelText("Reply to the rejection");
    fireEvent.change(box, { target: { value: "Fixed." } });
    fireEvent.click(approve());
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith(expect.stringContaining("Write a reply"), "error"));
    expect(box).toHaveValue("Fixed.");
  });

  it("asks for nothing when no approval was rejected, and sends no reply", async () => {
    h.workflowsApi.runForRequest.mockResolvedValue(sentBack({ rework: null }));
    h.workflowsApi.completeStep.mockResolvedValue(sentBack({ rework: null }));
    mount();
    await screen.findByRole("heading", { name: "Acme MSA" });
    await waitFor(() => expect(approve()).toBeEnabled());
    expect(screen.queryByLabelText("Reply to the rejection")).toBeNull();
    fireEvent.click(approve());
    await waitFor(() => expect(h.workflowsApi.completeStep).toHaveBeenCalledWith("run-1", undefined, 1));
  });

  it("labels the reply in the comment thread so the approver can find it", async () => {
    h.workflowsApi.runForRequest.mockResolvedValue(sentBack({
      rework: null,
      comments: [{ idx: 1, kind: "reply", text: "Fixed the dates.", actor_id: "u-1", actor_name: "Legal One", at: "2026-10-08T10:00:00Z" }],
    }));
    mount();
    expect(await screen.findByText("Fixed the dates.")).toBeInTheDocument();
    const thread = document.querySelector(".wfcomments") as HTMLElement;
    expect(within(thread).getByText(/replied to the rejection/)).toBeInTheDocument();
  });
});

describe("a request that was only just filed (round 2, bug 16)", () => {
  const justFiled = () => ({ ...request, contract_id: null, submitted_at: new Date(Date.now() - 5_000).toISOString() }) as IntakeRequest;

  it("says the workflow is being set up, and does not offer a second one to start", async () => {
    h.intakeApi.get.mockResolvedValue(justFiled());
    h.workflowsApi.runForRequest.mockResolvedValue(null);
    h.workflowsApi.listFlows.mockResolvedValue([{ id: "f-1", name: "Standard MSA", enabled: true }]);
    mount();
    expect(await screen.findByText("Setting up the workflow…")).toBeInTheDocument();
    expect(screen.queryByText("No workflow is set up for this request.")).not.toBeInTheDocument();
    expect(screen.queryByRole("combobox", { name: "Pick a workflow" })).not.toBeInTheDocument();
  });

  it("still lets a person pick a workflow for an older request that has none", async () => {
    h.intakeApi.get.mockResolvedValue({ ...justFiled(), submitted_at: new Date(Date.now() - 3_600_000).toISOString() });
    h.workflowsApi.runForRequest.mockResolvedValue(null);
    h.workflowsApi.listFlows.mockResolvedValue([{ id: "f-1", name: "Standard MSA", enabled: true }]);
    mount();
    expect(await screen.findByText("No workflow is set up for this request.")).toBeInTheDocument();
    expect(screen.getByRole("combobox", { name: "Pick a workflow" })).toBeInTheDocument();
  });
});

describe("what the page says about the request (round 2, bug 16)", () => {
  it("shows the form's own term instead of 'open-ended' when there is no end date", async () => {
    h.intakeApi.get.mockResolvedValue({ ...request, field_values: { start_date: "2026-10-01", term: "2-year term" } });
    mount();
    await screen.findByRole("heading", { name: "Acme MSA" });
    // The date reads the way every other date in the app does, not as the stored 2026-10-01 (round 2, bug 48).
    expect(screen.getByText(`${fmtDate("2026-10-01")} · 2-year term`)).toBeInTheDocument();
    expect(screen.queryByText(/2026-10-01/)).not.toBeInTheDocument();
    expect(screen.queryByText(/open-ended/)).not.toBeInTheDocument();
  });

  it("names the stage the way the stage bar does, not by the request's own status word", async () => {
    h.intakeApi.get.mockResolvedValue({ ...request, stage: "new" });
    mount();
    await screen.findByRole("heading", { name: "Acme MSA" });
    const row = screen.getByText("Stage", { selector: ".glance .k" }).closest(".row") as HTMLElement;
    expect(row).toHaveTextContent("Review");
    expect(row).not.toHaveTextContent("new");
  });
});

describe("notes written on the workflow (round 2, bug 45)", () => {
  const note = { idx: 0, kind: "comment", text: "Please check clause 9", actor_id: "u-1", actor_name: "Erin Legal", at: "2026-10-08T09:10:00Z" };

  it("carries a time, and appears in the request's Activity too", async () => {
    h.workflowsApi.runForRequest.mockResolvedValue({ ...stuckRun(), comments: [note] });
    mount();
    await screen.findByRole("heading", { name: "Acme MSA" });
    const comments = document.querySelector(".wfcomments") as HTMLElement;
    expect(within(comments).getByText("Please check clause 9")).toBeInTheDocument();
    expect(comments.querySelector(".cmeta")?.textContent).toMatch(/Oct\s+8/);
    const trail = document.querySelector(".trail") as HTMLElement;
    expect(trail).toHaveTextContent("Erin Legal");
    expect(trail).toHaveTextContent("wrote: Please check clause 9");
  });
});

describe("sending a step back (round 2, bugs 43 and 44)", () => {
  beforeEach(() => { Element.prototype.scrollIntoView = vi.fn(); }); // jsdom has none
  it("says why Send back is off until a step is picked, and turns it on when one is", async () => {
    mount();
    await screen.findByRole("heading", { name: "Acme MSA" });
    const card = document.querySelector(".sendback") as HTMLElement;
    const button = within(card).getByRole("button", { name: "Send back" });
    expect(button).toBeDisabled();
    expect(within(card).getByText("Pick the step to return to, then Send back is available.")).toBeInTheDocument();
    fireEvent.change(within(card).getByRole("combobox"), { target: { value: "0" } });
    expect(within(card).getByRole("button", { name: "Send back" })).toBeEnabled();
    expect(within(card).queryByText(/Pick the step to return to, then/)).not.toBeInTheDocument();
  });

  it("has one Send back form, and the decision card points to it instead of sending by itself", async () => {
    mount();
    await screen.findByRole("heading", { name: "Acme MSA" });
    expect(screen.getByRole("button", { name: "Send back for rework…" })).toBeEnabled();
    fireEvent.click(screen.getByRole("button", { name: "Send back for rework…" }));
    expect(h.workflowsApi.returnStep).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Add a comment" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Request changes/ })).not.toBeInTheDocument();
  });
});

describe("steps the engine skipped", () => {
  const withSkipped = () => runOf([
    step(0, "clm_draft", "skipped", "Prepare draft", { note: "The contract already exists — nothing to draft." }),
    step(1, "human_task", "done", "Legal review"),
    step(2, "human_task", "skipped", "Privacy review", { note: "Condition not met" }),
    step(3, "human_task", "waiting_human", "Counterparty review"),
  ], { current_index: 3 });

  it("shows a skipped step as skipped, not as done", async () => {
    h.workflowsApi.runForRequest.mockResolvedValue(withSkipped());
    mount();
    await screen.findByRole("heading", { name: "Acme MSA" });
    const rowOf = (name: string) => within(ladder()).getByText(name).closest(".step") as HTMLElement;
    expect(within(rowOf("Privacy review")).getByText("skipped")).toBeInTheDocument();
    expect(within(rowOf("Privacy review")).queryByText("done")).not.toBeInTheDocument();
    expect(rowOf("Privacy review").querySelector(".snode")).toHaveTextContent("–");
    expect(within(rowOf("Legal review")).getByText("done")).toBeInTheDocument();
  });

  it("counts only the steps that applied, and says how many were skipped", async () => {
    h.workflowsApi.runForRequest.mockResolvedValue(withSkipped());
    mount();
    await screen.findByRole("heading", { name: "Acme MSA" });
    // 4 steps, 2 skipped: one of the 2 that applied is done.
    expect(await screen.findByText(/1 of 2 steps done, 2 skipped/)).toBeInTheDocument();
  });

  it("does not mention skipped steps when there are none", async () => {
    h.workflowsApi.runForRequest.mockResolvedValue(runOf([step(0, "human_task", "done", "Legal review"), step(1, "human_task", "waiting_human", "Sign-off")]));
    mount();
    expect(await screen.findByText(/1 of 2 steps done$/)).toBeInTheDocument();
  });
});

describe("steps that run side by side", () => {
  // Quality review and Privacy review run together; the run's current_index stays on the first of them.
  const together = (qualityStatus: string, privacyStatus: string, over: Partial<WorkflowRun> = {}) => runOf(
    [
      step(0, "clm_draft", "done", "Draft"),
      step(1, "human_task", qualityStatus, "Quality review", { assignee_user_id: "u-q", assignee_label: "Quentin" }),
      step(2, "human_task", privacyStatus, "Privacy review", { parallel: true, assignee_user_id: "u-p", assignee_label: "Priya" }),
      step(3, "approval", "pending", "Finance approval"),
    ],
    { current_index: 1, ...over },
  );
  const approve = () => screen.getByRole("button", { name: /Approve & advance/ });

  it("offers the step that is still waiting once the first is done, and completes that one", async () => {
    h.workflowsApi.runForRequest.mockResolvedValue(together("done", "waiting_human"));
    h.workflowsApi.completeStep.mockResolvedValue(together("done", "done", { current_index: 3 }));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    mount();
    await screen.findByRole("heading", { name: "Acme MSA" });
    await waitFor(() => expect(approve()).toBeEnabled());
    // The page says where it is: the Privacy review, not the finished Quality review.
    expect(screen.getAllByText(/Privacy review/).length).toBeGreaterThan(0);
    fireEvent.click(approve());
    await waitFor(() => expect(h.workflowsApi.completeStep).toHaveBeenCalledWith("run-1", undefined, 2));
  });

  it("names the step it completes, so the server never has to guess", async () => {
    h.workflowsApi.runForRequest.mockResolvedValue(together("waiting_human", "waiting_human"));
    h.workflowsApi.completeStep.mockResolvedValue(together("done", "waiting_human"));
    vi.spyOn(window, "confirm").mockReturnValue(true);
    mount();
    await screen.findByRole("heading", { name: "Acme MSA" });
    await waitFor(() => expect(approve()).toBeEnabled());
    fireEvent.click(approve());
    await waitFor(() => expect(h.workflowsApi.completeStep).toHaveBeenCalledWith("run-1", undefined, 1));
  });
});


describe("a request that could not be opened", () => {
  it("says it was not found and offers the way back, instead of 'Loading request…' for ever", async () => {
    h.intakeApi.get.mockRejectedValue(new HttpError(404, "Not found"));
    const onBack = vi.fn();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><RequestOverview id="gone" onBack={onBack} canManage /></QueryClientProvider>);
    expect(await screen.findByRole("alert")).toHaveTextContent("Couldn't find this request");
    expect(screen.queryByText("Loading request…")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "← Back to Legal Intake" }));
    expect(onBack).toHaveBeenCalled();
  });

  it("says there is no access when the server refuses", async () => {
    h.intakeApi.get.mockRejectedValue(new HttpError(403, "Missing permission: intake:read"));
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><RequestOverview id="q-9" onBack={() => {}} canManage /></QueryClientProvider>);
    expect(await screen.findByRole("alert")).toHaveTextContent("You don't have access to this request.");
  });
});

describe("the risk card's count line", () => {
  const riskOf = (counts: Record<string, number>, drivers: unknown[] = []) =>
    ({ band: "high", counts: { critical: 0, high: 0, medium: 0, low: 0, ...counts }, drivers, note: null, contract_version_id: "v-1", playbook_id: "p-1", playbook_version_id: "pv-1", playbook_run_ids: ["r-1"], computed_at: "" }) as never;
  const deviation = (id: string, severity: string) => ({ id, severity, status: "open", clause_type: "liability", issue: `Issue ${id}` }) as never;
  const line = async () => {
    await screen.findByRole("heading", { name: "Acme MSA" });
    return waitFor(() => {
      const el = document.querySelector(".rc");
      expect(el).not.toBeNull();
      return el as HTMLElement;
    });
  };

  it("when every finding was already decided, says so instead of showing open findings next to an empty list", async () => {
    h.contractsApi.risk.mockResolvedValue(riskOf({ high: 1, low: 1 }, [
      { deviation_id: "d-1", issue: "Liability is unlimited", severity: "high", status: "accepted", conceded: true },
      { deviation_id: "d-2", issue: "Governing law", severity: "low", status: "waived", conceded: true },
    ]));
    h.contractsApi.deviations.mockResolvedValue([]);
    mount();

    const el = await line();
    await waitFor(() => expect(el).toHaveTextContent("0 still to decide"));
    expect(el).toHaveTextContent("2 findings count toward this");
    expect(el).toHaveTextContent("2 decided, still counted");
    expect(el).not.toHaveTextContent("open finding");
  });

  it("splits the count into what still needs a decision and what was decided", async () => {
    h.contractsApi.risk.mockResolvedValue(riskOf({ high: 1, medium: 1 }, [
      { deviation_id: "d-1", issue: "Liability is unlimited", severity: "high", status: "accepted", conceded: true },
    ]));
    h.contractsApi.deviations.mockResolvedValue([deviation("d-2", "medium")]);
    mount();

    const el = await line();
    await waitFor(() => expect(el).toHaveTextContent("1 still to decide"));
    expect(el).toHaveTextContent("2 findings count toward this");
    expect(el).toHaveTextContent("1 decided, still counted");
  });

  it("has nothing decided to mention when every finding is still open", async () => {
    h.contractsApi.risk.mockResolvedValue(riskOf({ medium: 1 }));
    h.contractsApi.deviations.mockResolvedValue([deviation("d-1", "medium")]);
    mount();

    const el = await line();
    await waitFor(() => expect(el).toHaveTextContent("1 still to decide"));
    expect(el).toHaveTextContent("1 finding counts toward this");
    expect(el).not.toHaveTextContent("decided, still counted");
  });
});
