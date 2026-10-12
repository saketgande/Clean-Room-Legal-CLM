import { describe, expect, it } from "vitest";
import { daysToExpiry, expiringWithoutRenewal, isExpiringSoon } from "./expiry";

const NOW = new Date("2026-10-08T09:00:00").getTime();
const inDays = (n: number) => {
  const d = new Date("2026-10-08T00:00:00");
  d.setDate(d.getDate() + n);
  const p = (x: number) => String(x).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
};

describe("isExpiringSoon", () => {
  it("counts a running contract that ends within 90 days", () => {
    expect(isExpiringSoon({ lifecycle_stage: "active", expiration_date: inDays(20) }, NOW)).toBe(true);
    expect(isExpiringSoon({ lifecycle_stage: "active", expiration_date: inDays(90) }, NOW)).toBe(true);
  });

  it("does not count one that ends later, or already ended, or has no end date", () => {
    expect(isExpiringSoon({ lifecycle_stage: "active", expiration_date: inDays(91) }, NOW)).toBe(false);
    expect(isExpiringSoon({ lifecycle_stage: "active", expiration_date: inDays(-3) }, NOW)).toBe(false);
    expect(isExpiringSoon({ lifecycle_stage: "active", expiration_date: null }, NOW)).toBe(false);
  });

  it.each(["intake", "drafting", "review", "approval", "signature", "closed"] as const)(
    "does not count a contract that is %s: it is not a running agreement",
    (lifecycle_stage) => {
      expect(isExpiringSoon({ lifecycle_stage, expiration_date: inDays(20) }, NOW)).toBe(false);
    },
  );
});

describe("daysToExpiry", () => {
  it("counts whole days to the end date", () => {
    expect(daysToExpiry({ expiration_date: inDays(20) }, NOW)).toBe(20);
  });
  it("is null with no end date", () => {
    expect(daysToExpiry({ expiration_date: null }, NOW)).toBeNull();
  });
});

describe("expiringWithoutRenewal", () => {
  const c = (id: string, days: number, lifecycle_stage: "active" | "review" = "active") => ({
    id, lifecycle_stage, expiration_date: inDays(days),
  });

  it("lists running contracts ending soon that have no renewal row, soonest first", () => {
    const out = expiringWithoutRenewal([c("late", 60), c("soon", 20), c("tracked", 10)], [{ contract_id: "tracked" }], NOW);
    expect(out.map((x) => x.id)).toEqual(["soon", "late"]);
  });

  it("leaves out contracts that are not running agreements or end later", () => {
    expect(expiringWithoutRenewal([c("draft", 20, "review"), c("far", 200)], [], NOW)).toEqual([]);
  });
});
