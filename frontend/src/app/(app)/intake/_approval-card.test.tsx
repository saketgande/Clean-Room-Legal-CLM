import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApprovalCard } from "./_approval-card";
import { HttpError } from "@/lib/api";
import type { ApprovalSummary, IntakeApprovalRung } from "@/lib/types";

const h = vi.hoisted(() => ({
  notify: vi.fn(),
  intakeApi: { approvalSummary: vi.fn() },
  approvalsApi: { decide: vi.fn() },
  workflowsApi: { refreshRun: vi.fn() },
}));
vi.mock("@/lib/endpoints", () => ({ intakeApi: h.intakeApi, approvalsApi: h.approvalsApi, workflowsApi: h.workflowsApi }));
vi.mock("@/components/toast", () => ({ useToast: () => ({ notify: h.notify }) }));

const rung = (over: Partial<IntakeApprovalRung> = {}): IntakeApprovalRung => ({
  approval_request_id: "a-1", step_order: 1, status: "pending", approver_label: "Finance team", due_at: null,
  can_decide: true, decisions: [], ...over,
});
const summary = (crit = 0, high = 0): ApprovalSummary => ({
  request_id: "q-1", contract_id: "c-1", approval_request_id: "a-1", profile: "finance",
  fields: [
    { key: "value", label: "Value", value: "INR 5,00,000", source: "verified", quote: false },
    { key: "payment_terms", label: "Payment terms", value: null, source: null, quote: false },
    { key: "liability", label: "Liability", value: "Capped at fees", source: "extracted", quote: true },
  ],
  open_risk: { critical: crit, high },
});

function mount(rungs: IntakeApprovalRung[], runId: string | null = "run-1", extra: { stepName?: string; subject?: string } = {}) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const invalidate = vi.spyOn(qc, "invalidateQueries");
  render(<QueryClientProvider client={qc}><ApprovalCard requestId="q-1" rungs={rungs} runId={runId} {...extra} /></QueryClientProvider>);
  return { qc, invalidate };
}

beforeEach(() => {
  vi.resetAllMocks();
  h.intakeApi.approvalSummary.mockResolvedValue(summary());
  h.approvalsApi.decide.mockResolvedValue({});
  h.workflowsApi.refreshRun.mockResolvedValue({ id: "run-1" });
});

describe("the approval card", () => {
  it("shows facts with source tags and 'Not available' for a missing one, never a guess", async () => {
    // Failure mode: a missing fact rendered blank or defaulted, so an approver signs off on a made-up figure (FR-6).
    mount([rung()]);
    expect(await screen.findByText("INR 5,00,000")).toBeInTheDocument();
    expect(screen.getByText("verified")).toBeInTheDocument();
    expect(screen.getByText("extracted")).toBeInTheDocument();
    expect(screen.getByText("Not available")).toBeInTheDocument();
    expect(screen.getByText(/“Capped at fees”/)).toBeInTheDocument();
  });

  it("gives a quoted fact the whole row, so a long clause is not squeezed into a narrow column", async () => {
    // Failure mode: a long quote (top issues) wrapped one word per line in a ~140px grid column.
    mount([rung()]);
    const quote = (await screen.findByText(/Capped at fees/)).closest(".f") as HTMLElement;
    expect(quote.style.gridColumn).toBe("1 / -1");
    expect((screen.getByText("INR 5,00,000").closest(".f") as HTMLElement).style.gridColumn).toBe("");
  });

  it("gives a long non-quote fact (top issues) the whole row while short ones keep their column", async () => {
    // Failure mode: the backend does not flag top_issues as a quote, so it sat in a 172px column wrapping a few words per line.
    const long = "Liability is uncapped for data breaches. Termination for convenience is missing. Governing law is silent on venue. ".repeat(2).slice(0, 200);
    h.intakeApi.approvalSummary.mockResolvedValue({
      ...summary(),
      fields: [
        { key: "top_issues", label: "Top issues", value: long, source: null, quote: false },
        { key: "overall_risk", label: "Overall risk", value: "High", source: null, quote: false },
        { key: "payment_terms", label: "Payment terms", value: null, source: null, quote: false },
      ],
    });
    mount([rung()]);
    expect(((await screen.findByText(long)).closest(".f") as HTMLElement).style.gridColumn).toBe("1 / -1");
    expect((screen.getByText("High").closest(".f") as HTMLElement).style.gridColumn).toBe("");
    expect((screen.getByText("Not available").closest(".f") as HTMLElement).style.gridColumn).toBe("");
  });

  it("is read-only for someone who cannot decide (FR-2)", async () => {
    // Failure mode: Approve/Reject offered to the requester or a non-assigned viewer.
    mount([rung({ can_decide: false })]);
    expect(await screen.findByText("Waiting on Finance team")).toBeInTheDocument();
    expect(screen.getAllByText(/Waiting on/)).toHaveLength(1);
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Reject" })).toBeNull();
  });

  it("renders nothing when no rung is pending or decidable", () => {
    // Failure mode: an empty Approval card shown on a request with no active rung (FR-23 after deciding).
    mount([rung({ status: "approved", can_decide: false })]);
    expect(document.querySelector(".apc")).toBeNull();
  });

  it("shows no checkbox when no critical/high risk is open and approves without acknowledgement (FR-20)", async () => {
    // Failure mode: a needless acknowledgement demanded, or ack=true sent when there was nothing to acknowledge.
    mount([rung()]);
    expect(await screen.findByText("No critical or high risks open")).toBeInTheDocument();
    expect(screen.queryByRole("checkbox")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(h.approvalsApi.decide).toHaveBeenCalledWith("a-1", "approve", undefined, false));
  });

  it("keeps Approve disabled until the open risks are acknowledged, then sends the acknowledgement (FR-18)", async () => {
    // Failure mode: approving past open critical risks with no explicit acknowledgement.
    h.intakeApi.approvalSummary.mockResolvedValue(summary(1, 2));
    mount([rung()]);
    expect(await screen.findByText((_, el) => el?.textContent === "1 critical + 2 high risks open" && el.className === "dw")).toBeInTheDocument();
    const approve = screen.getByRole("button", { name: "Approve" });
    expect(approve).toBeDisabled();
    fireEvent.click(screen.getByRole("checkbox"));
    expect(approve).toBeEnabled();
    fireEvent.click(approve);
    await waitFor(() => expect(h.approvalsApi.decide).toHaveBeenCalledWith("a-1", "approve", undefined, true));
  });

  it("refuses Reject with a blank comment without calling the server (FR-17)", async () => {
    // Failure mode: a reject with no reason reaching the server or, worse, being recorded.
    mount([rung()]);
    await screen.findByText("INR 5,00,000");
    fireEvent.change(screen.getByLabelText("Comment (required to reject)"), { target: { value: "   " } });
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(screen.getByText("Rejection requires a comment")).toBeInTheDocument();
    expect(h.approvalsApi.decide).not.toHaveBeenCalled();
  });

  it("on success refreshes the chain, the request and the workflow run (FR-22, FR-24)", async () => {
    // Failure mode: the page keeps showing the old chain, or a no-contract request waits for the periodic check.
    const { qc, invalidate } = mount([rung()]);
    await screen.findByText("INR 5,00,000");
    fireEvent.change(screen.getByLabelText("Comment (required to reject)"), { target: { value: " ok " } });
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(h.workflowsApi.refreshRun).toHaveBeenCalledWith("run-1"));
    expect(h.approvalsApi.decide).toHaveBeenCalledWith("a-1", "approve", "ok", false);
    const keys = invalidate.mock.calls.map((c) => (c[0] as { queryKey: unknown[] }).queryKey[0]);
    expect(keys).toEqual(expect.arrayContaining(["intake-approval-chain", "intake-request", "flow-run", "approvals", "contract-risk", "intake-approval-summary"]));
    await waitFor(() => expect(qc.getQueryData(["flow-run", "q-1"])).toEqual({ id: "run-1" }));
    expect(h.notify).toHaveBeenCalledWith("Approved", "success");
  });

  it("shows a server refusal inline, keeps the comment and re-reads the chain (409)", async () => {
    // Failure mode: a repeat/lost-race decision failing silently or wiping what the approver typed.
    h.approvalsApi.decide.mockRejectedValue(new HttpError(409, "Already decided"));
    const { invalidate } = mount([rung()]);
    await screen.findByText("INR 5,00,000");
    const box = screen.getByLabelText("Comment (required to reject)") as HTMLTextAreaElement;
    fireEvent.change(box, { target: { value: "no way" } });
    fireEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Already decided");
    expect(box.value).toBe("no way");
    expect(h.notify).toHaveBeenCalledWith("Already decided", "error");
    expect(invalidate.mock.calls.map((c) => (c[0] as { queryKey: unknown[] }).queryKey[0])).toContain("intake-approval-chain");
  });

  it("disables both buttons while a decision is in flight (AC-29)", async () => {
    // Failure mode: a double click submitting two decisions.
    h.approvalsApi.decide.mockReturnValue(new Promise(() => {}));
    mount([rung()]);
    await screen.findByText("INR 5,00,000");
    fireEvent.click(screen.getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled());
    expect(screen.getByRole("button", { name: "Reject" })).toBeDisabled();
  });

  it("names the step and its place in a multi-rung chain, and the subject", async () => {
    // Failure mode: the header said only "Approval", so the approver could not tell which approval they were giving.
    mount([rung({ step_order: 2 }), rung({ step_order: 1, can_decide: false, approval_request_id: "a-0" }), rung({ step_order: 3, can_decide: false, approval_request_id: "a-2" })],
      "run-1", { stepName: "Legal Approval", subject: "Mutual NDA · Initech Software Ltd" });
    expect(await screen.findByRole("heading", { name: "Legal Approval" })).toBeInTheDocument();
    expect(screen.getByText("Approval step 2 of 3")).toBeInTheDocument();
    expect(screen.getByText("Mutual NDA · Initech Software Ltd")).toBeInTheDocument();
  });

  it("shows no step line for a single rung and falls back to 'Approval' without a name", async () => {
    // Failure mode: "Approval step 1 of 1" noise, or an empty title when the parent has no step name.
    mount([rung()]);
    expect(await screen.findByRole("heading", { name: "Approval" })).toBeInTheDocument();
    expect(screen.queryByText(/Approval step/)).toBeNull();
  });

  it("chips 'Waiting for you' for the decider and keeps the view name out of the header text", async () => {
    // Failure mode: internal wording ('General view') shown to approvers, or a read-only chip shown to the decider.
    mount([rung()]);
    expect(await screen.findByText("Waiting for you")).toBeInTheDocument();
    await screen.findByText("INR 5,00,000");
    const head = document.querySelector(".apc .ch") as HTMLElement;
    expect(head.textContent).not.toMatch(/view/i);
    expect(head.getAttribute("title")).toBe("Finance view");
  });

  it("clamps a 400-character fact behind Show more / Show less, and gives a short fact no toggle", async () => {
    // Failure mode: a long finding cut mid-sentence with no way to read the rest, or a toggle on text that fits.
    const long = "Term is two years with auto-renewal and no exit. ".repeat(9).trim();
    h.intakeApi.approvalSummary.mockResolvedValue({ ...summary(), fields: [
      { key: "top_issues", label: "Top issues", value: long, source: null, quote: false },
      { key: "overall_risk", label: "Overall risk", value: "High", source: null, quote: false },
    ] });
    mount([rung()]);
    const v = (await screen.findByText(long)) as HTMLElement;
    expect(v.className).toContain("clamp");
    expect(screen.getAllByRole("button", { name: /Show/ })).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Show more" }));
    expect(v.className).not.toContain("clamp");
    expect(screen.getByRole("button", { name: "Show less" })).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(screen.getByRole("button", { name: "Show less" }));
    expect(v.className).toContain("clamp");
  });
});
