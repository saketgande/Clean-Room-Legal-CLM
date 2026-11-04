import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { DecisionArea, type HumanDecision } from "./_decision-area";

const human = (over: Partial<HumanDecision> = {}): HumanDecision => ({
  stepName: "Legal review", warning: { tone: "ok", text: "No blocking risks — clear to advance" },
  rework: null, reply: "", onReply: vi.fn(), replyMissing: false, busy: false, onApprove: vi.fn(),
  sendBack: { options: [{ value: "0", label: "1. Draft" }], to: "", onTo: vi.fn(), note: "", onNote: vi.fn(), open: false, onToggle: vi.fn(), onSend: vi.fn() }, risks: [], onSeeRisks: vi.fn(), note: "", onNote: vi.fn(), ...over,
});

describe("DecisionArea", () => {
  it("renders nothing when nothing is pending and the viewer has no step (an empty block would take space)", () => {
    const { container } = render(<DecisionArea approval={null} human={null} negotiation={null} />);
    expect(container.firstChild).toBeNull();
  });

  it("puts the approval card before the human-step card (the pending approval must be the first thing seen)", () => {
    render(<DecisionArea approval={<div data-testid="apc">card</div>} human={human()} negotiation={<div data-testid="neg" />} />);
    const sec = screen.getByLabelText("Decision");
    const kids = Array.from(sec.children);
    expect(kids[0]).toBe(screen.getByTestId("apc"));
    expect(kids[1].textContent).toContain("Your decision");
    expect(kids[2]).toBe(screen.getByTestId("neg"));
  });

  it("shows a read-only approval node alone, with no human-step buttons (non-decider sees waiting state only)", () => {
    render(<DecisionArea approval={<div>Waiting on Finance team</div>} human={null} negotiation={null} />);
    expect(screen.getByText("Waiting on Finance team")).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("blocks Approve until the rework reply is typed, and shows the rejection reason", () => {
    const onReply = vi.fn();
    render(<DecisionArea approval={null} human={human({ rework: { reason: "Cap too low" }, replyMissing: true, onReply })} negotiation={null} />);
    expect(screen.getByText(/Cap too low/)).toBeTruthy();
    expect((screen.getByRole("button", { name: /Approve & advance/ }) as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "fixed" } });
    expect(onReply).toHaveBeenCalledWith("fixed");
  });

  it("wires approve, send back and the decision note to the parent callbacks; hides send back on the first step", () => {
    const h = human();
    const { rerender } = render(<DecisionArea approval={null} human={h} negotiation={null} />);
    fireEvent.click(screen.getByRole("button", { name: /Approve & advance/ }));
    fireEvent.click(screen.getByRole("button", { name: /Send back for rework/ }));
    fireEvent.change(screen.getByLabelText(/Decision note/), { target: { value: "checked cl. 4" } });
    expect(h.onApprove).toHaveBeenCalled(); expect(h.sendBack!.onToggle).toHaveBeenCalled(); expect(h.onNote).toHaveBeenCalledWith("checked cl. 4");
    rerender(<DecisionArea approval={null} human={human({ sendBack: null })} negotiation={null} />);
    expect(screen.queryByRole("button", { name: /Send back for rework/ })).toBeNull();
  });

  it("lists the open high risks in the card and offers the full list", () => {
    const h = human({ warning: { tone: "warn", text: "1 high risk open — review before approving" }, risks: [{ id: "d1", severity: "high", clause: "limitation of liability", issue: "Cap is uncapped" }] });
    render(<DecisionArea approval={null} human={h} negotiation={null} />);
    expect(screen.getByText("Cap is uncapped")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Limitation of liability/i }));
    expect(h.onSeeRisks).toHaveBeenCalled();
  });

  it("lets the person hand the work to someone in the step's own team", () => {
    const onConfirm = vi.fn(); const onPick = vi.fn();
    const reassign = { teamName: "Legal Counsel", options: [{ id: "u2", name: "Asha Rao", detail: "2 open" }], open: true, onToggle: vi.fn(), picked: "u2", onPick, onConfirm };
    render(<DecisionArea approval={null} human={human({ reassign })} negotiation={null} />);
    expect(screen.getByText(/someone in Legal Counsel/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("radio", { name: /Asha Rao/ }));
    fireEvent.click(screen.getByRole("button", { name: "Reassign to selected person" }));
    expect(onPick).toHaveBeenCalledWith("u2"); expect(onConfirm).toHaveBeenCalled();
  });
});
