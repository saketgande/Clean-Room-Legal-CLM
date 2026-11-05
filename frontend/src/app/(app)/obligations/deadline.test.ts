import { describe, expect, it } from "vitest";
import type { DateState, Obligation, ObligationDeadline } from "@/lib/types";
import {
  DATE_FILTERS, UNCONFIRMED, anchorText, conflictChoices, conflictText, dateState, editChanges, expectedTotal, needsEventDate,
  stateOf, timingDetail, timingOf,
} from "./deadline";

const fmt = (iso: string) => iso;
const dl = (over: Partial<ObligationDeadline>): ObligationDeadline => ({
  formula: null, reason: null, ends_on: null, anchor: null, anchor_date: null, set_by_person: false,
  confirmed: false, confirmed_by: null, confirmed_at: null, needs_reconfirm: false, recomputed: null, ...over,
});
const ob = (deadline: ObligationDeadline | null, date_state?: DateState) => ({ deadline, date_state }) as Obligation;

describe("obligation date state", () => {
  it("a computed date reads 'calculated - not yet confirmed' until confirmed", () => {
    /** Guards an AI-derived date looking as settled as one a person checked. */
    expect(UNCONFIRMED).toBe("calculated - not yet confirmed");
    expect(dateState(ob(dl({ formula: "due Wed 21 Oct 2026 = effective date Tue 6 Oct 2026 + 15 days" })))?.text).toBe(UNCONFIRMED);
    expect(dateState(ob(dl({ formula: "due …", confirmed: true })))?.text).toBe("confirmed");
  });

  it("an anchor change on a confirmed date asks for re-confirming", () => {
    /** Guards a confirmed date silently going stale after the effective date moved. */
    expect(dateState(ob(dl({ formula: "due …", confirmed: true, needs_reconfirm: true })))?.text)
      .toBe("the date it counts from changed - needs re-confirming");
  });

  it("no date shows the server's reason, including 'needs a person'", () => {
    /** Guards a period that would come from law being shown as a blank with no explanation. */
    const s = dateState(ob(dl({ reason: "period not stated in the contract - needs a person" }), "needs_person"));
    expect(s).toEqual({ text: "period not stated in the contract - needs a person", tone: "crit" });
    // Whether a missing date is calm or an alarm is the server's date_state, not the wording of the reason.
    expect(dateState(ob(dl({ reason: "starts when the invoice is received - enter the date" }), "waiting_event"))?.tone).toBe("muted");
    expect(dateState(ob(dl({ reason: "starts when the invoice is received - enter the date" }), "needs_person"))?.tone).toBe("crit");
    // A reason on a duty that is not a gap (an ongoing duty whose end cannot be counted yet) is explanation, not an alarm.
    expect(dateState(ob(dl({ reason: "effective date not set on the contract" }), "no_fixed_date"))?.tone).toBe("muted");
  });

  it("an old row with no deadline block has no state line (its Timing label says it was not read for dates)", () => {
    /** Guards pre-Step-3d obligations suddenly showing an empty state line, and reading as 'conditional' elsewhere. */
    expect(dateState(ob(null, "not_read"))).toBeNull();
    expect(timingOf(ob(null, "not_read"), fmt).label).toBe("Not read for dates");
  });
});

describe("anchors", () => {
  it("names where the anchor date came from", () => {
    /** Guards a person not being able to tell a form date from one read off the contract. */
    expect(anchorText({ name: "effective_date", value: "2026-10-15", source: "form", conflict: null }, fmt))
      .toBe("effective date 2026-10-15, from the request form");
  });

  it("a disputed anchor shows both values", () => {
    /** Guards the page picking one side of a form-vs-text disagreement. */
    expect(conflictText("effective_date", { form: "2026-10-01", text: "2026-10-15" }, fmt))
      .toBe("effective date: request form says 2026-10-01, contract text says 2026-10-15 - needs a person");
  });

  it("asks for an event date only when the clause counts from an event", () => {
    /** Guards an event-date field appearing on contract-anchored deadlines (or missing on event ones). */
    expect(needsEventDate(ob(dl({ reason: "starts when the invoice is received - enter the date" }), "waiting_event"))).toBe(true);
    // Reworded reason text must not matter: the server's state decides.
    expect(needsEventDate(ob(dl({ reason: "waiting for something" }), "waiting_event"))).toBe(true);
    expect(needsEventDate(ob(dl({ reason: "starts when the invoice is received - enter the date" }), "needs_person"))).toBe(false);
    expect(needsEventDate(ob(dl({ anchor: { name: "invoice_received", value: "2026-10-01", source: "user", conflict: null } })))).toBe(true);
    expect(needsEventDate(ob(dl({ anchor: { name: "effective_date", value: "2026-10-01", source: "user", conflict: null } })))).toBe(false);
  });
});

describe("settling a form-vs-text dispute", () => {
  it("offers one button per side that is a date", () => {
    /** Guards a person having to leave the page (or guess) to pick between the form's and the text's date. */
    expect(conflictChoices({ form: "2026-10-01", text: "2026-10-15" }, fmt)).toEqual([
      { label: "Use form date 2026-10-01", value: "2026-10-01" },
      { label: "Use contract-text date 2026-10-15", value: "2026-10-15" },
    ]);
  });

  it("an evergreen text side has no date to pick", () => {
    /** Guards a "Use contract-text date continues until terminated" button sending a phrase as a date. */
    expect(conflictChoices({ form: "2027-10-15", text: "continues until terminated" }, fmt)).toEqual([
      { label: "Use form date 2027-10-15", value: "2027-10-15" },
    ]);
  });

  it("names a computed or change-request expiry's source", () => {
    /** Guards the new expiry sources rendering as raw keys. */
    expect(anchorText({ name: "expiration_date", value: "2027-10-14", source: "computed", conflict: null }, fmt))
      .toBe("expiry 2027-10-14, counted from the contract's term");
    expect(anchorText({ name: "expiration_date", value: "2027-10-14", source: "change_request", conflict: null }, fmt))
      .toBe("expiry 2027-10-14, from a finished change request");
  });
});

describe("edit changes", () => {
  const row = {
    owner_user_id: "u1", responsible_party: "Acme", obligation_type: "payment", status: "open",
    due_date: "2026-12-01", recurrence: null,
  } as Obligation;
  const same = { ownerUserId: "u1", responsibleParty: "Acme", obligationType: "payment", status: "open" as const,
    dueDate: "2026-12-01", recurrence: "" };

  it("sends nothing for a form left as it was", () => {
    /** Guards a Save with no edit sending the old status and owner, and recording the unchanged due
     *  date as one a person typed. */
    expect(editChanges(row, same)).toEqual({});
  });

  it("sends only the fields that were changed", () => {
    /** Guards a stale dialog reverting a colleague's completion or reassignment: it must not carry
     *  the status or owner it loaded unless the person changed them. */
    expect(editChanges(row, { ...same, responsibleParty: "Acme Ltd" })).toEqual({ responsible_party: "Acme Ltd" });
    expect(editChanges(row, { ...same, status: "completed", recurrence: "monthly" }))
      .toEqual({ status: "completed", recurrence: "monthly" });
  });

  it("sends null for a field that was cleared", () => {
    /** Guards clearing a box doing nothing. */
    expect(editChanges(row, { ...same, dueDate: "", obligationType: "" })).toEqual({ due_date: null, obligation_type: null });
  });
});

describe("what the Timing column says when there is no date", () => {
  const row = (date_state: DateState, over: Partial<Obligation> = {}, deadline: ObligationDeadline | null = dl({})) =>
    ({ date_state, due_date: null, recurrence: null, deadline, ...over }) as Obligation;

  it("says why, by the server's state", () => {
    /** Guards 'On trigger' standing for four different things: waiting for an event, a gap a person must fill, a row never read for dates, and a duty with no date by nature. */
    expect(timingOf(row("waiting_event"), fmt)).toEqual({ label: "Waiting for event", sub: null, tone: "conditional" });
    expect(timingOf(row("needs_person"), fmt)).toEqual({ label: "Needs a date", sub: null, tone: "needs" });
    expect(timingOf(row("not_read"), fmt)).toMatchObject({ label: "Not read for dates", tone: "unread" });
    // It must not promise a re-run the app refuses for a contract whose extraction already succeeded: Edit always works.
    expect(timingOf(row("not_read"), fmt).sub).toBe("read before dates were counted - set the date with Edit");
    expect(timingOf(row("no_fixed_date"), fmt)).toEqual({ label: "No fixed date", sub: null, tone: "conditional" });
  });

  it("shows an ongoing duty's end or its recurrence beneath 'No fixed date', never as a due date", () => {
    /** Guards an ongoing duty turning overdue, and the end date being lost. */
    expect(timingOf(row("no_fixed_date", {}, dl({ ends_on: "2029-10-14" })), fmt)).toEqual({ label: "No fixed date", sub: "Ends 2029-10-14", tone: "recurring" });
    expect(timingOf(row("no_fixed_date", { recurrence: "annually" }), fmt)).toEqual({ label: "No fixed date", sub: "annually", tone: "recurring" });
  });

  it("a dated row reads as a date and how far off it is", () => {
    const now = new Date("2026-10-28T00:00:00Z").getTime();
    expect(timingOf(row("dated", { due_date: "2026-11-02" }), fmt, now)).toEqual({ label: "2026-11-02", sub: "in 5d", tone: "date" });
    expect(timingOf(row("dated", { due_date: "2026-10-25" }), fmt, now).sub).toBe("3d overdue");
    expect(timingOf(row("dated", { due_date: "2026-10-28" }), fmt, now).sub).toBe("today");
  });

  it("a server that sends no state still gets a sensible label", () => {
    expect(stateOf({ due_date: "2026-11-02" } as Obligation)).toBe("dated");
    expect(stateOf({ due_date: null } as Obligation)).toBe("no_fixed_date");
  });
});

describe("the Timing line in an open row", () => {
  const row = (date_state: DateState, over: Partial<Obligation> = {}) =>
    ({ date_state, due_date: null, recurrence: null, deadline: dl({}), ...over }) as Obligation;

  it("tells a person who can edit to set the date, and a person who cannot who has to", () => {
    /** Guards the page telling a view-only person to press an Edit button they do not have. */
    expect(timingDetail(row("needs_person"), fmt, true)).toBe("Needs a date — set one with Edit");
    expect(timingDetail(row("needs_person"), fmt, false)).toBe("Needs a date — someone who can edit this contract needs to set it");
    expect(timingDetail(row("not_read"), fmt, true)).toContain("set the date with Edit");
    expect(timingDetail(row("not_read"), fmt, false)).toContain("someone who can edit this contract needs to set it");
    expect(timingDetail(row("not_read"), fmt, true)).not.toContain("re-running");
  });

  it("states the other cases the same way for everyone", () => {
    expect(timingDetail(row("dated", { due_date: "2026-12-01" }), fmt, false)).toBe("Due 2026-12-01");
    expect(timingDetail(row("waiting_event"), fmt, false)).toContain("Waiting for the event");
    expect(timingDetail(row("no_fixed_date", { deadline: dl({ ends_on: "2029-10-14" }) }), fmt, true)).toBe("Ends on 2029-10-14");
    expect(timingDetail(row("no_fixed_date", { recurrence: "annually" }), fmt, true)).toBe("annually");
    expect(timingDetail(row("no_fixed_date"), fmt, true)).toBe("No fixed date — applies when it happens");
  });
});

describe("the Date filter and the 'of N' total", () => {
  it("offers the filters the server understands and nothing else", () => {
    /** Guards a dropdown value the server answers with a 422. */
    expect(DATE_FILTERS.map((f) => f.value)).toEqual(["", "dated", "needs_date", "waiting_event", "no_fixed_date"]);
  });

  it("says how many there would be without the cap, from the summary", () => {
    /** Guards 'Showing 200' looking like everything. */
    const summary = {
      total: 340, by_status: { open: 300, due_soon: 5, overdue: 5, completed: 20, cancelled: 10 },
      dates: { dated: 10, waiting_event: 12, needs_person: 2, no_fixed_date: 53, not_read: 43 }, needs_date: 45,
    };
    expect(expectedTotal(summary, "", "")).toBe(340);
    expect(expectedTotal(summary, "overdue", "")).toBe(5);
    expect(expectedTotal(summary, "", "needs_date")).toBe(45);
    expect(expectedTotal(summary, "", "waiting_event")).toBe(12);
    expect(expectedTotal(summary, "open", "dated")).toBeNull(); // two filters on: the summary cannot say
  });
});
