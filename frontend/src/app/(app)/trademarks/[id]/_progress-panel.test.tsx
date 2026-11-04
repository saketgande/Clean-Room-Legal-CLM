import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { WorkflowRun, WorkflowRunStep } from "@/lib/types";
import { ProgressPanel } from "./_progress-panel";

// The step list and the one action the current step needs, in the case's side column.

const h = vi.hoisted(() => ({ runForRequest: vi.fn(), completeStep: vi.fn(), returnStep: vi.fn(), notify: vi.fn() }));
vi.mock("@/lib/endpoints", () => ({
  workflowsApi: { runForRequest: h.runForRequest, completeStep: h.completeStep, returnStep: h.returnStep },
}));
vi.mock("@/components/toast", () => ({ useToast: () => ({ notify: h.notify }) }));

const step = (idx: number, name: string, status: string, over: Partial<WorkflowRunStep> = {}): WorkflowRunStep =>
  ({ idx, type: "human_task", name, status, assignee_user_id: null, assignee_label: null, team_id: null, team_label: null, note: null, result: null, updated_at: null, ...over }) as WorkflowRunStep;

const run = (steps: WorkflowRunStep[], over: Partial<WorkflowRun> = {}) =>
  ({ id: "r-1", request_id: "q-1", flow_id: "f", flow_name: "Trademark Clearance & Filing", status: "waiting", current_index: 1, contract_id: null, error: null, steps, comments: [], rework: null, ...over }) as WorkflowRun;

const live = () => run([step(0, "Requisition", "done"), step(1, "Detailed search", "waiting_human", { assignee_label: "Erin Legal" }), step(2, "TM Legal review", "pending")]);

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <ProgressPanel requestId="q-1" trademarkId="t-1" />
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.resetAllMocks());

describe("ProgressPanel", () => {
  it("lists the steps with who has them, and offers Mark done and Send back on the current one", async () => {
    h.runForRequest.mockResolvedValue(live());
    mount();

    expect(await screen.findByText("Detailed search")).toBeInTheDocument();
    expect(screen.getByText("With Erin Legal")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Mark done/ })).toBeEnabled();
    expect(screen.getByRole("button", { name: /Send back/ })).toBeInTheDocument();
  });

  it("completes the step it names, so the server never guesses which one", async () => {
    h.runForRequest.mockResolvedValue(live());
    h.completeStep.mockResolvedValue(run([step(0, "Requisition", "done"), step(1, "Detailed search", "done"), step(2, "TM Legal review", "waiting_human")], { current_index: 2 }));
    mount();

    fireEvent.click(await screen.findByRole("button", { name: /Mark done/ }));
    await waitFor(() => expect(h.completeStep).toHaveBeenCalledWith("r-1", undefined, 1));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith("Step completed", "success"));
  });

  it("sends back with the note it was given, after a confirmation click", async () => {
    h.runForRequest.mockResolvedValue(live());
    h.returnStep.mockResolvedValue(live());
    mount();

    fireEvent.click(await screen.findByRole("button", { name: /^Send back/ }));
    fireEvent.change(screen.getByPlaceholderText(/What needs to be reworked/), { target: { value: "Add Registry report" } });
    fireEvent.click(screen.getByRole("button", { name: /Confirm send back/ }));
    await waitFor(() => expect(h.returnStep).toHaveBeenCalledWith("r-1", undefined, "Add Registry report"));
  });

  it("holds Mark done until the reply a rejection is owed has been written", async () => {
    h.runForRequest.mockResolvedValue(
      run([step(0, "Requisition", "done"), step(1, "Detailed search", "waiting_human")], { rework: { reply_required: true, reason: "Rejected: wrong class" } }),
    );
    mount();

    const done = await screen.findByRole("button", { name: /Mark done/ });
    expect(done).toBeDisabled();
    fireEvent.change(screen.getByPlaceholderText("Reply before continuing"), { target: { value: "Fixed the class" } });
    expect(done).toBeEnabled();
  });

  it("offers no action once the workflow is complete", async () => {
    h.runForRequest.mockResolvedValue(run([step(0, "A", "done"), step(1, "B", "done")], { status: "complete", current_index: 2 }));
    mount();

    expect(await screen.findByText("Complete")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Mark done/ })).toBeNull();
  });
});
