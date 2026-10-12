/* What the obligations page says about a date. The server computes every date
   from the clause's wording and stores the formula; this file only words the
   state for a person — kept pure so the wording is pinned by tests. */

import type { DateConflict, DeadlineAnchor, Obligation } from "@/lib/types";

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
  if (d.reason) return { text: d.reason, tone: d.reason.startsWith("starts when") ? "muted" : "crit" };
  return null;
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
  if (d.reason?.startsWith("starts when")) return true;
  return d.anchor?.source === "user" && !CONTRACT_ANCHORS.has(d.anchor.name);
}
