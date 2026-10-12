import { describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import { NextStepCard } from "./lifecycle-view";
import type { IntakeTeam, WorkflowRun, WorkflowRunStep } from "@/lib/types";

// "The step the work is waiting on": for an approval step it must say who the live approval is with and its
// own due date, not the step's planned team and "started + SLA hours" (which Delegate / Escalate never touch).

const approvalStep = (over: Partial<WorkflowRunStep> = {}): WorkflowRunStep => ({
  idx: 0, type: "approval", name: "Finance approval", status: "waiting_job",
  assignee_user_id: null, assignee_label: null, team_id: null, team_label: null, planned_team_label: "Finance",
  note: null, result: null, updated_at: new Date(Date.now() - 3_600_000).toISOString(), sla_hours: 24, stage: "approval", ...over,
});

const run = (step: WorkflowRunStep): WorkflowRun => ({
  id: "r", request_id: "q", flow_id: "f", flow_name: "MSA", status: "waiting", current_index: 0, contract_id: "c",
  error: null, steps: [step], comments: [],
});

const finance = { id: "t-1", name: "Finance", members: [{ active: true, name: "Gale Approver", user_id: "u-1" }] } as unknown as IntakeTeam;

describe("NextStepCard on a step with no named owner", () => {
  const sigStep = (over: Partial<WorkflowRunStep> = {}): WorkflowRunStep =>
    approvalStep({ type: "signature", name: "Internal signature", stage: "signature", planned_team_label: null, sla_hours: null, ...over });

  it("says it is waiting for the signers instead of printing a bare dash", () => {
    render(<NextStepCard run={run(sigStep())} />);
    expect(screen.getByText(/Waiting for the signers\./)).toBeInTheDocument();
    expect(screen.queryByText(/^—\.$/)).toBeNull();
  });

  it("prints nothing for the owner of a step that has none, rather than a dash", () => {
    const { container } = render(<NextStepCard run={run(sigStep({ type: "notify", name: "Tell the team" }))} />);
    expect(container.textContent).not.toContain("—.");
  });
});

describe("NextStepCard on an approval step", () => {
  it("names the person the approval was handed to, not the planned team", () => {
    const due = new Date(Date.now() + 5 * 86_400_000).toISOString();
    render(<NextStepCard run={run(approvalStep({ approval: { approver_label: "Casey Delegate", step_order: 1, due_at: due, overdue: false } }))} teams={[finance]} />);
    expect(screen.getByText("Casey Delegate")).toBeInTheDocument();
    expect(screen.queryByText(/Gale Approver/)).toBeNull();
    expect(screen.queryByText(/team —/)).toBeNull();
  });

  it("uses the approval's own due date, not the step's SLA", () => {
    const due = new Date(Date.now() + 5 * 86_400_000);
    render(<NextStepCard run={run(approvalStep({ approval: { approver_label: "Casey Delegate", step_order: 1, due_at: due.toISOString(), overdue: false } }))} />);
    const day = due.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
    expect(screen.getByText(`Due ${day}.`)).toBeInTheDocument();
  });

  it("says overdue when the approval is overdue even though the step's own SLA would not be", () => {
    const due = new Date(Date.now() - 2 * 3_600_000).toISOString();
    render(<NextStepCard run={run(approvalStep({ sla_hours: 720, approval: { approver_label: "Casey Delegate", step_order: 1, due_at: due, overdue: true } }))} />);
    expect(screen.getByText(/^Overdue since/)).toBeInTheDocument();
  });

  it("falls back to the step's team and SLA when there is no live approval to read", () => {
    render(<NextStepCard run={run(approvalStep({ approval: null }))} teams={[finance]} />);
    expect(screen.getByText(/team —/)).toBeInTheDocument();
    expect(screen.getByText(/Gale Approver/)).toBeInTheDocument();
  });
});
