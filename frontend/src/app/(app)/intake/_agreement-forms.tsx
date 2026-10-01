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
import { Check, ChevronLeft, Info, Paperclip, Search, TriangleAlert, X } from "lucide-react";
import { Button, Card, CardBody, Input } from "@/components/ui";
import { contractsApi, intakeApi, partiesApi } from "@/lib/endpoints";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/toast";
import { cn } from "@/lib/utils";
import { ATTACHMENT_ACCEPT, ATTACHMENT_LIMITS_TEXT, attachmentProblem, toAttachment } from "@/lib/intake";
import type { Counterparty, IntakeDraft, LegalEntity, RequestFormDef, RequestFormRule } from "@/lib/types";

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
  "purpose", "needed_by", "department"];
const TERM = ["start_date", "term", "end_date", "renewal_term", "notice_days"];

export const AGREEMENT_FORMS: AgreementFormDef[] = [
  { key: "new_agreement", name: "New agreement", group: "New paper",
    desc: "NDA, services, buying, software, consultancy or selling. One form; the questions adapt to the kind of agreement.",
    steps: [
      { title: "Agreement and parties", fields: WHO },
      { title: "Commercial terms", fields: ["nda_kind", "nda_direction", "nda_term", "governing_law", "value", ...TERM,
        "scope", "buying", "hosting", "payment_terms", "nonstandard"] },
      { title: "Risk checks and files", fields: ["personal_data", "gxp", "attachments"] },
    ] },
  { key: "sow", name: "Statement of Work", group: "New paper", desc: "New scope and fees under a signed master agreement.",
    steps: [
      { title: "The master agreement", fields: ["parent_contract_id", "cp_signer_name", "cp_signer_email", "needed_by", "department"] },
      { title: "Scope and fees", fields: ["sow_title", "services_start", "services_end", "value", "pricing", "scope", "deliverables"] },
      { title: "Risk checks and files", fields: ["personal_data", "gxp", "attachments"] },
    ] },
  { key: "dpa", name: "Data Processing Agreement", group: "New paper", desc: "Personal data moves between us and a partner.",
    steps: [
      { title: "The agreement", fields: ["parent_contract_id", "cp_signer_name", "cp_signer_email", "paper", "their_draft", "needed_by"] },
      { title: "The data", fields: ["our_role", "processing_purpose", "data_types", "data_subjects", "transfer", "subprocessors"] },
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
  purpose: { placeholder: "One or two sentences a lawyer can act on.", uses: "The purpose wording in the draft; the Aegis read checks your attachments against it." },
  needed_by: { help: "Leave blank if there is no real deadline.", uses: "Priority: within 7 days is High, otherwise Medium." },
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
  deliverables: { placeholder: "Name the thing, the date and who signs it off.", uses: "Deliverables and acceptance clause." },
  our_role: { uses: "Which side carries which duties in the DPA." },
  processing_purpose: { uses: "Processing description in the DPA annex." },
  data_types: { uses: "Health or special category data flags a DPIA before signature." },
  data_subjects: { uses: "DPA annex." },
  transfer: { uses: "Yes adds transfer clauses and flags it for the Privacy review." },
  subprocessors: { uses: "Sub-processor clause in the DPA." },
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

type Value = string | string[];
type Values = Record<string, Value>;

export function shown(rules: RequestFormRule[] | undefined, values: Values): boolean {
  return (rules ?? []).every((r) => {
    const a = values[r.field];
    return (Array.isArray(a) ? a : [a]).some((x) => typeof x === "string" && r.in.includes(x));
  });
}

export function useRequestForms() {
  return useQuery({ queryKey: ["intake-forms"], queryFn: intakeApi.forms, staleTime: Infinity });
}

/** The "What kind of agreement?" choices a form offers — what "Used for" can narrow to. */
export function agreementTypeOptions(forms: RequestFormDef[] | undefined, formKey: string): string[] {
  return forms?.find((f) => f.key === formKey)?.fields.find((f) => f.key === "agreement_type")?.options ?? [];
}

const blank = (v: Value | undefined) => (Array.isArray(v) ? v.length === 0 : !String(v ?? "").trim());

const DRAFT_OF: Record<string, string> = {
  NDA: "NDA template", "Services (MSA)": "MSA template", Consultancy: "MSA template",
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
  if (key === "new_agreement") return DRAFT_OF[String(v.agreement_type)] ?? "Chosen by the kind of agreement";
  return ({ sow: "MSA template (SoW schedule)", dpa: "DPA template", regularize: "The signed document is the record",
    cancellation: "Nothing is drafted" } as Record<string, string>)[key] ?? "Legal drafts the letter or deed";
}

function priorityOf(v: Values): "High" | "Medium" {
  const due = typeof v.needed_by === "string" && v.needed_by ? Date.parse(v.needed_by) : NaN;
  return Number.isFinite(due) && (due - Date.now()) / 86_400_000 <= 7 ? "High" : "Medium";
}

function money(cur: Value | undefined, n: Value | undefined): string {
  const x = Number(String(n ?? "").replace(/,/g, ""));
  return x ? `${cur || "INR"} ${x.toLocaleString(cur === "INR" || !cur ? "en-IN" : "en-US")}` : "";
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
  async function create() {
    setBusy(true);
    try {
      const made = await partiesApi.createCounterparty({
        name: text.trim(), jurisdiction: form.jurisdiction || null, contact_email: form.contact_email || null,
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
              + Create "{text.trim()}" as a new counterparty
            </button>
          )}
        </div>
      )}
      {creating && (
        <div className="mt-2 space-y-2 rounded-lg border border-brand-200 bg-brand-50 p-3">
          <div className="text-[12.5px] font-medium text-slate-800">New counterparty: {text.trim() || "—"}</div>
          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <Input aria-label="Jurisdiction" placeholder="Jurisdiction (e.g. India)" value={form.jurisdiction}
              onChange={(e) => setForm((f) => ({ ...f, jurisdiction: e.target.value }))} />
            <Input aria-label="Contact email" type="email" placeholder="Contact email (for signature)" value={form.contact_email}
              onChange={(e) => setForm((f) => ({ ...f, contact_email: e.target.value }))} />
          </div>
          <div className="flex gap-2">
            <Button size="sm" loading={busy} disabled={!text.trim()} onClick={create}>Create counterparty</Button>
            <Button size="sm" variant="ghost" onClick={() => setCreating(false)}>Cancel</Button>
          </div>
        </div>
      )}
    </div>
  );
}


// --------------------------------------------------------------- wizard ----

export function AgreementWizard({ def, draft, onFiled, onBack }: {
  def: AgreementFormDef;
  draft?: IntakeDraft | null;
  onFiled: (id: string) => void;
  onBack: () => void;
}) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { user } = useAuth();
  const { data: forms, isLoading: formsLoading } = useRequestForms();
  const server = forms?.find((f) => f.key === def.key);
  const q = useMemo(() => new Map((server?.fields ?? []).map((f) => [f.key, f])), [server]);

  const steps = [...def.steps, { title: REVIEW, fields: [] as string[] }];
  const [step, setStep] = useState(Math.min(draft?.page_index ?? 0, steps.length - 1));
  const [visited, setVisited] = useState(draft?.visited ?? 0);
  const [values, setValues] = useState<Values>(draft?.values ?? { currency: "INR" });
  const [files, setFiles] = useState<Record<string, File | null>>({});
  const [errors, setErrors] = useState<Set<string>>(new Set());
  const [errBar, setErrBar] = useState("");
  const [busy, setBusy] = useState(false);
  const [draftId, setDraftId] = useState<string | null>(draft?.id ?? null);
  const [savedAt, setSavedAt] = useState<string | null>(draft?.updated_at ?? null);
  const [dirty, setDirty] = useState(false);
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
    setErrors((prev) => {
      const next = new Set(prev);
      Object.keys(patch).forEach((k) => next.delete(k));
      return next.size === prev.size ? prev : next;
    });
  };

  const labelOf = (k: string) => UI[k]?.label ?? q.get(k)?.label ?? k;
  const isShown = (k: string) => shown(UI[k]?.show ?? q.get(k)?.show, values);
  const required = (k: string) => (UI[k]?.kind === "file" ? !!UI[k].req : !!q.get(k)?.required);
  const visible = (i: number) => (steps[i]?.fields ?? []).filter((k) => (q.has(k) || FILE_KEYS.includes(k)) && isShown(k));

  function missing(i: number): string[] {
    return visible(i).filter((k) => {
      if (!required(k)) return false;
      if (UI[k]?.kind === "file") return !files[k];
      if ((COMPANION[k] ?? []).some((c) => blank(values[c]))) return true;
      return blank(values[k]);
    });
  }

  // What gets filed. Check-approvers and the routing preview send exactly this.
  const body = useMemo(() => {
    const fv: Values = { ...values, request_form: def.key };
    if (parent) {
      fv.parent_contract_title = parent.title;
      if (!fv.counterparty) fv.counterparty = parent.counterparty_name ?? "";
    }
    const kind = def.key === "new_agreement" && typeof values.agreement_type === "string" ? values.agreement_type : "";
    const text = [values.purpose, values.reason, values.processing_purpose, values.note_approvers]
      .find((x) => typeof x === "string" && x.trim()) as string | undefined;
    return {
      type_label: kind ? `New agreement · ${kind}` : `${def.name} Request`,
      priority: priorityOf(values),
      department: typeof values.department === "string" ? values.department || null : null,
      description: text ?? def.name,
      field_values: fv,
    };
  }, [values, parent, def]);

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
        const miss = missing(i);
        if (miss.length) {
          setStep(i);
          setErrors(new Set(miss));
          setErrBar(`${miss.length === 1 ? "1 answer" : `${miss.length} answers`} still needed: ${miss.map(labelOf).join(", ")}.`);
          return;
        }
      }
    }
    setErrBar(""); setErrors(new Set());
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

  async function saveDraft() {
    setSaving(true);
    try {
      const payload = { form_key: def.key, title: subject(), values, parent_contract_id: parent?.id ?? null,
        page_index: step, visited };
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
      if (missing(i).length) { go(i + 1); return; }
    }
    // The document that IS the contract goes last: the server takes the newest
    // readable attachment as the contract.
    const ordered = ["attachments", "their_draft", "executed"].map((k) => files[k]).filter((f): f is File => !!f);
    const bad = ordered.map(attachmentProblem).find(Boolean);
    if (bad) { setErrBar(bad); return; }
    setBusy(true);
    try {
      const created = await intakeApi.create({
        ...body, subject: subject(), requester_name: user?.full_name ?? null,
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

  const isReview = step === steps.length - 1;
  const shownNow = visible(step);

  const routing: [string, string][] = [
    ["Workflow", def.key === "cancellation" ? "None: the request is withdrawn"
      : preview.data?.workflow?.name ?? (preview.isFetching ? "Working it out…"
        : preview.isSuccess ? "None fits yet: a person picks one after filing" : "Pick the kind of agreement")],
    ["Draft", draftFor(def.key, values)],
    ["Approvals", (preview.data?.approvers ?? []).map((a) => `${a.step_name}: ${a.approver}`).join("\n") || "None on this workflow"],
    ["Priority", priorityOf(values) === "High" ? "High: needed within 7 days" : "Medium"],
    ["Owner", "Assigned when filed, never you"],
  ];
  if (typeof values.cp_signer_email === "string" && values.cp_signer_email) {
    routing.push(["Their signer", `${values.cp_signer_name || ""}\n${values.cp_signer_email}`.trim()]);
  }
  if (FINISH[def.key]) routing.push(["When the workflow finishes", FINISH[def.key]]);

  const summary: [string, string][] = [];
  if (isReview) {
    def.steps.forEach((_, i) => visible(i).forEach((k) => {
      const v = values[k];
      const shown = UI[k]?.kind === "file" ? files[k]?.name
        : UI[k]?.kind === "money" ? money(values.currency, v)
        : k === "parent_contract_id" ? parent?.title
        : Array.isArray(v) ? v.join(", ") : v;
      if (shown) summary.push([labelOf(k), String(shown)]);
    }));
  }

  if (formsLoading || !server) {
    return <div className="py-16 text-center text-[13px] text-slate-500">{formsLoading ? "Loading the form…" : "This form isn't available."}</div>;
  }

  return (
    <div className="mx-auto flex max-w-[1180px] flex-col">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2 pb-4">
        <Button variant="ghost" size="sm" className="-ml-2" onClick={onBack}>
          <ChevronLeft className="h-4 w-4" />All request types
        </Button>
        <span className="h-4 w-px bg-slate-200" />
        <h2 className="text-[17px] font-semibold tracking-[-0.01em] text-slate-900">{def.name}</h2>
        {parent && <span className="text-[12px] text-slate-500">on <b className="font-medium text-slate-700">{parent.title}</b></span>}
        <span className={cn("ml-auto text-[12px]", dirty && savedAt ? "text-warning" : "text-slate-400")}>
          {!savedAt ? "Not saved yet" : dirty ? "Unsaved changes" : `Draft saved ${new Date(savedAt).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}`}
        </span>
      </header>

      <ol className="flex flex-wrap gap-2 pb-4">
        {steps.map((s, i) => {
          const cur = i === step, done = i < step || i <= visited - 1 && i !== step;
          return (
            <li key={s.title}>
              <button type="button" onClick={() => go(i)} aria-current={cur ? "step" : undefined}
                className={cn("flex h-9 items-center gap-2 rounded-full border pl-1.5 pr-3.5 text-[12.5px]",
                  cur ? "border-brand-600 bg-brand-50 font-semibold text-brand-700"
                    : done ? "border-slate-200 bg-slate-100 font-medium text-success" : "border-slate-200 bg-slate-100 text-slate-500")}>
                <span className={cn("grid h-6 w-6 place-items-center rounded-full text-[11px] font-bold",
                  cur ? "bg-brand-600 text-white" : done ? "bg-success text-white" : "bg-slate-200 text-slate-600")}>
                  {done && !cur ? <Check className="h-3 w-3" /> : i + 1}
                </span>
                {s.title}
              </button>
            </li>
          );
        })}
      </ol>

      <div className="grid grid-cols-1 items-start gap-5 lg:grid-cols-[minmax(0,1fr)_340px]">
        <Card>
          <CardBody className="min-h-[420px] space-y-5 px-7 py-7">
            <div>
              <div className="text-[10.5px] font-semibold uppercase tracking-[0.12em] text-brand-600">Step {step + 1} of {steps.length}</div>
              <h3 className="mt-1.5 text-[19px] font-semibold tracking-[-0.015em] text-slate-900">{steps[step].title}</h3>
            </div>
            {errBar && (
              <div className="flex gap-2.5 rounded-lg border border-danger/40 bg-danger-subtle px-3.5 py-3 text-[12.5px] text-danger">
                <TriangleAlert className="mt-px h-4 w-4 shrink-0" /><span>{errBar}</span>
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
                <dl className="grid grid-cols-1 gap-x-8 gap-y-3 md:grid-cols-2">
                  {summary.map(([k, v]) => (
                    <div key={k} className="border-b border-slate-200 pb-2.5">
                      <dt className="text-[11.5px] text-slate-500">{k}</dt>
                      <dd className="mt-0.5 whitespace-pre-line text-[13.5px] font-medium text-slate-900">{v}</dd>
                    </div>
                  ))}
                </dl>
                <label className="block space-y-1.5">
                  <FieldLabel label="Note to approvers (optional)" />
                  <textarea rows={3} className={cn(CONTROL, "h-auto border-slate-300 py-2.5 leading-relaxed")}
                    value={String(values.note_approvers ?? "")} placeholder="Two lines beat a paragraph."
                    onChange={(e) => set({ note_approvers: e.target.value })} />
                </label>
              </>
            ) : (
              <div className="space-y-5">
                {shownNow.map((k) => (
                  <Question key={k} k={k} label={labelOf(k)} req={required(k)} kind={UI[k]?.kind} field={q.get(k)}
                    help={UI[k]?.help} placeholder={UI[k]?.placeholder} values={values} set={set} bad={errors.has(k)}
                    file={files[k] ?? null} onFile={(f) => { setFiles((p) => ({ ...p, [k]: f })); setDirty(true); setErrors((p) => { const n = new Set(p); n.delete(k); return n; }); }}
                    currencies={q.get("currency")?.options ?? ["INR"]}
                    contracts={contracts ?? []} requests={myRequests ?? []} />
                ))}
              </div>
            )}
          </CardBody>
          <div className="flex items-center gap-3 border-t border-slate-200 px-7 py-4">
            {step > 0 && <Button variant="ghost" onClick={() => go(step - 1)}>← Back</Button>}
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
          {!isReview && (
            <div className="rounded-xl border border-slate-200 bg-slate-100 p-4 shadow-card">
              <div className="mb-3 text-[10.5px] font-semibold uppercase tracking-[0.12em] text-slate-500">What these answers do</div>
              <ul className="space-y-3">
                {shownNow.map((k) => (
                  <li key={k} className="flex gap-2.5">
                    <span className={cn("mt-1.5 h-2 w-2 shrink-0 rounded-full",
                      (UI[k]?.kind === "file" ? files[k] : !blank(values[k])) ? "bg-success" : "bg-slate-300")} />
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
        </aside>
      </div>
    </div>
  );
}

function Question({ k, label, req, kind, field, help, placeholder, values, set, bad, file, onFile, currencies, contracts, requests }: {
  k: string; label: string; req: boolean; kind?: UiKind; field?: RequestFormDef["fields"][number];
  help?: string; placeholder?: string; values: Values; set: (p: Values) => void; bad: boolean;
  file: File | null; onFile: (f: File | null) => void; currencies: string[];
  contracts: { id: string; title: string; counterparty_name: string | null; expiration_date: string | null }[];
  requests: { id: string; ref: string; subject: string | null; type_label: string; status: string }[];
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
  const opts = (field?.options ?? []).map((o) => ({ value: o, label: o }));

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
      label: [c.title, c.counterparty_name, c.expiration_date ? `ends ${c.expiration_date}` : null].filter(Boolean).join(" · ") })),
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
  } else if (field?.kind === "multiselect") {
    const picked = Array.isArray(v) ? v : [];
    control = (
      <div className={cn("flex flex-wrap gap-x-5 gap-y-2 rounded-lg border px-3 py-2.5", border)}>
        {opts.map((o) => (
          <label key={o.value} className="inline-flex items-center gap-2 text-[13px] text-slate-800">
            <input type="checkbox" checked={picked.includes(o.value)}
              onChange={(e) => set({ [k]: e.target.checked ? [...picked, o.value] : picked.filter((x) => x !== o.value) })} />
            {o.label}
          </label>
        ))}
      </div>
    );
  } else if (field?.kind === "select") {
    control = select(str, opts, (x) => set({ [k]: x }));
  } else if (field?.kind === "textarea") {
    control = <textarea aria-label={label} rows={3} className={cn(CONTROL, border, "h-auto py-2.5 leading-relaxed")}
      value={str} placeholder={placeholder} onChange={(e) => set({ [k]: e.target.value })} />;
  } else {
    const type = field?.kind === "date" ? "date" : kind === "email" ? "email" : field?.kind === "number" ? "number" : "text";
    control = <input aria-label={label} type={type} className={cn(CONTROL, border, type === "date" && "max-w-[240px]")}
      value={str} placeholder={placeholder} onChange={(e) => set({ [k]: e.target.value })} />;
  }

  return (
    <div className="space-y-1.5">
      <FieldLabel label={label} req={req} />
      {control}
      {(bad || help) && <p className={cn("text-[11.5px]", bad ? "text-danger" : "text-slate-500")}>{bad ? "Needed before you continue." : help}</p>}
    </div>
  );
}

/** For tests: every server question is placed on a step (or filled beside one). */
export const _layout = { UI, COMPANION, FILE_KEYS };
