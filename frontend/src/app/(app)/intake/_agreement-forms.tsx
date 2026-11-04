"use client";

/**
 * The request forms. Nine forms, each a few short steps that ask only what that
 * request needs; questions appear only once they apply.
 *
 * The questions themselves (labels, options, required, show-when rules) come
 * from the server's agreement_forms.json via /intake/forms, so the browser and
 * filing validation can never disagree. This file only lays them out: which
 * step each sits on, what control it is, and what each answer drives.
 */

import { type ReactNode, useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronDown, ChevronLeft, FileText, Info, Paperclip, Search, TriangleAlert, X, Wallet, CalendarRange, CreditCard, Repeat, ScrollText, Scale, Users, Building2, Paperclip as Clip, ShieldCheck, ListChecks, Lightbulb, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { Button, Card, CardBody, Input, LoadError } from "@/components/ui";
import { contractsApi, intakeApi, partiesApi } from "@/lib/endpoints";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/toast";
import { cn, fmtDate } from "@/lib/utils";
import { ATTACHMENT_ACCEPT, ATTACHMENT_LIMITS_TEXT, attachmentProblem, toAttachment } from "@/lib/intake";
import { approvalsText } from "@/lib/request-facts";
import { buildSummary, moneyText, summaryText, type SummaryLine } from "@/lib/request-summary";
import {
  acceptOffer, addPending, checkText, declineOffer, EMPTY_BOX, finalize, reconcile, removeInstruction, undoEdits,
  WORD_LIMIT, wordCount, type CheckContext,
} from "@/lib/summary-edits";
import {
  answerProblem, EMAIL_PROBLEM, looksLikeEmail, optionsFor, PROCESSOR_ROLE, rowsProblems, rulesFor, shown, shownColumns, tidiedRows, tidyCell,
} from "@/lib/form-checks";
import type {
  Counterparty, FormRow, FormValue, IntakeDraft, LegalEntity, RequestFormDef, RequestFormField, RequestFormRule, SummaryBoxState,
} from "@/lib/types";
import { EarlierAnswer, RowsTable, useRequestForms } from "./_deliverables";
import { ExtraInstructions, LiveSummary, NO_ATTACH, SUMMARY_BOX_ID, SummaryEditor, textWithEdits } from "./_summary-box";

// Both are used by other screens (the workflow builder, the tests) through this file.
export { shown, useRequestForms };

// ---------------------------------------------------------------- layout ----

export type FormGroup = "New paper" | "Change an agreement" | "Records";

export interface AgreementFormDef {
  key: string;
  /** Must equal the server form's name (a test checks). */
  name: string;
  group: FormGroup;
  desc: string;
  steps: { title: string; fields: string[] }[];
}

const REVIEW = "Review and submit";
const WHO = ["agreement_type", "entity", "counterparty", "cp_signer_name", "cp_signer_email", "paper", "their_draft",
  "needed_by", "department"];
const TERM = ["start_date", "term", "end_date", "renewal_term", "notice_days"];

export const AGREEMENT_FORMS: AgreementFormDef[] = [
  { key: "new_agreement", name: "New agreement", group: "New paper",
    desc: "NDA, services, buying, software, consultancy or selling. One form; the questions adapt to the kind of agreement.",
    steps: [
      { title: "Agreement and parties", fields: WHO },
      { title: "Commercial terms", fields: ["nda_kind", "nda_direction", "nda_term", "governing_law", "value", ...TERM,
        "scope", "advice_only", "consultancy_deliverables", "buying", "hosting", "payment_terms", "fee_basis", "fee_rate",
        "key_individuals", "company_contact_name", "company_contact_role", "nonstandard"] },
      { title: "Risk checks and files", fields: ["personal_data", "gxp", "attachments"] },
    ] },
  { key: "sow", name: "Statement of Work", group: "New paper", desc: "New scope and fees under a signed master agreement.",
    steps: [
      { title: "The master agreement", fields: ["parent_contract_id", "cp_signer_name", "cp_signer_email", "needed_by", "department"] },
      { title: "Scope and fees", fields: ["sow_title", "services_start", "services_end", "value", "pricing", "scope", "deliverable_rows"] },
      { title: "Risk checks and files", fields: ["personal_data", "gxp", "attachments"] },
    ] },
  { key: "dpa", name: "Data Processing Agreement", group: "New paper", desc: "Personal data moves between us and a partner.",
    steps: [
      { title: "The agreement", fields: ["parent_contract_id", "cp_signer_name", "cp_signer_email", "paper", "their_draft", "needed_by"] },
      { title: "The data", fields: ["our_role", "processing_purpose", "data_types", "data_subjects", "transfer", "transfer_countries",
        "subprocessors", "subprocessor_rows"] },
      { title: "Files", fields: ["attachments"] },
    ] },
  { key: "amendment", name: "Amendment", group: "Change an agreement", desc: "Change the value, dates, scope or parties.",
    steps: [
      { title: "The agreement", fields: ["parent_contract_id", "needed_by"] },
      { title: "What changes", fields: ["what_changes", "new_value", "new_end_date", "change_effective", "reason"] },
      { title: "Files", fields: ["attachments"] },
    ] },
  { key: "renewal", name: "Renewal", group: "Change an agreement", desc: "Extend an agreement that is ending.",
    steps: [
      { title: "The agreement", fields: ["parent_contract_id", "needed_by"] },
      { title: "The new term", fields: ["renew_terms", "renew_end", "renew_value", "reason"] },
      { title: "Files", fields: ["attachments"] },
    ] },
  { key: "termination", name: "Termination", group: "Change an agreement", desc: "End an agreement early or at expiry.",
    steps: [
      { title: "The agreement", fields: ["parent_contract_id", "needed_by"] },
      { title: "Ending it", fields: ["grounds", "termination_date", "clause_breached", "cure_served", "reason"] },
      { title: "Files", fields: ["attachments"] },
    ] },
  { key: "novation", name: "Novation", group: "Change an agreement", desc: "Move an agreement to a different company.",
    steps: [
      { title: "The agreement", fields: ["parent_contract_id", "needed_by"] },
      { title: "The transfer", fields: ["transferring", "incoming_party", "effective_date", "consent", "reason"] },
      { title: "Files", fields: ["attachments"] },
    ] },
  { key: "regularize", name: "Signed outside the system", group: "Records", desc: "Bring an already-signed agreement onto the register.",
    steps: [
      { title: "The signed document", fields: ["executed", "entity", "counterparty", "date_signed"] },
      { title: "Its terms", fields: ["value", ...TERM] },
      { title: "How it happened", fields: ["why_outside", "prior_approval", "signed_by_us"] },
    ] },
  { key: "cancellation", name: "Cancel a request", group: "Records", desc: "Withdraw a request that is still in progress.",
    steps: [
      { title: "The request", fields: ["cancel_request_ref"] },
      { title: "Why", fields: ["cancel_reason", "tell_cp"] },
    ] },
];

type UiKind = "entity" | "counterparty" | "party" | "contract" | "request" | "money" | "email" | "file";

/** How a question is shown and what its answer drives. Files live only here. */
const UI: Record<string, { kind?: UiKind; label?: string; req?: boolean; show?: RequestFormRule[];
  help?: string; placeholder?: string; uses: string }> = {
  agreement_type: { uses: "Picks the template and the workflow (its Used for), and decides the questions on the next step." },
  entity: { kind: "entity", uses: "Our party on the draft; decides who may sign for us." },
  counterparty: { kind: "counterparty", help: "Picked from the counterparty register.", uses: "Their party on the draft and on the contract record." },
  cp_signer_name: { placeholder: "e.g. Maya Chen", uses: "The counterparty signer on the signature envelope." },
  cp_signer_email: { kind: "email", placeholder: "name@company.com", uses: "Allowed as a signer when the contract goes out for signature." },
  paper: { uses: "Our template: Aegis drafts it. Their paper: their draft becomes the contract and the AI review runs on it." },
  their_draft: { kind: "file", label: "Their draft", req: true, show: [{ field: "paper", in: ["Their paper"] }],
    help: "An editable Word file where you can, not a scan.", uses: "Becomes version 1 of the contract." },
  needed_by: { help: "Leave blank if there is no real deadline.", uses: "Suggests the priority: within 7 days is High, otherwise Medium." },
  department: { uses: "Available to workflow step conditions and reporting." },
  nda_kind: { uses: "Mutual or one-way wording in the NDA template." },
  nda_direction: { uses: "Which party is disclosing and which is receiving." },
  nda_term: { uses: "The NDA's term clause." },
  governing_law: { uses: "The governing-law clause." },
  value: { kind: "money", uses: "Value and currency on the contract; Finance approval runs at 10,000 or more." },
  start_date: { uses: "Effective date on the draft and the contract." },
  term: { uses: "Expiry and renewal on the contract." },
  end_date: { uses: "Expiry date; the renewal reminder counts back from it." },
  renewal_term: { uses: "Renewal wording in the draft." },
  notice_days: { uses: "Notice wording in the draft; the renewal reminder uses it." },
  scope: { placeholder: "What they will do, where, and for whom.", uses: "Services clause in the draft." },
  buying: { uses: "Which vendor clauses apply." },
  hosting: { uses: "Outside India flags cross-border transfer for the Privacy review." },
  payment_terms: { uses: "The payment clause in the draft." },
  advice_only: { uses: "Tick only when the consultant hands over no separate deliverables; the schedule then says its output is its reports and advice." },
  consultancy_deliverables: { uses: "Deliverables and timetable in the Engagement Schedule, D1, D2, … in this order." },
  fee_basis: { uses: "The Fees entry of the Engagement Schedule. The total value is the fixed fee, or the most the rate can add up to." },
  fee_rate: { placeholder: "e.g. 25000", uses: "The day or hourly rate in the Fees entry, in the currency of the total value." },
  key_individuals: { uses: "Named in the Engagement Schedule; the consultant may not replace them without our written consent." },
  company_contact_name: { placeholder: "e.g. Ravi Shah", uses: "Company contact in the Engagement Schedule." },
  company_contact_role: { placeholder: "e.g. Head of Quality", uses: "Company contact in the Engagement Schedule." },
  nonstandard: { placeholder: "Discounts, SLAs, liability, anything off our standard.", uses: "What Legal review focuses on." },
  personal_data: { uses: "Yes adds the Privacy review step." },
  gxp: { uses: "Yes adds the Quality review step." },
  attachments: { kind: "file", label: "Supporting documents",
    help: "Quotes, budget approval, anything a reviewer would otherwise ask for.", uses: "Filed with the request; the Aegis read flags where they contradict your answers." },
  parent_contract_id: { kind: "contract", uses: "Parties, value and dates are read from it." },
  sow_title: { placeholder: "e.g. Data platform migration, phase 2", uses: "Title of the Statement of Work." },
  services_start: { uses: "Effective date of the SoW." },
  services_end: { uses: "Expiry of the SoW." },
  pricing: { uses: "Fees clause in the SoW." },
  deliverable_rows: { uses: "The Deliverables, Key personnel, Acceptance and Milestone payments clauses of the Statement of Work, one entry per row in this order (D1, D2, …)." },
  our_role: { uses: "Controller: drafted from our DPA template. Processor: no draft is made from it; attach the other side's DPA (or the paper to use) before the review." },
  processing_purpose: { uses: "Nature and purpose of the Processing in Annex 1 of the DPA." },
  data_types: { uses: "Types of personal data and the special-category entry in Annex 1 of the DPA. Health or special category data flags a DPIA before signature." },
  data_subjects: { uses: "Categories of Data Subjects in Annex 1 of the DPA." },
  transfer: { uses: "Yes asks which countries; Annex 1 of the DPA lists them, and flags it for the Privacy review." },
  transfer_countries: { uses: "Processing locations in Annex 1 of the DPA, after India, in this order." },
  subprocessors: { uses: "Yes asks for each one; Annex 3 of the DPA lists them." },
  subprocessor_rows: { uses: "Annex 3 of the DPA, one entry per row. Any other sub-processor needs our prior written authorisation." },
  what_changes: { uses: "Decides which questions follow." },
  new_value: { kind: "money", uses: "Written onto the contract when the workflow finishes." },
  new_end_date: { uses: "New end date on the contract when the workflow finishes." },
  change_effective: { uses: "Effective date of the amendment." },
  reason: { uses: "Approvers see it; the Aegis read checks it against the documents." },
  renew_terms: { uses: "Same terms is a straight extension." },
  renew_end: { uses: "New end date on the contract when the workflow finishes." },
  renew_value: { kind: "money", uses: "New value on the contract when the workflow finishes." },
  grounds: { uses: "Which notice rules apply and who approves." },
  termination_date: { help: "Check it against the notice period in the agreement.", uses: "The contract's end date when the workflow finishes." },
  clause_breached: { placeholder: "e.g. Clause 9.2 service levels", uses: "Cited in the termination notice." },
  cure_served: { uses: "No holds the notice until it has." },
  transferring: { uses: "Who signs the deed as the outgoing party." },
  incoming_party: { kind: "party", uses: "Becomes the counterparty on the contract when the workflow finishes." },
  effective_date: { uses: "Effective date of the novation." },
  consent: { uses: "Legal will not draft the deed without it." },
  executed: { kind: "file", label: "The signed agreement", req: true, uses: "Becomes the contract record; obligations are read from it." },
  date_signed: { uses: "Signature date on the record." },
  why_outside: { uses: "Logged as a policy exception." },
  prior_approval: { uses: "No runs the approvals retrospectively." },
  signed_by_us: { placeholder: "Name and role", uses: "Checked against signing authority." },
  cancel_request_ref: { kind: "request", uses: "That request is withdrawn; nothing is deleted." },
  cancel_reason: { uses: "Recorded on the request." },
  tell_cp: { uses: "Yes: Legal tells them it is withdrawn." },
};

const FILE_KEYS = Object.keys(UI).filter((k) => UI[k].kind === "file");
/** Server keys a lookup or the money control fills in beside the one shown. */
const COMPANION: Record<string, string[]> = {
  entity: ["entity_id"], counterparty: ["counterparty_id"], value: ["currency"], new_value: ["currency"],
  renew_value: ["currency"],
};

type Value = FormValue;
type Values = Record<string, Value>;

/** The rows a table question holds, or the one empty row a form starts with. */
const rowsOf = (v: Value | undefined): FormRow[] =>
  Array.isArray(v) && v.length > 0 && typeof v[0] === "object" ? (v as FormRow[]) : [{}];

/** The "What kind of agreement?" choices a form offers — what "Used for" can narrow to. */

/** What a question that is required and empty says. */
const NEEDED = "Needed before you continue.";

/** The earliest day a date question can take: the answer to the date it must not come before, if given. */
function notBeforeValue(field: RequestFormDef["fields"][number] | undefined, values: Values): string | undefined {
  const earlier = field?.not_before ? values[field.not_before] : undefined;
  return typeof earlier === "string" && earlier ? earlier : undefined;
}

/** The answers that count: those to questions that apply right now. Hidden ones stay in the form (a person who
 *  changes their mind gets them back) but they are not filed and the table windows do not read them, so a date
 *  typed under "Fixed end date" is not an end date once the term is "No end date (evergreen)". */
function liveAnswers(fields: RequestFormField[], values: Values): Values {
  const hidden = new Set(fields.filter((f) => !shown(f.show, values)).map((f) => f.key));
  return Object.fromEntries(Object.entries(values).filter(([k]) => !hidden.has(k)));
}

const blank = (v: Value | undefined) =>
  Array.isArray(v)
    ? v.every((x) => (typeof x === "string" ? !x.trim() : Object.values(x).every((c) => tidyCell(c) === "")))
    : !String(v ?? "").trim();

const DRAFT_OF: Record<string, string> = {
  NDA: "NDA template", "Services (MSA)": "MSA template", Consultancy: "Consultancy Agreement template",
  "Buying from a vendor": "Vendor template", "Software or SaaS": "Vendor template",
  "Selling to a customer": "Legal drafts it (no customer template yet)", "Something else": "Legal drafts it",
};
const FINISH: Record<string, string> = {
  amendment: "The new value and end date are written onto the contract",
  renewal: "The new end date (and value) are written onto the contract",
  termination: "The termination date becomes the contract's end date",
  novation: "The new party replaces the counterparty on the contract",
};

function draftFor(key: string, v: Values): string {
  if (v.paper === "Their paper") return "Their draft becomes the contract; the AI review runs on it";
  // Our DPA template is written for the Controller; as Processor the other side's paper is the one to start from.
  if (key === "dpa" && v.our_role === PROCESSOR_ROLE) return "No draft from our template: attach the other side's DPA";
  if (key === "new_agreement") return DRAFT_OF[String(v.agreement_type)] ?? "Chosen by the kind of agreement";
  return ({ sow: "Statement of Work template", dpa: "DPA template", regularize: "The signed document is the record",
    cancellation: "Nothing is drafted" } as Record<string, string>)[key] ?? "Legal drafts the letter or deed";
}

const PRIORITIES = ["Low", "Medium", "High"] as const;
type Priority = (typeof PRIORITIES)[number];

/** Suggested from the needed-by date; the requester's own pick (values.priority) wins. Critical is only set by escalating. */
function suggestedPriority(v: Values): "High" | "Medium" {
  const due = typeof v.needed_by === "string" && v.needed_by ? Date.parse(v.needed_by) : NaN;
  return Number.isFinite(due) && (due - Date.now()) / 86_400_000 <= 7 ? "High" : "Medium";
}

function priorityOf(v: Values): Priority {
  return (PRIORITIES as readonly unknown[]).includes(v.priority) ? (v.priority as Priority) : suggestedPriority(v);
}

/** Where to put a file when the extra instructions are too long (DEC-2: two forms have no file question). */
function attachHintFor(def: AgreementFormDef): string | null {
  const s = def.steps.find((x) => x.fields.includes("attachments"));
  return s ? `attach them as a file under Supporting documents on the “${s.title}” step`
    : null;
}

// ------------------------------------------------------------ controls ----

const CONTROL = "h-10 w-full rounded-lg border bg-slate-100 px-3 text-[13px] text-slate-900 placeholder:text-slate-400 focus:border-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-500/35";

function FieldLabel({ label, req }: { label: string; req?: boolean }) {
  return (
    <span className="block text-[12.5px] font-medium text-slate-800">
      {label}{req && <span className="ml-0.5 text-danger">*</span>}
    </span>
  );
}

type RegisterRecord = LegalEntity | Counterparty;

/**
 * Search the party register as you type and pick a real record. Picking stores
 * both the name (for display) and the record id (what the server checks); typing
 * after a pick clears the id, so a hand-edited name is never filed as a record.
 * Counterparties that don't exist yet can be created in place; legal entities
 * are maintained by admins under Operations → Entities & counterparties.
 */
function RegisterLookup({ kind, name, recordId, onPick, bad, label }: {
  kind: "entity" | "counterparty";
  name: string;
  recordId: string;
  onPick: (record: RegisterRecord | null, typed?: string) => void;
  bad?: boolean;
  label: string;
}) {
  const { notify } = useToast();
  const qc = useQueryClient();
  const [text, setText] = useState(name);
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState(name);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ jurisdiction: "", contact_email: "" });
  const [busy, setBusy] = useState(false);
  useEffect(() => { const t = setTimeout(() => setQ(text.trim()), 200); return () => clearTimeout(t); }, [text]);
  const { data: hits, isFetching } = useQuery({
    queryKey: ["party-lookup", kind, q],
    queryFn: () => (kind === "entity" ? partiesApi.entities(q) : partiesApi.counterparties(q)) as Promise<RegisterRecord[]>,
    enabled: open,
  });
  const exact = (hits ?? []).find((h) => h.name.toLowerCase() === text.trim().toLowerCase());

  function pick(r: RegisterRecord) {
    setText(r.name); setOpen(false); setCreating(false); onPick(r);
  }
  // A new counterparty must say where it is and how to reach it (round 2, bug 46): the server refuses one without.
  const emailGiven = form.contact_email.trim() !== "";
  const detailsMissing = !form.jurisdiction.trim() || !looksLikeEmail(form.contact_email);
  async function create() {
    setBusy(true);
    try {
      const made = await partiesApi.createCounterparty({
        name: text.trim(), jurisdiction: form.jurisdiction.trim(), contact_email: form.contact_email.trim(),
      });
      qc.invalidateQueries({ queryKey: ["party-lookup"] });
      notify(`Added ${made.name} to the counterparty register`, "success");
      pick(made);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Couldn't create the counterparty", "error");
    } finally { setBusy(false); }
  }

  return (
    <div className="relative">
      <div className={cn(
        "flex h-10 overflow-hidden rounded-lg border bg-slate-100 focus-within:border-brand-600 focus-within:ring-2 focus-within:ring-brand-500/35",
        bad ? "border-danger bg-danger-subtle" : "border-slate-300",
      )}>
        <input
          aria-label={label}
          value={text}
          onFocus={() => setOpen(true)}
          onBlur={() => setTimeout(() => setOpen(false), 150)}
          onChange={(e) => { setText(e.target.value); setOpen(true); if (recordId) onPick(null, e.target.value); }}
          placeholder={kind === "entity" ? "Search our legal entities" : "Search the counterparty register"}
          className="min-w-0 flex-1 bg-transparent px-3 text-[13px] text-slate-900 placeholder:text-slate-400 focus:outline-none"
        />
        <span className={cn("flex shrink-0 items-center gap-1.5 border-l border-slate-300 px-3 text-[12px] font-medium",
          recordId ? "bg-success-subtle text-success" : "bg-slate-50 text-slate-600")}>
          {recordId ? <><Check className="h-3.5 w-3.5" />In register</> : <>Look-up <Search className="h-3.5 w-3.5" /></>}
        </span>
      </div>
      {open && (
        <div className="absolute z-20 mt-1 w-full overflow-hidden rounded-lg border border-slate-200 bg-slate-50 shadow-pop">
          {(hits ?? []).length === 0 && !isFetching && (
            <div className="px-3 py-2.5 text-[12.5px] text-slate-500">
              {kind === "entity"
                ? (text.trim() ? `No legal entity matches "${text.trim()}". An admin adds entities under Operations → Entities & counterparties.` : "No legal entities set up yet. An admin adds them under Operations → Entities & counterparties.")
                : text.trim() ? `No counterparty matches "${text.trim()}".` : "Type a name to search."}
            </div>
          )}
          <ul className="max-h-60 overflow-auto">
            {(hits ?? []).map((h) => (
              <li key={h.id}>
                <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => pick(h)}
                  className="flex w-full items-baseline justify-between gap-3 px-3 py-2 text-left text-[13px] hover:bg-brand-50">
                  <span className="font-medium text-slate-900">{h.name}</span>
                  <span className="shrink-0 text-[11.5px] text-slate-500">{h.jurisdiction ?? ""}</span>
                </button>
              </li>
            ))}
          </ul>
          {kind === "counterparty" && text.trim() && !exact && (
            <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => { setCreating(true); setOpen(false); }}
              className="w-full border-t border-slate-200 px-3 py-2 text-left text-[12.5px] font-medium text-brand-700 hover:bg-brand-50">
              + Create &ldquo;{text.trim()}&rdquo; as a new counterparty
            </button>
          )}
        </div>
      )}
      {creating && (
        <div className="mt-2 space-y-2 rounded-lg border border-brand-200 bg-brand-50 p-3">
          <div className="text-[12.5px] font-medium text-slate-800">New counterparty: {text.trim() || "—"}</div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <Input aria-label="Jurisdiction" placeholder="Jurisdiction, e.g. India (required)" value={form.jurisdiction}
              onChange={(e) => setForm((f) => ({ ...f, jurisdiction: e.target.value }))} />
            <Input aria-label="Contact email" type="email" placeholder="Contact email (required)" value={form.contact_email}
              onChange={(e) => setForm((f) => ({ ...f, contact_email: e.target.value }))} />
          </div>
          <p className={cn("text-[11.5px]", emailGiven && !looksLikeEmail(form.contact_email) ? "text-danger" : "text-slate-500")}>
            {emailGiven && !looksLikeEmail(form.contact_email)
              ? EMAIL_PROBLEM
              : "A new counterparty needs a jurisdiction and a contact email, so the register is complete from the start."}
          </p>
          <div className="flex gap-2">
            <Button size="sm" loading={busy} disabled={!text.trim() || detailsMissing} onClick={create}>Create counterparty</Button>
            <Button size="sm" variant="ghost" onClick={() => setCreating(false)}>Cancel</Button>
          </div>
        </div>
      )}
    </div>
  );
}

// --------------------------------------------------------------- wizard ----

// Questions that need the full width: files, and the ones that list contracts or requests.
const WIDE_QUESTION = new Set(["file", "contract", "request"]);


// The step's questions grouped into themed cards. A question the form does not ask (or that does not apply to this answer set)
// simply is not in its card; anything not listed here falls into "Other details", so no question can go missing.
const SECTIONS: { title: string; hint: string; Icon: LucideIcon; tone: string; keys: string[] }[] = [
  { title: "Parties and paper", hint: "Who the agreement is between", Icon: Building2, tone: "ind", keys: ["agreement_type", "entity", "counterparty", "cp_signer_name", "cp_signer_email", "paper", "their_draft", "parent_contract_id", "agreement_id", "contract_id"] },
  { title: "Legal basis", hint: "How the agreement works legally", Icon: Scale, tone: "vio", keys: ["nda_kind", "nda_direction", "governing_law"] },
  { title: "Agreement value", hint: "What it is worth and how it is priced", Icon: Wallet, tone: "sky", keys: ["value", "currency", "fee_basis", "fee_rate"] },
  { title: "Contract term", hint: "When it starts and how long it lasts", Icon: CalendarRange, tone: "emr", keys: ["nda_term", "start_date", "term", "end_date"] },
  { title: "Payment terms", hint: "How and when we pay", Icon: CreditCard, tone: "amb", keys: ["payment_terms"] },
  { title: "Services and deliverables", hint: "What is being bought or delivered", Icon: ListChecks, tone: "ind", keys: ["scope", "advice_only", "consultancy_deliverables", "buying", "hosting", "key_individuals"] },
  { title: "Renewal and termination", hint: "Whether it renews and how to stop it", Icon: Repeat, tone: "vio", keys: ["renewal_term", "notice_days"] },
  { title: "Contacts", hint: "Who to talk to on our side", Icon: Users, tone: "sky", keys: ["company_contact_name", "company_contact_role"] },
  { title: "Special terms", hint: "Anything beyond our standard paper", Icon: ScrollText, tone: "amb", keys: ["nonstandard"] },
  { title: "Risk checks", hint: "Data and regulatory questions", Icon: ShieldCheck, tone: "red", keys: ["personal_data", "gxp"] },
  { title: "Supporting files", hint: "Documents that back the request", Icon: Clip, tone: "sky", keys: ["attachments"] },
  { title: "Dates and ownership", hint: "Timing and who asks", Icon: CalendarRange, tone: "emr", keys: ["needed_by", "department"] },
];

/** The next step, in one sentence a requester can act on. */
const NEXT_STEP_TEXT: Record<string, string> = {
  "Commercial terms": "Value, dates, payment and renewal.",
  "Risk checks and files": "Personal data and regulatory questions, and any files that support the request.",
  "Review and submit": "Read your request back, name it if you like, and submit it for routing.",
};

const TIPS = [
  "Say what is delivered and when, so payment can follow milestones.",
  "Check the currency and the total value against the quote.",
  "Read the renewal wording: does it renew by itself, and for how long?",
  "Give the notice needed to stop it; a missed date can lock in another term.",
];


/** "1 year, 2 months" between two ISO dates, or null when either is missing or the order is wrong (that has its own error). */
function durationText(a: unknown, b: unknown): string | null {
  const s = typeof a === "string" ? new Date(a) : null, e = typeof b === "string" ? new Date(b) : null;
  if (!s || !e || Number.isNaN(s.getTime()) || Number.isNaN(e.getTime()) || e < s) return null;
  let months = (e.getFullYear() - s.getFullYear()) * 12 + (e.getMonth() - s.getMonth());
  if (e.getDate() < s.getDate()) months -= 1;
  const days = Math.round((e.getTime() - s.getTime()) / 86_400_000);
  if (months < 1) return `${days + 1} day${days === 0 ? "" : "s"}`;
  const y = Math.floor(months / 12), m = months % 12;
  return [y ? `${y} year${y === 1 ? "" : "s"}` : "", m ? `${m} month${m === 1 ? "" : "s"}` : ""].filter(Boolean).join(", ");
}

const WZ_CSS = `
/* Commercial terms and every other step: themed section cards, a quieter card around them, a sticky action bar */
.wz-plain{background:none!important;border:0!important;box-shadow:none!important;overflow:visible}
.wz-body{padding:0!important;background:none}
.wz-secs{display:flex;flex-direction:column;gap:14px}
.wz-sec{--t:var(--c-ind,#4f46e5);padding:16px 18px 18px;border-radius:14px;background:var(--surface,#fff);box-shadow:0 0 0 1px var(--border,#dfe3ea)}
.wz-sec[data-tone=vio]{--t:var(--c-vio,#7c3aed)}.wz-sec[data-tone=sky]{--t:var(--c-sky,#0ea5e9)}.wz-sec[data-tone=emr]{--t:var(--c-emr,#10b981)}.wz-sec[data-tone=amb]{--t:var(--c-amb,#d97706)}.wz-sec[data-tone=red]{--t:var(--c-red,#dc2626)}
.wz-sec header{display:flex;align-items:center;gap:12px;margin-bottom:14px}
.wz-st{display:grid;place-items:center;width:34px;height:34px;border-radius:10px;background:color-mix(in srgb,var(--t) 14%,transparent);color:var(--t)}
.wz-st svg{width:18px;height:18px}
.wz-sec h4{margin:0;font:700 14.5px var(--sans,inherit);color:var(--ink,#0f172a)}
.wz-sec header p{margin:1px 0 0;font-size:12px;color:var(--ink-3,#64748b)}
.wz-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px 20px}
@media (max-width:760px){.wz-grid{grid-template-columns:1fr}}
.wz-plain>.wzfoot{position:sticky;bottom:12px;z-index:5;margin-top:14px;border:0;border-radius:14px;background:var(--surface,#fff);box-shadow:0 0 0 1px var(--border,#dfe3ea),0 12px 28px -14px rgba(20,26,40,.3)}
.wz-pri{margin:14px 0 0;border:0;padding:0}.wz-pri legend{font-size:12.5px;font-weight:600;color:var(--ink,#0f172a);margin-bottom:6px}
.wz-pri [role=radiogroup]{display:inline-flex;border:1px solid var(--line,#cbd5e1);border-radius:9px;overflow:hidden}
.wz-pri button{min-height:40px;min-width:84px;padding:0 16px;font-size:13px;font-weight:500;background:transparent;color:var(--ink-2,#475569);cursor:pointer;border-right:1px solid var(--line,#cbd5e1);transition:background .15s,color .15s}
.wz-pri button:last-child{border-right:0}.wz-pri button[data-on]{background:var(--accent,#4f46e5);color:#fff}
.wz-pri button:focus-visible{outline:2px solid var(--accent,#4f46e5);outline-offset:-2px}
.wz-pri p{margin:6px 0 0;font-size:12px;color:var(--ink-3,#64748b)}
.wz-foot{display:flex;align-items:center;gap:7px;margin:12px 0 0;font-size:12.5px;color:var(--ink-3,#64748b)}
.wz-foot b{color:var(--ink,#0f172a)}.wz-foot svg{width:15px;height:15px;color:var(--c-emr,#10b981)}
.wz-tg{width:100%;text-align:left;margin-bottom:0;cursor:pointer;border-radius:8px}
.wz-tg:focus-visible{outline:2px solid var(--accent,#4f46e5);outline-offset:2px}
.wz-chev{margin-left:auto;width:16px;height:16px;color:var(--ink-3,#64748b);transition:transform .2s}.wz-chev.open{transform:rotate(180deg)}
.wz-tg .wz-pill{margin-left:4px}
.wz-sec :is(input,select,textarea,button){scroll-margin-bottom:110px}
.wz-sec :is(input:not([type=checkbox]):not([type=radio]),select){min-height:40px}
@media (max-width:760px){.wzfoot{flex-wrap:wrap;gap:8px;padding:12px 14px}.wzfoot>span{display:none}.wzfoot .btn,.wzfoot button{white-space:nowrap}.wzfoot>div{margin-left:auto}}
@media (max-width:760px){.wz-sec :is(input:not([type=checkbox]):not([type=radio]),select){min-height:44px}.wzfoot .btn,.wzfoot button{min-height:44px}.wz-sec{padding:14px}}
@media (prefers-reduced-motion:reduce){.wz-chev{transition:none}}
.wz-card{padding:16px 18px;border-radius:14px;background:var(--surface,#fff);box-shadow:0 0 0 1px var(--border,#dfe3ea)}
.wz-ch{display:flex;align-items:center;justify-content:space-between;gap:8px;margin-bottom:10px}
.wz-ch b{display:inline-flex;align-items:center;gap:7px;font:700 14px var(--sans,inherit);color:var(--ink,#0f172a)}
.wz-ch b svg{width:16px;height:16px;color:var(--c-amb,#d97706)}
.wz-pill{padding:2px 10px;border-radius:99px;font:650 11.5px var(--sans,inherit);background:color-mix(in srgb,var(--c-amb,#d97706) 14%,transparent);color:var(--c-amb,#d97706)}
.wz-lnk{font:600 12.5px var(--sans,inherit);color:var(--accent,#4f46e5)}.wz-lnk:hover{text-decoration:underline}
.wz-kv{margin:0;display:flex;flex-direction:column}
.wz-kv[hidden]{display:none}
.wz-kv>div{display:flex;justify-content:space-between;gap:12px;padding:7px 0;font-size:12.5px}
.wz-kv dt{color:var(--ink-3,#64748b)}.wz-kv dd{margin:0;font-weight:600;color:var(--ink,#0f172a);text-align:right;overflow-wrap:anywhere}
.wz-next{list-style:none;margin:0;padding:0;display:flex;flex-direction:column;gap:2px}
.wz-next li{display:grid;grid-template-columns:24px minmax(0,1fr) auto;align-items:center;gap:10px;padding:7px 0;font-size:12.5px;color:var(--ink-2,#334155)}
.wz-next i{display:grid;place-items:center;width:22px;height:22px;border-radius:50%;font:700 11px var(--sans,inherit);font-style:normal;background:var(--surface-2,#eef1f5);color:var(--ink-3,#64748b)}
.wz-next i svg{width:12px;height:12px;stroke-width:3}
.wz-next li[data-s=done] i{background:var(--good,#16a34a);color:#fff}.wz-next li[data-s=cur] i{background:var(--accent,#4f46e5);color:var(--accent-ink,#fff)}
.wz-next li[data-s=cur] span{font-weight:650;color:var(--ink,#0f172a)}
.wz-next em{font-style:normal;font-size:11.5px;color:var(--ink-3,#64748b)}.wz-next li[data-s=done] em{color:var(--good,#16a34a)}.wz-next li[data-s=cur] em{color:var(--accent,#4f46e5)}
.wz-note{margin:8px 0 0;padding:9px 11px;border-radius:10px;background:var(--inset,#f3f5f9);font-size:12.5px;line-height:1.5;color:var(--ink-2,#334155)}
.wz-tips{margin:0;padding-left:18px;display:flex;flex-direction:column;gap:7px;font-size:12.5px;line-height:1.5;color:var(--ink-2,#334155)}

.wz-hd{display:flex;align-items:center;gap:14px;padding:4px 0 14px}
.wz-ic{flex:none;display:grid;place-items:center;width:46px;height:46px;border-radius:14px;background:color-mix(in srgb,var(--accent,#4f46e5) 14%,transparent);color:var(--accent,#4f46e5)}
.wz-ic svg{width:24px;height:24px}
.wz-tt{flex:1;min-width:0}
.wz-tt h2{margin:2px 0 0;font:700 22px/1.2 var(--sans,inherit);letter-spacing:-.02em;color:var(--ink,#0f172a)}
.wz-tt p{margin:3px 0 0;font-size:13px;color:var(--ink-3,#64748b)}
.wz-saved{flex:none;font-size:12px;color:var(--ink-3,#64748b)}.wz-saved.warn{color:var(--warn,#d97706)}
.wz-steps{list-style:none;margin:0 0 16px;padding:12px 16px;display:flex;gap:6px;border-radius:14px;background:var(--surface,#fff);box-shadow:0 0 0 1px var(--border,#dfe3ea);overflow-x:auto}
.wz-steps li{position:relative;flex:1;min-width:150px}
.wz-steps li+li::before{content:"";position:absolute;left:-4px;top:50%;width:8px;border-top:2px solid var(--border,#dfe3ea)}
.wz-steps li[data-state=done]+li::before,.wz-steps li[data-state=cur]::before{border-color:var(--good,#16a34a)}
.wz-steps button{display:flex;align-items:center;gap:10px;width:100%;padding:6px 8px;border-radius:10px;text-align:left;transition:background .15s}
.wz-steps button:hover{background:var(--surface-2,#eef1f5)}
.wz-steps b{flex:none;display:grid;place-items:center;width:30px;height:30px;border-radius:50%;font:700 12.5px var(--sans,inherit);background:var(--surface-2,#eef1f5);color:var(--ink-3,#64748b);box-shadow:inset 0 0 0 1.5px var(--border-strong,#b8c0cc)}
.wz-steps b svg{width:15px;height:15px;stroke-width:2.8}
.wz-steps li[data-state=done] b{background:var(--good,#16a34a);color:#fff;box-shadow:none}
.wz-steps li[data-state=cur] b{background:var(--accent,#4f46e5);color:var(--accent-ink,#fff);box-shadow:0 0 0 4px color-mix(in srgb,var(--accent,#4f46e5) 18%,transparent)}
.wz-steps em{display:block;font:650 13px var(--sans,inherit);font-style:normal;color:var(--ink,#0f172a)}
.wz-steps li[data-state=todo] em{color:var(--ink-2,#334155);font-weight:600}
.wz-steps small{display:block;font-size:11.5px;color:var(--ink-3,#64748b)}
`;

export function AgreementWizard({ def, draft, onFiled, onBack }: {
  def: AgreementFormDef;
  draft?: IntakeDraft | null;
  onFiled: (id: string) => void;
  onBack: () => void;
}) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { user } = useAuth();
  const { data: forms, isLoading: formsLoading, error: formsError, refetch: refetchForms } = useRequestForms();
  const server = forms?.find((f) => f.key === def.key);
  const q = useMemo(() => new Map((server?.fields ?? []).map((f) => [f.key, f])), [server]);
  // Question labels by key: a table's window message names "Services start" and "Services end".
  const labels = useMemo(() => Object.fromEntries([...q].map(([k, f]) => [k, f.label])), [q]);

  const steps = [...def.steps, { title: REVIEW, fields: [] as string[] }];
  const [step, setStep] = useState(Math.min(draft?.page_index ?? 0, steps.length - 1));
  const [visited, setVisited] = useState(draft?.visited ?? 0);
  const [values, setValues] = useState<Values>(draft?.values ?? { currency: "INR" });
  const [files, setFiles] = useState<Record<string, File | null>>({});
  // The questions the last Continue refused on. Whether each STILL has a problem is read fresh from the answers
  // (problemOf), so the banner above the form shrinks as they are fixed instead of listing fields already filled.
  const [flagged, setFlagged] = useState<Set<string>>(new Set());
  // A refusal that is not about one question (a file that is too large).
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [draftId, setDraftId] = useState<string | null>(draft?.id ?? null);
  const [savedAt, setSavedAt] = useState<string | null>(draft?.updated_at ?? null);
  const [dirty, setDirty] = useState(false);
  // The summary starts folded on a phone, where it would push the form down; always open on a desktop.
  const [sumOpen, setSumOpen] = useState(true);
  useEffect(() => { if (typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia("(max-width: 1023px)").matches) setSumOpen(false); }, []);
  const [saving, setSaving] = useState(false);
  // Editable override for the generated subject line. Blank means "use the
  // generated name" (subject() below), so requesters who don't care never type.
  const [nameOverride, setNameOverride] = useState("");

  const { data: contracts } = useQuery({ queryKey: ["contracts"], queryFn: () => contractsApi.list(),
    enabled: def.steps.some((s) => s.fields.includes("parent_contract_id")) });
  const { data: myRequests } = useQuery({ queryKey: ["intake-mine"], queryFn: () => intakeApi.mine(),
    enabled: def.key === "cancellation" });
  const parent = (contracts ?? []).find((c) => c.id === values.parent_contract_id) ?? null;

  const set = (patch: Values) => {
    setDirty(true);
    setValues((prev) => ({ ...prev, ...patch }));
  };

  const live = useMemo(() => liveAnswers(server?.fields ?? [], values), [server, values]);

  // The request in words. A failure here only hides the box; filing never depends on it (FR-9).
  const sumCtx = useMemo(() => ({ parentTitle: parent?.title ?? null }), [parent]);
  const lines = useMemo<SummaryLine[] | null>(() => {
    if (!server) return null;
    try { return buildSummary(server, def, values, sumCtx); } catch { return null; }
  }, [server, def, values, sumCtx]);
  const [box, setBox] = useState<SummaryBoxState>(draft?.summary_box ?? EMPTY_BOX);
  const [announce, setAnnounce] = useState("");
  // What was typed in the box; null = show the sentences (with any kept edits). Never rewritten while offers are open.
  const [boxText, setBoxText] = useState<string | null>(null);
  const checkCtx = (): CheckContext | null => (server && lines ? { form: server, def, values, lines, ctx: sumCtx } : null);
  // Withdraws offers whose answer moved and resolves edits whose line changed; also covers a resumed draft (FR-23, FR-36).
  // ponytail: depends on `lines` alone (it already follows `values`); a stale `checkCtx` closure is never read.
  useEffect(() => { const c = checkCtx(); if (c) setBox((b) => reconcile(b, c)); }, [lines]);
  const touch = (b: SummaryBoxState) => { setBox(b); setDirty(true); };

  const labelOf = (k: string) => UI[k]?.label ?? q.get(k)?.label ?? k;
  const isShown = (k: string) => shown(UI[k]?.show ?? q.get(k)?.show, values);
  const required = (k: string) => (UI[k]?.kind === "file" ? !!UI[k].req : !!q.get(k)?.required);
  const visible = (i: number) => (steps[i]?.fields ?? []).filter((k) => (q.has(k) || FILE_KEYS.includes(k)) && isShown(k));

  const isBlankAnswer = (k: string) => {
    if (UI[k]?.kind === "file") return !files[k];
    if ((COMPANION[k] ?? []).some((c) => blank(values[c]))) return true;
    return blank(answerOf(k));
  };
  // A multi-tick question holds a list. A saved form's older single choice ("Several") is a string: it is shown
  // beside the ticks for reference but is not an answer, so the person ticks (FR-45).
  const answerOf = (k: string) => (q.get(k)?.kind === "multiselect" && !Array.isArray(values[k]) ? [] : values[k]);

  /** What stops this question being accepted right now, or null. A required question left empty, an email that
   *  can never be delivered, an end date before its start date: the server refuses the same answers when the
   *  request is filed (round 2, bug 7). */
  function problemOf(k: string): string | null {
    if (!isShown(k)) return null;
    const field = q.get(k);
    if (field?.kind === "rows") return rowsProblems(field, values[k], live, labels)[0]?.message ?? null;
    if (isBlankAnswer(k)) return required(k) ? NEEDED : null;
    const earlier = field?.not_before && isShown(field.not_before) ? field.not_before : null;
    return answerProblem(field?.kind ?? UI[k]?.kind, values[k], earlier ? { label: labelOf(earlier), value: values[earlier] } : undefined,
      field ? rulesFor(field, live) : undefined, field?.kind === "select" ? optionsFor(field, live) : undefined);
  }

  const blocking = (i: number) => visible(i).filter((k) => problemOf(k));

  /** The line above the form: what is still wrong with the questions the last Continue refused on. */
  function barText(): string {
    if (notice) return notice;
    const open = [...flagged].filter((k) => problemOf(k));
    const isTable = (k: string) => q.get(k)?.kind === "rows";
    const needed = open.filter((k) => problemOf(k) === NEEDED);
    return [
      needed.length ? `${needed.length === 1 ? "1 answer" : `${needed.length} answers`} still needed: ${needed.map(labelOf).join(", ")}.` : "",
      ...open.filter((k) => problemOf(k) !== NEEDED && !isTable(k)).map((k) => `${labelOf(k)}: ${problemOf(k)}`),
      // A table's messages already name the row and the column, so they are listed as they are.
      ...open.filter(isTable).flatMap((k) => rowsProblems(q.get(k)!, values[k], live, labels).map((p) => p.message)),
    ].filter(Boolean).join(" ");
  }

  /** Stop on step `i`, mark what is wrong there. */
  function refuse(i: number, keys: string[]) {
    setStep(i);
    setNotice("");
    setFlagged(new Set(keys));
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  // What gets filed. Check-approvers and the routing preview send exactly this.
  const body = useMemo(() => {
    // Only answers that apply: a table hidden by "advice only" or a rate under "Fixed fee" is not filed.
    const fv: Values = { ...live, request_form: def.key };
    for (const f of server?.fields ?? []) {
      if (f.kind !== "rows") continue;
      // Only the columns that apply to the pricing chosen (shares are never filed unless Milestones); empty rows
      // stay, so the row numbers in a filing error are the ones on the page. The old free-text key is never sent.
      if (f.legacy_key) delete fv[f.legacy_key];
      if (Array.isArray(live[f.key])) {
        const keep = shownColumns(f, live).map((c) => c.key);
        fv[f.key] = rowsOf(live[f.key]).map((r) => Object.fromEntries(keep.map((c) => [c, r[c] ?? ""])));
      }
    }
    if (parent) {
      fv.parent_contract_title = parent.title;
      if (!fv.counterparty) fv.counterparty = parent.counterparty_name ?? "";
    }
    const kind = def.key === "new_agreement" && typeof values.agreement_type === "string" ? values.agreement_type : "";
    const text = [values.reason, values.processing_purpose, values.note_approvers]
      .find((x) => typeof x === "string" && x.trim()) as string | undefined;
    return {
      type_label: kind ? `New agreement · ${kind}` : `${def.name} Request`,
      priority: priorityOf(values),
      department: typeof values.department === "string" ? values.department || null : null,
      description: text ?? def.name,
      field_values: fv,
    };
  }, [values, live, parent, def, server]);

  const preview = useQuery({
    queryKey: ["approval-preview", body],
    queryFn: () => intakeApi.approvalPreview(body),
    // Routing reads step-1 answers (kind, paper, needed by), so ask from the
    // start — once a New agreement has a kind, there's something to route.
    enabled: def.key !== "new_agreement" || !blank(values.agreement_type),
    placeholderData: (prev) => prev,
    staleTime: 30_000,
  });

  function go(target: number) {
    if (target > step) {
      for (let i = step; i < target; i++) {
        const miss = blocking(i);
        if (miss.length) { refuse(i, miss); return; }
      }
    }
    setNotice(""); setFlagged(new Set());
    setVisited((v) => Math.max(v, target));
    setStep(target);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  function subject(): string {
    if (nameOverride.trim()) return nameOverride.trim().slice(0, 200);
    const who = (typeof values.counterparty === "string" && values.counterparty) || parent?.counterparty_name || parent?.title || "";
    const kind = typeof values.agreement_type === "string" ? values.agreement_type : def.name;
    return [def.key === "new_agreement" ? kind : def.name, who].filter(Boolean).join(" — ").slice(0, 200);
  }

  function onCheck() {
    const c = checkCtx();
    if (!c || boxText === null) return;
    const r = checkText(box, boxText, c);
    setBox(r.box);
    setDirty(true);
    if (r.announce) setAnnounce(r.announce);
    if (!r.open) setBoxText(null); // every offer answered (or none): rebuild the box from the answers
  }
  function onAccept(offerId: string) {
    const c = checkCtx();
    if (!c) return;
    const r = acceptOffer(box, offerId, c);
    setBox(r.box);
    set(r.patch); // the same path as a form control, so routing and "only when" rules react
    setAnnounce(r.announce);
    if (!r.box.offers.length) setBoxText(null);
    setTimeout(() => document.getElementById(SUMMARY_BOX_ID)?.focus(), 0);
  }
  function onDecline(offerId: string) {
    const c = checkCtx();
    if (!c) return;
    const r = declineOffer(box, offerId, c);
    setBox(r.box);
    setAnnounce(r.announce);
    if (!r.box.offers.length) setBoxText(null);
  }

  async function saveDraft() {
    setSaving(true);
    try {
      const payload = { form_key: def.key, title: subject(), values, parent_contract_id: parent?.id ?? null, summary_box: box,
        // The server counts pages from 1; a draft saved before moving off the first page has visited 0.
        page_index: step, visited: Math.max(1, visited) };
      const saved = draftId ? await intakeApi.updateDraft(draftId, payload) : await intakeApi.createDraft(payload);
      setDraftId(saved.id); setSavedAt(saved.updated_at); setDirty(false);
      qc.invalidateQueries({ queryKey: ["intake-drafts"] });
      notify(Object.values(files).some(Boolean)
        ? "Draft saved. Attached files aren't kept in drafts — attach them again when you continue."
        : "Draft saved — find it under Your drafts on the New request page.", "success");
    } catch (e) {
      notify(e instanceof Error ? e.message : "Couldn't save the draft", "error");
    } finally { setSaving(false); }
  }

  async function submit() {
    for (let i = 0; i < def.steps.length; i++) {
      const miss = blocking(i);
      if (miss.length) { refuse(i, miss); return; }
    }
    // The document that IS the contract goes last: the server takes the newest
    // readable attachment as the contract.
    const ordered = ["attachments", "their_draft", "executed"].map((k) => files[k]).filter((f): f is File => !!f);
    const bad = ordered.map(attachmentProblem).find(Boolean);
    if (bad) { setNotice(bad); return; }
    // Unchecked edits are checked; open offers hold Submit (below); the word limit refuses.
    const c = checkCtx();
    // Typed words not yet checked (no blur happened) are checked now; checking twice adds nothing twice.
    const checked = c && boxText !== null ? checkText(box, boxText, c).box : box;
    // FR-34: an open offer is the requester's to answer; nothing is filed, nothing becomes an instruction.
    if (checked.offers.length) {
      const n = checked.offers.length;
      setBox(checked);
      setNotice(`You have ${n} ${n === 1 ? "change" : "changes"} waiting — update or keep each one, then submit.`);
      setTimeout(() => document.querySelector<HTMLElement>("#riw-offers button")?.focus(), 0);
      return;
    }
    const fin = c ? finalize(checked, c) : addPending(checked);
    setBox(fin);
    const words = wordCount(fin);
    if (words > WORD_LIMIT) {
      setNotice(`Your extra instructions are ${words.toLocaleString("en-US")} words, over the limit of about 2,000. ${attachHint ? `Shorten them, or ${attachHint}.` : NO_ATTACH}`);
      return;
    }
    setBusy(true);
    try {
      const created = await intakeApi.create({
        ...body, subject: subject(), requester_name: user?.full_name ?? null,
        summary: { text: lines ? summaryText(lines) : null, instructions: fin.instructions, accepted: fin.accepted },
        attachments: await Promise.all(ordered.map(toAttachment)),
      });
      if (files.executed || (values.paper === "Their paper" && files.their_draft)) {
        try {
          await intakeApi.ingestAttachment(created.id);
        } catch {
          notify(`${created.ref} filed. The document could not be read automatically — Legal will open it.`, "info");
        }
      }
      if (draftId) {
        await intakeApi.deleteDraft(draftId).catch(() => undefined);
        qc.invalidateQueries({ queryKey: ["intake-drafts"] });
      }
      qc.invalidateQueries({ queryKey: ["intake-mine"] });
      qc.invalidateQueries({ queryKey: ["intake-list"] });
      notify(`Filed ${created.ref}`, "success");
      onFiled(created.id);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Submit failed", "error");
    } finally { setBusy(false); }
  }

  // The saved form's older single choice for a multi-tick question, shown beside the ticks. It comes from the
  // draft as opened, so it stays while the person ticks.
  const earlierOf = (k: string) => {
    const saved = draft?.values[k];
    return q.get(k)?.kind === "multiselect" && typeof saved === "string" && saved.trim() ? saved : undefined;
  };

  const isReview = step === steps.length - 1;
  const attachHint = attachHintFor(def);
  const shownNow = visible(step);
  const bar = barText();

  const routing: [string, string][] = [
    ["Workflow", def.key === "cancellation" ? "None: the request is withdrawn"
      : preview.data?.workflow?.name ?? (preview.isFetching ? "Working it out…"
        : preview.isSuccess ? "None fits yet: a person picks one after filing" : "Pick the kind of agreement")],
    ["Draft", draftFor(def.key, values)],
    ["Approvals", approvalsText({
      approvers: preview.data?.approvers ?? [], fetching: preview.isFetching, loaded: preview.isSuccess,
      noWorkflow: preview.isSuccess && !preview.data?.workflow && def.key !== "cancellation",
      valueMissing: def.steps.some((_, i) => visible(i).includes("value")) && !String(values.value ?? "").trim(),
    })],
    ["Priority", values.priority ? `${priorityOf(values)} (your choice)`
      : priorityOf(values) === "High" ? "High: needed within 7 days" : "Medium"],
    ["Owner", "Assigned when filed, never you"],
  ];
  if (typeof values.cp_signer_email === "string" && values.cp_signer_email) {
    routing.push(["Their signer", `${values.cp_signer_name || ""}\n${values.cp_signer_email}`.trim()]);
  }
  if (FINISH[def.key]) routing.push(["When the workflow finishes", FINISH[def.key]]);

  const summary: [string, string][] = [];
  const tables = isReview
    ? def.steps.flatMap((_, i) => visible(i)).flatMap((k) => { const f = q.get(k); return f?.kind === "rows" ? [f] : []; })
    : [];
  if (isReview) {
    def.steps.forEach((_, i) => visible(i).forEach((k) => {
      if (q.get(k)?.kind === "rows") return; // a table is shown as one, below
      const v = values[k];
      const shown = UI[k]?.kind === "file" ? files[k]?.name
        : UI[k]?.kind === "money" || k === "fee_rate" ? moneyText(values.currency, v)
        : k === "parent_contract_id" ? parent?.title
        : q.get(k)?.kind === "date" && typeof v === "string" && v ? fmtDate(v) // the box they typed in shows dd/mm/yyyy
        : Array.isArray(v) ? v.join(", ") : v;
      if (shown) summary.push([labelOf(k), String(shown)]);
    }));
  }

  if (!server) {
    // No half-built form while the questions load or when they could not be loaded.
    if (formsError && !formsLoading) {
      return <LoadError error={formsError} what="the form" onRetry={() => refetchForms()} onBack={onBack} backLabel="All request types" />;
    }
    return <div className="py-16 text-center text-[13px] text-slate-500">{formsLoading ? "Loading the form…" : "This form isn't available."}</div>;
  }

  return (
    <div className="mx-auto flex max-w-[1180px] flex-col">
      <style>{WZ_CSS}</style>
      <header className="wz-hd">
        <span className="wz-ic"><FileText aria-hidden /></span>
        <div className="wz-tt">
          <Button variant="ghost" size="sm" className="-ml-2" onClick={onBack}>
            <ChevronLeft className="h-4 w-4" />All request types
          </Button>
          <h2>{def.name}</h2>
          <p>{parent ? <>on <b>{parent.title}</b></> : def.desc}</p>
        </div>
        <span className={cn("wz-saved", dirty && savedAt && "warn")}>
          {!savedAt ? "Not saved yet" : dirty ? "Unsaved changes" : `Draft saved ${new Date(savedAt).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}`}
        </span>
      </header>

      <ol className="wz-steps" aria-label="Steps">
        {steps.map((s, i) => {
          const cur = i === step, done = i < step || i <= visited - 1 && i !== step;
          return (
            <li key={s.title} data-state={cur ? "cur" : done ? "done" : "todo"}>
              <button type="button" onClick={() => go(i)} aria-current={cur ? "step" : undefined}>
                <b>{done && !cur ? <Check /> : i + 1}</b>
                <span><em>{s.title}</em><small>{cur && !isReview ? `${shownNow.length} question${shownNow.length === 1 ? "" : "s"}` : i === steps.length - 1 ? "Check and submit" : done && !cur ? "Completed" : "Pending"}</small></span>
              </button>
            </li>
          );
        })}
      </ol>

      <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
        <Card className={cn(!isReview && "wz-plain")}>
          <CardBody className={cn("space-y-5", isReview ? "min-h-[420px] px-7 py-7" : "wz-body")}>
            <div>
              <div className="text-[10.5px] font-semibold uppercase tracking-[0.12em] text-brand-600">Step {step + 1} of {steps.length}</div>
              <h3 className="mt-1.5 text-[19px] font-semibold tracking-[-0.015em] text-slate-900">{steps[step].title}</h3>
            </div>
            {!isReview && (
              <a href="#request-in-words" className="block text-[12.5px] font-medium text-brand-700 underline lg:hidden">
                Read your request in words
              </a>
            )}
            {bar && (
              <div role="alert" className="flex gap-2.5 rounded-lg border border-danger/40 bg-danger-subtle px-3.5 py-3 text-[12.5px] text-danger">
                <TriangleAlert className="mt-px h-4 w-4 shrink-0" /><span>{bar}</span>
              </div>
            )}

            {isReview ? (
              <>
                <label className="block space-y-1.5">
                  <FieldLabel label="Name this request (optional)" />
                  <Input aria-label="Name this request" value={nameOverride} maxLength={200}
                    placeholder={subject()} onChange={(e) => setNameOverride(e.target.value)} />
                  <span className="block text-[11.5px] text-slate-500">Shown on your queue and dashboards instead of the generated name.</span>
                </label>
                {lines && (
                  <SummaryEditor lines={lines} box={box} text={boxText ?? textWithEdits(lines, box)} announce={announce}
                    onText={setBoxText} onCheck={onCheck} onAccept={onAccept} onDecline={onDecline}
                    onUndo={() => { setBox(undoEdits(box)); setBoxText(null); setAnnounce(""); setDirty(true); }} />
                )}
                <dl className="grid grid-cols-1 gap-x-8 gap-y-3 md:grid-cols-2">
                  {summary.map(([k, v]) => (
                    <div key={k} className="border-b border-slate-200 pb-2.5">
                      <dt className="text-[11.5px] text-slate-500">{k}</dt>
                      <dd className="mt-0.5 whitespace-pre-line text-[13.5px] font-medium text-slate-900">{v}</dd>
                    </div>
                  ))}
                </dl>
                {tables.map((f) => (
                  <div key={f.key} className="space-y-1.5">
                    <div className="text-[11.5px] text-slate-500">{f.label}</div>
                    <RowsTable field={f} rows={tidiedRows(f, live[f.key], live)} values={live} labels={labels} />
                  </div>
                ))}
                <label className="block space-y-1.5">
                  <FieldLabel label="Note to approvers (optional)" />
                  <textarea rows={3} className={cn(CONTROL, "h-auto border-slate-300 py-2.5 leading-relaxed")}
                    value={String(values.note_approvers ?? "")} placeholder="Two lines beat a paragraph."
                    onChange={(e) => set({ note_approvers: e.target.value })} />
                </label>
                <ExtraInstructions items={box.instructions} pending={box.pending} words={wordCount(box)} attachHint={attachHint}
                  onPending={(t) => touch({ ...box, pending: t })} onAdd={() => touch(addPending(box))}
                  onRemove={(i) => touch(removeInstruction(box, i))} />
              </>
            ) : (
              <div className="wz-secs">
                {(() => {
                  const placed = new Set<string>();
                  const groups = SECTIONS.map((sec) => ({ ...sec, ks: sec.keys.filter((k) => shownNow.includes(k)) })).filter((g) => g.ks.length);
                  groups.forEach((g) => g.ks.forEach((k) => placed.add(k)));
                  const rest = shownNow.filter((k) => !placed.has(k));
                  const all = rest.length ? [...groups, { title: "Other details", hint: "", Icon: Info, tone: "ind", keys: [], ks: rest }] : groups;
                  return all.map((g) => (
                    <section key={g.title} className="wz-sec" data-tone={g.tone} aria-label={g.title}>
                      <header><span className="wz-st"><g.Icon aria-hidden /></span><div><h4>{g.title}</h4>{g.hint ? <p>{g.hint}</p> : null}</div></header>
                      <div className="wz-grid">
                        {g.ks.map((k) => (

                      <div key={k} className={cn("min-w-0", WIDE_QUESTION.has(UI[k]?.kind ?? "") || ["textarea", "rows", "multiselect"].includes(q.get(k)?.kind ?? "") ? "md:col-span-2" : "")}>
                      <Question k={k} label={labelOf(k)} req={required(k)} kind={UI[k]?.kind} field={q.get(k)}
                        help={UI[k]?.help} placeholder={UI[k]?.placeholder} values={live} set={set} bad={flagged.has(k) && !!problemOf(k)} problem={problemOf(k)}
                        labels={labels} earlier={earlierOf(k)}
                        min={notBeforeValue(q.get(k), values)}
                        file={files[k] ?? null} onFile={(f) => { setFiles((p) => ({ ...p, [k]: f })); setDirty(true); setNotice(""); }}
                        currencies={q.get("currency")?.options ?? ["INR"]}
                        contracts={contracts ?? []} requests={myRequests ?? []} />
                      </div>
                        ))}
                      </div>
                      {g.title === "Dates and ownership" && q.get("needed_by") ? (
                        <fieldset className="wz-pri">
                          <legend>Priority</legend>
                          <div role="radiogroup" aria-label="Priority">
                            {PRIORITIES.map((p) => (
                              <button key={p} type="button" role="radio" aria-checked={priorityOf(values) === p} data-on={priorityOf(values) === p ? "" : undefined}
                                onClick={() => set({ priority: p })}>{p}</button>
                            ))}
                          </div>
                          <p>{values.priority ? "Your choice. " : "Suggested from the needed-by date. "}Critical is only set when Legal escalates a request.</p>
                        </fieldset>
                      ) : null}
                      {g.title === "Contract term" && durationText(live.start_date, live.end_date) ? <p className="wz-foot" aria-live="polite"><CalendarRange aria-hidden />Duration: <b>{durationText(live.start_date, live.end_date)}</b></p> : null}
                      {g.title === "Special terms" ? <p className="wz-foot">{String(live.nonstandard ?? "").length} characters</p> : null}
                    </section>
                  ));
                })()}
              </div>
            )}
          </CardBody>
          <div className="wzfoot flex items-center gap-3 border-t border-slate-200 px-7 py-4">
            {step > 0 && <Button variant="outline" onClick={() => go(step - 1)}>← Back</Button>}
            <span className="text-[12px] text-slate-500">{isReview ? "Check your answers, then submit" : `${shownNow.length} question${shownNow.length === 1 ? "" : "s"} on this step`}</span>
            <div className="ml-auto flex gap-2">
              <Button variant="outline" className="rounded-full px-5" loading={saving} onClick={saveDraft}>Save as draft</Button>
              {isReview
                ? <Button loading={busy} className="rounded-full px-6" onClick={submit}>Submit request</Button>
                : <Button className="rounded-full px-6" onClick={() => go(step + 1)}>Continue</Button>}
            </div>
          </div>
        </Card>

        <aside className="flex flex-col gap-3 lg:sticky lg:top-5">
          {!isReview && <LiveSummary lines={lines} instructions={box.instructions.length} />}
          {!isReview && (
            <div className="rounded-xl border border-slate-200 bg-slate-100 p-4 shadow-card">
              <div className="mb-3 text-[10.5px] font-semibold uppercase tracking-[0.12em] text-slate-500">What these answers do</div>
              <ul className="space-y-3">
                {shownNow.map((k) => (
                  <li key={k} className="flex gap-2.5">
                    <span className={cn("mt-1.5 h-2 w-2 shrink-0 rounded-full",
                      (UI[k]?.kind === "file" ? files[k] : !blank(answerOf(k))) ? "bg-success" : "bg-slate-300")} />
                    <span>
                      <span className="block text-[12.5px] font-semibold text-slate-800">{labelOf(k)}</span>
                      <span className="block text-[12px] leading-snug text-slate-600">{UI[k]?.uses}</span>
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
          <div className="rounded-xl border border-slate-200 bg-slate-100 p-4 shadow-card">
            <div className="mb-3 text-[10.5px] font-semibold uppercase tracking-[0.12em] text-slate-500">Where this request goes</div>
            <dl className="space-y-2.5">
              {routing.map(([k, v]) => (
                <div key={k} className="border-b border-slate-200 pb-2.5 last:border-0 last:pb-0">
                  <dt className="text-[11.5px] text-slate-500">{k}</dt>
                  <dd className="mt-0.5 whitespace-pre-line text-[13px] font-medium text-slate-900">{v}</dd>
                </div>
              ))}
            </dl>
            <p className="mt-3 flex gap-1.5 text-[11.5px] text-slate-500"><Info className="mt-px h-3.5 w-3.5 shrink-0" />Updates as you answer.</p>
          </div>
          {!isReview && (
            <div className="wz-card">
              <button type="button" className="wz-ch wz-tg" aria-expanded={sumOpen} aria-controls="wz-sum" onClick={() => setSumOpen((o) => !o)}>
                <b>Request summary</b><span className="wz-pill">Draft</span><ChevronDown className={cn("wz-chev", sumOpen && "open")} aria-hidden />
              </button>
              <dl className="wz-kv" id="wz-sum" hidden={!sumOpen}>
                <div><dt>Request type</dt><dd>{String(values.agreement_type ?? "") || def.name}</dd></div>
                {(["entity", "counterparty", "department"] as const).filter((k) => q.get(k)).map((k) => (
                  <div key={k}><dt>{k === "entity" ? "Legal entity" : k === "counterparty" ? "Counterparty" : "Department"}</dt><dd>{String(values[k] ?? "") || "—"}</dd></div>
                ))}
                {q.get("needed_by") ? <div><dt>Required by</dt><dd>{values.needed_by ? fmtDate(String(values.needed_by)) : "—"}</dd></div> : null}
                <div><dt>Step</dt><dd>{step + 1} of {steps.length}</dd></div>
              </dl>
            </div>
          )}
          {!isReview && (
            <div className="wz-card">
              <div className="wz-ch"><b>What&apos;s next?</b></div>
              <ol className="wz-next">
                {steps.map((st, i) => (
                  <li key={st.title} data-s={i < step ? "done" : i === step ? "cur" : "todo"}>
                    <i aria-hidden>{i < step ? <Check /> : i + 1}</i><span>{st.title}</span>
                    <em>{i < step ? "Completed" : i === step ? "In progress" : "Pending"}</em>
                  </li>
                ))}
              </ol>
              {steps[step + 1] ? <p className="wz-note"><b>Next:</b> {NEXT_STEP_TEXT[steps[step + 1].title] ?? "Check your answers."}</p> : null}
            </div>
          )}
          {!isReview && (
            <div className="wz-card">
              <div className="wz-ch"><b><Lightbulb aria-hidden />Helpful tips</b></div>
              <ul className="wz-tips">{TIPS.map((t) => <li key={t}>{t}</li>)}</ul>
            </div>
          )}
          {!isReview && (
            <div className="wz-card">
              <div className="wz-ch"><b>Standard templates</b><Link href="/templates" className="wz-lnk">View all</Link></div>
              <p className="wz-note">Our standard paper for each kind of agreement is kept on the Templates page.</p>
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}

/**
 * One question as a control: the label, the control the question's kind calls for, and the problem or help
 * under it. The wizard uses it for every question; the request page uses it to let an older request answer the
 * questions its draft needs. Only `k`, `label`, `req`, `values`, `labels` and `set` are needed: the rest has a
 * default (no file, no problem, no register lookups).
 */
export function Question({ k, label, req, kind, field, help, placeholder, values, labels, set, bad = false, problem, min, earlier,
  file = null, onFile = () => undefined, currencies = ["INR"], contracts = [], requests = [] }: {
  k: string; label: string; req: boolean; kind?: UiKind; field?: RequestFormDef["fields"][number];
  help?: string; placeholder?: string; values: Values; set: (p: Values) => void; bad?: boolean;
  /** Every question's label by key (a table's window message names the dates it falls between). */
  labels: Record<string, string>;
  /** What is wrong with the answer, in words (shown under the question when `bad`). */
  problem?: string | null;
  /** Date questions: the earliest day the date picker offers. */
  min?: string;
  /** Multi-tick questions: an older single choice (a string, such as "Several"), shown beside the ticks, never filed. */
  earlier?: string;
  file?: File | null; onFile?: (f: File | null) => void; currencies?: string[];
  contracts?: { id: string; title: string; counterparty_name: string | null; expiration_date: string | null }[];
  requests?: { id: string; ref: string; subject: string | null; type_label: string; status: string }[];
}) {
  const v = values[k];
  const str = typeof v === "string" ? v : "";
  const border = bad ? "border-danger bg-danger-subtle" : "border-slate-300";
  const select = (value: string, options: { value: string; label: string }[], onChange: (x: string) => void, cls = "") => (
    <select aria-label={label} className={cn(CONTROL, border, cls)} value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">Select…</option>
      {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
    </select>
  );
  const opts = (field ? optionsFor(field, values) : []).map((o) => ({ value: o, label: o }));

  let control: ReactNode;
  if (kind === "entity" || kind === "counterparty" || kind === "party") {
    const idKey = `${k}_id`;
    control = (
      <RegisterLookup kind={kind === "entity" ? "entity" : "counterparty"} label={label} name={str}
        recordId={String(values[idKey] ?? "")} bad={bad}
        onPick={(r, typed) => set(r ? { [k]: r.name, [idKey]: r.id } : { [k]: typed ?? "", [idKey]: "" })} />
    );
  } else if (kind === "contract") {
    control = select(str, contracts.map((c) => ({ value: c.id,
      label: [c.title, c.counterparty_name, c.expiration_date ? `ends ${fmtDate(c.expiration_date)}` : null].filter(Boolean).join(" · ") })),
    (x) => set({ [k]: x }));
  } else if (kind === "request") {
    control = select(str, requests.filter((r) => r.status !== "closed").map((r) => ({ value: r.ref,
      label: `${r.ref} · ${r.subject || r.type_label}` })), (x) => set({ [k]: x }));
  } else if (kind === "money") {
    control = (
      <div className="flex gap-2">
        {select(String(values.currency ?? ""), currencies.map((c) => ({ value: c, label: c })), (x) => set({ currency: x }), "w-[110px] shrink-0")}
        <input aria-label={label} inputMode="decimal" className={cn(CONTROL, border)} value={str}
          placeholder="Amount over the whole term" onChange={(e) => set({ [k]: e.target.value })} />
      </div>
    );
  } else if (kind === "file") {
    control = (
      <div className="flex flex-wrap items-center gap-3">
        {file ? (
          <span className="inline-flex h-10 items-center gap-2 rounded-lg border border-success/40 bg-success-subtle px-3 text-[13px] text-success">
            <Paperclip className="h-4 w-4" />{file.name}
            <button type="button" aria-label={`Remove ${file.name}`} onClick={() => onFile(null)} className="text-slate-500 hover:text-slate-900"><X className="h-3.5 w-3.5" /></button>
          </span>
        ) : (
          <label className={cn("inline-flex h-10 cursor-pointer items-center gap-2 rounded-lg border border-dashed px-3.5 text-[13px] font-medium",
            bad ? "border-danger bg-danger-subtle text-danger" : "border-brand-300 text-brand-700 hover:bg-brand-50")}>
            <Paperclip className="h-4 w-4" />Choose file
            <input type="file" accept={ATTACHMENT_ACCEPT} className="sr-only" onChange={(e) => onFile(e.target.files?.[0] ?? null)} />
          </label>
        )}
        <span className="text-[12px] text-slate-500">{ATTACHMENT_LIMITS_TEXT}</span>
      </div>
    );
  } else if (field?.kind === "rows") {
    const earlier = field.legacy_key ? values[field.legacy_key] : undefined;
    control = (
      <div role="group" aria-label={label} className="space-y-3">
        {typeof earlier === "string" && earlier.trim() && (
          <EarlierAnswer text={earlier} title="Your earlier free-text answer (kept for reference, not filed)" />
        )}
        <RowsTable field={field} rows={rowsOf(v)} values={values} labels={labels}
          problems={bad ? rowsProblems(field, v, values, labels) : []} onChange={(rows) => set({ [k]: rows })} />
      </div>
    );
  } else if (field?.kind === "multiselect") {
    const picked = Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
    control = (
      <div className="space-y-3">
        {earlier && <EarlierAnswer text={earlier} title="Your earlier answer (kept for reference, not filed)" />}
        <div role="group" aria-label={label} className={cn("flex flex-wrap gap-x-5 gap-y-2 rounded-lg border px-3 py-2.5", border)}>
          {opts.map((o) => (
            <label key={o.value} className="inline-flex items-center gap-2 text-[13px] text-slate-800">
              <input type="checkbox" checked={picked.includes(o.value)}
                onChange={(e) => set({ [k]: e.target.checked ? [...picked, o.value] : picked.filter((x) => x !== o.value) })} />
              {o.label}
            </label>
          ))}
        </div>
      </div>
    );
  } else if (field?.kind === "select") {
    control = select(str, opts, (x) => set({ [k]: x }));
  } else if (field?.kind === "textarea") {
    control = <textarea aria-label={label} rows={3} className={cn(CONTROL, border, "h-auto py-2.5 leading-relaxed")}
      value={str} placeholder={placeholder} onChange={(e) => set({ [k]: e.target.value })} />;
  } else {
    // A number is typed as text with the decimal keypad, as the money control does: a number box would drop "1,500"
    // and let "1e3" through, and the rate rules (lib/form-checks.ts) say what is wrong with either in words.
    const type = field?.kind === "date" ? "date" : kind === "email" ? "email" : "text";
    control = <input aria-label={label} type={type} className={cn(CONTROL, border, type === "date" && "max-w-[240px]")}
      min={type === "date" ? min : undefined} inputMode={field?.kind === "number" ? "decimal" : undefined}
      value={str} placeholder={placeholder} onChange={(e) => set({ [k]: e.target.value })} />;
  }

  return (
    <div className="space-y-1.5">
      <FieldLabel label={label} req={req} />
      {control}
      {field?.kind !== "rows" && (bad || help) && <p className={cn("text-[11.5px]", bad ? "text-danger" : "text-slate-500")}>{bad ? problem ?? NEEDED : help}</p>}
    </div>
  );
}

/** For tests: every server question is placed on a step (or filled beside one). */
export const _layout = { UI, COMPANION, FILE_KEYS };
