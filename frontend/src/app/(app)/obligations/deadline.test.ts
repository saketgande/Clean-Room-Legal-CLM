import { describe, expect, it } from "vitest";
import type { Obligation, ObligationDeadline } from "@/lib/types";
import { UNCONFIRMED, anchorText, conflictChoices, conflictText, dateState, needsEventDate } from "./deadline";

const fmt = (iso: string) => iso;
const dl = (over: Partial<ObligationDeadline>): ObligationDeadline => ({
  formula: null, reason: null, ends_on: null, anchor: null, anchor_date: null, set_by_person: false,
  confirmed: false, confirmed_by: null, confirmed_at: null, needs_reconfirm: false, recomputed: null, ...over,
});
const ob = (deadline: ObligationDeadline | null) => ({ deadline }) as Obligation;

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
    const s = dateState(ob(dl({ reason: "period not stated in the contract - needs a person" })));
    expect(s).toEqual({ text: "period not stated in the contract - needs a person", tone: "crit" });
    expect(dateState(ob(dl({ reason: "starts when the invoice is received - enter the date" })))?.tone).toBe("muted");
  });

  it("an old row with no deadline block keeps the old wording", () => {
    /** Guards pre-Step-3d obligations suddenly showing an empty state line. */
    expect(dateState(ob(null))).toBeNull();
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
    expect(needsEventDate(ob(dl({ reason: "starts when the invoice is received - enter the date" })))).toBe(true);
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
