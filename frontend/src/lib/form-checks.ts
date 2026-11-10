/**
 * Answers that cannot be right, caught while the person can still fix them (round 2, bug 7). The server
 * refuses the same answers when the request is filed (intake/service.py: looks_like_email, the form's
 * `not_before`, `no_blanks` and `min_exclusive` rules, and agreement_forms.clean_rows for a table question),
 * so what the wizard accepts is what filing accepts.
 *
 * The table rules (`rowsProblems`, `findBlanks`, `answerProblem`) are pinned to the server by two shared
 * cases files, backend/tests/fixtures/deliverable_rows_cases.json (007) and consultancy_dpa_cases.json (008),
 * which the backend suite and this one both read. Change a rule here and there together, and add the case.
 */

import type {
  FormRow, FormValue, RequestFormColumn, RequestFormDef, RequestFormField, RequestFormRule,
} from "./types";

/** One "@", something either side of it, a dot in the domain, no spaces. Only sending can tell whether the
 *  mailbox exists; this catches the typos that can never be delivered. */
export function looksLikeEmail(text: string): boolean {
  const t = text.trim();
  const at = t.indexOf("@");
  if (t.length > 254 || at < 1 || t.indexOf("@", at + 1) !== -1 || /\s/.test(t)) return false;
  const domain = t.slice(at + 1);
  return /^[^.].*\.[^.]+$/.test(domain) && !domain.includes("..");
}

const ISO_DAY = /^\d{4}-\d{2}-\d{2}$/;

export const EMAIL_PROBLEM = "Enter a full email address, like name@company.com.";

// ------------------------------------------------------------- blanks ----

// Port of backend/app/contracts/blanks.py. Python's \w, \W and \d are Unicode there, so the classes here are
// spelled out (\p{L}\p{N}_ and \p{Nd}); \b after an article becomes "not followed by a word character".
// ponytail: JS trim()/\s differ from Python strip()/\s only for \x1c-\x1f, \x85 and ﻿; no case needs them.
const MAX_LISTED = 20;
const BRACKET = /\[([^\[\]\n]{1,2000})\]/gu; // same bound as blanks.py _MAX_INSIDE
const MARKER = /^(?:[●•]+|_{2,})$/u;
const EDITORIAL: RegExp[] = [
  /^[^\p{L}\p{N}_]*$/u,
  /^\p{Nd}[\p{Nd}.,\-/]*$/u,
  /^[A-Za-z]$/,
  /^(?:sic|redacted|omitted|reserved|confidential|logo|seal|signature|signed)$/iu,
  /^emphasis\s+(?:added|supplied|omitted|in original)$/iu,
  /^(?:intentionally|deliberately)\s+(?:omitted|left blank|deleted)$/iu,
  /^signature\s+pages?\s+(?:follows?|attached)$/iu,
  /^(?:remainder|rest)\s+of\s+(?:this\s+)?page\s+(?:is\s+)?intentionally\s+left\s+blank$/iu,
  /^(?:schedule|exhibit|annex|annexure|appendix|addendum|attachment)s?\s+[\p{L}\p{N}_.\-]{1,12}$/iu,
  /^(?:the|a|an|its|their|our|his|her|this|that)(?![\p{L}\p{N}_])/u,
];

function isBlank(inside: string): boolean {
  const t = inside.trim();
  if (MARKER.test(t.replace(/ /g, ""))) return true;
  return !EDITORIAL.some((rule) => rule.test(t));
}

/** Each different blank in the text ("[TBD]", "[●]", "[Insert name]"), in the order it first appears. */
export function findBlanks(text: string): string[] {
  const seen = new Set<string>();
  for (const m of text.matchAll(BRACKET)) {
    if (!isBlank(m[1])) continue;
    seen.add(m[0]);
    if (seen.size >= MAX_LISTED) break;
  }
  return [...seen];
}

const BLANK_TAIL = "which reads as a blank to fill in later. Reword it without square brackets.";

// ----------------------------------------------------------- questions ----

/** A question or column applies only when every rule matches (a multi-select matches on any pick). A rule with
 *  `not` matches when the answer is none of `in`, so an unanswered question matches it. */
export function shown(rules: RequestFormRule[] | undefined, values: Record<string, unknown>): boolean {
  return (rules ?? []).every((r) => {
    const a = values[r.field];
    const hit = (Array.isArray(a) ? a : [a]).some((x) => typeof x === "string" && r.in.includes(x));
    return r.not ? !hit : hit;
  });
}

/** The choices a select offers now: the first `options_when` entry whose rules all match the answers (the
 *  server resolves it the same way), else the default `options`. */
export function optionsFor(field: RequestFormField, values: Record<string, unknown>): string[] {
  const hit = (field.options_when ?? []).find((o) => shown(o.when, values));
  return hit ? hit.options : field.options ?? [];
}

/** The columns of a table question that apply to the form's current answers (the Share column: Milestones only). */
export function shownColumns(field: RequestFormField, values: Record<string, unknown>): RequestFormColumn[] {
  return (field.columns ?? []).filter((c) => shown(c.show, values));
}

/** The single-question rules a question carries (`rulesFor` picks the ones in force). */
export interface AnswerRules { no_blanks?: boolean; min_exclusive?: number; max_length?: number; max_decimals?: number }

/** A question's own rules, or none while its `rules_when` does not match (Consultancy on our paper only, say). */
export function rulesFor(field: RequestFormField, values: Record<string, unknown>): AnswerRules {
  if (!shown(field.rules_when, values)) return {};
  const { no_blanks, min_exclusive, max_length, max_decimals } = field;
  return Object.fromEntries(Object.entries({ no_blanks, min_exclusive, max_length, max_decimals })
    .filter(([, v]) => v !== undefined));
}

const ASCII_WS_ENDS = /^[ \t\r\n\f\v]+|[ \t\r\n\f\v]+$/g;
/** A plain number: ASCII digits, an optional minus, an optional fraction. "1e3", ".5", "5.", "+25" and digits
 *  from other scripts are not (the server's `max_decimals` reading is the same). */
const PLAIN_NUMBER = /^-?[0-9]+(?:\.[0-9]+)?$/;

/**
 * What is wrong with an answer that is there, or null. `kind` is the server's kind for the question;
 * `notBefore` is the answer to the date this one must not come before, with that question's label;
 * `rules` are the question's own rules (`rulesFor`). The server words the rules with the question's label;
 * here the label sits above the answer, so the sentence starts at the verb. Its order is the server's:
 * length, blank brackets, then for a number "must be a number", above the floor, decimal places.
 */
export function answerProblem(
  kind: string | undefined,
  value: FormValue | undefined,
  notBefore?: { label: string; value: FormValue | undefined },
  rules?: AnswerRules,
  offered?: string[],
): string | null {
  if (typeof value !== "string" || !value.trim()) return null;
  // `offered` is given for a select only: a half-filled draft can hold a choice the form no longer offers.
  if (offered && !offered.includes(value)) return `"${value}" is no longer offered for this agreement. Pick again.`;
  if (kind === "email" && !looksLikeEmail(value)) return EMAIL_PROBLEM;
  if (kind === "date" && notBefore && typeof notBefore.value === "string") {
    // Both are ISO days (YYYY-MM-DD), which sort the way the days do.
    if (ISO_DAY.test(value) && ISO_DAY.test(notBefore.value) && value < notBefore.value)
      return `Can't be before ${notBefore.label.toLowerCase()}.`;
  }
  if (rules?.max_length !== undefined && cp(tidyCell(value)) > rules.max_length)
    return `Is longer than ${rules.max_length} characters.`;
  if (rules?.no_blanks) {
    const blank = findBlanks(value)[0];
    if (blank) return `Contains ${blank}, ${BLANK_TAIL}`;
  }
  if (kind === "number") {
    const typed = value.replace(/,/g, "").replace(ASCII_WS_ENDS, "");
    // With max_decimals the answer must be a plain number first; without it half-typed text is not refused.
    if (rules?.max_decimals !== undefined && !PLAIN_NUMBER.test(typed)) return "Must be a number.";
    const n = Number(typed);
    if (rules?.min_exclusive !== undefined && Number.isFinite(n) && n <= rules.min_exclusive)
      return `Must be more than ${rules.min_exclusive}.`;
    if (rules?.max_decimals !== undefined && (typed.split(".")[1]?.length ?? 0) > rules.max_decimals)
      return `Can have at most ${rules.max_decimals} decimal places.`;
  }
  return null;
}

/** The option a Consultancy or DPA request carries when we are the Processor (the form's own option text). */
export const PROCESSOR_ROLE = "Processor: we act on their instructions";

/** An answer that is not there for the draft: missing, empty, a list with nothing in it, or (for a multi-tick
 *  question) an older single choice that is not one of today's options, such as "Several". */
export function lacksAnswer(field: RequestFormField, value: unknown): boolean {
  if (value === null || value === undefined) return true;
  if (Array.isArray(value)) {
    return value.every((x) => (isRow(x) ? Object.values(x).every((c) => tidyCell(c) === "") : tidyCell(x) === ""));
  }
  if (typeof value === "string") {
    const t = tidyCell(value);
    return t === "" || (field.kind === "multiselect" && !(field.options ?? []).includes(t));
  }
  return false;
}

/** The form's own-paper choice for "Whose paper?". */
export const OUR_PAPER = "Our template";

/** The answers with "Whose paper?" read as our own paper when the request never gave one (an older request): the
 *  spec's own paper is "Our template, or no such answer". Only the draft's questions read it this way; the
 *  wizard's filing rules and `shown` itself are not changed. */
export function withOwnPaper(fv: Record<string, unknown>): Record<string, unknown> {
  return typeof fv.paper === "string" && fv.paper.trim() !== "" ? fv : { ...fv, paper: OUR_PAPER };
}

/**
 * The questions the draft still needs: shown, required, feeding the template (`fills`), and lacking an answer.
 * None for their paper (their draft is the contract) or when we are the Processor (no draft is made from our
 * template). A request with no "Whose paper?" answer counts as our own paper. The server's hold also checks the
 * wording in effect; this is the part the page can know.
 */
export function needsDraftAnswers(form: RequestFormDef, fv: Record<string, unknown>): RequestFormField[] {
  const own = withOwnPaper(fv);
  if (own.paper === "Their paper" || own.our_role === PROCESSOR_ROLE) return [];
  return form.fields.filter((f) => !!f.fills?.length && f.required && shown(f.show, own) && lacksAnswer(f, own[f.key]));
}

// -------------------------------------------------------------- tables ----

/** One thing wrong with a table. `message` is the server's text; `row` is 1-based as sent (empty rows count). */
export interface RowsProblem { row: number | null; column: string | null; message: string }

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
  "November", "December"];

function isRealDay(iso: string): boolean {
  if (!ISO_DAY.test(iso)) return false;
  const [y, m, d] = iso.split("-").map(Number);
  if (y < 1) return false; // Python's date() has no year 0, and Date would read 0-99 as 1900-1999
  const t = new Date(Date.UTC(2000, 0, 1));
  t.setUTCFullYear(y, m - 1, d);
  return t.getUTCFullYear() === y && t.getUTCMonth() === m - 1 && t.getUTCDate() === d;
}

/** The window a date column's `within` names. Both answers real days: the whole window. With `open_end`, a real
 *  start and an end that is not a day (no end date): the start bound only, `end` null. Otherwise it is not checked. */
export function dateWindow(
  col: RequestFormColumn,
  values: Record<string, unknown>,
): { start: string; end: string | null; startKey: string; endKey: string } | null {
  if (!col.within) return null;
  const [startKey, endKey] = col.within;
  const start = values[startKey];
  const end = values[endKey];
  if (typeof start !== "string" || !isRealDay(start)) return null;
  if (typeof end === "string" && isRealDay(end)) return { start, end, startKey, endKey };
  return col.open_end ? { start, end: null, startKey, endKey } : null;
}

/** "2026-11-01" → "1 November 2026" (the server's wording in the window message). */
export function longDate(iso: string): string {
  const [y, m, d] = iso.split("-").map(Number);
  return `${d} ${MONTHS[m - 1]} ${y}`;
}

/** A cell as the server reads it: nothing is "", anything else is text, runs of ASCII whitespace are one space
 *  and the ends are trimmed. ASCII only, on purpose: a no-break space is part of the text. */
export function tidyCell(raw: unknown): string {
  if (raw === null || raw === undefined) return "";
  return String(raw).replace(/[ \t\r\n\f\v]+/g, " ").replace(/^ | $/g, "");
}

const PERCENT = /^\d{1,3}(?:\.\d{1,2})?$/;

/** A share in hundredths of a percent (ASCII digits only), or null when it is not above 0 and at most 100. */
function hundredths(cell: string): number | null {
  if (!PERCENT.test(cell)) return null;
  const [whole, frac = ""] = cell.split(".");
  const n = Number(whole) * 100 + Number(frac.padEnd(2, "0"));
  return n > 0 && n <= 10_000 ? n : null;
}

/** 9950 → "99.5", 9900 → "99", 1 → "0.01": two decimals with trailing zeros removed. */
function percentText(h: number): string {
  const whole = Math.trunc(h / 100);
  const frac = h % 100;
  return frac === 0 ? `${whole}` : `${whole}.${String(frac).padStart(2, "0").replace(/0$/, "")}`;
}

const cp = (s: string) => [...s].length; // code points, as Python counts them

const isRow = (x: unknown): x is Record<string, unknown> => typeof x === "object" && x !== null && !Array.isArray(x);

/** The rows that count: tidied cells of the shown columns only, rows with nothing in them dropped, each with
 *  its 1-based position in what was sent. */
function readRows(columns: RequestFormColumn[], rows: Record<string, unknown>[]): { n: number; cells: FormRow }[] {
  return rows
    .map((r, i) => ({ n: i + 1, cells: Object.fromEntries(columns.map((c) => [c.key, tidyCell(r[c.key])])) }))
    .filter((r) => columns.some((c) => r.cells[c.key] !== ""));
}

/** What the server would keep of a table answer (before it writes shares to two decimals), or [] for a wrong shape. */
export function tidiedRows(field: RequestFormField, raw: unknown, values: Record<string, unknown>): FormRow[] {
  if (!Array.isArray(raw) || !raw.every(isRow)) return [];
  return readRows(shownColumns(field, values), raw).map((r) => r.cells);
}

/**
 * Everything wrong with a table answer, in the server's order (shape, row count, each cell row by row and
 * column by column, repeated names, shares adding up). The server stops at the first; the wizard lists them
 * all, so `[0]` is always the server's message. `raw` is whatever was sent: M1 and M2 cover the wrong shapes.
 */
export function rowsProblems(
  field: RequestFormField,
  raw: unknown,
  values: Record<string, unknown>,
  labels: Record<string, string>,
): RowsProblem[] {
  const L = field.label;
  const answer = raw === null || raw === undefined || raw === "" ? [] : raw;
  if (!Array.isArray(answer)) return [{ row: null, column: null, message: `"${L}" must be a table of rows, not free text.` }];
  const notRows = answer.flatMap((r, i): RowsProblem[] =>
    isRow(r) ? [] : [{ row: i + 1, column: null, message: `"${L}", row ${i + 1} is not a row of the table.` }]);
  if (notRows.length) return notRows;

  const columns = shownColumns(field, values);
  const rows = readRows(columns, answer as Record<string, unknown>[]);
  const min = field.min_rows ?? 1;
  const max = field.max_rows ?? Infinity;
  if (rows.length < min)
    return [{ row: null, column: null, message: `"${L}" needs at least ${min} ${min === 1 ? "row" : "rows"}.` }];
  const out: RowsProblem[] = [];
  if (rows.length > max)
    out.push({ row: null, column: null, message: `"${L}" can have at most ${max} rows (this has ${rows.length}).` });

  for (const { n, cells } of rows) {
    for (const c of columns) {
      const cell = cells[c.key];
      const at = (what: string) => out.push({ row: n, column: c.key, message: `"${L}", row ${n}: "${c.label}" ${what}` });
      if (cell === "") { at("is required."); continue; }
      if (c.kind === "text") {
        // In the server's order, one message a cell: length, blank bracket, the value to keep out, the value it must be.
        const blank = c.no_blanks ? findBlanks(cell)[0] : undefined;
        const lower = cell.toLowerCase();
        const must = c.must_be && shown([c.must_be.when], values) ? c.must_be : null;
        if (c.max_length !== undefined && cp(cell) > c.max_length) at(`is longer than ${c.max_length} characters.`);
        else if (blank) at(`contains ${blank}, ${BLANK_TAIL}`);
        else if (c.outside !== undefined && lower === c.outside.toLowerCase())
          at(`is ${c.outside}. List only the countries outside ${c.outside}.`);
        else if (must && lower !== must.value.toLowerCase()) {
          const given = values[must.when.field];
          const answer = Array.isArray(given) ? given.find((x) => must.when.in.includes(x)) : given;
          at(`is "${cell}", but the answer to "${labels[must.when.field] ?? must.when.field}" is ${answer}. Enter ${must.value}, or change that answer.`);
        }
      } else if (c.kind === "date") {
        if (!isRealDay(cell)) { at("must be a date in YYYY-MM-DD form."); continue; }
        const w = dateWindow(c, values);
        if (w?.end === null && cell < w.start) at(`must be on or after ${longDate(w.start)} (${labels[w.startKey] ?? w.startKey}).`);
        else if (w?.end && (cell < w.start || cell > w.end))
          at(`must be between ${longDate(w.start)} and ${longDate(w.end)} (${labels[w.startKey] ?? w.startKey} to ${labels[w.endKey] ?? w.endKey}).`);
      } else if (hundredths(cell) === null) {
        at("must be a number above 0 and at most 100, with no more than two decimal places.");
      }
    }
  }

  for (const c of columns.filter((x) => x.unique)) {
    const first = new Map<string, { n: number; cell: string }>();
    for (const { n, cells } of rows) {
      const cell = cells[c.key];
      if (cell === "") continue;
      const earlier = first.get(cell.toLowerCase());
      if (earlier)
        out.push({ row: n, column: c.key, message: `"${L}", rows ${earlier.n} and ${n} have the same "${c.label}" ("${earlier.cell}"). Give each row its own.` });
      else first.set(cell.toLowerCase(), { n, cell });
    }
  }

  for (const c of columns.filter((x) => x.total !== undefined)) {
    // A share that is missing or wrong is reported on its own row; adding up what is left would only mislead.
    if (rows.some((r) => hundredths(r.cells[c.key]) === null)) continue;
    const sum = rows.reduce((t, r) => t + (hundredths(r.cells[c.key]) ?? 0), 0);
    const want = c.total! * 100;
    if (sum !== want) {
      const gap = percentText(Math.abs(want - sum));
      out.push({ row: null, column: c.key,
        message: `"${L}": the "${c.label}" column adds up to ${percentText(sum)}%, ${gap} ${sum < want ? "short of" : "over"} ${c.total}%.` });
    }
  }
  return out;
}

/** The running total of the table's share column, as text ("75", "99.5"), or null when it has none. Cells that
 *  are not yet a valid share (still being typed) add nothing. Whether the column is shown is the caller's call. */
export function shareTotal(field: RequestFormField, rows: FormRow[]): string | null {
  const c = (field.columns ?? []).find((x) => x.total !== undefined);
  if (!c) return null;
  return percentText(rows.reduce((t, r) => t + (hundredths(tidyCell(r[c.key])) ?? 0), 0));
}

/** 1-based numbers of the rows whose due date is a real day before `today` (an ISO day). Whether it is also
 *  outside the Services window is the table's other check; this is only "already passed" (FR-12). */
export function pastDueRows(field: RequestFormField, rows: FormRow[], today: string): number[] {
  const c = (field.columns ?? []).find((x) => x.kind === "date");
  if (!c) return [];
  return rows.flatMap((r, i) => {
    const due = tidyCell(r[c.key]);
    return isRealDay(due) && due < today ? [i + 1] : [];
  });
}
