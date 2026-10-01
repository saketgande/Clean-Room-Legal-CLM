import { describe, expect, it } from "vitest";
import { currentStage, stepStage } from "./lifecycle-view";
import type { WorkflowRunStep } from "@/lib/types";

const step = (stage: WorkflowRunStep["stage"], status = "pending") => ({ stage, status } as WorkflowRunStep);

// The lifecycle bar must point at where the work actually is: a stale contract
// stage would show "Review" while the workflow is already waiting on approval.
describe("currentStage", () => {
  it("follows the live workflow's current step over the contract's stored stage", () => {
    const run = { status: "waiting" as const, current_index: 1, steps: [step("review", "done"), step("approval", "waiting_human")] };
    expect(currentStage({ run, contractStage: "review" })).toBe("approval");
  });

  it("uses the contract's stage once the workflow has finished", () => {
    const run = { status: "complete" as const, current_index: 2, steps: [step("approval", "done"), step("signature", "done")] };
    expect(currentStage({ run, contractStage: "active" })).toBe("active");
  });

  it("a closed request with nothing drafted is Closed, an open one is still Intake", () => {
    expect(currentStage({ requestClosed: true })).toBe("closed");
    expect(currentStage({})).toBe("intake");
  });

  it("puts steps saved before stages existed under Review", () => {
    expect(stepStage(step(null))).toBe("review");
  });
});
