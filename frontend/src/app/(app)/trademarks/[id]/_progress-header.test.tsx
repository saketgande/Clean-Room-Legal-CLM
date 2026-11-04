import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { TrademarkCase, WorkflowRun, WorkflowRunStep } from "@/lib/types";
import { ProgressHeader } from "./_progress-header";

// The case's progress on every tab: each step numbered and named, a tick once done, the live step marked.

const h = vi.hoisted(() => ({ runForRequest: vi.fn() }));
vi.mock("@/lib/endpoints", () => ({ workflowsApi: { runForRequest: h.runForRequest } }));

const step = (idx: number, name: string, status: string, over: Partial<WorkflowRunStep> = {}): WorkflowRunStep =>
  ({ idx, type: "human_task", name, status, assignee_user_id: null, assignee_label: null, team_id: null, team_label: null, note: null, result: null, updated_at: null, ...over }) as WorkflowRunStep;

const trademarkCase = { request_id: "q-1", flow_name: "Trademark Clearance & Filing", step_total: 4, step_status: "waiting_human", run_status: "waiting" } as unknown as TrademarkCase;

const run = (steps: WorkflowRunStep[], over: Partial<WorkflowRun> = {}) =>
  ({ id: "r-1", request_id: "q-1", flow_id: "f", flow_name: "Trademark Clearance & Filing", status: "waiting", current_index: 1, contract_id: null, error: null, steps, comments: [], ...over }) as WorkflowRun;

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <ProgressHeader trademarkCase={trademarkCase} />
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.resetAllMocks());

describe("the case's progress header", () => {
  const steps = [
    step(0, "Requisition & preliminary search", "done"),
    step(1, "Detailed search & risk mapping", "waiting_human", { team_label: "LTE / Associates" }),
    step(2, "TM Legal review", "pending", { planned_team_label: "TM Legal" }),
    step(3, "Escalation (TM Head / GC)", "skipped"),
  ];

  it("numbers and names every step, in order", async () => {
    h.runForRequest.mockResolvedValue(run(steps));
    mount();

    const list = await screen.findByRole("list", { name: "Case progress" });
    const items = within(list).getAllByRole("listitem");
    expect(items).toHaveLength(4);
    items.forEach((item, i) => expect(item).toHaveTextContent(`Step ${i + 1}`));
    expect(items[0]).toHaveAccessibleName("Step 1 of 4, Requisition & preliminary search: done");
    expect(items[3]).toHaveTextContent("Escalation (TM Head / GC)");
  });

  it("ticks the finished step, marks the live one with its owner, and shows the others by their number", async () => {
    h.runForRequest.mockResolvedValue(run(steps));
    mount();

    const items = within(await screen.findByRole("list", { name: "Case progress" })).getAllByRole("listitem");
    expect(items[0].querySelector("svg")).not.toBeNull(); // the tick
    expect(items[1]).toHaveAttribute("aria-current", "step");
    expect(items[1]).toHaveTextContent("LTE / Associates");
    expect(items[2].querySelector("svg")).toBeNull();
    expect(items[2]).toHaveTextContent("3"); // not started: its number, not a tick
    expect(items[3]).toHaveTextContent("Skipped");
  });

  it("counts the finished steps and says who has the case", async () => {
    h.runForRequest.mockResolvedValue(run(steps));
    mount();

    expect(await screen.findByText("Step 2 of 4")).toBeInTheDocument();
    expect(screen.getByText(/1 of 4 steps done · with LTE \/ Associates/)).toBeInTheDocument();
  });

  it("says Complete when every step is done, with nothing marked as live", async () => {
    h.runForRequest.mockResolvedValue(run([step(0, "A", "done"), step(1, "B", "done")], { status: "complete", current_index: 2 }));
    mount();

    expect(await screen.findByText("Complete")).toBeInTheDocument();
    const items = within(screen.getByRole("list", { name: "Case progress" })).getAllByRole("listitem");
    items.forEach((item) => expect(item).not.toHaveAttribute("aria-current"));
  });
});
