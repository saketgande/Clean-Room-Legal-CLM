import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { DecisionArea, type HumanDecision } from "./_decision-area";

const human = (over: Partial<HumanDecision> = {}): HumanDecision => ({
  stepName: "Legal review", warning: { tone: "ok", text: "No blocking risks — clear to advance" },
  rework: null, reply: "", onReply: vi.fn(), replyMissing: false, busy: false, onApprove: vi.fn(),
  canSendBack: true, onJumpToSendBack: vi.fn(), onJumpToComment: vi.fn(), ...over,
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

  it("wires approve, send back and comment to the parent callbacks; hides send back on the first step", () => {
    const h = human();
    const { rerender } = render(<DecisionArea approval={null} human={h} negotiation={null} />);
    fireEvent.click(screen.getByRole("button", { name: /Approve & advance/ }));
    fireEvent.click(screen.getByRole("button", { name: /Send back for rework/ }));
    fireEvent.click(screen.getByRole("button", { name: /Add a comment/ }));
    expect(h.onApprove).toHaveBeenCalled(); expect(h.onJumpToSendBack).toHaveBeenCalled(); expect(h.onJumpToComment).toHaveBeenCalled();
    rerender(<DecisionArea approval={null} human={human({ canSendBack: false })} negotiation={null} />);
    expect(screen.queryByRole("button", { name: /Send back for rework/ })).toBeNull();
  });
});
