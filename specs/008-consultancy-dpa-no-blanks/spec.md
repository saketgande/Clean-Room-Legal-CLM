# Feature Specification: Consultancy and DPA Drafts With No Blank Brackets

Feature ID: 008-consultancy-dpa-no-blanks
Created: 2026-10-15
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->
Amended: 2026-10-15 with the owner's answers to the six open questions (D-5 to D-10) and D-11

## Summary

A Consultancy agreement or a Data Processing Agreement (DPA) drafted from our templates today carries
blank bracketed passages: 5 in the Consultancy Engagement Schedule (services, deliverables and
timetable, fees, the Consultant's key individuals, the Company contact) and 6 in the DPA annexes (nature
and purpose of the processing, categories of data subjects, types of personal data, sensitive data,
processing locations, authorised sub-processors). The application's own blank check sees all 11 and
refuses to send such a draft for signature until someone fills them in by hand. Neither request form
asks for most of these facts, and the DPA form collects its purpose, kinds of data and whose data but
never puts them in the draft. This feature makes both drafts contain no blank bracket: it fills them
from answers the forms already collect, adds the questions the owner chose for the facts that are
missing, and writes standard wording, visible and editable in the template, for the rest. It reuses the
mechanisms built for the Statement of Work in 007. Because our DPA template is written for us as the
Controller, a DPA request that says we are the Processor is no longer drafted from it: the person is told
to attach the other side's DPA before the review. Both templates' last check (2026-10-15) was Passes, and the AI review is still not repeatable
(006 FR-10), so this feature promises drafts with no blanks that state the positions the playbooks
prefer, not a template that always passes.

## User stories

- As a requester filing a Consultancy request, I want to say what the consultant will hand over and
  when, how the consultant is paid, who the consultant's key individuals are and who our contact is, so
  that the draft states them and I never fill in placeholders by hand.
- As a requester filing a DPA request, I want to say whose data it is and what kinds (more than one where
  that applies), which countries the data goes to and which sub-processors are used, so that the annex
  states what I told it.
- As a legal reviewer receiving a drafted Consultancy agreement or DPA, I want it to contain no bracketed
  blank, so that I review real terms and the draft is not stopped at signature by leftover placeholders.
- As a privacy or compliance reviewer of a DPA, I want the annex to state the categories of people, the
  kinds of data (and whether any is special category), the countries and the sub-processors consistently
  with the request, and to allow processing outside India or by a sub-processor only on the conditions
  the DPA sets, so that I assess the real arrangement.
- As a finance approver of a Consultancy request, I want the fees entry to state the fee basis and the
  amount I was asked to approve, with that amount as a ceiling when the consultant is paid by a rate, so
  that I approve real numbers.
- As a template owner (the administrator who maintains our standard paper), I want the Consultancy and
  DPA templates on the Templates page to have no blanks, to stay editable, previewable and checkable, and
  to list their new fill-in fields under plain names with examples, so that I can maintain them without
  knowing any codes.
- As a template owner, I want the standard wording (key individuals are not replaced without consent;
  transfers and sub-processors are allowed only on the DPA's conditions) to sit in the template where I
  can read, edit and check it, so that changing our standard position is a template edit, not a code
  change.
- As a requester with an older request, or a DPA request that came in by email or chat, I want it to keep
  working, my earlier answers never to be lost or rewritten, and its draft to wait until the missing
  details are entered, so that nothing is invented for me.
- As a requester whose DPA request says we are the Processor, I want to be told plainly that no draft is
  made from our Controller-side template and that I should attach the other side's DPA (or the paper to
  use) before the review, so that I never receive a draft on the wrong side's paper.
- As a compliance owner, I want what a Consultancy agreement or DPA was filled from, any later change to
  those answers on a request, and the decision not to draft a Processor request, recorded in the audit
  trail.
- As the owner of the AI quality exam, I want its Consultancy and DPA documents and its baseline to stay
  exactly as they are, so that past and future exam results stay comparable.

## Functional requirements

Numbered, testable statements. Each must be verifiable by /verify. FR-53 to FR-56 were added when the
owner's answers were folded in; they sit at the end, as 007's late requirements did, and are referred to
from the sections they affect.

Terms used here. **Consultancy agreement**: a draft of our Consultancy template, made for a request filed
on the New agreement form with the kind "Consultancy". **Engagement Schedule**: the schedule at the end of
that template. **DPA**: a draft of our Data Processing Agreement template, made for a request filed on the
Data Processing Agreement form (or recognised from free text, see FR-46). **Annex 1** and **Annex 3**:
the annexes of the DPA that describe the processing and list the authorised sub-processors. **Company**:
our contracting entity in the Consultancy wording (the Controller in the DPA wording); **Consultant** and
**Processor** are the other party. **Blank bracket**: a bracketed passage that the application's own blank
check treats as a blank to be filled in later (the check behind the "Fill in blanks" item on a contract
and the refusal to send a contract with blanks for signature). Lines left empty in a signature block
("Name:", "Title:", "Date:", "Address for notices:") are not blank brackets. **Standard wording**: text
written into the template that states a position by its legal effect, used where the request form asks
nothing. **Fill map**: the tables under FR-3 and FR-8. **Example reading**: the template filled with a
complete example instead of a request's answers, which is how the Templates page check and preview read
it (006 FR-10, FR-62; 007 FR-23). **Own paper**: a request whose answer to "Whose paper?" is "Our
template", or that has no such answer. **Older request**: a Consultancy or DPA request filed before this
feature. **Saved form**: a request form a person started and saved without submitting. **Lacks an
answer**: the wording in effect uses a fill-in field built from an answer this feature asks, the question
applies to the request's other answers, and the request holds no valid answer to it. For a Consultancy
request the answers are the deliverables (or the no-deliverables choice), the fee basis (and the rate for
a rate basis), the key individuals and the Company contact. For a DPA request they are the kinds of
people and kinds of data (a single earlier choice other than "Several" is a valid answer), the countries
when the request says data leaves India, and the sub-processors when it says sub-processors are used.
**Held draft**: a draft that is not made because the request lacks an answer (FR-43). **Processor
request**: a DPA request whose answer to "Our role" is "Processor: we act on their instructions", that is,
a request in which we, not the other party, would be the processor. Other terms (template, check, status,
fill-in field, wording, version) are as in 006.

**A. What a drafted Consultancy agreement contains**

- FR-1: The shipped Consultancy wording SHALL contain no blank bracket. (Today it contains 5, all in the
  Engagement Schedule.)
- FR-2: WHEN a Consultancy agreement is drafted from the shipped wording, the drafted agreement SHALL
  contain no blank bracket, no leftover fill-in code or brace, and no instruction to the reader, whatever
  answers the form accepted (each fee basis, with and without deliverables).
- FR-3: Each of the 5 blanks SHALL be filled as this Fill map says: from the request's answers where the
  form asks, and with standard wording where it does not.

  | # | Place in the Engagement Schedule | Blanks today | The drafted agreement contains |
  |---|---|---|---|
  | 1 | Services | 1 | The "Scope of services" answer as written, line breaks kept, surrounding spaces removed. |
  | 2 | Deliverables and timetable | 1 | One entry per deliverable row, in the order entered, numbered D1, D2, ...: the name and the due date. When the requester said there are no separate deliverables, a statement that none are specified and that the Consultant's output is the reports and advice it gives under Clause 2.2. |
  | 3 | Fees | 1 | The fee basis and amounts, as FR-4 says. |
  | 4 | Consultant's key individual(s) | 1 | Each key individual named once (names compared ignoring capital letters and extra spaces), followed by standard wording that the Consultant shall not replace them without the Company's prior written consent. |
  | 5 | Company contact | 1 | The Company contact's name and role. |

- FR-4: The Fees entry SHALL state the fee basis the requester chose and the amounts that go with it. For
  "Fixed fee": a fixed fee equal to the total value, with its currency. For "Day rate" or "Hourly rate":
  that the Fees are charged at the rate the requester gave, per day or per hour, for time properly spent
  on the Services, and that they SHALL NOT exceed the total value (with its currency) except by a written
  amendment signed by both Parties. It SHALL NOT present the bases as alternatives to choose from, SHALL
  say that invoicing and payment follow Clauses 6.2 to 6.4, and SHALL NOT provide for payment in advance.
- FR-5: Nothing the drafted schedule says in the five places SHALL depart from the positions the
  published Consultancy Agreement Playbook prefers: payment within the number of days in Clause 6.3 of a
  valid, undisputed invoice, never payable in advance, and any advance no more than 20% of the Fees
  (Clause 6.5); the Consultant as an independent contractor, with nothing that has the Company direct or
  supervise how, when or where it works or provide its tools, equipment, leave or benefits; fees above the
  stated amount only by a written amendment signed by both Parties; and the key individuals not replaced
  without the Company's prior written consent.

**B. What a drafted DPA contains**

- FR-6: The shipped DPA wording SHALL contain no blank bracket. (Today it contains 6: five in Annex 1 and
  one in Annex 3.)
- FR-7: WHEN a DPA is drafted from the shipped wording, the drafted DPA SHALL contain no blank bracket, no
  leftover fill-in code or brace, and no instruction to the reader, whatever answers the form accepted.
- FR-8: Each of the 6 blanks SHALL be filled as this Fill map says.

  | # | Place in the DPA | Blanks today | The drafted DPA contains |
  |---|---|---|---|
  | 1 | Annex 1: Nature and purpose of the Processing | 1 | The "Why is the data processed?" answer as written, line breaks kept, surrounding spaces removed, and standard wording that the Processing is the operations defined in Section 1.3 that are needed to provide the Services for that purpose, and nothing else. |
  | 2 | Annex 1: Categories of Data Subjects | 1 | Each category the requester ticked (Employees, Customers, Patients or trial subjects, Suppliers), named in the form's own words, and no other. |
  | 3 | Annex 1: Types of Personal Data | 1 | Each kind the requester ticked, and no other: Contact and identity as names, contact details and identification data; Employee / HR as employment and HR records; Health or other special category as health data and other special category data; Financial as financial and transaction data. |
  | 4 | Annex 1: Sensitive or special category Personal Data | 1 | WHEN "Health or other special category" is among the kinds ticked: a statement that special category data is processed, only for the stated purpose, with the measures in Annex 2 applied to it. Otherwise: "None", with a statement that no special category data is processed. |
  | 5 | Annex 1: Processing locations | 1 | WHEN the answer to "Will data leave India?" is No: India, with a statement that Personal Data is processed and accessed only there. WHEN it is Yes: India and the countries the requester listed, with a statement that processing or access in any country outside India is permitted only on the conditions of Section 9. |
  | 6 | Annex 3: Authorised sub-processors | 1 | WHEN the answer to "Will they use sub-processors?" is No: "None", with a statement that no Sub-processor is authorised at the Effective Date and that one may be engaged only with the Controller's prior specific written authorisation under Section 6.1. WHEN it is Yes: one entry per sub-processor with its name, its country and the processing it performs, the same statement that any other Sub-processor needs prior specific written authorisation, and a statement that a Sub-processor outside India processes only on the conditions of Section 9. |

- FR-9: Nothing the drafted annexes say in the six places SHALL depart from the positions the published
  Data Processing Agreement Playbook prefers: processing only on the Controller's documented
  instructions and for the stated purpose; Sub-processors only with the Controller's prior written
  authorisation, bound by the same obligations, with the Processor fully liable for them; no transfer
  outside India (or, for EU data, outside the EEA) without Standard Contractual Clauses or another lawful
  safeguard; and the security measures, return or deletion and audit rights left as the template states
  them.

**C. Rules for both templates**

- FR-10: Standard wording SHALL be part of the template's own wording, so that it is visible, editable,
  previewable and checked on the Templates page. Drafting SHALL NOT add standard wording that the
  template does not contain. Only text that varies with the request (the services, the lists, the fee
  statement, the categories, countries and sub-processors built from the answers) SHALL be supplied by
  fill-in fields.
- FR-11: Dates in a drafted agreement SHALL be written as drafts write other dates (for example "15
  December 2026"). Money SHALL be written with its currency and two decimals (for example "INR
  1,800,000.00"). Lists SHALL keep the order the requester entered them, name each entry once, and
  number deliverables D1, D2, ... always by that order.

**D. The Consultancy form's new questions**

- FR-12: WHEN the New agreement form's kind is Consultancy and the request is for our own paper, the form
  SHALL additionally ask: the deliverables; how the consultant is paid; the Consultant's key individuals;
  and the Company contact. For any other kind, or for Their paper, none of these SHALL be shown, asked or
  required, and what those forms accept today SHALL NOT change.
- FR-13: The deliverables SHALL be a table with the columns Deliverable name and Due date, both required
  in every row. The table SHALL need at least one row and allow at most 20, keep the order entered, and
  set the D-numbers by that order. A row left entirely empty SHALL be ignored; a row filled in part SHALL
  be refused, naming the row and the missing column. A name SHALL be 1 to 200 characters after leading
  and trailing spaces are removed and line breaks inside a cell are turned into single spaces. No two rows
  SHALL have the same name (compared ignoring capital letters and extra spaces); the refusal SHALL name
  both rows.
- FR-14: A due date SHALL fall on or after the "Start date" and, when the form has an "End date", on or
  before it, both included. WHEN the term has no end date (an evergreen term), only the start bound
  applies. A date outside the window SHALL be refused, naming the row and the window. WHEN the dates are
  changed after rows were entered, rows now outside the window SHALL be refused again at the next check,
  naming them. A due date inside the window but earlier than the day the request is filed SHALL be
  accepted; the wizard SHALL show a note naming the row that the date has passed, and the note SHALL NOT
  block the request.
- FR-15: A requester MAY say that there are no separate deliverables (advice only) instead of listing
  any, by an explicit tick. WHEN they do, the table SHALL NOT be asked or required and no rows SHALL be
  filed; the wizard SHALL keep any rows already typed while the form stays open and show them again if
  the tick is undone. WHEN they do not, FR-13 applies, so leaving the table empty is not the same as
  saying there are none.
- FR-16: The fee basis SHALL be a required choice of "Fixed fee", "Day rate" or "Hourly rate". For a rate
  basis the form SHALL also ask the rate, required, a number above 0 with at most two decimal places; for
  "Fixed fee" it SHALL NOT be shown, asked or required. The total value is the fixed fee for "Fixed fee"
  and the ceiling a rate cannot pass for a rate basis (the form asks for no separate cap). WHEN the
  choice is changed before filing, the wizard SHALL keep the typed rate while the form stays open, show
  it again if a rate basis is chosen again, and file no rate for "Fixed fee". WHEN a filed request's
  basis is updated to "Fixed fee", that update SHALL remove its rate; WHEN it is updated to a rate basis,
  a rate meeting this requirement is needed or the update is refused.
- FR-17: The Consultancy form SHALL refuse a total value of zero or less, so that the fee is always a
  stated amount. The same question on the other kinds of agreement SHALL keep accepting what it accepts
  today.
- FR-18: The Consultant's key individuals SHALL be a list of names, at least one and at most 10, each 1 to
  120 characters after tidying as in FR-13. An empty entry SHALL be ignored. No two entries SHALL be the
  same name (compared ignoring capital letters and extra spaces); the refusal SHALL name both. A name
  SHALL be typed as text. It SHALL NOT be looked up against people in the system, and entering a name
  SHALL NOT create an account, a permission, a notification or a link to any person.
- FR-19: The Company contact SHALL be a name and a role, both required, each 1 to 120 characters after
  tidying, typed as text and inert exactly as FR-18 says.
- FR-20: WHEN a free-text answer that reaches a Consultancy agreement (the scope of services of a
  Consultancy request, a deliverable name, a key individual's name, the contact's name or role) contains
  a blank bracket, filing SHALL be refused, naming the answer and the bracket and saying to reword it
  without square brackets. Round brackets and other punctuation SHALL be accepted. The scope question of
  the other kinds of agreement SHALL keep accepting what it accepts today.
- FR-21: The rules of FR-13 to FR-20 SHALL be applied identically however a Consultancy request is filed
  or changed: through the request wizard, through the AI assistant or any other programmatic route, and
  when a filed request's answers are updated. An answer the wizard accepts SHALL never be refused at
  filing, and the reverse.
- FR-22: The wizard SHALL show the new Consultancy questions on the same step as the other commercial
  terms, with: each control and cell labelled for people who use a keyboard or a screen reader; errors
  shown beside the answer and summarised on trying to continue, naming rows and columns; entries kept
  when the person moves between steps and when filing fails (the failure message SHALL name the row at
  fault); for the table and the list, add-row and remove-row controls (the last row cannot be removed;
  add-row is unavailable at the maximum and says why); and the form's existing loading and error states,
  with no half-built control shown while the form's questions load or when they fail to load.
- FR-23: WHEN a Consultancy request's answers are shown (the wizard's review step, the request page and
  approvers' views), the new answers SHALL appear as readable rows, lists and labelled values, never as
  raw data. The advisory read of the request SHALL be given them in the same readable form. None of these
  SHALL stop working because an answer is now a table or a list. For a Consultancy request the review
  step SHALL name the Consultancy Agreement template as what will be drafted (it names the Master
  Services Agreement template today).

**E. The DPA form's new questions**

- FR-24: WHEN the request is for our own paper, "Whose data?" and "What kind of personal data?" SHALL each
  let the requester tick more than one of the specific choices, SHALL need at least one, and SHALL NOT
  offer "Several" any more. This holds whatever the answer to "Our role" (FR-53 to FR-56 decide only
  whether a draft is made).
- FR-25: WHEN the answer to "Will data leave India?" is Yes, the form SHALL ask which countries outside
  India the data goes to or is accessed from: a list of at least one and at most 20, each 1 to 100
  characters after tidying as in FR-13. An empty entry SHALL be ignored. No two entries SHALL be the same
  (compared ignoring capital letters and extra spaces), the refusal naming both, and an entry reading
  "India" SHALL be refused. WHEN the answer is No, the question SHALL NOT be shown, asked or required.
- FR-26: WHEN the answer to "Will they use sub-processors?" is Yes, the form SHALL ask for a table of
  sub-processors with the columns Sub-processor name (1 to 200 characters), Country (1 to 100) and What
  it does (1 to 300), all required in every row, at least one row and at most 20, rows in the order
  entered. A row left entirely empty SHALL be ignored; a row filled in part SHALL be refused naming the
  row and the missing column; no two rows SHALL have the same name (compared ignoring capital letters and
  extra spaces), the refusal naming both. Names are text and inert as FR-18 says. WHEN the answer is No,
  the table SHALL NOT be shown, asked or required.
- FR-27: WHEN the answer to "Will data leave India?" is No, a sub-processor whose country is not India
  (compared ignoring capital letters and extra spaces) SHALL be refused, naming the row and saying that
  the answer about data leaving India says otherwise.
- FR-28: WHEN a free-text answer that reaches a DPA (the processing purpose, a country, a sub-processor's
  name, country or what it does) contains a blank bracket, filing SHALL be refused as FR-20 says.
- FR-29: WHEN "Will data leave India?" or "Will they use sub-processors?" is changed to No before filing,
  the wizard SHALL hide the follow-up, keep what was typed while the form stays open, show it again if
  Yes is chosen again, and file none of it. A request filed with No SHALL hold no countries or
  sub-processors. WHEN a filed request's answer is updated to No, that update SHALL remove them; WHEN it
  is updated to Yes, entries meeting FR-25 or FR-26 are needed or the update is refused.
- FR-30: The rules of FR-24 to FR-29 SHALL be applied identically however a DPA request is filed or
  changed, as FR-21 says for Consultancy.
- FR-31: The wizard SHALL show the new DPA questions on the step that holds the other data questions,
  with the behaviour FR-22 lists.
- FR-32: WHEN a DPA request's answers are shown (the wizard's review step, the request page and approvers'
  views), the ticked categories and kinds, the countries and the sub-processors SHALL appear as readable
  lists and rows, never as raw data, and the advisory read SHALL be given them in the same form. None of
  these SHALL stop working because an answer can now hold several choices. The review step SHALL keep
  naming the DPA template as what will be drafted.

**F. The Templates page and the checks (builds on 006 and 007)**

- FR-33: WHEN a template is read as an example reading (the Templates page check, which reads a request
  with no answers, and the page's preview), each new fill-in field SHALL give a complete, blank-free
  example that together makes a consistent agreement. For Consultancy: at least one deliverable with a
  name and a due date inside the example term, a fee basis with its amounts (a rate with the ceiling, or
  a fixed fee), at least one key individual and a Company contact. For the DPA: a purpose, at least two
  categories of people, at least two kinds of data including special category data with the matching
  sensitive-data statement, at least one country outside India with the Section 9 statement, and at
  least one sub-processor with its name, country and processing.
- FR-34: Example content SHALL be used only for the Templates page check and preview. A draft made for a
  real request SHALL NEVER contain example content, including when the request lacks an answer.
- FR-35: Every new fill-in field SHALL appear in the Templates page's list of fields with a plain name,
  what it fills in and an example, and SHALL be insertable from that list (006 FR-62). A wording that
  uses one SHALL pass the same "drafting can fill it" validation as any other field (006 FR-34), and a
  name not in the list SHALL still be refused. The list SHALL NOT offer two fields that fill the same
  thing under different names.
- FR-36: The Consultancy and DPA templates SHALL stay editable on the page, and every action 006 gives a
  template (save as a new version, preview, check, reset to the shipped wording, load back an earlier
  version) SHALL work for them, with the same audit and confirmation behaviour.
- FR-37: WHEN this feature is released, each of the two templates on its shipped wording SHALL show the
  status Out of date, saying the wording changed (006 FR-6), if its last completed check covered the old
  shipped wording, and SHALL stay Never checked if it was never checked. The release SHALL start no paid
  check by itself. An organisation's own saved wording SHALL NOT be changed by the release and SHALL
  keep drafting as it does today; a saved wording that uses none of the new fields simply does not use
  the new answers. Resetting to the shipped wording (006 FR-56) SHALL give the new wording as a new
  version.
- FR-38: A check of the new shipped Consultancy wording and of the new shipped DPA wording SHALL read text
  with no blank bracket and no unfilled field, and no finding it reports SHALL quote a bracketed blank or
  be caused by one. The verification report SHALL record the actual findings of one owner-approved check
  of each.
- FR-39: The page SHALL keep stating wherever a check result is shown that the check is an AI review that
  can report different findings on identical wording, that Passes is not a guarantee about any
  contract, and that a requester's form answers are not covered (006 FR-10). This feature SHALL NOT
  claim that either template will Pass.

**G. Older requests, email and chat requests, saved forms, drafts already made**

- FR-40: A Consultancy agreement or DPA already drafted SHALL never change because of this feature, or
  because the shipped wording or a saved wording is later changed (006 FR-45). This feature SHALL NOT
  re-draft, rewrite or flag any drafted agreement.
- FR-41: An older request SHALL keep its answers exactly as filed, including a "Several" choice, and show
  them as they were. The system SHALL NOT convert, delete or rewrite them.
- FR-42: Updating an older request for any reason that does not touch the new answers (for example its
  priority, department or another answer) SHALL NOT be refused because the new answers are missing or
  because the request holds the earlier single choices. An update that sets the new answers SHALL meet
  the rules in sections D and E.
- FR-43: WHEN a draft is asked for, by a person or by a workflow, from a request for our own paper that
  lacks an answer, drafting SHALL NOT proceed (a held draft) and SHALL create no contract. The person
  SHALL be told which answers are needed. For a request filed on the form, the people who may change that
  request today SHALL be able to enter the answers on the request, and once they meet the rules in
  sections D and E the draft SHALL be made from them. For a DPA request that came by email or chat and
  has no form answers, the person SHALL be told to file the request on the DPA form. The system SHALL NOT
  supply a missing answer by any means, and a draft SHALL NOT contain example content or any deliverable,
  name, country or sub-processor the requester did not give. A request that lacks no answer (for example
  a DPA request that ticks one kind of people and one kind of data and answers No to data leaving India
  and No to sub-processors) SHALL be drafted at once. A Processor request is never held (FR-56).
- FR-44: WHEN a workflow starts the draft of a held request, the workflow's step SHALL stop and show the
  reason. The step SHALL NOT be skipped or marked complete, SHALL NOT make a contract, and SHALL be able
  to continue once the answers are entered, without the request being filed again.
- FR-45: A saved form SHALL reopen with the new questions empty. A saved DPA form that holds a "Several"
  answer SHALL show that earlier answer beside the question for reference. Nothing SHALL be lost, and the
  form SHALL NOT be submittable until the rules in sections D and E are met.

**H. Other routes, other templates and the exam**

- FR-46: This feature SHALL NOT change how a request is recognised, routed or which template it is
  drafted from, except that a Processor request is not drafted from the DPA template (FR-53 to FR-56). A
  free-text request (email, chat) that says "DPA" or "data processing agreement" is still drafted from the
  DPA template, and FR-43 applies to it.
- FR-47: This feature SHALL NOT change what any other shipped template (NDA, Master Services Agreement,
  Software/SaaS, Vendor, Statement of Work) drafts.
- FR-48: The AI quality exam's documents drafted from the Consultancy and DPA templates (the review
  documents, the DPA schedule of the long documents, and the portfolio contracts made from them), their
  planted-change positions, their recorded hashes and the 005 baseline SHALL be exactly as before this
  feature. The exam SHALL keep drafting them from the wording as it stood on 2026-10-15.

**I. Records**

- FR-49: Each Consultancy agreement or DPA drafted from a template SHALL record, in the draft's history
  and in the audit trail alongside what 006 FR-46 records: for a Consultancy agreement, the number of
  deliverable rows (0 where there are none), the fee basis and the number of key individuals; for a DPA,
  the number of countries outside India, the number of sub-processors and whether special category data
  is stated.
- FR-50: A new filing's audit record SHALL include the new answers exactly as filed. Setting or changing
  any of the new answers of a filed request, including answers removed or required because another
  answer changed (FR-16, FR-29) and answers entered for an older request, SHALL be recorded in the
  request's history and in the audit trail: who, when, and the answers before and after.

**J. Permissions and scoping** (details in the next section)

- FR-51: This feature SHALL NOT introduce a permission. Who may file a Consultancy or DPA request, change
  a filed request's answers, draft from it, and use each Templates page action stays as it is today.
- FR-52: The new answers belong to the request they were entered on and are visible only to people who may
  see that request, under the same ethical walls and grants as the request's other answers and the
  drafted agreement. Names typed into an answer never grant or reveal access.

**K. A DPA request that says we are the Processor**

Our DPA template is written for us as the Controller and the other party as the Processor, and drafting
has never read the answer to "Our role". A request that says we are the Processor would therefore be
drafted on the wrong side's paper. The owner decided (D-10, D-11) that no draft is made from our template
for it, and that the person is told to attach the other side's DPA (or the paper to use) before the
review.

- FR-53: WHEN a person asks for the draft of a Processor request that has no contract yet, no draft SHALL
  be made from the DPA template and no contract SHALL be created. The person SHALL be told, in plain
  words, that no draft was made, that the request says we are the Processor, that our DPA template is
  written for the Controller, and that they should attach the other side's DPA (or the paper to use)
  before the review. The message SHALL NOT say who prepares the paper. This refusal changes nothing, so
  it writes no audit or history record (as a held draft does, FR-43).
- FR-54: WHEN a workflow's drafting step would draft a Processor request from the template, the step SHALL
  be skipped, not failed, not left waiting and not marked done, with a note that says the same as the
  message of FR-53, and the workflow SHALL go on to its next step. No contract SHALL be made and no
  AI-written bespoke draft SHALL be created in its place. This holds when the step starts with no person
  present. A drafting step that is set to wait for a person to draft, or to wait for the requester's own
  document, is unaffected.
- FR-55: WHEN a drafting step is skipped under FR-54, the request's history SHALL record that no draft was
  made because the request says we are the Processor and that the other side's DPA (or the paper to use)
  is to be attached before the review, and the audit trail SHALL record it: who (the workflow, as the
  system), when, the request, and the reason.
- FR-56: The Processor rule SHALL be applied before the hold of FR-43: a Processor request is never held
  for missing answers, and its person is not asked to enter answers for a draft that will not be made. It
  SHALL NOT stop a bespoke draft or an uploaded document being made by any means open today, SHALL NOT
  change a request that already has a contract, and SHALL NOT apply to a request whose answer to "Our
  role" is the Controller choice, or that has no such answer (FR-43 applies to that).

## Permissions, scoping & audit

This platform has role-based access, ethical walls and an immutable audit trail. This feature changes what
a request records and what a draft contains. It adds no action that did not exist.

**Who may do what**

| Action | Who may perform it |
|---|---|
| File a Consultancy or DPA request, including the new answers | The people who may file a request today (FR-51) |
| Change the answers of a filed request; enter missing answers for an older request | The people who may change that request today (FR-51) |
| See a request's new answers and the drafted agreement | The people who may see that request or contract today, with ethical walls and grants applied unchanged (FR-52) |
| Draft a Consultancy agreement or DPA from a request | The people, and the workflows, that may draft today (FR-51); a Processor request is not drafted by anyone from the DPA template (FR-53, FR-54) |
| See the two templates, their fields, versions, checks and downloads | People who may view playbooks (006 FR-66; today administrators and legal reviewers) |
| Check a template | People who may run playbook reviews (006 FR-67) |
| Save, reset, load back or otherwise change a template's wording | People who may edit playbooks (006 FR-68; today administrators only) |
| A release that changes the shipped wording | Not a user action; its effect is the templates' Out of date status (FR-37) |

**Scoping**

- A request, its new answers and every drafted agreement belong to one organisation and are visible only
  within it, and only to people who may see that request (FR-52). The shipped wording is the same for
  every organisation; an organisation's saved wording belongs to it alone (006 FR-69).
- Names (key individuals, the Company contact, sub-processors) are free text. They are not matched to
  users or to the counterparty register, so no cross-organisation or cross-wall information can leak
  through them, and typing a person's name confers nothing (FR-18, FR-19, FR-26, FR-52).
- A check sends only the template filled with example content to the AI provider. No real request,
  counterparty or personal data is involved (006 scoping unchanged).

**Audit trail.** Each of these SHALL be recorded with who, when, the request or template, and what
changed. No user can edit or delete the record. Names typed into an answer are recorded with the answer,
as the trail records any answer.

- A Consultancy or DPA request filed: the filing record includes the new answers exactly as filed (FR-50).
- The new answers of a filed request set or changed, including answers removed or required because another
  answer changed, and answers entered for an older request: who, when, answers before and after (FR-50).
- A Consultancy agreement or DPA drafted: everything 006 FR-46 records (template, version, status at that
  moment) plus the counts in FR-49.
- A workflow's drafting step skipped because the request says we are the Processor: the request's history
  and the audit trail, as FR-55 says.
- Every Templates page action on the two templates: as 006 records it, unchanged.
- A filing refused for failing the rules, a held draft (FR-43) and a person's refused draft of a Processor
  request (FR-53) change nothing, so they are not audit events. Entering the answers that release a held
  draft is (FR-50).

## Acceptance criteria

Given/When/Then scenarios that define "done". Every criterion maps to at least one functional
requirement. AC-58 to AC-61 were added with FR-53 to FR-56. Unless stated, the example Consultancy request
is for our own paper, with start date 2026-11-01, end date 2027-04-30, total value 1,800,000 INR, payment
terms 30 days, the scope "Advise on the design of the new quality-management process.", fee basis "Fixed
fee", two deliverables "Current-state review" due 2026-12-15 and "Process design report" due 2027-03-15,
the key individuals Anita Rao and Dev Menon, and the Company contact Ravi Shah, Head of Quality. The
example DPA request is for our own paper, with "Our role" answered "Controller: we decide how the data is
used", the purpose "Hosting and support of the clinical-trial site portal.", the people Customers and
Patients or trial subjects, the kinds Contact and identity and Health or other special category, data
leaving India with the countries Singapore and Germany, and sub-processors "CloudHost Pte Ltd"
(Singapore, "Hosting of the portal database") and "SupportDesk GmbH" (Germany, "Second-line support"). The
plain DPA request has the same role and purpose, the people Employees, the kind Contact and identity, No
to data leaving India and No to sub-processors. The Processor DPA request is the plain DPA request with
"Our role" answered "Processor: we act on their instructions".

- AC-1 (FR-1): Given the shipped Consultancy wording, when the application's blank check is run on it,
  then it finds no blank bracket (it finds 5 in today's wording).
- AC-2 (FR-2): Given the example Consultancy request, when an agreement is drafted from the shipped
  wording, then it has no blank bracket by the blank check, no curly brace or fill-in code and no
  instruction to the reader; and given each of "Day rate" at INR 25,000 and "Hourly rate" at INR 3,500 in
  turn, and given the choice of no separate deliverables, then the same holds.
- AC-3 (FR-3): Given the example request, when the drafted Engagement Schedule is read, then Services
  holds exactly "Advise on the design of the new quality-management process."; Deliverables and timetable
  holds D1 "Current-state review" due 15 December 2026 and D2 "Process design report" due 15 March 2027;
  the key individuals place names Anita Rao and Dev Menon once each; and the Company contact place reads
  Ravi Shah, Head of Quality.
- AC-4 (FR-3): Given the request says there are no separate deliverables, when the schedule is read, then
  Deliverables and timetable states that none are specified and refers to the reports under Clause 2.2,
  and holds no D-numbered entry.
- AC-5 (FR-4): Given "Fixed fee", when the Fees entry is read, then it states a fixed fee of INR
  1,800,000.00 and that invoicing and payment follow Clauses 6.2 to 6.4; given "Day rate" at INR 25,000,
  then it states a day rate of INR 25,000.00 per day and that the Fees do not exceed INR 1,800,000.00
  except by a written amendment signed by both Parties; given "Hourly rate" at INR 3,500, then likewise
  per hour; and none shows more than one basis as an alternative or provides for payment in advance.
- AC-6 (FR-5): Given a drafted Consultancy agreement for each fee basis, when the five places are read,
  then none says "payable in advance" or an advance above 20%, none has the Company direct or supervise
  how, when or where the Consultant works or provide its tools, equipment, leave or benefits, none lets
  fees exceed the stated amount without a signed written amendment, and the key individuals place says
  they are not replaced without the Company's prior written consent.
- AC-7 (FR-6): Given the shipped DPA wording, when the blank check is run on it, then it finds no blank
  bracket (it finds 6 in today's wording).
- AC-8 (FR-7): Given the example DPA request and the plain DPA request, when a DPA is drafted from the
  shipped wording, then each has no blank bracket, no curly brace or fill-in code and no instruction to
  the reader.
- AC-9 (FR-8): Given the example DPA request, when Annex 1 is read, then the purpose place holds "Hosting
  and support of the clinical-trial site portal." and the standard wording that the Processing is the
  Section 1.3 operations needed for that purpose and nothing else; the categories place names Customers
  and Patients or trial subjects and no other; and the types place names contact and identity data and
  health data and other special category data and no other.
- AC-10 (FR-8): Given the example DPA request, when the sensitive-data place is read, then it states that
  special category data is processed, only for the stated purpose, with the Annex 2 measures applied; and
  given the plain request, then it reads "None" with the statement that no special category data is
  processed.
- AC-11 (FR-8): Given the example DPA request, when the processing locations place is read, then it names
  India, Singapore and Germany and states that processing or access outside India is permitted only on the
  conditions of Section 9; and given the plain request, then it reads India only, with the statement that
  Personal Data is processed and accessed only there.
- AC-12 (FR-8): Given the example DPA request, when Annex 3 is read, then it holds one entry for "CloudHost
  Pte Ltd" (Singapore, hosting of the portal database) and one for "SupportDesk GmbH" (Germany,
  second-line support), the statement that any other Sub-processor needs prior specific written
  authorisation, and the statement that a Sub-processor outside India processes only on the conditions of
  Section 9; and given the plain request, then it reads "None" with the statement that none is authorised
  at the Effective Date.
- AC-13 (FR-9): Given the example and the plain DPA, when the six places are read, then none permits
  processing beyond the stated purpose or the Controller's documented instructions, none authorises a
  transfer outside India without the Section 9 conditions, none authorises a Sub-processor other than
  those listed or without prior specific written authorisation, and none weakens the Annex 2 measures,
  the return or deletion terms or the audit rights.
- AC-14 (FR-10): Given a person who may change templates opens the Consultancy template, when they read
  its wording, then the standard wording on key individuals is visible in the wording; given they open the
  DPA template, then the Section 9 and sub-processor authorisation statements are visible; and given they
  change a standard sentence and save, then the next draft uses their sentence.
- AC-15 (FR-11): Given the example Consultancy request, when the schedule is read, then dates read "15
  December 2026" style, the fixed fee reads "INR 1,800,000.00", and D1 and D2 follow the order the rows
  were entered; and given the example DPA request, then the countries and sub-processors appear in the
  order entered, each once.
- AC-16 (FR-12): Given the New agreement form with kind Consultancy and "Our template", when the
  commercial-terms step is opened, then it asks for the deliverables, the fee basis, the key individuals
  and the Company contact; and given "Their paper", or the kind Services (MSA), NDA, Software or SaaS,
  Buying from a vendor, Selling to a customer or Something else, then none of them is shown, asked or
  required and the form accepts what it accepts today.
- AC-17 (FR-13): Given the deliverables table, when the requester files with no rows, then it is refused;
  with one row, then it is accepted; with 20 rows, then it is accepted; with a 21st row, then it is
  refused and add-row is unavailable and says why; with an entirely empty row among filled ones, then it
  is ignored; with a row missing its due date, then it is refused naming the row and the column; with a
  name of 201 characters, then it is refused; with "Process design report" and "process  design  report"
  in two rows, then it is refused naming both rows; and rows keep their entered order and set D1 onward.
- AC-18 (FR-14): Given start 2026-11-01 and end 2027-04-30, when a due date is 2026-11-01 or 2027-04-30,
  then it is accepted; when it is 2026-10-31 or 2027-05-01, then it is refused naming the row and the
  window; given the term has no end date, then 2030-01-01 is accepted and 2026-10-31 is refused; given the
  end date is then moved to 2027-02-01, then the "Process design report" row is refused again naming it;
  and given the request is filed on 2026-12-20 with a row due 2026-12-15, then it is accepted, the wizard
  showed a note naming that row that the date has passed, and the note did not block filing.
- AC-19 (FR-15): Given the requester ticks that there are no separate deliverables, when they file, then
  no table is asked or filed and the request is accepted; given they had typed rows before, then the rows
  were kept in the open form, are not filed, and reappear if the tick is undone; and given they do not
  tick it and list no rows, then FR-13 refuses it.
- AC-20 (FR-16): Given "Fixed fee", when the form is opened, then no rate is shown; given "Day rate" or
  "Hourly rate", then a rate is required and a request with none is refused naming the question; given a
  rate of 0, -5 or 25.001, then it is refused; given 25000 or 25000.50, then it is accepted; given the
  requester switches from "Day rate" at 25,000 to "Fixed fee" before filing, then the rate is hidden and
  not filed and returns if they switch back; given a filed "Day rate" request is updated to "Fixed fee",
  then its rate is removed and the audit trail shows it before and after; and given a filed "Fixed fee"
  request is updated to "Hourly rate", then the update is refused until a rate is given.
- AC-21 (FR-17): Given a Consultancy request with a total value of 0 or -5, when it is filed, then it is
  refused; given 1, then it is accepted; and given a Services (MSA) request with the value 0, then the
  form accepts it as it does today.
- AC-22 (FR-18): Given the key individuals list, when it is empty, then filing is refused; with 10
  names, then it is accepted; with an 11th, then it is refused; with "Anita Rao" and "anita  rao", then it
  is refused naming both; with a name of 121 characters, then it is refused; with an empty entry among
  filled ones, then the empty entry is ignored; and given a name that matches a user in the system, then
  no access, notification, link or account results and no lookup is made.
- AC-23 (FR-19): Given the Company contact, when the role is missing, then filing is refused naming the
  question; when the name or the role is 121 characters, then it is refused; and given "Ravi Shah" with
  "Head of Quality (acting)", then it is accepted.
- AC-24 (FR-20): Given a Consultancy request whose scope is "Advise on [TBD] processes", or a deliverable
  name "[Insert name]", or a key individual "[Name]", or a contact role "[Role]", when it is filed, then
  it is refused naming the answer and the bracket; given "Advise on (phase 1) processes", then it is
  accepted; and given a Services (MSA) request with the scope "Advise on [TBD] processes", then the form
  accepts it as it does today.
- AC-25 (FR-21): Given the same invalid Consultancy answers (a due date outside the window), when they
  are sent through the wizard, through the AI assistant, as a programmatic filing, and as an update to a
  filed request, then each is refused with the same message; and given answers the wizard accepts, then
  filing accepts them.
- AC-26 (FR-22): Given the wizard, when the questions are loading, then no new control is shown; when
  loading fails, then the form's error state with a retry is shown; when filing fails, then every entry
  is still there and the message names the row at fault; when the person uses only a keyboard, then they
  can add, fill and remove rows and entries and reach every error; and when they try to continue with
  errors, then a summary names the rows and columns.
- AC-27 (FR-23): Given a filed example Consultancy request, when the review step, the request page and an
  approver's view are opened, then each shows the deliverables as rows, the key individuals as a list, the
  fee basis with its rate, and the contact, none shows raw data, and none fails; the advisory read is given
  the same readable form and still completes; and the review step says the Consultancy Agreement template
  will be drafted.
- AC-28 (FR-24): Given the DPA form for our own paper, when it is opened, then "Whose data?" and "What
  kind of personal data?" accept several ticks and no "Several" choice is offered; given Customers and
  Patients or trial subjects, then it is accepted; given none ticked, then filing is refused naming the
  question; and given the same form with "Our role" answered "Processor: we act on their instructions",
  then the same holds.
- AC-29 (FR-25): Given the answer Yes to data leaving India, when the form is opened, then it asks for
  countries; given Singapore and Germany, then it is accepted; given none, then it is refused; given
  "Singapore" and "singapore ", then it is refused naming both; given "India", then it is refused; given
  21 entries, then it is refused; and given the answer No, then the question is not shown.
- AC-30 (FR-26): Given the answer Yes to sub-processors, when the table is filled, then a row missing its
  country is refused naming the row and the column; two rows named "CloudHost Pte Ltd" and "cloudhost  pte
  ltd" are refused naming both; 20 rows are accepted and a 21st is refused; and given the answer No, then
  the table is not shown.
- AC-31 (FR-27): Given No to data leaving India, when a sub-processor is in Singapore, then the request is
  refused naming the row and saying the answer about data leaving India says otherwise; given the country
  "india", then it is accepted; and given Yes to data leaving India, then a sub-processor in Singapore is
  accepted whether or not Singapore is in the countries list.
- AC-32 (FR-28): Given a DPA request whose purpose is "Hosting of [TBD] systems", or a country "[Country]",
  or a sub-processor name "[Name]", when it is filed, then it is refused naming the answer and the
  bracket; and given "Hosting of (phase 1) systems", then it is accepted.
- AC-33 (FR-29): Given Yes to sub-processors with two rows typed, when the person changes the answer to No
  and files, then nothing about sub-processors is filed and the stored request holds none, and changing
  back to Yes before filing brings the rows back; given a filed Yes request is updated to No, then its
  countries and sub-processors are removed and the audit trail shows them before and after; and given a
  filed No request is updated to Yes, then the update is refused until entries are given. The same holds
  for the countries and the answer about data leaving India.
- AC-34 (FR-30): Given the same invalid DPA answers (a sub-processor row with no name), when they are sent
  through the wizard, through the AI assistant, as a programmatic filing, and as an update, then each is
  refused with the same message.
- AC-35 (FR-31): Given the DPA wizard, when the questions load, fail to load, filing fails, or the person
  uses only a keyboard, then the behaviour is as AC-26 describes.
- AC-36 (FR-32): Given a filed example DPA request, when the review step, the request page and an
  approver's view are opened, then each shows the ticked categories and kinds as lists, the countries as a
  list and the sub-processors as rows, none shows raw data, none fails, the advisory read completes, and
  the review step names the DPA template.
- AC-37 (FR-33): Given the Templates page, when the check of the Consultancy template is read and the
  preview is opened, then the example reading holds a deliverable with name and due date inside the example
  term, a fee basis with its amounts, a key individual and a contact, with no blank bracket; and given the
  DPA template, then its example reading holds a purpose, two categories, two kinds including special
  category data with the matching sensitive-data statement, a country outside India with the Section 9
  statement, and a sub-processor with name, country and processing, with no blank bracket.
- AC-38 (FR-34): Given the example reading's names, deliverables, amounts, countries and sub-processors,
  when a Consultancy agreement or DPA is drafted for any real request (including one that lacks an
  answer, which is held and makes no draft), then none of that example content is present.
- AC-39 (FR-35): Given the Templates page, when the field list is opened, then every new field shows a
  plain name, what it fills in and an example, and no two fields fill the same thing; when one is inserted
  into a wording and saved, then it saves; and when a name not in the list is typed, then saving is
  refused as before.
- AC-40 (FR-36): Given a person who may change templates, when they edit the Consultancy and the DPA
  templates, save, preview, check, reset, and load back an earlier version, then each works with the same
  confirmation and audit behaviour as for any template.
- AC-41 (FR-37): Given the release and a Consultancy or DPA template whose last completed check covered the
  old shipped wording, when the Templates page is opened, then it shows Out of date saying the wording
  changed, no check has started and no AI usage was recorded; given a template never checked, then it
  still shows Never checked; given an organisation with a saved Consultancy wording that still has brackets
  and none of the new fields, then it still drafts exactly as before and is not held; and given that
  organisation resets to the shipped wording, then a new version holds the new blank-free wording.
- AC-42 (FR-38): Given a check of the new shipped Consultancy wording and of the new shipped DPA wording,
  when each completes, then the text it read has no blank bracket and no finding quotes a bracketed blank
  or is caused by one; and the verification report records each check's actual findings, whatever they
  are.
- AC-43 (FR-39): Given any view of a check result for the two templates, then the page states that AI
  reviews can differ on identical wording, that Passes is not a guarantee, and that form answers are not
  covered; and nothing in the page or the release notes says either template will Pass.
- AC-44 (FR-40): Given Consultancy agreements and DPAs drafted before this feature, when the feature is
  released and a template is later edited or reset, then those drafted agreements' text is identical to
  before.
- AC-45 (FR-41, FR-42): Given an older DPA request that holds the earlier single choice "Several" for
  whose data, when it is opened after the release, then the choice is shown unchanged and the stored answer
  is unchanged; and given its priority or another answer is then updated, then the update succeeds; and
  given the new answers are set to values that break a rule, then the update is refused as in AC-25 and
  AC-34.
- AC-46 (FR-43): Given an older Consultancy request and the shipped wording, when a person asks for the
  draft, then no contract is created and they are told which answers are needed; given a person who may
  change that request then enters answers that meet the rules, when the draft is made, then it holds
  exactly those answers and no example content or other deliverable, name, country or sub-processor; and
  given nothing converts the earlier answers, then they are still unchanged afterwards.
- AC-47 (FR-43): Given an older DPA request (role Controller) that ticks Customers, Contact and identity,
  No to data leaving India and No to sub-processors, when a person asks for the draft, then it is made at
  once with no hold; given one that holds "Several" or Yes to sub-processors, then it is held and the
  person is told which answers are needed; and given an organisation's saved wording that uses none of the
  new fields, then the same older requests draft as today with no hold.
- AC-48 (FR-43): Given a DPA request that came by email saying "please draft a DPA" and has no form
  answers, when a person asks for the draft, then no contract is created and the person is told to file
  the request on the DPA form; and given a request whose answer to "Whose paper?" is Their paper, then
  it is not held for the new answers.
- AC-49 (FR-44): Given a workflow reaches its drafting step for a held request, when the step runs, then
  it stops showing the reason, it is not skipped or marked complete, and no contract is made; and given a
  person then enters valid answers, then the step can continue and the draft is made without the request
  being filed again.
- AC-50 (FR-45): Given a saved DPA form that holds "Several" for whose data, when it is reopened, then the
  question is empty, the earlier answer is shown beside it, nothing is lost, and the form cannot be
  submitted until the question is answered; and given a saved Consultancy form, then the new questions
  show empty and are required before it can be submitted.
- AC-51 (FR-46): Given a request filed by email whose text says "need a DPA for GDPR", when it is routed
  before and after the release, then the same template is chosen (the DPA template) and FR-43 applies to
  its draft; given a request filed on the New agreement form with kind Consultancy, then it is drafted
  from the Consultancy template; and given a Processor request, then it is still recognised as a DPA
  request but is drafted by no one from the template (AC-58 to AC-61).
- AC-52 (FR-47): Given the NDA, Master Services Agreement, Software/SaaS, Vendor and Statement of Work
  templates, when a draft is made before and after the release, then the drafts are identical.
- AC-53 (FR-48): Given the AI quality exam's committed content, when its content tests are run after the
  release, then they pass unchanged, the documents drafted from the Consultancy and DPA templates are
  byte-for-byte what they were, and no recorded hash or planted-change position has moved.
- AC-54 (FR-49): Given the example Consultancy request is drafted, when the draft's history and the audit
  trail are read, then both state the template, its version and its status at that moment (006 FR-46),
  and also 2 deliverable rows, the fee basis "Fixed fee" and 2 key individuals; and given the example DPA
  request is drafted, then both state 2 countries outside India, 2 sub-processors and that special
  category data is stated.
- AC-55 (FR-50): Given a filed Consultancy request, when its deliverables are changed from two rows to
  one, then the request's history and the audit trail show who, when, and the two rows before and the one
  after, and no user can edit or delete that audit record; and given a DPA request is filed, then its
  filing record holds the ticked categories and kinds, countries and sub-processors exactly as filed.
- AC-56 (FR-51): Given a person who may not file requests, when they try to file a Consultancy or DPA
  request, then it is refused as today; given a legal reviewer, when they try to save the Consultancy
  template, then it is refused as in 006; and given the application's list of permissions before and
  after this feature, then the list is the same.
- AC-57 (FR-52): Given two organisations, when a person in one asks for the other's request, its new
  answers or its drafted agreement, then it is not found; given a person walled off from a contract, then
  they cannot see its drafted terms or the request's new answers; and given a key individual or contact
  whose name equals a user's name, then it reveals or grants nothing.
- AC-58 (FR-53): Given the Processor DPA request with no contract, when a person asks for the draft, then
  no contract and no stored document is created, and the person is told that no draft was made, that the
  request says we are the Processor, that our DPA template is written for the Controller, and that they
  should attach the other side's DPA (or the paper to use) before the review, with no claim about who
  prepares the paper; the audit trail and the request's history hold no new record of the refusal;
  and given the plain DPA request (role Controller), then asking for the draft makes it as in AC-8.
- AC-59 (FR-54): Given a workflow reaches its drafting step for the Processor DPA request, when the step
  runs, with or without a person present, then the step is skipped (not failed, not waiting, not done)
  with a note that says the same as the message of AC-58, no contract is made, no AI-written bespoke draft is
  created, and the workflow goes on to its next step; and given a drafting step set to wait for a person to
  draft, or for the requester's own document, then it behaves as today.
- AC-60 (FR-55): Given the step was skipped for the Processor DPA request, when the request's history and
  the audit trail are read, then both record that no draft was made because the request says we are the
  Processor and that the other side's DPA (or the paper to use) is to be attached before the review, with
  the workflow as the actor, the time and the request; and no user can edit or delete the audit record.
- AC-61 (FR-56): Given a Processor DPA request that also lacks answers (for example an older request with
  "Several"), when a person asks for the draft, then they get the Processor message of AC-58 and not the
  held-draft message, and they are not asked to enter answers; given a bespoke draft or an uploaded
  document is then made, then that works as today; given a Processor request that
  already has a contract, then nothing about it changes; and given a DPA request with no answer to "Our
  role" (it came by email), then FR-43 applies and not the Processor rule.

## Out of scope

- The Statement of Work, NDA, Master Services Agreement, Software/SaaS and Vendor templates are not changed
  (FR-47). They have no blank brackets, or (for the Statement of Work) were fixed by 007.
- Re-drafting, correcting or flagging agreements already drafted (FR-40), including filling in the blanks
  of an existing Consultancy or DPA draft.
- How a free-text request (email, chat) is recognised, routed or drafted (FR-46). Only the hold of FR-43
  applies to its draft.
- A Processor-side DPA template, or a template that reads "Our role" and writes the paper for either side
  (D-10). FR-53 to FR-56 only stop a wrong-side draft and say what to attach.
- Who picks up a Processor request, any new assignment or routing for it, and checking that the other
  side's DPA was attached, beyond the workflow going on to its next step (FR-54, D-11).
- Warning about, blocking, stripping or migrating blank brackets in an organisation's own saved template
  wording. The shipped wording ships without them (FR-1, FR-6); an organisation's edit is its own, and the
  existing blank check still catches blanks before approval and signature.
- Converting an older request's earlier answers into the new ones, by the system or by AI (FR-41, FR-43).
- Categories of people or kinds of data beyond those the DPA form offers (for example website visitors, or
  a description of a special category), and free-text "other" boxes for them. A case the form cannot
  describe is not served by this feature.
- Tracking deliverables after drafting: reminders for due dates, obligations created from rows, progress,
  acceptance, notifications, and linking names to people or to the counterparty register.
- Importing from a spreadsheet or file; per-deliverable descriptions, fees or acceptance criteria; a
  separate rate card; several rates for one engagement; a DPA annex for a processing activity per
  sub-processor beyond name, country and what it does.
- Changing the playbooks' rules, the AI review's prompts or models, or making the review repeatable.
  Optional or conditional clauses inside a template.
- The DPA's "Address for notices" lines, naming the Principal Agreement in the draft, and the signature
  blocks' empty lines (Known issues).
- Bespoke drafts that Legal writes or the AI drafts for "Selling to a customer" and "Something else"
  requests, which are not template drafts and carry their own placeholders.
- The wizard's "what will be drafted" text for kinds other than Consultancy (Software or SaaS still names
  the Vendor template).
- Word-formatted drafts and Word or PDF template upload (006 Phases 2 and 3). Fields added here are
  ordinary fill-in fields and follow whatever the page supports at the time.
- Currencies beyond the form's four, tax, languages other than English, a global redesign, new
  navigation, a new theme or new shared components.
- Changing who approves a Consultancy or DPA request or which workflow it uses.

**Known issues (found, not fixed here)**

- Until now the DPA form's answers (purpose, kinds of data, whose data, transfer, sub-processors) reached no
  draft at all, although the wizard's help text says they feed "the DPA annex" and "the sub-processor
  clause". This feature makes the purpose, kinds, people, countries and sub-processors true and makes
  "Our role" decide whether a draft is made; the help text for the transfer question ("adds transfer
  clauses") still promises more than drafting does, because the DPA always carries Section 9.
- The DPA signature block has empty "Address for notices:" lines that Section 14.2 relies on. They are not
  bracketed, so the blank check does not see them, and they are left as the other empty signature lines
  are.

## Edge cases & error behavior

- Release: shipping this feature starts no paid check. A template that had been checked shows Out of date,
  and one never checked stays Never checked, until a change, a playbook publication or a person's check
  starts one (FR-37). Dev and test data with no saved wording pick up the new wording at once.
- An organisation's saved Consultancy or DPA wording keeps its brackets and drafts them as today. The new
  answers are stored on the request but unused by that wording, and no request is held for it (FR-37,
  FR-43). The Processor rule (FR-53 to FR-56) is about the request, not the wording, so it applies to a
  Processor request whatever wording is in effect.
- A term with no end date: deliverable due dates need only be on or after the start date, and the drafted
  agreement's term reads "continues until terminated" as today (FR-14).
- A one-day term: every due date must equal that day (FR-14).
- The dates move after rows were entered: the wizard flags the rows now outside the window and filing
  refuses them (FR-14).
- The total value changes after a rate was entered: the rate stays, and the ceiling follows the new value
  (FR-16).
- A requester's own choice that departs from a playbook position, for example 90-day payment terms or
  data going to a country outside India, is drafted as asked. This feature does not override it, and a
  review of the draft may report it (FR-5, FR-9).
- A requester ticks "Patients or trial subjects" but not "Health or other special category": the
  sensitive data statement follows the kinds ticked only, so it reads "None". There is no cross-check
  between the two answers (FR-8).
- A requester lists "India" among the countries: refused, because the question asks for countries outside
  India (FR-25).
- Names with quotes, curly braces, non-English characters or very long text within the limits are
  inserted as plain text and never break the draft (FR-13, FR-18, 006 FR-42).
- A requester pastes a template placeholder such as "[Name and role.]" into an answer: refused as a blank
  bracket (FR-20, FR-28).
- The same Consultant person typed twice with different spacing or capitals: refused (FR-18).
- A new field inserted into another template, for example an NDA: a real request for that kind has no such
  answer, so the field fills with nothing and never with a bracket or example content (FR-34). The
  Templates page check and preview still show the example reading.
- A workflow starts a Consultancy or DPA draft with no person present: the same filling applies; for a
  request that lacks an answer the step stops and shows the reason, then continues once the answers are
  entered (FR-43, FR-44); for a Processor request the step is skipped with its note and the workflow goes
  on (FR-54).
- A Processor request is edited after its draft step was skipped, for example to the Controller choice:
  the skip is not undone and no draft appears by itself; a person may ask for the draft from the request
  page and it is made as for any Controller request (FR-53, FR-56).
- A Processor DPA request's new answers (countries, sub-processors) are still collected and shown, so
  the reviewer has the facts; no draft is made from them (FR-24, FR-56).
- The AI review is unavailable, over its limit, or returns different findings for identical wording: as
  006 FR-8, FR-10, FR-11 and FR-20 (FR-39).
- A person without change permission opens a template: read-only with the reason shown, as 006 FR-64.

## Decisions and assumptions

Decisions the owner gave on 2026-10-15; each is folded into the requirements above. D-1 to D-4 were given
before drafting; D-5 to D-10 in answer to the six open questions, which are all closed; D-11 when the
owner approved the spec.

- D-1: This is its own spec (008), through spec, plan, tasks and implement, separate from 007.
- D-2: It reuses 007's building blocks and invents no parallel mechanism: the table question for
  deliverables and sub-processors, the rules that refuse a blank bracket in a typed answer and a
  non-positive amount, the shared rule-cases file that keeps the wizard and the server in step, the
  example reading whose example content never reaches a real draft, the hold for older requests, and the
  frozen copy of the wording the AI exam was built from. (FR-13 to FR-21, FR-33, FR-34, FR-43, FR-44,
  FR-48)
- D-3: The exam's Consultancy and DPA documents are frozen like the Statement of Work's, so the exam and
  its 005 baseline do not move. It is not an open question: the exam renders both templates today (its
  review documents, the DPA schedule of its long documents and two portfolio contracts), and a template
  change would break its committed content, hashes and offsets. Touching the exam's files is outside every
  agent's default ownership, so the plan needs the owner's explicit yes at plan time, as the owner gave
  for 007. (FR-48)
- D-4: The owner's aim is a draft that "comes back from its playbook review without risks and never goes
  out with blank brackets". Only the second half is a promise: FR-1, FR-2, FR-6 and FR-7. The first half
  is carried as FR-5 and FR-9 (the filled places state the positions the playbooks prefer) and FR-38 (a
  check records its findings), not as a guarantee, because the AI review is not repeatable (FR-39). Both
  templates were Passes on 2026-10-15, so for them this is a requirement not to regress, not a fix.
- D-5 (owner, 2026-10-15; DPA facts): the DPA form asks the real facts. The requester ticks every kind of
  data subject and every kind of personal data that applies (no "Several"); when data leaves India, the
  form asks which countries; when sub-processors are used, it asks each one's name, country and what it
  does. The annex states exactly what was given. (FR-8 rows 2, 3, 5 and 6, FR-24 to FR-32)
- D-6 (owner, 2026-10-15; Consultancy deliverables): the Consultancy form gets a deliverables table that
  reuses 007's table question (name and due date per row, both required), plus an explicit "no separate
  deliverables (advice only)" tick. The date bound is one-sided when the term has no end date. (FR-3 row
  2, FR-12 to FR-15)
- D-7 (owner, 2026-10-15; fees): the fee basis is Fixed fee, Day rate or Hourly rate; for a rate the rate
  is asked too. The total value is the fixed fee, or the ceiling a rate cannot pass. (FR-3 row 3, FR-4,
  FR-16)
- D-8 (owner, 2026-10-15; people): the Consultancy form asks for both the Consultant's key individuals,
  as a list of at least one, and the Company contact's name and role. (FR-3 rows 4 and 5, FR-18, FR-19)
- D-9 (owner, 2026-10-15; requests without the new details): hold, as in 007. The person is told what is
  missing and enters it on the request page; an email or chat DPA request is told to file on the DPA form;
  a workflow-started draft stops with the reason and continues once the details are in; nothing is
  invented; a request that already has everything is drafted at once. (FR-41 to FR-45)
- D-10 (owner, 2026-10-15; DPA when we are the Processor): no draft is made from our Controller-side
  template, as for a kind of agreement with no template: a person-started draft gets a clear message, and
  a workflow's draft step is skipped with a note (the rule 006's second phase plans for a kind with no
  active template). A separate Processor-side template is out of scope. (FR-53 to FR-56)
- D-11 (owner, 2026-10-15; the Processor message): neither the person-facing message nor the workflow
  note says "Legal prepares the paper", because in the starter DPA workflow the next step is the Privacy
  review. Both say, in substance: "No draft was made: this request says we are the Processor, and our DPA
  template is written for the Controller. Attach the other side's DPA (or the paper to use) before the
  review." Who picks the request up stays out of scope. (FR-53 to FR-55)

Wording added by the analyst to make the decisions testable. The owner has not confirmed these; they are
reviewed at approval and any of them may be reversed.

- A-1: The Consultancy wording is written from the Company's side (us); the DPA wording makes us the
  Controller, which is why a Processor request is not drafted (FR-53). Fills that name people avoid
  saying whose data it is (FR-8).
- A-2: The deliverables table holds 1 to 20 rows, like the Statement of Work's; the key individuals list
  1 to 10; the countries list 1 to 20; the sub-processors table 1 to 20 rows (FR-13, FR-18, FR-25,
  FR-26).
- A-3: The new questions are asked, and a request is held, only for our own paper. A request for Their
  paper is not drafted from our template, so it is not asked for facts only our template needs (FR-12,
  FR-24, FR-43).
- A-4: The "total value above zero" rule is limited to Consultancy because the question is shared by six
  kinds of agreement; the scope question's bracket rule is limited likewise (FR-17, FR-20).
- A-5: For "Fixed fee" the total value is the fixed fee; for a rate basis it is the ceiling, because the
  form asks for no separate cap, as 007 A-8 does for time and materials (FR-4, FR-16). A fixed fee is not
  split into instalments, because the form asks for none; invoicing follows Clauses 6.2 to 6.4.
- A-6: Processing locations are India plus the countries listed, because the question "Will data leave
  India?" assumes the data starts in India (FR-8, FR-25).
- A-7: The sensitive-data statement follows the kinds of data ticked only (FR-8).
- A-8: A sub-processor outside India is refused when the request says data does not leave India, so that
  the annex does not contradict itself (FR-27).
- A-9: The phrases that describe each kind of data and each category (FR-8 rows 2 and 3) and the standard
  sentences in both Fill maps are the analyst's proposal. They are for the owner and counsel to review and
  they become the shipped wording.
- A-10: The hold applies only when the wording in effect uses the new fields and a question applies to
  the request's other answers, because a wording that ignores the answers has nothing to invent (FR-43),
  as 007 A-18.
- A-11: The rule positions cited in FR-5 and FR-9 are those of the published playbooks. The orchestrator
  read them from the development database on 2026-10-15: both playbooks are at version 1 with the same
  clause types as the starter library (Consultancy: intellectual property, independent contractor,
  confidentiality, conflicts of interest, non-solicitation, payment terms at 30 days with no advance above
  20%, termination, limitation of liability; DPA: processing instructions, sub-processors, breach
  notification within 48 hours, security measures, international transfers, audit, data subject rights,
  return or deletion). The analyst did not read the database. The planner re-reads the published text
  before the wording is written, because an organisation may edit its playbook later.
- A-12: The counts recorded at drafting (FR-49) are counts, not content, as 007 FR-37 records the number
  of rows and the pricing model.
- A-13: Names are typed text and never looked up (FR-18, FR-19, FR-26), as 007 A-4.
- A-14: A due date that has already passed is accepted with a non-blocking note, as 007 A-3 (FR-14).
- A-15: "No separate deliverables" is an explicit tick, not an empty table, so that forgetting is not
  taken for "none" (FR-15).
- A-16: A person's refused draft of a Processor request writes no audit or history record, as a held draft
  does, because it changes nothing; the workflow's skip does, because it moves the request on without a
  draft (FR-53, FR-55).
- A-17: The new DPA questions are asked of every own-paper DPA request whatever the answer to "Our role",
  because the form does not split by role and the reviewer needs the facts for a Processor request; no draft is
  made from them for a Processor request (FR-24, FR-56).
- A-18: A Processor request is recognised by its answer to "Our role" only. A request with no such answer
  (email, chat) is not treated as a Processor request (FR-56).

---

### Context for planning (non-normative)

Implementation-adjacent facts recorded to save the architect re-deriving them. They are not requirements
and carry no normative weight.

- **What 007 built and this spec reuses.** The `rows` form question kind (columns of text, date and
  percent, with `unique`, `within`, `total` and a column-level `show`; `intake/agreement_forms.json` and
  `.py`); `no_blanks` and `min_exclusive` on single questions; the shared rule-cases file
  `backend/tests/fixtures/deliverable_rows_cases.json`, read by both the Python and the TypeScript suites
  so the wizard (`frontend/src/lib/form-checks.ts`) and the server cannot drift; the pure module
  `app/intake/sow.py` (`sow_values(fv, example=)`, `EXAMPLE_ANSWERS`, `EXAMPLE_VALUES`, `EXAMPLE_MARKERS`,
  `ROW_FIELDS`, the frozen hold strings); `template_values` and `render_document` taking `example=`, passed
  only by `drafting_templates/checks.py` (`check_values`, `_review`); `DraftHeld` and `draft_hold` in
  `intake/drafting.py`; the workflow catch in `workflows/service.py` (`_HELD_FOR_ROWS`,
  `_resolve_waiting_group`, `complete_human_step`); the request-page editor `RequestDeliverables`
  (`frontend/.../intake/_deliverables.tsx`); the audit pattern `intake.deliverables_changed`; and the
  exam freeze (`backend/eval/exam/build.py` `_template_body`, `_SOW_WORDING`,
  `eval/exam/sow_2026-10-15.txt`).
- **What is SOW-only today and must be generalised.** `draft_hold` returns early unless
  `resolve_doc_type(request) == "sow"` and tests `sow.ROW_FIELDS`; `_template_body` freezes only `sow`;
  the draft record in `draft_contract_for_request` adds `deliverable_rows` and `pricing` only for
  `doc_type == "sow"`; `checks._enrich` guards Apply with `sow.EXAMPLE_MARKERS` only; `_CODED` lists the
  nine SOW names. `rows` `within` applies only "when both answers are ISO days", so a Consultancy term with
  no end date needs a one-sided bound (FR-14). Stored-aware validation (`_validate_field_values(...,
  stored=)`) already exists for carrying an older request's answers through an update (FR-41, FR-42).
- **The field list is shared.** `PLACEHOLDERS` in `drafting_templates/service.py` is one list for all seven
  templates and `template_values` fills every key for every template (`str.format` ignores unused ones), so
  new names must not collide and each needs an entry with a plain label, an "about" and a sample from the
  example reading. `services` (from `scope`) and `fee_amount` (from `value` and `currency`) already exist
  with Statement-of-Work wording in their "about" text and fill for any request; reusing them for
  Consultancy needs only an "about" change (FR-35).
- **The blank check.** `app/contracts/blanks.py` `find_blanks`: the limit was raised from 120 to 2000 and
  is committed (`42d2a28`), so at HEAD the detector sees 5 Consultancy and 6 DPA blanks. The Consultancy
  blanks are `defaults/consultancy.txt` lines 191, 193, 195, 197, 199 (the key-individuals one holds the
  sentence "The Consultant shall not replace them without the Company's prior written consent", which must
  become standard wording outside any bracket); the DPA blanks are `defaults/dpa.txt` lines 168, 170, 172,
  174, 176 and 204. `tests/test_sow_drafting.py::test_the_blanks_the_other_templates_already_had_are_untouched`
  now pins consultancy 5 and dpa 6; this feature changes both to 0.
- **Literal playbook check.** `tests/test_playbook_library.py::test_our_own_template_passes_the_literal_check`
  renders every template with only value, currency, dates and payment terms and requires each rule's
  `required_language` to appear and each `prohibited_language` ("payable in advance", "unlimited
  liability") not to. With the new fields filled from nothing it must still pass.
- **Playbooks.** The starter rules are in `app/playbooks/library.py`. The orchestrator confirmed on
  2026-10-15 that the published Consultancy and DPA playbooks in the dev database are at version 1 with
  the same clause types (A-11). 006's Check all on 2026-10-15 gave Consultancy and DPA Passes
  (`specs/006-template-playbook-sync/verification.md`), so the blanks did not cause review findings; the
  gain is the signature gate and hand-filling.
- **Dev data.** The dev database holds no Consultancy or DPA request (orchestrator, 2026-10-15). Older-
  request behaviour (FR-41 to FR-45) and the Processor rule have no dev data to walk, so tests and the
  verification walk must stage requests.
- **Forms.** Consultancy is `agreement_type == "Consultancy"` of `new_agreement`. It asks `scope`
  (shared with MSA and Something else), `value` and `currency` (shared by six kinds), `start_date`, `term`,
  `end_date` (only for fixed or renewing terms), `payment_terms`, `personal_data`, `gxp`. The DPA form asks
  `our_role` (options "Controller: we decide how the data is used" and "Processor: we act on their
  instructions"), `processing_purpose`, `data_types`, `data_subjects` (both `select` with a "Several"
  option), `transfer` and `subprocessors` (Yes/No). A `multiselect` kind already exists (the Amendment
  form's `what_changes`). No backend drafting code reads `our_role`, `processing_purpose`, `data_types`,
  `data_subjects`, `transfer` or `subprocessors`; only the triage and flow prompts show them to the AI. The
  wizard's `UI` help text (`frontend/.../intake/_agreement-forms.tsx`) claims "Processing description in
  the DPA annex" and "Sub-processor clause in the DPA". `DRAFT_OF` there still says Consultancy is drafted
  from the "MSA template" (FR-23).
- **The Processor rule (FR-53 to FR-56).** Today a request for which `resolve_doc_type` is None takes the
  `custom` path in the workflow's `clm_draft` branch (`workflows/service.py` ~1169 to 1196): an AI-written
  or shell bespoke draft is created and the step waits for an attorney; there is no skip. The skip the
  owner refers to is 006 Phase 2's `clm_draft` rule (`specs/006-template-playbook-sync/plan.md`, Workflows
  (P2), D-12): on the fast-lane path, for a request filed on a form with no active template, the step is
  skipped with the note "No active template for this kind of agreement — Legal prepares the paper." and
  the workflow goes on to its next step. 006 Phase 2 is not built (only Phase 1 is), so this feature must
  build the skip for the Processor case, in the same shape, so the two can later share one rule. A
  person-started draft for a kind with no template today raises "This request type has no draft template
  — handle it manually." in `draft_contract_for_request`; the page's Draft button is driven by
  `_draftable` (`intake/service.py`) from `resolve_doc_type`, which must keep returning `dpa` for a
  Processor request (FR-46). The starter DPA workflow (`workflows/contract_library.py`) runs Draft
  (compliance team), Privacy review (compliance), Legal sign-off (legal_counsel), Negotiate, Sign,
  Activate, so after the skip the next step is the Privacy review, not a Legal step. That is why D-11
  words the message as "attach the other side's DPA before the review" and makes no claim about who
  prepares the paper; who picks the request up is out of scope beyond the note and the next step.
  Check the step before the existing-contract skip so a request that already has a contract is unchanged.
- **`paper`.** The backend does not read "Whose paper?" when drafting; Their paper is handled by the wizard
  uploading the file and by workflows whose condition is `paper` (`workflows/contract_library.py`). The
  planner should confirm that a Their-paper Consultancy or DPA request never reaches a template draft step
  (A-3).
- **Free-text DPA.** `resolve_doc_type` maps any request with no form whose text matches `dpa` or `data
  processing (agreement|addendum)` to the `dpa` template, so such a request is drafted from the DPA
  template with no answers. Before this feature that left 6 blank brackets that block signature; after it,
  without the hold, it would leave empty annex entries with no bracket and nothing to stop it (FR-43).
- **Exam.** `eval/exam/build.py` renders `consultancy` and `dpa` with `render_document` for
  `ae-consultancy-01..04`, `ae-dpa-01..04`, Schedule 2 of the four `ae-long-*` documents, and the
  portfolio contracts `pf-05` (consultancy) and `pf-10` (dpa). The manifest holds a hash per template
  (`eval/exam/content/manifest.json`), and `tests/test_exam_content.py::
  test_committed_constructed_content_is_exactly_what_the_build_produces` compares the rebuild with the
  committed files (it runs only when the public downloads are present, but CI also checks the committed
  JSON). Freezing means byte copies of today's `consultancy.txt` and `dpa.txt` next to the SOW copy. The
  exam folder is outside every agent's default ownership, so the plan needs the owner's explicit yes
  (D-3), which the owner gave for 007.
- **Custom drafts.** `_custom_shell` and the AI drafting path write their own bracketed placeholders
  ("[Counterparty]", "[TO BE CONFIRMED: ...]"); they are not template drafts (Out of scope).
- **Audit and history.** `write_audit_log` and `write_timeline_event` (`app/core/audit.py`); the 006 record
  is `DraftingService._template_at_this_moment` and `_record_drafted_from_template`; extend
  `intake.contract_drafted` and `intake.deliverables_changed`. Do not write the audit row on a second
  session or hold it across slow work (CLAUDE.md, audit-lock freeze).
- **Repo graph.** `specs/_graph/domains/intake.md` and `drafting_templates.md` list the routes and models
  involved; read them first.
