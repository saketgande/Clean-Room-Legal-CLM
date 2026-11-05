import { describe, expect, it } from "vitest";
import { canEditObligations, canRunReminders } from "./obligation-access";

describe("who sees the obligation buttons", () => {
  it("shows the edit buttons only with the obligation:update permission, or the wildcard", () => {
    /** Guards buttons that answer 403 for the many people who can read obligations but cannot change them. */
    expect(canEditObligations(["obligation:read", "obligation:update"])).toBe(true);
    expect(canEditObligations(["*"])).toBe(true);
    expect(canEditObligations(["obligation:read"])).toBe(false);
    expect(canEditObligations([])).toBe(false);
    expect(canEditObligations(null)).toBe(false);
    expect(canEditObligations(undefined)).toBe(false);
  });

  it("keeps Run reminders to administrators", () => {
    /** Guards every member being offered a button that emails owners across the organisation. */
    expect(canRunReminders(["admin_panel:access"])).toBe(true);
    expect(canRunReminders(["*"])).toBe(true);
    expect(canRunReminders(["obligation:update"])).toBe(false);
    expect(canRunReminders(undefined)).toBe(false);
  });
});
