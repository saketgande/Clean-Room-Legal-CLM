# Feature Specification: Statement of Work Filled From the Request

Feature ID: 007-sow-filled-from-request
Created: 2026-10-15
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->
Amended: 2026-10-15 with the owner's answers to the five open questions (D-3 to D-7)

## Summary

A Statement of Work (SOW) drafted from a request today still contains 35 blank bracketed
passages ("[Deliverable name and description]", "[YYYY-MM-DD]", "[name]", "[currency and
amount]", and so on), because drafting fills only a handful of fields and the SOW request form
does not ask for the rest. Those blanks stop the SOW being sent for signature until someone fills
them by hand, and the 2026-10-15 check of the SOW template on the Templates page reported its
most serious finding (scope and deliverables) because of them. This feature makes a drafted SOW
contain no blank bracket: it fills the SOW from the answers the SOW request form already collects,
replaces the free-text "Deliverables" answer with a small table (one row per deliverable: name,
due date, their owner, our owner, plus a share of the fee when pricing is Milestones) so every
deliverable is named with a date and an owner, and writes standard wording, visible and editable
in the template, for everything the form does not ask. The Consultancy and Data Processing
Agreement templates also draft with blank brackets; they are left for a separate later spec. The
AI review of a template is still not repeatable (006 FR-10), so this feature promises a SOW with
no blanks, not a template that always passes.

## User stories

- As a requester filing a Statement of Work, I want to list each deliverable as a row (name, due
  date, their owner, our owner), so that the draft names every deliverable with a date and an
  owner without my writing contract wording.
- As a requester choosing Milestones pricing, I want to say what share of the fee each deliverable
  earns, so that the draft pays what the deal says, not equal shares I never agreed.
- As a requester, I want the SOW draft filled from the title, services, dates, price, currency,
  pricing model and signer I already gave, so that I never retype them or hunt for placeholders.
- As a legal reviewer receiving a drafted SOW, I want it to contain no bracketed blank, so that I
  review real terms and the SOW is not stopped at signature by leftover placeholders.
- As a finance approver, I want the fees clause to state the pricing model and the amount I was
  asked to approve, and the milestone payments to add up to the total, so that I approve real
  numbers.
- As a template owner (the administrator who maintains our standard paper), I want the SOW
  template on the Templates page to have no blanks, to stay editable, previewable and checkable,
  and to list its new fill-in fields under plain names with examples, so that I can maintain it
  without knowing any codes.
- As a template owner, I want the standard wording (what is out of scope, customer dependencies,
  location, reporting frequency, acceptance criteria) to sit in the template where I can read,
  edit and check it, so that changing our standard position is a template edit, not a code change.
- As a requester with an older SOW request, I want it to keep working, my earlier free-text
  deliverables never to be lost or silently rewritten, and its draft to wait until I have entered
  the deliverables as rows, so that nothing is invented for me.
- As a compliance owner, I want what a SOW was filled from, and any later change to a request's
  deliverables, recorded in the audit trail.

## Functional requirements

Numbered, testable statements. Each must be verifiable by /verify. FR-41 to FR-45 were added when
the open questions were answered; they sit with the section they belong to.

Terms used here. **SOW**: a Statement of Work drafted under a signed master agreement. In the
shipped SOW wording, the **Customer** is our contracting entity and the **Provider** is the other
party; "their owner" is the Provider's owner and "our owner" is the Customer's owner. **Blank
bracket**: a bracketed passage that the application's own blank check already treats as a blank
to be filled in later (the check behind the "Fill in blanks" item on a contract and the refusal to
send a contract with blanks for signature). Lines left empty in the signature block for people to
complete at signing ("Name:", "Title:", "Date:") are not blank brackets. **Standard wording**: text
written into the template that passes the SOW playbook and states a position by its legal effect,
used where the request form asks nothing. **Deliverables table**: the SOW form's table with one
**row** per deliverable. **Share**: a row's percentage of the fee, asked only when pricing is
Milestones. **Services window**: the SOW form's "Services start" and "Services end" dates. **Fill
map**: the table under FR-3. **Example reading**: the SOW wording filled with a complete example
instead of a request's answers, which is how the Templates page check and preview read it (006
FR-10, FR-62). **Older request**: a SOW request filed before this feature with its deliverables as
free text. **Saved form**: a SOW request form a person started and saved without submitting.
**Drafted SOW**: the contract made from a request, as distinct from the request. **Held draft**: a
draft that is not made because an older request has no deliverable rows yet (FR-33). Other terms
(template, check, status, fill-in field, wording, version) are as in 006.

**A. What a drafted SOW contains**

- FR-1: The shipped SOW wording SHALL contain no blank bracket. (Today it contains 35.)
- FR-2: WHEN a SOW is drafted from the shipped SOW wording for a request filed on the SOW form,
  the drafted SOW SHALL contain no blank bracket, no leftover fill-in code or brace, and no
  "delete as appropriate" instruction, whatever the request's answers.
- FR-3: Each of the 35 blanks SHALL be filled as this Fill map says: from the request's answers
  where the SOW form asks, and with standard wording where it does not. The Provider and Customer
  owners are the "their owner" and "our owner" answers.

  | # | Place in the SOW | Blanks today | The drafted SOW contains |
  |---|---|---|---|
  | 1 | Background and objectives | 1 | Standard wording that names the SOW title answer: this SOW is issued under the master agreement for the work with that title, and the Customer expects the Services and Deliverables in it to be performed, delivered and accepted as this SOW provides. |
  | 2 | Services | 1 | The "Scope of services" answer as written, line breaks kept, surrounding spaces removed. |
  | 3 | Deliverables | 12 (4 in each of 3 entries) | One entry per row, in row order, numbered D1, D2, ...: the name, the due date, the Provider owner, the Customer owner. |
  | 4 | Out of scope | 1 | Standard wording: nothing other than the listed Services and Deliverables is in scope, no further exclusions have been agreed at the SOW Effective Date, and anything else is added only by a signed change order. |
  | 5 | Customer dependencies | 1 | Standard wording: no Customer dependency is listed at the SOW Effective Date, and the Provider may rely on a Customer dependency to excuse a delay only if it is recorded in a signed change order. |
  | 6 | Location and working hours | 1 | Standard wording: the Services are performed at the Provider's premises or remotely during the Provider's normal business hours, and at the Customer's premises only if the Customer asks in writing, in which case the Provider follows the Customer's site rules. |
  | 7 | Key personnel | 6 (3 in each of 2 lines) | The people named as Provider owner in the rows, each listed once (names compared ignoring capital letters and extra spaces), with the role "owner of" their Deliverables (by D-number) and the responsibility "delivery of those Deliverables". The template's existing rule that key personnel are not replaced without the Customer's prior written consent applies to them. |
  | 8 | Acceptance criteria | 3 | One entry per Deliverable (by D-number), in standard wording: the Deliverable conforms in all material respects to its description in the Deliverables entry and to any specification the Parties agree in writing before its due date. Acceptance stays in writing, within 10 working days, as the template already says. |
  | 9 | Pricing model | 1 | The pricing answer, stated as FR-19 says. |
  | 10 | Fixed fee amount | 1 | The total value with its currency, stated as FR-20 says. |
  | 11 | Time-and-materials not-to-exceed amount | 1 | The total value with its currency, stated as FR-20 says. |
  | 12 | Milestone payments | 3 | One entry per Deliverable, or a statement that none apply, as FR-21 and FR-44 say. |
  | 13 | Reporting frequency | 1 | Standard wording: progress is reported weekly. |
  | 14 | Representatives | 2 | Provider: their signer's name and email, with the role "authorised signatory". Customer: standard wording that the Customer owner named for each Deliverable is the Customer's representative for it, and that the Customer may tell the Provider in writing of an overall representative's name, role and email. |

- FR-4: Standard wording SHALL be part of the template's own wording, so that it is visible,
  editable, previewable and checked on the Templates page. Drafting SHALL NOT add standard wording
  that the template does not contain. Only text that varies with the request (the title, the
  services, and the lists, pricing statement and amounts built from the answers) SHALL be supplied
  by fill-in fields.
- FR-5: Nothing the drafted SOW says in the places in the Fill map SHALL provide for: deemed
  acceptance, or acceptance by use, payment or silence; payment tied to a calendar date rather
  than to written acceptance; a change to scope, fees, due dates or key personnel without a signed
  written change order; replacing key personnel without the Customer's prior written consent;
  anything outside the listed Services and Deliverables being in scope; or the Provider's own
  terms overriding the master agreement.
- FR-6: Dates in the drafted SOW SHALL be written as drafts write other dates (for example "30
  November 2026"). Money SHALL be written with its currency and two decimals (for example "INR
  4,500,000.00"). The D-numbers in the deliverables, key personnel, acceptance and payment entries
  SHALL always refer to the same Deliverable.

**B. The deliverables table on the SOW request form**

- FR-7: The SOW form's free-text question "Deliverables and who accepts them" SHALL be replaced by
  a deliverables table with the columns: Deliverable name, Due date, Their owner, Our owner (and
  Share of the fee (%) when pricing is Milestones, FR-41). The form SHALL NOT also keep a free-text
  deliverables box.
- FR-8: The table SHALL need at least one row and SHALL allow at most 20. Rows SHALL keep the order
  the requester entered them, and that order SHALL set the D-numbers. A row left entirely empty
  SHALL be ignored; a row filled in part SHALL be refused, naming the row and the missing column. A
  table with no row left after ignoring empty rows SHALL be refused.
- FR-9: Deliverable name, Due date, Their owner and Our owner SHALL be required in every row. An
  empty cell SHALL be refused; a requester who does not yet know an owner finds out before filing.
- FR-10: A deliverable name SHALL be 1 to 200 characters and an owner 1 to 120 characters, after
  leading and trailing spaces are removed and line breaks inside a cell are turned into single
  spaces. No two rows SHALL have the same deliverable name (compared ignoring capital letters and
  extra spaces); the refusal SHALL name both rows.
- FR-11: A due date SHALL fall on or after the "Services start" date and on or before the
  "Services end" date, both included. A date outside that window SHALL be refused, naming the row
  and the window. WHEN the Services window is changed after rows were entered, rows now outside it
  SHALL be refused again at the next check, naming them.
- FR-12: A due date that is inside the Services window but earlier than the day the request is
  filed SHALL be accepted. The wizard SHALL show a note, naming the row, that the date has passed;
  the note SHALL NOT block the request.
- FR-13: An owner SHALL be the name of a person typed as text. It SHALL NOT be looked up against
  people in the system or against the counterparty's contacts, and entering a name SHALL NOT
  create an account, a permission, a notification or a link to any person.
- FR-14: WHEN a free-text answer that reaches the SOW (the SoW title, the scope of services, a
  deliverable name or an owner) contains a blank bracket, filing SHALL be refused, naming the answer
  and the bracket, and saying to reword it without square brackets. Round brackets and other
  punctuation SHALL be accepted.
- FR-15: The SOW form SHALL refuse a total value of zero or less, so that the fee is always a
  stated amount.
- FR-16: The table rules (FR-8 to FR-15 and FR-41 to FR-43) SHALL be applied identically however a
  SOW request is filed or changed: through the request wizard, through the AI assistant or any
  other programmatic route, and when a filed request's answers are updated. A table the wizard
  accepts SHALL never be refused at filing, and the reverse. A deliverables answer sent as free
  text for a new filing SHALL be refused with a message saying a table of rows is needed.
- FR-17: The wizard SHALL show the deliverables table on the same step as the other scope-and-fees
  questions, with: a row to start with; add-row and remove-row controls (the last row cannot be
  removed; add-row is unavailable at 20 rows and says why); each column headed and each cell
  labelled for people who use a keyboard or a screen reader; errors shown beside the cell and
  summarised on trying to continue, naming rows and columns; entries kept when the person moves
  between steps and when filing fails (the failure message SHALL name the row at fault); the
  Share column and its running total only while pricing is Milestones (FR-41, FR-42); and the
  form's existing loading and error states, with no half-built table shown while the form's
  questions load or when they fail to load.
- FR-18: WHEN a SOW request's answers are shown (the wizard's review step, the request page and
  approvers' views), the deliverables SHALL appear as readable rows (name, due date, their owner,
  our owner, and the share when pricing is Milestones), never as raw data. The advisory read of
  the request SHALL be given the deliverables as readable rows too. None of these SHALL stop
  working because the answer is now a table. The review step SHALL name the Statement of Work
  template as what will be drafted.
- FR-41: WHEN the pricing answer is "Milestones", the deliverables table SHALL have a further
  column, "Share of the fee (%)", required in every row. WHEN the pricing answer is anything else,
  the column SHALL NOT be shown, asked or required.
- FR-42: A share SHALL be a number with at most two decimal places (whole numbers such as 40 and
  decimals such as 33.33 are both accepted), greater than 0 and at most 100. The shares of all
  rows SHALL total exactly 100, worked out to two decimals. A share that is not a number, has more
  than two decimal places, is 0 or less, or is more than 100 SHALL be refused, naming the row. A
  total other than 100 SHALL be refused, showing the total and how far it is from 100. The wizard
  SHALL show the running total as shares are entered.
- FR-43: WHEN the pricing answer is changed away from "Milestones" before filing, the wizard SHALL
  hide the Share column, keep the entered shares while the form stays open, show them again if
  "Milestones" is chosen again, and file no shares. A request filed with any pricing other than
  "Milestones" SHALL hold no shares. WHEN a filed request's pricing is updated away from
  "Milestones", that update SHALL remove its shares; WHEN it is updated to "Milestones", every row
  SHALL need a share meeting FR-42 or the update is refused. Each such change SHALL be recorded as
  FR-38 says.

**C. Pricing, fees and payments**

- FR-19: The drafted SOW SHALL state the pricing model from the pricing answer: "Fixed fee" states
  a fixed fee; "Time and materials" states time and materials with a not-to-exceed amount;
  "Milestones" states a fixed fee payable in instalments, each tied to written acceptance of a
  Deliverable. It SHALL NOT present the models as alternatives to choose from.
- FR-20: The total value and currency SHALL be the fixed fee for "Fixed fee" and "Milestones", and
  the not-to-exceed amount for "Time and materials" (the form asks for no separate cap). The
  fees clause for the model that was not chosen SHALL stay in the drafted SOW, SHALL open by saying
  it applies only where the SOW is priced that way, and any amount in it SHALL be the same total
  value.
- FR-21: The milestone payments SHALL be stated by pricing model as follows.
  - "Fixed fee": one payment per Deliverable, each in equal shares of the total value, to two
    decimals, with any rounding difference added to the last payment so that the payments add up
    exactly to the total value.
  - "Milestones": one payment per Deliverable in the share the requester gave for it, worked out
    as FR-44 says.
  - "Time and materials": no per-Deliverable payment amounts. The section SHALL say that no fixed
    milestone payments apply because the SOW is priced on a time-and-materials basis and that
    payment follows the time-and-materials payment terms in the same section, including the
    template's existing hold-back until written acceptance.
- FR-22: Every payment SHALL be payable only after the Customer has accepted in writing the
  Deliverable it is linked to, and SHALL NOT depend on a calendar date. No payment amount SHALL be
  left blank.
- FR-44: WHEN pricing is "Milestones", each Deliverable's payment SHALL be its share of the total
  value, rounded to two decimals. Any difference between the sum of the rounded payments and the
  total value SHALL be added to, or taken from, the last Deliverable's payment, so that the
  payments add up exactly to the total value. Each entry SHALL state both the share and the amount
  (for example "40% of the fixed fee: INR 1,800,000.00") and the written-acceptance condition of
  FR-22.

**D. The Templates page and the checks (builds on 006)**

- FR-23: WHEN the SOW wording is read as an example reading (the Templates page check, which
  reads a request with no answers, and the page's preview), each new fill-in field SHALL give a
  complete, blank-free example that together makes a consistent SOW: at least one Deliverable with
  name, due date and both owners; acceptance, key personnel and payment entries that match them; a
  pricing model that shows a payment for each Deliverable (Fixed fee or Milestones); an amount; and
  both representatives, with every date inside the example term and the example payments adding up
  to the example total.
- FR-24: Example content SHALL be used only for the Templates page check and preview. A draft made
  for a real request SHALL NEVER contain example content.
- FR-25: Every new fill-in field SHALL appear in the Templates page's list of fields with a plain
  name, what it fills in and an example, and SHALL be insertable from that list (006 FR-62). A
  wording that uses one SHALL pass the same "drafting can fill it" validation as any other field
  (006 FR-34), and a name not in the list SHALL still be refused. The new fields are: SoW title,
  Services, Deliverables list, Key personnel list, Acceptance criteria list, Pricing model, Fee
  amount, Milestone payments list, Provider representative.
- FR-26: The SOW template SHALL stay editable on the page, and every action 006 gives a template
  (save as a new version, preview, check, reset to the shipped wording, load back an earlier
  version) SHALL work for it, with the same audit and confirmation behaviour.
- FR-27: WHEN this feature is released, the SOW template on its shipped wording SHALL show the
  status Out of date, saying the wording changed (006 FR-6), if its last completed check covered
  the old shipped wording, and SHALL stay Never checked if it was never checked. The release SHALL
  start no paid check by itself. An organisation's own saved SOW wording SHALL NOT be changed by
  the release and SHALL keep drafting as it does today; a saved wording that does not use the new
  fields SHALL simply not use the deliverable rows. Resetting to the shipped wording (006 FR-56)
  SHALL give the new wording as a new version.
- FR-28: A check of the new shipped SOW wording SHALL read text with no blank bracket and no
  unfilled field, and no finding it reports SHALL quote a bracketed blank or be caused by one.
- FR-29: The page SHALL keep stating wherever a check result is shown that the check is an AI
  review that can report different findings on identical wording, that Passes is not a guarantee
  about any contract, and that a requester's form answers are not covered (006 FR-10). This feature
  SHALL NOT claim that the SOW template will Pass; it claims only FR-28.

**E. Older requests, saved forms and drafts already made**

- FR-30: A SOW already drafted SHALL never change because of this feature, or because the shipped
  SOW wording or a saved SOW wording is later changed (006 FR-45). This feature SHALL NOT re-draft,
  rewrite or flag any drafted SOW.
- FR-31: An older request SHALL keep its free-text deliverables answer exactly as filed. The
  request SHALL show it, labelled as the earlier free-text answer. The system SHALL NOT convert,
  delete or rewrite it.
- FR-32: Updating an older request for any reason that does not touch its deliverables (for
  example its priority, department or another answer) SHALL NOT be refused because of the old
  free-text answer. An update that sets the deliverables SHALL meet the table rules (FR-16).
- FR-33: WHEN a draft is asked for, by a person or by a workflow, from an older request whose
  deliverables exist only as free text, and the wording in effect uses the deliverable fields,
  drafting SHALL NOT proceed (a held draft) and SHALL create no contract. The person SHALL be told
  that the deliverables must be entered as rows first. The old text SHALL stay visible beside the
  table so that it can be copied. The people who may change that request today SHALL be able to
  enter the rows (FR-39). Once rows that meet the table rules (FR-16) exist, the draft SHALL be made
  from them. The system SHALL NOT turn the old text into rows by any means, and a draft SHALL NOT
  contain example content (FR-24) or any deliverable name, date or owner the requester did not
  give.
- FR-34: A saved form that holds the old free-text answer SHALL reopen with the deliverables table
  empty and the old text shown beside it for reference. Nothing SHALL be lost, and the form SHALL
  NOT be submittable until the table meets the table rules (FR-16).
- FR-45: WHEN a workflow starts the draft of an older request that is a held draft, the workflow's
  step SHALL stop and show the reason (the deliverables must be entered as rows first). The step
  SHALL NOT be skipped or marked complete, SHALL NOT make a contract, and SHALL be able to continue
  once the rows are entered, without the request being filed again.

**F. Other routes and the other templates**

- FR-35: The shipped SOW wording SHALL be used only for requests filed on the Statement of Work
  form. A request filed by email, chat or any other free-text route SHALL be recognised, routed
  and drafted exactly as it is today (a free-text request that says "SOW" or "statement of work" is
  drafted from the Master Services Agreement template), and this feature SHALL NOT change that.
  This is recorded as a known issue under Out of scope.
- FR-36: This feature SHALL NOT change what any other shipped template (NDA, Master Services
  Agreement, Software/SaaS, Vendor, Consultancy, Data Processing Agreement) drafts. The Consultancy
  and DPA blanks are left for a separate later spec.

**G. Records**

- FR-37: Each SOW drafted from a template SHALL record, in the draft's history and in the audit
  trail alongside what 006 FR-46 records, the number of deliverable rows it was filled from and
  the pricing model.
- FR-38: Setting or changing the deliverables of a filed request, including their shares and the
  removal or addition of shares when the pricing changes (FR-43), SHALL be recorded in the
  request's history and in the audit trail: who, when, and the rows before and after.

**H. Permissions and scoping** (details in the next section)

- FR-39: This feature SHALL NOT introduce a permission. Who may file a SOW request, change a filed
  request's answers, draft from it, and use each Templates page action stays as it is today.
- FR-40: Deliverable rows belong to the request they were entered on and are visible only to
  people who may see that request, under the same ethical walls and grants as the request's other
  answers and the drafted SOW. Owner names are text and never grant or reveal access.

## Permissions, scoping & audit

This platform has role-based access, ethical walls and an immutable audit trail. This feature
changes what a request records and what a draft contains. It adds no action that did not exist.

**Who may do what**

| Action | Who may perform it |
|---|---|
| File a SOW request, including its deliverables table | The people who may file a request today (FR-39) |
| Change the answers, including the deliverables and shares, of a filed request; enter rows for an older request | The people who may change that request today (FR-39) |
| See a request's deliverables and the drafted SOW | The people who may see that request or contract today, with ethical walls and grants applied unchanged (FR-40) |
| Draft a SOW from a request | The people, and the workflows, that may draft today (FR-39) |
| See the SOW template, its fields, versions, checks and downloads | People who may view playbooks (006 FR-66; today administrators and legal reviewers) |
| Check the SOW template | People who may run playbook reviews (006 FR-67) |
| Save, reset, load back or otherwise change the SOW template's wording | People who may edit playbooks (006 FR-68; today administrators only) |
| A release that changes the shipped SOW wording | Not a user action; its effect is the template's Out of date status (FR-27) |

**Scoping**

- A request, its deliverable rows, and every drafted SOW belong to one organisation and are visible
  only within it, and only to people who may see that request (FR-40). The shipped SOW wording is
  the same for every organisation; an organisation's saved wording belongs to it alone (006 FR-69).
- Owner names are free text. They are not matched to users, so no cross-organisation or
  cross-wall information can leak through them, and typing a person's name confers nothing (FR-13).
- A check sends only the SOW wording filled with example content to the AI provider. No real
  request, counterparty or personal data is involved (006 scoping unchanged).

**Audit trail.** Each of these SHALL be recorded with who, when, the request or template, and what
changed. No user can edit or delete the record.

- A SOW request filed: the filing record includes the deliverable rows exactly as filed (with their
  shares when pricing is Milestones), the pricing model, dates, value and currency (FR-37, FR-38).
- The deliverables of a filed request set or changed, including shares added or removed because
  the pricing changed, and rows entered for an older request: who, when, rows before and after
  (FR-38, FR-43).
- A SOW drafted: everything 006 FR-46 records (template, version, status at that moment) plus the
  number of deliverable rows and the pricing model (FR-37).
- Every Templates page action on the SOW template: as 006 records it, unchanged.
- A filing refused for failing the table rules, and a held draft (FR-33), change nothing, so they
  are not audit events. Entering the rows that release a held draft is (FR-38).

## Acceptance criteria

Given/When/Then scenarios that define "done". Every criterion maps to at least one functional
requirement. AC-44 to AC-48 were added when the open questions were answered. Unless stated, the
example request has services from 2026-11-01 to 2027-03-31, a total value of 4,500,000 INR,
pricing "Fixed fee", the title "Data platform migration, phase 2", the scope "Migrate the data
warehouse to the new platform.", their signer Maya Chen (maya.chen@example.com), and three rows:
"Migration plan" due 2026-11-30, "Migrated warehouse" due 2027-02-15 and "Handover pack" due
2027-03-31, with Priya Nair as their owner of the first two and Tom Reyes of the third, and Ravi
Shah as our owner of all three.

- AC-1 (FR-1): Given the shipped SOW wording, when the application's blank check is run on it,
  then it finds no blank bracket (it finds 35 in today's wording).
- AC-2 (FR-2): Given the example request, when a SOW is drafted from the shipped wording, then the
  drafted SOW has no blank bracket by the blank check, no curly brace or fill-in code, and no
  "delete as appropriate"; and given each of the three pricing answers in turn (with shares 40, 35
  and 25 for Milestones), then the same holds.
- AC-3 (FR-3): Given the example request, when the drafted SOW is read, then the Services place
  holds exactly "Migrate the data warehouse to the new platform."; the Background names "Data
  platform migration, phase 2"; and the Deliverables place holds D1 "Migration plan" due 30
  November 2026, D2 "Migrated warehouse" due 15 February 2027 and D3 "Handover pack" due 31 March
  2027, with Provider owner Priya Nair, Priya Nair and Tom Reyes and Customer owner Ravi Shah in
  all three.
- AC-4 (FR-3): Given the same draft, when the key personnel place is read, then Priya Nair is
  listed once as owner of D1 and D2, Tom Reyes once as owner of D3; given the second row's owner is
  typed "priya  nair", then Priya Nair is still listed once; and the acceptance place holds one
  entry each for D1, D2 and D3.
- AC-5 (FR-3): Given the same draft, when the out of scope, customer dependencies, location and
  working hours, and reporting places are read, then each holds its standard wording (nothing
  outside the listed Services and Deliverables in scope; no Customer dependency listed; Provider's
  premises or remote during the Provider's business hours; progress reported weekly).
- AC-6 (FR-3): Given the same draft, when the representatives place is read, then the Provider
  entry is Maya Chen, maya.chen@example.com, "authorised signatory", and the Customer entry says
  the Customer owner of each Deliverable is the Customer's representative for it.
- AC-7 (FR-4): Given a person who may change templates opens the SOW template on the page, when
  they read its wording, then the standard wording for out of scope, customer dependencies,
  location, reporting and acceptance criteria is visible in the wording; and given they change
  "weekly" to "fortnightly" and save, then the next SOW drafted says fortnightly.
- AC-8 (FR-5): Given a drafted SOW for each pricing answer, when the filled places are read, then
  none provides for deemed acceptance, acceptance by use or payment, payment by calendar date, a
  change without a signed change order, replacing key personnel without consent, or anything
  unlisted being in scope.
- AC-9 (FR-6): Given the example request, when the drafted SOW is read, then dates read "30
  November 2026" style, the fee reads "INR 4,500,000.00", and D1 to D3 refer to the same
  Deliverables in the deliverables, key personnel, acceptance and payment entries.
- AC-10 (FR-7): Given the SOW form with pricing "Fixed fee", when it is opened, then the
  deliverables question is a table with the four columns Deliverable name, Due date, Their owner,
  Our owner, and there is no free-text deliverables box.
- AC-11 (FR-8): Given the table, when the requester files with no rows, then it is refused; with
  one row, then it is accepted; with 20 rows, then it is accepted; with a 21st row, then it is
  refused and the wizard's add-row control is unavailable and says why; with an entirely empty
  row among filled ones, then the empty row is ignored; with a row missing its due date, then it
  is refused naming the row and the column; and rows keep their entered order and set D1 onward.
- AC-12 (FR-9): Given a row with no deliverable name, or no due date, or no their owner, or no our
  owner, when the request is filed, then it is refused naming the row and the missing column; and
  given all four filled, then it is accepted.
- AC-13 (FR-10): Given a name of 201 characters, then it is refused; given an owner of 121
  characters, then it is refused; given a name typed with surrounding spaces and a line break, then
  the draft shows it tidied on one line; given two rows named "Migration plan" and "migration  plan",
  then filing is refused naming both rows.
- AC-14 (FR-11): Given the example window, when a due date is 2026-11-01 or 2027-03-31, then it is
  accepted; when it is 2026-10-31 or 2027-04-01, then it is refused naming the row and the window;
  and given the requester then moves "Services end" to 2027-02-01, then the "Handover pack" row is
  refused again naming it.
- AC-15 (FR-12): Given a request filed on 2026-12-15 with a window starting 2026-11-01 and a row
  due 2026-11-30, when it is filed, then it is accepted, the wizard showed a note naming the row
  that the date has passed, and the note did not block filing.
- AC-16 (FR-13): Given an owner name that matches no user, when the request is filed, then it is
  accepted; given an owner name that matches a user, then no access, notification, link or account
  results; and no lookup is made.
- AC-17 (FR-14): Given a scope of "Migrate [TBD] systems", or a deliverable name "[Insert name]",
  when the request is filed, then it is refused naming the answer and the bracket; and given "Migrate
  (phase 1) systems", then it is accepted.
- AC-18 (FR-15): Given a total value of 0 or -5, when the request is filed, then it is refused; and
  given 1, then it is accepted.
- AC-19 (FR-16): Given the same invalid table (a row outside the window), when it is sent through
  the wizard, through the AI assistant, as a programmatic filing, and as an update to a filed
  request, then each is refused with the same message; given the same invalid shares (total 99)
  with pricing Milestones, then the same holds; given a deliverables answer sent as free text for a
  new filing, then it is refused saying a table of rows is needed; and given a table the wizard
  accepts, then filing accepts it.
- AC-20 (FR-17): Given the wizard, when the questions are loading, then no table is shown; when
  loading fails, then the form's error state with a retry is shown; when filing fails, then every
  entry is still there and the message names the row at fault; when the person uses only a keyboard,
  then they can add, fill, and remove rows and reach every error; and when the person tries to
  continue with errors, then a summary names the rows and columns.
- AC-21 (FR-18): Given a filed example request, when the review step, the request page and an
  approver's view are opened, then each shows the three deliverables as rows with name, due date,
  their owner and our owner, none shows raw data, and none fails; the advisory read is given the
  three deliverables as readable rows and still completes; given the request is filed with
  pricing Milestones, then each row also shows its share; and the review step says the Statement
  of Work template will be drafted.
- AC-22 (FR-19): Given pricing "Fixed fee", "Time and materials" and "Milestones" in turn, when the
  SOW is drafted, then the pricing statement says respectively a fixed fee, time and materials with a
  not-to-exceed amount, and a fixed fee payable in instalments tied to written acceptance; and none
  shows both models as alternatives.
- AC-23 (FR-20): Given the example request with "Fixed fee", then the fixed fee amount reads "INR
  4,500,000.00" and the time-and-materials clause opens by saying it applies only where the SOW is
  priced that way, with the same amount; given "Time and materials", then the not-to-exceed amount
  reads "INR 4,500,000.00"; and given "Milestones", then the fixed fee amount reads "INR
  4,500,000.00".
- AC-24 (FR-21): Given "Fixed fee", a total value of 100.00 and three rows, when the SOW is
  drafted, then the payments are 33.33, 33.33 and 33.34; given one row, then the single payment is
  the whole total; and given "Time and materials", then the section lists no per-Deliverable amount,
  says no fixed milestone payments apply, and keeps the hold-back until written acceptance. The
  "Milestones" case is AC-47.
- AC-25 (FR-22): Given any pricing answer, when the drafted SOW is read, then every payment is
  stated as payable after written acceptance of its Deliverable, none depends on a calendar date,
  none is blank, and the payments add up exactly to the total value.
- AC-26 (FR-23): Given the Templates page, when the check of the SOW template is read and the
  preview is opened, then the example reading holds at least one Deliverable with name, due date,
  Provider owner and Customer owner, matching acceptance, key personnel and payment entries, a
  pricing model that shows a payment for each Deliverable, an amount and both representatives, has
  no blank bracket, has every date inside the example term, and has payments adding up to the
  example total.
- AC-27 (FR-24): Given the example request, when the drafted SOW is searched for the page's example
  deliverables, names and amounts, then none is present; and given a request with one row, then
  the drafted SOW has exactly that one Deliverable.
- AC-28 (FR-25): Given the Templates page, when the field list is opened, then it shows the nine new
  fields (SoW title, Services, Deliverables list, Key personnel list, Acceptance criteria list,
  Pricing model, Fee amount, Milestone payments list, Provider representative), each with a plain
  name, what it fills in and an example; when one is inserted into the SOW wording and saved, then
  it saves; and when a name not in the list is typed, then saving is refused as before.
- AC-29 (FR-26): Given a person who may change templates, when they edit the SOW template, save,
  preview, check, reset, and load back an earlier version, then each works with the same
  confirmation and audit behaviour as for any template.
- AC-30 (FR-27): Given the release and a SOW template whose last completed check covered the old
  shipped wording, when the Templates page is opened, then it shows Out of date saying the wording
  changed, no check has started and no AI usage was recorded; given a SOW template never checked,
  then it still shows Never checked; given an organisation with a saved SOW wording that has
  brackets and none of the new fields, then it still drafts exactly as before; and given that
  organisation resets to the shipped wording, then a new version holds the new blank-free wording.
- AC-31 (FR-28): Given a check of the new shipped SOW wording, when it completes, then the text it
  read has no blank bracket and no finding quotes a bracketed blank. The verification report
  records the check's actual findings, whatever they are.
- AC-32 (FR-29): Given any view of a SOW template check result, then the page states that AI
  reviews can differ on identical wording, that Passes is not a guarantee, and that form answers
  are not covered; and nothing in the page or the release notes says the SOW template will Pass.
- AC-33 (FR-30): Given SOWs drafted before this feature, when the feature is released and the SOW
  template is later edited or reset, then those drafted SOWs' text is identical to before.
- AC-34 (FR-31): Given an older request with the free-text deliverables answer "Plan by Nov, report
  by Mar, Ravi signs off", when it is opened after the release, then the text is shown unchanged and
  labelled as the earlier free-text answer, and the stored answer is unchanged.
- AC-35 (FR-32): Given that older request, when its priority or another answer is updated, then the
  update succeeds; and when its deliverables are set to a table with a row outside the window, then
  the update is refused as in AC-19.
- AC-36 (FR-33): Given that older request and the shipped SOW wording, when a person asks for the
  draft, then no contract is created, they are told the deliverables must be entered as rows
  first, and the old text is shown beside the table; given a person who may change that request
  then enters rows that meet the rules, when the draft is made, then it holds exactly those rows and
  no example content or other deliverable, date or owner; given nothing converts the old text, then
  the stored free text is still unchanged afterwards; and given an organisation's saved SOW wording
  that does not use the deliverable fields, then the older request drafts as today with no hold.
- AC-37 (FR-34): Given a saved form that holds the old text, when it is reopened, then the table is
  empty, the old text is shown beside it, nothing is lost, and it cannot be submitted until the
  table meets the rules.
- AC-38 (FR-35): Given a request filed by email whose text says "please draft a SOW", when it is
  routed and drafted before and after the release, then the outcome is identical (drafted from the
  Master Services Agreement template); and given a request filed on the Statement of Work form,
  then it is drafted from the SOW template.
- AC-39 (FR-36): Given the NDA, Master Services Agreement, Software/SaaS, Vendor, Consultancy and
  Data Processing Agreement templates, when a draft is made before and after the release, then the
  drafts are identical (the Consultancy and DPA drafts still contain their blanks).
- AC-40 (FR-37): Given the example request is drafted, when the drafted SOW's history and the audit
  trail are read, then both state the template, its version and its status at that moment (006
  FR-46), and also three deliverable rows and the pricing model "Fixed fee".
- AC-41 (FR-38): Given a filed request, when its deliverables are changed from three rows to two,
  then the request's history and the audit trail show who, when, and the three rows before and the
  two after (with their shares when pricing is Milestones), and no user can edit or delete that
  audit record.
- AC-42 (FR-39): Given a person who may not file requests, when they try to file a SOW request,
  then it is refused as today; given a legal reviewer, when they try to save the SOW template, then
  it is refused as in 006; and given the application's list of permissions before and after this
  feature, then the list is the same.
- AC-43 (FR-40): Given two organisations, when a person in one asks for the other's request, rows
  or drafted SOW, then it is not found; given a person walled off from a contract, then they cannot
  see its deliverables; and given an owner name that equals a user's name, then it reveals or grants
  nothing.
- AC-44 (FR-41): Given the SOW form with pricing "Fixed fee" or "Time and materials", when the
  table is shown, then it has no Share column; given pricing is changed to "Milestones", then a
  "Share of the fee (%)" column appears and is required in every row, and a row without a share is
  refused naming the row and the column.
- AC-45 (FR-42): Given pricing "Milestones" and the three example rows, when shares are 40, 35 and
  25, or 33.33, 33.33 and 33.34, or 12.5, 37.5 and 50, then each is accepted; given a single row
  with share 100, then it is accepted; given a share of 33.333, 0, -5, 101 or "forty", then it is
  refused naming the row; given shares 40, 35 and 24, then filing is refused showing a total of 99
  and a difference of 1; given 40, 35 and 26, then it is refused showing 101; given a single row
  with share 50, then it is refused; and given shares being typed in the wizard, then the running
  total updates as they are entered.
- AC-46 (FR-43): Given pricing "Milestones" with shares 40, 35 and 25 entered, when the person
  changes pricing to "Fixed fee", then the Share column is hidden and nothing about shares is filed,
  the stored request holds no shares, and the drafted SOW pays INR 1,500,000.00 per Deliverable; given
  they change it back to "Milestones" before filing, then 40, 35 and 25 reappear; given a filed
  Milestones request is updated to "Time and materials", then its shares are removed by the update
  and the audit trail shows the rows before (with shares) and after (without); and given a filed
  "Fixed fee" request is updated to "Milestones", then the update is refused naming the rows that
  lack a share, and accepted once shares of 40, 35 and 25 are given.
- AC-47 (FR-44): Given pricing "Milestones", total INR 4,500,000.00 and shares 40, 35 and 25, when
  the SOW is drafted, then the payments read "40% of the fixed fee: INR 1,800,000.00", "35% ...:
  INR 1,575,000.00" and "25% ...: INR 1,125,000.00", each payable after written acceptance of its
  Deliverable; given total 100.01 and shares 33.33, 33.33 and 33.34, then the payments are 33.33,
  33.33 and 33.35 and add up to 100.01 exactly; and given one row with share 100, then the single
  payment is the whole total.
- AC-48 (FR-45): Given a workflow reaches its drafting step for an older request that is a held
  draft, when the step runs, then it stops showing the reason that the deliverables must be entered
  as rows first, it is not skipped or marked complete, and no contract is made; and given a person
  then enters valid rows, then the step can continue and the draft is made without the request
  being filed again.

## Out of scope

- The Consultancy and Data Processing Agreement templates are left for a separate later spec (D-3).
  Consultancy has 5 blank brackets (services, deliverables and timetable, fees, key individual(s),
  Company contact) and its form does not ask for deliverables, fee basis, key individuals or the
  Company contact. The DPA has 6 (nature and purpose of the processing, categories of data
  subjects, types of personal data, sensitive data, processing locations, authorised
  sub-processors); its annex needs facts its form does not collect (the processing locations, the
  sub-processor names, and which categories apply when the answer is "Several"). Their drafts keep
  these blanks, which stop signature until filled by hand. The NDA, Master Services Agreement,
  Software/SaaS and Vendor templates have no blank brackets and are not changed.
- Re-drafting, correcting or flagging SOWs already drafted (FR-30), including filling in the
  blanks of an existing draft.
- How a free-text request (email, chat) is recognised, routed or drafted (FR-35; see Known issues).
- Warning about, blocking, stripping or migrating blank brackets in an organisation's own saved
  template wording. The shipped wording ships without them (FR-1); an organisation's edit is its
  own, and the existing blank check still catches blanks before approval and signature.
- Converting an older request's free-text deliverables into rows by the system or by AI (FR-33).
- Tracking deliverables after drafting: reminders for due dates, creating obligations from rows,
  progress, acceptance status, notifications to owners, and linking owners to people in the system.
- Importing deliverables from a spreadsheet or file; per-deliverable descriptions beyond the name;
  per-deliverable acceptance criteria or fees entered by the requester (other than the share of the
  fee asked when pricing is Milestones, FR-41).
- Changing the SOW playbook's rules, the AI review's prompts or models, or making the review
  repeatable. Optional or conditional clauses inside a template, and fill-in fields beyond those in
  FR-25.
- The signature block's empty lines (name, title, date), the governing-law fallback, naming the
  master agreement in the draft, the SOW wording's "this Agreement" in the total-value sentence,
  and using the SoW title as the contract's title or file name.
- The wizard's "what will be drafted" text for forms other than the SOW (for example Consultancy,
  Vendor and SaaS, which still name other templates).
- Word-formatted drafts and Word or PDF template upload (006 Phases 2 and 3). Fields added here
  are ordinary fill-in fields and follow whatever the page supports at the time.
- Currencies beyond the form's four, tax, languages other than English, a global redesign, new
  navigation, a new theme or new shared components.
- Changing who approves a SOW or which workflow it uses.

**Known issues (found, not fixed here)**

- Free-text SOW requests are drafted from the Master Services Agreement template. Any email or chat
  request whose text says "SOW" or "statement of work" is drafted from the MSA template, which has
  no blank brackets but is not a SOW. It is not fixed here (D-7) because a free-text request has no
  deliverables, dates or owners to name, so the SOW template could not meet the playbook's scope
  rule from it. A separate follow-up could send such requests to Legal, as for any kind of agreement
  without a template, and point the requester to the Statement of Work form.
- Consultancy and DPA drafts still contain blank brackets (5 and 6; see the first Out of scope
  item) and are left for a separate later spec (D-3).

## Edge cases & error behavior

- Release: shipping this feature starts no paid check. A SOW template that had been checked shows
  Out of date, and one never checked stays Never checked, until a change, a playbook publication or
  a person's check starts one (FR-27). Dev and test data with no saved SOW wording are on the
  shipped wording and pick up the new wording at once.
- An organisation's saved SOW wording keeps brackets and drafts them as today (FR-27). The
  deliverable rows are stored on the request but unused by that wording, and an older request is
  not held for it (FR-33).
- One deliverable: D1 only; the single payment is the whole total, and with Milestones its share is
  100 (FR-21, FR-42). Twenty deliverables: accepted; a twenty-first is refused (FR-8).
- A one-day Services window: every due date must equal that day (FR-11).
- The Services window moves after rows were entered: the wizard flags the rows now outside it and
  filing refuses them (FR-11).
- The pricing answer changes after the table was filled: the table is unaffected; the Share column
  appears or hides as FR-43 says, shares are kept in the open form but never filed unless pricing
  is Milestones, and the milestone payments are worked out from the pricing at drafting time
  (FR-21).
- Shares such as 33.33, 33.33 and 33.34 are the way to split a fee in thirds; 33.333 is refused
  (FR-42). The total value changing after shares were entered leaves the shares as they are and the
  amounts follow the new value (FR-44).
- A total value with more than two decimals: payments are rounded to two decimals and the last
  payment absorbs the difference (FR-21, FR-44).
- The same Provider owner on several rows: listed once in key personnel with all their D-numbers;
  two different people who share a name cannot be told apart (FR-3).
- Names with quotes, curly braces, non-English characters or very long text within the limits are
  inserted as plain text and never break the draft (FR-10; 006 FR-42).
- A requester pastes a template placeholder such as "[Describe the business need]" into the scope:
  refused as a blank bracket (FR-14).
- The advisory read, the request page and approver views meet an older request with free text and
  a newer one with a table: both display (FR-18, FR-31).
- A SOW field inserted into a non-SOW template (for example an NDA): a real request for that kind
  has no deliverable rows, so the field fills with nothing and never with a bracket or example
  content (FR-24). The Templates page check and preview still show the example reading.
- A workflow starts a SOW draft with no person present: the same filling applies; for an older
  request the step stops and shows the reason, then continues once rows are entered (FR-33, FR-45).
- An older request whose pricing is Milestones needs shares as well as rows before it can be
  drafted (FR-33, FR-41).
- A filed request with a total value of zero (filed before FR-15): drafted with the amount as
  given, "0.00"; FR-15 applies to new filings.
- The AI review is unavailable, over its limit, or returns different findings for identical wording:
  as 006 FR-8, FR-10, FR-11 and FR-20 (FR-29).
- A person without change permission opens the SOW template: read-only with the reason shown, as
  006 FR-64.

## Decisions

Answers the owner gave on 2026-10-15; each is folded into the requirements above. D-1 and D-2 were
given before drafting; D-3 to D-7 in answer to the five open questions, which are all closed.

- D-1: The SOW is fixed in three steps. (a) Fill it from what the form already asks: services,
  scope, deliverables, start and end dates, price, currency, pricing model and their signer. (b)
  Change the SOW form's "Deliverables" from one free-text box into a small table, one row per
  deliverable (name, due date, their owner, our owner), so the playbook's high-severity scope rule
  can be met. (c) Everything the form does not ask gets standard wording that passes the SOW
  playbook instead of a blank: background, out-of-scope list, customer dependencies, location, key
  personnel, reporting frequency, our representative, per-deliverable acceptance criteria and
  milestone payment amounts. The result is a drafted SOW with no blank bracket. (FR-1 to FR-22)
- D-2: This is its own spec (007), separate from 006. It builds on 006: the Templates page, field
  list, checks and the rule that drafted contracts never change. (FR-25 to FR-30)
- D-3 (owner, 2026-10-15; Consultancy and DPA): they are left for a separate later spec. This
  feature keeps them out of scope and does not change what they draft. (FR-36; Out of scope)
- D-4 (owner, 2026-10-15; owners): both "Their owner" and "Our owner" are required in every row. A
  requester who does not know an owner finds out before filing. (FR-9)
- D-5 (owner, 2026-10-15; older requests): drafting an older request whose deliverables exist only
  as free text is held until the rows are entered. The old text stays visible for copying, a
  workflow-started draft stops with the reason shown, nothing is invented, and no AI conversion is
  used. (FR-33, FR-45)
- D-6 (owner, 2026-10-15; Milestones): a required "Share of the fee (%)" column appears only when
  pricing is Milestones, and the shares must total 100%. The validation, the payment arithmetic and
  the handling when pricing is switched are the analyst's wording under it (A-15 to A-17). (FR-41
  to FR-44)
- D-7 (owner, 2026-10-15; free-text SOW requests): they keep today's routing (the MSA template). It
  is logged as a known issue; a separate follow-up could send them to Legal. (FR-35; Known issues)

Wording added by the analyst to make the decisions testable. The owner has not reviewed these;
they are reviewed at approval and any of them may be reversed. A-1 to A-14 were written with the
first draft; A-15 to A-18 were added with the owner's answers.

- A-1: The shipped SOW wording is written from the Customer's side: the Customer is us and the
  Provider is the other party. So "their owner" is the Provider owner and "our owner" the Customer
  owner (FR-3).
- A-2: A table holds 1 to 20 rows, in entered order, numbered D1 onward by that order (FR-8).
- A-3: A due date must be inside the Services window, both ends included, and a date that has
  already passed is allowed with a non-blocking note, because the form does not refuse a past
  start date either (FR-11, FR-12).
- A-4: An owner is a typed name, not a person in the system. Their owner is a counterparty person
  who has no account, and our owner may be a colleague without one (FR-13).
- A-5: Two rows may not share a deliverable name, so that acceptance and payment entries stay
  unambiguous (FR-10).
- A-6: Free text that would reach the draft is refused at filing if it contains a blank bracket, so
  that "no blank bracket" holds for requester-written text too. It uses the application's existing
  definition of a blank (FR-14).
- A-7: A SOW's total value must be above zero, so the fee is always a stated amount (FR-15).
- A-8: For time and materials, the form's one money figure ("Total value of this SoW") is the
  not-to-exceed amount, because the form asks for no separate cap (FR-20).
- A-9: Key personnel are the people named as Provider owner in the rows. This binds those people
  under the template's no-replacement-without-consent rule (FR-3).
- A-10: The Provider's representative is their signer, with the role "authorised signatory"; the
  Customer's representatives are the Customer owners (FR-3).
- A-11: A "Fixed fee" SOW pays in equal shares per Deliverable on written acceptance, the neutral
  way to satisfy the playbook's rule that payments follow accepted milestones (FR-21).
- A-12: Reporting is weekly, the stricter of the two options the template offered; the playbook is
  silent on it (FR-3).
- A-13: The Templates page check keeps reading the template as a request with no answers (006
  FR-10), so it reads an example SOW; this is why FR-23 and FR-24 exist (FR-23).
- A-14: The SoW title is used only in the background statement. The form asks for it today but
  drafting does not use it (FR-3).
- A-15: A share accepts whole numbers and decimals up to two places, must be above 0 and at most
  100 in each row (so no row is a zero-payment milestone), and the total must be exactly 100 to two
  decimals (FR-42).
- A-16: Each Milestones payment is the share of the total value rounded to two decimals, the
  rounding difference goes to the last Deliverable's payment, and the draft states both the share
  and the amount (FR-44).
- A-17: When pricing leaves Milestones, shares are kept only in the open form (so a change of mind
  loses nothing) and are never filed; on a filed request the update removes them; moving a filed
  request to Milestones requires shares for every row (FR-43).
- A-18: The hold on an older request applies only when the wording in effect uses the deliverable
  fields, because an organisation's own wording that ignores them has nothing to invent (FR-33).

---

### Context for planning (non-normative)

Implementation-adjacent facts recorded to save the architect re-deriving them. They are not
requirements and carry no normative weight.

- **What the blank check is.** `app/contracts/blanks.py` (`find_blanks`) is the one detector used
  by the signature gate (`signatures/service.py`, `ensure_text_is_complete`, 409 unless the sender
  may override), the contract readiness checklist ("Fill in blanks", advisory) and the AI edit
  engine. Reuse it as the definition for FR-1, FR-2 and FR-14 and in tests. It treats any bracket
  with words as a blank except a short list of editorial marks (for example a bracket opening
  with a lower-case article), and does not look at signature-block lines.
- **Where the 35 blanks are.** `drafting_templates/defaults/sow.txt`, clauses 4.1 to 4.6, 5.1, 6.1,
  8.1 to 8.3, 9.1, 10.1, 10.2 (counts in the Fill map). Already filled today: effective date
  (from "Services start"), term (from "Services end"), payment days (default 45), governing law
  (the entity's jurisdiction, else Delaware), the two party names, the total-value sentence.
- **Current drafting path.** `intake/drafting.py`: `resolve_doc_type` (form `sow` maps to the `sow`
  template; free text with "statement of work" or "sow" maps to `msa`, line ~88; unchanged by
  FR-35), `template_values` builds the placeholder values, `render_document` fills with
  `str.format` (values are not re-parsed, so brackets and braces in a value are inert),
  `draft_contract_for_request` records the 006 template record. `request_facts` already maps
  `services_start`/`services_end` to the contract's dates and `value`/`currency` to the contract's
  value. The hold (FR-33) belongs before rendering in `draft_contract_for_request`; the `clm_draft`
  workflow step calls it, so check how that step shows a refusal and keep the reason visible
  (FR-45).
- **The check reads a bare request.** `drafting_templates/checks.py` `check_values` calls
  `template_values(..., fields={})`; `tests/test_template_check.py` pins that the check sends
  exactly what a bare request drafts. So the new fields' no-answer fill is what the check sees, and
  it must be the complete example of FR-23. It must be reachable only from the check and preview
  (FR-24), never from a real request with missing rows (older requests: FR-33). The preview uses
  `render_sample` with `SAMPLE_VALUES`, so each new field needs an entry in `PLACEHOLDERS` (label,
  about, sample) in `drafting_templates/service.py`.
- **Finding location and Apply.** `checks.template_form_of` and `_CODED` turn filled text back into
  template wording for "jump to passage" and one-click Apply (006 FR-24, FR-25). Multi-line list
  values will not be coded, so a finding that quotes one will say it could not be located, which
  006 FR-24 already allows. Apply must never write example deliverables into the template.
- **Form question mechanics.** Questions live only in `intake/agreement_forms.json`; kinds today are
  text, email, textarea, select, multiselect, date, number, so a table is a new kind. Filing
  validation is `_validate_field_values` and `_coerce_field` in `intake/service.py`; it already
  drops answers to questions that no longer apply, but the Share column is part of one table
  question, so FR-41 and FR-43 need a column-level show rule like the form-level `show`.
  `update_request` re-validates field values through the same path, so an older request's string
  answer would fail a table rule on any update unless it is passed through (FR-32). The wizard
  (`frontend/.../intake/_agreement-forms.tsx`) loads questions from `/intake/forms` and adds only
  layout (the SOW's "Scope and fees" step lists `deliverables`); `agreement-forms.test.ts` fails if a
  question has no step. Saved forms are `IntakeDraft` records (`/intake/drafts`). Keep the page
  working in demo mode (`src/lib/demo.ts`) if the wizard is.
- **Money arithmetic.** Use exact decimals for shares and payments (never floats), so that FR-42's
  total of exactly 100 and FR-44's sum-to-total hold. `request_facts` already parses `value` with
  `Decimal`.
- **Stale wizard label.** `draftFor` and `DRAFT_OF` in `_agreement-forms.tsx` still say the SOW is
  drafted from the "MSA template (SoW schedule)" (and name the MSA or Vendor template for
  Consultancy, Vendor and SaaS). FR-18 covers only the SOW line.
- **Consultancy and DPA facts (for the later spec).** Consultancy blanks (`consultancy.txt` ~191 to
  199): Services, Deliverables and timetable, Fees, key individual(s), Company contact. Its form
  (New agreement, kind Consultancy) asks scope, value, currency, start and end dates, payment terms;
  it asks nothing for deliverables, fee basis, key individuals or the Company contact. DPA blanks
  (`dpa.txt` ~168 to 176, 204): nature and purpose, categories of Data Subjects, types of Personal
  Data, sensitive data, processing locations, authorised sub-processors. Its form asks the purpose
  (free text), data types, data subjects (single choices, each with a "Several" option), "will data
  leave India" (Yes/No), "will they use sub-processors" (Yes/No) and our role (controller or
  processor); it does not ask the locations or the sub-processor names.
- **Audit and history.** `write_audit_log` and `write_timeline_event` (`app/core/audit.py`); the
  006 record is built in `DraftingService._template_at_this_moment` and
  `_record_drafted_from_template`; `intake.contract_drafted` is the audit entry to extend (FR-37).
  `update_request` currently audits only priority and work status in `intake.updated`; FR-38 needs
  the deliverables (with shares) before and after. Do not write the audit row on a second session
  or hold it across slow work (CLAUDE.md, audit-lock freeze).
- **006 status.** Only Phase 1 (statuses, checks, editing text templates) is built; upload, new
  types and Word-formatted drafts (Phases 2 and 3) are not. This spec depends only on Phase 1.
  `docx_fields.py` exists and enforces the same field rule for Word, so list-valued fields should be
  designed so a Phase 3 Word fill can insert them as separate paragraphs.
- **Evidence.** The 2026-10-15 Check all flagged the SOW template with a high-severity finding on
  scope and deliverables because of the blanks. The dev org has no saved template versions, so every
  template is on its shipped wording. MSA, NDA, SaaS and Vendor shipped wording have no blank
  brackets; a test that fills each shipped template (bare and with a full request) and runs
  `find_blanks` would keep it so, with Consultancy and DPA listed as known exceptions until fixed.
- **Repo graph.** `specs/_graph/domains/intake.md` and `drafting_templates.md` list the routes and
  models involved; read them first.
