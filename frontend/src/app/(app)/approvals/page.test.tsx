import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import ApprovalsPage from "./page";
import type { ApprovalRequest } from "@/lib/types";

// The Approvals page: what it counts as "yours to action", how it names a row, and the Delegate /
// Escalate / Reject boxes (which say what they are for, and only offer people the server accepts).

const h = vi.hoisted(() => ({
  notify: vi.fn(),
  approvalsApi: { list: vi.fn(), decide: vi.fn(), reassign: vi.fn(), reassignCandidates: vi.fn(), eligibleApprovers: vi.fn() },
  contractsApi: { list: vi.fn() },
}));

vi.mock("@/lib/endpoints", () => ({ approvalsApi: h.approvalsApi, contractsApi: h.contractsApi }));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: "u-me", roles: ["approver"], active_role_name: "approver" } }) }));
vi.mock("@/components/toast", () => ({ useToast: () => ({ notify: h.notify }) }));
vi.mock("next/link", () => ({ default: (p: { href: string; children: ReactNode; className?: string }) => <a href={p.href} className={p.className}>{p.children}</a> }));

const row = (over: Partial<ApprovalRequest> = {}): ApprovalRequest => ({
  id: "a-1", org_id: "o", contract_id: "c-1", contract_title: "Acme MSA", contract_type: "MSA", contract_version_id: "v-1",
  status: "pending", requested_by_user_id: "u-req", approver_user_id: "u-me", approver_role: null, step_order: 1, mode: "any",
  approvals: 0, needed: 1, due_at: new Date(Date.now() + 3 * 86_400_000).toISOString(), overdue: false,
  metadata_json: {}, created_at: "2026-10-01T00:00:00Z", updated_at: "2026-10-01T00:00:00Z", can_decide: true, ...over,
});

function mount() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={qc}>
      <ApprovalsPage />
    </QueryClientProvider>,
  );
}

const person = (id: string, name: string) => ({ id, full_name: name, email: `${id}@example.com`, roles: ["approver"] });

beforeEach(() => {
  vi.resetAllMocks();
  h.approvalsApi.list.mockResolvedValue([row()]);
  h.approvalsApi.reassignCandidates.mockResolvedValue([person("u-1", "Gale Approver")]);
  h.contractsApi.list.mockResolvedValue([]);
});

describe("the header counts", () => {
  it("counts only rows still waiting as yours to action", async () => {
    h.approvalsApi.list.mockResolvedValue([
      row({ id: "a-1" }),
      row({ id: "a-2", status: "approved", can_decide: true }), // an administrator is "eligible" for old rows too
      row({ id: "a-3", status: "rejected", can_decide: true }),
      row({ id: "a-4", can_decide: false }),
    ]);
    mount();
    await waitFor(() => expect(screen.getByText("yours to action").parentElement).toHaveTextContent("1 yours to action"));
    expect(screen.getByText("pending").parentElement).toHaveTextContent("2 pending");
  });
});

describe("naming what a row is about", () => {
  it("links an approval on an intake request to that request instead of saying no contract is linked", async () => {
    h.approvalsApi.list.mockResolvedValue([
      row({ contract_id: null, contract_title: null, intake_request_id: "ir-9", intake_ref: "REQ-4123", intake_title: "Notice from the tax office" }),
    ]);
    mount();
    const link = await screen.findByRole("link", { name: "REQ-4123 · Notice from the tax office" });
    expect(link).toHaveAttribute("href", "/intake?open=ir-9");
    expect(screen.queryByText(/No contract linked/i)).toBeNull();
    expect(screen.getByRole("button", { name: "Approve" })).toBeInTheDocument();
  });

  it("says nothing is linked, and offers no buttons, for a row that points at nothing", async () => {
    h.approvalsApi.list.mockResolvedValue([row({ contract_id: null, contract_title: null, can_decide: false })]);
    mount();
    expect(await screen.findByText("Nothing linked to this approval")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
  });
});

describe("who a row is with", () => {
  it("names the person it is assigned to, from the row itself (the people list is not readable by every approver)", async () => {
    h.approvalsApi.list.mockResolvedValue([row({ approver_user_id: "u-gale", approver_user_name: "Gale Approver" })]);
    mount();
    expect(await screen.findByText("Gale Approver")).toBeInTheDocument();
    expect(screen.queryByText("Assigned approver")).toBeNull();
  });

  it("says a person is assigned when the row carries no name", async () => {
    h.approvalsApi.list.mockResolvedValue([row({ approver_user_id: "u-gale", approver_user_name: null })]);
    mount();
    expect(await screen.findByText("Assigned approver")).toBeInTheDocument();
  });

  it("names the team or the role when it is assigned to one", async () => {
    h.approvalsApi.list.mockResolvedValue([row({ approver_user_id: null, approver_team_id: "t-1", approver_team_name: "Finance" })]);
    mount();
    expect(await screen.findByText("Finance")).toBeInTheDocument();
  });
});

describe("when it is due", () => {
  it("shows how late an overdue row is next to the Overdue pill", async () => {
    const due = new Date(Date.now() - 3 * 3_600_000).toISOString();
    h.approvalsApi.list.mockResolvedValue([row({ due_at: due, overdue: true })]);
    mount();
    const cell = (await screen.findByText("Overdue")).closest("tr")!.querySelectorAll("td")[4];
    expect(cell).toHaveTextContent(/3h ago/);
  });

  it("shows the time for a row that is due later today instead of just a date", async () => {
    const due = new Date(); due.setHours(23, 59, 0, 0);
    if (due.getTime() < Date.now()) return; // test run after 23:59 local — nothing later today to show
    h.approvalsApi.list.mockResolvedValue([row({ due_at: due.toISOString(), overdue: false })]);
    mount();
    const cell = (await screen.findByRole("button", { name: "Approve" })).closest("tr")!.querySelectorAll("td")[4];
    expect(cell).toHaveTextContent(/^Today /);
    expect(screen.queryByText("Overdue")).toBeNull();
  });
});

describe("the boxes and messages say which contract", () => {
  it("names the contract in the success message", async () => {
    h.approvalsApi.decide.mockResolvedValue({});
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Approve" }));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith("Approved: Acme MSA", "success"));
  });

  it("names the contract and step in the Reject box", async () => {
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Reject" }));
    const box = (await screen.findByRole("heading", { name: "Reject approval" })).closest(".dlg") as HTMLElement;
    expect(box).toHaveTextContent("Acme MSA");
    expect(box).toHaveTextContent("step 1");
  });

  it("names the contract in the Delegate box and offers only the people the server returns", async () => {
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Delegate" }));
    const box = (await screen.findByRole("heading", { name: "Delegate approval" })).closest(".dlg") as HTMLElement;
    expect(box).toHaveTextContent("Acme MSA");
    await within(box).findByRole("option", { name: /Gale Approver/ });
    expect(h.approvalsApi.reassignCandidates).toHaveBeenCalledWith("a-1", "delegate");
    expect(h.approvalsApi.eligibleApprovers).not.toHaveBeenCalled(); // the old list of every user is gone
    expect(within(box).getAllByRole("option")).toHaveLength(2); // the placeholder + Gale
  });

  it("says who will be emailed once a person is chosen, and hands the approval over", async () => {
    h.approvalsApi.reassign.mockResolvedValue({});
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Delegate" }));
    const box = (await screen.findByRole("heading", { name: "Delegate approval" })).closest(".dlg") as HTMLElement;
    await within(box).findByRole("option", { name: /Gale Approver/ });
    fireEvent.change(within(box).getByLabelText("Delegate to"), { target: { value: "u-1" } });
    expect(box).toHaveTextContent("Gale Approver (u-1@example.com) will get an email");
    fireEvent.click(within(box).getByRole("button", { name: "Delegate" }));
    await waitFor(() => expect(h.approvalsApi.reassign).toHaveBeenCalledWith("a-1", "u-1", "delegate"));
    await waitFor(() => expect(h.notify).toHaveBeenCalledWith("Delegated: Acme MSA", "success"));
  });

  it("asks for senior approvers when escalating and says so when there are none", async () => {
    h.approvalsApi.reassignCandidates.mockResolvedValue([]);
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Escalate" }));
    const box = (await screen.findByRole("heading", { name: "Escalate approval" })).closest(".dlg") as HTMLElement;
    expect(await within(box).findByText(/No senior approver is available/)).toBeInTheDocument();
    expect(h.approvalsApi.reassignCandidates).toHaveBeenCalledWith("a-1", "escalate");
    expect(within(box).getByRole("button", { name: "Escalate" })).toBeDisabled();
  });
});
