/* Wording for the renewal dates the server computes (renewals/service.renewal_dates).
   Pure, so the phrases a person relies on are pinned by tests. */

import type { DateConflict, RenewalDatesStatus, RenewalEvent } from "@/lib/types";

export const STATUS_TEXT: Record<RenewalDatesStatus, string> = {
  confirmed: "confirmed",
  needs_reconfirm: "dates re-counted - needs re-confirming",
  needs_person: "needs a person",
  calculated: "calculated - not yet confirmed",
};

export const STATUS_TONE: Record<RenewalDatesStatus, "good" | "warn" | "crit"> = {
  confirmed: "good",
  needs_reconfirm: "warn",
  needs_person: "crit",
  calculated: "warn",
};

/** Both values when the request form and the contract text disagree. */
export function conflictText(c: DateConflict, fmt: (iso: string) => string): string {
  const show = (v: string) => (/^\d{4}-\d{2}-\d{2}$/.test(v) ? fmt(v) : v);
  return `Request form says ${show(c.form)}; contract text says ${show(c.text)}`;
}

/** Calculated dates a person can accept as they stand (or after a re-count).
 *  needs_person rows have nothing to accept: a person types the dates. */
export function canConfirm(r: RenewalEvent): boolean {
  return r.dates_status === "calculated" || r.dates_status === "needs_reconfirm";
}

type Recount = { expiration_date?: string | null; notice_date?: string | null; formulas?: Record<string, string> };

/** The re-count kept beside confirmed dates after an anchor moved. */
export function recomputed(r: RenewalEvent): Recount | null {
  const dates = (r.metadata_json as { dates?: { recomputed?: Recount | null } }).dates;
  return dates?.recomputed ?? null;
}

/** Which contract date a renewal's form-vs-text conflict is about. The server's
 *  reason names it ("effective date differs …" / "expiry differs …"). */
export function conflictField(r: RenewalEvent): "effective_date" | "expiration_date" {
  return r.date_reason?.startsWith("effective date") ? "effective_date" : "expiration_date";
}

/** The form gives an end date but the contract text runs until terminated. */
export function evergreenConflict(r: RenewalEvent): boolean {
  return !!r.date_conflict && !/^\d{4}-\d{2}-\d{2}$/.test(r.date_conflict.text);
}

export const EVERGREEN_NOTE =
  "The contract text has no end date - it continues until terminated - but the request form gives one. " +
  "Use the form's date, type the right one, or confirm it runs until terminated.";

/** No expiry a person could accept as it stands, so confirming needs a typed one:
 *  a conflict, or no (re-)counted expiry on a contract that is not evergreen.
 *  An evergreen contract with no dispute legitimately has no expiry. */
export function mustTypeExpiry(r: RenewalEvent): boolean {
  if (r.date_conflict) return true;
  const recount = recomputed(r);
  if (recount ? recount.expiration_date : r.expiration_date) return false;
  const facts = (r.metadata_json as { dates?: { facts?: { term_kind?: string } } }).dates?.facts;
  return facts?.term_kind !== "evergreen";
}
