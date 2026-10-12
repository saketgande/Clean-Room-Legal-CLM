import { describe, expect, it } from "vitest";
import type { RenewalEvent } from "@/lib/types";
import { EVERGREEN_NOTE, STATUS_TEXT, canConfirm, conflictField, conflictText, evergreenConflict, mustTypeExpiry, recomputed } from "./dates";

const fmt = (iso: string) => `[${iso}]`;

describe("renewal date wording", () => {
  it("each status says what a person must do", () => {
    /** Guards a calculated renewal date reading as settled. */
    expect(STATUS_TEXT.calculated).toBe("calculated - not yet confirmed");
    expect(STATUS_TEXT.needs_person).toBe("needs a person");
    expect(STATUS_TEXT.needs_reconfirm).toContain("needs re-confirming");
  });

  it("a conflict shows both values, including a phrase for an evergreen text", () => {
    /** Guards an evergreen-vs-form-expiry dispute rendering as a broken date. */
    expect(conflictText({ form: "2027-10-15", text: "continues until terminated" }, fmt))
      .toBe("Request form says [2027-10-15]; contract text says continues until terminated");
  });

  it("only calculated or re-counted dates can be accepted as they stand", () => {
    /** Guards a needs-a-person row offering to "confirm" dates that don't exist. */
    const r = (dates_status: RenewalEvent["dates_status"]) => ({ dates_status }) as RenewalEvent;
    expect(canConfirm(r("calculated"))).toBe(true);
    expect(canConfirm(r("needs_reconfirm"))).toBe(true);
    expect(canConfirm(r("needs_person"))).toBe(false);
    expect(canConfirm(r(null))).toBe(false);
  });

  it("reads the re-count kept beside confirmed dates", () => {
    /** Guards the confirm dialog hiding what re-confirming would accept. */
    const r = { metadata_json: { dates: { recomputed: { expiration_date: "2027-10-20" } } } } as unknown as RenewalEvent;
    expect(recomputed(r)?.expiration_date).toBe("2027-10-20");
    expect(recomputed({ metadata_json: {} } as RenewalEvent)).toBeNull();
  });
});

describe("confirming renewal dates", () => {
  const ev = (over: Partial<RenewalEvent> & { term_kind?: string; recount?: unknown }) => {
    const { term_kind, recount, ...rest } = over;
    return {
      expiration_date: null, date_conflict: null, date_reason: null,
      metadata_json: { dates: { facts: { term_kind: term_kind ?? "fixed" }, recomputed: recount ?? null } },
      ...rest,
    } as unknown as RenewalEvent;
  };

  it("requires a typed expiry when there is none to accept", () => {
    /** Guards an empty "Confirm these dates" submit on a row with no expiry (the server 422s it). */
    expect(mustTypeExpiry(ev({}))).toBe(true);
    expect(mustTypeExpiry(ev({ expiration_date: "2027-10-14" }))).toBe(false);
  });

  it("requires a typed expiry while the form and the text disagree", () => {
    /** Guards confirming past a dispute nobody settled. */
    expect(mustTypeExpiry(ev({ expiration_date: "2027-10-14", date_conflict: { form: "2027-10-14", text: "2027-11-17" } }))).toBe(true);
  });

  it("a re-count with no expiry needs a typed one, even if the confirmed row had one", () => {
    /** Guards accepting a re-count that has no expiry in it. */
    expect(mustTypeExpiry(ev({ expiration_date: "2027-10-14", recount: { expiration_date: null } }))).toBe(true);
  });

  it("an undisputed evergreen contract may be confirmed with no expiry", () => {
    /** Guards forcing a made-up end date on a contract that runs until terminated. */
    expect(mustTypeExpiry(ev({ term_kind: "evergreen" }))).toBe(false);
  });

  it("explains an evergreen text against a form end date and settles the expiry", () => {
    /** Guards the evergreen dispute showing no way forward, or settling the wrong contract field. */
    const r = ev({
      term_kind: "evergreen",
      date_conflict: { form: "2027-10-15", text: "continues until terminated" },
      date_reason: "expiry differs between the form and the contract text - needs a person",
    });
    expect(evergreenConflict(r)).toBe(true);
    expect(EVERGREEN_NOTE).toContain("runs until terminated");
    expect(conflictField(r)).toBe("expiration_date");
  });

  it("an effective-date dispute settles the effective date", () => {
    /** Guards a term-start dispute writing the form's start date into the contract's expiry. */
    const r = ev({
      date_conflict: { form: "2026-11-17", text: "2026-10-15" },
      date_reason: "effective date differs between the form and the contract text - needs a person",
    });
    expect(conflictField(r)).toBe("effective_date");
    expect(evergreenConflict(r)).toBe(false);
  });
});
