import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { LegalIntakeBoard } from "./_legal-intake";
import type { IntakeRequest, IntakeWorkflowStep } from "@/lib/types";
import { HttpError } from "@/lib/api";

// The intake board's briefing and its "Awaiting triage" filter: finished work is not "open", and a request
// that already has a workflow is "in workflow", not also "awaiting triage".

const h = vi.hoisted(() => ({
  notify: vi.fn(),
  intakeApi: { list: vi.fn(), pool: vi.fn(), assignToMe: vi.fn() },
}));

vi.mock("@/lib/endpoints", () => ({ intakeApi: h.intakeApi }));
vi.mock("@/components/toast", () => ({ useToast: () => ({ notify: h.notify }) }));

const step = (over: Partial<IntakeWorkflowStep> = {}): IntakeWorkflowStep => ({
  label: "Review", stage: "review", done: false, active: true, ...over,
});

const req = (over: Partial<IntakeRequest>): IntakeRequest => ({
  id: "r-1", ref: "REQ-1", source: "form", requester_user_id: "u-1", requester_name: "Maya", department: null,
  type_label: "NDA", subject: "Acme NDA", description: "", field_values: {}, priority: "Medium",
  status: "open", stage: "new", work_status: null, assigned_to_user_id: "u-2", assigned_to_label: "Gale",
  approval_gate_user_id: null, sla_hours: 24, sla_status: "on_track", sla_pct: 10,
  submitted_at: "2026-10-01T00:00:00Z", closed_at: null, triaged_by_user_id: null, triage_action: null,
  ai_triage: null, handoff_holder: null, handoff_user_id: null, contract_id: null, workflow: [],
  created_at: "2026-10-01T00:00:00Z", ...over,
});

const waiting = req({ id: "r-wait", ref: "REQ-WAIT", subject: "Nobody has started this" });
const running = req({ id: "r-run", ref: "REQ-RUN", subject: "Already in review", workflow: [step()] });
const finished = req({
  id: "r-done", ref: "REQ-DONE", subject: "All stages finished", status: "closed", stage: "complete",
  closed_at: "2026-10-02T00:00:00Z", workflow: [step({ done: true, active: false })],
});

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <LegalIntakeBoard onOpen={vi.fn()} />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  vi.resetAllMocks();
  h.intakeApi.list.mockResolvedValue([waiting, running, finished]);
});

describe("the briefing", () => {
  it("counts only unfinished requests as open and keeps 'to triage' and 'in workflow' apart", async () => {
    mount();
    await screen.findByText("2 open requests.");
    const line = document.querySelector(".oneline") as HTMLElement;
    expect(line).toHaveTextContent("1 to triage");
    expect(line).toHaveTextContent("1 in workflow");
  });

  it("does not call a finished request overdue even if its clock would have run out", async () => {
    // The server reports a closed request as on track, but a stale one still marked open + overdue is counted;
    // what matters here is that a closed row never reaches the overdue figure.
    h.intakeApi.list.mockResolvedValue([{ ...finished, sla_status: "overdue" }, waiting]);
    mount();
    await screen.findByText("1 open request.");
    expect(screen.queryByText(/1 overdue/)).toBeNull();
  });
});

describe("the Awaiting triage filter", () => {
  it("narrows the table to requests no workflow has started on", async () => {
    mount();
    await screen.findByText("Nobody has started this");
    expect(screen.getByText("Already in review")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Status/ }));
    fireEvent.click(screen.getByLabelText("Awaiting triage"));
    await waitFor(() => expect(screen.queryByText("Already in review")).toBeNull());
    expect(screen.getByText("Nobody has started this")).toBeInTheDocument();
    expect(within(document.querySelector("tbody") as HTMLElement).queryByText("All stages finished")).toBeNull();
  });
});


describe("a queue that could not be loaded", () => {
  it("says so, with the reason, instead of showing a healthy queue of zeros", async () => {
    h.intakeApi.list.mockRejectedValue(new HttpError(403, "Missing permission: intake:read"));
    mount();
    expect(await screen.findByRole("alert")).toHaveTextContent("You don't have access to the request queue.");
    expect(screen.getByRole("alert")).toHaveTextContent("Missing permission: intake:read");
    expect(screen.queryByText(/queue is healthy/)).toBeNull();
    expect(screen.queryByText(/Everything's assigned/)).toBeNull();
    expect(screen.queryByText("No requests match these filters.")).toBeNull();
  });

  it("offers Try again when trying again could help, and shows the queue once it loads", async () => {
    h.intakeApi.list.mockRejectedValueOnce(new HttpError(500, "Something broke")).mockResolvedValue([waiting]);
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Try again" }));
    await screen.findByText("1 open request.");
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("does not offer Try again when the server said no", async () => {
    h.intakeApi.list.mockRejectedValue(new HttpError(403, "Missing permission: intake:read"));
    mount();
    await screen.findByRole("alert");
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });

  it("shows a loading note, not zeros, while the queue is being fetched", async () => {
    h.intakeApi.list.mockReturnValue(new Promise(() => {}));
    mount();
    expect(await screen.findByText("Loading requests…")).toBeInTheDocument();
    expect(screen.queryByText(/open requests?\./)).toBeNull();
  });
});
