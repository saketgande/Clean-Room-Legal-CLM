/* Shared between the register list and the notice detail page, so a status
   colour or a deadline phrase can't drift between the two views. */

import type { Notice, NoticeDeadlineFacts, NoticeDeadlinePosture, NoticeStatus } from "@/lib/types";

export const NOTICE_TYPES = [
  { value: "demand", label: "Demand" },
  { value: "cease_and_desist", label: "Cease and desist" },
  { value: "breach", label: "Breach" },
  { value: "termination", label: "Termination" },
  { value: "statutory", label: "Statutory" },
  { value: "recovery", label: "Recovery" },
  { value: "infringement", label: "Infringement" },
  { value: "other", label: "Other" },
];

export const typeLabel = (v: string) =>
  NOTICE_TYPES.find((t) => t.value === v)?.label ?? v;

type Tone = "slate" | "blue" | "green" | "amber" | "red" | "violet";

export const STATUS_TONE: Record<NoticeStatus, Tone> = {
  draft: "slate",
  open: "blue",
  responded: "green",
  escalated: "red",
  closed: "slate",
};

export const POSTURE_TONE: Record<NoticeDeadlinePosture, Tone> = {
  none: "slate",
  needs_date: "amber",
  on_track: "green",
  at_risk: "amber",
  overdue: "red",
  met: "green",
};

export const REMINDER_LABEL: Record<"t7" | "t3" | "due" | "overdue", string> = {
  t7: "A week out",
  t3: "Due soon",
  due: "Due today",
  overdue: "Deadline missed",
};

/** Deadline as a human phrase. The raw date is useless at a glance; "3 days
 *  left" / "10 days overdue" is what a lawyer actually scans for. */
export function deadlineLabel(n: Notice): string {
  if (!n.response_due_date) return n.deadline_posture === "needs_date" ? "Needs a date" : "No deadline";
  const d = n.days_to_due;
  if (n.deadline_posture === "met") return n.responded_at ? "Answered" : "Closed";
  if (d === null) return n.response_due_date;
  if (d < 0) return `${Math.abs(d)} day${Math.abs(d) === 1 ? "" : "s"} overdue`;
  if (d === 0) return "Due today";
  return `${d} day${d === 1 ? "" : "s"} left`;
}

export const fmtDate = (iso: string | null) =>
  iso
    ? new Date(iso + "T00:00:00").toLocaleDateString(undefined, {
        day: "numeric",
        month: "short",
        year: "numeric",
      })
    : "—";

// ---- Deadline counting ------------------------------------------------------
// A port of notices/service.compute_due + core/deadlines (shift, explain) so the
// create form can show the formula live as the filer edits the dates. The server
// recounts on save from the same facts; the formula strings must match it.

export const UNCONFIRMED = "calculated - not yet confirmed";
export const STATUTORY_NOTE =
  "The notice states no deadline; a statutory period may apply. A person must set the date.";
const HOLIDAYS_NOTE = "public holidays not counted - please confirm";

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const UNIT_WORDS: Record<string, [string, string]> = {
  days: ["day", "days"],
  business_days: ["business day", "business days"],
  weeks: ["week", "weeks"],
  months: ["month", "months"],
};
const ANCHOR_LABELS: Record<string, string> = { notice_date: "notice dated", receipt: "received" };

// UTC throughout: local-time dates shift by a day across DST changes.
const parse = (iso: string) => {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(Date.UTC(y, m - 1, d));
};
const toIso = (d: Date) => d.toISOString().slice(0, 10);

/** "Wed 21 Oct 2026" — core/deadlines._fmt. */
export function fmtDay(iso: string): string {
  const d = parse(iso);
  return `${DAYS[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
}

const stepBusinessDays = (d: Date, count: number, step: 1 | -1) => {
  let left = count;
  while (left > 0) {
    d.setUTCDate(d.getUTCDate() + step);
    const wd = d.getUTCDay();
    if (wd !== 0 && wd !== 6) left -= 1;
  }
};

/** `count` units after `anchor`, the anchor day not counted; a month end is
 *  clamped (31 Jan + 1 month = 28/29 Feb); business days skip Sat/Sun only.
 *  Clear days end one day earlier (one business day for business days) — the
 *  safe side, as core/deadlines.shift does. */
export function shiftAfter(anchor: string, count: number, unit: string, clearDays = false): string {
  const d = parse(anchor);
  if (unit === "days" || unit === "weeks") {
    d.setUTCDate(d.getUTCDate() + count * (unit === "weeks" ? 7 : 1));
  } else if (unit === "months") {
    const idx = d.getUTCMonth() + count;
    const y = d.getUTCFullYear() + Math.floor(idx / 12);
    const m = idx % 12;
    const last = new Date(Date.UTC(y, m + 1, 0)).getUTCDate();
    d.setTime(Date.UTC(y, m, Math.min(d.getUTCDate(), last)));
  } else if (unit === "business_days") {
    stepBusinessDays(d, count, 1);
  } else {
    throw new Error(`unknown period unit ${unit}`);
  }
  if (clearDays) {
    if (unit === "business_days") stepBusinessDays(d, 1, -1);
    else d.setUTCDate(d.getUTCDate() - 1);
  }
  return toIso(d);
}

const ONES = [
  "zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten",
  "eleven", "twelve", "thirteen", "fourteen", "fifteen", "sixteen", "seventeen", "eighteen", "nineteen",
];
const TENS = ["twenty", "thirty", "forty", "fifty", "sixty", "seventy", "eighty", "ninety"];

/** Whether the quoted sentence itself states `n` ("15", "fifteen", "a month") —
 *  obligations/service._states_number. A number not in it came from a statute. */
export function statesNumber(n: number, quote: string): boolean {
  const text = quote.toLowerCase();
  if (new RegExp(`(?<!\\d)${n}(?!\\d)`).test(text)) return true;
  let words: string;
  if (n < 20) words = ONES[n];
  else if (n < 100) {
    const [tens, ones] = [Math.floor(n / 10), n % 10];
    words = TENS[tens - 2] + (ones ? `[\\s-]${ONES[ones]}` : "");
  } else return false;
  if (new RegExp(`\\b${words}\\b`).test(text)) return true;
  return n === 1 && /\ban?\s+(\w+\s+)?(business\s+)?(day|week|month|year)/.test(text);
}

export type CountedDue = { due: string | null; formula: string | null; reason: string | null; needs_person: boolean };

/** The response deadline counted from the notice's own wording. Anything code
 *  can't count gets no date and needs a person — a wrong deadline is worse than none. */
export function computeDue(
  facts: NoticeDeadlineFacts | null,
  noticeDate: string | null,
  receivedAt: string | null,
): CountedDue {
  if (!facts) return { due: null, formula: null, reason: null, needs_person: false };
  const needs = (reason: string): CountedDue => ({ due: null, formula: null, reason, needs_person: true });
  if (!facts.stated) return needs(STATUTORY_NOTE);
  const { period_count: count, period_unit: unit, runs_from: from } = facts;
  // A period beats a date read beside it: the date is the one the model could
  // have worked out itself.
  if (facts.explicit_date && (count == null || !unit)) {
    return {
      due: facts.explicit_date,
      formula: `due ${fmtDay(facts.explicit_date)} = stated in notice`,
      reason: null,
      needs_person: false,
    };
  }
  if (count == null || !unit) {
    return needs("The notice states a deadline but no date or period could be read. A person must set the date.");
  }
  if (!statesNumber(count, facts.quote ?? "")) {
    return needs(`The period (${count}) is not in the quoted sentence; it may come from a statute. A person must set the date.`);
  }
  if (!from || !(from in ANCHOR_LABELS)) {
    return needs(`The period runs from '${facts.runs_from_text || "an event code does not track"}'. A person must set the date.`);
  }
  const anchor = from === "notice_date" ? noticeDate : receivedAt;
  if (!anchor) {
    const missing = from === "notice_date" ? "date on the notice" : "received date";
    return needs(`The ${missing} is not set, so the period can't be counted. Enter it or set the date.`);
  }
  const clear = !!facts.clear_days;
  const due = shiftAfter(anchor, count, unit, clear);
  const [one, many] = UNIT_WORDS[unit];
  const words = `${clear ? "clear " : ""}${count === 1 ? one : many}`;
  let formula = `due ${fmtDay(due)} = ${ANCHOR_LABELS[from]} ${fmtDay(anchor)} + ${count} ${words}`;
  if (unit === "business_days") formula += ` (${HOLIDAYS_NOTE})`;
  return { due, formula, reason: null, needs_person: false };
}

/** A code-counted date nobody has confirmed (or whose anchor moved since) —
 *  notices/service.deadline_unconfirmed. */
export function deadlineUnconfirmed(n: Notice): boolean {
  const b = n.deadline_basis;
  return !!b && b.set_by === "document" && (!b.confirmed_by || b.needs_reconfirm);
}

export const NEEDS_RECONFIRM = "received or notice date changed - needs re-confirming";

/** The recount kept beside a confirmed date after its anchor moved: the formula
 *  (or why there is no date), so a person sees what accepting it would set. */
export function recountText(n: Notice): string | null {
  const r = n.deadline_basis?.recomputed;
  if (!r) return null;
  return r.formula ?? r.reason ?? "no date could be counted";
}
