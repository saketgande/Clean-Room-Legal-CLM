# MSA + SOW review — `msa.txt` / `sow.txt` vs their playbooks (customer side, India)

*Drafting review produced 27 Oct 2026 by an AI reviewer; not legal advice. **[Lawyer]** = judgement call for counsel.*

Fills: `drafting.py::template_values`, `intake/sow.py::sow_values`.

# A. MSA (9 rules)

## A1. Template vs industry standard
Solid customer base: strong warranties (3.1), written-only acceptance (4.1), convenience termination (8.1), IP vests on payment, 5-year confidentiality, DPDP-aware cl. 10, 12-month-fees cap with wide carve-outs.

| # | Clause | Problem | Sev |
|---|---|---|---|
| M1 | 19.1/19.3 | Delaware fallback; `governing_law` shown only for NDAs, so MSA gets entity jurisdiction or Delaware; 19.3 "courts... under the laws of X" names no forum; no arbitration (an award is enforceable under the New York Convention, a judgment hard against a foreign provider). | High |
| M2 | 6.3 | Form offers 30/45/60/90 days; MSMED Act s.15 caps micro/small supplier payment at 45 days; Income-tax s.43B(h) disallows late payment. No MSME clause. | High |
| M3 | 6.6 | GST/withholding generic: no GSTIN/CGST invoices, no ITC recovery, no reverse charge on imports, no TRC/Form 10F; currency not stated. | Med |
| M4 | 5.3–5.4 | Service levels, credits, critical levels only in the SOW; SOW has no section; no MSA default credit or cap. | Med |
| M5 | 14.1 | No insurance amounts, no cyber cover. | Med |
| M6 | 11.1 | No Copyright Act s.19(4)/(6) wording (assignment silent on term/territory deemed 5 years, India only; lapses after 1 year non-exercise). | High |
| M7 | 10.4 | 72h processor notice leaves the fiduciary no time (DPDP Rules 72h; CERT-In 6h). [Lawyer] | Med |
| M8 | —/17.1 | No security clause; 17.1 refers to undefined BC/DR obligations. | Med |
| M9 | 16.2 | Audit financial only. | Low |
| M10 | 8.4 | Transition at "rates in the SOW" (none); charged even when we terminate for breach. | Med |
| M11 | 2.3 | Precedence inconsistent with SOW 2.1. | Low |
| M12 | 13.1/13.3 | "Paid or payable in preceding 12 months" is market; tiny early cap, no floor; uncapped confidentiality/data for both sides. [Lawyer] | Med |
| M13 | 12.1(a) | Indemnity for "breach of this Agreement" uncapped via 13.3(b). [Lawyer] | Low |
| M14 | 18.1 | Notices "to the address set out in this Agreement" — none; no CIN/address/GSTIN. | Med |
| M15 | 20.2 | No change-of-control right. | Low |
| M16 | 6.1 | Hard USD cap on umbrella MSA; currency only here. | Low |
| M17 | — | GxP asked, no quality-agreement hook. | Low |
| M18 | 8.5 | 15.2 and 16.2 missing from survival. | Low |

## A2. Playbook vs industry standard
- `payment_terms`: 60-day fallback ignores MSMED; prohibited "payable in advance" false-matches; 30 days worse for us as payer not stated.
- `service_levels`: fallback "credits only, capped at 10%" is a provider position; doesn't accept SOW-defined levels (why every MSA is flagged); project-only SOWs not addressed.
- `limitation_of_liability`: no super-cap option; doesn't say "paid or payable" passes; "2x annual fees" ambiguous.
- `data_protection`: "where personal data is processed" ignored in practice; must accept "DPA required before processing".
- `insurance`: nothing measurable.
- Fine: indemnification, confidentiality, intellectual_property, termination.
- Missing: dispute resolution/governing law, security, subcontracting, audit, transition, taxes.

## A3. Real redlines
| Redline | n | Cause | Fix side |
|---|---|---|---|
| payment_terms 60d/30d vs 45 | 3 | Form offered 60/30 | Form + rule |
| service_levels deferred, no floor | 4 (+check) | MSA defers, SOW can't hold | Both |
| limitation_of_liability "paid or payable" as total value | 1 | Reviewer misread | Rule (keep "paid or payable") |
| limitation_of_liability IP indemnity not named | 1 | 13.3(b) covers cl. 12 | Cosmetic template |
| data_protection DPA unsigned | 1 | Rule applied though no personal data | Rule |
| insurance no amounts | 1 | Real gap | Template + rule |

MSA defers service levels (5.1), credits (5.3), critical levels (5.4), transition rates (8.4), insurance amounts (14.1), expenses (6.7) to the SOW; the SOW supplies none of the first five and points rates back to the MSA.

## A4. Fixes
- **F-M1 (19):** fallback India; show `governing_law` for MSA. 19.1 "This Agreement and any dispute or claim arising out of or in connection with it (including non-contractual disputes) are governed by the laws of {governing_law}." 19.3 "Any dispute not resolved under Clause 19.2 shall be finally resolved by arbitration under {arbitration_rules} by a sole arbitrator appointed under those rules. The seat is {arbitration_seat}, the language is English, and the award is final and binding. Either Party may seek urgent interim relief from any competent court, and the courts at the seat have supervisory jurisdiction." Defaults: Indian provider → A&C Act 1996 / MCIA Mumbai; foreign → SIAC, Singapore or New Delhi [Lawyer]. Rule `dispute_resolution`: preferred Indian law + India seat; fallback SIAC Singapore for foreign; prohibited "exclusive jurisdiction of the courts of" a foreign place.
- **F-M2 (6.3A):** "If the Provider is a micro or small enterprise registered under the MSMED Act, 2006, it shall state its Udyam number on each invoice, and the period in Clause 6.3 shall not exceed forty-five (45) days. The Provider shall notify any change in that status." Rule: preferred 45; fallback 60 only for non-MSE with milestones tied to acceptance; nothing under 30; prohibited → "advance payment exceeding 20%".
- **F-M3 (6.6):** "Fees exclude GST. The Provider shall be GST-registered, state its GSTIN on each invoice and issue tax invoices that comply with the CGST Act, 2017, and shall timely file its returns and pay the tax so the Customer can claim input tax credit. The Provider shall reimburse any input tax credit the Customer loses through the Provider's default, with interest and penalties. For imported Services the Customer pays GST under reverse charge and the Fees do not increase. The Customer may withhold tax required by law and shall provide certificates. A reduced treaty rate applies only on receipt of a valid tax residency certificate, Form 10F and any other document the law requires. Fees are payable in the currency stated in the Statement of Work."
- **F-M4 (5.5):** "Where a Statement of Work sets Service Levels but no service credits, the credit for each failure in a month is five percent (5%) of the monthly Fees for the affected Services. Total credits in any month shall not exceed fifteen percent (15%) of the monthly Fees under that Statement of Work. Where a Statement of Work sets no Service Levels, Clauses 5.1–5.5 apply only to Service Levels later agreed by a signed change order." Rule preferred: SOW-set levels; MSA default credit, at-risk cap ≥10%, termination for repeated/critical failure; project SOWs need none. Fallback 10% cap + termination. 15% vs 10% [Lawyer].
- **F-M5 (14.1):** "...limits of not less than INR 5,00,00,000 per claim and in aggregate (or the equivalent in the currency of the Fees), or any higher amount in a Statement of Work; and (c) where it processes Personal Data or accesses the Customer's systems, cyber liability insurance of not less than INR 5,00,00,000." Rule measurable to match. Amounts [Lawyer].
- **F-M6 (11.1 append):** "The assignment is worldwide and for the full term of the Intellectual Property Rights, including all renewals and extensions. To the extent permitted by law, Sections 19(4) and 19A of the Copyright Act, 1957 do not apply, and it shall not lapse or revert to the Provider for non-exercise of any right within any period."
- **F-M7 (10.4 + rule):** "without undue delay and in any event within twenty-four (24) hours, and within six (6) hours where reportable under the CERT-In Directions". Rule: "A clause requiring our DPA before any processing meets preferred. Don't flag an unsigned DPA where the request says no personal data."
- **F-M8:** 10.8 "The Provider shall maintain an information security programme consistent with ISO/IEC 27001 or equivalent, provide its certificate or SOC 2 report on request, remediate critical vulnerabilities promptly and cooperate with the Customer's CERT-In obligations." 17.2 "The Provider shall maintain, test annually and share on request a business continuity and disaster recovery plan for the Services."
- **F-M9 (16.2):** "...and, on reasonable notice no more than once a year (or after a security incident or at a regulator's request), its security and compliance controls relevant to the Services."
- **F-M10 (8.4):** "...at the rates in the applicable SOW or, if none, the Provider's then-current rates less any SOW discount, and at no charge where the Customer terminates under Clause 8.2 or 5.4."
- **F-M11 (2.3):** "...and except that the description of the Services, Deliverables and Fees in a Statement of Work prevails for that SOW."
- **F-M12:** Rule: "'Fees paid or payable in the 12 months preceding the event' meets preferred; a floor equal to the SOW's total fees is acceptable." Optional 13.1 floor; optional foreign super-cap 3x [Lawyer]; clarify 13.3(b) "(including Clause 12.1(c) (infringement))".
- **F-M14:** preamble company type, CIN, registered office, GSTIN from registers; 18.1 "Notices: Customer: {company_notice_email}; Provider: {counterparty_notice_email}."
- **F-M15 (20.2):** "The Customer may terminate on notice if the Provider undergoes a change of control in favour of a competitor of the Customer or a sanctioned person."
- **F-M17 (3.4, gxp=Yes):** "Where the Services are GxP-regulated, the Parties shall sign a quality agreement before the Services start, and it prevails on quality matters."
- **F-M18:** add 15.2, 16.2 to 8.5.

## A5. MSA form needs
Registers for parties (plus address, CIN, GSTIN, notice email); payment terms 45/60 only + MSME question; show governing law (default India; map "Maharashtra, India" → "India"); new arbitration seat/rules; signer names (theirs from form, ours asked); personal_data and gxp drive cl. 10 and 3.4; `scope` unused — drop or one background sentence.

# B. SOW (7 rules)

## B1. Template vs industry standard
Strong: named deliverables with dates/owners (4.3), closed scope (4.4), written-only acceptance (6.2), signed change orders (7), payment on acceptance (9.1), T&M NTE with 75% alert (8.3), 10% holdback (9.2).

| # | Clause | Problem | Sev |
|---|---|---|---|
| S1 | 8.2+8.3 | Both pricing models always print; with no pricing answer "Pricing model: " and "is , inclusive". | High |
| S2 | 8.3/8.4 | "Rates set out in the Master Agreement" — none; T&M unpriced. | High |
| S3 | Preamble/1.1 | Master Agreement unnamed though `parent_contract_id` asked. | Med |
| S4 | 12.1 | `{governing_law}` not asked → entity or Delaware; can contradict Indian-law MSA. | High |
| S5 | 9.3 | `{payment_days}` always 45; can contradict MSA at 60. | Med |
| S6 | 8.5 | `{value_sentence}` says "this Agreement", tacked onto expenses. | Low |
| S7 | — | No service levels, credits, insurance, transition rates (all deferred by MSA). | Med |
| S8 | 10.2 | Provider representative = signer; blank → "Provider: ." | Low |
| S9 | 4.2 | Raw scope text in ("This is a test"). | Med |
| S10 | 5.1 | Key personnel names only. | Low |
| S11 | — | No pointer to the MSA's DPA. | Low |

## B2. Playbook
- `fees_and_pricing`: assumes an MSA rate card; accept an SOW rate card.
- `milestone_payments`: penalises valid T&M; make conditional on fixed/milestone.
- `acceptance`: prohibited "deemed accepted" matches negating text — keep 6.2 phrasing.
- Fine: scope_and_deliverables (add "not placeholder text"), change_control, key_personnel, order_of_precedence.
- Missing: service_levels (ongoing only), payment_terms (defer to MSA), governing_law (follow MSA).

## B3. Real redlines
| Redline | n | Cause | Fix |
|---|---|---|---|
| fees_and_pricing both models | 2 | S1 template defect | Template |
| scope_and_deliverables "This is a test" | 2 | Test input | Form validation |
| Template check bracket placeholders | 1 | Quote not in current sow.txt — stale saved version or stale check | Re-run check; retire old version |

## B4. Fixes
- **F-S1:** replace 8.1–8.3 with `8.1 {pricing_terms}` from `sow_values`. Fixed/Milestones: "Fixed fee. The total fee for the Services and Deliverables is {fee_amount}, inclusive of all Provider costs and exclusive only of applicable taxes[, payable in the instalments in Clause 9.1, each only after written acceptance of the linked Deliverable]." T&M: "Time and materials. The Provider shall charge for time actually and properly worked at the rates in Clause 8.2, supported by Customer-approved timesheets. Total charges shall not exceed the not-to-exceed amount of {fee_amount}. The Provider shall notify the Customer at 75%, and the Customer is not liable above that amount unless it is increased by a signed Change Order." Missing pricing: hold the draft.
- **F-S2:** 8.2 "Rates. {rate_card} These rates are fixed for the term of this SOW." From T&M rows (role, day rate). Fixed: "No rates apply; changes are priced in the Change Order." Rule preferred: fixed fee, or T&M at SoW/MSA rates with NTE and no increase.
- **F-S3:** "under the {master_agreement_ref} (the "Master Agreement")" from `parent_contract_id` ("Master Services Agreement dated 1 November 2026 between the Parties").
- **F-S4 (12.1):** "This SOW is governed by the law, and disputes under it are resolved in the manner, set out in the Master Agreement." Remove `{governing_law}`.
- **F-S5 (9.3):** "...within the period set out in the Master Agreement..." Remove `{payment_days}`.
- **F-S6:** drop `{value_sentence}`, or `{sow_value_sentence}` "Total charges under this SOW, including expenses, shall not exceed {currency} {value}."
- **F-S7:** 4.7 "Service Levels. {service_levels}", default "No Service Levels apply to this SOW, which is for project Deliverables; any Service Level is added only by signed Change Order."; rows for ongoing services (metric, target, credit %, critical). SOW rule `service_levels`: ongoing services have measurable levels and credits, or the SoW states none apply.
- **F-S8:** no signer → "the individual the Provider notifies in writing".
- **F-S9:** scope validation (≈15 words; refuse "test", "TBD", "lorem") [product call].

## B5. SOW form needs
`{pricing_terms}` (make `pricing` required), new `{rate_card}` (T&M rows), new `{service_levels}` (ongoing? rows), new `{master_agreement_ref}` from `parent_contract_id`, "their project lead"; remove `{payment_days}` and `{governing_law}`; personal_data Yes → "the DPA under MSA 10.2 applies".

## Counsel calls
Arbitration seat/institution; breach window 24h/6h vs 72h; SLA cap 10% vs 15%; insurance amounts; uncapped mutual confidentiality/data vs super-cap; 12.1(a) breadth; post-term non-solicit (s.27); India-only data localisation (10.5) for foreign providers.
