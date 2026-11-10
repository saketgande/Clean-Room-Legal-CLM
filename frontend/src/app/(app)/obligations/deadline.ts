/* What the obligations page says about a date. The server computes every date
   from the clause's wording and stores the formula; this file only words the
   state for a person — kept pure so the wording is pinned by tests. */

import type { DateConflict, DateState, DeadlineAnchor, Obligation, ObligationStatus } from "@/lib/types";

export const UNCONFIRMED = "calculated - not yet confirmed";

const CONTRACT_ANCHORS = new Set(["effective_date", "expiration_date", "signature_date"]);

const ANCHOR_LABEL: Record<string, string> = {
  effective_date: "effective date",
  expiration_date: "expiry",
  signature_date: "signature date",
};

const SOURCE_LABEL: Record<string, string> = {
  user: "set by a person",
  form: "from the request form",
  ai: "read from the contract text",
  computed: "counted from the contract's term",
  change_request: "from a finished change request",
  signature: "from the completed signature",
};

/** One-line state shown under the date. null = an old row with no deadline
 *  block, which keeps the page's previous wording. */
export function dateState(o: Obligation): { text: string; tone: "good" | "warn" | "crit" | "muted" } | null {
  const d = o.deadline;
  if (!d) return null;
  if (d.needs_reconfirm) return { text: "the date it counts from changed - needs re-confirming", tone: "warn" };
  if (d.set_by_person) return { text: "set by a person", tone: "good" };
  if (d.confirmed) return { text: "confirmed", tone: "good" };
  if (d.formula) return { text: UNCONFIRMED, tone: "warn" };
  // Whether a missing date is an alarm is the server's call (date_state), not this file's: it used to read the reason text.
  // Only a real gap is red: a reason on a duty that is waiting for an event or has no fixed date is just the explanation.
  if (d.reason) return { text: d.reason, tone: o.date_state === "needs_person" ? "crit" : "muted" };
  return null;
}

/** The server's date state, for a row from a server that does not send one yet. */
export const stateOf = (o: Obligation): DateState => o.date_state ?? (o.due_date ? "dated" : "no_fixed_date");

export type TimingTone = "date" | "recurring" | "conditional" | "needs" | "unread";

/** What the Timing column says. A fixed date reads as a date and how far off it is. No date says WHY, by the server's
 *  date_state: waiting for an event (calm), needs a date (red), not read for dates (amber: it was extracted before
 *  dates were counted, so extraction needs re-running), or no fixed date (an ongoing or conditional duty). */
export function timingOf(
  o: Obligation, fmt: (iso: string) => string, now: number = Date.now(),
): { label: string; sub: string | null; tone: TimingTone } {
  if (o.due_date) {
    const days = Math.round((new Date(o.due_date).getTime() - now) / 86_400_000);
    const rel = days < 0 ? `${Math.abs(days)}d overdue` : days === 0 ? "today" : `in ${days}d`;
    return { label: fmt(o.due_date), sub: rel, tone: "date" };
  }
  switch (stateOf(o)) {
    case "waiting_event":
      return { label: "Waiting for event", sub: null, tone: "conditional" };
    case "needs_person":
      return { label: "Needs a date", sub: null, tone: "needs" };
    case "not_read":
      // Not "re-run extraction": for a contract whose extraction already succeeded the app refuses a second one, so the
      // one thing a person can always do is set the date by hand.
      return { label: "Not read for dates", sub: "read before dates were counted - set the date with Edit", tone: "unread" };
    default: {
      // An ongoing duty's end is never a due date, so it can't show as overdue.
      if (o.deadline?.ends_on) return { label: "No fixed date", sub: `Ends ${fmt(o.deadline.ends_on)}`, tone: "recurring" };
      if (o.recurrence) return { label: "No fixed date", sub: o.recurrence, tone: "recurring" };
      return { label: "No fixed date", sub: null, tone: "conditional" };
    }
  }
}

/** The Timing line in the open row: what the date is, or what is missing and what a person can do about it. `mayEdit`
 *  says whether this viewer has the Edit button, so nobody is told to press one they do not have. */
export function timingDetail(o: Obligation, fmt: (iso: string) => string, mayEdit: boolean): string {
  if (o.due_date) return `Due ${fmt(o.due_date)}`;
  const set = mayEdit ? "set one with Edit" : "someone who can edit this contract needs to set it";
  switch (stateOf(o)) {
    case "needs_person":
      return `Needs a date — ${set}`;
    case "not_read":
      return `Not read for dates — extracted before dates were counted; ${mayEdit ? "set the date with Edit" : "someone who can edit this contract needs to set it"}`;
    case "waiting_event":
      return "Waiting for the event it counts from — log its date to count it";
    default:
      return o.deadline?.ends_on ? `Ends on ${fmt(o.deadline.ends_on)}` : o.recurrence ?? "No fixed date — applies when it happens";
  }
}

/** The 'Date' filter on the page: the value sent as `date_state`, and what the person reads. */
export const DATE_FILTERS: { value: string; label: string }[] = [
  { value: "", label: "All dates" },
  { value: "dated", label: "Has a date" },
  { value: "needs_date", label: "Needs a date" },
  { value: "waiting_event", label: "Waiting for an event" },
  { value: "no_fixed_date", label: "No fixed date" },
];

/** How many obligations the list would hold if it were not capped, from the summary: null when two filters are on
 *  (the summary cannot say). Lets the page say "Showing 200 of 340" instead of looking complete. */
export function expectedTotal(
  s: { total: number; by_status: Record<string, number>; dates: Record<string, number>; needs_date: number },
  status: string, dateFilter: string,
): number | null {
  if (status && dateFilter) return null;
  if (status) return s.by_status[status] ?? null;
  if (dateFilter) return dateFilter === "needs_date" ? s.needs_date : s.dates[dateFilter] ?? null;
  return s.total;
}

/** Where the anchor date came from, e.g. "effective date 15 Oct 2026, from the request form". */
export function anchorText(a: DeadlineAnchor | null, fmt: (iso: string) => string): string | null {
  if (!a || a.conflict || !a.value) return null;
  const label = ANCHOR_LABEL[a.name] ?? "event date";
  const source = a.source ? SOURCE_LABEL[a.source] ?? a.source : null;
  return `${label} ${fmt(a.value)}${source ? `, ${source}` : ""}`;
}

/** Both values of a disputed anchor, so a person can see what to settle. */
export function conflictText(name: string, c: DateConflict, fmt: (iso: string) => string): string {
  const show = (v: string) => (/^\d{4}-\d{2}-\d{2}$/.test(v) ? fmt(v) : v);
  return `${ANCHOR_LABEL[name] ?? name}: request form says ${show(c.form)}, contract text says ${show(c.text)} - needs a person`;
}

const ISO = /^\d{4}-\d{2}-\d{2}$/;

/** The contract fields a person may settle a form-vs-text dispute on. */
export const SETTLEABLE = new Set(["effective_date", "expiration_date"]);

/** One button per side of a disputed anchor that is an actual date. A text side
 *  like "continues until terminated" has no date to pick, so it gets none. */
export function conflictChoices(c: DateConflict, fmt: (iso: string) => string): { label: string; value: string }[] {
  const out: { label: string; value: string }[] = [];
  if (ISO.test(c.form)) out.push({ label: `Use form date ${fmt(c.form)}`, value: c.form });
  if (ISO.test(c.text) && c.text !== c.form) out.push({ label: `Use contract-text date ${fmt(c.text)}`, value: c.text });
  return out;
}

/** The clause counts from an event the contract can't know about (invoice
 *  received, notice served...): a person logs the day it happened. */
export function needsEventDate(o: Obligation): boolean {
  const d = o.deadline;
  if (!d) return false;
  if (o.date_state === "waiting_event") return true;
  return d.anchor?.source === "user" && !CONTRACT_ANCHORS.has(d.anchor.name);
}

export interface EditDraft {
  ownerUserId: string;
  responsibleParty: string;
  obligationType: string;
  status: ObligationStatus;
  dueDate: string;
  recurrence: string;
}

/** What the edit dialog changed, and only that. Sending the whole form let a stale copy undo a
 *  colleague's change (a completed row reopened, an owner put back), and a due date sent unchanged
 *  is recorded as one a person typed, which is never recounted. */
export function editChanges(o: Obligation, d: EditDraft): Record<string, unknown> {
  const changes: Record<string, unknown> = {};
  if ((d.ownerUserId || null) !== o.owner_user_id) changes.owner_user_id = d.ownerUserId || null;
  if ((d.responsibleParty || null) !== o.responsible_party) changes.responsible_party = d.responsibleParty || null;
  if ((d.obligationType || null) !== o.obligation_type) changes.obligation_type = d.obligationType || null;
  if (d.status !== o.status) changes.status = d.status;
  if ((d.dueDate || null) !== o.due_date) changes.due_date = d.dueDate || null;
  if ((d.recurrence || null) !== o.recurrence) changes.recurrence = d.recurrence || null;
  return changes;
}
