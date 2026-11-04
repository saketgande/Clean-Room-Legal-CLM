# Verification: Templates and playbooks aligned (014-template-playbook-alignment)

Task T018, qa-engineer, 2026-10-28. Nothing here reached the AI gateway outside pytest (conftest forces mocks). No code edited. Nothing committed (whole feature is in the working tree).

## Result

46 criteria (AC-1..AC-45 + AC-1A): **42 PASS, 4 PARTIAL, 0 FAIL, 0 NOT-RUN** (the AI half of AC-1A is NOT-RUN for the current wording, which is why AC-1A is PARTIAL).

PARTIAL: AC-1A (AI half not run on the current wording), AC-7 / AC-8 / AC-42 (browser/live-data half not exercised; code and vitest evidence only).

## Targeted runs (this session, `docker compose exec -T backend python -m pytest -q`, from `/backend`)

| Files | Result |
|---|---|
| test_alignment_nda, _msa, _sow, _vendor | 2968 passed (24 s) |
| test_alignment_saas, _consultancy, _dpa | 24 passed (29 s) |
| test_paper_fill, test_payment_options_014, test_playbook_rules_014, test_drafting_context_014 | 265 passed |
| test_standard_update_014, test_existing_contracts_untouched_014, test_template_status, test_template_check, test_template_check_jobs, test_drafting_templates | 266 passed |
| test_pair_fixes_pf15_21, _pf22_26, _pf27_30, test_playbook_review_output_014, test_nda_recital_purpose, test_nda_check | 98 passed |
| Frontend (`npx vitest run` on templates dir, form-checks.test.ts, _agreement-wizard.test.tsx) | 5 files, 815 passed |

Migration: `docker compose exec -T backend alembic current` / `heads` both `0077_std_paper (head)` (single head).

## Suites (2026-10-28)

- `docker compose exec backend python -m ruff check .`: All checks passed.
- `alembic heads` / `alembic current`: single head `0077_std_paper`, applied (offline `--sql` renders, per the reviewer).
- full `pytest -q` (one-off container `docker compose run --rm --no-deps backend`, since the dev backend was being restarted by other work): 8366 passed, 2 failed, 1 skipped. Failures: `test_ai_gateway_phase2::test_moved_prompts_are_byte_for_byte_what_the_modules_had` (pre-existing, not 014) and `test_exam_isolation::...refused_before_anything_happens[run --mode sample]` (a scratch database created concurrently by other work; `tests/test_exam_isolation.py` alone: 43 passed).
- `npm run typecheck`: only the two known stale `.next/types/validator.ts` docstudio errors (not 014).
- `npm run test`: 83 files, 2138 passed.

## Acceptance criteria

Matrix tests: the `test_alignment_<key>.py` files walk the full answer matrix with no AI (about 10.5k combinations; sizes pinned by `test_matrix_has_the_planned_size_and_reads_its_choices_from_the_form`, test_drafting_context_014.py:235, and per-template size tests). "Matrix" below means that file's every-combination test.

| AC | Evidence | Status |
|---|---|---|
| AC-1 | All seven matrix tests, zero phrase-check findings per combination (nda `test_every_drafted_nda_has_no_playbook_finding`, msa `...passes_the_msa_playbook`, sow `...passes_the_playbook_and_prints_one_model`, vendor `...passes_the_vendor_playbook`, saas `test_every_combination_has_no_finding`, consultancy and dpa `..._is_clean_against_its_playbook`). AI half: see AC-1A | PASS (phrase check) |
| AC-1A | Deterministic half: the matrix tests above, PASS, no AI, in normal `pytest`. AI half: **NOT-RUN on the current wording** (last data predates PF-27..PF-30). Rule and recording code verified: `eval/template_alignment.py` pass rule (lines 16-44, `repeated_clause_types`) pinned by `test_pass_rule_fails_only_a_clause_type_repeated_in_two_runs` (test_pair_fixes_pf27_30.py:80); script refuses under mock and needs `--yes`; not imported by tests. Reports kept: backend/eval/results/template_alignment/ (3 files). Computation under the new rule is below | PARTIAL |
| AC-2 | test_payment_options_014.py (`test_msa_and_consultancy_offer_only_45_and_60`, `..._vendor_and_saas_offer_45_60_90`, `..._other_types_keep_the_full_list`) plus payment periods in every matrix; frontend `optionsFor` (form-checks.test.ts:505-525) and wizard options_when case (_agreement-wizard.test.tsx:1635) | PASS |
| AC-3 | Pairs shipped together: matrix tests assert each template against its own playbook; 0077_std_paper ships the rules with the text; `test_every_shipped_template_has_rules_a_note_and_a_change_list` (test_standard_update_014.py:174) | PASS |
| AC-4 | `test_every_combination_fills_completely_with_no_delaware_and_the_right_currency` (test_drafting_context_014.py:243); `test_signature_blocks_split_and_blank`, `test_addresses_one_line_or_blank_never_invented` (test_paper_fill.py:85,95); SOW `test_no_model_or_no_total_prints_nothing` (test_alignment_sow.py:55) | PASS |
| AC-5 | `test_wording_our_paper_writes_is_not_flagged` / `test_risky_counterparty_wording_is_flagged` (test_playbook_rules_014.py:285,291), `test_every_rule_has_wordings_or_is_listed_as_unchanged` (:296) | PASS |
| AC-6 | `test_customer_agreement_playbook_is_unchanged` (test_playbook_rules_014.py:313); `test_the_customer_agreement_playbook_still_hashes_to_the_reviewed_baseline` (test_standard_update_014.py:167); `test_the_update_does_not_touch_the_customer_agreement_playbook` (:377) | PASS |
| AC-7 | Code: shipped wording change turns the saved check out of date (`test_a_release_that_edits_the_shipped_wording_turns_version_zero_out_of_date`, test_template_status.py:245; `test_a_failed_check_of_old_wording_is_out_of_date_not_couldnt_check` :258). Board log shows Check all re-ran the SOW (sow 0 findings, 2026-10-27/28). Live dev data and the Templates page not read by me (auth call was denied by the permission classifier) | PARTIAL |
| AC-8 | `test_new_wording_since_the_last_check_is_out_of_date_for_the_wording` (test_template_status.py:195), `..._newer_playbook_version...` (:208), `..._both_moving_says_both` (:219); summary `standard.wording_behind/playbook_behind` (test_standard_update_014.py:295). Not seen in a browser | PARTIAL |
| AC-9 | `test_the_standard_update_leaves_existing_contracts_requests_findings_and_changes_alone` and `test_replaying_the_update_still_leaves_existing_work_alone` (test_existing_contracts_untouched_014.py:85,159) | PASS |
| AC-10 | `test_no_template_but_the_nda_ever_names_delaware_or_a_state` (test_drafting_context_014.py:65), `test_nda_law_mapping`, `test_other_templates_are_always_india` (test_paper_fill.py:20,25), per-matrix "no Delaware" (nda :45, saas :98, dpa :86) | PASS |
| AC-11 | `test_draft_context_reads_the_register_rows...` (test_drafting_context_014.py:158), `test_seat_from_register_address` (test_paper_fill.py:38), `test_default_nda_is_under_indian_law_with_courts_at_the_seat` (test_alignment_nda.py:39) | PASS |
| AC-12 | `test_sow_and_dpa_state_no_dispute_clause` (test_paper_fill.py:80), `test_parent_agreement` (:149), `test_the_principal_agreement_is_named...` / `test_term_has_no_contradiction...` (test_alignment_dpa.py:61,68), SOW master-agreement case (test_alignment_sow.py:47) | PASS |
| AC-13 | `test_signature_blocks_split_and_blank` (test_paper_fill.py:85), `test_the_register_rows_reach_the_values_and_a_bare_request_gets_blank_lines` (test_drafting_context_014.py:101), `test_signature_blocks_and_term_in_words` (test_alignment_nda.py:49) | PASS |
| AC-14 | `test_addresses_one_line_or_blank_never_invented` (test_paper_fill.py:95), `test_seat_without_known_city_states_it_without_inventing_one` (:43) | PASS |
| AC-15 | MSA, Vendor, Consultancy matrices cover every offered period with the MSME line and zero findings; 30 refused on MSA/Consultancy/Vendor (test_payment_options_014.py:23,36). Note FR-3 supersedes the AC text: 30 days is no longer offered, as decided in Q16 | PASS |
| AC-16 | `test_gst_has_one_position_only` (test_alignment_consultancy.py:57) | PASS |
| AC-17 | `test_insurance_is_stated_in_the_contract_currency` (test_alignment_msa.py:62), `test_floor_follows_the_currency` (test_alignment_saas.py:80), `test_minimums_by_currency_single_table` (test_paper_fill.py:111), drafting_context currency check (:243) | PASS |
| AC-18 | Matrices (insurance sentences) + insurance flag/pass pair in test_playbook_rules_014.py:285,291; `test_consultancy_public_liability_has_an_amount_the_rule_names` (test_pair_fixes_pf27_30.py:52) | PASS |
| AC-19 | Matrices carry the 24h/6h window; flag tests for slower windows (`test_a_counterparty_72_hour_breach_clause_is_flagged`, test_alignment_dpa.py:95; rules pairs test_playbook_rules_014.py:291) | PASS |
| AC-20 | `test_dpa_status_variants` (test_paper_fill.py:123); Vendor/MSA matrices with personal_data Yes/No have zero data_protection findings; `test_pair_fixes_pf22_26.py` (no-personal-data DPA wording) | PASS |
| AC-21 | `test_placeholder_scope_holds_a_sow_and_a_consultancy_on_our_paper` (test_drafting_context_014.py:213; "TBD", "This is a test", lorem, 4 words held; their paper not held), `test_placeholder_scope` (test_paper_fill.py:209), `test_the_new_holds_use_the_answers_branch_and_resume_on_their_own` (:225) | PASS |
| AC-22 | `test_the_nda_term_is_written_in_words` (test_drafting_context_014.py:80), NDA matrix (2/3/5, mutual and one-way), `term_and_duration` flag pair in test_playbook_rules_014.py | PASS |
| AC-23 | `test_nda_personal_data_variants`, `test_nda_yes_prints_breach_notice_once` (test_paper_fill.py:133,214); test_nda_recital_purpose.py | PASS |
| AC-24 | NDA non-compete/indemnity flag pair (test_playbook_rules_014.py:285-291) | PASS |
| AC-25 | MSA matrix `test_every_combination_has_the_fixed_protections` (:49), `service_levels` rule pair; SOW matrix 4.7 | PASS |
| AC-26 | MSA `limitation_of_liability` pass wording (test_playbook_rules_014.py:285), `test_stated_contract_value_says_it_does_not_limit_liability` (test_pair_fixes_pf27_30.py:63) | PASS |
| AC-27 | `test_every_combination_passes_the_playbook_and_prints_one_model` (test_alignment_sow.py:35), `test_a_sow_without_pricing_or_value_is_held...` (test_drafting_context_014.py:192), `test_held_drafts_name_their_reason` (test_alignment_sow.py:60) | PASS |
| AC-28 | `test_rates_follow_the_master_agreement_and_fixed_fee_has_none` (test_alignment_sow.py:47); `milestone_payments` zero findings in SOW matrix | PASS |
| AC-29 | SOW matrix (parent named, master payment period, service levels, rep fallback), `test_parent_agreement` (test_paper_fill.py:149), context test (test_drafting_context_014.py:158) | PASS |
| AC-30 | `test_each_buying_variant_has_its_wording` (test_alignment_vendor.py:59), `test_vendor_supply_variants` (test_paper_fill.py:160), Vendor matrix | PASS |
| AC-31 | `test_data_breach_is_under_one_cap_and_never_also_uncapped` (test_alignment_saas.py:68) + SaaS matrix | PASS |
| AC-32 | `test_hosting_clause_follows_the_answer` (test_alignment_saas.py:45), `test_hosting_variants` (test_paper_fill.py:178), `data_location` flag pair | PASS |
| AC-33 | `test_one_renewal_notice_of_at_least_sixty_days` (test_alignment_saas.py:54), `test_renewal_notice_floor_and_words` (test_paper_fill.py:184), `test_saas_renewal_notice_is_one_period_of_at_least_sixty_days` (test_drafting_context_014.py:93) | PASS |
| AC-34 | `test_continuity_suspension_subprocessor_exit_billing_and_tax_are_in_the_paper` (test_alignment_saas.py:86) | PASS |
| AC-35 | `test_rule_counts` (test_playbook_rules_014.py:306), `test_vendor_playbook_has_its_twelve_rules` (test_alignment_vendor.py:35) | PASS |
| AC-36 | `test_every_combination_has_the_frozen_clauses` (test_alignment_consultancy.py:44), `test_a_day_is_eight_hours_only_on_a_day_rate` (:65), `test_consultancy_invoicing_variants` (test_paper_fill.py:194) | PASS |
| AC-37 | Consultancy non-compete flag pair and advance-payment pass pair (test_playbook_rules_014.py:285,291); `test_a_counterparty_delaware_clause_is_flagged` (test_alignment_consultancy.py:80) | PASS |
| AC-38 | `test_dpdp_data_needs_no_scc_while_eu_uk_data_does` (test_alignment_dpa.py:76), principal agreement and term tests (:61,68), `test_every_combination_has_the_frozen_clauses` (:45) | PASS |
| AC-39 | DPA "Data Principal"/"erasure" pass pair and `test_a_counterparty_72_hour_breach_clause_is_flagged` (test_alignment_dpa.py:95) | PASS |
| AC-40 | Pass/flag pair for every changed rule (test_playbook_rules_014.py:285,291) plus `test_phrases_one_per_line...` / `test_required_lines...` / `test_prohibited_lines...` (:325-354); AI risky counterparty papers flagged 7/7 (847a) and 4/4 (f1fc) in the sample reports (older prompt/rules but still flags) | PASS |
| AC-41 | `test_the_update_publishes_matching_rules_marks_the_wording_and_is_safe_to_replay` (test_standard_update_014.py:331), `test_adopting_publishes_the_standard_rules_audits_and_starts_a_check` (:209), `test_reverting_archives_the_standard_versions...` (:388) | PASS |
| AC-42 | Backend: `test_the_update_leaves_edited_rules_and_own_wording_alone_and_says_so` (:353), `test_the_summary_says_when_wording_and_playbook_are_older_than_the_standard` (:295). Frontend: page.test.tsx:984-1016 ("Older than the standard" badge, "Use the standard rules" button, hidden without permission, adopt call). No live browser check | PARTIAL |
| AC-43 | `test_a_template_drafting_could_not_fill_is_refused` in test_drafting_templates.py (green in the 266-test run); `test_the_catalogue_lists_every_paper_field...` (test_drafting_context_014.py:133) | PASS |
| AC-44 | `test_adopting_the_standard_needs_the_permission_to_publish_and_writes_nothing` (test_standard_update_014.py:197), `test_another_organisations_playbook_is_never_adopted_into` (:275), `test_adopting_refuses_unknown_templates...` (:253); existing cross-org tests in test_drafting_templates.py | PASS |
| AC-45 | Audit assertions in `test_adopting_publishes_the_standard_rules_audits_and_starts_a_check` (:209) and the "no audit row on viewing" assertions in the same file (:197) and test_template_status.py:428; `test_a_check_says_who_asked_and_whether_the_system_acted_for_them` (:527) | PASS |

## AC-1A, AI half: what the latest data would show under the new rule

Rule (spec, 2026-10-28): a combination fails if the same clause type appears in 2 or more of its 3 runs (also an errored run, or a risky paper not flagged). Computed from the JSON reports in `backend/eval/results/template_alignment/` (clause_type per finding, distinct per run).

Latest data per template: nda, msa, sow from batch 847a2aea (2026-10-27 file, post PF-15..21); vendor, saas, consultancy, dpa from batch f1fc2f46 (2026-10-28, post PF-22..26). 19 main combinations, **14 pass, 5 fail**. Risky papers flagged 7/7 (847a) and 4/4 (f1fc), every expected clause type found; 0 errors in f1fc (847a's one errored consultancy run is superseded by f1fc).

| Combination | Repeated clause type (runs) | Addressed since? |
|---|---|---|
| msa, 45 days, fixed end date, no personal data, GxP No | limitation_of_liability (3 of 3) | PF-30 (contract value vs cap), unverified; the unrecorded partial third run also showed this at 5 of 6 runs |
| vendor, 60 days, Services, renews | indemnification (2) | PF-28 (defence control), unverified |
| saas, 45 days, India hosting, renews | governing_law (2) | PF-27 (seat wording), unverified |
| saas, 45 days, Several regions, no end date | data_location (2) | No PF-27..30 fix targets it; likely still failing |
| consultancy, 45 days, fixed fee, advice | insurance (2) | PF-29 (public liability amount), unverified |

Clean under the new rule: all 3 nda, 1 of 2 msa, all 3 sow, 2 of 3 vendor, 2 of 4 saas, 1 of 2 consultancy, both dpa. For scale, 18 of 57 main runs in the combined latest set (per plan.md) had a finding (35 in the first sample, 21 in the second); under the new rule 5 of 19 combinations still fail.

Verdict: the AI half of AC-1A is NOT-RUN for the current templates and rules (PF-27..PF-30 and the pass rule are unproven by AI). A fresh deliberate run (about 64 reviews, a few dollars, needs the user's go-ahead) is required to close it. The SaaS "Several regions" data_location repeat has no fix queued.

## Security and audit posture

- Adopt endpoint `POST /drafting-templates/{key}/playbook/standard` requires `playbook:publish`: 403 without it, 404 for another org's key, 409 custom type (test_standard_update_014.py:197,253,275). No new permission added.
- Mutations audited (who, when, why); viewing writes nothing (test_standard_update_014.py:197, test_template_status.py:428); system audit rows for the migration/standard update.
- Org scoping: playbook and template lookups filter by org (test at :275).
- Live API spot checks (curl with a login) were not made: the permission classifier denied the attempt, so no live 401/403 evidence beyond pytest.

## Defects and observations

No FAIL. Items for the user and orchestrator:

1. AI evidence is stale for PF-27..PF-30 (see above); one repeat (SaaS "Several regions" data_location) has no fix queued.
2. test_ai_gateway_phase2::test_moved_prompts_are_byte_for_byte_what_the_modules_had fails: pre-existing, not this feature (not rerun here; the orchestrator's full run decides).
3. Migration `0077_std_paper` is chained after the untracked `0077_obligation_edit_roles` (not this feature); both share the 0077 prefix. Single head confirmed. `0077_obligation_edit_roles` must be committed with it or the chain breaks.
4. Nothing is committed; the 014 work sits in the working tree beside specs 009-013 and unrelated obligation work.
5. Browser verification not performed (Templates page badge/notice/button, payment options in the live wizard); vitest only. Dev-org check results are for the wording before PF-27..PF-30, so the page would show "out of date" until Check all is run (expected, a paid action I did not trigger).
6. Spec AC-15 text still lists a 30-day period; FR-3/Q16 removed it from MSA, Vendor, Consultancy (and SaaS). Evidence follows the later decision.

## Open user decisions recorded in plan.md

- PF-15: foreign-currency floor figures (USD 600,000/120,000, EUR 550,000/110,000, GBP 475,000/95,000) are round figures from the user's decision 3; Finance may want to confirm.
- PF-18: Consultancy with no contract value is held (not drafted with a blank fee); the user may prefer a blank.
- PF-23: the DPA is always signed with the agreement, applying from the Effective Date, even when personal data is No.
- PF-24: no professional indemnity cover for a goods-only vendor (re-performing under warranty does not count as services).
- PF-27: seat wording "the courts at {seat} alone have supervisory jurisdiction over the arbitration".
- PF-28: settlement wording in Vendor 10.2 and SaaS 14.2 (provider controls defence; no settlement admitting fault or binding us without written consent).
- PF-29: Consultancy public liability tier (INR 1 crore, USD 120,000, EUR 110,000, GBP 95,000 per claim).
- Also open from earlier: the 2026-10-28 pass-rule change (same clause type in 2 or more of 3 runs) was set by the user; the fresh AI run needs their go-ahead and cost approval.


## Code review (code-reviewer, 2026-10-28): APPROVE WITH NITS

No blocker; adopt route is `playbook:publish`-gated, org-scoped and audited; migration leaves existing contracts untouched; the stray-quote repair cannot corrupt valid output.

1. MEDIUM `paper.is_foreign`: an Indian city given alone ("Mumbai", "Bangalore, KA") is read as foreign, so the draft gets a Singapore seat and a false "outside India" recital.
2. MEDIUM `paper._seat`: takes the first listed city in the address ("Delhi Public School Road, Pune" → New Delhi).
3. MEDIUM `standard.row_hash`: ignores approval_required / escalation_role / negotiation_guidance / sample_clause, so an org that edited only those on a baseline playbook is overwritten by the standard update and `playbook_behind` misses it.
4. LOW-MEDIUM `_phrases` now splits multi-line rule text for org-written rules too (a wrapped paragraph matches line by line).
5. LOW `0077_std_paper` uses current ORM models and library text (fragile for old databases with an org later).
6. LOW risky runs pass on any finding; expected vs found clause types are not compared.
7. LOW the pass rule never fails with `--runs 1`; a mistyped RISKY_SENTENCES key falls back silently.
8. NIT rule wording: the Singapore-seat sentence can be met by the counterparty's own recital; the MSA "contract value never reduces the cap" sentence should except a liability clause that itself caps at that value.
9. NIT renewal notice typed in months; KeyError if default currency is not INR/USD/EUR/GBP.

## Verdict

Not DONE yet: AC-1A's AI half is NOT-RUN on the current wording (PF-27..PF-30 and the new pass rule are unverified by the AI; the latest data gives 14 of 19 combinations passing, and SaaS "Several regions" data_location has no fix queued); AC-7, AC-8 and AC-42 are PARTIAL (no browser check); reviewer medium findings 1–3 are open.

## Follow-up after /verify (2026-10-28)

Reviewer findings 1–3 fixed as PF-31, PF-32 and PF-33; the SaaS "Several regions" data_location repeat fixed as PF-34 (see plan.md). Full suite 8402 passed / 1 failed (known gateway test); ruff clean. Still open: the paid AI half of AC-1A on the current wording, AC-7/8/42 browser checks, and the low/nit review findings 4–9.
