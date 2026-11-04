# Consultancy + DPA review — client side / Controller side, India

*Drafting review produced 27 Oct 2026 by an AI reviewer; not legal advice. **[Lawyer]** = judgement call.*

Findings are made by a literal phrase check (`playbooks/service.py::evaluate_rules_against_text`: `prohibited_language` flags the phrase anywhere; `required_language` flags when missing) plus the AI review using preferred/fallback positions. Latest checks: 0 findings for both templates; no redlines on our paper for either. Fills: `drafting.py::template_values`, `intake/consultancy.py`, `intake/dpa.py`.

# A. CONSULTANCY

## A1. Template vs industry standard
Strong: independent-contractor clause (3), conflicts (4), present assignment of IP (8.1), confidentiality 5-year tail + trade secrets indefinitely (9.6), mutual non-solicit with job-ad carve-out (10), cap with carve-outs (12.3), anti-bribery (15.1), company contact "does not direct or supervise".
1. **GST contradiction (3.2 vs 6.6):** 3.2 consultant "solely responsible" for GST on Fees; 6.6 Company pays GST on top; no ITC protection; no reverse charge for unregistered/offshore.
2. **TDS (6.6):** generic (fine); non-resident needs TRC, Form 10F, no-PE [Lawyer]; Income-tax Act 2025 renumbered — keep generic.
3. **Governing law (13) — biggest defect:** not asked; entity jurisdiction else Delaware; "courts of competent jurisdiction under the laws of the State of Delaware"; no seat; no arbitration.
4. **No insurance clause.**
5. **No re-performance or acceptance** (2.2(a); Schedule deliverables).
6. **IP under Indian copyright (8):** s.19(3) consideration; s.19(4) lapse; "full title guarantee" English-law term; moral rights (s.57) — add non-assertion [Lawyer]; POA in 8.4 may need stamping.
7. **Misclassification / contract labour (3):** no non-exclusivity, no fixed-hours/ID/email ban, no CLRA compliance on site.
8. **Data protection (9.8):** 72h notice (DPDP Rule 7) contradicts our DPA's 48h; unconditional even when personal data = No.
9. **Fixed fee "monthly in arrears" (6.2).**
10. **Day/hourly rate** doesn't define a day, part days, travel.
11. **Notices (14.1)** — no address in the agreement.
12. **Stamp duty** — nothing [Lawyer].
13. **Liability (12):** personal-data breach uncapped only incidentally — name it.

## A2. Playbook
- Missing: governing_law/dispute_resolution (why Delaware isn't flagged), insurance, indemnity (misclassification, tax), data_protection (DPDP), warranties/re-performance, key_personnel, tax (GST credit, TDS), assignment/subcontracting.
- `payment_terms`: 45-day fallback is the MSMED legal ceiling; rationale should say why.
- Prohibited "payable in advance" flags protective wording → null.
- Prohibited "unlimited liability" flags market carve-outs → null; preferred says "paid", template "paid or payable" — align.
- `termination.fallback` 15 → 60 days jump; use 30.
- `non_solicitation` fine [Lawyer] s.27; add `non_compete` (prohibited).
- `intellectual_property.fallback` "assignment on payment" only with licence until payment.
- `required_language` keywords ("terminate", "confidential", "intellectual property") give false comfort.

## A3. Conflicts
Unfilled template passes all 8 rules. Only from answers: payment 60/90 → finding (correct); Delaware → no finding (no rule — silent defect); "paid or payable" vs "paid" → possible AI comment.

## A4. Fixes
- **C1:** 3.2 "The Consultant is solely responsible for all income tax, social security, provident fund, employees' state insurance, gratuity and other statutory contributions in respect of its Personnel, and for charging, depositing and reporting goods and services tax on the Fees. It shall indemnify the Company against any such liability, interest and penalties assessed against the Company."
- **C2:** add to 6.6 "Each invoice shall show the Consultant's GSTIN and comply with the GST law. If the Company loses input tax credit because the Consultant fails to deposit tax or report the invoice, the Consultant shall reimburse that credit with any interest. Where GST is payable by the Company under reverse charge, the Fees are inclusive of it. A non-resident Consultant shall provide its tax residency certificate and Form 10F before the first payment."
- **C3:** drop Delaware (default India, `drafting.py` ~178; map entity jurisdictions to the country). 13.2 "...any unresolved dispute shall be finally resolved by arbitration under the Arbitration and Conciliation Act, 1996 by a sole arbitrator appointed by the Parties. The seat shall be {dispute_seat} and the language English. Subject to this, the courts at {dispute_seat} have exclusive jurisdiction." `{dispute_seat}` default entity's registered-office city [Lawyer: courts vs arbitration for domestic individuals].
- **C4:** rule `governing_law` (high): preferred Indian law + arbitration or courts at a named Indian city; fallback Singapore/English law with SIAC/LCIA for a foreign consultant; required "laws of India".
- **C5:** 2.5 "If any Services or Deliverables do not comply with this Agreement, the Consultant shall, at the Company's option, re-perform them at no cost within ten (10) business days of notice, or refund the Fees paid for them." Schedule: "Each Deliverable is accepted when the Company confirms in writing that it meets the agreed description."
- **C6:** 12A "The Consultant shall maintain professional indemnity insurance of not less than [amount] per claim, and public liability insurance, during the term and for two (2) years after, and provide evidence on request." Rule `insurance` (medium) [Lawyer amount].
- **C7:** 8.1 "with full title guarantee" → "free of all encumbrances"; add "The Fees are the full consideration for this assignment. Section 19(4) of the Copyright Act, 1957 does not apply, and the assignment does not lapse for non-exercise."; 8.2 add "and undertakes not to assert them".
- **C8:** 3.4 "The Consultant is free to provide services to others (subject to Clause 4), is not required to work fixed hours, and shall not hold itself out as an employee. Where its Personnel attend the Company's premises, the Consultant is their employer for all purposes and shall comply with all labour laws applicable to them, including any registration or licence required." Rule `independent_contractor.required_language` = "independent contractor".
- **C9:** 9.8 "without undue delay and in any event within forty-eight (48) hours" + "and, where the Company so requires, shall enter into the Company's standard data processing agreement". Rule `data_protection` (high): 48h preferred, no fallback.
- **C10:** 6.2 "monthly in arrears, or for a fixed fee on acceptance of each Deliverable in the proportions in the Engagement Schedule"; rates: "A day is eight (8) hours. Part days are charged pro rata. Travel time is not chargeable unless agreed in writing."
- **C11:** "Address for notices:" lines and party descriptions with registered office from the registers; "The Company shall bear any stamp duty." [Lawyer]
- **C12:** `payment_terms.prohibited_language` → null + MSMED rationale; `limitation_of_liability` "fees paid or payable", carve-outs incl. death/personal injury and data breach, prohibited → null; `termination.fallback` → 30 days; add `non_compete`.

## A5. Form needs
Governing law/seat (not asked — add, or C3); signature names (theirs from `cp_signer_name`), notice addresses from registers; new "consultant resident outside India?" (C2, C3); new "individual or firm?" (misclassification, CLRA, TDS); "how is a fixed fee invoiced?"; consider hiding "Renews automatically"; drop `{value_sentence}` (duplicates `{fee_amount}`); payment hint about MSME > 45 days. Unused: `personal_data` (should switch on C9 DPA sentence), `gxp` (no clause), department, needed_by, note_approvers, cp_signer_email.

# B. DPA (we are the Controller)

## B1. Template vs industry standard
Strong: every GDPR Art. 28(3) element (3.1, 3.3, 4, 5 + Annex 2), prior specific authorisation of sub-processors (6.1–6.3), data subject rights incl. DPDP grievance/nomination (7.1), 48h breach notice (8.1), audits not replaced by certificates (10.2–10.3), DPIA assistance (10.5), deletion with certificate (11), no AI training (3.4), 12-month logs (DPDP Rule 6).
1. **Principal Agreement unnamed (Recital A)** though `parent_contract_id` asked.
2. **Term (13.1):** no dates asked → "continues until terminated", contradicting Annex 1 "Duration: the term of the Principal Agreement"; Effective Date = drafting day.
3. **Governing law (14.1):** Delaware/entity fallback; "courts of India" no court; market: follow the Principal Agreement.
4. **Transfers (9.2) wrong for DPDP-only data:** DPDP s.16 allows transfer anywhere except restricted countries, no SCC equivalent; clause unworkable; 9.3 picks no SCC module/options/annex mapping; no UK Addendum [Lawyer: SCCs where importer subject to GDPR Art. 3(2)].
5. **Breach content (8.2):** missing time/location, cause, person responsible (DPDP Rule 7); 14.2 privacy contact unnamed.
6. **Children's data:** nothing on DPDP s.9; form can't say "children".
7. **Annex 1 lists closed** but form has no "Other".
8. **Liability (12):** uncapped indemnity incl. fines; super-cap is the usual result [Lawyer: DPDP penalties recoverable?].
9. **Missing:** records of processing (Art. 30(2)), government access notice, processor DPO/contact, erasure deadline (DPDP s.8(7)).
10. **Sub-processor country vs transfer countries** not cross-checked.

## B2. Playbook
- `breach_notification.fallback` 72h leaves zero time (DPDP) → null [Lawyer: 24h first notice].
- `international_transfers.preferred_position` "SCCs for transfers out of India" — inaccurate under DPDP; omits UK.
- Missing: liability, term, processing_description, confidentiality_of_personnel, assistance/DPIA, children_data, no_own_use, governing_law.
- `required_language` keywords fail on DPDP wording ("data subject" vs "Data Principal", "sub-processor" vs "subprocessor", "international transfers" vs "cross-border", "deletion" vs "erasure").
- `sub_processors.fallback` market; add termination if objection unresolved.
- `audit.fallback` keep on-site audits after a breach or at a regulator's demand.
- `data_subject_rights` add timing (forward within 2 business days; assist within 5).

## B3. Conflicts
Passes all 8 rules; latent: rewording cl. 9 in DPDP terms breaks `international_transfers.required_language`; consultancy 9.8 (72h) vs DPA (48h) incoherent across playbooks.

## B4. Fixes
- **D1:** Recital A "...under the {principal_agreement} (the "Principal Agreement")", "[title] dated [date]" from `parent_contract_id`.
- **D2:** 13.1 "This Agreement takes effect on the Effective Date, applies to all Processing under the Principal Agreement whether before or after that date, and continues until the later of the termination or expiry of the Principal Agreement and the return or deletion of all Personal Data under Section 11." Remove `{agreement_term}`.
- **D3:** 14.1 "This Agreement is governed by the law that governs the Principal Agreement, and disputes are resolved under the dispute-resolution clause of the Principal Agreement, save that the Standard Contractual Clauses, where they apply, are governed by the law and forum they specify." Remove `{governing_law}`.
- **D4:** 9.2 "(a) For Personal Data subject to the DPDP Act, an authorised transfer shall not be made to any country or territory restricted by the Central Government under section 16, and shall comply with any conditions prescribed under it. (b) For Personal Data subject to the GDPR or UK GDPR, the transfer shall be made only under the Standard Contractual Clauses (Module Two, or Module Three for a Sub-processor), the UK International Data Transfer Addendum, or another safeguard under Chapter V GDPR, with any supplementary measures needed." 9.3: Clause 7 included; Clause 9 option 1; Clause 13 exporter's supervisory authority; Clauses 17/18 Ireland; Annexes I–III = Annexes 1–3 [Lawyer: SCC governing law].
- **D5:** rule `international_transfers` preferred "No transfer outside India without our written authorisation and never to a country restricted under DPDP s.16. For EU/UK data, SCCs or the UK Addendum (or another Chapter V safeguard)."; required → "transfer".
- **D6:** 8.2 add "(f) the date, time and location of the breach and when it was discovered; (g) its cause and, if known, the person responsible"; 14.2 names `{privacy_contact}`.
- **D7:** rule `breach_notification.fallback` → null (DPDP Rule 7).
- **D8:** 3.5 "The Processor shall not undertake tracking, behavioural monitoring or targeted advertising directed at children, and shall process children's Personal Data only as the Controller instructs in accordance with section 9 of the DPDP Act." Rule `children_data` (high).
- **D9:** form "Other (describe)" for subjects and data types; check sub-processor countries ⊆ India ∪ `transfer_countries`.
- **D10:** rule `liability` (high): preferred uncapped for data protection breaches; fallback super-cap ≥3x annual fees, never below the general cap [Lawyer].
- **D11:** 10.6 "The Processor shall keep a record of its Processing as required by Applicable Data Protection Law, notify the Controller promptly (where lawful) of any request by a public authority for access to Personal Data, and erase specific Personal Data within ten (10) business days of the Controller's instruction."
- **D12:** required language: `data_subject_rights` → "request" (or null), `return_or_deletion` → null, `sub_processors` → "processor"; add rules term, processing_description, no_own_use.

## B5. Form needs
Parties from the parent contract (fallback if none); `{agreement_term}` and `{governing_law}` deleted (D2, D3); new `{principal_agreement}` from `parent_contract_id`; `{data_subject_categories}` add "Children (under 18)" and "Other"; data types "Other"; new `{privacy_contact}` (question or org settings); new "EU/UK personal data involved?"; signature names and notice addresses. Unused: `our_role` (gate only, correct), needed_by, note_approvers, cp_signer_email.
