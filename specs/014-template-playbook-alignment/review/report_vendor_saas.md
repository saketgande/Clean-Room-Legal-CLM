# Vendor + SaaS review — customer side, India

*Drafting review produced 27 Oct 2026 by an AI reviewer; not legal advice. **[Lawyer]** = judgement call.*

Template checks: 0 findings for both. No redlines on our paper for vendor/saas. `governing_law` is asked only for NDAs, so vendor/SaaS take the entity's jurisdiction or "the State of Delaware"; a state name ("Maharashtra") produces wrong text.

# A. VENDOR

## A1. Template vs industry standard (11)
Strong: our terms win (1.2, 19.1), time of essence (2.3), 30-day inspection with rejection remedies (3.2–3.3), uncapped indemnities, audit (14), compliance termination (6.4), convenience termination (15.4).
1. **Disputes (18.2):** "courts of {governing_law}" → "courts of India" (no city), no arbitration; "laws of Maharashtra"; blank → Delaware.
2. **MSME law:** MSMED s.15 45-day limit; s.43B(h); form offers 60/90.
3. **GST ITC (4.1):** no e-invoice/GSTR-1/payment duty, no ITC indemnity.
4. **Labour:** principal-employer exposure (CLRA, EPF/ESI); no statutory-dues proof or indemnity.
5. **Delay remedy:** time of essence but no liquidated damages.
6. **Goods mechanics:** no Incoterms, short/over delivery, recall; risk on acceptance (pushback) [Lawyer].
7. **Cap size (11.1):** "fees paid and payable … preceding 12 months" near zero early.
8. **Uncapped indemnity breadth (11.3(b) + 10.1(a),(c))** [Lawyer].
9. **Breach notice (7.4):** 72h; DPDP Rules 72h for us; CERT-In 6h.
10. **No insurance amounts (12.1).**
11. **Convenience termination (15.4)** pays accepted deliverables only, not WIP [Lawyer].

## A2. Playbook (8)
- `limitation_of_liability`: mutual or not unclear; fallback = preferred.
- `payment_terms`: reads "exactly 45" (90 flagged); "60 days strategic" fallback illegal for MSME.
- `warranties`: required "service levels" doesn't fit goods.
- `data_protection`: 72h too slow.
- `compliance`: required word met by the heading.
- Missing: delivery & acceptance, title & risk, IP, insurance, termination, governing law, GST, MSME, delay damages.
- Audit, subcontracting, indemnity fine.

## A3. Conflicts (only with certain answers; 0 today)
payment_terms 30/60/90 flagged; personal_data = No → 7.2 DPA promise flagged (as on MSA); goods-only removal of 5.2 trips `warranties`.

## A4. Fixes
**Template**
- 18.2: "…finally resolved by arbitration under the Arbitration and Conciliation Act, 1996 by a sole arbitrator; seat and venue {dispute_seat}; language English; subject to this, the courts at {dispute_seat} have exclusive jurisdiction, including for interim relief." Code: map Indian state → "India"; fallback India [Lawyer].
- 4.6 GST: "The Vendor shall issue GST-compliant tax invoices (including e-invoices where required), report each correctly in its GST returns and pay the tax collected… If the Company is denied or must reverse input tax credit because the Vendor failed to do so, the Vendor shall reimburse it with interest and penalty, and the Company may withhold or set it off."
- 4.7 MSME: "If the Vendor is a micro or small enterprise registered under the MSMED Act, 2006 and has declared this in writing, payment shall be made within the period that Act requires and in any event within 45 days of acceptance." Cap `payment_days` at 45 for MSME.
- 2.4 LDs: 0.5% of the delayed Deliverable's Fees per week, cap 10%, genuine pre-estimate; terminate if delay > 30 days [Lawyer].
- 6.5 personnel: Vendor solely responsible for wages and statutory dues (PF, ESI, gratuity, bonus), complies with the Contract Labour Act or replacing labour code, evidence on request; 10.1(e) claims by Vendor personnel/subcontractors.
- 11.1 cap: "the greater of (i) Fees paid and payable in the preceding 12 months and (ii) total Fees payable under all Orders in the first 12 months".
- 11.3(b) [Lawyer, optional]: narrow to 10.1(b),(d),(e); 3x super-cap for 10.1(a),(c).
- 7.4: 24 hours; 6 hours for CERT-In reportable.
- 7.1–7.2 by personal_data: Yes → "The Parties have entered into the DPA in Schedule 1, effective on the Effective Date"; No → "The Parties do not expect the Vendor to process personal data; before any such processing begins they shall sign a DPA on the Company's template."
- 12.1: "limits not less than {insurance_minimum} per claim"; default greater of contract value and INR 1 crore [Lawyer].

**Using `buying`:** `{supply_description}` in Background + Deliverables definition; `vendor_values(f)` blocks. Goods: DAP (Incoterms 2020) delivery, quantity rejection, recall; drop 5.1(d), PI insurance, 6.5. Services: acceptance on written confirmation; drop packing, title/risk, product liability. Both: keep all.

**Playbook:** `limitation_of_liability` mutual cap ≥12 months' fees (greater of trailing/first-year); uncapped for confidentiality, IP/data/breach-of-law indemnities, gross negligence, wilful misconduct, fraud; fallback super-cap ≥3x. `payment_terms` "no sooner than 45 days, longer acceptable; declared MSME statutory ≤45"; fallback "30 days for strategic non-MSME with Finance approval". `warranties` required "warrants" ("service levels" only for services). `data_protection` 24h; "no personal data expected" acceptable. New: `delivery_and_acceptance`, `governing_law`, `insurance`, `tax_compliance`.

## A5. Form needs
Parties, effective date, term fields, payment (hide 60/90 for MSME), value; **governing law not asked — add, default India**; new `{dispute_seat}`; scope blocks from `buying`; new MSME (Udyam) question; `{insurance_minimum}` derived; cl. 7 from `personal_data`; signature names (theirs + our register signatory); notice addresses from registers. Unused: needed_by, department, paper, cp_signer_email, note_approvers; `gxp` = Yes should add a Quality Agreement clause.

# B. SOFTWARE / SaaS

## B1. Template vs industry standard (10)
Strong: no click-through, 99.5% SLA with credits + termination, 5%/90-day price cap, renewal reminder, AI-training ban, ISO 27001/SOC 2 + encryption, India hosting, 30-day exit, IP indemnity.
1. **Liability contradiction (15.2 vs 15.4):** Customer Data is Confidential Information; confidentiality (15.4(a)) and the cl. 8 indemnity (14.1(b), 15.4(b)) uncapped → the 3x data-breach super-cap never applies.
2. **BC/DR referenced (21.3), never defined;** no backups, RPO, RTO.
3. **First-year cap near zero (15.1, 15.2).**
4. **Breach notice (8.3) 72h.**
5. **No suspension clause** (pre-empt a broad provider one).
6. **Sub-processor objection (9.3)** has no remedy.
7. **Billing period undefined (4.2)** [Lawyer/Finance].
8. **Foreign providers:** reverse charge, gross-up, DTAA.
9. **Hosting fixed at India (9.1)** regardless of `hosting`.
10. **No overage, escrow, convenience termination** [Lawyer].

## B2. Playbook (7)
- `limitation_of_liability`: 3x data cap vs uncapped confidentiality/indemnities — contradicts itself.
- `payment_terms`: "no advance above 20%" unrealistic for SaaS; "exactly 45".
- `data_location`: India or EU only; US common; DPDP s.16 restricts only blacklisted countries; SCCs are GDPR — require a named location + Privacy approval.
- `data_security`: 72h; required phrase met by the heading.
- Missing: subprocessors, data_use/AI training, ip_indemnity, suspension, governing_law, BC/DR.
- Fine: service_levels, price_increases, renewal, exit_and_data_return.

## B3. Conflicts (only with certain answers; 0 today)
1. Auto-renew with `notice_days` = 30: 6.1 says 30, 6.2 says 60; `renewal` rule flags fallback; 6.1 renews the Agreement, 6.2 the Subscription Term.
2. `payment_terms` ≠ 45 flagged; no 60-day fallback.
3. `hosting` = US/several: 9.1 still India (mismatch); writing US would be flagged under the current rule.
4. 15.2 vs 15.4 → "ambiguous cap" finding risk.

## B4. Fixes
**Template**
- 15.4 [Lawyer, choose]: "Liability arising from a Security Incident or breach of clauses 8 or 9 is governed exclusively by clause 15.2, even where it is also a breach of clause 11 or falls under 14.1(b)"; super-cap "the greater of 3x the General Cap and {data_breach_floor}". Alternative: delete 15.2 and change the rule.
- 15.1: same greater-of trailing/first-year floor.
- 8.6 BC/DR: maintain and test annually; daily backup to a separate in-region location; RPO 24h, RTO 8h unless the Order Form says otherwise.
- 8.3: 24h; 6h CERT-In.
- 4.5 suspension: only (a) to the extent strictly necessary for an imminent security threat, briefly, with notice; or (b) undisputed Fees > 30 days overdue after two written notices 15 days apart.
- 9.3 add: unresolved objection in 30 days → Customer may terminate the affected Order Form without liability, refund of prepaid Fees.
- 4.2: "quarterly in arrears unless the Order Form states otherwise".
- 4.1 add: provider outside India → Customer pays GST under reverse charge; invoice sufficient for that.
- 6.1: "…renews in accordance with clauses 6.2 and 6.3"; 6.2 notice `{renewal_notice_days}` = larger of `notice_days` and 60.

**Using `hosting`:** 9.1/9.2 → `{hosting_clause}`, each keeping "no other location without approval". India: current text. EU: EU data centres + SCCs for GDPR data. US: US data centres "as approved by the Customer's privacy function", no transfer to a DPDP s.16-restricted country. Several: regions in the Order Form, approved by Privacy.

**Playbook:** `limitation_of_liability` general cap ≥12 months (greater of trailing/first-year); data breach (incl. via confidentiality or data indemnity) super-capped ≥3x; IP indemnity, other confidentiality, gross negligence, wilful misconduct, fraud uncapped. `payment_terms` no sooner than 45 days; arrears, or quarterly/annual advance with pro-rata refund; annual advance > INR 10 lakh needs Finance; remove "payable in advance". `data_location` named location, India preferred, others with Privacy approval, no s.16 country, SCCs for GDPR. `data_security` 24h, required "Security Incident". New: subprocessors, data_use, ip_indemnity, suspension, business_continuity, governing_law.

## B5. Form needs
Parties/dates/value/payment as vendor; term + 6.2 notice (default 60, show 30 as "needs approval"); governing law and seat (add); `{hosting_clause}` from `hosting` + "Has Privacy approved this location?" when not India; billing period (optional); 8.4 DPA from `personal_data`; `{data_breach_floor}` derived from value [Lawyer]; signatures and notice addresses. Unused: needed_by, department, paper, cp_signer_email, note_approvers; `gxp` = Yes should add a computerised-system validation clause (Annex 11 / Part 11).
