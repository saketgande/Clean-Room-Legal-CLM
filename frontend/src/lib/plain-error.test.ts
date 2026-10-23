import { describe, expect, it } from "vitest";
import { NO_PERMISSION_MESSAGE, plainErrorMessage } from "./plain-error";

describe("plainErrorMessage", () => {
  it("replaces a bare permission-code refusal with plain words", () => {
    expect(plainErrorMessage(403, "Missing permission: intake:read")).toBe(NO_PERMISSION_MESSAGE);
    expect(plainErrorMessage(403, "Missing permissions: contract:read, playbook:run")).toBe(NO_PERMISSION_MESSAGE);
    expect(NO_PERMISSION_MESSAGE).not.toMatch(/[a-z_]+:[a-z_]+/);
  });

  it("leaves every other refusal exactly as the server wrote it", () => {
    expect(plainErrorMessage(403, "Only this step's assignee or its team can act on it")).toBe("Only this step's assignee or its team can act on it");
    expect(plainErrorMessage(403, "You are not assigned to decide this approval")).toBe("You are not assigned to decide this approval");
  });

  it("only rewrites a 403", () => {
    expect(plainErrorMessage(422, "Missing permission: intake:read")).toBe("Missing permission: intake:read");
    expect(plainErrorMessage(500, "Broken")).toBe("Broken");
  });
});
