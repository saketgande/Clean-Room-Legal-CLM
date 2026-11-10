# Feature Specification: Templates and playbooks aligned, so our own paper passes our own playbook

Feature ID: 014-template-playbook-alignment
Created: 2026-10-27
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->

## Summary

Aegis drafts contracts from seven templates (NDA, MSA, SOW, Vendor, SaaS, Consultancy, DPA) and
then reviews them against a matching playbook. Today the two disagree: 17 real findings were raised
on contracts Aegis drafted from its own templates (MSA 11, SOW 4, NDA 2), and the review found
text that is wrong for an Indian company (a Delaware fallback, no named dispute seat, no
MSME payment rule, an unclear GST position, a SOW that prints two pricing models, a SaaS data
cap that never applies). This feature brings each of the seven templates and its playbook to a
market-standard position for an Indian company that is usually the customer, or the Controller
for data, and changes each pair together. The bar: every filled template, for every answer the
current form can give, shows zero findings against its own playbook. Contracts already drafted are
not touched. Asking new questions on the forms is the next feature; until then this feature uses
safe, stated defaults.

## User stories

- As a requester, I want the draft Aegis builds for me to pass our own playbook, so that I do not
  see findings on our own paper when Legal redlines it.
- As a lawyer, I want our standard paper and our playbooks to say the same thing, so that a finding
  means a real problem and not a mismatch.
- As a lawyer, I want our paper to be market standard for an Indian customer (Indian law, a named
  dispute seat, MSME, GST, data-breach timing), so that I am not fixing the same gaps on every
  draft.
- As a lawyer, I want the rules to still catch real risk on a counterparty's own paper, so that
  loosening a rule for our paper does not let a bad clause through.
- As an administrator, I want every change to a template or playbook recorded as a new version, so
  that there is a trail and an org's own edits are not lost.
- As the approver of this spec, I want the points that need a lawyer's judgement put to me as plain
  decisions, so that I decide them once.

## Functional requirements

### The bar

- FR-1: For each of the seven templates, the system SHALL produce a filled draft with zero
  findings against that template's own playbook, for every combination of answers that the
  current form for that type can give (FR-2), on the automated template check.
- FR-1A: "Zero findings" SHALL be proven in two ways, both required. (1) Every answer combination
  in FR-2 passes the fixed phrase and rule check with zero findings; this is the full matrix, is
  repeatable, uses no AI, and runs with the normal tests. (2) The AI playbook review (the one that
  raised the real redlines) is run 3 times on each template's main combinations, about 40 runs in
  total, and no finding (same clause type) repeats in 2 or more of a combination's 3 runs (changed
  by the user on 2026-10-28 from "zero findings in every run": the AI reviewer raises a different
  one-off nit on each run, so only a repeated finding marks a real gap). The main combinations per template are the default fill
  plus each answer that changes a clause: payment options; NDA terms 2, 3 and 5 and mutual versus
  one-way; renewal on and off (with each notice period); personal data yes and no; each hosting
  option; each pricing model; goods, services and both; and one non-INR value. This second proof
  costs real AI calls (a few dollars), SHALL be run deliberately (before approval of the build and
  after any later change to a template or playbook) and SHALL NOT run in normal CI. Its date and
  result SHALL be recorded with the check result (FR-8).
- FR-2: "Every combination" SHALL cover each option of each question the form asks for that
  type, not only the defaults. At least: payment periods offered; NDA term 2, 3 and 5 years; NDA
  mutual and one-way, and each direction; renews automatically or not, and each notice period;
  personal data yes and no; each hosting option; each pricing model (fixed, milestones, time and
  materials) and no pricing answer; each governing-law option and a blank one; goods, services and
  both for Vendor; and a currency other than INR.
- FR-3: WHEN the form offers an answer that the playbook rejects, then either the playbook rule
  SHALL be changed so the answer passes, or the form SHALL stop offering that answer. The choice
  for each answer SHALL be recorded in the plan. No offered answer SHALL leave a finding. As
  decided (Q16): MSA and Consultancy SHALL offer payment periods of 45 and 60 days only; Vendor and
  SaaS SHALL offer 45, 60 and 90 days and no 30; every draft carries the MSME 45-day line.
- FR-4: The system SHALL change each template and its playbook together, as a pair, so that at no
  point does a shipped template fail its own playbook because only one of the two was changed.
- FR-5: A filled template SHALL contain no empty placeholder, no stray punctuation from a missing
  answer (for example "Pricing model: " or "Provider: ."), and no text from a clause that does
  not apply to the answers given.
- FR-6: The seven playbooks SHALL contain no rule that passes on any contract whatever it says, and
  no rule that flags market-standard or protective wording, in the places the review found them.
- FR-7: The Customer Agreement playbook has no template. It SHALL be left as it is, except that
  where it shares a rule with a changed playbook it SHALL NOT be made to disagree with the new
  position (Q17).

### Automated check, existing contracts

- FR-8: After this feature, the automated template check SHALL be run for every template and every
  combination in FR-2, and its latest result SHALL be current, shown with the date it ran.
  Results from before this feature (including the saved SOW result that quotes bracket
  placeholders the current SOW does not contain) SHALL be replaced or marked out of date.
- FR-9: WHEN a template or a playbook is changed later, the system SHALL make it possible to
  re-run the check and SHALL show whether the saved result is older than the change.
- FR-10: Contracts and requests already drafted SHALL NOT be changed in any way by this feature:
  their text, their findings, their suggested changes and their history stay as they are.
- FR-11: A finding on an existing contract SHALL stay as it was raised even if the same finding
  would not be raised under the new playbook.

### Law, disputes and signatures (all templates)

- FR-12: The system SHALL NOT produce Delaware, or any other fixed foreign law or court, as a
  fallback. WHEN the governing law is blank or not a country, the draft SHALL say Indian law.
- FR-13: WHEN the governing-law answer is an Indian state or city (for example "Maharashtra,
  India"), the draft SHALL read it as Indian law and SHALL NOT print the state as the law.
- FR-14: Every template except SOW and DPA SHALL name a dispute seat (a city), not only a country.
  Until a question for it exists, the seat SHALL be the registered city of our legal entity.
- FR-15: The SOW and the DPA SHALL follow the law and dispute route of the master agreement or
  principal agreement they sit under, and SHALL NOT state a second, separate law.
- FR-16: As decided (Q2), disputes SHALL be under Indian law, by arbitration with one arbitrator
  under the Indian Arbitration and Conciliation Act, seated at the dispute seat of FR-14 (an
  Indian institution such as MCIA Mumbai where the provider is Indian; SIAC with Singapore as the
  seat for a foreign provider), with either side free to ask any competent court for urgent
  relief. Exceptions: an individual consultant SHALL get the courts at the seat instead of
  arbitration; the NDA SHALL use the courts at the seat, with the same freedom for urgent relief.
- FR-17: WHEN our signatory is held in the legal-entity register, and the counterparty signer is
  given on the request, the signature blocks SHALL show the name and title of each. WHEN a name or
  title is not available, the block SHALL show a plain blank line to fill by hand, not "Provider: ."
  or an empty label.
- FR-18: Notice addresses and party details (registered office, company number, tax number) SHALL
  be filled from the registers WHERE the register holds them, and SHALL otherwise be left as a
  clear blank to fill by hand. The draft SHALL NOT invent an address.

### Payment, tax and money (all templates that have fees)

- FR-19: Every paid template SHALL say that a supplier registered as a micro or small enterprise
  under the MSME law is paid within 45 days at most, whatever longer period is stated for others,
  and SHALL ask such a supplier to state its registration number on each invoice. The playbooks
  SHALL accept this wording.
- FR-20: Every paid template SHALL state the GST position: fees exclude GST; compliant tax invoices
  with the supplier's GST number; the supplier reimburses input tax credit lost through its
  default; GST on imported services paid by the customer under reverse charge with no increase in
  fees; tax withheld as required by law with certificates given; a reduced treaty rate only on a
  tax residency certificate and the forms the law requires. The Consultancy template SHALL NOT
  say both that the consultant is solely responsible for GST and that we pay GST on top.
- FR-21: Each template SHALL state the currency of the fees. WHEN the request's currency is not
  INR, the draft and the checks SHALL use that currency and SHALL NOT print a fixed USD or INR
  figure that contradicts it.
- FR-22: Where a template names an insurance requirement, it SHALL give a measurable minimum, and
  the matching playbook SHALL check it. As decided (Q3): INR 5 crore per claim for professional
  indemnity, plus INR 5 crore cyber cover where the provider handles personal data or our systems;
  for goods vendors, the greater of the contract value and INR 1 crore.
- FR-23: Where a template sets a time to tell us of a personal-data breach, it SHALL be, as decided
  (Q1), 24 hours, and 6 hours where the law requires a CERT-In report, and the playbooks SHALL
  accept that window and nothing slower.
- FR-24: WHERE a template carries a data-protection clause, the "personal data" answer SHALL decide
  its text. WHEN the answer is yes, the text SHALL say the DPA applies from the start. WHEN it is
  no, the text SHALL say no personal data is expected and a DPA must be signed before any
  processing starts. The playbook SHALL NOT flag an unsigned DPA when the answer is no.
- FR-25: WHEN the scope or purpose in a request is placeholder or test text (for example "This is a
  test"), the draft SHALL NOT print it as the scope. The request SHALL be held with a plain message
  until real text is given (Q18): at least about 15 words, and text such as "test", "TBD" or
  "lorem" is refused.

### NDA

- FR-26: The NDA SHALL be mutual-workable and market standard: exclusions as shown by the receiving
  party's written records (no "clear and convincing" standard); at least reasonable care; no
  duplicated clauses; return or destruction in ten business days with backups allowed to be
  overwritten in the normal cycle and retained copies kept confidential; representatives include
  affiliates, advisers, contractors and service providers bound by written confidentiality, with no
  prior-consent step for ordinary service providers; trade secrets protected for as long as they
  remain trade secrets; survival that does not contradict the stated limit; independent development
  allowed; assignment to an affiliate or successor; electronic signatures recognised; stamp duty
  as decided in Q6.
- FR-27: The NDA SHALL print the term in words in the same style for 2, 3 and 5 years.
- FR-28: The NDA playbook SHALL treat term and survival as two separate things. A term of 2, 3 or
  5 years SHALL pass when confidentiality survives at least 3 years after the end (5 preferred),
  with trade secrets protected as long as they stay trade secrets. A term under 1 year, or no
  survival, SHALL still be flagged.
- FR-29: WHEN personal data is yes, the NDA SHALL say the DPDP Act 2023 applies and that breaches
  are notified without undue delay; WHEN no, it SHALL still require compliance with data
  protection law.
- FR-30: The NDA SHALL carry the AI/ML restriction in the way decided in Q9. The recital SHALL
  keep reading "explore a potential business relationship" until the next feature adds a purpose
  question (Q14).
- FR-31: The NDA playbook SHALL cover the missing topics: it flags non-compete or exclusivity,
  indemnities and residuals, and accepts routine regulator or stock-exchange inspections without
  prior notice.

### MSA and SOW

- FR-32: The MSA SHALL contain: a dispute clause as decided in Q2; the MSME and GST wording (FR-19,
  FR-20); a default service-credit rule where an SOW sets service levels but no credit, and
  an at-risk cap as decided in Q4; insurance (FR-22); the copyright assignment wording for
  worldwide and full-term transfer with no lapse for non-use; an information-security and
  continuity clause; audit of security controls; transition help free of charge when we terminate
  for breach; the SOW's description of services and fees prevailing for that SOW; a
  change-of-control right; a quality-agreement hook when GxP is yes; and survival of the audit and
  continuity clauses.
- FR-33: The MSA liability cap and the data-breach treatment SHALL be as decided in Q5, and the
  playbook SHALL accept "fees paid or payable in the preceding 12 months" as meeting its preferred
  position.
- FR-34: The MSA service-levels rule SHALL accept levels set in the SOW, and SHALL NOT flag an MSA
  because a project-only SOW has none. The SOW rule SHALL require ongoing services to have
  measurable levels and credits, or to state that none apply.
- FR-35: The SOW SHALL print only the pricing model chosen (fixed or milestone, or time and
  materials), with the amounts, and SHALL NOT print the other model. WHEN no pricing answer is
  given, the draft SHALL be held with a plain message and not printed with blanks.
- FR-36: A time-and-materials SOW SHALL state its rates in the SOW itself; a fixed-fee SOW SHALL say
  no rates apply and that changes are priced in the change order. The SOW rules SHALL accept
  rates stated in the SOW and SHALL NOT penalise time and materials for lacking milestones.
  Until the next feature asks for rates, a time-and-materials SOW SHALL say the rates are those
  in the master agreement, or, where it has none, that rates must be agreed in a signed change
  order before any charge (Q19).
- FR-37: The SOW SHALL name the master agreement it sits under where one is chosen on the request,
  and SHALL take its payment period from the master agreement and not state its own.
- FR-38: The SOW SHALL state whether service levels apply: default "none, project deliverables",
  with levels added only by signed change order, until the next feature adds service-level
  questions.
- FR-39: WHEN the SOW has no named provider representative, it SHALL say the provider notifies the
  individual in writing and SHALL NOT print a blank.

### Vendor and SaaS

- FR-40: The Vendor template SHALL be changed as in the review: a dispute clause as decided in Q2;
  the GST and MSME wording; wages and statutory dues responsibility for the vendor's personnel;
  a minimum liability cap (the greater of trailing 12 months and first-12-month fees); breach
  notice (FR-23); an insurance minimum (FR-22). For goods it SHALL cover delivery terms, quantity
  rejection and recall; for services, acceptance on written confirmation; for both, all of these.
  Delay damages, risk passing and convenience-termination payment SHALL be as decided in Q10.
- FR-41: The Vendor text for data protection SHALL follow FR-24, and its supply description SHALL
  come from the goods/services answer.
- FR-42: The Vendor and SaaS liability sections SHALL be consistent with each other and with their
  playbooks as decided in Q5, so the SaaS data-breach limit actually applies. No clause SHALL
  make the same loss both capped and uncapped.
- FR-43: The SaaS template SHALL print hosting according to the hosting answer (India, EU, US,
  several) and SHALL NOT print India when another is chosen. A non-India location SHALL say the
  location needs the privacy function's approval and that no transfer goes to a country the DPDP
  Act restricts. The hosting rule SHALL accept a named location with that approval and SHALL NOT
  accept an unnamed one (Q12). India-only is not required for foreign providers.
- FR-44: The SaaS renewal text SHALL give one notice period everywhere it is stated. The notice
  period SHALL be the larger of the answer and 60 days. The renewal rule SHALL pass this.
- FR-45: The SaaS template SHALL add: a definition of backup, restore and continuity (daily backup
  in-region, tested yearly); limits on when the provider may suspend service; a way out of the
  affected order when a sub-processor objection is not settled in 30 days, with refund of prepaid
  fees; reverse charge for a provider outside India; and a billing period, as decided in Q13.
- FR-46: The SaaS and Vendor playbooks SHALL gain the missing topics the review lists (for example
  insurance, governing law, delivery and acceptance, tax, suspension, continuity), and the
  data-security rule SHALL use the window in Q1.

### Consultancy and DPA

- FR-47: The Consultancy template SHALL have one clear GST and tax position (FR-20), a dispute
  clause as decided in Q2, an insurance clause (FR-22), a re-performance and acceptance clause,
  copyright wording that fits Indian law, independent-contractor wording (free to serve others, no
  fixed hours, employer of its own people), a day defined as eight hours with part days pro rata,
  how fixed fees are invoiced, and a data-protection sentence driven by the personal-data answer
  (FR-24). Moral-rights and stamp-duty wording SHALL be as decided in Q6 and Q11.
- FR-48: The Consultancy playbook SHALL gain rules for governing law, insurance, data protection,
  tax and a non-compete (prohibited), SHALL stop flagging protective advance-payment wording or
  usual liability carve-outs, and SHALL use a 30-day step in termination.
- FR-49: The DPA SHALL name the principal agreement it belongs to where one is chosen; SHALL run
  from the effective date until the later of the end of the principal agreement and return or
  deletion of the data, with no separate term to fill in; SHALL follow the principal agreement's
  law and dispute route (FR-15); SHALL treat transfers differently for DPDP-only data and for
  EU/UK data, as decided in Q7; SHALL list in breach notices the date, time, place, cause and
  responsible person; SHALL cover children's data, records of processing, public-authority access
  notices and a ten-business-day erasure; and SHALL name a privacy contact, left blank when not
  known.
- FR-50: The DPA playbook SHALL be reworded to DPDP terms (so that "Data Principal", "erasure" and
  "cross-border" pass), SHALL have no slower fallback than the Q1 window, and SHALL gain rules for
  liability (Q5, Q8), term, processing description, no use for own purposes, personnel
  confidentiality, assistance and children's data. The DPA's notice window SHALL equal the one in
  the Consultancy and other templates so the playbooks do not disagree.

### Playbooks protect against real risk

- FR-51: A rule loosened so that our paper passes SHALL still flag the risky version on any
  contract, including a counterparty's own paper reviewed under the same playbook. At least: a
  law or court that is foreign with no India route; payment beyond 45 days to an MSME; unlimited or
  uncapped liability beyond the agreed carve-outs; a breach window slower than Q1; no insurance
  amount; no DPA where personal data is processed.
- FR-52: For each rule changed, the plan SHALL name one wording that must pass and one that must
  still be flagged, and both SHALL be checked.

### Versions, who may change, audit

- FR-53: Every change to a template SHALL be saved as a new template version and every change to a
  playbook as a new playbook version, with the reason and the review item it answers. No existing
  version SHALL be edited in place.
- FR-54: The shipped standard text for the seven templates SHALL be updated to the new position.
  WHEN an organisation has its own saved version of a template or has edited a playbook, the system
  SHALL keep it, SHALL NOT overwrite it, and SHALL show Legal that it is older than the new
  standard with the result of the check on it, and SHALL offer a one-click reset to the standard (Q15).
- FR-55: A template save SHALL still be refused unless every placeholder in it is filled by the
  form and registers.

## Permissions, scoping & audit

- Who may change a playbook: only people who already hold the permission to edit playbooks. Who may
  change a template: only people who already hold the permission to edit templates. This feature
  adds no new permission. A person without it cannot publish or restore a version.
- Who may run or view the automated template check: people who may open the Templates page.
- Who sees what: templates, playbooks, versions and check results belong to one organisation and
  are shown only to that organisation's users. Contracts are seen under the usual access rules,
  including ethical walls; this feature adds no new view of any contract.
- Audit: every new template version and playbook version records who made it, when, the template
  or playbook, the reason, and what changed in plain terms. Every run of the automated template
  check records who ran it, when, which template, and how many findings. A shipped-default update
  for the organisation is recorded as a system change with the date. Viewing writes nothing.
- This feature changes no existing contract, so it writes no audit entry on any contract.

## Acceptance criteria

- AC-1 (FR-1, FR-2): Given each of the seven templates, when it is filled with every combination
  of the answers its form offers and checked against its playbook, then every run shows zero
  findings.
- AC-1A (FR-1A): Given the full matrix, when the fixed check runs without AI, then there are zero
  findings in every combination; and given the main combinations of each template, when the AI
  review is run 3 times on each (about 40 runs), then no finding of the same clause type appears in
  2 or more of a combination's 3 runs, and every risky counterparty paper is flagged. Given normal
  CI, then the AI runs do not execute; the run is started deliberately and its date and result
  are recorded.
- AC-2 (FR-3): Given an answer the form offers that the playbook would reject, when this feature
  is done, then either that answer passes or the form no longer offers it, and no offered answer
  leaves a finding.
- AC-3 (FR-4): Given a change to a template wording, when it ships, then the matching playbook
  change ships with it, and the pair passes together.
- AC-4 (FR-5): Given a request with no pricing answer, a blank signer or a blank law, when the
  draft is made, then it shows no empty label, no "is , inclusive" and no stray punctuation.
- AC-5 (FR-6): Given each rule the review listed as passing on anything or flagging market text,
  when it is reread, then it no longer does.
- AC-6 (FR-7): Given the Customer Agreement playbook, when this feature is done, then it is
  unchanged except where it shared a rule that now must not disagree.
- AC-7 (FR-8): Given the saved SOW check that quotes bracket placeholders, when the check is
  re-run, then the result is replaced, dated, and no longer quotes text that is not in the SOW.
- AC-8 (FR-9): Given a template changed after its last check, when Legal opens the Templates page,
  then the page shows the check as older than the change.
- AC-9 (FR-10, FR-11): Given a contract drafted before this feature with a payment-terms finding,
  when this feature ships, then the contract text and finding are unchanged.
- AC-10 (FR-12, FR-13): Given a blank governing law, and given "Maharashtra, India", when an NDA, an
  MSA, a Vendor, a SaaS and a Consultancy draft are made, then each says Indian law and none says
  Delaware or Maharashtra as the law.
- AC-11 (FR-14): Given a legal entity registered in Mumbai, when an NDA is drafted, then the draft
  names Mumbai as the seat.
- AC-12 (FR-15): Given an SOW and a DPA drafted under a master agreement, then neither states its
  own law; both point to the master or principal agreement.
- AC-13 (FR-17): Given the legal-entity register holds our signatory and the request gives their
  signer, then both names and titles appear; given neither, then plain blank lines appear.
- AC-14 (FR-18): Given a register with no address for a party, then the notice line is a blank to
  fill by hand and no address is invented.
- AC-15 (FR-19): Given payment periods 30, 45, 60 and 90 on an MSA, Vendor and Consultancy
  request, then each draft carries the 45-day MSME rule and none shows a finding.
- AC-16 (FR-20): Given a Consultancy draft, then it has one GST position and no clause makes the
  consultant responsible for GST while another has us paying GST on top.
- AC-17 (FR-21): Given a request in USD, when an MSA or SOW is drafted, then the text and the check
  use USD and no figure in another currency appears.
- AC-18 (FR-22): Given an MSA, Vendor and Consultancy draft, then each states an insurance minimum
  and the playbook checks it; a draft with no amount is flagged.
- AC-19 (FR-23): Given any draft with a breach clause, then the window is the Q1 window; and given
  a counterparty's paper with a longer window, then it is flagged.
- AC-20 (FR-24): Given personal data yes, then the draft says the DPA applies from the start; given
  no, then it says a DPA is needed before processing, and the playbook shows no DPA finding.
- AC-21 (FR-25): Given scope text "This is a test", then the draft is not made with that text as
  the scope and the requester sees a plain message.
- AC-22 (FR-26, FR-27, FR-28): Given NDAs of 2, 3 and 5 years, mutual and one-way, then the term
  reads in the same word style, survival is stated, no clause contradicts another, and there are
  no findings; given an NDA with no survival, then it is flagged.
- AC-23 (FR-29, FR-30): Given personal data yes and no on an NDA, then the data-protection sentence
  differs as stated; and the recital reads "explore a potential business relationship".
- AC-24 (FR-31): Given an NDA with a non-compete or an indemnity, then the playbook flags it.
- AC-25 (FR-32, FR-34): Given an MSA and an SOW that sets service levels with no credit, then the
  default credit and cap apply and no service-level finding is raised; given a project SOW with no
  levels, then the MSA is not flagged.
- AC-26 (FR-33): Given "fees paid or payable in the preceding 12 months", then the liability rule
  passes.
- AC-27 (FR-35): Given an SOW with each of fixed, milestones and time and materials, then only that
  model prints; given no pricing answer, then the draft is held with a message.
- AC-28 (FR-36): Given a time-and-materials SOW, then rates are stated or the stated fallback
  applies; the playbook does not flag it for lacking milestones.
- AC-29 (FR-37, FR-38, FR-39): Given an SOW with a chosen master agreement, then the draft names it,
  uses its payment period and says whether service levels apply; given no provider representative,
  then the notify-in-writing line prints.
- AC-30 (FR-40, FR-41): Given a Vendor request for goods, services and both, then each draft has
  the matching clauses and no clause for the other kind, and the data text follows personal data.
- AC-31 (FR-42): Given a SaaS draft, then no clause makes a data-breach loss both capped and
  uncapped, and the playbook raises no cap finding.
- AC-32 (FR-43): Given hosting India, EU, US and several, then the draft names the matching
  location; for a non-India location it names the approval step; the rule flags an unnamed one.
- AC-33 (FR-44): Given auto-renew with notice 30 and with notice 60, then every clause on notice
  shows the same period (at least 60) and no renewal finding.
- AC-34 (FR-45): Given a SaaS draft, then it has the continuity, suspension, sub-processor
  objection and reverse-charge wording and a billing period.
- AC-35 (FR-46): Given the Vendor and SaaS playbooks, then they hold the listed new topics.
- AC-36 (FR-47): Given a Consultancy draft, then it has each listed clause, and personal data
  decides the data-protection sentence.
- AC-37 (FR-48): Given a Consultancy paper with a non-compete, then it is flagged; given advance
  payment wording that protects us, then it is not.
- AC-38 (FR-49): Given a DPA with a chosen principal agreement, then it names it and has no separate
  term; given DPDP-only data, then the transfer text restricts only listed countries and asks for
  no SCCs; given EU/UK data, then it requires SCCs or an equivalent.
- AC-39 (FR-50): Given DPA wording that uses "Data Principal" and "erasure", then the rules pass;
  given a 72-hour breach window, then it is flagged.
- AC-40 (FR-51, FR-52): Given each loosened rule, then its must-pass wording passes and its
  must-still-flag wording is flagged, on our paper and on a counterparty's paper.
- AC-41 (FR-53): Given a change to a template or playbook, then a new version exists with its
  reason and the earlier version is unchanged.
- AC-42 (FR-54): Given an organisation with its own saved NDA version, when the new standard ships,
  then its version stays active, Legal sees it is older than the standard, and the check result
  on it is shown.
- AC-43 (FR-55): Given a template edit with a placeholder nothing fills, then the save is refused.
- AC-44 (Permissions): Given a person without the permission to edit playbooks or templates, then
  they cannot publish or restore a version; given a person in another organisation, then they
  cannot see these versions or results.
- AC-45 (Audit): Given a new version or a check run, then the audit trail shows who, when, what,
  and why; given someone only viewing, then nothing is written.

## Out of scope

- New questions on the forms. The next feature (per-type forms) will add: governing law for every
  type, dispute seat, our signer and signer titles, MSME status and registration number, NDA
  purpose and survival, NDA arbitration option, SOW rate card and service levels, SaaS billing
  period and renewal notice, consultancy residency and individual-or-firm, how a fixed fee is
  invoiced, DPA privacy contact, EU/UK data, children's data and "Other" in the lists, payment
  options hidden for MSME, a "stamp duty paid by" question, and "Privacy approved this location"
  for SaaS. Until then this feature uses the stated defaults (Indian law; seat = our registered
  city; blanks where data is missing).
- Changing the form's layout, labels, or which questions apply.
- The Customer Agreement playbook (no template), beyond FR-7.
- Changing or re-reviewing any existing contract, request, finding or suggested change (FR-10).
- New templates, new contract types, other countries' law, or other languages.
- New roles or permissions, or changes to who may see contracts.
- How suggested changes for playbook findings are written (the issue-fix drafting).
- Final legal sign-off. This spec uses the market-standard positions from the drafting review;
  items the review marked for counsel are the decisions in Open questions.

## Edge cases & error behavior

- An organisation edited a template (a saved version exists): the edit stays active; the shipped
  text changes only the default (FR-54). The check shows its findings against the playbook.
- An organisation edited a playbook after this change: its edit is a new version; the check shows
  whether the shipped templates still pass it. Nothing is overwritten or reverted by this feature.
- A counterparty's own paper (not our template): still reviewed under the playbooks, and the
  loosened rules still catch the risky versions (FR-51).
- Non-INR value: draft and check use that currency; no INR or USD figure that contradicts it.
- Foreign counterparty: our paper keeps Indian law as default; the dispute route follows Q2; foreign
  provider wording (reverse charge, treaty certificate, data location) applies.
- Personal data "No" but processing starts later: the draft requires a DPA before any processing
  (FR-24); the playbook does not flag it.
- Test text in the scope ("This is a test"): the draft is held with a message (FR-25).
- Blank answers (no law, no signer, no pricing, no register data): blank lines or a held draft,
  never an empty label (FR-5, FR-17).
- An answer valid on the form but rejected by a playbook: the answer passes or is no longer offered
  (FR-3); a half-filled saved draft that holds a removed option shows a plain message and asks the
  requester to pick again.
- An existing contract drafted before this feature still shows old findings (FR-11).
- The automated check cannot run (service down): the page says the result is old, and no template
  is shown as passing.
- Two people save a template or playbook at the same moment: both versions are kept in order; the
  newest wins as today.

## Open questions

None open. Decisions recorded (the user accepted the recommended answer for every question,
2026-10-27). Counsel may still change a position before the build; each is one place to change.

- [x] Q1 Breach notice: 24 hours, and 6 hours where a CERT-In report is required. Applies to MSA,
  Vendor, SaaS, Consultancy and DPA; the NDA keeps "without undue delay".
- [x] Q2 Disputes: Indian law; one arbitrator under the Arbitration and Conciliation Act; seat in the
  entity's registered city (MCIA Mumbai for an Indian provider; SIAC, Singapore, for a foreign
  one); urgent relief from any court. Individual consultants get the courts at the seat. The NDA
  uses the courts at the seat.
- [x] Q3 Insurance: INR 5 crore per claim professional indemnity; INR 5 crore cyber where personal
  data or our systems are involved; goods vendors the greater of contract value and INR 1 crore.
- [x] Q4 Service credits: 5% of the month's fees per failure, capped at 15% a month.
- [x] Q5 Liability: general cap is the greater of fees paid or payable in the last 12 months and
  first-year fees; data-breach claims super-capped at 3 times (greater of 3x the cap and a floor)
  in SaaS and DPA, stated once so nothing is both capped and uncapped; confidentiality, IP
  indemnity, fraud, wilful misconduct and gross negligence stay uncapped; the uncapped indemnity
  for "any breach of the agreement" is narrowed to specific breaches.
- [x] Q6 Stamp duty: each side bears its own costs; the Company bears stamp duty (Consultancy; the
  NDA defaults to the Company until the next feature asks).
- [x] Q7 DPA transfers: DPDP data may go anywhere except government-restricted countries, with no
  special clauses; EU/UK data needs SCCs (Module Two, or Three for a sub-processor) plus the UK
  Addendum, Irish law and courts for the clauses.
- [x] Q8 DPDP fines: the supplier pays a fine caused by its staff under the data indemnity, within
  the Q5 super-cap.
- [x] Q9 NDA AI/ML ban: kept in every mutual NDA, with an exception for business tools that do not
  train on our data. The "without bond" wording reads "to the extent permitted by law".
- [x] Q10 Goods and delay: delay damages of 0.5% of the late item's fees per week, capped at 10%;
  we may end the order after 30 days' delay; risk passes on our acceptance; on convenience
  termination we pay accepted deliverables only.
- [x] Q11 Staff and IP: keep the 12-month mutual non-solicit with the job-advert exception; add
  moral-rights non-assertion; flag the IP power of attorney for stamping.
- [x] Q12 Data location: a foreign provider may keep data abroad only in a named location the
  privacy team approved, never a government-restricted country.
- [x] Q13 SaaS billing: quarterly in arrears unless the order form says otherwise.
- [x] Q14 NDA purpose: keep the generic "explore a potential business relationship" line until the
  next feature brings the purpose question back.
- [x] Q15 Organisations' own edits: kept; Legal is warned they are older than the standard, with the
  check result and a one-click reset to standard.
- [x] Q16 Payment options: MSA and Consultancy 45 and 60 only; Vendor and SaaS 45, 60 and 90, no
  30; every draft has the MSME 45-day line.
- [x] Q17 Customer Agreement playbook: left alone except where it shares a rule that must not
  disagree.
- [x] Q18 Test text: draft is held; real scope text of at least about 15 words is required.
- [x] Q19 Time and materials without a rate card: SOW points to the master agreement's rates, or
  requires a signed change order before any charge, until the next feature adds rates.
- [x] Proof of "zero findings": the full fixed matrix with no AI, plus the AI review 3 times on each
  template's main combinations (about 40 runs, a few dollars, run deliberately, not in normal CI)
  (FR-1A).

## Context for planning

Non-binding notes for the architect; not requirements.

- Source: the drafting review in `specs/014-template-playbook-alignment/review/` (four reports,
  `summary.html`, `redlines_on_our_paper.json`, `template_checks.json`, `playbooks.json`). Each fix
  in the reports is tagged TEMPLATE, RULE or BOTH; BOTH rows are the pairs FR-4 requires.
- The seven templates ship as text files under `app/drafting_templates/defaults/`, filled by
  `intake/drafting.render_document` and the per-type fillers (`intake/sow.py`, `intake/consultancy.py`,
  `intake/dpa.py`). An org's edits are versions in `drafting_template_version`; the newest wins
  and `body` NULL means back to the shipped default. Saves are refused unless every placeholder
  is one `template_values` fills.
- Playbooks are reviewed by a phrase check (`evaluate_rules_against_text`: `prohibited_language`
  flags the phrase anywhere, `required_language` flags when missing) plus an AI comparison with
  preferred/fallback positions. FR-1A fixes the proof: full matrix by the phrase check (cheap,
  can sit in tests), plus a deliberate 3x-per-main-combination AI run (about 40 runs, real cost,
  outside CI, like the manual eval workflow).
- Several fixes are code in the fillers (governing-law fallback and state-to-country mapping near
  `drafting.py` line 178, nda term words, SOW pricing blocks, hosting clause, signature blocks from
  the legal-entity register). The next feature (per-type forms) will add the questions listed in
  Out of scope; this spec's defaults are meant to be replaced by those answers without changing the
  wording rules.
- The consultancy and other templates give different breach windows today (72h, 48h); Q1 should set
  one number so templates and playbooks agree.
- Constitution notes: playbook and template edits are mutations and follow the audit pattern
  (`write_audit_log` with the audit-lock caution in CLAUDE.md); new versions, not edits in place.
- Reviewer's items marked [Lawyer] are decisions Q1 to Q12 below; the review's stale SOW check
  (15 Oct) quotes bracket placeholders that are not in today's SOW.
