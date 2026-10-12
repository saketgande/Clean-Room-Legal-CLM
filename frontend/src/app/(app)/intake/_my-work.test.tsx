import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MyWorkBoard } from "./_my-work";
import type { ApprovalRequest, IntakeMyWork, IntakeTask } from "@/lib/types";

// My Work is the page that answers "what is waiting on me?". Approvals waiting on the user belong on it:
// it used to show "0 tasks" while three approvals sat in the user's queue.

const h = vi.hoisted(() => ({
  intakeApi: { myWork: vi.fn(), mine: vi.fn(), updateTask: vi.fn() },
  approvalsApi: { list: vi.fn() },
}));

vi.mock("@/lib/endpoints", () => ({ intakeApi: h.intakeApi, approvalsApi: h.approvalsApi }));
vi.mock("next/link", () => ({ default: (p: { href: string; children: ReactNode; className?: string }) => <a href={p.href} className={p.className}>{p.children}</a> }));

const approval = (over: Partial<ApprovalRequest> = {}): ApprovalRequest => ({
  id: "a-1", org_id: "o", contract_id: "c-1", contract_title: "Acme MSA", contract_type: "MSA", contract_version_id: "v",
  status: "pending", requested_by_user_id: "u-req", approver_user_id: "u-me", approver_role: null, step_order: 1,
  due_at: new Date(Date.now() + 2 * 86_400_000).toISOString(), overdue: false, metadata_json: {},
  created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z", can_decide: true, ...over,
});

const task = (over: Partial<IntakeTask> = {}): IntakeTask => ({
  id: "t-1", request_id: "q-1", title: "Collect the signed NDA", description: null, assignee_user_id: "u-me",
  assignee_label: "Me", status: "open", sort_order: 1, effort_minutes: 0, ...over,
});

function mount(onOpen = vi.fn()) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={qc}>
      <MyWorkBoard onOpen={onOpen} />
    </QueryClientProvider>,
  );
  return { qc, onOpen };
}

const tile = (label: string) => screen.getByText(label).parentElement as HTMLElement;

beforeEach(() => {
  vi.resetAllMocks();
  h.intakeApi.myWork.mockResolvedValue({ awaiting_review: [], my_tickets: [], my_tasks: [] } as IntakeMyWork);
  h.intakeApi.mine.mockResolvedValue([]);
  h.approvalsApi.list.mockResolvedValue([]);
});

describe("approvals waiting on the user", () => {
  it("are on the desk: counted, with a card that goes to the Approvals page", async () => {
    h.approvalsApi.list.mockResolvedValue([approval(), approval({ id: "a-2", contract_title: "Beta NDA" })]);
    mount();
    expect(await screen.findByText("Acme MSA")).toBeInTheDocument();
    expect(screen.getByText("Beta NDA")).toBeInTheDocument();
    expect(tile("approvals")).toHaveTextContent("2");
    expect(tile("on your desk")).toHaveTextContent("2");
    const card = screen.getByText("Acme MSA").closest(".tcard") as HTMLElement;
    expect(within(card).getByRole("link", { name: "Review" })).toHaveAttribute("href", "/approvals");
    expect(screen.queryByText(/inbox zero/)).toBeNull();
  });

  it("count towards the overdue figure when they are overdue", async () => {
    h.approvalsApi.list.mockResolvedValue([approval({ overdue: true, due_at: new Date(Date.now() - 3_600_000).toISOString() }), approval({ id: "a-2" })]);
    mount();
    await screen.findAllByText("Waiting for your approval");
    expect(tile("overdue")).toHaveTextContent("1");
    expect(screen.getByText("1 overdue")).toBeInTheDocument();
  });

  it("leaves out rows already decided and rows the user may not decide", async () => {
    h.approvalsApi.list.mockResolvedValue([
      approval(),
      approval({ id: "a-2", status: "approved", contract_title: "Done deal" }),
      approval({ id: "a-3", can_decide: false, contract_title: "Someone else's" }),
    ]);
    mount();
    await screen.findByText("Acme MSA");
    expect(screen.queryByText("Done deal")).toBeNull();
    expect(screen.queryByText("Someone else's")).toBeNull();
    expect(tile("approvals")).toHaveTextContent("1");
  });

  it("names an approval on an intake request by its reference and title", async () => {
    h.approvalsApi.list.mockResolvedValue([
      approval({ contract_id: null, contract_title: null, intake_request_id: "ir-1", intake_ref: "REQ-4123", intake_title: "Tax notice" }),
    ]);
    mount();
    expect(await screen.findByText("REQ-4123 · Tax notice")).toBeInTheDocument();
  });

  it("do not break the page when they cannot be loaded", async () => {
    h.approvalsApi.list.mockRejectedValue(new Error("Missing permission: approval:read"));
    h.intakeApi.myWork.mockResolvedValue({ awaiting_review: [], my_tickets: [], my_tasks: [task()] });
    mount();
    expect(await screen.findByText("Collect the signed NDA")).toBeInTheDocument();
    expect(tile("approvals")).toHaveTextContent("0");
  });

  it("only say 'nothing on your desk' when there are no approvals either", async () => {
    mount();
    expect(await screen.findByText(/Nothing on your desk/)).toBeInTheDocument();
  });
});

describe("tasks", () => {
  it("'Mark done' marks the task done and refreshes the list", async () => {
    h.intakeApi.myWork.mockResolvedValue({ awaiting_review: [], my_tickets: [], my_tasks: [task()] });
    h.intakeApi.updateTask.mockResolvedValue(task({ status: "done" }));
    const { qc } = mount();
    const refetch = vi.spyOn(qc, "invalidateQueries");
    fireEvent.click(await screen.findByRole("button", { name: "Mark done" }));
    await waitFor(() => expect(h.intakeApi.updateTask).toHaveBeenCalledWith("t-1", { status: "done" }));
    await waitFor(() => expect(refetch).toHaveBeenCalledWith(expect.objectContaining({ queryKey: ["intake-mywork"] })));
  });

  it("shows a finished task as Done and does nothing when pressed", async () => {
    h.intakeApi.myWork.mockResolvedValue({ awaiting_review: [], my_tickets: [], my_tasks: [task({ status: "done" })] });
    mount();
    const button = await screen.findByRole("button", { name: "Done" });
    expect(button).toBeDisabled();
  });
});

describe("requests you raised", () => {
  const raised = (over: Record<string, unknown> = {}) =>
    ({
      id: "q-1", ref: "REQ-7013", subject: "NDA — Initech", description: "", type_label: "New agreement · NDA",
      status: "open", sla_status: "on_track", workflow: [], field_values: {}, contract_id: null, ...over,
    }) as unknown as import("@/lib/types").IntakeRequest;
  const row = async (ref: string) => (await screen.findByText(ref)).closest("tr") as HTMLElement;

  it("says 'awaiting triage' and 'not started' while nobody has begun", async () => {
    h.intakeApi.mine.mockResolvedValue([raised()]);
    mount();
    const r = await row("REQ-7013");
    expect(within(r).getByText("awaiting triage")).toBeInTheDocument();
    expect(within(r).getByText("not started")).toBeInTheDocument();
  });

  it("says 'in progress' once a person has drafted the contract, even with no workflow", async () => {
    h.intakeApi.mine.mockResolvedValue([raised({ contract_id: "c-1" })]);
    mount();
    const r = await row("REQ-7013");
    expect(within(r).getByText("in progress")).toBeInTheDocument();
    expect(within(r).getByText("contract drafted")).toBeInTheDocument();
    expect(within(r).queryByText("awaiting triage")).toBeNull();
  });
});
