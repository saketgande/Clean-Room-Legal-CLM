import { describe, expect, it } from "vitest";
import { waitLabel } from "./_governance-ladder";

const step = (type: string, over: Record<string, unknown> = {}) =>
  ({ type, assignee_user_id: null, assignee_label: null, team_label: null, ...over }) as Parameters<typeof waitLabel>[0];

describe("waitLabel", () => {
  it("says 'on you' only to the person the step is assigned to", () => {
    const s = step("human_task", { assignee_user_id: "u-1", assignee_label: "Erin Legal" });
    expect(waitLabel(s, "u-1")).toBe("Waiting on you");
    expect(waitLabel(s, "u-2")).toBe("Waiting on Erin Legal");
  });

  it("names the team when no person holds the step, and says so when nobody does", () => {
    expect(waitLabel(step("human_task", { team_label: "Legal Ops" }), "u-2")).toBe("Waiting on Legal Ops");
    expect(waitLabel(step("human_task"), "u-2")).toBe("Waiting on a person");
  });

  it("does the same for an AI step that waits for a person to confirm it", () => {
    expect(waitLabel(step("ai_task", { assignee_user_id: "u-1", assignee_label: "Erin Legal" }), "u-1")).toBe("Needs your review");
    expect(waitLabel(step("ai_task", { assignee_user_id: "u-1", assignee_label: "Erin Legal" }), "u-2")).toBe("Needs review by Erin Legal");
    expect(waitLabel(step("ai_task"), null)).toBe("Needs a person's review");
  });

  it("leaves other step types as they were", () => {
    expect(waitLabel(step("approval"), "u-1")).toBe("Awaiting sign-off");
    expect(waitLabel(step("signature"), "u-1")).toBe("Out for signature");
  });
});
