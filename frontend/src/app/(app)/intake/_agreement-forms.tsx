"use client";

/**
 * The eight-step agreement intake forms, ported from the old CLM so migrating
 * users meet the same steps in the same order. One config-driven wizard covers
 * all nine request types; only steps 1 and 5 differ between them.
 *
 * ponytail: field specs live in this file rather than the DB. They are stable
 * legal taxonomy, not tenant config — move them to IntakeRequestType.fields if
 * a second org ever needs different ones.
 */

import { useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, ChevronLeft, FileText, Info, Paperclip, Search, TriangleAlert } from "lucide-react";
import {
  Badge, Button, Card, CardBody, CardHeader, CardTitle, Field, Input, Select, Textarea,
} from "@/components/ui";
import { contractsApi, intakeApi } from "@/lib/endpoints";
import { useAuth } from "@/lib/auth";
import { useToast } from "@/components/toast";
import { cn } from "@/lib/utils";
import type { ContractResponse, IntakeRequest } from "@/lib/types";

// ---------------------------------------------------------------- types ----

type FieldKind = "text" | "date" | "money" | "select" | "textarea";

interface FieldSpec {
  k: string;
  label: string;
  kind?: FieldKind;
  options?: string[];
  req?: boolean;
  help?: string;
  wide?: boolean;
}

type Values = Record<string, string>;

export interface AgreementFormDef {
  key: string;
  name: string;
  desc: string;
  group: "New paper" | "Change an existing agreement" | "Records & corrections";
  /** What step 1 does: pick a contract, pick an in-flight request, upload an executed document, or name our entity. */
  parent: "contract" | "none" | "upload" | "request";
  /** Fields shown under step 1 — the values read off an uploaded document. */
  parentFields?: FieldSpec[];
  /** Extra fields on step 2, where a type needs more than the inherited parties. */
  partyFields?: FieldSpec[];
  parentTitle: string;
  parentSub: string;
  detailTitle: string;
  detailSub: string;
  detail: FieldSpec[];
  /** Extra approvers appended whatever the value tier. */
  extraApprover?: string;
  /** Nothing is signed by this request type. */
  noSignature?: boolean;
  guide: Record<number, [string, string][]>;
}

// ------------------------------------------------------------- catalogue ----

/**
 * Step 4 as the old CLM asks it — the left column locates the requester, the
 * right column classifies the agreement, and the four driver fields (function,
 * business unit, agreement type, monetary value) decide the approval ladder.
 */
const CLASSIFY: FieldSpec[] = [
  { k: "region", label: "Region", kind: "select", options: ["India", "EMEA", "North America", "APAC"], req: true },
  { k: "department", label: "Department", kind: "select", options: ["Enterprise Systems", "Procurement", "Commercial", "R&D", "Human Resources", "Finance", "Quality"], req: true },
  { k: "country", label: "Country", kind: "select", options: ["Not applicable", "India", "United Kingdom", "United States", "Germany", "Singapore"] },
  { k: "agreement_type", label: "Agreement type", kind: "select", req: true,
    options: ["Confidentiality agreement — India", "Consultancy agreement — India", "Master Services Agreement", "Review / drafting by legal counsel",
              "Service agreement — advertising, media, facilities", "Vendor / supplier agreement", "Customer / sales agreement", "SaaS or software licence", "Others — India"] },
  { k: "area", label: "Area", kind: "select", options: ["Not applicable", "North", "South", "East", "West"] },
  { k: "agreement_category", label: "Agreement category", kind: "select", req: true,
    options: ["Master Service Agreement", "Services — inbound", "Services — outbound", "Technology", "Facilities", "Marketing"] },
  { k: "business_unit", label: "Business unit", kind: "select", options: ["Corporate", "Global Generics", "Pharmaceutical Services", "Biologics"], req: true },
  { k: "agreement_sub_category", label: "Agreement sub-category", kind: "select", options: ["Not applicable", "Professional services", "Managed services", "Licence", "Maintenance"] },
  { k: "plant", label: "Plant", kind: "select", options: ["Not applicable", "Hyderabad — Unit 1", "Hyderabad — Unit 2", "Bengaluru — R&D", "Vizag — Unit 6"] },
  { k: "value", label: "Total monetary value of agreement", kind: "money", req: true, help: "Over the full term — this sets the approval tier under the delegation of authority." },
];

/**
 * Requests that hang off an existing contract inherit most of the above from
 * the parent, so they ask the short set and re-state only the value.
 */
const CLASSIFY_CHILD: FieldSpec[] = [
  { k: "region", label: "Region", kind: "select", options: ["India", "EMEA", "North America", "APAC"], req: true },
  { k: "department", label: "Department", kind: "select", options: ["Enterprise Systems", "Procurement", "Commercial", "R&D", "Human Resources", "Finance", "Quality"], req: true },
  { k: "business_unit", label: "Business unit", kind: "select", options: ["Corporate", "Global Generics", "Pharmaceutical Services", "Biologics"], req: true },
  { k: "agreement_category", label: "Agreement category", kind: "select", req: true,
    options: ["Master Service Agreement", "Services — inbound", "Services — outbound", "Technology", "Facilities", "Marketing"] },
  { k: "value", label: "Total monetary value of agreement", kind: "money", req: true, help: "Sets the approval tier under the delegation of authority." },
];

function classifyFor(def: AgreementFormDef): FieldSpec[] {
  return def.parent === "contract" ? CLASSIFY_CHILD : CLASSIFY;
}

const COMMON_GUIDE: Record<number, [string, string][]> = {
  2: [["Where this comes from", "Entity and counterparty details are read from the record you picked. Correct them at source rather than here."]],
  3: [["Digital signature", "Sent through DocuSign to the counterparty email registered on their record, once approvals clear."],
      ["Offline ink", "Download after approval, obtain a physical signature, then upload the executed copy back into the CLM."]],
  4: [["How approvers are decided", "Approvers and signatories follow the delegation of authority approved by the board — function, business unit, agreement type and monetary value."]],
  7: [["Write a good summary", "A clear summary helps approvers and signatories make quick, informed decisions. Two lines beat a paragraph."]],
  8: [["What to attach", "Third-party papers, supporting documents, budget approvals — anything a reviewer would otherwise have to ask you for. Upload DOC/DOCX where collaborative review is needed."]],
};

export const AGREEMENT_FORMS: AgreementFormDef[] = [
  {
    key: "new_agreement",
    name: "New agreement",
    desc: "A contract that does not exist yet — NDA, MSA, vendor or customer paper.",
    group: "New paper",
    parent: "none",
    parentTitle: "Select the legal entity that will be a party to this agreement",
    parentSub: "The contracting entity decides the approval ladder and who is authorised to sign. If you are unsure which entity applies, check with Legal or Finance.",
    detailTitle: "Agreement request detail",
    detailSub: "Terms specific to this agreement. Anything left blank comes back as a question from your reviewer.",
    detail: [
      { k: "effective_date", label: "Agreement effective date", kind: "date", req: true },
      { k: "end_date", label: "Agreement end date", kind: "date", req: true },
      { k: "existing_contract", label: "Any existing contract", kind: "select", options: ["No", "Yes"], req: true },
      { k: "vendor_code", label: "Vendor code", kind: "text" },
      { k: "origin", label: "Origin of agreement", kind: "select", options: ["Our template", "Counterparty paper", "From a precedent"] },
      { k: "auto_renewal", label: "Auto-renewal", kind: "select", options: ["No", "Annual", "Evergreen"] },
      { k: "purpose", label: "Other information / comments", kind: "textarea", wide: true },
    ],
    guide: {
      1: [["Why we ask", "If you are unaware of the correct legal entity, reach out to Legal or Finance — the entity decides both the approval ladder and who may sign."],
          ["Multiple entities", "More than one of our entities can be party to the same agreement; each adds its own approvers."]],
      5: [["Fill what you know", "Blanks come back as questions from your reviewer, which costs a round trip."]],
    },
  },
  {
    key: "sow",
    name: "Statement of Work",
    desc: "Scope, deliverables and fees under a master agreement that is already signed.",
    group: "New paper",
    parent: "contract",
    parentTitle: "Select the master agreement this SoW sits under",
    parentSub: "A Statement of Work draws liability, IP, confidentiality and payment terms from a signed master. Only scope, deliverables and fees are set here.",
    detailTitle: "Statement of Work detail",
    detailSub: "Scope, deliverables, timeline and fees — the only terms negotiated at SoW level.",
    detail: [
      { k: "sow_title", label: "SoW title", kind: "text", req: true, wide: true },
      { k: "services_start", label: "Services start", kind: "date", req: true },
      { k: "services_end", label: "Services end", kind: "date", req: true },
      { k: "pricing_model", label: "Pricing model", kind: "select", req: true, options: ["Fixed fee", "Time and materials", "Milestone-based", "Retainer"] },
      { k: "rates", label: "Rates", kind: "select", req: true, options: ["Master rate card", "Rates specific to this SoW"], help: "SoW-specific rates vary the master rate card and draw extra Finance review." },
      { k: "scope", label: "Scope of services", kind: "textarea", req: true, wide: true },
      { k: "deliverables", label: "Deliverables and acceptance", kind: "textarea", req: true, wide: true },
      { k: "personnel", label: "Key personnel / resources", kind: "text", wide: true },
    ],
    guide: {
      1: [["No master, no SoW", "A Statement of Work cannot stand alone. If there is no signed master with this counterparty, raise a New agreement request first."]],
      5: [["Deliverables matter most", "Vague deliverables are the biggest cause of payment disputes. Name the thing, the date and who accepts it."]],
    },
  },
  {
    key: "dpa",
    name: "Data Processing Agreement",
    desc: "Personal data is shared with, or processed by, a third party.",
    group: "New paper",
    parent: "contract",
    parentTitle: "What arrangement causes personal data to be processed?",
    parentSub: "A DPA usually attaches to a services or licence agreement. Linking it keeps the two renewing and terminating together.",
    detailTitle: "Processing detail",
    detailSub: "What data, whose data, why, for how long, and where it goes. These answers decide whether a DPIA is required.",
    detail: [
      { k: "purpose_of_processing", label: "Purpose of processing", kind: "textarea", req: true, wide: true },
      { k: "data_categories", label: "Categories of personal data", kind: "select", req: true, options: ["Contact and identity data", "Employment and HR data", "Financial data", "Customer and usage data", "Mixed — several categories"] },
      { k: "data_subjects", label: "Data subjects", kind: "select", req: true, options: ["Employees", "Customers", "Suppliers and contractors", "Patients or trial subjects", "Mixed"] },
      { k: "special_category", label: "Special category data involved?", kind: "select", req: true, options: ["No", "Yes"], help: "Health, biometric, genetic, religious, political or trade-union data." },
      { k: "scale", label: "Scale of processing", kind: "select", req: true, options: ["Limited — under 10,000 data subjects", "Moderate — 10,000 to 100,000", "Large scale — over 100,000 data subjects"] },
      { k: "monitoring", label: "Systematic monitoring or profiling?", kind: "select", req: true, options: ["No", "Yes"] },
      { k: "location", label: "Where is data processed?", kind: "select", req: true, options: ["India only", "India and EEA", "United States", "Multiple regions"] },
      { k: "retention", label: "Retention period", kind: "select", req: true, options: ["Duration of the contract only", "Contract plus 1 year", "Contract plus 3 years", "Contract plus 7 years — statutory"] },
      { k: "sub_processors", label: "Sub-processors used?", kind: "select", req: true, options: ["None", "Named list attached", "To be approved case by case"] },
      { k: "breach_window", label: "Breach notification window", kind: "select", req: true, options: ["24 hours", "48 hours", "72 hours — regulatory minimum"] },
    ],
    extraApprover: "Privacy officer",
    partyFields: [
      { k: "their_role", label: "Counterparty's role", kind: "select", req: true,
        options: ["Processor — they process on our instructions", "Sub-processor", "Joint controller"],
        help: "If they decide how and why the data is used, this is a data sharing agreement instead." },
      { k: "our_role", label: "Our role", kind: "select", req: true, options: ["Controller", "Processor", "Joint controller"] },
      { k: "privacy_contact", label: "Their data protection contact", kind: "text", wide: true },
    ],
    guide: {
      1: [["Link it to the contract", "A linked DPA renews and terminates with its agreement. Standalone DPAs must be tracked separately."]],
      5: [["Special category data", "Health, biometric, genetic, religious or trade-union data triggers a mandatory DPIA and privacy sign-off before signature."],
          ["Transfers", "Processing outside India needs standard contractual clauses and a transfer impact assessment."]],
    },
  },
  {
    key: "amendment",
    name: "Amendment",
    desc: "Change the terms of a live agreement — scope, pricing, dates.",
    group: "Change an existing agreement",
    parent: "contract",
    parentTitle: "Select the agreement you want to amend",
    parentSub: "Parties, business unit and existing terms carry across from the contract, so you only enter what changes.",
    detailTitle: "Amendment detail",
    detailSub: "Anything left as no change carries forward from the original agreement untouched.",
    detail: [
      { k: "what_changes", label: "What is changing", kind: "select", req: true, options: ["Commercial terms", "Scope of services", "Term / dates", "Parties or entity details", "Other"] },
      { k: "effective_date", label: "Amendment effective date", kind: "date", req: true },
      { k: "revised_end_date", label: "Revised end date", kind: "date", help: "Leave blank if the term is unchanged." },
      { k: "reason", label: "Reason for the amendment", kind: "textarea", req: true, wide: true },
    ],
    guide: {
      1: [["Cannot find it?", "Agreements signed outside the CLM must be regularized first, then amended."]],
      5: [["Value changes", "Enter the revised total for the agreement, not the delta — the approval tier is calculated on the new total."]],
    },
  },
  {
    key: "renewal",
    name: "Renewal",
    desc: "Extend an agreement approaching expiry.",
    group: "Change an existing agreement",
    parent: "contract",
    parentTitle: "Select the agreement you want to renew",
    parentSub: "Renewing keeps the contract history, the counterparty record and any unchanged terms.",
    detailTitle: "Renewal detail",
    detailSub: "Set the new term and any commercial changes. Dates only means a straight extension, which moves faster.",
    detail: [
      { k: "renewal_type", label: "Type of renewal", kind: "select", req: true, options: ["Straight extension — same terms", "Renew with commercial changes", "Renew with scope changes"] },
      { k: "new_start", label: "New term starts", kind: "date", req: true },
      { k: "new_end", label: "New term ends", kind: "date", req: true },
      { k: "auto_renewal", label: "Auto-renewal after this term", kind: "select", options: ["No", "Annual", "Evergreen"] },
      { k: "reason", label: "Reason for renewing", kind: "textarea", req: true, wide: true },
    ],
    guide: {
      1: [["Raise it early", "Give legal at least 60 days before expiry. Renewals raised inside the notice window may miss the deadline."]],
      5: [["Price changes", "A rise above 10% is flagged to Finance regardless of the value tier."]],
    },
  },
  {
    key: "termination",
    name: "Termination",
    desc: "End a live agreement — for convenience, at expiry, or for breach.",
    group: "Change an existing agreement",
    parent: "contract",
    parentTitle: "Select the agreement you want to terminate",
    parentSub: "Aegis reads the notice period from the agreement and checks your proposed date against it.",
    detailTitle: "Termination detail",
    detailSub: "The grounds decide the notice you must give and who has to approve.",
    detail: [
      { k: "grounds", label: "Grounds for termination", kind: "select", req: true, options: ["For convenience", "For breach", "By mutual agreement", "Non-renewal at expiry"] },
      { k: "termination_date", label: "Proposed termination date", kind: "date", req: true },
      { k: "clause_breached", label: "Clause breached", kind: "text", help: "Required where the grounds are breach — e.g. Clause 9.2, service credits." },
      { k: "cure_period", label: "Cure period served", kind: "select", options: ["Not applicable", "Yes", "No"], help: "Breach usually requires a cure period to have run before notice is served." },
      { k: "reason", label: "Reason", kind: "textarea", req: true, wide: true },
    ],
    extraApprover: "General Counsel",
    guide: {
      1: [["This is not cancellation", "Termination ends a signed agreement. To withdraw a request still in flight, use Cancel a request in the CLM."]],
      5: [["Notice period", "The earliest lawful termination date is calculated from the notice period in the agreement. A shorter date needs the counterparty's written agreement."]],
    },
  },
  {
    key: "novation",
    name: "Novation",
    desc: "Transfer an agreement to a different legal entity.",
    group: "Change an existing agreement",
    parent: "contract",
    parentTitle: "Select the agreement you want to novate",
    parentSub: "Novation transfers a contract to a different legal entity. All three parties must sign the same deed.",
    detailTitle: "Novation detail",
    detailSub: "Who is leaving, who is taking over, and what moves with them.",
    detail: [
      { k: "reason", label: "Reason for the novation", kind: "select", req: true, options: ["Counterparty acquired or merged", "Internal group restructuring", "Supplier transferring the contract", "Business or asset sale"] },
      { k: "effective_date", label: "Novation effective date", kind: "date", req: true },
      { k: "liabilities", label: "Accrued liabilities", kind: "select", req: true, options: ["Transfer to the incoming party", "Stay with the outgoing party", "Split at the effective date"] },
      { k: "consent", label: "Has the outgoing party agreed?", kind: "select", req: true, options: ["Yes — in writing", "Yes — verbally, written to follow", "Not yet"] },
      { k: "background", label: "Background", kind: "textarea", req: true, wide: true },
    ],
    extraApprover: "General Counsel",
    partyFields: [
      { k: "transferring_side", label: "Which side is transferring?", kind: "select", req: true,
        options: ["The counterparty is transferring out", "We are transferring out"], help: "Decides who signs as outgoing party and who stays." },
      { k: "outgoing_party", label: "Outgoing party", kind: "text", req: true, wide: true },
      { k: "incoming_party", label: "Incoming party — the entity taking over", kind: "text", req: true, wide: true,
        help: "New to us? Sanctions and conflicts screening runs automatically and must clear before signature." },
      { k: "remaining_party", label: "Remaining party", kind: "text", wide: true },
    ],
    guide: {
      1: [["Novation, not assignment", "Novation replaces a party with their consent and transfers obligations as well as rights."]],
      5: [["Liabilities", "Decide whether accrued liabilities stay with the outgoing party or transfer — the clause most often argued over."]],
    },
  },
  {
    key: "regularize",
    name: "Regularize an agreement executed outside the CLM",
    desc: "Already signed off-system — bring it onto the register.",
    group: "Records & corrections",
    parent: "upload",
    parentTitle: "Upload the executed agreement",
    parentSub: "This brings paper signed outside the CLM onto the register, so it is tracked, its obligations are managed and it appears in reporting.",
    detailTitle: "How did this come to be signed outside the CLM?",
    detailSub: "Legal needs the circumstances to decide what remediation is required and whether a policy exception must be logged.",
    parentFields: [
      { k: "counterparty", label: "Counterparty", kind: "text", req: true, wide: true, help: "Read from the signature block — confirm it matches the counterparty register." },
      { k: "agreement_type", label: "Agreement type", kind: "select", req: true,
        options: ["Service agreement", "Master Services Agreement", "Consultancy agreement", "NDA / confidentiality", "Vendor / supplier agreement", "Others — India"] },
      { k: "date_signed", label: "Date signed", kind: "date", req: true },
      { k: "effective_date", label: "Effective date", kind: "date", req: true },
      { k: "end_date", label: "End date", kind: "date", req: true },
      { k: "value", label: "Total value", kind: "money", req: true },
    ],
    partyFields: [
      { k: "entity", label: "Our entity", kind: "text", req: true, wide: true },
      { k: "signed_by_us", label: "Who signed for us?", kind: "text", req: true, wide: true, help: "Name and role — checked against the delegation of authority at step 5." },
      { k: "signed_by_them", label: "Who signed for the counterparty?", kind: "text", wide: true },
    ],
    detail: [
      { k: "why_outside", label: "Why was it signed outside the CLM?", kind: "select", req: true,
        options: ["Urgency — no time to route it", "Counterparty insisted on their process", "The team was unaware of the CLM requirement", "Signed before the CLM was introduced", "Low value — assumed not required"] },
      { k: "prior_approval", label: "Were the usual approvals obtained at the time?", kind: "select", req: true, options: ["No approvals were sought", "Approved informally by email", "Fully approved outside the system"] },
      { k: "signer_authority", label: "Did the signer have authority under the delegation?", kind: "select", req: true, options: ["Yes", "No", "Not sure"] },
      { k: "circumstances", label: "Circumstances", kind: "textarea", req: true, wide: true },
    ],
    extraApprover: "Compliance officer",
    noSignature: true,
    guide: {
      1: [["Executed copy required", "Upload the fully signed version, not a draft — every signature page included."],
          ["Why this matters", "Contracts outside the CLM are invisible to renewal alerts, obligation tracking and reporting."]],
      5: [["Be straightforward", "The circumstances decide the remediation, not a penalty. Under-reporting simply produces the wrong remediation."]],
    },
  },
  {
    key: "cancellation",
    name: "Cancel a request in the CLM",
    desc: "Withdraw a request you raised that is still in flight.",
    group: "Records & corrections",
    parent: "request",
    parentTitle: "Select the request you want to cancel",
    parentSub: "Cancellation withdraws a request that has not completed. Anything already signed must be terminated instead.",
    detailTitle: "Why is the request being cancelled?",
    detailSub: "The reason is recorded against the request and decides who is told.",
    detail: [
      { k: "reason", label: "Reason for cancelling", kind: "select", req: true,
        options: ["No longer needed", "Duplicate of another request", "Superseded by a different request", "Business change — project stopped", "Raised in error", "Counterparty withdrew"] },
      { k: "superseded_by", label: "Superseded by", kind: "text" },
      { k: "notify_counterparty", label: "Notify the counterparty?", kind: "select", req: true, options: ["No — they were never contacted", "Yes — they have the paper already"] },
      { k: "note", label: "Anything the approvers should know", kind: "textarea", req: true, wide: true },
    ],
    noSignature: true,
    guide: {
      1: [["Cancellation, not termination", "This withdraws a request still in flight. To end a signed contract, raise a Termination request."]],
      5: [["Nothing is deleted", "The request, its approvals and its correspondence stay on the audit record, marked cancelled."]],
    },
  },
];

/** ponytail: the entity register has no endpoint yet — swap for one when it exists. */
const ENTITIES = [
  { name: "Acme Laboratories Limited", address: "8-2-337, Road No. 3, Banjara Hills, Hyderabad 500034", signatory: "Erez Israeli", jurisdiction: "India" },
  { name: "Acme Pharma UK Ltd", address: "5th Floor, 20 St Andrew Street, London EC4A 3AG", signatory: "Sarah Whitfield", jurisdiction: "England & Wales" },
  { name: "Acme Life Sciences Inc", address: "107 College Road East, Princeton, NJ 08540", signatory: "Michael Reyes", jurisdiction: "Delaware, USA" },
];

// ------------------------------------------------------------- approvals ----

const TIERS = [
  { max: 1_000_000, name: "Tier 1", people: ["Legal approver", "Business approver"], eta: "1.8 days" },
  { max: 5_000_000, name: "Tier 2", people: ["Legal approver", "Finance approver", "Business approver"], eta: "3.2 days" },
  { max: 50_000_000, name: "Tier 3", people: ["Legal approver", "Finance approver", "Business approver", "CFO"], eta: "6.5 days" },
  { max: Infinity, name: "Tier 4", people: ["Legal approver", "Finance approver", "Business approver", "CFO", "Board committee"], eta: "14 days" },
];
function tierFor(value: number) {
  return TIERS.find((t) => value <= t.max) ?? TIERS[TIERS.length - 1];
}
function inr(n: number): string {
  return Number.isFinite(n) ? `INR ${n.toLocaleString("en-IN")}` : "—";
}
function daysBetween(a: string, b: string): number {
  return Math.round((new Date(b).getTime() - new Date(a).getTime()) / 86_400_000);
}

/** Per-type warnings computed from what the requester has entered so far. */
function checksFor(def: AgreementFormDef, v: Values, parent: ContractResponse | null): { tone: "warn" | "danger" | "info"; text: string }[] {
  const out: { tone: "warn" | "danger" | "info"; text: string }[] = [];
  const value = Number(v.value || 0);

  if (def.key === "sow" && parent?.expiration_date && v.services_end && v.services_end > parent.expiration_date) {
    out.push({ tone: "danger", text: `This SoW runs past its master agreement, which ends ${parent.expiration_date}. A SoW cannot outlive the master it depends on.` });
  }
  if (def.key === "sow" && v.rates === "Rates specific to this SoW") {
    out.push({ tone: "warn", text: "SoW-specific rates vary the master rate card — Finance reviews the delta and legal checks the variation is permitted." });
  }
  if (def.key === "dpa") {
    const dpia = v.special_category === "Yes" || v.monitoring === "Yes" || v.scale?.startsWith("Large scale");
    if (dpia) out.push({ tone: "danger", text: "A DPIA is required before signature. It is opened as a linked task when this request is approved." });
    if (v.location && v.location !== "India only") out.push({ tone: "warn", text: "Processing outside India needs standard contractual clauses and a transfer impact assessment." });
    if (v.sub_processors === "To be approved case by case") out.push({ tone: "warn", text: "An open sub-processor clause means someone must action every request — name them up front where you can." });
  }
  if (def.key === "renewal" && parent?.value_amount && value) {
    const pct = Math.round(((value - parent.value_amount) / parent.value_amount) * 100);
    if (pct > 10) out.push({ tone: "warn", text: `Price increase of ${pct}% — anything above 10% is flagged to Finance regardless of the approval tier.` });
  }
  if (def.key === "termination" && v.termination_date) {
    const notice = daysBetween(new Date().toISOString().slice(0, 10), v.termination_date);
    if (notice < 90) out.push({ tone: "danger", text: `Your date gives ${notice} days' notice. Check the notice clause — short notice needs the counterparty's written agreement.` });
    if (v.grounds === "For breach" && !v.clause_breached?.trim()) out.push({ tone: "warn", text: "Termination for breach needs the specific clause breached, and usually a cure period already served." });
  }
  if (def.key === "novation" && v.consent === "Not yet") {
    out.push({ tone: "danger", text: "Without the outgoing party's written agreement there is no novation. Legal will not draft the deed until consent is confirmed." });
  }
  if (def.key === "regularize") {
    if (v.date_signed) {
      const age = daysBetween(v.date_signed, new Date().toISOString().slice(0, 10));
      if (age > 90) out.push({ tone: "warn", text: `Signed ${age} days ago — anything over 90 days is reported to the compliance committee as an ageing exception.` });
    }
    if (v.prior_approval === "No approvals were sought") out.push({ tone: "danger", text: "No approvals were obtained. The approvers who would have reviewed this do so retrospectively." });
    if (v.signer_authority === "No") out.push({ tone: "danger", text: "Signed without authority — escalated to the General Counsel; the contract may need ratification." });
  }
  if (def.key === "new_agreement" && v.auto_renewal && v.auto_renewal !== "No") {
    out.push({ tone: "warn", text: `Auto-renewal set to ${v.auto_renewal} — an obligation and a notice reminder are created on signature.` });
  }
  return out;
}

// ------------------------------------------------------------------- UI ----

function stepLabels(def: AgreementFormDef): string[] {
  const first = def.parent === "contract" ? "Contract" : def.parent === "upload" ? "Document" : "Entity";
  const second = def.parent === "contract" || def.parent === "upload" ? "Parties" : "Counterparty";
  return [first, second, "Signature", "Business unit", "Request detail", "Summary", "Message", "Attachments"];
}

function Stepper({ labels, current, complete, onGo }: { labels: string[]; current: number; complete: boolean[]; onGo: (n: number) => void }) {
  // `complete` is read inside the map for the connector to the left.
  return (
    <div className="flex items-start overflow-x-auto pb-3 pt-1">
      {labels.map((label, i) => {
        const n = i + 1;
        const done = complete[i] && n !== current;
        return (
          <button
            key={label}
            type="button"
            onClick={() => onGo(n)}
            aria-current={n === current ? "step" : undefined}
            className="relative flex min-w-[92px] flex-1 flex-col items-center gap-1.5"
          >
            {i > 0 && <span className={cn("absolute left-0 right-1/2 top-[13px] h-0.5", complete[i - 1] ? "bg-success" : "bg-slate-200")} />}
            {i < labels.length - 1 && <span className={cn("absolute left-1/2 right-0 top-[13px] h-0.5", done ? "bg-success" : "bg-slate-200")} />}
            <span
              className={cn(
                "relative z-10 grid h-[26px] w-[26px] place-items-center rounded-full border-[1.5px] text-[11px] font-semibold transition-colors",
                n === current
                  ? "border-brand-600 bg-brand-600 text-white ring-4 ring-brand-500/20"
                  : done
                    ? "border-transparent bg-success text-white"
                    : "border-slate-300 bg-slate-100 text-slate-500",
              )}
            >
              {done ? <Check className="h-3 w-3" /> : n}
            </span>
            <span className={cn("whitespace-nowrap px-1.5 text-center text-[11.5px]", n === current ? "font-semibold text-brand-600" : "text-slate-500")}>
              {label}
            </span>
          </button>
        );
      })}
    </div>
  );
}

function Note({ tone = "info", children }: { tone?: "info" | "warn" | "danger"; children: React.ReactNode }) {
  const Icon = tone === "info" ? Info : TriangleAlert;
  return (
    <div
      className={cn(
        "mt-4 flex gap-2.5 rounded-lg border p-3 text-[12px]",
        tone === "info" && "border-slate-200 bg-slate-50 text-slate-600",
        tone === "warn" && "border-warning/40 bg-warning-subtle text-warning",
        tone === "danger" && "border-danger/40 bg-danger-subtle text-danger",
      )}
    >
      <Icon className="mt-px h-4 w-4 shrink-0" />
      <div>{children}</div>
    </div>
  );
}

function FieldLabel({ label, req }: { label: string; req?: boolean }) {
  return (
    <span className="block text-[12.5px] font-medium text-slate-800">
      {label} :{req && <span className="ml-0.5 text-danger">*</span>}
    </span>
  );
}

/** Text field with the Look-up control attached to its right edge. */
function LookupInput({ value, onChange, placeholder = "Type something", bad, list }: {
  value: string; onChange: (v: string) => void; placeholder?: string; bad?: boolean; list?: string;
}) {
  return (
    <div className={cn(
      "flex h-10 overflow-hidden rounded-lg border bg-slate-100 focus-within:border-brand-600 focus-within:ring-2 focus-within:ring-brand-500/35",
      bad ? "border-danger bg-danger-subtle" : "border-slate-300",
    )}>
      <input
        list={list}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="min-w-0 flex-1 bg-transparent px-3 text-[13px] text-slate-900 placeholder:text-slate-400 focus:outline-none"
      />
      <span className="flex shrink-0 items-center gap-1.5 border-l border-slate-300 bg-slate-50 px-3 text-[12px] font-medium text-slate-600">
        Look-up <Search className="h-3.5 w-3.5" />
      </span>
    </div>
  );
}

function FieldGrid({ specs, values, errors, onChange }: {
  specs: FieldSpec[]; values: Values; errors: Set<string>; onChange: (k: string, v: string) => void;
}) {
  return (
    <div className="grid grid-cols-1 gap-x-8 gap-y-5 md:grid-cols-2">
      {specs.map((f) => {
        const bad = errors.has(f.k);
        const v = values[f.k] ?? "";
        const control = cn(
          "h-10 w-full rounded-lg border bg-slate-100 px-3 text-[13px] text-slate-900 placeholder:text-slate-400",
          "focus:border-brand-600 focus:outline-none focus:ring-2 focus:ring-brand-500/35",
          bad ? "border-danger bg-danger-subtle" : "border-slate-300",
        );
        return (
          <div key={f.k} className={cn("min-w-0 space-y-1.5", f.wide && "md:col-span-2")}>
            <FieldLabel label={f.label} req={f.req} />
            {f.kind === "select" ? (
              <select className={control} value={v} onChange={(e) => onChange(f.k, e.target.value)}>
                <option value="">{`Select ${f.label.replace(/ \(.*\)/, "")}`}</option>
                {(f.options ?? []).map((o) => <option key={o}>{o}</option>)}
              </select>
            ) : f.kind === "textarea" ? (
              <textarea
                rows={3}
                className={cn(control, "h-auto py-2.5 leading-relaxed")}
                value={v}
                onChange={(e) => onChange(f.k, e.target.value)}
                placeholder="Enter Value"
              />
            ) : f.kind === "money" ? (
              <div className={cn("flex h-10 overflow-hidden rounded-lg border bg-slate-100 focus-within:border-brand-600 focus-within:ring-2 focus-within:ring-brand-500/35",
                bad ? "border-danger bg-danger-subtle" : "border-slate-300")}>
                <span className="grid shrink-0 place-items-center border-r border-slate-300 bg-slate-50 px-3 text-[12px] text-slate-500">INR</span>
                <input
                  inputMode="numeric"
                  value={v}
                  onChange={(e) => onChange(f.k, e.target.value)}
                  placeholder="Enter Value"
                  className="min-w-0 flex-1 bg-transparent px-3 text-[13px] text-slate-900 placeholder:text-slate-400 focus:outline-none"
                />
              </div>
            ) : (
              <input
                type={f.kind === "date" ? "date" : "text"}
                className={control}
                value={v}
                onChange={(e) => onChange(f.k, e.target.value)}
                placeholder={f.kind === "date" ? undefined : "Enter Value"}
              />
            )}
            {(bad || f.help) && (
              <p className={cn("text-[11.5px]", bad ? "text-danger" : "text-slate-500")}>
                {bad ? "Required before you continue." : f.help}
              </p>
            )}
          </div>
        );
      })}
    </div>
  );
}

// --------------------------------------------------------------- wizard ----

export function AgreementWizard({ def, onFiled, onBack }: {
  def: AgreementFormDef;
  onFiled: (id: string) => void;
  onBack: () => void;
}) {
  const qc = useQueryClient();
  const { notify } = useToast();
  const { user } = useAuth();

  // One step per page — the requester answers one question at a time.
  const PAGES: number[][] = [[1], [2], [3], [4], [5], [6], [7], [8]];
  const [pageIndex, setPageIndex] = useState(0);
  const [values, setValues] = useState<Values>({});
  const [errors, setErrors] = useState<Set<string>>(new Set());
  const [parentId, setParentId] = useState<string>("");
  const [file, setFile] = useState<File | null>(null);
  const [ladderOpen, setLadderOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const [errBar, setErrBar] = useState<string>("");
  const [visited, setVisited] = useState(1);

  const LABELS = useMemo(() => stepLabels(def), [def]);
  const needsContract = def.parent === "contract";
  const { data: contracts } = useQuery({
    queryKey: ["contracts"],
    queryFn: () => contractsApi.list(),
    enabled: needsContract,
  });
  const { data: myRequests } = useQuery({
    queryKey: ["intake-mine"],
    queryFn: () => intakeApi.mine(),
    enabled: def.parent === "request",
  });
  const parent = useMemo(
    () => (contracts ?? []).find((c) => c.id === parentId) ?? null,
    [contracts, parentId],
  );

  const set = (k: string, v: string) => {
    setValues((prev) => ({ ...prev, [k]: v }));
    setErrors((prev) => {
      if (!prev.has(k)) return prev;
      const next = new Set(prev);
      next.delete(k);
      return next;
    });
  };

  const value = Number(values.value || parent?.value_amount || 0);
  const tier = tierFor(value);
  const approvers = def.extraApprover ? [...tier.people, def.extraApprover] : tier.people;
  const checks = checksFor(def, values, parent);

  /** Required keys for a given step, skipping steps that ask nothing. */
  function requiredFor(n: number): FieldSpec[] {
    if (n === 4) return classifyFor(def).filter((f) => f.req);
    if (n === 5) return def.detail.filter((f) => f.req);
    if (n === 1 && def.parentFields) return def.parentFields.filter((f) => f.req);
    if (n === 2 && def.parent === "none") return [{ k: "counterparty", label: "Counterparty", req: true }, ...(def.partyFields ?? []).filter((f) => f.req)];
    if (n === 2) return (def.partyFields ?? []).filter((f) => f.req);
    if (n === 7) return [{ k: "note_approvers", label: "Note to approvers", req: true }];
    return [];
  }
  function validate(n: number): boolean {
    if (n === 1 && needsContract && !parentId) {
      setErrBar("Pick the agreement this request relates to — everything else is read from it.");
      return false;
    }
    if (n === 1 && def.parent === "none" && !values.entity?.trim()) {
      setErrBar("Choose the legal entity that will be a party to this agreement.");
      return false;
    }
    if (n === 1 && def.parent === "request" && !values.cancel_request_ref) {
      setErrBar("Pick the request you want to cancel.");
      return false;
    }
    if (n === 1 && def.parent === "upload" && !file) {
      setErrBar("Upload the executed agreement before continuing — everything else is read from it.");
      return false;
    }
    const missing = requiredFor(n).filter((f) => !String(values[f.k] ?? "").trim());
    if (missing.length) {
      setErrors(new Set(missing.map((f) => f.k)));
      setErrBar(`${missing.length} field${missing.length > 1 ? "s" : ""} still needed on this step — ${missing.map((f) => f.label).join(", ")}.`);
      return false;
    }
    setErrBar("");
    return true;
  }
  // A step counts as done only once the requester has actually passed through
  // it. Without the `visited` guard every step with no required fields (2, 3,
  // 6, 8) showed a green tick on a blank form.
  const complete = LABELS.map((_, i) => {
    const n = i + 1;
    if (n > visited) return false;
    if (n === 1) {
      if (needsContract) return !!parentId;
      if (def.parent === "upload") return !!file && (def.parentFields ?? []).every((f) => !f.req || !!values[f.k]?.trim());
      if (def.parent === "request") return !!values.cancel_request_ref;
      return !!values.entity?.trim();
    }
    return requiredFor(n).every((f) => String(values[f.k] ?? "").trim());
  });

  const pageSteps = PAGES[pageIndex];

  function validatePage(): boolean {
    return pageSteps.every((n) => validate(n));
  }

  function goPage(next: number) {
    const target = Math.min(PAGES.length - 1, Math.max(0, next));
    if (target > pageIndex && !validatePage()) return;
    setErrBar("");
    setVisited((v) => Math.max(v, Math.max(...PAGES[target])));
    setPageIndex(target);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  /** The stepper and the summary's Edit links address steps, not pages. */
  function goToStep(n: number) {
    const target = PAGES.findIndex((page) => page.includes(n));
    if (target < 0) return;
    if (target > pageIndex && !validatePage()) return;
    setErrBar("");
    setVisited((v) => Math.max(v, Math.max(...PAGES[target])));
    setPageIndex(target);
    window.scrollTo({ top: 0, behavior: "smooth" });
  }

  async function submit() {
    if (!validatePage()) return;
    setBusy(true);
    try {
      const payload: Record<string, string> = { ...values, request_form: def.key };
      if (parent) {
        payload.parent_contract_id = parent.id;
        payload.parent_contract_title = parent.title;
        payload.counterparty = parent.counterparty_name ?? "";
      }
      const created = await intakeApi.create({
        type_label: `${def.name} Request`,
        subject: subjectLine(def, values, parent),
        priority: value > 5_000_000 ? "High" : "Medium",
        department: values.department || null,
        requester_name: user?.full_name ?? null,
        description: values.note_approvers || values.reason || values.purpose || def.name,
        field_values: payload,
      });
      if (file) {
        const b64 = await fileToB64(file);
        await intakeApi.uploadDocument(created.id, {
          filename: file.name,
          mime_type: file.type || "application/octet-stream",
          content_b64: b64,
        });
        // Only treat the attachment AS the contract where that is what it is:
        // an already-executed agreement, or the counterparty's own paper. A
        // budget approval or a screenshot is supporting material, not the deal.
        const isTheContract = def.key === "regularize" || values.origin === "Counterparty paper";
        if (isTheContract) {
          try {
            await intakeApi.ingestAttachment(created.id);
          } catch {
            // The request and the file are already saved — a failed extraction
            // (scanned PDF, image-only) must not lose the requester's work.
            notify(`${created.ref} filed. The attachment could not be read automatically — legal will open it manually.`, "info");
          }
        }
      }
      qc.invalidateQueries({ queryKey: ["intake-mine"] });
      qc.invalidateQueries({ queryKey: ["intake-list"] });
      notify(`Filed ${created.ref} — routed for triage`, "success");
      onFiled(created.id);
    } catch (e) {
      notify(e instanceof Error ? e.message : "Submit failed", "error");
    } finally {
      setBusy(false);
    }
  }

  const guide = (def.guide[pageSteps[0]] ?? COMMON_GUIDE[pageSteps[0]] ?? []).slice(0, 2);

  return (
    <div className="mx-auto flex max-w-[1180px] flex-col">
      <header className="flex flex-wrap items-center gap-x-3 gap-y-2 pb-4">
        <Button variant="ghost" size="sm" className="-ml-2" onClick={onBack}>
          <ChevronLeft className="h-4 w-4" />All request types
        </Button>
        <span className="h-4 w-px bg-slate-200" />
        <h2 className="text-[17px] font-semibold tracking-[-0.01em] text-slate-900">{def.name} request form</h2>
        <Badge tone="violet">Draft</Badge>
        {parent && (
          <span className="text-[12px] text-slate-500">
            on <b className="font-medium text-slate-700">{parent.title}</b>
          </span>
        )}
        <span className="ml-auto text-[12px] text-slate-400">Saved automatically</span>
      </header>

      <Stepper labels={LABELS} current={pageSteps[pageSteps.length - 1]} complete={complete} onGo={goToStep} />

      <div className="grid grid-cols-1 items-start gap-5 pt-4 lg:grid-cols-[minmax(0,1fr)_300px]">
        <Card>
          <CardBody className="min-h-[430px] px-7 py-7">
            {errBar && (
              <div className="flex gap-2.5 rounded-lg border border-danger/40 bg-danger-subtle px-3.5 py-3 text-[12.3px] text-danger">
                <TriangleAlert className="mt-px h-4 w-4 shrink-0" />
                <span>{errBar}</span>
              </div>
            )}

            {pageSteps.map((n, idx) => (
              <section key={n}>
                <div className="mb-6">
                  <div className="text-[10.5px] font-semibold uppercase tracking-[0.12em] text-brand-600">
                    Step {n} of 8
                  </div>
                  <h3 className="mt-1.5 text-[19px] font-semibold leading-snug tracking-[-0.015em] text-slate-900">
                    {stepTitle(def, n)}
                  </h3>
                  <p className="mt-1.5 max-w-[76ch] text-[13px] leading-relaxed text-slate-600">{stepSub(def, n)}</p>
                </div>

                {n === 1 && (
                  <StepParent
                    def={def}
                    contracts={contracts ?? []}
                    parentId={parentId}
                    onPick={setParentId}
                    file={file}
                    onFile={setFile}
                    values={values}
                    errors={errors}
                    onChange={set}
                    requests={myRequests ?? []}
                  />
                )}

                {n === 2 && (
                  <>
                    <StepParties def={def} parent={parent} values={values} errors={errors} onChange={set} />
                    {def.partyFields && (
                      <div className="mt-5">
                        <FieldGrid specs={def.partyFields} values={values} errors={errors} onChange={set} />
                      </div>
                    )}
                  </>
                )}

                {n === 3 && <StepSignature def={def} values={values} onChange={set} />}

                {n === 4 && (
                  <>
                    <FieldGrid specs={classifyFor(def)} values={values} errors={errors} onChange={set} />
                    <EnvelopePanel def={def} values={values} parent={parent} />
                    <div className="mt-6 flex justify-center gap-3">
                      <Button variant="outline" className="rounded-full border-brand-200 px-6 text-brand-700 hover:bg-brand-50" onClick={() => { setValues({}); setLadderOpen(false); }}>reset</Button>
                      <Button className="rounded-full px-6" onClick={() => { if (validatePage()) setLadderOpen(true); }}>Check Approvers</Button>
                    </div>
                    {ladderOpen && (
                      <div className="mt-6">
                        <div className="text-[12.5px] text-slate-600">
                          Agreement flow: <b className="font-semibold text-slate-900">Delegated</b>
                        </div>
                        <div className="mt-1 text-[12.5px] text-slate-600">
                          Legal owner / reviewer: <b className="font-semibold text-slate-900">Bhavya Murgai</b>
                        </div>
                        <div className="mt-3 overflow-hidden rounded-xl border border-slate-200 bg-slate-50">
                          <div className="border-b border-slate-200 px-5 py-4">
                            <div className="mb-3 text-[12px] font-semibold text-brand-700">Approvers</div>
                            <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3 lg:grid-cols-5">
                              {approvers.map((p, i) => (
                                <div key={p}>
                                  <div className="text-[12.5px] font-semibold text-slate-900">{p}</div>
                                  <div className="text-[11.5px] text-slate-500">Approver {i + 1}</div>
                                </div>
                              ))}
                            </div>
                          </div>
                          <div className="px-5 py-4">
                            <div className="mb-3 text-[12px] font-semibold text-brand-700">Signatories</div>
                            <div className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-3">
                              <div>
                                <div className="text-[12.5px] font-semibold text-slate-900">Counterparty signatory</div>
                                <div className="text-[11.5px] text-slate-500">From the counterparty record</div>
                              </div>
                              <div>
                                <div className="text-[12.5px] font-semibold text-slate-900">Our signatory</div>
                                <div className="text-[11.5px] text-slate-500">Authorised at {tier.name}</div>
                              </div>
                            </div>
                          </div>
                        </div>
                        <p className="mt-2 text-[11.5px] text-slate-500">
                          Approvers and the signatory are determined by the delegation of authority approved by the board.
                          In case of doubt, reach out to your legal counsel.
                        </p>
                      </div>
                    )}
                  </>
                )}

                {n === 5 && (
                  <>
                    <FieldGrid specs={def.detail} values={values} errors={errors} onChange={set} />
                    {checks.map((c, i) => <Note key={i} tone={c.tone}>{c.text}</Note>)}
                    <DetailPanels def={def} values={values} parent={parent} />
                  </>
                )}

                {n === 6 && <StepSummary def={def} values={values} parent={parent} approvers={approvers} tier={tier} onGo={goToStep} />}

                {n === 7 && <StepMessage def={def} values={values} parent={parent} errors={errors} onChange={set} approvers={approvers} />}

                {n === 8 && <StepFiles def={def} file={file} onFile={setFile} values={values} onChange={set} />}
              </section>
            ))}
          </CardBody>

          <div className="flex items-center gap-3 border-t border-slate-200 px-7 py-4">
            {pageIndex > 0 && <Button variant="ghost" onClick={() => goPage(pageIndex - 1)}>← Back</Button>}
            <span className="text-[12px] text-slate-500">
              <b className="font-semibold text-slate-900">{complete.filter(Boolean).length} of 8</b> steps complete
            </span>
            <div className="ml-auto flex gap-2">
              <Button variant="outline" className="rounded-full px-5" onClick={onBack}>Save as Draft</Button>
              {pageIndex === PAGES.length - 1
                ? <Button loading={busy} className="rounded-full px-6" onClick={submit}>Submit</Button>
                : <Button className="rounded-full px-6" onClick={() => goPage(pageIndex + 1)}>Proceed</Button>}
            </div>
          </div>
        </Card>

        <aside className="flex flex-col gap-3 lg:sticky lg:top-5">
          <div className="text-[10.5px] font-semibold uppercase tracking-[0.12em] text-slate-400">
            Guidance
          </div>
          {guide.map(([head, body]) => (
            <div key={head} className="rounded-xl border border-slate-200 bg-slate-100 p-4 shadow-card">
              <div className="flex items-start gap-2.5">
                <Info className="mt-px h-4 w-4 shrink-0 text-brand-600" />
                <div>
                  <div className="text-[12.5px] font-semibold text-slate-900">{head}</div>
                  <p className="mt-1 text-[12.2px] leading-relaxed text-slate-600">{body}</p>
                </div>
              </div>
            </div>
          ))}
        </aside>
      </div>
    </div>
  );
}

// --------------------------------------------------------------- steps ----

function stepTitle(def: AgreementFormDef, step: number): string {
  switch (step) {
    case 1: return def.parentTitle;
    case 2: return def.parent === "contract" ? "Confirm the parties" : "Select the counterparty";
    case 3: return def.noSignature ? "Signature — not applicable" : "Select the signature method";
    case 4: return "Identify the business unit requesting the agreement";
    case 5: return def.detailTitle;
    case 6: return "Request summary";
    case 7: return "Write a brief description for approvers & signatories";
    default: return "Upload attachments";
  }
}
function stepSub(def: AgreementFormDef, step: number): string {
  switch (step) {
    case 1: return def.parentSub;
    case 2: return def.parent === "contract"
      ? "These come from the agreement you picked. If any is wrong, either the parent is wrong or the record needs correcting first."
      : "Search the counterparty register. Signature routing uses the email held against the record.";
    case 3: return def.noSignature
      ? "Kept in the flow so every request type has the same eight steps — nothing is signed by this request."
      : "You can change this at any point until the request reaches the signature stage.";
    case 4: return "Function, business unit, agreement type and monetary value determine the approvers and signatories under the delegation of authority.";
    case 5: return def.detailSub;
    case 6: return "Check it before it goes out. Edit any section from here — nothing is submitted until step 8.";
    case 7: return "This is the first thing your approvers read.";
    default: return "Attach third-party papers, supporting documents and approvals. Upload DOC or DOCX where collaborative review is required.";
  }
}

function StepParent({ def, contracts, requests, parentId, onPick, file, onFile, values, errors, onChange }: {
  def: AgreementFormDef; contracts: ContractResponse[]; requests: IntakeRequest[]; parentId: string;
  onPick: (id: string) => void; file: File | null; onFile: (f: File | null) => void;
  values: Values; errors: Set<string>; onChange: (k: string, v: string) => void;
}) {
  const [search, setSearch] = useState("");
  const [extraEntity, setExtraEntity] = useState(false);
  if (def.parent === "upload") {
    return (
      <>
        <label className="block cursor-pointer rounded-xl border border-dashed border-slate-300 bg-slate-50 p-8 text-center hover:border-brand-600 hover:bg-brand-50">
          <input type="file" className="sr-only" onChange={(e) => onFile(e.target.files?.[0] ?? null)} />
          <Paperclip className="mx-auto mb-2 h-5 w-5 text-slate-500" />
          <div className="text-[13px] font-medium text-slate-900">
            {file ? file.name : "Drop the executed agreement here, or browse"}
          </div>
          <div className="mt-1 text-[11.5px] text-slate-500">Signed PDF or DOCX · Aegis reads the parties, dates and value from it</div>
        </label>
        {file && def.parentFields && (
          <div className="mt-5">
            <div className="mb-3 text-[11px] font-semibold uppercase tracking-[0.05em] text-slate-500">
              Read from the document — confirm each value
            </div>
            <FieldGrid specs={def.parentFields} values={values} errors={errors} onChange={onChange} />
          </div>
        )}
        <Note>Upload the <b>fully executed</b> copy — every signature page included. A draft cannot be regularized.</Note>
      </>
    );
  }

  if (def.parent === "request") {
    return (
      <>
        <div className="flex flex-col gap-2">
          {requests.length === 0 && (
            <p className="text-[13px] text-slate-500">You have no requests in flight to cancel.</p>
          )}
          {requests.map((r) => {
            const signing = r.stage === "signature" || r.status === "approved";
            return (
              <button
                key={r.id}
                type="button"
                disabled={signing}
                onClick={() => onChange("cancel_request_ref", r.ref)}
                className={cn(
                  "flex items-start gap-3 rounded-xl border p-4 text-left transition-colors",
                  signing
                    ? "cursor-not-allowed border-slate-200 bg-slate-50 opacity-70"
                    : values.cancel_request_ref === r.ref
                      ? "border-brand-600 bg-brand-50"
                      : "border-slate-300 bg-slate-100 hover:border-brand-600",
                )}
              >
                <span className={cn("mt-1 h-4 w-4 shrink-0 rounded-full border-[1.5px]",
                  values.cancel_request_ref === r.ref ? "border-brand-600 bg-brand-600 ring-[3px] ring-inset ring-slate-100" : "border-slate-400")} />
                <span className="min-w-0">
                  <span className="block text-[13.5px] font-semibold text-slate-900">{r.ref} — {r.type_label}</span>
                  <span className="mt-1 flex flex-wrap gap-3 text-[11.8px] text-slate-500">
                    <span>{r.subject ?? "No subject"}</span>
                    <span>Stage: {r.stage}</span>
                    <span>Status: {r.status}</span>
                  </span>
                </span>
                {signing && <Badge tone="red" className="ml-auto shrink-0">Cannot cancel</Badge>}
              </button>
            );
          })}
        </div>
        <Note>
          A request that has reached signature cannot be cancelled — one party has already committed, so the agreement
          must complete and then be terminated.
        </Note>
      </>
    );
  }
  if (def.parent === "none") {
    const chosen = ENTITIES.find((e) => e.name.toLowerCase() === (values.entity ?? "").trim().toLowerCase()) ?? null;
    return (
      <>
        <div className="flex flex-col gap-4">
          <div className="max-w-[520px] space-y-1.5">
            <FieldLabel label="Entity1" req />
            <LookupInput
              list="aegis-entity-register"
              value={values.entity ?? ""}
              onChange={(v) => onChange("entity", v)}
              bad={errors.has("entity")}
            />
          </div>
          <datalist id="aegis-entity-register">
            {ENTITIES.map((e) => <option key={e.name} value={e.name} />)}
          </datalist>

          {extraEntity && (
            <div className="max-w-[520px] space-y-1.5">
              <FieldLabel label="Entity2" />
              <LookupInput list="aegis-entity-register" value={values.entity_2 ?? ""} onChange={(v) => onChange("entity_2", v)} />
            </div>
          )}
        </div>

        {chosen && (
          <div className="mt-4 overflow-hidden rounded-xl border border-slate-200">
            <div className="border-b border-slate-200 bg-slate-50 px-4 py-2.5 text-[11px] font-semibold uppercase tracking-[0.05em] text-slate-500">
              From the entity record
            </div>
            {([["Registered address", chosen.address], ["Authorised signatory", chosen.signatory], ["Jurisdiction", chosen.jurisdiction]] as [string, string][]).map(([k, v]) => (
              <div key={k} className="grid grid-cols-[190px_minmax(0,1fr)] gap-3 border-b border-slate-200 px-4 py-2.5 text-[12.8px] last:border-b-0">
                <span className="text-slate-500">{k}</span>
                <span className="font-medium text-slate-900">{v}</span>
              </div>
            ))}
          </div>
        )}

        {!extraEntity && (
          <Button variant="outline" size="sm" className="mt-4 rounded-full border-brand-200 text-brand-700 hover:bg-brand-50" onClick={() => setExtraEntity(true)}>+ Add Entity</Button>
        )}
        <Note>
          You can add multiple entities for both our side and the counterparty. If you are unsure, it is best to consult your legal counsel.
        </Note>
      </>
    );
  }

  const live = contracts
    .filter((c) => !c.archived)
    .filter((c) => {
      const q = search.trim().toLowerCase();
      if (!q) return true;
      return `${c.title} ${c.counterparty_name ?? ""} ${c.contract_type ?? ""}`.toLowerCase().includes(q);
    });
  return (
    <>
      <div className="mb-3">
        <Input
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="Search by counterparty, title or reference…"
        />
      </div>
      <div className="flex flex-col gap-2">
        {live.length === 0 && <p className="text-[13px] text-slate-500">No contracts you can access. Signed outside the CLM? Regularize it first.</p>}
        {live.map((c) => (
          <button
            key={c.id}
            type="button"
            onClick={() => onPick(c.id)}
            className={cn(
              "flex items-start gap-3 rounded-xl border p-4 text-left transition-colors",
              parentId === c.id ? "border-brand-600 bg-brand-50" : "border-slate-300 bg-slate-100 hover:border-brand-600",
            )}
          >
            <span className={cn("mt-1 h-4 w-4 shrink-0 rounded-full border-[1.5px]", parentId === c.id ? "border-brand-600 bg-brand-600 ring-[3px] ring-inset ring-slate-100" : "border-slate-400")} />
            <span className="min-w-0">
              <span className="block text-[13.5px] font-semibold text-slate-900">
                {c.counterparty_name ?? "—"} — {c.title}
              </span>
              <span className="mt-1 flex flex-wrap gap-3 text-[11.8px] text-slate-500">
                <span>{c.contract_type ?? "Contract"}</span>
                <span>{c.effective_date ?? "—"} → {c.expiration_date ?? "—"}</span>
                <span>{c.value_amount ? inr(c.value_amount) : "No value recorded"}</span>
              </span>
            </span>
            {c.renewal_due && <Badge tone="amber" className="ml-auto shrink-0">Renewal due</Badge>}
          </button>
        ))}
      </div>
      <Note>Only contracts you have access to are listed. Signed outside the CLM? <b>Regularize</b> it first, then raise this request.</Note>
    </>
  );
}

function StepParties({ def, parent, values, errors, onChange }: {
  def: AgreementFormDef; parent: ContractResponse | null; values: Values; errors: Set<string>; onChange: (k: string, v: string) => void;
}) {
  const [second, setSecond] = useState(false);
  if (def.parent === "contract" && parent) {
    const rows: [string, string][] = [
      ["Parent agreement", parent.title],
      ["Counterparty", parent.counterparty_name ?? "—"],
      ["Current term", `${parent.effective_date ?? "—"} → ${parent.expiration_date ?? "—"}`],
      ["Current value", parent.value_amount ? inr(parent.value_amount) : "—"],
      ["Lifecycle stage", parent.lifecycle_stage],
    ];
    return (
      <>
        <div className="overflow-hidden rounded-xl border border-slate-200">
          <div className="border-b border-slate-200 bg-slate-50 px-4 py-2.5 text-[11.5px] text-slate-600">
            Carried across from the agreement you picked — change it at step 1 if this is the wrong one.
          </div>
          {rows.map(([k, v]) => (
            <div key={k} className="grid grid-cols-[190px_minmax(0,1fr)] gap-3 border-b border-slate-200 px-4 py-2.5 text-[12.8px] last:border-b-0">
              <span className="text-slate-500">{k}</span>
              <span className="font-medium text-slate-900">{v}</span>
            </div>
          ))}
        </div>
        {def.key === "novation" && (
          <Note tone="warn">The incoming entity is new to us — sanctions and conflicts screening runs automatically and must clear before signature.</Note>
        )}
      </>
    );
  }
  return (
    <>
      <div className="flex flex-col gap-4">
        <div className="max-w-[520px] space-y-1.5">
          <FieldLabel label="Name of Counterparty1" req />
          <LookupInput value={values.counterparty ?? ""} onChange={(v) => onChange("counterparty", v)} bad={errors.has("counterparty")} />
        </div>
        {second && (
          <div className="max-w-[520px] space-y-1.5">
            <FieldLabel label="Name of Counterparty2" />
            <LookupInput value={values.counterparty_2 ?? ""} onChange={(v) => onChange("counterparty_2", v)} />
          </div>
        )}
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-3">
        {!second && <Button variant="outline" size="sm" className="rounded-full border-brand-200 text-brand-700 hover:bg-brand-50" onClick={() => setSecond(true)}>+ Add Entity</Button>}
        <button type="button" className="border-b border-brand-200 text-[12.5px] font-medium text-brand-700">
          Create counterparty
        </button>
      </div>
      <Note>
        If your counterparty does not exist in the CLM system, or needs to be updated or otherwise corrected, request it
        using the link above before you proceed.
      </Note>
    </>
  );
}

function StepSignature({ def, values, onChange }: { def: AgreementFormDef; values: Values; onChange: (k: string, v: string) => void }) {
  if (def.noSignature) {
    return (
      <>
        <Note>
          <b>Nothing is signed by this request.</b> The step stays in the flow so every request type has the same eight steps.
          {def.key === "regularize" ? " Record how the agreement was executed so the register is accurate." : " The parties are notified when it is submitted."}
        </Note>
        {def.key === "regularize" && (
          <div className="mt-4">
            <FieldGrid
              specs={[
                { k: "how_signed", label: "How was it signed?", kind: "select", req: true, options: ["Wet ink — physical copies", "Emailed PDF signature", "DocuSign outside the CLM", "Other e-signature tool"] },
                { k: "original_location", label: "Where is the original held?", kind: "text", wide: true, help: "Wet-ink originals must be traceable for audit." },
              ]}
              values={values}
              errors={new Set()}
              onChange={onChange}
            />
          </div>
        )}
      </>
    );
  }
  const chosen = values.signature_method || "Digital signature";
  const options = [
    { key: "Digital signature", desc: "Sent through DocuSign to the counterparty email registered on their record, automatically, once every approval clears. The executed copy files itself." },
    { key: "Offline ink signature", desc: "Download the approved copy, obtain a physical signature, then upload the executed version back into the CLM." },
  ];
  return (
    <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
      {options.map((o) => (
        <button
          key={o.key}
          type="button"
          onClick={() => onChange("signature_method", o.key)}
          className={cn(
            "flex gap-3 rounded-xl border p-4 text-left transition-colors",
            chosen === o.key ? "border-brand-600 bg-brand-50" : "border-slate-300 bg-slate-100 hover:border-slate-400",
          )}
        >
          <span className={cn("mt-0.5 h-4 w-4 shrink-0 rounded-full border-[1.5px]", chosen === o.key ? "border-brand-600 bg-brand-600 ring-[3px] ring-inset ring-slate-100" : "border-slate-400")} />
          <span>
            <span className="block text-[13.5px] font-semibold text-slate-900">{o.key}</span>
            <span className="mt-1 block text-[12px] leading-relaxed text-slate-500">{o.desc}</span>
          </span>
        </button>
      ))}
    </div>
  );
}

function StepSummary({ def, values, parent, approvers, tier, onGo }: {
  def: AgreementFormDef; values: Values; parent: ContractResponse | null;
  approvers: string[]; tier: { name: string; eta: string }; onGo: (n: number) => void;
}) {
  const cells: [string, string, number][] = [
    ...(parent ? [["Parent agreement", parent.title, 1] as [string, string, number]] : []),
    ["Counterparty", parent?.counterparty_name ?? values.counterparty ?? "—", 2],
    ["Signature", def.noSignature ? "Not applicable" : values.signature_method || "Digital signature", 3],
    ...classifyFor(def).map((f) => [f.label, values[f.k] || "Not applicable", 4] as [string, string, number]),
    ...def.detail.map((f) => [f.label, values[f.k] || "—", 5] as [string, string, number]),
  ];
  return (
    <div className="flex flex-col gap-5">
      <div>
        <div className="mb-3 text-[11px] font-medium uppercase tracking-[0.05em] text-slate-500">
          Effect of this {def.name.toLowerCase()}
        </div>
        <DiffTable rows={diffRows(def, values, parent)} />
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {cells.map(([k, v, jump]) => (
          <button key={k} type="button" onClick={() => onGo(jump)} className="rounded-lg p-1 text-left hover:bg-slate-50">
            <div className="text-[11px] uppercase tracking-[0.04em] text-slate-500">{k}</div>
            <div className="mt-0.5 break-words text-[13px] font-medium text-slate-900">{v}</div>
          </button>
        ))}
      </div>
      <div className="rounded-xl border border-slate-200 p-4">
        <div className="mb-3 text-[11px] font-medium uppercase tracking-[0.05em] text-slate-500">Routing</div>
        <div className="flex flex-wrap items-center gap-2 text-[13px] text-slate-900">
          <Badge tone="blue">{tier.name}</Badge>
          <span>{approvers.join(" → ")}</span>
          <span className="text-slate-500">· median {tier.eta}</span>
        </div>
      </div>
    </div>
  );
}

function StepMessage({ def, values, parent, errors, onChange, approvers }: {
  def: AgreementFormDef; values: Values; parent: ContractResponse | null;
  errors: Set<string>; onChange: (k: string, v: string) => void; approvers: string[];
}) {
  const note = values.note_approvers ?? "";
  return (
    <div className="grid grid-cols-1 gap-5 lg:grid-cols-2">
      <div className="flex flex-col gap-4">
        <Field label="Approvers *" hint={approvers.join(", ")}>
          <Textarea
            rows={5}
            maxLength={500}
            value={note}
            onChange={(e) => onChange("note_approvers", e.target.value)}
            placeholder="What is this for, why now, and anything non-standard about the terms."
            className={errors.has("note_approvers") ? "border-danger bg-danger-subtle" : undefined}
          />
        </Field>
        <div className="-mt-3 flex flex-wrap items-center gap-2">
          {["No deviations from our template.", "Budget approved under the current plan.", "Needed before the current term lapses."].map((phrase) => (
            <button
              key={phrase}
              type="button"
              onClick={() => onChange("note_approvers", `${note.trim()} ${phrase}`.trim().slice(0, 500))}
              className="rounded-full border border-dashed border-brand-200 bg-brand-50 px-2.5 py-1 text-[11px] font-medium text-brand-700 hover:bg-brand-100"
            >
              + {phrase.split(" ").slice(0, 3).join(" ").toLowerCase().replace(/[.,]$/, "")}
            </button>
          ))}
          <span className="ml-auto text-[11px] text-slate-500">{note.length} / 500</span>
        </div>
        <Field label="Signatories">
          <Textarea rows={3} maxLength={500} value={values.note_signatories ?? ""} onChange={(e) => onChange("note_signatories", e.target.value)} placeholder="One line for whoever signs." />
        </Field>
      </div>
      <div>
        <div className="mb-1.5 block text-xs font-medium text-slate-700">Preview — what the approver receives</div>
        <div className="rounded-xl border border-slate-200 bg-slate-50 p-4 text-[12.3px] leading-relaxed text-slate-600">
          <p className="mb-1.5">Dear approver,</p>
          <p>Requesting you to approve this {def.name.toLowerCase()}. The summary details are as follows:</p>
          <ol className="my-2 list-decimal pl-5">
            <li>Counterparty: <b className="font-medium text-slate-900">{parent?.counterparty_name ?? values.counterparty ?? "—"}</b></li>
            <li>Type of agreement: <b className="font-medium text-slate-900">{values.agreement_type ?? def.name}</b></li>
            <li>Agreement value: <b className="font-medium text-slate-900">{values.value ? inr(Number(values.value)) : "—"}</b></li>
          </ol>
          <p>Summary of the agreement: <b className="font-medium text-slate-900">{note || "—"}</b></p>
        </div>
      </div>
    </div>
  );
}

function StepFiles({ def, file, onFile, values, onChange }: {
  def: AgreementFormDef; file: File | null; onFile: (f: File | null) => void;
  values: Values; onChange: (k: string, v: string) => void;
}) {
  return (
    <>
      {def.parent === "contract" && (
        <div className="mb-4 overflow-hidden rounded-xl border border-slate-200">
          <div className="grid grid-cols-[minmax(0,1fr)_auto] gap-3 border-b border-slate-200 bg-slate-50 px-4 py-2.5 text-[11px] font-semibold uppercase tracking-[0.05em] text-slate-500">
            <span>Attached automatically</span>
            <span>Status</span>
          </div>
          <div className="grid grid-cols-[minmax(0,1fr)_auto] items-center gap-3 px-4 py-2.5 text-[12.5px]">
            <span className="font-medium text-slate-900">The executed parent agreement</span>
            <Badge tone="green">From the register</Badge>
          </div>
        </div>
      )}
      <label className="block cursor-pointer rounded-xl border border-dashed border-slate-300 bg-slate-50 p-7 text-center hover:border-brand-600 hover:bg-brand-50">
        <input type="file" className="sr-only" onChange={(e) => onFile(e.target.files?.[0] ?? null)} />
        <FileText className="mx-auto mb-2 h-5 w-5 text-slate-500" />
        <div className="text-[13px] font-medium text-slate-900">{file ? file.name : "Drag and drop, or browse files"}</div>
        <div className="mt-1 text-[11.5px] text-slate-500">PDF, DOC, DOCX, XLSX, PNG · up to 25 MB each</div>
      </label>
      <div className="mt-4">
        <Field label="File description">
          <Textarea rows={2} value={values.file_description ?? ""} onChange={(e) => onChange("file_description", e.target.value)} placeholder="What did you attach, and what should the reviewer look at?" />
        </Field>
      </div>
      <Note>
        {def.key === "regularize"
          ? "Attach anything showing what was agreed at the time — email approvals, the purchase order, the quote it was signed against."
          : "Upload a DOC or DOCX file if collaborative review is required — PDFs can only be commented on."}
      </Note>
    </>
  );
}


// ------------------------------------------------------- computed panels ----

const TODAY = () => new Date().toISOString().slice(0, 10);

function Stat({ k, v, tone }: { k: string; v: string; tone?: "ok" | "bad" }) {
  return (
    <div>
      <div className="text-[10.5px] uppercase tracking-[0.05em] text-slate-500">{k}</div>
      <div className={cn("mt-0.5 text-[14px] font-semibold",
        tone === "bad" ? "text-danger" : tone === "ok" ? "text-success" : "text-slate-900")}>{v}</div>
    </div>
  );
}

function Calc({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mt-4 rounded-xl border border-info/40 bg-info-subtle p-4">
      <div className="mb-2.5 text-[12.5px] font-semibold text-info">{title}</div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">{children}</div>
    </div>
  );
}

function ItemList({ title, items }: { title: string; items: [string, string, boolean?][] }) {
  return (
    <div className="mt-4 overflow-hidden rounded-xl border border-slate-200">
      <div className="border-b border-slate-200 bg-slate-50 px-4 py-2.5 text-[11px] font-semibold uppercase tracking-[0.05em] text-slate-500">
        {title}
      </div>
      {items.map(([head, sub, active]) => (
        <div key={head} className="flex gap-2.5 border-b border-slate-200 px-4 py-2.5 text-[12.5px] last:border-b-0">
          <span className={cn("mt-1.5 h-1.5 w-1.5 shrink-0 rounded-full", active === false ? "bg-slate-300" : "bg-warning")} />
          <span>
            <span className="font-medium text-slate-900">{head}</span>
            <br />
            <span className="text-[11.5px] text-slate-500">{sub}</span>
          </span>
        </div>
      ))}
    </div>
  );
}

/** The type-specific calculators and lists that sit under the step-5 fields. */
function DetailPanels({ def, values, parent }: { def: AgreementFormDef; values: Values; parent: ContractResponse | null }) {
  const today = TODAY();

  if (def.key === "termination" && values.termination_date) {
    const given = daysBetween(today, values.termination_date);
    const required = 90; // ponytail: read from the contract's notice clause once it is extracted
    return (
      <>
        <Calc title="Notice calculation">
          <Stat k="Contract notice" v={`${required} days`} />
          <Stat k="Earliest lawful date" v={addDays(today, required)} />
          <Stat k="Your date" v={values.termination_date} />
          <Stat k="Notice given" v={`${given} days`} tone={given < required ? "bad" : "ok"} />
        </Calc>
        <ItemList
          title="Survives termination — stays tracked in Aegis"
          items={[
            ["Confidentiality", "Continues after termination per the confidentiality clause"],
            ["Data return and deletion", "Within 30 days of the termination date"],
            ["Final invoicing", "Services delivered up to the termination date remain payable"],
            ["Audit rights", "Continue for the period stated in the agreement"],
          ]}
        />
      </>
    );
  }

  if (def.key === "renewal" && parent?.expiration_date) {
    const remaining = daysBetween(today, parent.expiration_date);
    const noticeBy = addDays(parent.expiration_date, -60);
    const late = daysBetween(today, noticeBy) < 0;
    return (
      <Calc title="Renewal window">
        <Stat k="Current expiry" v={parent.expiration_date} />
        <Stat k="Days remaining" v={String(remaining)} />
        <Stat k="Notice deadline" v={noticeBy} />
        <Stat k="Status" v={late ? "Inside the notice window" : "In good time"} tone={late ? "bad" : "ok"} />
      </Calc>
    );
  }

  if (def.key === "novation") {
    return (
      <ItemList
        title="Transfers with the agreement"
        items={[
          ["Statements of Work", "Every SoW under this agreement moves to the incoming party"],
          ["Open obligations", "Reassigned to the incoming party on the effective date"],
          ["Data Processing Agreement", "Must be re-signed by the incoming entity — raised as a linked request"],
          ["Outstanding invoices", values.liabilities === "Stay with the outgoing party"
            ? "Settled by the outgoing party before novation"
            : "Assumed by the incoming party"],
        ]}
      />
    );
  }

  if (def.key === "dpa") {
    const dpia = values.special_category === "Yes" || values.monitoring === "Yes" || (values.scale ?? "").startsWith("Large scale");
    const transfer = !!values.location && values.location !== "India only";
    return (
      <ItemList
        title="Modules that will be attached to this DPA"
        items={[
          ["Article 28 processing terms", "Always — the core processor obligations"],
          ["Technical and organisational measures", "Annexure II — supplier completes and we verify"],
          ["Standard contractual clauses", transfer ? "Required — processing leaves India" : "Not needed — processing stays in India", transfer],
          ["Transfer impact assessment", transfer ? "Required alongside the SCCs" : "Not needed", transfer],
          ["Sub-processor list", values.sub_processors === "None" ? "Not needed — no sub-processors" : "Required — Annexure III", values.sub_processors !== "None"],
          ["DPIA", dpia ? "Required before signature" : "Not triggered by this processing", dpia],
        ]}
      />
    );
  }

  if (def.key === "regularize") {
    return (
      <Calc title="What happens on approval">
        <Stat k="Contract" v="Created on the register" />
        <Stat k="Obligations" v="Extracted and tracked" />
        <Stat k="Renewal alerts" v={values.end_date || "From the end date"} />
        <Stat k="Compliance" v="Exception logged" tone="bad" />
      </Calc>
    );
  }

  return null;
}

/** The SoW envelope check, shown under the classification fields. */
function EnvelopePanel({ def, values, parent }: { def: AgreementFormDef; values: Values; parent: ContractResponse | null }) {
  if (def.key !== "sow" || !parent?.value_amount) return null;
  const master = parent.value_amount;
  const sow = Number(values.value || 0);
  const over = sow > master;
  return (
    <>
      <Calc title="Master agreement envelope">
        <Stat k="Master value" v={inr(master)} />
        <Stat k="This SoW" v={sow ? inr(sow) : "—"} tone={over ? "bad" : "ok"} />
        <Stat k="Headroom" v={inr(Math.max(0, master - sow))} />
        <Stat k="Within cap" v={over ? "No" : "Yes"} tone={over ? "bad" : "ok"} />
      </Calc>
      {over && (
        <Note tone="danger">
          This SoW exceeds the master agreement&apos;s value. The master needs an amendment to raise its cap before this can be signed.
        </Note>
      )}
    </>
  );
}

/** Before / after, the way the summary showed it in the design. */
function DiffTable({ rows }: { rows: [string, string, string, boolean][] }) {
  return (
    <div className="overflow-hidden rounded-xl border border-slate-200">
      <div className="grid grid-cols-[minmax(120px,170px)_1fr_1fr] gap-3 border-b border-slate-200 bg-slate-50 px-4 py-2.5 text-[10.5px] font-semibold uppercase tracking-[0.06em] text-slate-500">
        <span />
        <span>Currently</span>
        <span>After this request</span>
      </div>
      {rows.map(([k, was, now, changed]) => (
        <div key={k} className="grid grid-cols-[minmax(120px,170px)_1fr_1fr] items-center gap-3 border-b border-slate-200 px-4 py-2.5 text-[12.8px] last:border-b-0">
          <span className="text-slate-500">{k}</span>
          <span className={changed ? "text-slate-500 line-through decoration-slate-400" : "text-slate-500"}>{was}</span>
          <span className={changed ? "font-semibold text-brand-600" : "text-slate-500"}>{now}</span>
        </div>
      ))}
    </div>
  );
}

function diffRows(def: AgreementFormDef, v: Values, parent: ContractResponse | null): [string, string, string, boolean][] {
  const curValue = parent?.value_amount ? inr(parent.value_amount) : "—";
  const newValue = v.value ? inr(Number(v.value)) : curValue;
  const curTerm = `${parent?.effective_date ?? "—"} → ${parent?.expiration_date ?? "—"}`;
  switch (def.key) {
    case "amendment":
      return [
        ["Term", curTerm, `${parent?.effective_date ?? "—"} → ${v.revised_end_date || parent?.expiration_date || "—"}`, !!v.revised_end_date],
        ["Total value", curValue, newValue, newValue !== curValue],
        ["What changes", "—", v.what_changes || "—", true],
      ];
    case "renewal":
      return [
        ["Term", curTerm, `${v.new_start || "—"} → ${v.new_end || "—"}`, true],
        ["Total value", curValue, newValue, newValue !== curValue],
        ["Auto-renewal", "As signed", v.auto_renewal || "Unchanged", !!v.auto_renewal],
      ];
    case "termination":
      return [
        ["Status", `Live until ${parent?.expiration_date ?? "—"}`, `Terminated ${v.termination_date || "—"}`, true],
        ["Grounds", "—", v.grounds || "—", true],
        ["Value ended", curValue, curValue, false],
      ];
    case "novation":
      return [
        ["Counterparty", parent?.counterparty_name ?? "—", v.incoming_party || "—", true],
        ["Effective from", parent?.effective_date ?? "—", v.effective_date || "—", true],
        ["Accrued liabilities", `With ${parent?.counterparty_name ?? "the outgoing party"}`, v.liabilities || "—", true],
      ];
    case "sow":
      return [
        ["Master agreement", parent?.title ?? "—", "Unchanged", false],
        ["SoW term", "—", `${v.services_start || "—"} → ${v.services_end || "—"}`, true],
        ["Pricing", "Master rate card", v.rates || "Master rate card", v.rates === "Rates specific to this SoW"],
      ];
    case "dpa":
      return [
        ["Linked agreement", parent?.title ?? "Standalone", "Unchanged", false],
        ["Processing location", "—", v.location || "—", true],
        ["Retention", "—", v.retention || "—", true],
      ];
    case "regularize":
      return [
        ["Status", "Not on the register", "Live contract, tracked", true],
        ["Signed", v.date_signed || "—", "Unchanged — this does not re-sign it", false],
        ["Compliance", "No record", "Policy exception logged", true],
      ];
    case "cancellation":
      return [
        ["Request status", "In flight", "Cancelled", true],
        ["Counterparty access", v.notify_counterparty?.startsWith("Yes") ? "Share link live" : "Never shared", v.notify_counterparty?.startsWith("Yes") ? "Revoked" : "Unchanged", v.notify_counterparty?.startsWith("Yes") ?? false],
        ["Audit record", "Open request", "Preserved, marked cancelled", true],
      ];
    default:
      return [
        ["Agreement", "None", v.agreement_type || def.name, true],
        ["Term", "—", `${v.effective_date || "—"} → ${v.end_date || "—"}`, true],
        ["Total value", "—", newValue, true],
      ];
  }
}

function addDays(iso: string, n: number): string {
  const d = new Date(iso);
  d.setDate(d.getDate() + n);
  return d.toISOString().slice(0, 10);
}

// -------------------------------------------------------------- helpers ----

function subjectLine(def: AgreementFormDef, v: Values, parent: ContractResponse | null): string {
  const cp = parent?.counterparty_name ?? v.counterparty ?? "";
  if (def.key === "sow" && v.sow_title) return `${v.sow_title}${cp ? ` — ${cp}` : ""}`;
  return `${def.name}${cp ? ` — ${cp}` : ""}`;
}

async function fileToB64(f: File): Promise<string> {
  const buf = await f.arrayBuffer();
  let bin = "";
  const bytes = new Uint8Array(buf);
  for (let i = 0; i < bytes.byteLength; i += 1) bin += String.fromCharCode(bytes[i]);
  return btoa(bin);
}
