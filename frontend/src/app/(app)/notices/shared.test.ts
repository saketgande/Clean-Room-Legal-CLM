import { describe, expect, it } from "vitest";
import type { Notice, NoticeDeadlineFacts } from "@/lib/types";
import { STATUTORY_NOTE, computeDue, deadlineLabel, deadlineUnconfirmed, recountText } from "./shared";

const facts = (over: Partial<NoticeDeadlineFacts>): NoticeDeadlineFacts => ({
  stated: true, explicit_date: null, period_count: null, period_unit: null,
  runs_from: null, runs_from_text: null, quote: null, clear_days: false, ...over,
});

// Expected strings were produced by the backend's notices.service.compute_due:
// the live preview in the create form must say exactly what the server saves.
describe("computeDue matches the server's count", () => {
  const cases: [string, NoticeDeadlineFacts, string | null, string | null, string][] = [
    ["days from receipt", facts({ period_count: 15, period_unit: "days", runs_from: "receipt", quote: "within 15 days of receipt" }), null, "2026-10-06",
      "due Wed 21 Oct 2026 = received Tue 6 Oct 2026 + 15 days"],
    ["a Saturday deadline is not moved later", facts({ period_count: 2, period_unit: "days", runs_from: "receipt", quote: "within two (2) days of receipt" }), null, "2026-10-01",
      "due Sat 3 Oct 2026 = received Thu 1 Oct 2026 + 2 days"],
    ["business days skip the weekend and flag holidays", facts({ period_count: 1, period_unit: "business_days", runs_from: "notice_date", quote: "within one business day of this notice" }), "2026-10-09", null,
      "due Mon 12 Oct 2026 = notice dated Fri 9 Oct 2026 + 1 business day (public holidays not counted - please confirm)"],
    ["a month from 31 Jan clamps to the month end", facts({ period_count: 1, period_unit: "months", runs_from: "notice_date", quote: "within a month of the date hereof" }), "2026-01-31", null,
      "due Sat 28 Feb 2026 = notice dated Sat 31 Jan 2026 + 1 month"],
    ["weeks", facts({ period_count: 2, period_unit: "weeks", runs_from: "receipt", quote: "within two weeks of receipt" }), null, "2026-10-06",
      "due Tue 20 Oct 2026 = received Tue 6 Oct 2026 + 2 weeks"],
    ["clear days end a day earlier", facts({ period_count: 15, period_unit: "days", runs_from: "receipt", clear_days: true, quote: "within fifteen clear days of receipt" }), null, "2026-10-06",
      "due Tue 20 Oct 2026 = received Tue 6 Oct 2026 + 15 clear days"],
    ["clear business days end a business day earlier", facts({ period_count: 3, period_unit: "business_days", runs_from: "receipt", clear_days: true, quote: "within 3 clear business days" }), null, "2026-10-09",
      "due Tue 13 Oct 2026 = received Fri 9 Oct 2026 + 3 clear business days (public holidays not counted - please confirm)"],
    ["a clear month from 31 Jan is the day before the clamped month end", facts({ period_count: 1, period_unit: "months", runs_from: "notice_date", clear_days: true, quote: "one clear month from the date hereof" }), "2026-01-31", null,
      "due Fri 27 Feb 2026 = notice dated Sat 31 Jan 2026 + 1 clear month"],
    ["clear weeks", facts({ period_count: 2, period_unit: "weeks", runs_from: "receipt", clear_days: true, quote: "two clear weeks of receipt" }), null, "2026-10-06",
      "due Mon 19 Oct 2026 = received Tue 6 Oct 2026 + 2 clear weeks"],
    ["an explicit date is taken as written", facts({ explicit_date: "2026-11-02" }), null, null,
      "due Mon 2 Nov 2026 = stated in notice"],
  ];
  for (const [name, f, noticeDate, received, formula] of cases) {
    it(name, () => {
      /** Guards a preview formula drifting from the date the server will store. */
      const r = computeDue(f, noticeDate, received);
      expect(r.formula).toBe(formula);
      expect(r.needs_person).toBe(false);
    });
  }

  it("recounts live when the received date changes", () => {
    /** Guards the preview keeping a stale date after the filer corrects "Received on". */
    const f = facts({ period_count: 15, period_unit: "days", runs_from: "receipt", quote: "within 15 days of receipt" });
    expect(computeDue(f, null, "2026-10-06").due).toBe("2026-10-21");
    expect(computeDue(f, null, "2026-10-01").due).toBe("2026-10-16");
  });
});

describe("computeDue leaves what it can't count to a person", () => {
  it("no period in the notice reads as a possible statutory period", () => {
    /** Guards a notice with no stated deadline showing as "no deadline" instead of needing a person. */
    const r = computeDue(facts({ stated: false }), null, "2026-10-06");
    expect(r).toMatchObject({ due: null, needs_person: true, reason: STATUTORY_NOTE });
    expect(STATUTORY_NOTE).toBe("The notice states no deadline; a statutory period may apply. A person must set the date.");
  });

  it("a period from an event code doesn't track gets no date", () => {
    /** Guards counting from the wrong anchor when the notice runs from e.g. the invoice date. */
    const r = computeDue(facts({ period_count: 7, period_unit: "days", runs_from: "other", runs_from_text: "the date of the invoice", quote: "7 days from the date of the invoice" }), null, "2026-10-06");
    expect(r.reason).toBe("The period runs from 'the date of the invoice'. A person must set the date.");
    expect(r.due).toBeNull();
  });

  it("a missing received date gets no date", () => {
    /** Guards counting from an empty anchor. */
    const r = computeDue(facts({ period_count: 7, period_unit: "days", runs_from: "receipt", quote: "seven days of receipt" }), null, null);
    expect(r.reason).toBe("The received date is not set, so the period can't be counted. Enter it or set the date.");
  });

  it("a period whose number isn't in the quoted sentence gets no date", () => {
    /** Guards counting a statutory period the model brought in as if the notice stated it. */
    const r = computeDue(facts({ period_count: 15, period_unit: "days", runs_from: "receipt", quote: "respond within the statutory period" }), null, "2026-10-06");
    expect(r).toMatchObject({ due: null, needs_person: true });
    expect(r.reason).toBe("The period (15) is not in the quoted sentence; it may come from a statute. A person must set the date.");
  });

  it("a period beats a date read beside it", () => {
    /** Guards trusting a date the model may have worked out over the period the notice states. */
    const r = computeDue(facts({ explicit_date: "2026-10-30", period_count: 15, period_unit: "days", runs_from: "receipt", quote: "within 15 days of receipt" }), null, "2026-10-06");
    expect(r.due).toBe("2026-10-21");
  });

  it("no basis at all is not a needs-a-person case", () => {
    /** Guards a manually filed notice being nagged for a date it never claimed. */
    expect(computeDue(null, null, null).needs_person).toBe(false);
  });
});

describe("register wording", () => {
  const base = { response_due_date: null, deadline_posture: "none", deadline_basis: null } as unknown as Notice;

  it("a notice waiting for a person's date says so, not 'No deadline'", () => {
    /** Guards a statutory notice hiding in the register as having no deadline. */
    expect(deadlineLabel({ ...base, deadline_posture: "needs_date" })).toBe("Needs a date");
    expect(deadlineLabel(base)).toBe("No deadline");
  });

  it("a calculated date is unconfirmed until a person confirms it, and again after its anchor moves", () => {
    /** Guards the "calculated - not yet confirmed" label vanishing early or never returning. */
    const doc = { set_by: "document", confirmed_by: null, needs_reconfirm: false };
    expect(deadlineUnconfirmed({ ...base, deadline_basis: doc } as unknown as Notice)).toBe(true);
    expect(deadlineUnconfirmed({ ...base, deadline_basis: { ...doc, confirmed_by: "u1" } } as unknown as Notice)).toBe(false);
    expect(deadlineUnconfirmed({ ...base, deadline_basis: { ...doc, confirmed_by: "u1", needs_reconfirm: true } } as unknown as Notice)).toBe(true);
    expect(deadlineUnconfirmed({ ...base, deadline_basis: { ...doc, set_by: "person" } } as unknown as Notice)).toBe(false);
  });
});

describe("a recount under a confirmed date", () => {
  const withBasis = (recomputed: unknown) =>
    ({ deadline_basis: { set_by: "document", confirmed_by: "u1", needs_reconfirm: true, recomputed } }) as unknown as Notice;

  it("shows the recount's formula, or why it has no date", () => {
    /** Guards a moved received date silently replacing a confirmed deadline with no trace of the new count. */
    expect(recountText(withBasis({ due: "2026-10-20", formula: "due Tue 20 Oct 2026 = received Mon 5 Oct 2026 + 15 days", reason: null, needs_person: false })))
      .toBe("due Tue 20 Oct 2026 = received Mon 5 Oct 2026 + 15 days");
    expect(recountText(withBasis({ due: null, formula: null, reason: "The received date is not set, so the period can't be counted. Enter it or set the date.", needs_person: true })))
      .toContain("received date is not set");
    expect(recountText(withBasis(null))).toBeNull();
  });
});
