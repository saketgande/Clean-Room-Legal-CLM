import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { ProgressStrip, RequestHeader, StageTimeline } from "./_request-header";

const base = {
  title: "Acme MSA", refCode: "REQ-7705", requesterName: "Priya", raisedAt: "2026-10-01T10:00:00Z",
  ownerLabel: "Sam", highRisk: false, sla: null as string | null, onBack: () => {},
};

describe("RequestHeader", () => {
  it("shows the summary and both pills when high risk and overdue", () => {
    /** Guards FR-2: a true condition with no pill hides risk from the reader. */
    render(<RequestHeader {...base} highRisk sla="overdue" />);
    expect(screen.getByRole("heading", { name: "Acme MSA" })).toBeTruthy();
    expect(screen.getByText(/REQ-7705 · raised by Priya on .* · owner Sam/)).toBeTruthy();
    expect(screen.getByText("High risk")).toBeTruthy();
    expect(screen.getByText("Overdue")).toBeTruthy();
  });

  it("shows no pills when nothing is wrong, and falls back for missing names", () => {
    /** Guards FR-2: pills are only for true conditions; null owner must not print "null". */
    const { container } = render(<RequestHeader {...base} requesterName={null} raisedAt={null} ownerLabel={null} sla="on_track" />);
    expect(container.querySelector(".pill")).toBeNull();
    expect(screen.getByText("REQ-7705 · raised by the requester · owner unassigned")).toBeTruthy();
  });

  it("shows SLA at risk, and the crumb goes back", () => {
    /** Guards the at_risk pill and the back link wiring. */
    const onBack = vi.fn();
    render(<RequestHeader {...base} sla="at_risk" onBack={onBack} />);
    expect(screen.getByText("SLA at risk")).toBeTruthy();
    fireEvent.click(screen.getByText("← Legal Intake"));
    expect(onBack).toHaveBeenCalled();
  });
});

describe("ProgressStrip", () => {
  const strip = { steps: [], current: "review" as const, comments: [], extras: {}, line: "Review · Legal review · step 2 of 4", tone: "info" as const };

  it("renders the status line and the working indicator only when working", () => {
    /** Guards FR-3: the line must show, and "Agent working…" must not linger when idle. */
    const { rerender } = render(<ProgressStrip {...strip} working={false} />);
    expect(screen.getByText("Review · Legal review · step 2 of 4")).toBeTruthy();
    expect(screen.queryByText(/Agent working/)).toBeNull();
    rerender(<ProgressStrip {...strip} working />);
    expect(screen.getByText(/Agent working…/)).toBeTruthy();
  });

  it("shows the warn tone class on the line", () => {
    /** Guards FR-20: the warn tone must reach the line. */
    const { container } = render(<ProgressStrip {...strip} tone="warn" working={false} />);
    expect(container.querySelector(".stline.warn")).toBeTruthy();
  });

  it("lists the seven stages in the timeline, the current one open, and opens another on a click", () => {
    /** Guards the side timeline: all seven stages reachable, and a stage can be opened and closed by a person. */
    render(<StageTimeline steps={[]} current="review" comments={[]} extras={{}} />);
    expect(screen.getByText("2 of 7 stages complete")).toBeTruthy();
    const stages = screen.getAllByRole("button", { name: /^(Intake|Drafting|Review|Approval|Signature|Active|Closed):/ });
    expect(stages).toHaveLength(7);
    expect(stages.map((b) => b.getAttribute("aria-expanded"))).toEqual(["false", "false", "true", "false", "false", "false", "false"]);
    fireEvent.click(stages[0]);
    expect(screen.getAllByRole("button", { name: /^Intake:/ })[0].getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(stages[0]);
    expect(screen.getAllByRole("button", { name: /^Intake:/ })[0].getAttribute("aria-expanded")).toBe("false");
  });
});
