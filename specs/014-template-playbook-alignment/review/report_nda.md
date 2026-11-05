# NDA review — `nda.txt` vs "Corporate NDA Negotiation Playbook v1.0"

*Drafting review produced 27 Oct 2026 by an AI reviewer for the in-house team; not legal advice. **[Lawyer]** = judgement call for counsel.*

Inputs: `nda.txt`, the 8 rules in the playbook, the 2 NDA findings on our own paper (both `term_and_duration_of_confidentiality`, medium, 16 and 27 Oct), the latest template check (NDA: 0 findings with the default fill), `drafting.py::template_values`.

The form asks `nda_kind`, `nda_direction`, `nda_term` (2/3/5 years, required) and `governing_law` (5 options, required). It has no `purpose` (removed 27 Oct) and no `survival_years` question, so every NDA gets "explore a potential business relationship" and 5 years' survival. A blank `governing_law` (non-form requests) gives "the State of Delaware".

## 1. Template vs industry standard (14)

The template is strongly discloser-favourable, made mutual; most clauses are the playbook's `required_language` pasted in. In a mutual NDA, and a one-way NDA where "They share", every strict term binds **us**.

| # | Clause | Issue | Sev |
|---|---|---|---|
| T1 | 9.1 + fallback | Blank `governing_law` gives Delaware law and Delaware courts for an Indian entity. | High |
| T2 | 9.1 | One placeholder fills law and courts; "courts of India" names no seat (Indian practice names a city). | High |
| T3 | 9 | No arbitration option; Indian decrees can't be enforced directly in the US (not a reciprocating territory). MCIA/SIAC with an interim-relief carve-out is common for foreign parties. [Lawyer] | Med |
| T4 | 3.1, 3.2 | Exclusions need "clear and convincing written evidence" (US standard), stated twice. Market: "as shown by its written records". | Med |
| T5 | 4.2 | "Higher of reasonable care … no less than a high degree of care" — circular, above market. Market: "at least reasonable care". | Med |
| T6 | 1.2, 4.3, 4.4 | Representatives exclude affiliates and contractors; service providers need prior written consent (breached day one by cloud/email vendors). 4.3 second sentence repeats 4.4. | High |
| T7 | 4.6 | Breach notice within 48 hours; market "promptly", 72 hours usual stretch. | Low |
| T8 | 6.1–6.3 | 6.1/6.2 duplicate; backups must be permanently deleted (unrealistic); officer certification. Market = the playbook's own fallback (backups retained, overwritten, still confidential). | Med |
| T9 | 7.3 | "Indefinitely or … trade secret, whichever is longer" illogical; India has no trade-secret statute. [Lawyer] | Med |
| T10 | 7.4 | Sections 2–5 survive with no end, contradicting 7.2's 5-year limit. | High |
| T11 | `term_clause` | Filled "continues for the term of 2 years" vs default "three (3) years" — inconsistent style. | Low |
| T12 | 8.1, 8.2, 8.4 | 8.1/8.2 duplicate; "without bond" may not bind an Indian court (Specific Relief Act); prevailing-party fees is a US habit. [Lawyer] | Low |
| T13 | 11.1 | Personal data refers only to "applicable laws"; should name the DPDP Act 2023; `personal_data` answer ignored. | Med |
| T14 | Missing | (a) freedom to develop independently; (b) assignment to affiliate/successor; (c) stamp duty [Lawyer]; (d) IT Act 2000 e-signature; (e) notice addresses. | Med |

Acceptable: broad definition (2.1–2.3), compelled disclosure (5), no licence/warranty (10.1–10.2), no obligation to proceed (10.3), no residuals (3.3), publicity (11.3), entire agreement, counterparts, no non-solicit.

## 2. Playbook vs industry standard (10)

| # | Rule | Problem |
|---|---|---|
| P1 | `term_and_duration_of_confidentiality` | Mixes up agreement term and survival. A 2-year term + 5 years' survival is market and is flagged. Unclear whether 5-year term is a deviation. |
| P2 | `exclusions_from_confidential_information` | Prohibits standard wording ("becomes generally known through no fault of the RP", independent development by personnel without access); requires "clear and convincing". |
| P3 | `permitted_use_and_standard_of_care` | Required language needs consent for every service provider, contradicting its own preferred position; "high degree of care" above market. |
| P4 | `return_or_destruction_of_information` | Preferred demands backup deletion (conceded in fallback); "officer" vs "authorized representative" inconsistent. |
| P5 | `governing_law_and_jurisdiction` | No seat/city for India, no cross-border arbitration route; jury waiver meaningless outside the US. |
| P6 | `remedies_and_injunctive_relief` | Bond fallback "$[10,000]" in USD and bracketed; silent on prevailing-party fees. |
| P7 | `compelled_disclosure_and_legal_process` | US-centric ("grand jury"); no carve-out for routine SEBI/RBI/stock-exchange inspections. |
| P8 | `definition_of_confidential_information` | Fine; AI/ML language non-negotiable — [Lawyer] confirm for every mutual NDA. |
| P9 | Missing | Permitted disclosees/affiliates, assignment, residuals, non-solicit, non-compete/exclusivity (prohibit), indemnity (prohibit), DPDP personal data, publicity, stamp duty. |
| P10 | All | Written only from the discloser's side; never checks what we accept as receiver. |

## 3. Conflicts

Both real redlines: requests answered `nda_term` = "2 years" → "continues for the term of 2 years" = the rule's fallback → flagged; the suggested fix rewrites the requester's choice to 3 years. **The rule is wrong (P1)**; the form offers 2 years as a normal option.

Latent: `nda_term` = 5 years may be flagged (P1 ambiguous); blank law → Delaware passes the rule (Delaware is in its list) — bad text not caught; fixing T4/T5/T6/T8 in the template alone creates new findings because the rule's required language demands the strict wording. **Template and rule must change together.**

## 4. Fixes

| Issue | Side | Wording / rule |
|---|---|---|
| P1 + redlines | RULE (+form) | Preferred: term 2–5 years; confidentiality survives ≥3 years (5 preferred) after expiry; trade secrets for as long as they remain trade secrets. Fallback: 1-year term + 3 years' survival. Prohibited: no survival; any end date on trade secrets; survival under 2 years. Keep 2/3/5 options. |
| T11 | TEMPLATE (code) | Map `nda_term` to words: "continues for two (2) years". |
| T1 | CODE | Fallback `governing_law` → "India" (or the entity's country); never Delaware. |
| T2, P5 | BOTH | 9.1: "…laws of {governing_law}… exclusive jurisdiction of the courts at {court_seat}." New question `court_seat`, default entity's registered city. Rule: courts named by city/seat. |
| T3, P5 | BOTH (optional) | 9.4: "Any dispute shall be finally resolved by arbitration under the [MCIA/SIAC] Rules, seated at {court_seat}, in English, by a sole arbitrator; nothing prevents either Party seeking interim or injunctive relief from any competent court." [Lawyer] |
| T4, P2 | BOTH | 3.2: "The Receiving Party bears the burden of showing, by its written records, that an exclusion applies. A combination of elements is not excluded merely because individual elements are." Delete "clear and convincing" from 3.1. Rule: "clear and convincing" fallback-only; drop prohibitions on market wording. |
| T5, P3 | BOTH | 4.2: "…using at least the degree of care it uses for its own confidential information of similar sensitivity, and no less than reasonable care." Rule: that is preferred. |
| T6, P3 | BOTH | 1.2 Representatives = "directors, officers, employees, Affiliates, professional advisers, and contractors and service providers, each with a need to know for the Purpose and bound by written (or professional) confidentiality obligations no less protective than this Agreement." Delete consent sentences in 4.3/4.4. Rule required language: "…bound by written confidentiality obligations; RP liable for their breach." Keep the AI/ML ban. |
| T7 | TEMPLATE | "promptly, and in any event within seventy-two (72) hours". |
| T8, P4 | BOTH | Merge 6.1/6.2: "Within ten (10) business days … return or destroy … and certify in writing, signed by an authorized representative. The RP may retain copies (i) required by law or regulation, or (ii) in automated backup systems not accessible in the ordinary course, overwritten in the normal cycle; retained copies remain subject to this Agreement." Rule: that is preferred. |
| T9 | TEMPLATE | 7.3: "…information that is a trade secret shall remain protected for so long as it remains a trade secret or otherwise protectable as confidential under applicable law." |
| T10 | TEMPLATE | 7.4: "Subject to Sections 7.2 and 7.3, Sections 2–6 and 8–11 survive…" |
| T12, P6 | BOTH | Merge 8.1/8.2, add "to the extent permitted by applicable law" before "without bond"; delete 8.4 or "costs as the court may award". Rule fallback: nominal security as the court may require (INR). [Lawyer] |
| T13 | TEMPLATE | 11.1: "…in compliance with applicable data protection laws, including the Digital Personal Data Protection Act, 2023, with reasonable security safeguards…"; if `personal_data` = Yes add "notify the other Party of any personal data breach without undue delay." Add a DPDP rule. |
| P7 | RULE + TEMPLATE | Fallback + 5.2: "No notice needed for routine regulatory examinations or stock-exchange requirements not targeted at the DP, provided confidential treatment is requested." |
| T14 | TEMPLATE | (a) 10.4 "Nothing restricts either Party from independently developing products or services without use of Confidential Information." (b) 11.5 "…except to an Affiliate or a successor to all or substantially all of its business, on written notice." (c) 11.11 "Each Party bears its own costs; stamp duty, if any, shall be borne by [{stamp_duty_party}]." (d) 11.10 "…including electronic signatures under the Information Technology Act, 2000." (e) notice addresses from the form/registers. |
| P9 | RULE | Add rules prohibiting non-compete/exclusivity, indemnities and residuals; non-solicit optional; assignment rule. |
| Duplicates | TEMPLATE | Keep one copy each of 3.1/3.2, 4.3/4.4, 5.1/5.2, 6.1/6.2, 8.1/8.2. |

With every BOTH row applied as a pair, the filled default and each form option should give zero findings. Re-run the template check after each pair.

## 5. What the template needs from the form

| Placeholder / blank | Source today | Question |
|---|---|---|
| `{company}`, `{counterparty}` | entity, counterparty | registers |
| `{effective_date}` | start_date | Start date |
| `{nda_kind_title}`, `{nda_kind_word}` | nda_kind | Mutual or one-way |
| `{direction_recital}` | nda_direction | Who shares |
| `{purpose_phrase}` | none → fallback | Add "What is the information being shared for?" (user removed purpose 27 Oct — decision) |
| `{term_clause}` | nda_term | 2/3/5 years |
| `{survival_clause}` | none → 5 years | Optional 3 or 5 years, default 5 |
| `{governing_law}` | governing_law | keep |
| new `{court_seat}` | — | city, default entity's |
| dispute route | — | optional courts or arbitration |
| personal-data variant | personal_data (unused) | drives DPDP sentence |
| signature name/title both sides | cp_signer_name only | our signer + titles |
| notice addresses | — | registers |
| new `{stamp_duty_party}` | — | optional [Lawyer] |

Unused by the text (routing only): needed_by, department, paper, note_approvers; cp_signer_email for signing.
