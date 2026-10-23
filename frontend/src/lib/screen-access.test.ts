import { describe, it, expect } from "vitest";
import { hasAtLeast, getScreenLevel, buildScreenLevelMap, LEVEL_RANK } from "./screen-access";
import type { MyScreenAccessResponse } from "./types";

describe("LEVEL_RANK", () => {
  it("encodes the strict VIEW < ADD < EDIT < DELETE hierarchy", () => {
    expect(LEVEL_RANK.VIEW).toBeLessThan(LEVEL_RANK.ADD);
    expect(LEVEL_RANK.ADD).toBeLessThan(LEVEL_RANK.EDIT);
    expect(LEVEL_RANK.EDIT).toBeLessThan(LEVEL_RANK.DELETE);
  });
});

describe("hasAtLeast", () => {
  it("a resolved DELETE satisfies every required level", () => {
    expect(hasAtLeast("DELETE", "VIEW")).toBe(true);
    expect(hasAtLeast("DELETE", "ADD")).toBe(true);
    expect(hasAtLeast("DELETE", "EDIT")).toBe(true);
    expect(hasAtLeast("DELETE", "DELETE")).toBe(true);
  });

  it("a resolved EDIT satisfies VIEW/ADD/EDIT but not DELETE", () => {
    expect(hasAtLeast("EDIT", "VIEW")).toBe(true);
    expect(hasAtLeast("EDIT", "ADD")).toBe(true);
    expect(hasAtLeast("EDIT", "EDIT")).toBe(true);
    expect(hasAtLeast("EDIT", "DELETE")).toBe(false);
  });

  it("a resolved ADD satisfies VIEW/ADD but not EDIT/DELETE", () => {
    expect(hasAtLeast("ADD", "VIEW")).toBe(true);
    expect(hasAtLeast("ADD", "ADD")).toBe(true);
    expect(hasAtLeast("ADD", "EDIT")).toBe(false);
    expect(hasAtLeast("ADD", "DELETE")).toBe(false);
  });

  it("a resolved VIEW satisfies only VIEW", () => {
    expect(hasAtLeast("VIEW", "VIEW")).toBe(true);
    expect(hasAtLeast("VIEW", "ADD")).toBe(false);
    expect(hasAtLeast("VIEW", "EDIT")).toBe(false);
    expect(hasAtLeast("VIEW", "DELETE")).toBe(false);
  });

  it("null/undefined (no access) satisfies nothing", () => {
    expect(hasAtLeast(null, "VIEW")).toBe(false);
    expect(hasAtLeast(undefined, "VIEW")).toBe(false);
    expect(hasAtLeast(null, "ADD")).toBe(false);
    expect(hasAtLeast(null, "EDIT")).toBe(false);
    expect(hasAtLeast(null, "DELETE")).toBe(false);
  });
});

const SAMPLE_ACCESS: MyScreenAccessResponse = {
  org_unit_id: null,
  screens: [
    { screen_id: "s1", screen_code: "contracts", route_path: "/contracts", action_level: "EDIT", rank: 3 },
    { screen_id: "s2", screen_code: "intake", route_path: "/intake", action_level: "VIEW", rank: 1 },
  ],
};

describe("getScreenLevel", () => {
  it("finds a granted screen's resolved level", () => {
    expect(getScreenLevel(SAMPLE_ACCESS, "contracts")).toBe("EDIT");
    expect(getScreenLevel(SAMPLE_ACCESS, "intake")).toBe("VIEW");
  });

  it("returns null for a screen not present in the response (no access)", () => {
    expect(getScreenLevel(SAMPLE_ACCESS, "trademarks")).toBeNull();
  });

  it("returns null when the access response itself is missing", () => {
    expect(getScreenLevel(undefined, "contracts")).toBeNull();
    expect(getScreenLevel(null, "contracts")).toBeNull();
  });
});

describe("buildScreenLevelMap", () => {
  it("builds a screen_code -> action_level map from a list of entries", () => {
    const map = buildScreenLevelMap(SAMPLE_ACCESS.screens);
    expect(map.get("contracts")).toBe("EDIT");
    expect(map.get("intake")).toBe("VIEW");
    expect(map.has("trademarks")).toBe(false);
  });

  it("returns an empty map for an empty/missing list", () => {
    expect(buildScreenLevelMap(undefined).size).toBe(0);
    expect(buildScreenLevelMap(null).size).toBe(0);
    expect(buildScreenLevelMap([]).size).toBe(0);
  });
});
