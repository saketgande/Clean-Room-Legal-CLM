import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { createRef } from "react";
import { ApprovalChain, WorkflowCard, type FlowState, type StepRow } from "./_request-steps";
import type { IntakeApprovalRung } from "@/lib/types";

const row = (over: Partial<StepRow> = {}): StepRow => ({ label: "Legal review", tone: "todo", node: "1", badgeTone: "cur", detail: [], ...over });
const base = { rows: [row()], countText: null, flow: { kind: "running", name: "NDA flow", status: "running" } as FlowState, error: null, check: null, extras: [], trace: [], sendBack: null };
const show = (over: Partial<Parameters<typeof WorkflowCard>[0]> = {}) => render(<WorkflowCard {...base} {...over} />);
const rung: IntakeApprovalRung = {
  approval_request_id: "a1", step_order: 1, status: "rejected", approver_label: "Legal team", due_at: null, mode: "all", approvals: 1, needed: 2, can_decide: false,
  decisions: [{ approver_user_id: "u", approver_name: "Priya", decision: "reject", comment: "Too risky", decided_at: "2026-10-01T10:00:00Z" } as never],
};
const sendBackProps = (over: object = {}) => ({ options: [{ value: "0", label: "1. Draft" }], to: "", note: "", onTo: vi.fn(), onNote: vi.fn(), onSend: vi.fn(), busy: false, selectRef: createRef<HTMLSelectElement>(), open: false, onToggle: vi.fn(), ...over });
const six = (): StepRow[] => [
  row({ label: "Draft", tone: "done", node: "✓", assign: "Ann", badgeTone: "done" }),
  row({ label: "Review", tone: "done", node: "✓", assign: "Bo", badgeTone: "done" }),
  row({ label: "Legal", tone: "done", node: "✓", badgeTone: "done" }),
  row({ label: "Approval", tone: "cur", node: "4", badge: "here now", focus: true, wait: { who: "EY-Dev legal approval team", extra: null, due: "Overdue 2 days", overdue: true } }),
  row({ label: "Sign", node: "5", next: true }),
  row({ label: "File", node: "6" }),
];

describe("WorkflowCard", () => {
  it("draws one tile per step, the open one highlighted with who and the check action", () => {
    /* Guards the six-step order collapsing back into a tall list, or the open step losing who it waits on. */
    const { container } = show({ rows: six(), countText: "3 of 6 steps done", check: { label: "Check approval status", onClick: vi.fn(), busy: false } });
    expect(screen.getByText("3 of 6 steps done")).toBeTruthy();
    expect(container.querySelectorAll(".wft .t")).toHaveLength(6);
    expect(container.querySelectorAll(".wft .t.done")).toHaveLength(3);
    const cur = container.querySelector(".wft .t.cur") as HTMLElement;
    expect(cur.textContent).toContain("With EY-Dev legal approval team");
    expect(cur.querySelector(".over")?.textContent).toBe("Overdue 2 days");
    expect(cur.querySelector("button")?.textContent).toBe("Check approval status");
    const todo = container.querySelectorAll(".wft .t.todo");
    expect(todo).toHaveLength(2);
    expect(todo[0].textContent).toContain("Next");
    expect(todo[1].textContent).not.toContain("Next");
  });
  it("marks skipped and failed steps without counting them as done", () => {
    /* Guards a skipped or failed step looking finished. */
    const { container } = show({ rows: [row({ label: "Sign", skipped: true, tone: "done", node: "–", badgeTone: "skip" }), row({ label: "Review", tone: "warn", node: "!", badge: "failed", badgeTone: "warn" })], countText: "0 of 2 steps done, 1 skipped" });
    expect(container.querySelector(".t.skip")?.textContent).toContain("Skipped");
    expect(container.querySelector(".t.warn")?.textContent).toContain("failed");
    expect(container.querySelector(".t.done")).toBeNull();
  });
  it("highlights every open parallel step", () => {
    /* Guards only one of two parallel open steps being shown as current. */
    const { container } = show({ rows: [row({ tone: "cur", label: "A", badge: "here now" }), row({ tone: "cur", label: "B", node: "2", badge: "here now" })] });
    expect(container.querySelectorAll(".t.cur")).toHaveLength(2);
  });
  it("shows the doorway link only on the focus tile", () => {
    /* Guards the doorway repeating on every tile, or going missing from the open one. */
    show({ rows: [row({ tone: "cur", focus: true, badge: "here now", doorway: { href: "/contracts/c1", label: "Open the draft" } }), row({ node: "2", label: "Other" })] });
    expect(screen.getAllByRole("link", { name: "Open the draft" })).toHaveLength(1);
  });
  it("hides the review result until opened", () => {
    /* Guards the result list being always on or never reachable. */
    show({ rows: [row({ sidx: 2, detail: [["Risk", "High · Liability is unlimited"]] })] });
    expect(screen.queryByText("High · Liability is unlimited")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Review result/ }));
    expect(screen.getByText("High · Liability is unlimited")).toBeTruthy();
    expect(screen.queryByText(/deviation_id|\{/)).toBeNull();
  });
  it("keeps the approval chain with its decisions behind its link", () => {
    /* Guards spec 009's per-rung decisions and comments being lost when the chain moved into the card. */
    show({ extras: [{ id: "approval-chain", label: "Approval chain", node: <ApprovalChain rungs={[rung]} /> }] });
    expect(screen.queryByText("Legal team")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Approval chain/ }));
    expect(screen.getByText(/step 1 · 1\/2/)).toBeTruthy();
    expect(screen.getByText(/Priya · rejected · .*“Too risky”/)).toBeTruthy();
  });
  it("collapses the hand-off trace and uses plain step names", () => {
    /* Guards internal step names ("Clm Draft") and agent names leaking into the trace. */
    show({ trace: [{ step: "Clm Draft", type: "clm_draft", agent: "drafting_agent", summary: "Drafted it" }] });
    expect(screen.queryByText(/Drafted it/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Agent hand-off trace (1)" }));
    expect(screen.getByText(/Drafted it/)).toBeTruthy();
    expect(screen.queryByText(/Clm Draft/)).toBeNull();
  });
  it("shows no links or buttons when there is nothing to open", () => {
    /* Guards empty Review result / trace / send-back links for viewers who cannot act. */
    show();
    expect(screen.queryAllByRole("button")).toHaveLength(0);
    expect(screen.queryAllByRole("link")).toHaveLength(0);
  });
  it("opens the send-back form from the parent's state and keeps Send back disabled until a step is picked", () => {
    /* Guards the decision area being unable to open the form, and a rework request with no target step. */
    const onToggle = vi.fn();
    const { rerender } = show({ sendBack: sendBackProps({ onToggle }) });
    expect(screen.queryByLabelText("Step to send back to")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Send back for rework/ }));
    expect(onToggle).toHaveBeenCalled();
    const onSend = vi.fn();
    rerender(<WorkflowCard {...base} sendBack={sendBackProps({ open: true, onSend })} />);
    expect((screen.getByRole("button", { name: /^Send back$/ }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText(/Pick the step to return to, then/)).toBeTruthy();
    rerender(<WorkflowCard {...base} sendBack={sendBackProps({ open: true, onSend, to: "0" })} />);
    fireEvent.click(screen.getByRole("button", { name: /^Send back$/ }));
    expect(onSend).toHaveBeenCalled();
  });
  it("renders a step's review notice under the tiles with its step name", () => {
    /* Guards the interrupted-review notice and its Retry vanishing from the card. */
    show({ rows: [row({ label: "AI review", notice: <span>Review was interrupted</span> })] });
    expect(screen.getByText("Review was interrupted")).toBeTruthy();
  });
  it("offers Start it only when allowed, as one button", () => {
    /* Guards the Start it button appearing for people who cannot start. */
    const onStart = vi.fn();
    const f = { kind: "suggested", name: "NDA flow", reasoning: "Matches", canStart: true, starting: false, onStart } as FlowState;
    const { unmount } = show({ flow: f });
    fireEvent.click(screen.getByRole("button", { name: "Start it" }));
    expect(onStart).toHaveBeenCalledTimes(1);
    unmount();
    show({ flow: { ...f, canStart: false } as FlowState });
    expect(screen.queryByRole("button", { name: "Start it" })).toBeNull();
  });
  it("covers setting up, none and empty states", () => {
    /* Guards a second workflow being offered while one is being set up, and a blank card with no steps. */
    const { unmount } = show({ rows: [], flow: { kind: "setting_up" } });
    expect(screen.getByText("Setting up the workflow…")).toBeTruthy();
    expect(screen.getByText("No steps yet.")).toBeTruthy();
    unmount();
    const none = { kind: "none", reasoning: null, canPick: false, flows: [{ id: "f", name: "F" }], picking: false, onPick: vi.fn() } as FlowState;
    const { unmount: u2 } = show({ flow: none });
    expect(screen.getByText("Pick one to start it.")).toBeTruthy();
    expect(screen.queryByLabelText("Pick a workflow")).toBeNull();
    u2();
    show({ flow: { ...none, canPick: true } as FlowState });
    expect(screen.getByLabelText("Pick a workflow")).toBeTruthy();
  });
});
