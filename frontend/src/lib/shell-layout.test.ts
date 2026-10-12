import { describe, expect, it } from "vitest";
import { needsOldChrome } from "./shell-layout";

describe("needsOldChrome", () => {
  it("pads the pages whose title sat against the top edge", () => {
    for (const path of ["/org-structure", "/screen-access", "/delegations", "/ai-usage", "/trademarks", "/trademarks/calendar", "/trademarks/abc-123"]) {
      expect(needsOldChrome(path), path).toBe(true);
    }
  });

  it("still pads the pages that were already padded", () => {
    for (const path of ["/admin", "/admin/users", "/jobs", "/notifications", "/notices/n-1", "/tabular-reviews/t-1", "/playbooks/p-1"]) {
      expect(needsOldChrome(path), path).toBe(true);
    }
  });

  it("leaves the full-screen and redesigned pages alone", () => {
    for (const path of ["/", "/intake", "/my-work", "/contracts", "/contracts/c-1", "/brain", "/playbooks/build", "/notices", "/tabular-reviews", "/renewals", "/search", "/signatures", "/workflow-builder/f-1"]) {
      expect(needsOldChrome(path), path).toBe(false);
    }
  });

  it("does not mistake a page that merely starts with the same letters", () => {
    expect(needsOldChrome("/jobsite")).toBe(false);
    expect(needsOldChrome("/trademarksx")).toBe(false);
  });
});
