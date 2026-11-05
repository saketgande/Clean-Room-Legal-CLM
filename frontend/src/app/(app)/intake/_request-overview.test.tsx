import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ComponentProps, ReactNode } from "react";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { RequestOverview } from "./_request-overview";
import { fmtDate } from "@/lib/utils";
import type { IntakeRequest, RequestFormDef, ReviewStatusResponse, WorkflowRun, WorkflowRunStep } from "@/lib/types";
import { HttpError } from "@/lib/api";

// The request page's workflow ladder: what it shows when the step it is on stopped because the contract's
// AI review was interrupted, and what its Retry button does. The rest of the page is stood in for or left
// to return nothing, so that only this wiring shows.

const h = vi.hoisted(() => ({
  notify: vi.fn(),
  permissions: ["contract:redline", "playbook:run"] as string[],
  // The stage bar is left out of every test except the one that reads a skipped step's note off it.
  stageBar: false,
  // When true the stage bar is replaced by a marker, so a test can count how many bars the page draws.
  stageMarker: false,
  intakeApi: { get: vi.fn(), documents: vi.fn(), handoffs: vi.fn(), activity: vi.fn(), counterparty: vi.fn(), requestSummary: vi.fn(), assignees: vi.fn(), approvalChain: vi.fn(), approvalSummary: vi.fn(), teams: vi.fn(), draftContract: vi.fn(), forms: vi.fn(), update: vi.fn() },
  workflowsApi: { runForRequest: vi.fn(), lifecycle: vi.fn(), listFlows: vi.fn(), refreshRun: vi.fn(), retryReview: vi.fn(), completeStep: vi.fn(), startFlow: vi.fn(), returnStep: vi.fn() },
  contractsApi: { risk: vi.fn(), deviations: vi.fn(), reviewStatus: vi.fn() },
  brainApi: { ask: vi.fn() },
  approvalsApi: { decide: vi.fn() },
  obligationsApi: { extract: vi.fn() },
}));

vi.mock("@/lib/endpoints", () => ({ intakeApi: h.intakeApi, workflowsApi: h.workflowsApi, contractsApi: h.contractsApi, brainApi: h.brainApi, approvalsApi: h.approvalsApi, obligationsApi: h.obligationsApi }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { full_name: "Legal One", permissions: h.permissions } }) }));
vi.mock("@/components/toast", () => ({ useToast: () => ({ notify: h.notify }) }));
vi.mock("next/navigation", () => ({ useRouter: () => ({ push: vi.fn(), replace: vi.fn() }) }));
vi.mock("./_negotiation-panel", () => ({ NegotiationPanel: () => null }));
vi.mock("@/components/lifecycle-view", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/components/lifecycle-view")>();
  return {
    ...actual,
    LifecycleView: (p: ComponentProps<typeof actual.LifecycleView>) => (h.stageBar ? <actual.LifecycleView {...p} /> : h.stageMarker ? <i data-testid="stage-bar" /> : null),
    NextStepCard: () => null,
  };
});
vi.mock("next/link", () => ({ default: (p: { href: string; children: ReactNode; className?: string }) => <a href={p.href} className={p.className}>{p.children}</a> }));

// The deliverables block reads the real form definitions, as the page does from GET /intake/forms.
const FORMS: RequestFormDef[] = JSON.parse(
  readFileSync(resolve(__dirname, "../../../../../backend/app/intake/agreement_forms.json"), "utf8"),
);

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

// The workflow card (tiles, notices and links). Waits for the run to load: before it does the card says "No steps yet".
const ladder = () => document.querySelector(".wft")?.closest(".card") as HTMLElement;
const tiles = () => [...document.querySelectorAll(".wft .t")] as HTMLElement[];
const tileOf = (name: string) => tiles().find((t) => t.querySelector("b")?.textContent?.includes(name)) as HTMLElement;
const findLadder = () => waitFor(() => { const el = ladder(); if (!el) throw new Error("no workflow tiles yet"); return el; });
const mainColumn = () => document.querySelector(".body > .col") as HTMLElement;
const sideColumn = () => document.querySelectorAll(".body > .col")[1] as HTMLElement;
const headings = (el: Element) => [...el.querySelectorAll(":scope > .card > .ch h2, :scope > .rpane > .card > .ch h2, :scope > section.decision > .card > .ch h2")].map((h2) => h2.textContent);

beforeEach(() => {
  vi.resetAllMocks();
  h.permissions = ["contract:redline", "playbook:run"];
  h.stageBar = false;
  h.stageMarker = false;
  h.intakeApi.get.mockResolvedValue(request);
  h.intakeApi.documents.mockResolvedValue([]);
  h.intakeApi.handoffs.mockResolvedValue([]);
  h.intakeApi.activity.mockResolvedValue({ items: [], truncated: false });
  h.intakeApi.counterparty.mockResolvedValue({ name: null, summary: { contracts: 0, active: 0, requests: 0 }, note: null, contracts: [], requests: [] });
  h.intakeApi.requestSummary.mockResolvedValue({ text: null, at: null, by_name: null, instructions: [] });
  h.intakeApi.assignees.mockResolvedValue([]);
  h.intakeApi.approvalChain.mockResolvedValue([]);
  h.intakeApi.approvalSummary.mockResolvedValue({ request_id: "q-1", contract_id: "c-1", approval_request_id: "a-1", profile: "general", fields: [], open_risk: { critical: 0, high: 0 } });
  h.intakeApi.teams.mockResolvedValue([]);
  h.intakeApi.forms.mockResolvedValue(FORMS);
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
    const notice = await within(await findLadder()).findByRole("alert");
    expect(notice).toHaveTextContent("The AI review was interrupted");
    expect(notice).toHaveTextContent("review the contract yourself and complete the step");
    expect(within(notice).getByRole("button", { name: "Retry review" })).toBeEnabled();
    // The notice sits on the AI step, not on the steps around it.
    // The notice sits under the tiles, headed by the step's name.
    expect(notice.closest(".wfnotice")?.querySelector(".lbl")).toHaveTextContent("AI risk review");
    // One way to finish it, in "Your decision": not a button on a tile, one in the header and one in the card.
    expect(within(tileOf("AI risk review")).queryByRole("button", { name: /Complete|Mark done/ })).not.toBeInTheDocument();
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

describe("the Filed by row", () => {
  it("names the agent when the request was filed with an API key", async () => {
    h.intakeApi.get.mockResolvedValue({ ...request, filed_by_agent: true, filed_by_agent_name: "Agent: Bot" });
    mount();
    await screen.findByRole("heading", { name: "Acme MSA" });
    expect(await screen.findByText("Filed by")).toBeInTheDocument();
    expect(screen.getByText("Agent · Agent: Bot")).toBeInTheDocument();
  });

  it("is absent for a request not filed by an agent", async () => {
    // Old data and demo fixtures have no such field and must render as before.
    mount();
    await screen.findByRole("heading", { name: "Acme MSA" });
    await screen.findByText("Requested by");
    expect(screen.queryByText("Filed by")).toBeNull();
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
    await findLadder();
    expect(tileOf("AI risk review")).toBeTruthy();
    expect(document.querySelector(".wfnotice")).toBeNull();
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
  /** The warning is the app's own dialog; say yes to it when it shows (it only shows when something is open). */
  const sayYes = async () => { const b = await screen.findByRole("button", { name: "Approve anyway" }, { timeout: 400 }).catch(() => null); if (b) fireEvent.click(b); };
  let restoreConfirm = () => {};

  beforeEach(() => {
    const spy = vi.spyOn(window, "confirm");
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
    await sayYes();
    await waitFor(() => expect(h.workflowsApi.completeStep).toHaveBeenCalledWith("run-1", undefined, 1, undefined, "Fixed the dates."));
    await waitFor(() => expect(screen.queryByLabelText("Reply to the rejection")).toBeNull());
  });

  it("keeps the server's refusal on the page (not a toast that vanishes) and keeps what was typed", async () => {
    h.workflowsApi.completeStep.mockRejectedValue(new Error("The approval was rejected: term dates are impossible. Write a reply to that before sending it on: say what you changed, or why it stands."));
    mount();
    const box = await screen.findByLabelText("Reply to the rejection");
    fireEvent.change(box, { target: { value: "Fixed." } });
    fireEvent.click(approve());
    await sayYes();
    expect(await screen.findByRole("alert")).toHaveTextContent(/Write a reply/);
    expect(screen.getByText("This step can't be completed yet")).toBeInTheDocument();
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
    await sayYes();
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

describe("the progress strip's 'Agent working…'", () => {
  it("is not shown for an approval waiting on people", async () => {
    // Failure mode: approval/signature steps sit in waiting_job while people decide, so the strip claimed an agent was working.
    h.workflowsApi.runForRequest.mockResolvedValue(runOf([step(0, "clm_draft", "done", "Draft"), step(1, "approval", "waiting_job", "Legal Approval")], { status: "running" }));
    mount();
    await screen.findByRole("heading", { name: "Acme MSA" });
    await waitFor(() => expect(screen.getAllByText(/Legal Approval/).length).toBeGreaterThan(0));
    expect(screen.queryByText(/Agent working/)).not.toBeInTheDocument();
  });

  it("is shown for an automated step in flight", async () => {
    h.workflowsApi.runForRequest.mockResolvedValue(runOf([step(0, "clm_draft", "done", "Draft"), step(1, "ai_task", "running", "AI risk review")], { status: "running" }));
    mount();
    expect(await screen.findByText(/Agent working…/)).toBeInTheDocument();
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
    // Failure mode: the status line reading the request's raw stage ("new") while the stage bar says Review.
    h.intakeApi.get.mockResolvedValue({ ...request, stage: "new" });
    mount();
    await screen.findByRole("heading", { name: "Acme MSA" });
    const line = document.querySelector(".stline") as HTMLElement;
    expect(line).toHaveTextContent("Review");
    expect(line).not.toHaveTextContent("new");
  });
});

describe("notes written on the workflow (round 2, bug 45)", () => {
  const note = { idx: 0, kind: "comment", text: "Please check clause 9", actor_id: "u-1", actor_name: "Erin Legal", at: "2026-10-08T09:10:00Z" };

  it("carries a time in Comments, and Activity shows what the activity endpoint sent (the page no longer merges comments itself)", async () => {
    // Failure mode: the client keeps stitching comments into Activity, so a note shows twice or a code leaks out.
    h.workflowsApi.runForRequest.mockResolvedValue({ ...stuckRun(), comments: [note] });
    h.intakeApi.activity.mockResolvedValue({ truncated: false, items: [{ id: "c:run-1:0", at: "2026-10-08T09:10:00Z", sentence: "Commented: “Please check clause 9”", who: "Erin Legal", source: "request", tone: "plain", system: false }] });
    mount();
    await screen.findByRole("heading", { name: "Acme MSA" });
    const comments = document.querySelector(".wfcomments") as HTMLElement;
    expect(within(comments).getByText("Please check clause 9")).toBeInTheDocument();
    expect(comments.querySelector(".cmeta")?.textContent).toMatch(/Oct\s+8/);
    const feed = await waitFor(() => { const el = document.querySelector(".actv") as HTMLElement | null; if (!el) throw new Error("no activity yet"); return el; });
    expect(feed).toHaveTextContent("Erin Legal");
    expect(feed).toHaveTextContent("Commented: “Please check clause 9”");
  });
});

describe("sending a step back (round 2, bugs 43 and 44)", () => {
  beforeEach(() => { Element.prototype.scrollIntoView = vi.fn(); }); // jsdom has none
  it("says why Send back is off until a step is picked, and turns it on when one is", async () => {
    mount();
    await findLadder();
    expect(document.querySelector(".sendback")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Send back for rework" }));
    const card = document.querySelector(".sendback") as HTMLElement;
    const button = within(card).getByRole("button", { name: "Send back" });
    expect(button).toBeDisabled();
    expect(within(card).getByText("Pick the step to return to, then Send back is available.")).toBeInTheDocument();
    fireEvent.change(within(card).getByRole("combobox"), { target: { value: "0" } });
    expect(within(card).getByRole("button", { name: "Send back" })).toBeEnabled();
    expect(within(card).queryByText(/Pick the step to return to, then/)).not.toBeInTheDocument();
  });

  it("the decision card's Send back opens the Workflow tab's form and focuses the step picker, sending nothing by itself", async () => {
    // Failure mode: the button did nothing because the form sits in a hidden tab, or it sent without a reason.
    mount();
    await findLadder();
    fireEvent.click(screen.getByRole("button", { name: "Send back for rework…" }));
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Step to send back to" })).toHaveFocus());
    expect(document.querySelectorAll(".sendback")).toHaveLength(1);
    expect(screen.getByRole("tab", { name: /Workflow/ })).toHaveAttribute("aria-selected", "true");
    expect(h.workflowsApi.returnStep).not.toHaveBeenCalled();
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
    await findLadder();
    const rowOf = tileOf;
    expect(within(rowOf("Privacy review")).getByText("Skipped")).toBeInTheDocument();
    expect(within(rowOf("Privacy review")).queryByText("Done")).not.toBeInTheDocument();
    expect(rowOf("Privacy review").querySelector("b")).toHaveTextContent("Privacy review");
    expect(rowOf("Privacy review").querySelector(".tnode")).toHaveTextContent("–");
    expect(rowOf("Privacy review")).toHaveClass("skip");
    expect(within(rowOf("Legal review")).getByText("Done")).toBeInTheDocument();
  });

  it("counts only the steps that applied, and says how many were skipped", async () => {
    h.workflowsApi.runForRequest.mockResolvedValue(withSkipped());
    mount();
    await screen.findByRole("heading", { name: "Acme MSA" });
    // 4 steps: the drafting step is not a skip (the contract was already there), so only Privacy review is one.
    expect(await screen.findByText(/2 of 3 steps done, 1 skipped/)).toBeInTheDocument();
  });

  it("does not call the drafting step skipped: it says whose paper the draft is", async () => {
    // Failure mode: "Prepare draft — Skipped" on a request whose draft is the document the requester uploaded, or our own template.
    h.intakeApi.get.mockResolvedValue({ ...request, field_values: { paper: "Our template" } });
    h.workflowsApi.runForRequest.mockResolvedValue(withSkipped());
    mount();
    await findLadder();
    const row = tileOf("Prepare draft");
    expect(row).toHaveClass("done");
    expect(within(row).getByText("Drafted from our template")).toBeInTheDocument();
    expect(within(row).queryByText("Skipped")).not.toBeInTheDocument();
  });

  it("hides a step whose condition was not met, and shows it again on request", async () => {
    // Failure mode: Quality review and Privacy review listed on a request they do not apply to.
    h.workflowsApi.runForRequest.mockResolvedValue(runOf([
      step(0, "human_task", "done", "Legal review"),
      { ...step(1, "human_task", "skipped", "Privacy review", { note: "Condition not met" }), cond: { field: "personal_data", op: "is", value: "Yes" } } as never,
      step(2, "human_task", "waiting_human", "Sign-off"),
    ], { current_index: 2 }));
    mount();
    await findLadder();
    expect(screen.queryByText("Privacy review")).not.toBeInTheDocument();
    expect(await screen.findByText(/1 of 2 steps done$/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Show 1 step that does not apply/ }));
    expect(tileOf("Privacy review")).toHaveClass("skip");
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
  /** The warning is the app's own dialog; say yes to it when it shows (it only shows when something is open). */
  const sayYes = async () => { const b = await screen.findByRole("button", { name: "Approve anyway" }, { timeout: 400 }).catch(() => null); if (b) fireEvent.click(b); };

  it("offers the step that is still waiting once the first is done, and completes that one", async () => {
    h.workflowsApi.runForRequest.mockResolvedValue(together("done", "waiting_human"));
    h.workflowsApi.completeStep.mockResolvedValue(together("done", "done", { current_index: 3 }));
    mount();
    await screen.findByRole("heading", { name: "Acme MSA" });
    await waitFor(() => expect(approve()).toBeEnabled());
    // The page says where it is: the Privacy review, not the finished Quality review.
    expect(screen.getAllByText(/Privacy review/).length).toBeGreaterThan(0);
    fireEvent.click(approve());
    await sayYes();
    await waitFor(() => expect(h.workflowsApi.completeStep).toHaveBeenCalledWith("run-1", undefined, 2));
  });

  it("names the step it completes, so the server never has to guess", async () => {
    h.workflowsApi.runForRequest.mockResolvedValue(together("waiting_human", "waiting_human"));
    h.workflowsApi.completeStep.mockResolvedValue(together("done", "waiting_human"));
    mount();
    await screen.findByRole("heading", { name: "Acme MSA" });
    await waitFor(() => expect(approve()).toBeEnabled());
    fireEvent.click(approve());
    await sayYes();
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

describe("drafting from a template that does not pass its playbook (006, FR-47)", () => {
  const undrafted = { ...request, contract_id: null, draftable_doc_label: "MSA" } as unknown as IntakeRequest;
  const NOTICE = "This template does not currently pass its playbook: Has findings — 2 findings, highest High. The draft was created anyway.";

  async function draftIt(result: unknown) {
    h.intakeApi.get.mockResolvedValue(undrafted);
    h.workflowsApi.runForRequest.mockResolvedValue(null);
    h.intakeApi.draftContract.mockResolvedValue(result);
    mount();
    fireEvent.click(await screen.findByRole("button", { name: /Approve & draft the MSA/ }));
    await waitFor(() => expect(h.intakeApi.draftContract).toHaveBeenCalledWith("q-1"));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith("Drafting — review running", "success"));
  }

  it("shows the server's notice at the top, without changing the draft's own success flow", async () => {
    // Guards the notice being dropped (the person drafts from a failing template and never hears it) or
    // being turned into a block: the draft must still report success.
    await draftIt({ ...undrafted, contract_id: "c-9", template_notice: NOTICE });
    expect(await screen.findByText(NOTICE)).toBeInTheDocument();
    expect(screen.getByText(NOTICE).closest('[role="status"]')).toBeInTheDocument();
  });

  it("shows no notice when the template passes", async () => {
    // Guards an empty warning bar (or stale text) appearing on every draft that has nothing to warn about.
    await draftIt({ ...undrafted, contract_id: "c-9", template_notice: null });
    expect(screen.queryByText(/does not currently pass/)).toBeNull();
    expect(document.querySelector('[role="status"]')).toBeNull();
  });
});

// ---------------------------------------------------------------------------------------------------
// 007: the deliverables of a Statement of Work request, and the draft step that waits for them.

const OLD_TEXT = "Plan by Nov, report by Mar, Ravi signs off";
const HELD_NOTE = "Enter the deliverables as rows first. This step carries on by itself once the rows are saved.";
const WINDOW = { services_start: "2026-11-01", services_end: "2027-03-31", pricing: "Fixed fee" };
const ROWS = [
  { name: "Migration plan", due: "2026-11-30", their_owner: "Priya Nair", our_owner: "Ravi Shah" },
  { name: "Handover pack", due: "2027-03-31", their_owner: "Tom Reyes", our_owner: "Ravi Shah" },
];
const sowRequest = (fv: Record<string, unknown>, over: Partial<IntakeRequest> = {}): IntakeRequest =>
  ({ ...request, type_label: "Statement of Work Request", contract_id: null, ...over, field_values: { request_form: "sow", ...WINDOW, ...fv } }) as IntakeRequest;

const heldStep = (over: Partial<WorkflowRunStep> = {}) =>
  step(0, "clm_draft", "waiting_human", "Draft the SOW", { note: HELD_NOTE, result: { held: "deliverable_rows" }, ...over });
const heldRun = () => runOf([heldStep(), step(1, "human_task", "pending", "Legal review")], { current_index: 0, contract_id: null });
const approveButtons = () => screen.queryAllByRole("button", { name: /Approve & advance/ });
const fillFirstRow = () => {
  for (const [label, value] of [["Deliverable name", "Migration plan"], ["Due date", "2026-11-30"], ["Their owner", "Priya Nair"], ["Our owner", "Ravi Shah"]]) {
    fireEvent.change(screen.getByLabelText(`Row 1, ${label}`), { target: { value } });
  }
};

describe("the deliverables on the request page (FR-18, FR-31)", () => {
  it("shows a request's rows as a read-only table, never as raw data, even to someone who may edit", async () => {
    // Failure mode: a table answer shown as "[object Object]" or JSON on the page, or an editor offered over rows that exist.
    h.intakeApi.get.mockResolvedValue(sowRequest({ deliverable_rows: ROWS }));
    mount();
    const t = await screen.findByRole("table", { name: "Deliverables" });
    expect(within(t).getByText("Handover pack").closest("tr")).toHaveTextContent("D2");
    expect(within(t).getByText("Handover pack").closest("tr")).toHaveTextContent("Tom Reyes");
    expect(within(t).queryAllByRole("textbox")).toHaveLength(0);
    expect(screen.queryByRole("button", { name: "Save deliverables" })).toBeNull();
    expect(screen.queryByText("Earlier free-text answer")).toBeNull();
    expect(screen.queryByText(/object Object|deliverable_rows/)).toBeNull();
  });

  it("shows an older request's free text unchanged, labelled as the earlier answer, with the editor beside it", async () => {
    // Failure mode: the old answer hidden or rewritten (FR-31), or an older request left with no way to enter rows.
    h.intakeApi.get.mockResolvedValue(sowRequest({ deliverables: OLD_TEXT }));
    mount();
    expect(await screen.findByText("Earlier free-text answer")).toBeInTheDocument();
    expect(screen.getByText(OLD_TEXT)).toBeInTheDocument();
    expect(screen.getByLabelText("Row 1, Deliverable name")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Save deliverables" })).toBeInTheDocument();
  });

  it("shows the earlier text but offers no editor to someone who may only look", async () => {
    // Failure mode: an editor shown to a person the page does not let change the request (the server would refuse the save).
    h.intakeApi.get.mockResolvedValue(sowRequest({ deliverables: OLD_TEXT }));
    mount(false);
    expect(await screen.findByText(OLD_TEXT)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save deliverables" })).toBeNull();
    expect(screen.queryByLabelText("Row 1, Deliverable name")).toBeNull();
  });

  it("offers no editor once a contract has been drafted from the request", async () => {
    // Failure mode: rows edited after the draft was made, which would change nothing in the contract.
    h.intakeApi.get.mockResolvedValue(sowRequest({ deliverables: OLD_TEXT }, { contract_id: "c-1" }));
    mount();
    expect(await screen.findByText(OLD_TEXT)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Save deliverables" })).toBeNull();
  });

  it("shows no deliverables block for a request of any other form", async () => {
    // Failure mode: an empty "Deliverables" heading or editor on every request page.
    h.intakeApi.get.mockResolvedValue({ ...request, field_values: { request_form: "nda", deliverables: OLD_TEXT } });
    mount();
    await screen.findByRole("heading", { name: "Acme MSA" });
    await waitFor(() => expect(h.intakeApi.forms).toHaveBeenCalled());
    await act(async () => { await h.intakeApi.forms.mock.results[0].value; });
    expect(screen.queryByRole("heading", { name: "Deliverables" })).toBeNull();
    expect(screen.queryByText("Earlier free-text answer")).toBeNull();
    expect(screen.queryByRole("button", { name: "Save deliverables" })).toBeNull();
  });
});

describe("a draft step held for the deliverables (FR-33, FR-45)", () => {
  beforeEach(() => {
    h.intakeApi.get.mockResolvedValue(sowRequest({ deliverables: OLD_TEXT }));
    h.workflowsApi.runForRequest.mockResolvedValue(heldRun());
  });

  it("shows why the draft is held and offers no Approve & advance, since the step is not done by hand", async () => {
    // Failure mode: a person completing the held step by hand (the server answers 409) or left with no reason shown.
    mount();
    expect(await screen.findByRole("status")).toHaveTextContent(HELD_NOTE);
    expect(approveButtons()).toHaveLength(0);
    expect(screen.queryByRole("heading", { name: "Your decision" })).toBeNull();
    expect(h.workflowsApi.completeStep).not.toHaveBeenCalled();
    // The rows to enter are right there under the reason, beside the old text.
    expect(screen.getByLabelText("Row 1, Deliverable name")).toBeInTheDocument();
    expect(screen.getByText(OLD_TEXT)).toBeInTheDocument();
  });

  it("shows the reason to an approver who may not edit, with no editor", async () => {
    // Failure mode: the hold visible only to the people who can fix it (FR-18: approvers see the same page).
    mount(false);
    expect(await screen.findByRole("status")).toHaveTextContent(HELD_NOTE);
    expect(screen.queryByRole("button", { name: "Save deliverables" })).toBeNull();
    expect(approveButtons()).toHaveLength(0);
  });

  it("saves the rows, asks the held run to look again, says so, and then fetches the request and the run afresh", async () => {
    // Failure mode: rows saved but the step left held until the next sweep, or the page still showing the hold and no draft.
    const saved = sowRequest({ deliverables: OLD_TEXT, deliverable_rows: ROWS.slice(0, 1) });
    const drafted = sowRequest({ deliverables: OLD_TEXT, deliverable_rows: ROWS.slice(0, 1) }, { contract_id: "c-9" });
    const movedOn = runOf([step(0, "clm_draft", "done", "Draft the SOW"), step(1, "human_task", "waiting_human", "Legal review")], { current_index: 1, contract_id: "c-9" });
    h.intakeApi.get.mockResolvedValueOnce(sowRequest({ deliverables: OLD_TEXT })).mockResolvedValue(drafted);
    h.workflowsApi.runForRequest.mockResolvedValueOnce(heldRun()).mockResolvedValue(movedOn);
    h.intakeApi.update.mockResolvedValue(saved);
    h.workflowsApi.refreshRun.mockResolvedValue(movedOn);

    mount();
    await screen.findByRole("status");
    fillFirstRow();
    fireEvent.click(screen.getByRole("button", { name: "Save deliverables" }));

    await waitFor(() => expect(h.notify).toHaveBeenCalledWith("Deliverables saved", "success"));
    // Every stored answer goes back with the rows added: the update replaces the answers whole.
    expect(h.intakeApi.update).toHaveBeenCalledWith("q-1", { field_values: { ...sowRequest({ deliverables: OLD_TEXT }).field_values, deliverable_rows: [ROWS[0]] } });
    expect(h.workflowsApi.refreshRun).toHaveBeenCalledWith("run-1");
    const order = (m: { mock: { invocationCallOrder: number[] } }, i = 0) => m.mock.invocationCallOrder[i];
    const toast = h.notify.mock.calls.findIndex((c) => c[0] === "Deliverables saved");
    // update, then the run is asked to look again, then the toast, then the request is fetched again.
    await waitFor(() => expect(h.intakeApi.get).toHaveBeenCalledTimes(2));
    expect(order(h.intakeApi.update)).toBeLessThan(order(h.workflowsApi.refreshRun));
    expect(order(h.workflowsApi.refreshRun)).toBeLessThan(h.notify.mock.invocationCallOrder[toast]);
    expect(h.notify.mock.invocationCallOrder[toast]).toBeLessThan(order(h.intakeApi.get, 1));
    await waitFor(() => expect(h.workflowsApi.runForRequest).toHaveBeenCalledTimes(2));

    // The hold is gone; the drafted contract is there to open and the next step can be approved.
    await waitFor(() => expect(screen.queryByText(HELD_NOTE)).toBeNull());
    expect(screen.getByRole("link", { name: /Review & redline/ })).toHaveAttribute("href", "/contracts/c-9");
    expect(approveButtons().length).toBeGreaterThan(0);
  });

  it("keeps the rows and tells the person when the server refuses them, without asking the run to look again", async () => {
    // Failure mode: a refused save still poking the workflow, or the typed rows thrown away.
    h.intakeApi.update.mockRejectedValue(new Error('"Deliverables", row 1: "Due date" must be between 1 November 2026 and 31 March 2027'));
    mount();
    await screen.findByRole("status");
    fillFirstRow();
    fireEvent.click(screen.getByRole("button", { name: "Save deliverables" }));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith(expect.stringContaining('row 1: "Due date"'), "error"));
    expect(h.workflowsApi.refreshRun).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Row 1, Deliverable name")).toHaveValue("Migration plan");
    expect(screen.getByRole("status")).toHaveTextContent(HELD_NOTE);
  });
});

describe("a Statement of Work step that is waiting for another reason", () => {
  it("still has Approve & advance: only a draft held for the deliverables loses it", async () => {
    // Failure mode: the new rule hiding the button for every waiting draft or every Statement of Work request.
    h.intakeApi.get.mockResolvedValue(sowRequest({ deliverable_rows: ROWS }, { contract_id: "c-1" }));
    h.workflowsApi.runForRequest.mockResolvedValue(runOf([
      step(0, "clm_draft", "waiting_human", "Draft the SOW"), step(1, "human_task", "pending", "Legal review"),
    ], { current_index: 0 }));
    mount();
    await screen.findByRole("table", { name: "Deliverables" });
    expect(await screen.findByRole("button", { name: /Approve & advance/ })).toBeEnabled();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("still has Approve & advance on a person's review step of a request that has rows", async () => {
    // Failure mode: the hold test keyed on the request instead of on the step.
    h.intakeApi.get.mockResolvedValue(sowRequest({ deliverable_rows: ROWS }, { contract_id: "c-1" }));
    h.workflowsApi.runForRequest.mockResolvedValue(runOf([
      step(0, "clm_draft", "done", "Draft the SOW"), step(1, "human_task", "waiting_human", "Legal review"),
    ]));
    mount();
    expect(await screen.findByRole("button", { name: /Approve & advance/ })).toBeEnabled();
  });
});

// ---------------------------------------------------------------------------------------------------
// 008: the answers a Consultancy or DPA draft is made from, and the draft steps that wait for them or skip.

const HELD_ANSWERS = "Enter these answers on the request first: \"Deliverables\", \"How is the consultant paid?\", \"Consultant's key individuals\", \"Company contact: name\" and \"Company contact: role\". The Engagement Schedule of the Consultancy draft is made from them, and this request does not have them yet. This step carries on by itself once the answers are saved on the request.";
const HELD_FORM = "File this request on the Data Processing Agreement form first. It came in by email or chat, so it has none of the form's answers, and Annex 1 and Annex 3 of the DPA draft are made from them.";
const PROCESSOR_NOTE = "No draft was made: this request says we are the Processor, and our DPA template is written for the Controller. Attach the other side's DPA (or the paper to use) before the review.";
const PROCESSOR = "Processor: we act on their instructions";
const OLDER_CONSULTANCY = {
  request_form: "new_agreement", agreement_type: "Consultancy", paper: "Our template", currency: "INR", value: 1800000,
  term: "Fixed end date", start_date: "2026-11-01", end_date: "2027-04-30", scope: "Advise on the quality process.",
};
const consultancyRequest = (fv: Record<string, unknown>, over: Partial<IntakeRequest> = {}): IntakeRequest =>
  ({ ...request, type_label: "New agreement · Consultancy", contract_id: null, draftable_doc_type: "consultancy", draftable_doc_label: "Consultancy", ...over, field_values: { ...OLDER_CONSULTANCY, ...fv } }) as IntakeRequest;
const dpaRequest = (fv: Record<string, unknown>, over: Partial<IntakeRequest> = {}): IntakeRequest =>
  ({ ...request, type_label: "Data Processing Agreement", contract_id: null, draftable_doc_type: "dpa", draftable_doc_label: "DPA", ...over, field_values: fv }) as IntakeRequest;
const heldAnswers = (note = HELD_ANSWERS, held = "answers") =>
  runOf([step(0, "clm_draft", "waiting_human", "Draft the agreement", { note, result: { held } }), step(1, "human_task", "pending", "Legal review")],
    { current_index: 0, contract_id: null });
const fillConsultancyAnswers = () => {
  for (const [label, value] of [
    ["Row 1, Deliverable name", "Current-state review"], ["Row 1, Due date", "2026-12-15"], ["How is the consultant paid?", "Fixed fee"],
    ["Row 1, Name", "Anita Rao"], ["Company contact: name", "Ravi Shah"], ["Company contact: role", "Head of Quality"],
  ]) fireEvent.change(screen.getByLabelText(label), { target: { value } });
};

describe("a Consultancy or DPA draft step held for answers (FR-43, FR-44)", () => {
  it("shows why the draft is held with the answers to enter, and offers no Approve & advance", async () => {
    // Failure mode: a person completing the held step by hand (the server answers 409), or left with no reason and no way to fix it.
    h.intakeApi.get.mockResolvedValue(consultancyRequest({}));
    h.workflowsApi.runForRequest.mockResolvedValue(heldAnswers());
    mount();
    expect(await screen.findByRole("status")).toHaveTextContent(HELD_ANSWERS);
    expect(approveButtons()).toHaveLength(0);
    expect(screen.queryByRole("heading", { name: "Your decision" })).toBeNull();
    expect(h.workflowsApi.completeStep).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Row 1, Deliverable name")).toHaveValue("");
    expect(screen.getByRole("button", { name: "Save answers" })).toBeInTheDocument();
  });

  it("shows the reason to an approver who may not edit, with no editor", async () => {
    // Failure mode: the hold visible only to the people who can fix it.
    h.intakeApi.get.mockResolvedValue(consultancyRequest({}));
    h.workflowsApi.runForRequest.mockResolvedValue(heldAnswers());
    mount(false);
    expect(await screen.findByRole("status")).toHaveTextContent(HELD_ANSWERS);
    expect(screen.getAllByText("Not answered yet")).toHaveLength(5);
    expect(screen.queryByRole("button", { name: "Save answers" })).toBeNull();
    expect(approveButtons()).toHaveLength(0);
  });

  it("saves the answers, asks the held run to look again, says so, and then fetches the request and the run afresh", async () => {
    // Failure mode: answers saved but the step left held until the next sweep, or the page still showing the hold and no draft.
    const filled = { consultancy_deliverables: [{ name: "Current-state review", due: "2026-12-15" }], fee_basis: "Fixed fee",
      key_individuals: [{ name: "Anita Rao" }], company_contact_name: "Ravi Shah", company_contact_role: "Head of Quality" };
    const drafted = consultancyRequest(filled, { contract_id: "c-9" });
    const movedOn = runOf([step(0, "clm_draft", "done", "Draft the agreement"), step(1, "human_task", "waiting_human", "Legal review")], { current_index: 1, contract_id: "c-9" });
    h.intakeApi.get.mockResolvedValueOnce(consultancyRequest({})).mockResolvedValue(drafted);
    h.workflowsApi.runForRequest.mockResolvedValueOnce(heldAnswers()).mockResolvedValue(movedOn);
    h.intakeApi.update.mockResolvedValue(consultancyRequest(filled));
    h.workflowsApi.refreshRun.mockResolvedValue(movedOn);

    mount();
    await screen.findByRole("status");
    fillConsultancyAnswers();
    fireEvent.click(screen.getByRole("button", { name: "Save answers" }));

    await waitFor(() => expect(h.notify).toHaveBeenCalledWith("Answers saved", "success"));
    expect(h.intakeApi.update).toHaveBeenCalledWith("q-1", { field_values: { ...OLDER_CONSULTANCY, ...filled } });
    expect(h.workflowsApi.refreshRun).toHaveBeenCalledWith("run-1");
    const toast = h.notify.mock.calls.findIndex((c) => c[0] === "Answers saved");
    await waitFor(() => expect(h.intakeApi.get).toHaveBeenCalledTimes(2));
    // update, then the run is asked to look again, then the toast, then the request is fetched again.
    expect(h.intakeApi.update.mock.invocationCallOrder[0]).toBeLessThan(h.workflowsApi.refreshRun.mock.invocationCallOrder[0]);
    expect(h.workflowsApi.refreshRun.mock.invocationCallOrder[0]).toBeLessThan(h.notify.mock.invocationCallOrder[toast]);
    expect(h.notify.mock.invocationCallOrder[toast]).toBeLessThan(h.intakeApi.get.mock.invocationCallOrder[1]);
    await waitFor(() => expect(h.workflowsApi.runForRequest).toHaveBeenCalledTimes(2));

    await waitFor(() => expect(screen.queryByText(/Enter these answers on the request first/)).toBeNull());
    expect(screen.getByRole("link", { name: /Review & redline/ })).toHaveAttribute("href", "/contracts/c-9");
    expect(approveButtons().length).toBeGreaterThan(0);
    // The drafted contract exists, so the answers are shown, not asked for again.
    expect(screen.queryByRole("button", { name: "Save answers" })).toBeNull();
    expect(screen.getByRole("table", { name: "Deliverables" })).toBeInTheDocument();
  });

  it("keeps the entries and shows the server's message when it refuses them", async () => {
    // Failure mode: a refused save still poking the workflow, or the typed answers thrown away.
    h.intakeApi.get.mockResolvedValue(consultancyRequest({}));
    h.workflowsApi.runForRequest.mockResolvedValue(heldAnswers());
    h.intakeApi.update.mockRejectedValue(new Error('"Deliverables", row 1: "Due date" must be between 1 November 2026 and 30 April 2027'));
    mount();
    await screen.findByRole("status");
    fillConsultancyAnswers();
    fireEvent.click(screen.getByRole("button", { name: "Save answers" }));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith(expect.stringContaining('row 1: "Due date"'), "error"));
    expect(h.workflowsApi.refreshRun).not.toHaveBeenCalled();
    expect(screen.getByLabelText("Row 1, Deliverable name")).toHaveValue("Current-state review");
    expect(screen.getByRole("status")).toHaveTextContent(HELD_ANSWERS);
  });

  it("offers no editor once a contract has been drafted from the request", async () => {
    // Failure mode: answers edited after the draft was made, which would change nothing in the contract.
    h.intakeApi.get.mockResolvedValue(consultancyRequest({}, { contract_id: "c-1" }));
    mount();
    await screen.findByRole("heading", { name: "Answers for the draft" });
    expect(screen.queryByRole("button", { name: "Save answers" })).toBeNull();
    expect(screen.queryByLabelText("Row 1, Deliverable name")).toBeNull();
  });

  it("shows an email request's reason and no editor: with no form there is nothing to enter", async () => {
    // Failure mode: Approve & advance on a step the server refuses to complete by hand, or an editor with no form behind it.
    h.intakeApi.get.mockResolvedValue(dpaRequest({}));
    h.workflowsApi.runForRequest.mockResolvedValue(heldAnswers(HELD_FORM, "form"));
    mount();
    expect(await screen.findByRole("status")).toHaveTextContent(HELD_FORM);
    expect(approveButtons()).toHaveLength(0);
    expect(screen.queryByRole("heading", { name: "Answers for the draft" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Save answers" })).toBeNull();
  });

  it("still has Approve & advance on a Consultancy draft step that waits for another reason", async () => {
    // Failure mode: the hold test keyed on the kind of request instead of on the step's own `held` value.
    h.intakeApi.get.mockResolvedValue(consultancyRequest({}, { contract_id: "c-1" }));
    h.workflowsApi.runForRequest.mockResolvedValue(runOf([
      step(0, "clm_draft", "waiting_human", "Draft the agreement"), step(1, "human_task", "pending", "Legal review"),
    ], { current_index: 0 }));
    mount();
    expect(await screen.findByRole("button", { name: /Approve & advance/ })).toBeEnabled();
    expect(screen.queryByRole("status")).toBeNull();
  });
});

describe("a DPA request where we are the Processor (FR-53, FR-54, FR-55)", () => {
  const PROCESSOR_DPA = { request_form: "dpa", paper: "Our template", our_role: PROCESSOR, processing_purpose: "Hosting the portal.", transfer: "No", subprocessors: "No" };

  it("shows the skipped draft step as skipped, with its note, and neither a hold nor an editor", async () => {
    // Failure mode: a Processor request shown as held and asking for answers to a draft that will never be made.
    h.stageBar = true;
    h.intakeApi.get.mockResolvedValue(dpaRequest(PROCESSOR_DPA));
    h.workflowsApi.runForRequest.mockResolvedValue(runOf([
      step(0, "clm_draft", "skipped", "Draft the DPA", { note: PROCESSOR_NOTE, result: { skipped: "processor_role" }, stage: "drafting" }),
      step(1, "human_task", "waiting_human", "Legal review", { stage: "review" }),
    ], { current_index: 1, contract_id: null }));
    mount();
    await screen.findByRole("heading", { name: "Answers for the draft" });
    await findLadder();
    expect(within(tileOf("Draft the DPA")).getByText("Skipped")).toBeInTheDocument();
    expect(screen.queryByRole("status")).toBeNull();
    expect(screen.queryByRole("button", { name: "Save answers" })).toBeNull();
    // The stage bar carries the note: open the Drafting stage.
    fireEvent.click(screen.getByRole("button", { name: /^Drafting:/ }));
    expect(await screen.findByText(PROCESSOR_NOTE)).toBeInTheDocument();
    // The run went on to its next step, which a person approves.
    expect(approveButtons().length).toBeGreaterThan(0);
  });

  it("shows the server's own sentence when a person's Draft is refused, and drafts nothing", async () => {
    // Failure mode: the 422 shown as a generic failure, so the person never learns to attach the other side's DPA.
    h.intakeApi.get.mockResolvedValue(dpaRequest(PROCESSOR_DPA));
    h.workflowsApi.runForRequest.mockResolvedValue(null);
    h.intakeApi.draftContract.mockRejectedValue(new HttpError(422, PROCESSOR_NOTE));
    mount();
    fireEvent.click(await screen.findByRole("button", { name: /Approve & draft the DPA/ }));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith(PROCESSOR_NOTE, "error"));
    expect(h.notify).not.toHaveBeenCalledWith("Drafting — review running", "success");
  });
});

describe("a person's Draft refused because answers are missing (FR-43)", () => {
  it("shows the server's sentence naming the answers", async () => {
    // Failure mode: the 409 shown as a generic failure, so the person does not know which answers to enter.
    h.intakeApi.get.mockResolvedValue(consultancyRequest({}));
    h.workflowsApi.runForRequest.mockResolvedValue(null);
    const sentence = HELD_ANSWERS.replace(" This step carries on by itself once the answers are saved on the request.", "");
    h.intakeApi.draftContract.mockRejectedValue(new HttpError(409, sentence));
    mount();
    fireEvent.click(await screen.findByRole("button", { name: /Approve & draft the Consultancy/ }));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith(sentence, "error"));
  });
});

describe("the approval card and chain decisions on the request page (FR-1, FR-2, FR-22)", () => {
  const rungOf = (over: Record<string, unknown> = {}) => ({
    approval_request_id: "a-1", step_order: 1, status: "pending", approver_label: "Finance team", due_at: null,
    can_decide: false, decisions: [], ...over,
  });

  it("shows Approve and Reject above the chain to the assigned approver only", async () => {
    // Failure mode: decision controls missing for the approver, or shown to everyone who can open the page.
    h.intakeApi.approvalChain.mockResolvedValue([rungOf({ can_decide: true })]);
    mount();
    const approve = await screen.findByRole("button", { name: "Approve" });
    expect(screen.getByRole("button", { name: "Reject" })).toBeInTheDocument();
    // They sit in the decision area, the first thing in the main column.
    expect(approve.closest("section.decision")).toBe(sideColumn().firstElementChild);
  });

  it("tells the card which approval step is open and what it is about", async () => {
    // Failure mode: the card header said only "Approval", so the approver could not tell which approval this was.
    h.intakeApi.approvalChain.mockResolvedValue([rungOf({ can_decide: true })]);
    h.workflowsApi.runForRequest.mockResolvedValue(runOf([step(0, "clm_draft", "done", "Draft"), step(1, "approval", "waiting_job", "Legal Approval")], { status: "running" }));
    mount();
    const card = (await screen.findByRole("button", { name: "Approve" })).closest(".apc") as HTMLElement;
    expect(card.querySelector("h2")).toHaveTextContent("Legal Approval");
    expect(card.querySelector(".hd")).toHaveTextContent("MSA");
  });

  it("is read-only for a viewer who cannot decide", async () => {
    // Failure mode: the requester or another approver seeing Approve/Reject (FR-2).
    h.intakeApi.approvalChain.mockResolvedValue([rungOf()]);
    mount();
    const waiting = await screen.findByText("Waiting on Finance team");
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reject" })).toBeNull();
    // A pending approval is shown to everyone who can open the page, first in the main column (AC-5, AC-20).
    expect(waiting.closest("section.decision")).toBe(sideColumn().firstElementChild);
  });
});

describe("the decision-first layout of the request page (spec 010)", () => {
  const rung = (over: Record<string, unknown> = {}) => ({
    approval_request_id: "a-1", step_order: 1, status: "pending", approver_label: "Finance team", due_at: null,
    can_decide: false, decisions: [], ...over,
  });
  const working = () => runOf([step(0, "clm_draft", "done", "Draft"), step(1, "ai_task", "running", "AI risk review")], { status: "running" });
  const highRisk = { band: "high", counts: { critical: 0, high: 2, medium: 0, low: 0 }, drivers: [], contract_version_id: "v-1" };

  it("puts the cards in layout A's order in both columns, and draws one stage bar", async () => {
    // Failure mode: a card landing in the wrong column or order, or the old second tracker ("You are here") coming back.
    h.stageMarker = true;
    h.intakeApi.approvalChain.mockResolvedValue([rung()]);
    h.contractsApi.risk.mockResolvedValue(highRisk);
    h.intakeApi.counterparty.mockResolvedValue({ name: "Globex", summary: { contracts: 0, active: 0, requests: 0 }, note: "New counterparty. No other contracts or requests on file.", contracts: [], requests: [] });
    mount();
    await screen.findByText("Waiting on Finance team");
    await screen.findByRole("heading", { name: "AI risk review" });
    // One tab pane per section, in tab order; only the open pane (Overview) is on screen, the rest are inert.
    expect(headings(mainColumn())).toEqual(["Workflow", "Documents", "AI risk review", "AI analysis", "Activity", "Comments and questions", "Counterparty"]);
    expect([...document.querySelectorAll(".rpane[data-on]")].map((p) => p.id)).toEqual(["rp-overview"]);
    await waitFor(() => expect(document.querySelector(".cpname")).toBeInTheDocument());
    expect(headings(sideColumn())).toEqual(["Approval", "Your decision", "People involved", "About the counterparty"]);
    expect(screen.queryByRole("heading", { name: "Steps" })).toBeNull();
    expect(screen.getAllByTestId("stage-bar")).toHaveLength(1);
    for (const gone of ["You are here", "At a glance", "Workflow & stages", "Next step", "Relationship", "People", "Actions"]) {
      expect(screen.queryByRole("heading", { name: gone })).toBeNull();
    }
    expect(document.body).not.toHaveTextContent("You are here");
  });

  it("shows a pending approval the viewer cannot decide at the top of the main column, read-only (AC-5 a)", async () => {
    // Failure mode: a pending approval hidden from, or buried below, the people waiting on it.
    h.intakeApi.approvalChain.mockResolvedValue([rung()]);
    h.workflowsApi.runForRequest.mockResolvedValue(working());
    mount();
    const waiting = await screen.findByText("Waiting on Finance team");
    const area = document.querySelector("section.decision") as HTMLElement;
    expect(area).toBe(sideColumn().firstElementChild);
    expect(area).toContainElement(waiting);
    expect(within(area).queryByRole("button")).toBeNull();
  });

  it("takes no space for the decision when nothing is pending and no person's step is waiting (AC-5 b)", async () => {
    // Failure mode: an empty bordered "decision" block, or the approval card, on a request nobody has to decide.
    h.workflowsApi.runForRequest.mockResolvedValue(working());
    mount();
    await screen.findByRole("heading", { name: "Acme MSA" });
    await findLadder();
    expect(document.querySelector("section.decision")).toBeNull();
    expect(document.querySelector(".apc")).toBeNull();
    expect(document.querySelector(".ov2 h2")?.textContent).toBe("Request details");
  });

  it("keeps the header to a one-line summary, and shows each fact once outside the approval card (AC-6)", async () => {
    // Failure mode: the same owner, requester, counterparty or risk band drawn in several cards, one going stale.
    h.intakeApi.get.mockResolvedValue({
      ...request, field_values: { value: 500000, request_form: "msa" }, ai_triage: { source: "form" },
      parties: [{ name: "Globex", role: "counterparty" }],
    });
    h.intakeApi.counterparty.mockResolvedValue({ name: "Globex", summary: { contracts: 0, active: 0, requests: 0 }, note: "Known counterparty: 1 NDA on file.", contracts: [], requests: [] });
    h.intakeApi.approvalChain.mockResolvedValue([rung()]);
    h.intakeApi.approvalSummary.mockResolvedValue({ request_id: "q-1", contract_id: "c-1", approval_request_id: "a-1", profile: "general", fields: [{ key: "value", label: "Value", value: "INR 5,00,000", source: "verified", quote: false }], open_risk: { critical: 0, high: 2 } });
    h.contractsApi.risk.mockResolvedValue(highRisk);
    mount();
    await screen.findByText("Waiting on Finance team");
    await screen.findByText("High risk");
    const header = document.querySelector(".rohd") as HTMLElement;
    expect(header).toHaveTextContent("Acme MSA");
    expect(header).toHaveTextContent(/REQ-1 · raised by Asha · owner Legal One/);
    expect(header).toHaveTextContent("High risk");
    expect(header).not.toHaveTextContent("Sales");
    expect(header).toHaveTextContent("Globex"); // the mockup puts the counterparty in the header chips
    expect(header).not.toHaveTextContent("Signed an NDA");
    expect(header).not.toHaveTextContent(/medium|low/i);
    const body = (document.querySelector(".ro") as HTMLElement).cloneNode(true) as HTMLElement; // body + the read-later row
    const apc = body.querySelector(".apc") as HTMLElement;
    // The approval card repeats the value on purpose; it reads the same stored figure as the Value fact.
    expect(apc).toHaveTextContent("INR 5,00,000");
    expect(screen.getAllByText("INR 5,00,000").filter((n) => !apc.contains(n) && document.contains(n)).length).toBeGreaterThanOrEqual(1);
    apc.remove();
    const count = (sel: string, text: string) => [...body.querySelectorAll(sel)].filter((n) => n.textContent === text).length;
    expect(count(".person .n", "Legal One")).toBe(1);
    expect(count(".person .n", "Asha")).toBe(1);
    await waitFor(() => expect(document.querySelector(".cpname")).toBeInTheDocument());
    const body2 = (document.querySelector(".ro") as HTMLElement).cloneNode(true) as HTMLElement;
    body2.querySelector(".apc")?.remove();
    // 011 FR-16: Globex is a fact once and the Counterparty card's name once; the header has none.
    expect([...body2.querySelectorAll(".ov2 .kv dd")].filter((n) => n.textContent === "Globex")).toHaveLength(1);
    expect(body2.querySelectorAll(".cpname")).toHaveLength(1);
    expect(body2.querySelectorAll(".rb")).toHaveLength(1);
  });

  it("shows the requester's own title and words, and none of the system's filler (AC-8)", async () => {
    // Failure mode: the typed title replaced by a derived one, or "— form by Local Admin" and the engine sentence coming back.
    h.intakeApi.get.mockResolvedValue({ ...request, source: "form", subject: "Today_14_test", description: "Please review", requester_name: "Local Admin", field_values: { request_form: "nda" } });
    mount();
    expect(await screen.findByRole("heading", { level: 1 })).toHaveTextContent(/^Today_14_test$/);
    const asked = screen.getByRole("heading", { name: "Request details" }).closest(".card") as HTMLElement;
    expect(within(asked).getByText("Please review")).toBeInTheDocument();
    expect(document.body).not.toHaveTextContent(/form by/);
    expect(document.body).not.toHaveTextContent(/workflow engine drives/);
    expect(screen.queryByText(/Ask about this request/)).toBeNull();
  });

  it("never prints a step's saved internal name or raw result data (AC-7)", async () => {
    // Failure mode: "Clm Draft" or {"deviation_id": …} shown to a requester.
    h.stageBar = true;
    h.workflowsApi.runForRequest.mockResolvedValue(runOf([
      step(0, "clm_draft", "done", "Clm Draft", { result: { top_drivers: [{ deviation_id: "d-1", severity: "high", issue: "Liability is unlimited" }] } }),
      step(1, "human_task", "waiting_human", "Legal review"),
    ]));
    mount();
    await findLadder();
    expect(tileOf("Prepare draft")).toBeTruthy();
    expect(screen.getByText(/High · Liability is unlimited/)).toBeInTheDocument();
    expect(document.body.textContent).not.toContain("Clm Draft");
    expect(document.body.textContent).not.toContain("deviation_id");
    expect(document.body.textContent).not.toContain('{"');
  });

  it("gives a person who may only look no action, but still the read-only approval card (AC-20)", async () => {
    // Failure mode: manage-only controls shown to a viewer, or the pending approval hidden from them.
    h.intakeApi.approvalChain.mockResolvedValue([rung()]);
    h.intakeApi.assignees.mockResolvedValue([{ id: "u-2", name: "Erin", email: "e@x" }]);
    mount(false);
    expect(await screen.findByText("Waiting on Finance team")).toBeInTheDocument();
    for (const label of [/Approve & advance/, /Send back/, "Reassign owner", "Post", "Recompute", /Approve & draft/, /Use attached/, "Start it", "Retry review", /^Check /]) {
      expect(screen.queryByRole("button", { name: label })).toBeNull();
    }
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reject" })).toBeNull();
    expect(screen.queryByLabelText("Add a comment or question")).toBeNull();
    expect(screen.queryByLabelText("New owner")).toBeNull();
    // Ask Aegis stays open to everyone: the header button opens the chat popup (there is no inline card).
    expect(screen.getByRole("button", { name: "Ask Aegis" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Ask a question")).toBeNull();
  });

  it("offers each action once to a manager: Reassign, Post, Send back, Approve & advance (AC-18)", async () => {
    // Failure mode: an action dropped by the move, or drawn twice (one in the header, one in a card).
    h.intakeApi.assignees.mockResolvedValue([{ id: "u-2", name: "Erin", email: "e@x" }]);
    mount();
    await screen.findByRole("heading", { name: "Acme MSA" });
    await screen.findByRole("button", { name: /Approve & advance/ });
    for (const label of ["Reassign owner", "Post", /Approve & advance/, "Send back for rework…", "Send back for rework", "Recompute", "Open in CLM editor →"]) {
      expect(screen.getAllByRole(label === "Open in CLM editor →" ? "link" : "button", { name: label })).toHaveLength(1);
    }
    // The form's own Send back button exists only once the card's link opens it, and then exactly once.
    expect(screen.queryByRole("button", { name: "Send back" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Send back for rework" }));
    expect(screen.getAllByRole("button", { name: "Send back" })).toHaveLength(1);
  });

  it("shows a requester the tiles and every state but none of the controls (AC-5, AC-26)", async () => {
    // Failure mode: the new tile row handing a person who cannot act a Check, Start, picker, Send back or doorway.
    h.workflowsApi.runForRequest.mockResolvedValue(runOf([step(0, "clm_draft", "done", "Draft"), step(1, "approval", "waiting_job", "Approval", { team_label: "Finance" }), step(2, "human_task", "pending", "Legal review")]));
    mount(false);
    await findLadder();
    expect(tiles()).toHaveLength(3);
    expect(tileOf("Approval")).toHaveClass("cur");
    for (const label of [/^Check /, "Start it", "Send back for rework", "Send back", "Retry review"]) expect(screen.queryByRole("button", { name: label })).toBeNull();
    expect(screen.queryByLabelText("Pick a workflow")).toBeNull();
    expect(document.querySelector(".wft .doorway")).toBeNull();
  });

  it("keeps the rest of the page when Activity cannot load, and says so in one line (AC-12)", async () => {
    // Failure mode: one failing read blanking the whole request page.
    h.intakeApi.activity.mockRejectedValue(new Error("boom"));
    mount();
    expect(await screen.findByText("Activity could not be loaded.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Request details" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Workflow" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "People involved" })).toBeInTheDocument();
  });

  it("keeps a half-typed comment when more activity is shown (edge case)", async () => {
    // Failure mode: the activity card remounting the page and wiping a typed comment or reply.
    h.intakeApi.activity.mockResolvedValue({ truncated: false, items: Array.from({ length: 25 }, (_, i) => ({ id: `t:${i}`, at: `2026-10-08T09:${String(i).padStart(2, "0")}:00Z`, sentence: `Event ${i}`, who: "Aegis", source: "request", tone: "plain", system: false })) });
    mount();
    const input = await screen.findByLabelText("Add a comment or question");
    fireEvent.change(input, { target: { value: "half a thought" } });
    fireEvent.click(await screen.findByRole("button", { name: /Show earlier activity/ }));
    expect(screen.getByLabelText("Add a comment or question")).toHaveValue("half a thought");
  });

  it("opens another request from the Counterparty card through onOpenRequest, and scrolls to the top (AC-17)", async () => {
    // Failure mode: a link that changes the URL but leaves the old request on screen.
    window.scrollTo = vi.fn();
    h.intakeApi.counterparty.mockResolvedValue({ name: "Globex", summary: { contracts: 0, active: 0, requests: 1 }, note: null, contracts: [], requests: [{ id: "q-7", ref: "REQ-7", type_label: "NDA", status: "closed", date: null }] });
    const onOpenRequest = vi.fn();
    const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(<QueryClientProvider client={qc}><RequestOverview id="q-1" onBack={() => {}} canManage onOpenRequest={onOpenRequest} /></QueryClientProvider>);
    fireEvent.click(await screen.findByRole("link", { name: /REQ-7/ }));
    expect(onOpenRequest).toHaveBeenCalledWith("q-7");
    expect(window.scrollTo).toHaveBeenCalledWith({ top: 0 });
  });

  it("opens the Ask Aegis chat popup from the header, tied to the request's contract", async () => {
    // Failure mode: the button scrolling to a card that no longer exists, or opening a chat that is not about this request.
    const seen: unknown[] = [];
    const on = (e: Event) => seen.push((e as CustomEvent).detail);
    window.addEventListener("aegis:ask", on);
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Ask Aegis" }));
    window.removeEventListener("aegis:ask", on);
    expect(seen).toHaveLength(1);
    expect(seen[0]).toHaveProperty("contractId");
  });
});

describe("the request summary section on the request page (spec 013)", () => {
  it("shows the instructions inside the page", async () => {
    // Failure mode: filed instructions missing from the request page.
    h.intakeApi.requestSummary.mockResolvedValue({ text: "An NDA.", at: "2026-10-27T09:00:00Z", by_name: "Asha", instructions: [{ text: "Copy procurement", differs: [] }] });
    mount();
    await screen.findByText("Copy procurement");
    expect(h.intakeApi.requestSummary).toHaveBeenCalledWith("q-1");
  });

  it("a failing summary shows its own Retry and leaves the rest of the page", async () => {
    // Failure mode: a rejected summary query blanking the request page.
    h.intakeApi.requestSummary.mockRejectedValue(new Error("boom"));
    mount();
    await screen.findByText(/The request summary could not be loaded/);
    expect(await screen.findByRole("heading", { name: "People involved" })).toBeTruthy();
  });
});

describe("Retry on the request page's Active stage", () => {
  const failed = {
    name: "Obligation extraction failed", kind: "AI", detail: "The last attempt did not finish.", who: "Aegis AI",
    status: "failed", when: null, action: { kind: "extract_obligations", label: "Retry", contract_id: "c-1" },
  };
  async function openActive(permissions: string[]) {
    h.stageBar = true;
    h.permissions = permissions;
    h.workflowsApi.lifecycle.mockResolvedValue({ active: [failed] });
    const mounted = mount();
    fireEvent.click(await screen.findByRole("button", { name: /^Active:/ }));
    return mounted;
  }

  it("shows Retry only to someone whose role may edit obligations (the server still decides)", async () => {
    await openActive(["intake:read"]);
    expect(await screen.findByText("Obligation extraction failed")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  });

  it("starts the extraction for the row's contract and refetches the rows, also when the server refuses", async () => {
    h.obligationsApi.extract.mockRejectedValueOnce(new Error("Obligations were already read for this version."));
    const { invalidate } = await openActive(["obligation:update"]);
    invalidate.mockClear();
    fireEvent.click(await screen.findByRole("button", { name: "Retry" }));
    await waitFor(() => expect(h.obligationsApi.extract).toHaveBeenCalledWith("c-1"));
    await waitFor(() => expect(keysOf(invalidate).map((k) => JSON.stringify(k))).toContain(JSON.stringify(["lifecycle-rows", "q-1"])));
    // The refusal sits under this stage's list (the page has other alerts, so look for it by its text).
    expect(await screen.findByText(/already read for this version/)).toHaveAttribute("role", "alert");
  });
});
