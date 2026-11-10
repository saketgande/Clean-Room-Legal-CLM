# Legal Intake — where every word on the screen comes from

Four places produce the words people read. Each section below lists the words and the file to change them in.

| # | Source | Who can change it | Examples |
|---|--------|-------------------|----------|
| A | **Fixed in the frontend code** | a developer | tab names, card headings, button names, stage names, status words |
| B | **The request forms** (`backend/app/intake/agreement_forms.json` + the `UI` map in `_agreement-forms.tsx`) | a developer | every question, its options, its help text |
| C | **Written by the backend while a request runs** | a developer | step notes, activity sentences, request type labels, risk wording |
| D | **Data an admin owns** (database) | an admin, in Admin screens | workflow names, step names, teams, departments, legal entities, counterparties, the people |

Paths below are relative to `frontend/src/app/(app)/intake/` unless they start with `backend/` or `frontend/`.

---

## A. Fixed in the frontend

### A1. Queue page (`_legal-intake.tsx`, `page.tsx`)
| Words | Where |
|---|---|
| Page title "Legal Intake", "New request" button | `page.tsx` (`MockupShell`) |
| Tabs: Open requests · Awaiting triage · In workflow · At risk · Overdue · Unassigned | `_legal-intake.tsx`, the `tiles` array |
| KPI cards: Open requests · Awaiting triage · In workflow · At risk · Overdue (and "n% of open") | `_legal-intake.tsx`, `kcards` |
| Status filter: Awaiting triage · In workflow · Stuck / overdue · At risk · Unassigned · Low AI confidence | `STATUS_FILTERS` |
| Channel names: Email · Chat · Form · other | `channelOf()` |
| Row flags: overdue · at risk · unassigned · low confidence | `rowFlags()` |
| Table heads: Request · Counterparty · Stage · Status · Due · Actions; row buttons Open / Start / Assign to me | the table in `LegalIntakeBoard` |
| Side cards: Needs attention · Who has the work · "+n more owners" | `_legal-intake.tsx` |
| Sort: Newest first · Oldest first · Counterparty A–Z | the sort `<select>` |

### A2. New request pages (`page.tsx`, `_agreement-forms.tsx`)
| Words | Where |
|---|---|
| "Create a new legal request", the 4 step names (Request type · Details · Files · Review) | `page.tsx`, `NewRequestTab` |
| Form cards: name and one-line description of each of the 9 forms; groups New paper · Change an agreement · Records | `AGREEMENT_FORMS` in `_agreement-forms.tsx` |
| Wizard section cards: Parties and paper · Legal basis · Agreement value · Contract term · Payment terms · Services and deliverables · Renewal and termination · Contacts · Special terms · Risk checks · Supporting files · Dates and ownership | `SECTIONS` in `_agreement-forms.tsx` |
| Sidebar cards: Request summary · What's next? · Helpful tips (the 4 tips) · Standard templates | `_agreement-forms.tsx` |
| Next-step sentences | `NEXT_STEP_TEXT` |
| Buttons: Back · Save as draft · Continue · Submit request | `_agreement-forms.tsx` |

### A3. Request page
| Words | Where |
|---|---|
| Tabs: Overview · Workflow · Documents · Risk & compliance · AI analysis · Activity · Comments · Related | `_request-overview.tsx` |
| KPI labels: Priority · Due date · Time · Overall risk · Deal value · Request type | `_request-kpis.tsx` |
| Card headings: Request details · Contract file · Latest activity · Key dates · Workflow · Documents · AI risk review · AI analysis · Activity · Comments and questions · Counterparty · Your decision · People involved · About the counterparty · Ask Aegis | the component files `_request-*.tsx`, `_decision-area.tsx` |
| Header buttons: Add comment · Reassign · Ask Aegis · Open contract | `_request-overview.tsx` |
| Decision buttons: Approve · Send back · Reassign · Add a comment | `_decision-area.tsx` |
| Step buttons: Open the draft · Review & redline · Open to negotiate · Check approval status | `STEP_DOORWAY` in `_request-overview.tsx` |
| Workflow filters: All · In progress · Upcoming · Done · Skipped | `_request-steps.tsx` |

### A4. Stage and step vocabulary (shared) — `frontend/src/components/lifecycle-view.tsx`
| Words | Where |
|---|---|
| The 7 stages and the sentence under each: Intake · Drafting · Review · Approval · Signature · Active · Closed | `LIFECYCLE` |
| Step status words: Done · Skipped · In progress · Waiting · Working · Not reached · Failed | `STATUS_LABEL` |
| Step kind tags: AI · Review · Approval · Signature · Draft · Negotiation · Notify | `KIND` |
| Plain names for internal step types ("Clm Draft" → a readable name) | `PLAIN_NAMES` in `frontend/src/lib/workflow-steps.ts` |
| "Not reviewed — needs a person" | `RISK_UNKNOWN_LABEL` in `frontend/src/lib/utils.ts` |

---

## B. The request forms

There are **9 forms and 119 questions** in `backend/app/intake/agreement_forms.json` (question key, label, kind, required, options, "show only when" rules).
Per-question help text, placeholders and the "What these answers do" sentence are in the `UI` map of `_agreement-forms.tsx`.

| Form | Questions |
|---|---|
| new_agreement ("New agreement") | 36 |
| sow ("Statement of Work") | 16 |
| dpa ("Data Processing Agreement") | 14 |
| amendment | 9 |
| renewal / termination / novation | 8 each |
| regularize ("Signed outside the system") | 16 |
| cancellation ("Cancel a request") | 4 |

Fixed option lists (all in the JSON): agreement type (NDA, Services (MSA), Buying from a vendor, Software or SaaS, Consultancy, Selling to a customer, Something else) · paper (Our template, Their paper) · term (Fixed end date, Renews automatically, No end date (evergreen)) · payment terms (30/45/60/90 days) · notice (30/60/90 days) · renewal (1/2/3 years) · governing law (India, England and Wales, Singapore, New York, Delaware) · NDA term (2/3/5 years) · buying (Goods, Services, Goods and services) · hosting (India, European Union, United States, Several regions) · fee basis (Fixed fee, Day rate, Hourly rate).

Question groups per step: `WHO`, `TERM` and the step lists at the top of `AGREEMENT_FORMS` in `_agreement-forms.tsx`.

---

## C. Written by the backend

| Words | Where |
|---|---|
| Request type labels for non-form requests: IP Question · Vendor Due Diligence · Contract Question · Legal Question — General · Other | `BUILTIN_EXTRA_TYPES`, `backend/app/intake/agents.py` |
| Step notes the engine writes on a step: "The contract already exists — nothing to draft." · "Send the contract to the counterparty." · "No approval rungs required — skipped." · "No contract to sign — skipped." · "Approval rejected." · "Sent back for rework: …" · "Condition not met" · "Upload the document on the ticket — it becomes the contract." and the failure notes | `backend/app/workflows/service.py` (`sr.note = …`) |
| The last step's name and wording: "Activate" and its notes | `backend/app/workflows/stages.py` |
| The activity feed sentences ("Stage changed to Approval", "Rejected a suggested change", "AI review complete · …") | `backend/app/intake/activity.py` |
| The risk text ("Not reviewed — needs a person — The text changed since the last review…"), top issues, fixes | the risk and playbook code (`backend/app/contracts`, `backend/app/playbooks`) and the model's own wording |
| Value and term lines ("Requester-stated", "Extracted") | `backend/app/intake/approval_summary.py` |
| The "Aegis read" summary, mismatches, bespoke asks, negotiation points | written by the AI (`ai_triage.read`) |
| SLA words: on track · at risk · overdue | `sla_status`, computed by the backend |

---

## D. Data an admin owns

| Words | Where to change them |
|---|---|
| Workflow names — e.g. "MSA — high value", "NDA — standard", "Customer — strategic deal", "Renewal — same terms", "Termination — for breach", "Vendor — small purchase" — and each step's name (Prepare draft, AI review, Legal review, Quality review, Privacy review, Counterparty negotiation, Finance approval, Legal Approval, Signature / Internal signature, Activate) | Admin → Workflows (starter set in `backend/app/workflows/contract_library.py`) |
| Teams (Legal Counsel, Compliance, Finance, Paralegals, …) | Admin → Roles & teams |
| Departments in the form (Enterprise Systems, Procurement, Commercial, R&D, Human Resources, Finance, Quality) | the `department` options in `agreement_forms.json` today (not yet an Admin list) |
| Legal entities and counterparties | the entity register / counterparty register |
| People's names and roles | Admin → Roles & teams |
| Request titles, descriptions, comments | typed by users |

---

## Words that mean the same thing but are written two ways

These are the places a reader can be confused. Say which spelling you want and I will make them one.

| Idea | Written as | Where |
|---|---|---|
| Sending back | "Send back" (button) · "Send back for rework…" (full name) · "Request changes" (outcome name in a workflow) · "Reject" (approval card) | A3, `contract_library.py` |
| A step that does not apply | "Skipped" · "Condition not met" · "Conditional" (chip) · "does not apply" | A4, C |
| Where a request is | Stage (Intake…Closed) · Status (In progress) · "Step 4 of 6" | A1, A3, A4 |
| Who has it | Owner · "With …" · "Waiting on …" · Assigned | A1, A3 |
| Overdue | "overdue" · "Stuck / overdue" · "Overdue since" · "n days overdue" | A1, A3 |
