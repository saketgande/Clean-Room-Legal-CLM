import { describe, expect, it } from "vitest";
import { approvalsText, beforeWorkflow, counterpartySummary, groupActivityByDay, isBeingSetUp, SETUP_WINDOW_MS, termFact } from "./request-facts";
import { fmtDate } from "./utils";

// The dates are shown the way the rest of the app shows a date, not as the stored YYYY-MM-DD (round 2, bug 48).
const d = (iso: string) => fmtDate(iso);

describe("termFact", () => {
  it("shows both dates when both are given", () => {
    expect(termFact({ start_date: "2026-10-01", end_date: "2028-09-30" })).toBe(`${d("2026-10-01")} → ${d("2028-09-30")}`);
    expect(termFact({ start_date: "2026-10-01", end_date: "2028-09-30" })).not.toContain("2026-10-01");
  });

  it("shows the form's own term when there is no end date, instead of calling it open-ended", () => {
    expect(termFact({ start_date: "2026-10-01", term: "2-year term" })).toBe(`${d("2026-10-01")} · 2-year term`);
    expect(termFact({ start_date: "2026-10-01", nda_term: "3 years" })).toBe(`${d("2026-10-01")} · 3 years`);
  });

  it("does not claim an open end it was never told about", () => {
    expect(termFact({ start_date: "2026-10-01" })).toBe(`${d("2026-10-01")} → no end date given`);
  });

  it("shows the term alone when there are no dates, and nothing when there is nothing", () => {
    expect(termFact({ term: "Renews automatically" })).toBe("Renews automatically");
    expect(termFact({})).toBeNull();
    expect(termFact({ start_date: "  ", term: "" })).toBeNull();
  });

  it("reads an end date on its own", () => {
    expect(termFact({ termination_date: "2026-12-31" })).toBe(`— → ${d("2026-12-31")}`);
    expect(termFact({ end_date: "soon" })).toBe("— → soon"); // something that is not a date is left as typed
  });
});

describe("isBeingSetUp", () => {
  const now = Date.parse("2026-10-08T12:00:30Z");
  const filedAt = (iso: string) => ({ submitted_at: iso, created_at: null });

  it("is true shortly after filing while there is no run", () => {
    expect(isBeingSetUp(filedAt("2026-10-08T12:00:00Z"), null, now)).toBe(true);
  });

  it("is false once a run exists", () => {
    expect(isBeingSetUp(filedAt("2026-10-08T12:00:00Z"), { id: "run" }, now)).toBe(false);
  });

  it("is false once the window has passed, so a request with no matching workflow can be given one", () => {
    expect(isBeingSetUp(filedAt("2026-10-08T12:00:00Z"), null, Date.parse("2026-10-08T12:00:00Z") + SETUP_WINDOW_MS)).toBe(false);
    expect(isBeingSetUp(filedAt("2026-10-01T12:00:00Z"), null, now)).toBe(false);
  });

  it("falls back to created_at, and is false when there is no usable time or no request", () => {
    expect(isBeingSetUp({ submitted_at: null, created_at: "2026-10-08T12:00:10Z" }, null, now)).toBe(true);
    expect(isBeingSetUp({ submitted_at: null, created_at: null }, null, now)).toBe(false);
    expect(isBeingSetUp(undefined, null, now)).toBe(false);
  });
});

describe("approvalsText", () => {
  const none = { approvers: [], fetching: false, loaded: true, valueMissing: false };

  it("lists who approves, one step per line", () => {
    expect(approvalsText({ ...none, approvers: [{ step_name: "Finance", approver: "Priya" }, { step_name: "Legal", approver: "Sam" }] }))
      .toBe("Finance: Priya\nLegal: Sam");
  });

  it("does not say 'none' while it is still working it out or has not been asked", () => {
    expect(approvalsText({ ...none, fetching: true })).toBe("Working it out…");
    expect(approvalsText({ ...none, loaded: false })).toBe("Not known yet");
  });

  it("says the answer depends on the value while the value is empty", () => {
    expect(approvalsText({ ...none, valueMissing: true })).toBe("Depends on the contract value: fill it in to see who approves");
  });

  it("says none only when it is known to be none", () => {
    expect(approvalsText(none)).toBe("None on this workflow");
  });

  it("does not say 'none on this workflow' when no workflow fits: a person picks it after filing", () => {
    expect(approvalsText({ ...none, noWorkflow: true })).toBe("Decided when a person picks the workflow after filing");
  });

  it("still asks for the value first, even when no workflow fits yet", () => {
    expect(approvalsText({ ...none, noWorkflow: true, valueMissing: true })).toBe("Depends on the contract value: fill it in to see who approves");
  });

  it("lists approvers whenever there are some", () => {
    expect(approvalsText({ ...none, noWorkflow: true, approvers: [{ step_name: "Finance", approver: "Priya" }] })).toBe("Finance: Priya");
  });
});

describe("groupActivityByDay", () => {
  const item = (id: string, at: string) => ({ id, at, sentence: id, who: "Sam", source: "request" as const, tone: "plain" as const, system: false });
  const now = Date.parse("2026-10-23T12:00:00");
  it("keeps the given order, one heading per local day, and adds the year only for another year", () => {
    /* Guards the days being re-sorted (newest first is the user's decision) or an old date losing its year. */
    const g = groupActivityByDay([item("a", "2026-10-14T15:00:00"), item("b", "2026-10-14T09:00:00"), item("c", "2025-03-02T09:00:00")], now);
    expect(g.map((x) => [x.day, x.items.map((i) => i.id)])).toEqual([["14 Oct", ["a", "b"]], ["2 Mar 2025", ["c"]]]);
  });
  it("returns nothing for nothing", () => {
    /* Guards a stray empty heading. */
    expect(groupActivityByDay([], now)).toEqual([]);
  });
});

describe("counterpartySummary", () => {
  it("builds the line and leaves out zero parts", () => {
    /* Guards "0 active" noise and wrong plurals. */
    expect(counterpartySummary({ contracts: 5, active: 2, requests: 7 })).toBe("5 other contracts · 2 active · 7 earlier requests");
    expect(counterpartySummary({ contracts: 1, active: 0, requests: 1 })).toBe("1 other contract · 1 earlier request");
  });
  it("is null when everything is zero", () => {
    /* Guards an empty summary line being drawn. */
    expect(counterpartySummary({ contracts: 0, active: 0, requests: 0 })).toBeNull();
  });
});

describe("beforeWorkflow", () => {
  it("says not started, waiting for an owner, when nobody owns the request and nothing is drafted", () => {
    expect(beforeWorkflow({})).toEqual({ title: "Not started", note: "waiting for an owner" });
    expect(beforeWorkflow({ assigned_to_label: null, contract_id: null })).toEqual({ title: "Not started", note: "waiting for an owner" });
  });

  it("says in progress, with the owner, once somebody owns it", () => {
    expect(beforeWorkflow({ assigned_to_label: "Erin Legal" })).toEqual({ title: "In progress", note: "with Erin Legal" });
  });

  it("says in progress, contract drafted, when the contract exists but nobody owns the request", () => {
    expect(beforeWorkflow({ contract_id: "c-1" })).toEqual({ title: "In progress", note: "contract drafted" });
  });

  it("never pairs 'in progress' with 'not started'", () => {
    for (const r of [{}, { assigned_to_label: "A" }, { contract_id: "c" }, { assigned_to_label: "A", contract_id: "c" }]) {
      const { title, note } = beforeWorkflow(r);
      expect(title === "In progress" && /not started/i.test(note)).toBe(false);
    }
  });
});
