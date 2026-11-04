/**
 * "Your request in words" (spec 013): one plain sentence per answered question, built from the form's answers
 * alone, with no AI, no advice and no network. `agreement_forms.json` stays the only source of questions, labels,
 * options and "only when" rules; this file adds wording only, keyed by question key (the same home as each
 * question's `uses` text in the wizard). `request-summary.test.ts` fails when a question has no sentence, when a
 * rule names a question or option the JSON lacks, or when a choice's variants miss an option.
 *
 * Pure functions. Odd answers never throw (they read as unanswered); a broken sentence RULE does, by design, so a
 * bad rule fails loudly instead of printing a wrong summary.
 */

import type { FormValue, RequestFormDef, RequestFormField, RequestFormRule } from "./types";
import { lacksAnswer, shown, tidiedRows, tidyCell } from "./form-checks";

export interface SummaryContext {
  /** Title of the picked parent contract (the wizard's `parent`), as the form shows it. */
  parentTitle: string | null;
}
export interface SentenceRule {
  /** Composite id, or the question key for a single-question ("solo") rule. */
  id: string;
  /** Default: every form that asks all of `keys`. */
  forms?: string[];
  /** Slot order. */
  keys: string[];
  /** Template; "{key}" = that answer's slot. */
  text?: string;
  /** Single choice question: option → whole sentence (no slots). */
  variants?: Record<string, string>;
  /** Single rows question: noun from field.item ?? ["deliverable","deliverables"]. */
  rows?: { column: string; after?: string };
  /** Extra condition, same shape and meaning as a question's `show`. */
  when?: RequestFormRule[];
  /** key → option → wording in THIS sentence (default: the option's label). */
  phrases?: Record<string, Record<string, string>>;
  /** Shown, never editable (note_approvers). */
  readOnly?: boolean;
  /** Never shown as a gap (advice_only: an unticked box is an answer). */
  noGap?: boolean;
}
export interface SummaryLine {
  id: string;
  kind: "sentence" | "gap";
  /** The sentence, or "Counterparty: not chosen yet". */
  text: string;
  keys: string[];
  /** NAMES of its keys joined with " and ". */
  name: string;
  /** Any of its keys is required (gap styling). */
  required: boolean;
  /** sentence && !readOnly. */
  editable: boolean;
}

// ------------------------------------------------------------- wording ----

const PAPER = { "Our template": "our template", "Their paper": "their paper" };
const TERM = {
  "Fixed end date": "a fixed end date", "Renews automatically": "automatic renewal",
  "No end date (evergreen)": "no end date (evergreen)",
};
const LAW = {
  India: "Indian", "England and Wales": "English", Singapore: "Singapore", "New York": "New York", Delaware: "Delaware",
};
const NDA_KIND = { Mutual: "mutual", "One-way": "one-way" };
const NOT_NDA: RequestFormRule[] = [{ field: "agreement_type", in: ["NDA"], not: true }];
const IS_NDA: RequestFormRule[] = [{ field: "agreement_type", in: ["NDA"] }];
const KIND_PHRASES: Record<string, string> = {
  NDA: "an NDA", "Services (MSA)": "a services agreement (MSA)", "Buying from a vendor": "a purchase from a vendor",
  "Software or SaaS": "a software or SaaS agreement", Consultancy: "a consultancy agreement",
  "Selling to a customer": "a sale to a customer", "Something else": "another kind of agreement",
};
const yn = (yes: string, no: string) => ({ Yes: yes, No: no });

export const SENTENCE_RULES: SentenceRule[] = [
  // ---- composites (placed where their first key sits; fall back to the solos below) ----
  { id: "deal_nda", forms: ["new_agreement"], when: IS_NDA, keys: ["nda_kind", "agreement_type", "entity", "counterparty", "paper"],
    text: "This is a {nda_kind} {agreement_type} between {entity} and {counterparty}, on {paper}.",
    phrases: { nda_kind: NDA_KIND, agreement_type: { NDA: "NDA" }, paper: PAPER } },
  { id: "deal", forms: ["new_agreement"], when: NOT_NDA, keys: ["agreement_type", "entity", "counterparty", "paper"],
    text: "This is {agreement_type} between {entity} and {counterparty}, on {paper}.",
    phrases: { agreement_type: KIND_PHRASES, paper: PAPER } },
  { id: "signer", keys: ["cp_signer_name", "cp_signer_email"], text: "Their signer is {cp_signer_name} ({cp_signer_email})." },
  { id: "nda_term_law", forms: ["new_agreement"], keys: ["nda_term", "governing_law"],
    text: "It lasts {nda_term} and is under {governing_law} law.", phrases: { governing_law: LAW } },
  { id: "contact", forms: ["new_agreement"], keys: ["company_contact_name", "company_contact_role"],
    text: "Our contact is {company_contact_name} ({company_contact_role})." },
  { id: "services_window", forms: ["sow"], keys: ["services_start", "services_end"],
    text: "Services run from {services_start} to {services_end}." },
  { id: "parties_signed", forms: ["regularize"], keys: ["entity", "counterparty"], text: "It is between {entity} and {counterparty}." },

  // ---- shared solos ----
  { id: "needed_by", keys: ["needed_by"], text: "Needed by {needed_by}." },
  { id: "department", keys: ["department"], text: "Requested by {department}." },
  { id: "cp_signer_name", keys: ["cp_signer_name"], text: "Their signer is {cp_signer_name}." },
  { id: "cp_signer_email", keys: ["cp_signer_email"], text: "Their signer's email is {cp_signer_email}." },
  { id: "paper", keys: ["paper"], text: "It is on {paper}.", phrases: { paper: PAPER } },
  { id: "entity", keys: ["entity"], text: "Our side is {entity}." },
  { id: "counterparty", keys: ["counterparty"], text: "The other side is {counterparty}." },
  { id: "start_date", keys: ["start_date"], text: "It starts on {start_date}." },
  { id: "term", keys: ["term"], text: "It has {term}.", phrases: { term: TERM } },
  { id: "end_date", keys: ["end_date"], text: "It ends on {end_date}." },
  { id: "renewal_term", keys: ["renewal_term"], text: "Each renewal lasts {renewal_term}." },
  { id: "notice_days", keys: ["notice_days"], text: "Notice to stop it renewing: {notice_days}." },
  { id: "scope", keys: ["scope"], text: "Scope: {scope}" },
  { id: "personal_data", keys: ["personal_data"],
    variants: yn("Personal data will be shared or processed.", "No personal data will be shared or processed.") },
  { id: "gxp", keys: ["gxp"],
    variants: yn("It is GxP or other regulated quality work.", "It is not GxP or other regulated quality work.") },
  { id: "reason", keys: ["reason"], text: "Why: {reason}" },
  { id: "note_approvers", keys: ["note_approvers"], text: "Note to approvers: {note_approvers}", readOnly: true },

  // ---- New agreement ----
  { id: "agreement_type", forms: ["new_agreement"], keys: ["agreement_type"], text: "This is {agreement_type}.",
    phrases: { agreement_type: KIND_PHRASES } },
  { id: "nda_kind", forms: ["new_agreement"], keys: ["nda_kind"], text: "It is a {nda_kind} NDA.", phrases: { nda_kind: NDA_KIND } },
  { id: "nda_direction", forms: ["new_agreement"], keys: ["nda_direction"], text: "Only {nda_direction} share information.",
    phrases: { nda_direction: { "We share": "we", "They share": "they" } } },
  { id: "nda_term", forms: ["new_agreement"], keys: ["nda_term"], text: "It lasts {nda_term}." },
  { id: "governing_law", forms: ["new_agreement"], keys: ["governing_law"], text: "It is under {governing_law} law.",
    phrases: { governing_law: LAW } },
  { id: "value", forms: ["new_agreement", "regularize"], keys: ["value"], text: "The total value is {value}." },
  { id: "advice_only", forms: ["new_agreement"], keys: ["advice_only"], noGap: true,
    variants: { "There are no separate deliverables (advice only)": "There are no separate deliverables (advice only)." } },
  { id: "consultancy_deliverables", forms: ["new_agreement"], keys: ["consultancy_deliverables"], rows: { column: "name" } },
  { id: "buying", forms: ["new_agreement"], keys: ["buying"], text: "We are buying {buying}.",
    phrases: { buying: { Goods: "goods", Services: "services", "Goods and services": "goods and services" } } },
  { id: "hosting", forms: ["new_agreement"], keys: ["hosting"], text: "Our data will be hosted in {hosting}.",
    phrases: { hosting: { India: "India", "European Union": "the European Union", "United States": "the United States", "Several regions": "several regions" } } },
  { id: "payment_terms", forms: ["new_agreement"], keys: ["payment_terms"], text: "Payment is due within {payment_terms}." },
  { id: "fee_basis", forms: ["new_agreement"], keys: ["fee_basis"], text: "The consultant is paid {fee_basis}.",
    phrases: { fee_basis: { "Fixed fee": "a fixed fee", "Day rate": "a day rate", "Hourly rate": "an hourly rate" } } },
  { id: "fee_rate", forms: ["new_agreement"], keys: ["fee_rate"], text: "The rate is {fee_rate}." },
  { id: "key_individuals", forms: ["new_agreement"], keys: ["key_individuals"], rows: { column: "name" } },
  { id: "company_contact_name", forms: ["new_agreement"], keys: ["company_contact_name"], text: "Our contact is {company_contact_name}." },
  { id: "company_contact_role", forms: ["new_agreement"], keys: ["company_contact_role"], text: "Our contact's role is {company_contact_role}." },
  { id: "nonstandard", forms: ["new_agreement"], keys: ["nonstandard"], text: "Non-standard terms asked for: {nonstandard}" },

  // ---- Statement of Work ----
  { id: "parent_contract_id", forms: ["sow"], keys: ["parent_contract_id"], text: "It is under the master agreement “{parent_contract_id}”." },
  { id: "sow_title", forms: ["sow"], keys: ["sow_title"], text: "The SoW is called “{sow_title}”." },
  { id: "services_start", forms: ["sow"], keys: ["services_start"], text: "Services start on {services_start}." },
  { id: "services_end", forms: ["sow"], keys: ["services_end"], text: "Services end on {services_end}." },
  { id: "value", forms: ["sow"], keys: ["value"], text: "The total value of this SoW is {value}." },
  { id: "pricing", forms: ["sow"], keys: ["pricing"], text: "Pricing is {pricing}.",
    phrases: { pricing: { "Fixed fee": "a fixed fee", "Time and materials": "time and materials", Milestones: "by milestones" } } },
  { id: "deliverable_rows", forms: ["sow"], keys: ["deliverable_rows"], rows: { column: "name" } },

  // ---- Data Processing Agreement ----
  { id: "parent_contract_id", forms: ["dpa"], keys: ["parent_contract_id"], text: "It goes with “{parent_contract_id}”." },
  { id: "our_role", forms: ["dpa"], keys: ["our_role"], text: "We are the {our_role}.",
    phrases: { our_role: {
      "Controller: we decide how the data is used": "controller (we decide how the data is used)",
      "Processor: we act on their instructions": "processor (we act on their instructions)" } } },
  { id: "processing_purpose", forms: ["dpa"], keys: ["processing_purpose"], text: "Why the data is processed: {processing_purpose}" },
  { id: "data_types", forms: ["dpa"], keys: ["data_types"], text: "Personal data involved: {data_types}." },
  { id: "data_subjects", forms: ["dpa"], keys: ["data_subjects"], text: "Whose data: {data_subjects}." },
  { id: "transfer", forms: ["dpa"], keys: ["transfer"], variants: yn("Data will leave India.", "Data will stay in India.") },
  { id: "transfer_countries", forms: ["dpa"], keys: ["transfer_countries"], rows: { column: "country", after: " outside India" } },
  { id: "subprocessors", forms: ["dpa"], keys: ["subprocessors"],
    variants: yn("They will use sub-processors.", "They will not use sub-processors.") },
  { id: "subprocessor_rows", forms: ["dpa"], keys: ["subprocessor_rows"], rows: { column: "name" } },

  // ---- Amendment ----
  { id: "parent_contract_id", forms: ["amendment"], keys: ["parent_contract_id"], text: "It amends “{parent_contract_id}”." },
  { id: "what_changes", forms: ["amendment"], keys: ["what_changes"], text: "What is changing: {what_changes}." },
  { id: "new_value", forms: ["amendment"], keys: ["new_value"], text: "The new total value is {new_value}." },
  { id: "new_end_date", forms: ["amendment"], keys: ["new_end_date"], text: "The new end date is {new_end_date}." },
  { id: "change_effective", forms: ["amendment"], keys: ["change_effective"], text: "The change takes effect on {change_effective}." },

  // ---- Renewal ----
  { id: "parent_contract_id", forms: ["renewal"], keys: ["parent_contract_id"], text: "It renews “{parent_contract_id}”." },
  { id: "renew_terms", forms: ["renewal"], keys: ["renew_terms"], text: "It renews on {renew_terms}.",
    phrases: { renew_terms: { "Same terms": "the same terms", "Changed terms": "changed terms" } } },
  { id: "renew_end", forms: ["renewal"], keys: ["renew_end"], text: "The new end date is {renew_end}." },
  { id: "renew_value", forms: ["renewal"], keys: ["renew_value"], text: "The new total value is {renew_value}." },

  // ---- Termination ----
  { id: "parent_contract_id", forms: ["termination"], keys: ["parent_contract_id"], text: "It ends “{parent_contract_id}”." },
  { id: "grounds", forms: ["termination"], keys: ["grounds"], text: "It is ending {grounds}.",
    phrases: { grounds: {
      "For convenience": "for convenience", "Breach by the other side": "because of a breach by the other side",
      "Mutual agreement": "by mutual agreement", "Not renewing at expiry": "by not renewing at expiry" } } },
  { id: "termination_date", forms: ["termination"], keys: ["termination_date"], text: "The termination date is {termination_date}." },
  { id: "clause_breached", forms: ["termination"], keys: ["clause_breached"], text: "The clause breached is {clause_breached}." },
  { id: "cure_served", forms: ["termination"], keys: ["cure_served"],
    variants: yn("The cure period has run out.", "The cure period has not run out yet.") },

  // ---- Novation ----
  { id: "parent_contract_id", forms: ["novation"], keys: ["parent_contract_id"], text: "It moves “{parent_contract_id}” to a new party." },
  { id: "transferring", forms: ["novation"], keys: ["transferring"],
    variants: { "They are": "They are transferring out.", "We are": "We are transferring out." } },
  { id: "incoming_party", forms: ["novation"], keys: ["incoming_party"], text: "The new party is {incoming_party}." },
  { id: "effective_date", forms: ["novation"], keys: ["effective_date"], text: "It takes effect on {effective_date}." },
  { id: "consent", forms: ["novation"], keys: ["consent"],
    variants: { Yes: "The outgoing party has agreed in writing.", "Not yet": "The outgoing party has not agreed in writing yet." } },

  // ---- Signed outside the system ----
  { id: "date_signed", forms: ["regularize"], keys: ["date_signed"], text: "It was signed on {date_signed}." },
  { id: "why_outside", forms: ["regularize"], keys: ["why_outside"], text: "Why it was signed outside the system: {why_outside}." },
  { id: "prior_approval", forms: ["regularize"], keys: ["prior_approval"], text: "Approved at the time: {prior_approval}." },
  { id: "signed_by_us", forms: ["regularize"], keys: ["signed_by_us"], text: "Signed for us by {signed_by_us}." },

  // ---- Cancel a request ----
  { id: "cancel_request_ref", forms: ["cancellation"], keys: ["cancel_request_ref"], text: "Cancel request {cancel_request_ref}." },
  { id: "cancel_reason", forms: ["cancellation"], keys: ["cancel_reason"], text: "Reason: {cancel_reason}." },
  { id: "tell_cp", forms: ["cancellation"], keys: ["tell_cp"], variants: yn("The counterparty already has a draft.", "The counterparty has no draft yet.") },
];

/** Gap names. Key, or "<form>.<key>" override; anything missing uses the question's label without a trailing "?". */
export const NAMES: Record<string, string> = {
  agreement_type: "Kind of agreement", entity: "Our legal entity", counterparty: "Counterparty",
  cp_signer_name: "Their signer's name", cp_signer_email: "Their signer's email", paper: "Whose paper",
  department: "Requesting department", nda_kind: "Mutual or one-way",
  nda_direction: "Who shares information", nda_term: "How long it lasts", notice_days: "Notice to stop it renewing",
  consultancy_deliverables: "Deliverables", buying: "What we are buying", hosting: "Where our data is hosted",
  fee_basis: "How the consultant is paid", key_individuals: "Key individuals",
  company_contact_name: "Company contact's name", company_contact_role: "Company contact's role",
  nonstandard: "Non-standard terms", personal_data: "Personal data", gxp: "GxP or regulated work",
  "sow.parent_contract_id": "Master agreement", parent_contract_id: "Agreement", "sow.value": "Total value of this SoW",
  processing_purpose: "Why the data is processed", data_types: "Kinds of personal data", data_subjects: "Whose data",
  transfer: "Data leaving India", subprocessors: "Sub-processors used", subprocessor_rows: "Sub-processors",
  what_changes: "What is changing", reason: "Why", renew_terms: "Renew on", grounds: "Why it is ending",
  clause_breached: "Clause breached", cure_served: "Cure period run out", transferring: "Who is transferring out",
  incoming_party: "Who takes over", consent: "Outgoing party's written agreement",
  why_outside: "Why it was signed outside the system", prior_approval: "Approved at the time",
  signed_by_us: "Who signed for us", cancel_request_ref: "Request to cancel", cancel_reason: "Why cancel it",
  tell_cp: "Counterparty has a draft",
};

/** Extra spellings a person may type for an option (label and phrase are always accepted too). */
export const SYNONYMS: Record<string, Record<string, string[]>> = {
  agreement_type: {
    NDA: ["nda", "mnda", "non-disclosure agreement", "non disclosure agreement", "confidentiality agreement"],
    "Services (MSA)": ["msa", "services agreement", "master services agreement"],
    "Buying from a vendor": ["vendor agreement", "purchase", "purchase from a vendor"],
    "Software or SaaS": ["software", "saas", "software agreement", "saas agreement"],
    Consultancy: ["consultancy agreement", "consulting agreement", "consulting"],
    "Selling to a customer": ["customer agreement", "sale to a customer"],
  },
  nda_kind: { Mutual: ["two-way", "two way", "bilateral"], "One-way": ["one way", "unilateral"] },
  nda_direction: {
    "We share": ["we", "us", "our side"], "They share": ["they", "them", "the other side", "the counterparty"],
  },
  paper: {
    "Our template": ["our paper", "our own paper", "our standard paper"],
    "Their paper": ["their template", "their draft", "their own paper"],
  },
  governing_law: {
    India: ["indian"], "England and Wales": ["english", "england", "english and welsh", "uk"],
    Singapore: ["singaporean"], "New York": ["ny"],
  },
  hosting: {
    "European Union": ["eu", "europe"], "United States": ["us", "usa", "america"], "Several regions": ["multiple regions"],
  },
  buying: { "Goods and services": ["both"] },
  pricing: { "Fixed fee": ["fixed price"], "Time and materials": ["t&m"] },
  fee_basis: { "Day rate": ["daily rate"], "Hourly rate": ["hourly"] },
  our_role: {
    "Controller: we decide how the data is used": ["controller"],
    "Processor: we act on their instructions": ["processor"],
  },
  term: {
    "Fixed end date": ["fixed term"], "Renews automatically": ["auto-renewal", "auto renewal"],
    "No end date (evergreen)": ["no end date", "evergreen"],
  },
  renew_terms: { "Changed terms": ["new terms", "different terms"] },
};

// --------------------------------------------------------- slot values ----

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const ISO_DAY = /^(\d{4})-(\d{2})-(\d{2})$/;
const COMPANIONS = new Set(["entity_id", "counterparty_id", "currency"]);
const MONEY_KEYS = new Set(["value", "new_value", "renew_value", "fee_rate"]);
const REGISTER_PICKS = new Set(["entity", "counterparty", "parent_contract_id", "cancel_request_ref", "incoming_party"]);

/** "2026-11-15" → "15 Nov 2026" (fixed English months, so a sentence reads the same everywhere). */
export function writtenDate(iso: string): string {
  const m = ISO_DAY.exec(iso);
  return m ? `${Number(m[3])} ${MONTHS[Number(m[2]) - 1] ?? m[2]} ${m[1]}` : iso;
}

/** Moved from the wizard's money(): "INR 50,00,000", or "" when there is no amount. */
export function moneyText(currency: FormValue | undefined, amount: FormValue | undefined): string {
  const x = Number(String(amount ?? "").replace(/,/g, ""));
  return x ? `${currency || "INR"} ${x.toLocaleString(currency === "INR" || !currency ? "en-IN" : "en-US")}` : "";
}

/** "A", "A and B", "A, B and C". */
const listText = (xs: string[]) => (xs.length < 2 ? xs.join("") : `${xs.slice(0, -1).join(", ")} and ${xs[xs.length - 1]}`);

function rowsText(field: RequestFormField, raw: FormValue | undefined, values: Record<string, FormValue>,
  spec: { column: string; after?: string }): string | null {
  const rows = tidiedRows(field, raw, values);
  if (!rows.length) return null;
  const [one, many] = field.item ?? ["deliverable", "deliverables"];
  const cells = rows.map((r) => r[spec.column] ?? "").filter(Boolean);
  const first = cells.slice(0, 3);
  const items = rows.length > 3 ? `${first.join(", ")} and ${rows.length - 3} more` : listText(first);
  return `${rows.length} ${rows.length === 1 ? one : many}${spec.after ?? ""}: ${items}`;
}

/** One answer as words for a sentence, or null when it is not answered (or not readable). */
function slotText(
  form: RequestFormDef, key: string, values: Record<string, FormValue>, ctx: SummaryContext,
  phrases?: Record<string, string>, rows?: { column: string; after?: string },
): string | null {
  const field = form.fields.find((f) => f.key === key);
  const v = values[key];
  if (!field || lacksAnswer(field, v)) return null;
  const currency = typeof values.currency === "string" && values.currency.trim() ? values.currency : "INR";
  const str = typeof v === "string" ? v : null;
  switch (field.kind) {
    case "select": {
      const t = str === null ? "" : tidyCell(str);
      return t ? phrases?.[t] ?? t : null;
    }
    case "multiselect": {
      const picked: unknown[] = Array.isArray(v) ? v : [v];
      const opts = (field.options ?? []).filter((o) => picked.includes(o));
      return opts.length ? listText(opts.map((o) => phrases?.[o] ?? o)) : null;
    }
    case "date":
      return str !== null && ISO_DAY.test(str.trim()) ? writtenDate(str.trim()) : null;
    case "number": {
      if (str === null) return null;
      if (MONEY_KEYS.has(key)) return moneyText(currency, str) || null;
      const n = Number(str.replace(/,/g, ""));
      return Number.isFinite(n) && str.trim() ? n.toLocaleString("en-IN") : null;
    }
    case "rows":
      return rows ? rowsText(field, v, values, rows) : null;
    default: {
      if (str === null) return null;
      // One line per sentence: a pasted paragraph keeps every word, with its line breaks read as spaces.
      const t = str.trim().replace(/\s*[\r\n]+\s*/g, " ");
      if (!t) return null;
      return key === "parent_contract_id" ? ctx.parentTitle ?? "the chosen agreement" : t;
    }
  }
}

// ------------------------------------------------------------- building ----

const applies = (r: SentenceRule, form: RequestFormDef) =>
  r.forms ? r.forms.includes(form.key) : r.keys.every((k) => form.fields.some((f) => f.key === k));

/** The solo rule of a question in a form: one that names the form beats the shared one. */
function soloRule(formKey: string, key: string): SentenceRule | undefined {
  const solos = SENTENCE_RULES.filter((r) => r.keys.length === 1 && r.keys[0] === key
    && (r.forms ? r.forms.includes(formKey) : true));
  return solos.find((r) => r.forms) ?? solos[0];
}

export const nameOf = (form: RequestFormDef, key: string): string =>
  NAMES[`${form.key}.${key}`] ?? NAMES[key] ?? (form.fields.find((f) => f.key === key)?.label ?? key).replace(/\?$/, "");

const fill = (text: string, slots: Record<string, string>) => text.replace(/\{(\w+)\}/g, (_m, k: string) => slots[k] ?? "");

/** Test hooks: the rule resolution the builder uses. */
export const _rules = { soloRule, applies };

function build(form: RequestFormDef, def: { steps: { fields: string[] }[] }, values: Record<string, FormValue>,
  ctx: SummaryContext): SummaryLine[] {
  const field = (k: string) => form.fields.find((f) => f.key === k);
  const askedNow = (k: string) => { const f = field(k); return !!f && shown(f.show, values); };
  const walk = [...new Set([...def.steps.flatMap((s) => s.fields), "note_approvers"])]
    .filter((k) => !COMPANIONS.has(k) && field(k));
  const placed = new Set<string>();
  const out: SummaryLine[] = [];
  const line = (id: string, kind: "sentence" | "gap", text: string, keys: string[], rule?: SentenceRule): SummaryLine => ({
    id, kind, text, keys, name: keys.map((k) => nameOf(form, k)).join(" and "),
    required: keys.some((k) => !!field(k)?.required), editable: kind === "sentence" && !rule?.readOnly,
  });

  for (const key of walk) {
    if (placed.has(key) || !askedNow(key)) continue;

    for (const r of SENTENCE_RULES) {
      if (r.keys.length < 2 || !r.keys.includes(key) || !applies(r, form) || !shown(r.when, values)) continue;
      if (!r.keys.every((k) => askedNow(k) && !placed.has(k))) continue;
      const slots: Record<string, string> = {};
      for (const k of r.keys) {
        const s = slotText(form, k, values, ctx, r.phrases?.[k]);
        if (s === null) break;
        slots[k] = s;
      }
      if (Object.keys(slots).length !== r.keys.length || !r.text) continue;
      r.keys.forEach((k) => placed.add(k));
      out.push(line(r.id, "sentence", fill(r.text, slots), r.keys, r));
      break;
    }
    if (placed.has(key)) continue;

    placed.add(key);
    const r = soloRule(form.key, key);
    let text: string | null = null;
    if (r?.variants) {
      const v = values[key];
      const picked = (Array.isArray(v) ? v : [v]).find((x) => typeof x === "string" && x in r.variants!);
      if (typeof picked === "string" && !lacksAnswer(field(key)!, v)) text = r.variants[picked];
    } else if (r?.text) {
      const s = slotText(form, key, values, ctx, r.phrases?.[key]);
      text = s === null ? null : fill(r.text, { [key]: s });
    } else if (r?.rows) {
      const s = slotText(form, key, values, ctx, undefined, r.rows);
      text = s === null ? null : `${s}.`;
    }
    if (text !== null) out.push(line(r!.id, "sentence", text, [key], r));
    else if (r && !r.noGap) {
      const f = field(key)!;
      const pick = f.kind === "select" || f.kind === "multiselect" || REGISTER_PICKS.has(key);
      out.push(line(key, "gap", `${nameOf(form, key)}: ${pick ? "not chosen yet" : "not given yet"}`, [key], r));
    }
  }
  return out;
}

/** The request in words, in the order the form asks. Odd answers never throw (they read as unanswered). */
export function buildSummary(form: RequestFormDef, def: { steps: { fields: string[] }[] },
  values: Record<string, FormValue>, ctx: SummaryContext): SummaryLine[] {
  // No catch here on purpose: a throw means a bug in a RULE, and the wizard's try/catch is what
  // shows "The summary could not be built". Swallowing it would show an empty list instead.
  return build(form, def, values ?? {}, ctx);
}

/** The sentences only, one per line (what is filed). Gaps are hints and never filed. */
export const summaryText = (lines: SummaryLine[]): string =>
  lines.filter((l) => l.kind === "sentence").map((l) => l.text).join("\n");

/** X in "This differs from your answer: X": the answer as words, or "no answer" when blank or not asked now. */
export function answerText(form: RequestFormDef, key: string, values: Record<string, FormValue>,
  ctx: SummaryContext): string {
  try {
    const f = form.fields.find((x) => x.key === key);
    if (!f || !shown(f.show, values)) return "no answer";
    return slotText(form, key, values, ctx, undefined, soloRule(form.key, key)?.rows) ?? "no answer";
  } catch {
    return "no answer";
  }
}
