/**
 * Smart edits for "Your request in words" (spec 013). The requester may reword a line of the summary; this reads
 * what they typed and OFFERS to update the matching answer only when the change is certain. Anything else is kept
 * as an extra instruction for Legal with a note saying how it differs from the answer. Never a guess.
 *
 * Pure functions: no React, no API, no AI. Checking never changes an answer; `acceptOffer` is the only function
 * that returns an answer patch, and the wizard applies it with its own `set` (the same path as a form control).
 */

import type {
  FormValue, RequestFormDef, RequestFormField, SummaryBoxState, SummaryDiffers, SummaryEdit, SummaryOffer,
} from "./types";
import { answerProblem, rulesFor, shown, tidyCell } from "./form-checks";
import {
  SENTENCE_RULES, SYNONYMS, _rules, answerText, moneyText, nameOf, writtenDate,
  type SentenceRule, type SummaryContext, type SummaryLine,
} from "./request-summary";

export const WORD_LIMIT = 2000;
export const WORD_WARN = 1800;
export const EMPTY_BOX: SummaryBoxState = { edits: {}, offers: [], instructions: [], accepted: [], pending: "" };

export interface CheckContext {
  form: RequestFormDef;
  def: { steps: { fields: string[] }[] };
  values: Record<string, FormValue>;
  lines: SummaryLine[];
  ctx: SummaryContext;
  /** Default navigator.language; injected in tests. */
  locale?: string;
}

type Check = NonNullable<SummaryEdit["check"]>;
const NO_CHECK: Check = { templateMatched: false, differs: [], declined: [] };
const KEPT = "Kept as an extra instruction for Legal.";

/** Register picks are never patched: no pick changes and nothing is created from a sentence (FR-22). */
const REGISTER = new Set(["entity", "counterparty", "parent_contract_id", "cancel_request_ref", "incoming_party"]);
const MONEY = new Set(["value", "new_value", "renew_value"]);

// -------------------------------------------------------------- reading ----

/** NFKC, lower case, no quotes, one space, no end-of-sentence mark. */
export function norm(s: string): string {
  return s.normalize("NFKC").toLowerCase().replace(/[“”"‘’]/g, "").replace(/\s+/g, " ").trim().replace(/[.!]$/, "").trim();
}
const normChoice = (s: string) => norm(s).replace(/^(a|an|the) /, "");
const esc = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
const spaced = (s: string) => esc(s).replace(/ +/g, "\\s+");
const wordsRe = (s: string) => new RegExp(`(?<![\\p{L}\\p{N}])${esc(s)}(?![\\p{L}\\p{N}])`, "u");

const MONTHS: Record<string, number> = {
  jan: 1, january: 1, feb: 2, february: 2, mar: 3, march: 3, apr: 4, april: 4, may: 5, jun: 6, june: 6, jul: 7, july: 7,
  aug: 8, august: 8, sep: 9, sept: 9, september: 9, oct: 10, october: 10, nov: 11, november: 11, dec: 12, december: 12,
};
const MON = "(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sept?(?:ember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)";
const DATE_RE = new RegExp(
  `(?<!\\d)(\\d{4})-(\\d{2})-(\\d{2})(?!\\d)`
  + `|(?<!\\d)(\\d{1,2})(?:st|nd|rd|th)?\\s+${MON}\\.?,?\\s+(\\d{4})(?!\\d)`
  + `|${MON}\\.?\\s+(\\d{1,2})(?:st|nd|rd|th)?,?\\s+(\\d{4})(?!\\d)`
  + `|(?<!\\d)(\\d{1,2})[/.-](\\d{1,2})[/.-](\\d{4})(?!\\d)`, "gi");

const realDay = (y: number, m: number, d: number) => {
  if (y < 1 || m < 1 || m > 12 || d < 1) return false;
  const t = new Date(Date.UTC(2000, 0, 1));
  t.setUTCFullYear(y, m - 1, d);
  return t.getUTCFullYear() === y && t.getUTCMonth() === m - 1 && t.getUTCDate() === d;
};
const iso = (y: number, m: number, d: number) => `${String(y).padStart(4, "0")}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;

/** Whether the browser writes the day before the month (the order the app writes dates in). */
function dayFirst(locale?: string): boolean {
  try {
    const l = locale ?? (typeof navigator !== "undefined" ? navigator.language : "en-IN");
    const parts = new Intl.DateTimeFormat(l).formatToParts(new Date(2000, 10, 25)).map((p) => p.type);
    return parts.indexOf("day") < parts.indexOf("month");
  } catch {
    return false;
  }
}

/** An ISO day, or null when the text holds no date, two dates, a two-digit year, an impossible day or a day/month
 *  order that cannot be settled (D8). */
export function parseDate(text: string, locale?: string): string | null {
  const found = [...text.matchAll(DATE_RE)];
  if (found.length !== 1) return null;
  const m = found[0];
  if (/\d/.test(text.replace(m[0], " "))) return null;
  const month = (name: string) => MONTHS[name.toLowerCase()] ?? MONTHS[name.toLowerCase().slice(0, 3)];
  const named = (y: string, mo: string, d: string) => {
    const n = month(mo);
    return n && realDay(+y, n, +d) ? iso(+y, n, +d) : null;
  };
  if (m[1] !== undefined) return realDay(+m[1], +m[2], +m[3]) ? iso(+m[1], +m[2], +m[3]) : null;
  if (m[4] !== undefined) return named(m[6], m[5], m[4]);
  if (m[7] !== undefined) return named(m[9], m[7], m[8]);
  const a = +m[10], b = +m[11], y = +m[12];
  const dm = realDay(y, b, a) ? iso(y, b, a) : null; // a is the day
  const md = realDay(y, a, b) ? iso(y, a, b) : null; // a is the month
  if (dm && md) return dm === md || dayFirst(locale) ? dm : null;
  return dm ?? md;
}

const CURRENCIES: Record<string, string> = {
  inr: "INR", "₹": "INR", rs: "INR", "rs.": "INR", rupees: "INR",
  usd: "USD", $: "USD", "us$": "USD", dollars: "USD",
  eur: "EUR", "€": "EUR", euros: "EUR",
  gbp: "GBP", "£": "GBP", pounds: "GBP",
};
const TOKEN = "us\\$|inr|usd|eur|gbp|rupees|dollars|euros|pounds|rs\\.?|₹|\\$|€|£";
const SUFFIX: Record<string, number> = {
  lakh: 1e5, lakhs: 1e5, lac: 1e5, lacs: 1e5, l: 1e5, crore: 1e7, crores: 1e7, cr: 1e7,
  k: 1e3, thousand: 1e3, m: 1e6, mn: 1e6, million: 1e6,
};

/** An amount as plain digits, with the currency when one is named; null when it is not certain what is meant
 *  ("fifty lakh", "5.000.000", "5 000 000", ranges, "approx."). */
export function parseMoney(text: string): { amount: string; currency: string | null } | null {
  let t = text.trim().toLowerCase();
  const lead = new RegExp(`^(${TOKEN})\\s*`).exec(t);
  if (lead) t = t.slice(lead[0].length);
  const trail = new RegExp(`\\s*(${TOKEN})$`).exec(t);
  if (trail) t = t.slice(0, -trail[0].length);
  if (lead && trail) return null;
  const tok = (lead ?? trail)?.[1];
  let n: number;
  // Only real digit grouping: Western (1,234,567) or Indian (12,34,567); "1,50" or "1,234,56" are not certain.
  if (/^(\d{1,3}(,\d{3})+|\d{1,2}(,\d{2})*,\d{3})(\.\d{1,2})?$/.test(t)) n = Number(t.replace(/,/g, ""));
  else if (/^\d+(\.\d{1,2})?$/.test(t)) n = Number(t);
  else {
    const s = /^(\d+(?:\.\d+)?)\s*(lakhs?|lacs?|l|crores?|cr|k|thousand|m|mn|million)$/.exec(t);
    if (!s) return null;
    n = Number(s[1]) * SUFFIX[s[2]];
  }
  const cents = n * 100;
  if (!Number.isFinite(n) || n <= 0 || n > 1e15 || Math.abs(cents - Math.round(cents)) > 1e-4) return null;
  return { amount: String(Math.round(cents) / 100), currency: tok ? CURRENCIES[tok] ?? null : null };
}

// ------------------------------------------------------------- choices ----

const WORDS: Record<string, number> = {
  one: 1, two: 2, three: 3, four: 4, five: 5, six: 6, seven: 7, eight: 8, nine: 9, ten: 10,
  thirty: 30, "forty-five": 45, "forty five": 45, sixty: 60, ninety: 90,
};
const DURATION = /^(\d+|one|two|three|four|five|six|seven|eight|nine|ten|thirty|forty[- ]five|sixty|ninety) ?(years?|yrs?|months?|days?)$/;

/** "3 years", "three yrs", "36 months" → {3, y}; "45 days" → {45, d}. Months only when they make whole years. */
function duration(t: string): { n: number; unit: "y" | "d" } | null {
  const m = DURATION.exec(t);
  if (!m) return null;
  const n = WORDS[m[1]] ?? Number(m[1]);
  if (!(n > 0)) return null;
  if (m[2].startsWith("d")) return { n, unit: "d" };
  if (m[2].startsWith("m")) return n % 12 === 0 ? { n: n / 12, unit: "y" } : null;
  return { n, unit: "y" };
}
const isDurationField = (f: RequestFormField) => !!f.options?.length && f.options.every((o) => /^\d+ (year|years|day|days)$/.test(o));

/** Every wording that stands for an option: label, its phrase in the rule, the frozen synonyms. */
function spellings(field: RequestFormField, ruleId: string, option: string): string[] {
  const rule = SENTENCE_RULES.find((r) => r.id === ruleId && r.phrases?.[field.key]);
  const raw = [option, rule?.phrases?.[field.key]?.[option], ...(SYNONYMS[field.key]?.[option] ?? [])];
  return [...new Set(raw.filter((x): x is string => !!x).flatMap((x) => [norm(x), normChoice(x)]))];
}

/** The one option the text names, or null (none, or more than one). */
export function matchChoice(field: RequestFormField, ruleId: string, text: string): string | null {
  const opts = field.options ?? [];
  const t = normChoice(text);
  if (!t) return null;
  const hits = opts.filter((o) => spellings(field, ruleId, o).includes(t));
  if (hits.length === 1) return hits[0];
  if (hits.length === 0 && isDurationField(field)) {
    const d = duration(t);
    const same = d ? opts.filter((o) => { const x = duration(o); return x && x.n === d.n && x.unit === d.unit; }) : [];
    if (same.length === 1) return same[0];
  }
  return null;
}

/** Options named in already-normalised text (longest wording first, so "goods and services" is not also "goods");
 *  `rest` is what is left. */
function findOptions(field: RequestFormField, ruleId: string, text: string): { found: Set<string>; rest: string } {
  const all = (field.options ?? []).flatMap((o) => spellings(field, ruleId, o).map((s) => ({ o, s })))
    .filter((x) => x.s).sort((a, b) => b.s.length - a.s.length);
  const found = new Set<string>();
  let rest = text;
  for (const { o, s } of all) {
    const re = wordsRe(s);
    if (re.test(rest)) { found.add(o); rest = rest.replace(new RegExp(re.source, "gu"), " "); }
  }
  return { found, rest };
}

/** Several ticked options spelled out ("a, b and c"); null when anything else is left over. */
function matchChoices(field: RequestFormField, ruleId: string, text: string): string[] | null {
  const { found, rest } = findOptions(field, ruleId, norm(text));
  const left = rest.replace(/[,;&]|\band\b/g, " ").trim();
  if (left || (!found.size && field.required)) return null;
  return (field.options ?? []).filter((o) => found.has(o));
}

/** Words that make an amount approximate or a bound; the template path and the scan path both refuse them. */
const HEDGE = /(?<![\p{L}])(about|around|approx\.?|approximately|roughly|nearly|almost|up to|upto|over|under|at least|at most|more than|less than|maybe)(?![\p{L}])|~/iu;

/** The one amount in free text (for a line the template did not match), or null. */
function findMoney(text: string): { amount: string; currency: string | null } | null {
  if (HEDGE.test(text)) return null;
  const re = new RegExp(`(?:(?:${TOKEN})\\s*)?\\d[\\d,]*(?:\\.\\d+)?(?:\\s*(?:lakhs?|lacs?|crores?|cr|thousand|million|mn|k|m|l)(?![\\p{L}]))?(?:\\s*(?:inr|usd|eur|gbp|rupees|dollars|euros|pounds))?`, "giu");
  const hits = [...text.matchAll(re)].map((m) => m[0].replace(/[,.]+$/, ""));
  return hits.length === 1 ? parseMoney(hits[0]) : null;
}

// ------------------------------------------------------------- helpers ----

const fieldOf = (c: CheckContext, k: string) => c.form.fields.find((f) => f.key === k);
const applies = (c: CheckContext, k: string) => { const f = fieldOf(c, k); return !!f && shown(f.show, c.values); };
const oldTextFor = (keys: string[], c: CheckContext) =>
  keys.filter((k) => k !== "currency").map((k) => answerText(c.form, k, c.values, c.ctx)).join("; ");
const ruleOf = (l: SummaryLine, c: CheckContext): SentenceRule | undefined =>
  SENTENCE_RULES.find((r) => r.id === l.id && r.keys.length === l.keys.length && _rules.applies(r, c.form));
const diff = (c: CheckContext, key: string): SummaryDiffers =>
  ({ key, label: nameOf(c.form, key), answer: answerText(c.form, key, c.values, c.ctx) });
const omit = <T,>(o: Record<string, T>, k: string): Record<string, T> => { const { [k]: _gone, ...rest } = o; return rest; };
const openOffers = (b: SummaryBoxState, line: string) => b.offers.some((o) => o.line === line);

/** The slot text exactly as the sentence was built: option → its phrase in this rule. */
function slotOf(rule: SentenceRule, key: string, c: CheckContext): string {
  const v = c.values[key];
  if (fieldOf(c, key)?.kind === "select" && typeof v === "string") return rule.phrases?.[key]?.[tidyCell(v)] ?? tidyCell(v);
  return answerText(c.form, key, c.values, c.ctx);
}

interface Cand { patch: Record<string, FormValue>; newText: string }

function sameAnswer(c: CheckContext, key: string, v: FormValue): boolean {
  const cur = c.values[key];
  const f = fieldOf(c, key);
  if (Array.isArray(v)) return Array.isArray(cur) && [...cur].sort().join("\n") === [...v].sort().join("\n");
  if (f?.kind === "number") return typeof cur === "string" && cur.trim() !== "" && Number(cur.replace(/,/g, "")) === Number(v);
  return typeof cur === "string" && tidyCell(cur) === tidyCell(v);
}

/** Money as a candidate: the amount, plus the currency when one is named and differs (one combined offer). */
function moneyCand(c: CheckContext, key: string, m: { amount: string; currency: string | null }): Cand | null {
  const cur = typeof c.values.currency === "string" && c.values.currency.trim() ? c.values.currency : "INR";
  const patch: Record<string, FormValue> = { [key]: m.amount };
  if (m.currency && m.currency !== cur) {
    // The currency is shared with the total value, so the rate never changes it, and a form without that question cannot.
    if (key === "fee_rate" || !fieldOf(c, "currency")?.options?.includes(m.currency)) return null;
    patch.currency = m.currency;
  }
  return { patch, newText: moneyText(m.currency ?? cur, m.amount) };
}

/** What a changed slot says, when it is certain; null otherwise. */
function candFromText(c: CheckContext, rule: SentenceRule, key: string, text: string): Cand | null {
  const f = fieldOf(c, key);
  if (!f || REGISTER.has(key) || f.kind === "rows") return null;
  switch (f.kind) {
    case "select": { const o = matchChoice(f, rule.id, text); return o ? { patch: { [key]: o }, newText: o } : null; }
    case "multiselect": { const a = matchChoices(f, rule.id, text); return a ? { patch: { [key]: a }, newText: a.join(", ") } : null; }
    case "date": { const d = parseDate(text, c.locale); return d ? { patch: { [key]: d }, newText: writtenDate(d) } : null; }
    case "number": {
      const m = HEDGE.test(text) ? null : parseMoney(text);
      if (!m) return null;
      if (MONEY.has(key) || key === "fee_rate") return moneyCand(c, key, m);
      return m.currency ? null : { patch: { [key]: m.amount }, newText: m.amount };
    }
    default: { const t = tidyCell(text); return t ? { patch: { [key]: t }, newText: t } : null; }
  }
}

const NEGATION = /(?<![\p{L}])(no|not|dont|do not|never|without|except|excluding|other than)(?![\p{L}])/iu;
const negations = (s: string) => (s.match(new RegExp(NEGATION.source, "giu")) ?? []).length;

/** A changed slot in a line the template did not match: only a clear single reading counts. */
function candFromScan(c: CheckContext, rule: SentenceRule, key: string, edit: string): Cand | "same" | null {
  const f = fieldOf(c, key);
  if (!f) return null;
  const text = norm(edit);
  const cur = slotOf(rule, key, c);
  if (f.kind === "select") {
    const { found, rest } = findOptions(f, rule.id, text);
    // "Don't host in the US" names an option but means the opposite: never offer it.
    if (NEGATION.test(rest)) return null;
    const now = typeof c.values[key] === "string" ? tidyCell(c.values[key] as string) : "";
    if (found.has(now)) return "same";
    const [only] = [...found];
    return found.size === 1 && !REGISTER.has(key) ? { patch: { [key]: only }, newText: only } : null;
  }
  if (cur && cur !== "no answer" && wordsRe(norm(cur)).test(text)) return "same";
  if (f.kind === "date") { const d = parseDate(edit, c.locale); return d ? { patch: { [key]: d }, newText: writtenDate(d) } : null; }
  if (f.kind === "number" && (MONEY.has(key) || key === "fee_rate")) { const m = findMoney(edit); return m ? moneyCand(c, key, m) : null; }
  return null;
}

/** Turns a candidate into an offer, "same" (nothing to do) or null (never offer an answer the form would refuse). */
function offerFor(c: CheckContext, line: string, key: string, cand: Cand | "same" | null): SummaryOffer | "same" | null {
  if (cand === "same") return "same";
  if (!cand) return null;
  const entries = Object.entries(cand.patch);
  if (entries.every(([k, v]) => sameAnswer(c, k, v))) return "same";
  const f = fieldOf(c, key)!;
  const v = cand.patch[key];
  if (typeof v === "string") {
    const before = f.not_before ? fieldOf(c, f.not_before) : undefined;
    const merged = { ...c.values, ...cand.patch };
    const problem = answerProblem(f.kind, v, before ? { label: before.label, value: c.values[before.key] } : undefined, rulesFor(f, merged));
    if (problem) return null;
  }
  const keys = Object.keys(cand.patch);
  return {
    id: `${line}:${keys.join("+")}`, line, kind: "update", keys, label: nameOf(c.form, key),
    oldText: oldTextFor([key], c), newText: cand.newText, patch: cand.patch,
  };
}

// ------------------------------------------------------- the template ----

/** Regex from the rule's template with the current slot texts (tried first, so a name holding "and" stays whole). */
function templateRegex(rule: SentenceRule, slots: Record<string, string>): RegExp {
  const parts = rule.text!.split(/\{(\w+)\}/);
  const body = parts.map((p, i) => {
    if (i % 2 === 1) return slots[p] ? `(${spaced(slots[p])}|.+?)` : "(.+?)";
    const fixed = i === parts.length - 1 ? p.replace(/\.$/, "") : p;
    return spaced(fixed).replace(/[“”]/g, `["“”]?`);
  }).join("");
  return new RegExp(`^\\s*${body}[.!]?\\s*$`, "is");
}

/** The slot texts of a template match, or null; a single free-text slot behind "Prefix: " may also be typed bare. */
function matchTemplate(rule: SentenceRule, text: string, c: CheckContext): Record<string, string> | null {
  const slots: Record<string, string> = {};
  for (const k of rule.keys) slots[k] = slotOf(rule, k, c);
  const m = templateRegex(rule, slots).exec(text);
  if (m) return Object.fromEntries(rule.keys.map((k, i) => [k, m[i + 1]]));
  const kind = fieldOf(c, rule.keys[0])?.kind;
  if (rule.keys.length === 1 && (kind === "text" || kind === "textarea") && /^[^{}]+:\s*\{\w+\}\.?$/.test(rule.text ?? ""))
    return { [rule.keys[0]]: text.trim().replace(/[.!]$/, "") };
  return null;
}

// ------------------------------------------------------------ resolving ----

/** Ends an edit whose line has no open offers left: dropped when every change is now in the answers, otherwise kept
 *  as an extra instruction with a note for each answer it differs from. */
function settle(box: SummaryBoxState, lineId: string, c: CheckContext, clearAccepted = false): { box: SummaryBoxState; kept: boolean } {
  const E = box.edits[lineId];
  if (!E) return { box, kept: false };
  const base = { ...box, edits: omit(box.edits, lineId) };
  const ck = E.check ?? NO_CHECK;
  const keys = [...new Set([...ck.differs.map((d) => d.key), ...ck.declined])];
  const deleted = norm(E.text) === "";
  if (clearAccepted || norm(E.text) === norm(E.base) || (deleted && !keys.length) || (ck.templateMatched && !keys.length && !deleted))
    return { box: base, kept: false };
  const differs = keys.map((k) => diff(c, k));
  const text = deleted ? `Remove this line: “${E.base}”` : E.text.trim();
  return { box: { ...base, instructions: [...base.instructions, { text, differs }] }, kept: true };
}

const asAnswer = (v: FormValue | undefined): string | string[] | null =>
  typeof v === "string" ? v : Array.isArray(v) && v.every((x) => typeof x === "string") ? (v as string[]) : null;

// -------------------------------------------------------------- the API ----

/** Typing only stores the line; nothing is checked until the line is left (D3). */
export function editLine(box: SummaryBoxState, lineId: string, text: string, c: CheckContext): SummaryBoxState {
  const L = c.lines.find((l) => l.id === lineId);
  if (!L || !L.editable) return box;
  const prev = box.edits[lineId];
  return {
    ...box, offers: box.offers.filter((o) => o.line !== lineId),
    edits: { ...box.edits, [lineId]: { text, base: prev?.base ?? L.text, check: null } },
  };
}

export function checkLine(box: SummaryBoxState, lineId: string, c: CheckContext): { box: SummaryBoxState; announce: string } {
  const E = box.edits[lineId];
  if (!E) return { box, announce: "" };
  const L = c.lines.find((l) => l.id === lineId);
  const done = (b: SummaryBoxState, check: Check, offers: SummaryOffer[], announce?: string) => {
    const open = { ...b, offers: [...b.offers.filter((o) => o.line !== lineId), ...offers], edits: { ...b.edits, [lineId]: { ...E, check } } };
    if (offers.length) {
      return { box: open, announce: announce ?? offers.map((o) => `Aegis can update ${o.label}: ${o.oldText} → ${o.newText}.`).join(" ") };
    }
    const r = settle(open, lineId, c);
    return { box: r.box, announce: r.kept ? KEPT : "" };
  };
  const dropped = { ...box, edits: omit(box.edits, lineId), offers: box.offers.filter((o) => o.line !== lineId) };
  if (!L || !L.editable) return done(box, NO_CHECK, []);
  if (norm(E.text) === norm(L.text)) return { box: dropped, announce: "" };
  const rule = ruleOf(L, c);
  const keys = L.keys;
  // The text is unchanged since the last check (typing clears it), so an offer already declined stays declined.
  const kept = E.check?.declined ?? [];

  // 1. A deleted line.
  if (norm(E.text) === "") {
    if (L.required) return { box: dropped, announce: `${L.name} is required, so its line can't be removed. The line is back.` };
    if (rule?.rows || keys.some((k) => REGISTER.has(k))) {
      return done(box, { templateMatched: false, differs: keys.map((k) => diff(c, k)), declined: [] }, []);
    }
    const patch = Object.fromEntries(keys.map((k) => [k, ["multiselect", "rows"].includes(fieldOf(c, k)?.kind ?? "") ? [] : ""]));
    const offer: SummaryOffer = {
      id: `${L.id}:${keys.join("+")}`, line: L.id, kind: "clear", keys, label: L.name,
      oldText: oldTextFor(keys, c), newText: "no answer", patch,
    };
    return done(box, { templateMatched: true, differs: [], declined: [] }, [offer]);
  }

  // 2. A table summary is never applied: a table has several columns (D6).
  if (rule?.rows) return done(box, { templateMatched: false, differs: [diff(c, keys[0])], declined: [] }, []);

  // 3. A one-choice line with a fixed sentence per option.
  if (rule?.variants) {
    const key = keys[0];
    const f = fieldOf(c, key)!;
    const t = norm(E.text);
    const hit = Object.entries(rule.variants).find(([o, s]) => norm(s) === t && !sameAnswer(c, key, o))?.[0];
    const o = hit ? offerFor(c, L.id, key, { patch: { [key]: f.kind === "multiselect" ? [hit] : hit }, newText: hit }) : null;
    if (o && o !== "same") {
      return kept.includes(key) ? done(box, { templateMatched: true, differs: [], declined: [key] }, [])
        : done(box, { templateMatched: true, differs: [], declined: [] }, [o]);
    }
    const cur = Object.entries(rule.variants).find(([opt]) => asAnswer(c.values[key]) !== null && [asAnswer(c.values[key])].flat().includes(opt))?.[1];
    const other = Object.values(rule.variants).map(norm).filter((v) => v !== (cur ? norm(cur) : ""));
    // Agrees only when it is the answer's sentence, or holds it with nothing that turns it round.
    const only = !!cur && (t === norm(cur) || (t.includes(norm(cur)) && negations(t) <= negations(norm(cur)) && !other.some((v) => t.includes(v))));
    return done(box, { templateMatched: false, differs: only ? [] : [diff(c, key)], declined: [] }, []);
  }

  // 4. A template line.
  if (!rule?.text) return done(box, { templateMatched: false, differs: keys.map((k) => diff(c, k)), declined: [] }, []);
  const slots = matchTemplate(rule, E.text, c);
  const offers: SummaryOffer[] = [];
  const differs: SummaryDiffers[] = [];
  const declined: string[] = [];
  for (const key of keys) {
    let r: SummaryOffer | "same" | null;
    if (slots) {
      const cap = slots[key] ?? "";
      r = norm(cap) === norm(slotOf(rule, key, c)) ? "same" : offerFor(c, L.id, key, candFromText(c, rule, key, cap));
    } else {
      r = offerFor(c, L.id, key, candFromScan(c, rule, key, E.text));
    }
    if (r === null) differs.push(diff(c, key));
    else if (r !== "same") { if (kept.includes(key)) declined.push(key); else offers.push(r); }
  }
  return done(box, { templateMatched: !!slots, differs, declined }, offers);
}

// ---------------------------------------------------- the one free-text box ----

const wordSet = (s: string) => new Set(norm(s).split(/[^\p{L}\p{N}]+/u).filter(Boolean));

/** How alike a typed line is to a sentence: shared words over all words, plus a bonus for the same template start. */
function similarity(t: string, l: SummaryLine, c: CheckContext): number {
  const a = wordSet(t), b = wordSet(l.text);
  const shared = [...a].filter((w) => b.has(w)).length;
  const jaccard = shared / (a.size + b.size - shared || 1);
  const prefix = norm(ruleOf(l, c)?.text?.split("{")[0] ?? "");
  return jaccard + (prefix.split(" ").length >= 2 && norm(t).startsWith(prefix) ? 0.5 : 0);
}

const MIN_SIM = 0.4;
const MIN_GAP = 0.15;

/** The one candidate that clearly beats the rest (index), or -1. */
function clearBest(scores: number[]): number {
  const top = Math.max(...scores, -1);
  const rest = scores.filter((_, i) => i !== scores.indexOf(top));
  return top >= MIN_SIM && top - Math.max(...rest, 0) >= MIN_GAP ? scores.indexOf(top) : -1;
}

/**
 * Checks the whole box (on blur). Lines are aligned to the generated sentences: an identical line matches its
 * sentence; a changed line pairs with the sentence it is clearly most alike, both ways round; each pair goes
 * through `checkLine`. A sentence left with no line is a deleted line (only when every typed line found its
 * sentence, otherwise the words may simply have moved into a line we could not pair, so the answer stays). A
 * line that pairs with nothing becomes an extra instruction. Never a guess. `open` says offers are waiting.
 */
export function checkText(box: SummaryBoxState, text: string, c: CheckContext): { box: SummaryBoxState; announce: string; open: boolean } {
  const sentences = c.lines.filter((l) => l.editable);
  let typed = text.split("\n").map((s) => s.trim()).filter(Boolean);
  let spare = [...sentences];
  for (const s of sentences) {
    const i = typed.findIndex((t) => norm(t) === norm(s.text));
    if (i >= 0) { typed = typed.filter((_, k) => k !== i); spare = spare.filter((x) => x !== s); }
  }
  const scores = typed.map((t) => spare.map((s) => similarity(t, s, c)));
  const pairedText: Record<string, string> = {};
  const paired = new Set<number>();
  // A line that starts with one sentence's fixed template start ("Purpose:") is that sentence, however much the
  // slot was rewritten; a start shared by two sentences, or claimed by two lines, pairs nothing.
  const prefixOf = (s: SummaryLine) => norm(ruleOf(s, c)?.text?.split("{")[0] ?? "");
  typed.forEach((t, i) => {
    const hits = spare.filter((s) => prefixOf(s) && norm(t).startsWith(prefixOf(s)));
    if (hits.length !== 1 || spare.some((s) => s !== hits[0] && prefixOf(s) === prefixOf(hits[0]))) return;
    if (typed.filter((u) => norm(u).startsWith(prefixOf(hits[0]))).length !== 1) return;
    pairedText[hits[0].id] = t; paired.add(i);
  });
  typed.forEach((t, i) => {
    if (paired.has(i)) return;
    const j = clearBest(scores[i]);
    if (j >= 0 && spare[j].id in pairedText) return;
    if (j >= 0 && clearBest(typed.map((_, k) => scores[k][j])) === i) { pairedText[spare[j].id] = t; paired.add(i); }
  });
  const loose = typed.filter((_, i) => !paired.has(i));
  const notes: string[] = [];
  let b: SummaryBoxState = box;
  for (const s of sentences) {
    const prior = box.edits[s.id];
    const next = s.id in pairedText ? pairedText[s.id] : spare.includes(s) && !loose.length ? "" : null;
    if (next !== null && prior && prior.check && prior.text === next) continue; // unchanged since its last check
    b = { ...b, edits: omit(b.edits, s.id), offers: b.offers.filter((o) => o.line !== s.id) };
    if (next === null) continue;
    const r = checkLine(editLine(b, s.id, next, c), s.id, c);
    b = r.box;
    if (r.announce) notes.push(r.announce);
  }
  const seen = new Set(box.instructions.map((i) => i.text));
  const added = loose.filter((t) => !seen.has(t));
  if (added.length) {
    b = { ...b, instructions: [...b.instructions, ...added.map((t) => ({ text: t, differs: [] }))] };
    notes.push(KEPT);
  }
  // A line re-checked while other offers stay open must not add its instruction twice.
  const fresh = b.instructions.slice(box.instructions.length).filter((i, k, a) => !seen.has(i.text) && a.findIndex((x) => x.text === i.text) === k);
  b = { ...b, instructions: [...box.instructions, ...fresh] };
  return { box: b, announce: notes.join(" "), open: b.offers.length > 0 };
}

/** The only function that returns an answer patch. The wizard applies it with its own `set`. */
export function acceptOffer(box: SummaryBoxState, offerId: string, c: CheckContext):
  { box: SummaryBoxState; patch: Record<string, FormValue>; announce: string } {
  const o = box.offers.find((x) => x.id === offerId);
  if (!o) return { box, patch: {}, announce: "" };
  const accepted = [...box.accepted];
  for (const [key, v] of Object.entries(o.patch)) {
    const at = accepted.findIndex((a) => a.key === key);
    const entry = { key, label: nameOf(c.form, key), old: at >= 0 ? accepted[at].old : asAnswer(c.values[key]), new: asAnswer(v) };
    if (at >= 0) accepted[at] = entry; else accepted.push(entry);
  }
  let b: SummaryBoxState = { ...box, offers: box.offers.filter((x) => x !== o), accepted };
  let announce = `${o.label} updated to ${o.newText}.`;
  if (!openOffers(b, o.line)) {
    const r = settle(b, o.line, { ...c, values: { ...c.values, ...o.patch } }, o.kind === "clear");
    b = r.box;
    if (r.kept) announce += ` ${KEPT}`;
  }
  return { box: b, patch: o.patch, announce };
}

export function declineOffer(box: SummaryBoxState, offerId: string, c: CheckContext): { box: SummaryBoxState; announce: string } {
  const o = box.offers.find((x) => x.id === offerId);
  if (!o) return { box, announce: "" };
  const E = box.edits[o.line];
  let b: SummaryBoxState = { ...box, offers: box.offers.filter((x) => x !== o) };
  if (E) {
    const ck = E.check ?? NO_CHECK;
    b = { ...b, edits: { ...b.edits, [o.line]: { ...E, check: { ...ck, declined: [...ck.declined, ...o.keys.filter((k) => k !== "currency")] } } } };
  }
  if (openOffers(b, o.line)) return { box: b, announce: "Your answer is kept." };
  const r = settle(b, o.line, c);
  return { box: r.box, announce: r.kept ? "Your answer is kept. Your edit is kept as an extra instruction." : "Your answer is kept." };
}

/** FR-13: an explicit discard of edits and open offers; kept instructions and accepted answers stay. */
export function undoEdits(box: SummaryBoxState): SummaryBoxState {
  return { ...box, edits: {}, offers: [] };
}

/** FR-23, FR-36: run whenever the answers or the lines change, and once after a draft loads. */
export function reconcile(box: SummaryBoxState, c: CheckContext): SummaryBoxState {
  let b = box;
  const withdrawn = new Set<string>();
  for (const o of box.offers) {
    if (!o.keys.some((k) => !applies(c, k)) && oldTextFor(o.keys, c) === o.oldText) continue;
    b = { ...b, offers: b.offers.filter((x) => x !== o) };
    const E = b.edits[o.line];
    if (E) {
      const ck = E.check ?? NO_CHECK;
      b = { ...b, edits: { ...b.edits, [o.line]: { ...E, check: { ...ck, declined: [...ck.declined, ...o.keys.filter((k) => k !== "currency")] } } } };
    }
    withdrawn.add(o.line);
  }
  for (const [id, E] of Object.entries(b.edits)) {
    if (openOffers(b, id)) continue;
    const L = c.lines.find((l) => l.id === id && l.editable);
    if (L && E.check === null && E.base !== L.text) {
      b = { ...b, edits: { ...b.edits, [id]: { ...E, check: { templateMatched: false, differs: L.keys.map((k) => diff(c, k)), declined: [] } } } };
    } else if (L && !withdrawn.has(id)) continue;
    b = settle(b, id, c).box;
  }
  return b;
}

/** FR-34, on Submit: every edit is checked, every open offer is declined, the pending text is kept. Never blocks. */
export function finalize(box: SummaryBoxState, c: CheckContext): SummaryBoxState {
  let b = reconcile(box, c);
  for (const [id, E] of Object.entries(b.edits)) if (E.check === null) b = checkLine(b, id, c).box;
  for (const o of [...b.offers]) b = declineOffer(b, o.id, c).box;
  for (const id of Object.keys(b.edits)) b = settle(b, id, c).box;
  return addPending(b);
}

export function addPending(box: SummaryBoxState): SummaryBoxState {
  const text = box.pending.trim();
  return text ? { ...box, instructions: [...box.instructions, { text, differs: [] }], pending: "" } : box;
}

export function removeInstruction(box: SummaryBoxState, index: number): SummaryBoxState {
  return { ...box, instructions: box.instructions.filter((_, i) => i !== index) };
}

/** Words in the instructions and the pending box (the server's len(text.split())); notes are not counted. */
export function wordCount(box: SummaryBoxState): number {
  const n = (s: string) => s.trim().split(/\s+/).filter(Boolean).length;
  return box.instructions.reduce((t, i) => t + n(i.text), 0) + n(box.pending);
}

/** The note under an instruction: "This differs from your answer: X" / "…answers: X; Y". */
export function differsNote(d: SummaryDiffers[]): string | null {
  if (!d.length) return null;
  return `This differs from your ${d.length === 1 ? "answer" : "answers"}: ${d.map((x) => x.answer).join("; ")}`;
}
