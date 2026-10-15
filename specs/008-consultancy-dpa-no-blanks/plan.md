# Implementation Plan: Consultancy and DPA Drafts With No Blank Brackets

Feature ID: 008-consultancy-dpa-no-blanks
Spec: ./spec.md (must be APPROVED)
Created: 2026-10-15
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->

## Architecture overview

This feature is 007's pattern applied to two more templates. **It needs no migration, no new endpoint,
no new permission, no new setting, no new Celery task and no new dependency.** The new answers live in
the request's existing `field_values` JSON, as the SOW rows do. It changes the same modules 007 changed
and adds two pure modules beside `app/intake/sow.py`:

- the form definitions `app/intake/agreement_forms.json` / `.py` (questions, `show` rules, the `rows`
  kind and its rules);
- the filing validator `_validate_field_values(..., stored=)` in `app/intake/service.py`, shared by
  create, update and the AI assistant;
- the drafting bridge `app/intake/drafting.py` (`template_values`, `render_document`, `DraftHeld`,
  `draft_hold`, `draft_contract_for_request`);
- the 006 Templates domain `app/drafting_templates/` (PLACEHOLDERS, checks);
- the workflow engine's `clm_draft` branch in `app/workflows/service.py`.

```
 wizard (_agreement-forms.tsx) ── RowsTable / Question ── rules mirror (lib/form-checks.ts)
        │ POST /intake/requests                              ▲ same rules, same messages
        ▼                                                    │ (tests/fixtures/consultancy_dpa_cases.json)
 intake/service._validate_field_values ──▶ agreement_forms.clean_rows (+ open_end, outside, must_be)
        │  + rules_when, max_length, max_decimals, older_may_lack, legacy_single, `[]` = no answer
        │  stored in IntakeRequest.field_values (new keys below); PATCH audits intake.answers_changed
        ▼
 intake/drafting.draft_contract_for_request
        ├─▶ template_skip(request)  ── Processor DPA ── 422 PROCESSOR_NOTE (person) / step skipped (workflow)
        ├─▶ draft_hold(...)         ── answers lacking ── 409 DraftHeld(message) (generic, driven by `fills`)
        └─▶ template_values() ──▶ sow.sow_values + consultancy.consultancy_values + dpa.dpa_values
                                   consultancy.txt / dpa.txt: standard wording + 10 new fields
 workflows/service clm_draft: template_skip → skipped + audit; DraftHeld → waiting_human "held"; re-armed by
                              _resolve_waiting_group (007), refused by complete_human_step (007)
 drafting_templates: PLACEHOLDERS += 10 (samples = example readings); checks read example=True (007)
 eval/exam/build.py: consultancy and dpa rendered from frozen 2026-10-15 copies (as sow is)
```

What is reused from 007, and how it is generalised:

| 007 piece | 008 use | Generalisation (007 behaviour unchanged) |
|---|---|---|
| `rows` question kind, `clean_rows`, M1–M12 | Consultancy deliverables, key individuals (one column), countries (one column), sub-processors | three optional column attributes: `open_end` (date), `outside` and `must_be` (text). Absent on the SOW table, so its 146 cases are untouched |
| `no_blanks`, `min_exclusive` (F1, F2) | scope, value, purpose, contact | a question-level `rules_when` (the question's own rules apply only when these rules match) so a shared question gets a rule for Consultancy only (A-4); plus `max_length` (F3) and `max_decimals` (F4) |
| `shown()` rules | the deliverables table hides when "advice only" is ticked | a rule may carry `"not": true` (matches when the answer is none of `in`) |
| stored-aware validation (`stored=`) | older requests (FR-41, FR-42) | `older_may_lack` (a new question may stay unanswered on an update while nothing it depends on changed) and `legacy_single` (a multi-tick question keeps an older single choice sent back unchanged) |
| shared cases file | the new rules | a **sibling** file `tests/fixtures/consultancy_dpa_cases.json`; each case names its form and question, so one file covers four tables and the single-question rules. 007's file is not edited |
| `sow.py` (fills, example, markers, hold strings) | — | `consultancy.py` and `dpa.py` mirror its shape exactly; `sow._total` becomes public `total_value` (one rename, one caller) |
| `template_values(..., example=)` | — | spreads the two new value dicts after `sow_values` |
| `DraftHeld`, `draft_hold` | the hold | `DraftHeld(detail=sow.DRAFT_HELD)` takes a message; `draft_hold` keeps its SOW branch verbatim and adds one generic branch for Consultancy and DPA driven by each question's `fills` |
| workflow held step (`_HELD_FOR_ROWS`, `_is_held`, re-arm, manual refusal) | held Consultancy/DPA steps | `result.held` may also be `"answers"` or `"form"`; `_is_held` tests "has a `held` value"; the SOW note, result and refusal strings stay exactly as frozen |
| `checks._CODED`, the Apply guard | — | adds `consultancy.FIELDS`, `dpa.FIELDS` and both modules' `EXAMPLE_MARKERS` |
| `_template_body` exam freeze | consultancy, dpa | maps three keys to three frozen copies |
| `RequestDeliverables` | — | unchanged; a new `RequestAnswers` block renders it for SOW and its own block for Consultancy and DPA |
| 006 P2's planned `clm_draft` skip | the Processor skip | built now as `drafting.template_skip(request)`, called where P2's rule goes, so P2 adds its rule there |

Discrepancies flagged:
- The constitution mentions `src/lib/demo.ts`; there is no demo mode in this checkout.
- `can()` in `lib/intake.ts` returns true for anyone, so the server's `intake:update` check is the real
  gate for entering answers on the request page (as 007 noted).
- Today the server accepts `[]` for a required `multiselect` while the wizard refuses it. FR-24 needs "at
  least one", so the validator now treats `[]` as no answer for every question kind it checks in its main
  loop (multiselect is the only kind that can be a list there). The Amendment form's `what_changes` gets the
  same refusal the wizard already gives.
- The DPA form also asks "Whose paper?", so A-3 applies to it: the new DPA follow-ups and the hold are for
  our own paper only.
- The starter DPA workflow says its draft step is "skipped on their paper", but the step has no paper
  condition: it skips only because the wizard's ingest of their draft has made the contract first. See
  Open question 1.
- FR-12 says a Consultancy request on "Their paper" accepts what it accepts today; FR-17 and FR-20 say the
  Consultancy form refuses a zero value and a bracketed scope. The plan applies those two rules to
  Consultancy **on our own paper** (`rules_when`), which meets FR-12 literally and covers every draft the
  rules protect. See Open question 4.

## Path mapping (ownership boundaries for THIS feature — exact files)

| Agent | Task | Files in this feature |
|---|---|---|
| db-engineer | — | **none** (no model, no migration; head stays `0074_template_sync`) |
| backend-dev | T001 | `backend/app/intake/agreement_forms.json` (new_agreement: 7 new questions, `rules_when` + rule on `scope` and `value`; dpa: `data_types`/`data_subjects` become multi-tick, 2 new tables, `fills` on 5 existing questions, `no_blanks` on `processing_purpose`), `backend/tests/fixtures/consultancy_dpa_cases.json` (new) |
| backend-dev | T002 | `backend/app/intake/agreement_forms.py` (`form_fields`/`_column` pass the new attributes; `shown` reads `not`; `clean_rows` M9b/M13/M14; `tidy` made public; `answer_text` joins a list of strings), `backend/app/intake/service.py` (`_validate_field_values`, `_refuse_unsound_answer`, `_check_table`, `create_request` audit facts, `update_request` → `_record_answer_changes`). Tests: `backend/tests/test_consultancy_dpa_answers.py` (new, no DB), `backend/tests/test_consultancy_dpa_request_updates.py` (new, DB) |
| backend-dev | T003 | `backend/app/intake/consultancy.py` (new), `backend/app/intake/dpa.py` (new), `backend/app/intake/sow.py` (rename `_total` → `total_value`, nothing else), `backend/app/drafting_templates/defaults/consultancy.txt`, `backend/app/drafting_templates/defaults/dpa.txt`, `backend/app/intake/drafting.py` (`template_values` spreads the two new dicts; imports only), `backend/app/drafting_templates/service.py` (10 PLACEHOLDERS entries; `services` and `fee_amount` "about"), `backend/app/drafting_templates/checks.py` (`_CODED`, Apply guard markers), `backend/eval/exam/build.py` (`_template_body`), `backend/eval/exam/consultancy_2026-10-15.txt` (new, byte copy of HEAD `defaults/consultancy.txt`), `backend/eval/exam/dpa_2026-10-15.txt` (new, byte copy of HEAD `defaults/dpa.txt`). Tests: `backend/tests/test_consultancy_dpa_drafting.py` (new), `backend/tests/test_sow_drafting.py` (two tests change, see Test strategy) |
| backend-dev | T005 | `backend/app/intake/drafting.py` (`DraftHeld(detail)`, `TemplateSkip`/`template_skip`, generic `draft_hold`, `held_step`, the two constants, the FR-49 record keys), `backend/app/workflows/service.py` (`clm_draft` skip branch, held catch via `held_step`, `_is_held`, `complete_human_step` message). Tests: `backend/tests/test_consultancy_dpa_hold.py` (new, DB), `backend/tests/test_workflow_engine_consistency.py` (extend) |
| frontend-dev | T004 | `frontend/src/lib/types.ts` (form type additions), `frontend/src/lib/form-checks.ts` (`shown` `not`, `dateWindow` open end, `rowsProblems` M9b/M13/M14, `answerProblem` F3/F4, `rulesFor`, `lacksAnswer`, `needsDraftAnswers`, `PROCESSOR_ROLE`), `frontend/src/lib/form-checks.test.ts`, `frontend/src/app/(app)/intake/_deliverables.tsx` (`RowsTable`: `item`, `ref_prefix`, one-column list, open-end hint), `frontend/src/app/(app)/intake/_deliverables.test.tsx` (extend), `frontend/src/app/(app)/intake/_agreement-forms.tsx` (steps, `UI` entries and corrected help, `DRAFT_OF`, `export function Question` + `earlier` prop, multi-tick legacy handling, `rulesFor`, `fee_rate` on the review step), `frontend/src/app/(app)/intake/agreement-forms.test.ts`, `frontend/src/app/(app)/intake/_agreement-wizard.test.tsx` |
| frontend-dev | T006 | `frontend/src/app/(app)/intake/_request-answers.tsx` (new: `RequestAnswers`), `frontend/src/app/(app)/intake/_request-answers.test.tsx` (new), `frontend/src/app/(app)/intake/_request-overview.tsx` (mount `RequestAnswers` instead of `RequestDeliverables`; `held` = any `result.held`), `frontend/src/app/(app)/intake/_request-overview.test.tsx` (extend) |
| qa-engineer | T007 | `specs/008-consultancy-dpa-no-blanks/verification.md` |

`backend/eval/exam/**` is outside every agent's default ownership. It is assigned to backend-dev for
T003 only, because T003's template change would otherwise change the exam's committed content (D-3). The
owner gave this yes for 007 and is asked again at plan approval.

Shared-hotspot note:
- `backend/app/intake/drafting.py`: **T003 then T005** (T003 touches only `template_values` and imports;
  T005 the hold, skip and record). They never run together.
- `backend/app/intake/agreement_forms.json`: T001 only. T002, T004 and T005 read it and start after T001.
- `backend/app/intake/agreement_forms.py`, `backend/app/intake/service.py`: T002 only.
- `backend/app/workflows/service.py`, `backend/tests/test_workflow_engine_consistency.py`: T005 only.
- `backend/tests/test_sow_drafting.py`: T003 only.
- `frontend/src/lib/types.ts`: T004 only. `frontend/src/lib/endpoints.ts`: **no task** (`intakeApi.update`,
  `intakeApi.draftContract`, `workflowsApi.refreshRun` already take what is needed).
  `frontend/src/components/ui.tsx`: no task. `backend/app/models.py`: no task. `backend/app/jobs/tasks.py`:
  no task.
- `frontend/src/app/(app)/intake/_agreement-forms.tsx` and `_deliverables.tsx`: T004 only. T006 imports
  `Question` and `RowsTable`/`RequestDeliverables`/`EarlierAnswer`/`useRequestForms` from them.

Wave shape:
- **Wave 1:** T001 and T003 in parallel (no shared files; T003's fill code reads answers by key, not
  through the JSON).
- **Wave 2:** T002 (after T001) and T004 (after T001) in parallel.
- **Wave 3:** T005 (after T002 and T003) and T006 (after T004) in parallel.
- **Wave 4:** T007 (after all).

No migration task, so the Alembic rule is moot.

## Interface freeze (contract-first — fixed before implementation starts)

All paths are under `/api/v1` (client paths omit it). **No endpoint is added or removed and no
permission string changes (FR-51).** Every touched route keeps its gate and its org scope:
`get_request` filters `org_id == user.org_id` and lets only staff (`intake:read`) or the requester read a
request; the new answers are keys of `field_values`, so they have exactly the request's visibility
(FR-52). There is no new query. The drafted contract keeps `accessible_contract_filter` unchanged.
Names typed into answers are plain strings: nothing looks them up, notifies, grants or links (FR-18,
FR-19, FR-26).

### API contract

| Method | Path | Permission (unchanged) | Request change | Response change | New/changed errors |
|---|---|---|---|---|---|
| GET | /api/v1/intake/forms | `intake:create` | — | new_agreement: 7 new questions, attributes on `scope`/`value`; dpa: 2 questions become `multiselect`, 2 new tables, `fills` and `no_blanks` | — |
| POST | /api/v1/intake/requests | `intake:create` + screen `intake` ADD | new `field_values` keys (below) | as stored (cleaned) | 422: M1–M14 (M9b, M13, M14 new), F1–F4 (F3, F4 new), `Field "{label}" is required` for an empty multi-tick |
| PATCH | /api/v1/intake/requests/{id} | `intake:update` + screen `intake` EDIT | same | same | 422 as above; never for an older request's untouched answers (FR-42) |
| POST | /api/v1/intake/requests/{id}/draft-contract | `intake:read` + screen `intake` EDIT | — | — | **409** held (Consultancy/DPA messages); **422** `PROCESSOR_NOTE` |
| POST | /api/v1/workflows/runs/{id}/complete-step | `intake:read` | — | — | **409** `ANSWERS_STEP_NOT_MANUAL` on a held `answers`/`form` step (the SOW message stays for a SOW step) |
| POST | /api/v1/workflows/runs/{id}/refresh | `intake:read` | — | a held step whose answers now exist goes on and drafts (007 resolver) | — |
| GET | /api/v1/workflows/runs/by-request/{id} | `intake:read` | — | held step and skipped step shapes below | — |
| GET | /api/v1/drafting-templates/{key} | `playbook:read` | — | `placeholders` +10; consultancy/dpa shipped `body`/`default_body` are the new wording | — |
| POST | /api/v1/drafting-templates/{key}/preview | `playbook:read` | — | the 10 fields fill with their samples (= example readings) | — |
| POST | /api/v1/drafting-templates/{key}/checks (and the job) | `playbook:run` | — | the check reads the example readings; a fix quoting new example content has `suggestion_is_wording: false` | — |
| PUT / reset / restore | /api/v1/drafting-templates/{key}… | `playbook:update` | — | the new names pass `check_body` | — |

#### The new and changed form questions (`GET /intake/forms`, frozen)

`C` = `{"field": "agreement_type", "in": ["Consultancy"]}`, `OT` = `{"field": "paper", "in": ["Our template"]}`.
`TICK` = `"There are no separate deliverables (advice only)"`.

```json
// form "new_agreement" — changed questions (other attributes as today)
{"key": "scope", "...": "...", "no_blanks": true, "rules_when": [C, OT]}
{"key": "value", "...": "...", "min_exclusive": 0, "rules_when": [C, OT]}

// form "new_agreement" — new questions, inserted directly after "scope", in this order
{"key": "advice_only", "label": "Advice only", "kind": "multiselect", "required": false,
 "options": [TICK], "show": [C, OT], "fills": ["consultancy_deliverables"]},
{"key": "consultancy_deliverables", "label": "Deliverables", "kind": "rows", "required": true,
 "min_rows": 1, "max_rows": 20,
 "show": [C, OT, {"field": "advice_only", "in": [TICK], "not": true}],
 "older_may_lack": true, "fills": ["consultancy_deliverables"],
 "columns": [
   {"key": "name", "label": "Deliverable name", "kind": "text", "max_length": 200, "unique": true, "no_blanks": true},
   {"key": "due", "label": "Due date", "kind": "date", "within": ["start_date", "end_date"], "open_end": true}]}

// form "new_agreement" — new questions, inserted directly after "payment_terms", in this order
{"key": "fee_basis", "label": "How is the consultant paid?", "kind": "select", "required": true,
 "options": ["Fixed fee", "Day rate", "Hourly rate"], "show": [C, OT],
 "older_may_lack": true, "fills": ["consultancy_fee_basis"]},
{"key": "fee_rate", "label": "Rate per day or hour", "kind": "number", "required": true,
 "min_exclusive": 0, "max_decimals": 2,
 "show": [C, OT, {"field": "fee_basis", "in": ["Day rate", "Hourly rate"]}],
 "older_may_lack": true, "fills": ["consultancy_fee_basis"]},
{"key": "key_individuals", "label": "Consultant's key individuals", "kind": "rows", "required": true,
 "min_rows": 1, "max_rows": 10, "item": ["key individual", "key individuals"], "show": [C, OT],
 "older_may_lack": true, "fills": ["key_individuals"],
 "columns": [{"key": "name", "label": "Name", "kind": "text", "max_length": 120, "unique": true, "no_blanks": true}]},
{"key": "company_contact_name", "label": "Company contact: name", "kind": "text", "required": true,
 "max_length": 120, "no_blanks": true, "show": [C, OT], "older_may_lack": true, "fills": ["company_contact"]},
{"key": "company_contact_role", "label": "Company contact: role", "kind": "text", "required": true,
 "max_length": 120, "no_blanks": true, "show": [C, OT], "older_may_lack": true, "fills": ["company_contact"]}

// form "dpa" — changed questions (keys, labels, order unchanged)
{"key": "processing_purpose", "...": "...", "no_blanks": true, "rules_when": [OT], "fills": ["processing_purpose"]},
{"key": "data_types", "label": "What kind of personal data?", "kind": "multiselect", "required": true,
 "options": ["Contact and identity", "Employee / HR", "Health or other special category", "Financial"],
 "legacy_single": true, "fills": ["personal_data_types", "special_category_data"]},
{"key": "data_subjects", "label": "Whose data?", "kind": "multiselect", "required": true,
 "options": ["Employees", "Customers", "Patients or trial subjects", "Suppliers"],
 "legacy_single": true, "fills": ["data_subject_categories"]},
{"key": "transfer", "...": "...", "fills": ["processing_locations"]},
{"key": "subprocessors", "...": "...", "fills": ["authorised_subprocessors"]}

// form "dpa" — new questions: "transfer_countries" directly after "transfer",
// "subprocessor_rows" directly after "subprocessors"
{"key": "transfer_countries", "label": "Countries outside India", "kind": "rows", "required": true,
 "min_rows": 1, "max_rows": 20, "item": ["country", "countries"],
 "show": [{"field": "transfer", "in": ["Yes"]}, OT], "older_may_lack": true, "fills": ["processing_locations"],
 "columns": [{"key": "country", "label": "Country", "kind": "text", "max_length": 100, "unique": true,
              "no_blanks": true, "outside": "India"}]},
{"key": "subprocessor_rows", "label": "Sub-processors", "kind": "rows", "required": true,
 "min_rows": 1, "max_rows": 20, "item": ["sub-processor", "sub-processors"], "ref_prefix": "",
 "show": [{"field": "subprocessors", "in": ["Yes"]}, OT], "older_may_lack": true, "fills": ["authorised_subprocessors"],
 "columns": [
   {"key": "name", "label": "Sub-processor name", "kind": "text", "max_length": 200, "unique": true, "no_blanks": true},
   {"key": "country", "label": "Country", "kind": "text", "max_length": 100, "no_blanks": true,
    "must_be": {"value": "India", "when": {"field": "transfer", "in": ["No"]}}},
   {"key": "processing", "label": "What it does", "kind": "text", "max_length": 300, "no_blanks": true}]}
```

New attributes, defined once (server and wizard read them identically):

| Attribute | On | Meaning |
|---|---|---|
| `not` | a `show` rule | the rule matches when the answer is **none** of `in` (an unanswered question matches) |
| `rules_when` | a question | its `no_blanks`, `min_exclusive`, `max_length`, `max_decimals` apply only when every rule matches the form's answers |
| `max_length` | a text/textarea question | the answer is tidied (as a cell: runs of ASCII whitespace → one space, ends trimmed; stored tidied), then refused above this many code points (F3) |
| `max_decimals` | a number question | the answer as typed (commas removed, ASCII-trimmed) must match `^[0-9]+(\.[0-9]{1,N})?$` (F4). Checked after F2 |
| `older_may_lack` | a question | on an update, a missing answer is allowed (left absent) when the stored request has no value under this key **and** every field named in its `show` rules has the same answer as stored |
| `legacy_single` | a multiselect | on an update, a **string** answer equal to the stored one is kept verbatim, uncoerced and unchecked (an older single choice, including "Several") |
| `fills` | a question | the template fields built from this answer: the hold reads it (which answers a wording needs), the request page shows these questions, the audit tracks them. Not a validation rule |
| `item` | a `rows` question | `[singular, plural]` noun for "Add a …" and the cap text; default `["deliverable", "deliverables"]` (the SOW) |
| `ref_prefix` | a `rows` question | prefix of the read-only reference column; default `"D"` (the SOW); `""` shows plain numbers |
| `open_end` | a date column with `within` | when the start answer is a day and the end answer is not (no end date), only the start bound applies (M9b) |
| `outside` | a text column | a cell equal to this value (compared lower-case after tidying) is refused (M13) |
| `must_be` | a text column | when the `when` rule matches the form's answers, a cell not equal to `value` (compared lower-case after tidying) is refused (M14) |

#### Rules and exact messages

The `rows` rules are 007's (steps 1–9 of 007's plan), with these additions inside step 6, per cell, in
this order after the existing checks of that kind:

- `text`: after M6 and M7: `outside` (M13), then `must_be` (M14).
- `date`: after M8: `within` with both ends a day (M9, unchanged), else `open_end` with only the start a
  day: `cell < start` gives M9b.

| Id | Message (`{L}` question label, `{C}` column label, `{n}` row number) |
|---|---|
| M1–M12 | unchanged from 007 |
| M9b | `"{L}", row {n}: "{C}" must be on or after {start} ({start label}).` e.g. `"Deliverables", row 1: "Due date" must be on or after 1 November 2026 (Start date).` |
| M13 | `"{L}", row {n}: "{C}" is {outside}. List only the countries outside {outside}.` e.g. `"Countries outside India", row 2: "Country" is India. List only the countries outside India.` |
| M14 | `"{L}", row {n}: "{C}" is "{cell}", but the answer to "{when label}" is {answer}. Give {value}, or change that answer.` e.g. `"Sub-processors", row 1: "Country" is "Singapore", but the answer to "Will data leave India?" is No. Give India, or change that answer.` |
| F1, F2 | unchanged from 007 |
| F3 | `Field "{label}" is longer than {max_length} characters` (no full stop, as the existing 20,000-character message) |
| F4 | `Field "{label}" can have at most {max_decimals} decimal places` |
| required | `Field "{label}" is required` (existing), now also for a required multiselect sent as `[]` |

The wizard words F1–F4 from the verb (`Is longer than 120 characters.`, `Can have at most 2 decimal
places.`), as 007's `answerProblem` does; the cases file compares them with the label and full stop
stripped. For tables the wizard message is the server message, character for character.

Filing and update semantics in `_validate_field_values(fields, values, *, stored=None)` (007's order kept):

1. Main loop, per question: not shown → popped (existing). `rows` → post-pass (existing).
2. `missing` = `None`, a blank string, **or `[]`**. Missing and required → `Field "{label}" is required`,
   unless `_may_stay_unanswered(f, cleaned, stored)` (the `older_may_lack` rule), in which case it is
   popped.
3. `legacy_single` and `stored is not None` and the answer is a `str` equal to `stored[key]` → kept as is;
   skip 4–5.
4. `_coerce_field` (existing); then, when the question has `max_length` and the value is a string,
   `cleaned[key] = tidy(value)`.
5. When `stored is None` or the value differs from `stored[key]` (existing): `_refuse_unsound_answer(f,
   value, raw, cleaned)`: return at once when `rules_when` does not match; then F3, F1, F2, F4 in that
   order.
6. `_check_dates_in_order` (existing), then the rows post-pass. In `_check_table` the existing "older
   request without rows" exemption becomes `older_without_rows or _may_stay_unanswered(f, cleaned,
   stored)`. A `[]` table is still M3 (007 behaviour for a sent empty table is kept).

What these give, by the spec's scenarios:
- "advice only" ticked: the deliverables table is not shown, so it is neither required nor kept (FR-15).
- `fee_basis` changed to "Fixed fee": `fee_rate` is not shown, so it is removed (FR-16); changed to a rate
  basis: `fee_basis` differs from stored, so `older_may_lack` does not apply and the rate is required.
- `transfer`/`subprocessors` changed to No: the follow-up is removed; changed to Yes: required (FR-29).
- An older request's unrelated update (no `field_values`, or the stored answers sent back): passes; the new
  questions stay absent; "Several" is kept verbatim (FR-41, FR-42).
- An older request whose new answers are being set: each answer set must meet its rules (FR-42).

#### Full request/response JSON shapes

```json
// POST /intake/requests — the AC example Consultancy request (wizard shape; other answers as today)
{
  "type_label": "New agreement · Consultancy", "priority": "Medium", "department": "Quality",
  "description": "Quality-management process advice", "subject": "Consultancy — Acme Advisory LLP",
  "field_values": {
    "request_form": "new_agreement", "agreement_type": "Consultancy", "paper": "Our template",
    "entity": "Aegis Pharma Ltd", "entity_id": "le-1", "counterparty": "Acme Advisory LLP", "counterparty_id": "cp-1",
    "cp_signer_name": "Maya Chen", "cp_signer_email": "maya.chen@example.com",
    "purpose": "Quality-management process advice", "department": "Quality",
    "value": "1800000", "currency": "INR", "start_date": "2026-11-01", "term": "Fixed end date", "end_date": "2027-04-30",
    "scope": "Advise on the design of the new quality-management process.",
    "consultancy_deliverables": [
      {"name": "Current-state review", "due": "2026-12-15"},
      {"name": "", "due": ""},
      {"name": "Process design report", "due": "2027-03-15"}
    ],
    "payment_terms": "30 days", "fee_basis": "Fixed fee", "fee_rate": "25000",
    "key_individuals": [{"name": "Anita Rao"}, {"name": "Dev Menon"}],
    "company_contact_name": " Ravi  Shah ", "company_contact_role": "Head of Quality",
    "personal_data": "No", "gxp": "No"
  }
}
// 201 RequestResponse — field_values as stored: the empty row gone, the hidden rate gone (Fixed fee),
// the contact tidied, no "advice_only" key (not ticked)
{"...": "...", "field_values": {"...": "...", "value": 1800000,
  "consultancy_deliverables": [{"name": "Current-state review", "due": "2026-12-15"},
                               {"name": "Process design report", "due": "2027-03-15"}],
  "fee_basis": "Fixed fee", "key_individuals": [{"name": "Anita Rao"}, {"name": "Dev Menon"}],
  "company_contact_name": "Ravi Shah", "company_contact_role": "Head of Quality"}}

// "advice only" instead of rows
{"advice_only": ["There are no separate deliverables (advice only)"]}   // and no consultancy_deliverables key

// POST /intake/requests — the AC example DPA request (field_values only)
{"request_form": "dpa", "parent_contract_id": "c-1", "cp_signer_name": "Maya Chen",
 "cp_signer_email": "maya.chen@example.com", "paper": "Our template",
 "our_role": "Controller: we decide how the data is used",
 "processing_purpose": "Hosting and support of the clinical-trial site portal.",
 "data_types": ["Contact and identity", "Health or other special category"],
 "data_subjects": ["Customers", "Patients or trial subjects"],
 "transfer": "Yes", "transfer_countries": [{"country": "Singapore"}, {"country": "Germany"}],
 "subprocessors": "Yes",
 "subprocessor_rows": [
   {"name": "CloudHost Pte Ltd", "country": "Singapore", "processing": "Hosting of the portal database"},
   {"name": "SupportDesk GmbH", "country": "Germany", "processing": "Second-line support"}]}
// 201: stored as sent (cells tidied). With "transfer": "No" and "subprocessors": "No" the two tables
// are not stored even if sent.

// 422 (FastAPI shape), e.g.
{"detail": "\"Deliverables\", row 2: \"Due date\" must be between 1 November 2026 and 1 February 2027 (Start date to End date)."}
{"detail": "\"Sub-processors\", row 1: \"Country\" is \"Singapore\", but the answer to \"Will data leave India?\" is No. Give India, or change that answer."}
{"detail": "Field \"Rate per day or hour\" can have at most 2 decimal places"}

// PATCH /intake/requests/{id} — entering the answers on an older Consultancy request: the page sends
// every stored answer back with the new ones added (field_values is replaced whole, as today)
{"field_values": {"...every stored answer...": "...",
  "consultancy_deliverables": [{"name": "Current-state review", "due": "2026-12-15"}],
  "fee_basis": "Day rate", "fee_rate": "25000", "key_individuals": [{"name": "Anita Rao"}],
  "company_contact_name": "Ravi Shah", "company_contact_role": "Head of Quality"}}
// 200 RequestResponse with them stored; an older DPA's "data_subjects": "Several" sent back unchanged stays "Several"

// POST /intake/requests/{id}/draft-contract — held: no contract, no pin, no file, no audit row
// 409
{"detail": "Enter these answers on the request first: \"Deliverables\", \"How is the consultant paid?\", \"Consultant's key individuals\", \"Company contact: name\" and \"Company contact: role\". The Engagement Schedule of the Consultancy draft is made from them, and this request does not have them yet."}
// 409 (email/chat DPA)
{"detail": "<dpa.FREE_TEXT_HELD>"}
// 422 (Processor DPA request): nothing created, no audit, no history
{"detail": "No draft was made: this request says we are the Processor, and our DPA template is written for the Controller. Attach the other side's DPA (or the paper to use) before the review."}

// GET /workflows/runs/by-request/{id} — a held Consultancy/DPA draft step (existing fields)
{"idx": 0, "type": "clm_draft", "status": "waiting_human",
 "note": "<the held message> This step carries on by itself once the answers are saved on the request.",
 "result": {"held": "answers"}, "...": "..."}
// a held email/chat DPA draft step
{"idx": 0, "type": "clm_draft", "status": "waiting_human", "note": "<dpa.FREE_TEXT_HELD>", "result": {"held": "form"}}
// a Processor draft step (skipped, the run goes on to its next step)
{"idx": 0, "type": "clm_draft", "status": "skipped", "note": "<dpa.PROCESSOR_NOTE>", "result": {"skipped": "processor_role"}}
// a held SOW step: exactly as 007 froze it ({"held": "deliverable_rows"}, sow.HELD_STEP_NOTE)

// POST /workflows/runs/{id}/complete-step on a held answers/form step
// 409
{"detail": "This step makes the draft from the request's answers, so it can't be marked done by hand. Its note says what the draft is waiting for."}
```

#### Frozen strings

In `app/intake/consultancy.py`:
- `CONSULTANCY_HELD` = `Enter these answers on the request first: {answers}. The Engagement Schedule of
  the Consultancy draft is made from them, and this request does not have them yet.`
- `ADVICE_ONLY` = `No separate deliverables are specified. The Consultant's output is the reports and
  advice it gives under Clause 2.2, all of which are Work Product.`

In `app/intake/dpa.py`:
- `DPA_HELD` = `Enter these answers on the request first: {answers}. Annex 1 and Annex 3 of the DPA draft
  are made from them, and this request does not have them yet.`
- `FREE_TEXT_HELD` = `File this request on the Data Processing Agreement form first. It came in by email
  or chat, so it has none of the form's answers, and Annex 1 and Annex 3 of the DPA draft are made from
  them: whose data it is, what kinds of data, where it is processed and which sub-processors are used.`
- `PROCESSOR_ROLE` = `Processor: we act on their instructions` (equal to the JSON option; a test pins it)
- `PROCESSOR_NOTE` = `No draft was made: this request says we are the Processor, and our DPA template is
  written for the Controller. Attach the other side's DPA (or the paper to use) before the review.`
  (D-11; it says nothing about who prepares the paper)
- `PROCESSOR_TITLE` = `No draft made: the request says we are the Processor`
- `SPECIAL_YES` = `Special category Personal Data is processed: health data and other special category
  data. The Processor shall process it only for the purpose stated in this Annex 1 and shall apply the
  measures in Annex 2 to it.`
- `SPECIAL_NO` = `None. No special category Personal Data is processed under this Agreement.`

In `app/intake/drafting.py`:
- `ANSWERS_STEP_SUFFIX` = ` This step carries on by itself once the answers are saved on the request.`
- `ANSWERS_STEP_NOT_MANUAL` = `This step makes the draft from the request's answers, so it can't be
  marked done by hand. Its note says what the draft is waiting for.`

`{answers}` = each needed question's label in double quotes, in form order, joined `", "` with `" and "`
before the last (`"A"`, `"A" and "B"`, `"A", "B" and "C"`).

#### Audit actions written

All in the request's own transaction, before its commit, never on a second session (CLAUDE.md
audit-lock).

| Where | Action | Change |
|---|---|---|
| `create_request` | `intake.created` (existing) | For a `new_agreement` or `dpa` filing, `after` also holds every key of that form's `fills` questions present in the stored answers, exactly as stored (FR-50). The SOW facts are unchanged. An MSA filing adds nothing (it has none of the keys) |
| `update_request` | **`intake.answers_changed`** (new) + timeline `intake.answers_changed` on the request | For `new_agreement`/`dpa` requests, when any `fills` key differs between `stored` and the cleaned answers. `before`/`after` = `{key: value or null}` for **every** `fills` key of the form. Timeline title `Answers for the draft entered` when every changed key was absent before, else `Answers for the draft changed`; `details` = `{"before", "after", "changed": [keys]}`. Covers answers removed or required by another answer (FR-16, FR-29) and an older request's first entry. The SOW keeps `intake.deliverables_changed` only (its questions carry no `fills`) |
| `draft_contract_for_request` | `intake.contract_drafted` (existing) + contract timeline `contract.drafted_from_template` + `metadata_json.drafted_from_template` | The 006 record gains, for consultancy: `deliverable_rows: int` (0 when advice only), `fee_basis: str or null`, `key_individuals: int`; for dpa: `countries_outside_india: int`, `subprocessors: int`, `special_category_data: bool` (FR-49). `_sow_facts` becomes `_fill_facts` and copies these keys too |
| workflow `clm_draft` skip | **`workflow.draft_skipped`** (new) on `resource_type="intake_request"`, `resource_id` = the request, `actor_user_id=None` (the system) + timeline **`flow.draft_skipped`** on the request, actor None | `after` = `{"flow_run_id", "step_idx", "reason": "processor_role", "note": PROCESSOR_NOTE}`; timeline title `PROCESSOR_TITLE`, `details` = same (FR-55) |
| held draft, refused filing, a person's refused Processor draft | none | They change nothing (spec, Audit trail; FR-53) |

### Database schema

**No table, column or index changes. No migration.** `alembic heads` stays `0074_template_sync`.

New persisted shapes, inside the existing JSON column `intake_request.field_values` (org scoped by the
row's `org_id`, as today) and, unvalidated, in `intake_draft.values`:

| Key in `field_values` | Type | When present |
|---|---|---|
| `advice_only` | `["There are no separate deliverables (advice only)"]` | Consultancy, our paper, when ticked |
| `consultancy_deliverables` | array (1–20) of `{"name": str ≤200, "due": "YYYY-MM-DD"}` | Consultancy, our paper, not advice only. Absent on older requests until entered |
| `fee_basis` | `"Fixed fee"` \| `"Day rate"` \| `"Hourly rate"` | Consultancy, our paper |
| `fee_rate` | number (int or float) > 0, at most 2 decimals | with a rate basis only |
| `key_individuals` | array (1–10) of `{"name": str ≤120}` | Consultancy, our paper |
| `company_contact_name`, `company_contact_role` | str ≤120, tidied | Consultancy, our paper |
| `data_types`, `data_subjects` | array of option strings (≥1); **or** a string on older requests, kept as filed (including "Several of these" / "Several") | every DPA form request |
| `transfer_countries` | array (1–20) of `{"country": str ≤100}` | DPA, our paper, `transfer == "Yes"` |
| `subprocessor_rows` | array (1–20) of `{"name": str ≤200, "country": str ≤100, "processing": str ≤300}` | DPA, our paper, `subprocessors == "Yes"` |

### Frontend TypeScript models (added to src/lib/types.ts)

```ts
// Mirrors agreement_forms.json exactly.
export interface RequestFormRule {
  field: string; in: string[];
  /** The rule matches when the answer is none of `in` (an unanswered question matches). */
  not?: boolean;
}
export interface RequestFormColumn {
  key: string;
  label: string;
  kind: "text" | "date" | "percent";
  max_length?: number;
  unique?: boolean;
  no_blanks?: boolean;
  within?: [string, string];
  /** Date columns with `within`: only the start bound applies while the end answer is not a day. */
  open_end?: boolean;
  total?: number;
  show?: RequestFormRule[];
  /** Text columns: a cell equal to this (ignoring case and extra spaces) is refused. */
  outside?: string;
  /** Text columns: while `when` matches the form's answers, the cell must equal `value` (ignoring case and extra spaces). */
  must_be?: { value: string; when: RequestFormRule };
}
export interface RequestFormField {
  key: string; label: string;
  kind: "text" | "email" | "textarea" | "select" | "multiselect" | "date" | "number" | "rows";
  required: boolean; options?: string[]; show?: RequestFormRule[];
  not_before?: string;
  no_blanks?: boolean;
  min_exclusive?: number;
  /** Text answers: at most this many characters after tidying. */
  max_length?: number;
  /** Number answers: at most this many decimal places. */
  max_decimals?: number;
  /** This question's own rules apply only when every rule matches. */
  rules_when?: RequestFormRule[];
  /** The template fields built from this answer (the request page shows these questions). */
  fills?: string[];
  /** Server-side update rules (older requests); the wizard ignores them. */
  older_may_lack?: boolean;
  legacy_single?: boolean;
  columns?: RequestFormColumn[];
  min_rows?: number;
  max_rows?: number;
  /** `rows` questions: [singular, plural] for "Add a …" and the cap; default ["deliverable", "deliverables"]. */
  item?: [string, string];
  /** `rows` questions: read-only reference prefix; default "D"; "" shows plain numbers. */
  ref_prefix?: string;
  legacy_key?: string;
  legacy_label?: string;
}
// RequestFormDef, FormRow, FormValue, IntakeDraft, IntakeRequest, WorkflowRunStep: unchanged.
// A held draft step is `!!(result as { held?: string } | null)?.held` ("deliverable_rows", "answers" or "form").
```

`lib/form-checks.ts` exports (frozen, since T006 and the tests use them):

```ts
export function shown(rules: RequestFormRule[] | undefined, values: Record<string, unknown>): boolean;  // honours `not`
export function dateWindow(col: RequestFormColumn, values: Record<string, unknown>):
  { start: string; end: string | null; startKey: string; endKey: string } | null;  // end null only with open_end
export function rowsProblems(...): RowsProblem[];   // unchanged signature; + M9b, M13, M14 in the server's order
export function answerProblem(kind: string | undefined, value: FormValue | undefined,
  notBefore?: { label: string; value: FormValue | undefined },
  rules?: { no_blanks?: boolean; min_exclusive?: number; max_length?: number; max_decimals?: number }): string | null;
export function rulesFor(field: RequestFormField, values: Record<string, unknown>):
  { no_blanks?: boolean; min_exclusive?: number; max_length?: number; max_decimals?: number };  // {} when rules_when does not match
export function lacksAnswer(field: RequestFormField, value: unknown): boolean;  // missing, "", [], or a multiselect string not among the options
export const PROCESSOR_ROLE = "Processor: we act on their instructions";
/** The questions the draft still needs: shown, required, with `fills`, lacking an answer. [] for their paper
 *  ("paper" === "Their paper") and for a Processor request. The server's hold also checks the wording in effect. */
export function needsDraftAnswers(form: RequestFormDef, fv: Record<string, unknown>): RequestFormField[];
```

`_agreement-forms.tsx` exports `Question` (its props unchanged, plus `earlier?: string`, the earlier
single choice of a multi-tick question, shown beside it).

### Endpoint client additions (src/lib/endpoints.ts)

None. `intakeApi.update(id, { field_values })`, `intakeApi.draftContract(id)` and
`workflowsApi.refreshRun(runId)` already exist and take what this feature sends.

### The new fill-in fields (`PLACEHOLDERS` in `drafting_templates/service.py`)

Appended after `provider_representative`, in this order. Each `sample` is the module's
`EXAMPLE_VALUES[name]`, computed by the code that fills a real request, so the preview, the check and the
field list show one example (FR-33, FR-35). Labels are unique against the 22 existing ones.

| name | label | about |
|---|---|---|
| `consultancy_deliverables` | Consultancy deliverables | Consultancy: one entry per deliverable (D1, D2, …) with its name and due date, or that there are none when the requester ticked advice only |
| `consultancy_fee_basis` | Fee basis | Consultancy: the fee basis the requester chose: the fixed fee, or the day or hourly rate |
| `key_individuals` | Key individuals | Consultancy: each of the Consultant's key individuals, named once |
| `company_contact` | Company contact | Consultancy: our contact's name and role |
| `processing_purpose` | Processing purpose | DPA: why the data is processed, as the requester wrote it |
| `data_subject_categories` | Data subject categories | DPA: each category of people the requester ticked |
| `personal_data_types` | Personal data types | DPA: each kind of personal data the requester ticked, described |
| `special_category_data` | Special category data | DPA: that special category data is processed with the Annex 2 measures, or None |
| `processing_locations` | Processing locations | DPA: India, then each country outside India the requester listed |
| `authorised_subprocessors` | Authorised sub-processors | DPA: one entry per sub-processor with its name, country and processing, or None |

Existing fields reused (FR-35: no second field for the same thing):
- `services` about → `Statement of Work and Consultancy: the scope of services as the requester wrote it`.
- `fee_amount` about → `Statement of Work and Consultancy: the total value with its currency (the fixed fee,
  or the amount the fees may not exceed)`.
- Samples unchanged (`sow.EXAMPLE_VALUES`). `value_sentence`, `payment_days`, `agreement_term` unchanged.

### What each field fills (`consultancy.py`, `dpa.py`, frozen formats)

`consultancy_values(fv, *, example=False) -> dict[str, str]` and `dpa_values(fv, *, example=False)` return
all of their module's `FIELDS`. `example=True` returns `EXAMPLE_VALUES`, ignoring `fv`. Otherwise every
value comes from `fv` and a missing answer gives `""` (a request of another kind, or a held one, fills
with nothing; a real draft never gets example content, FR-34). Helpers: `sow.day`, `sow.money`,
`sow.total_value` (renamed from `_total`); `cell` = 007's tidy; `sentence(t)` adds a full stop unless `t`
ends with `.`, `!` or `?`; `and_join(items)` = `a` / `a and b` / `a, b and c`. List entries are joined
`"\n\n"` (006 Phase 3 can split them into Word paragraphs). Money is exact `Decimal`, half-up to cents.

| Field | Value |
|---|---|
| `consultancy_deliverables` | `advice_only` holds the tick: `ADVICE_ONLY`. Else per row of `consultancy_deliverables`, in stored order: `D{i}. {sentence(name)} Due date: {day(due)}.` Else `""` |
| `consultancy_fee_basis` | "Fixed fee" with a total: `The Fees are a fixed fee of {money(total)} for the Services.` "Day rate" with a rate: `The Fees are charged at a day rate of {money(rate)} per day for time properly spent on the Services.` "Hourly rate": `The Fees are charged at an hourly rate of {money(rate)} per hour for time properly spent on the Services.` Else `""`. `rate` = `Decimal(str(fee_rate))` to cents; currency = `fv.currency` or the default |
| `key_individuals` | `sentence(and_join(names))`, names in stored order (`Anita Rao and Dev Menon.`) |
| `company_contact` | `sentence(f"{name}, {role}")`; `sentence(name)` without a role; `""` without a name |
| `processing_purpose` | `str(processing_purpose).replace("\r\n", "\n").strip()`, line breaks kept |
| `data_subject_categories` | picks = the list (or `[the string]` when it is one of today's options; "Several" gives none), each once, in stored order: `"; ".join(picks) + "."` (`Customers; Patients or trial subjects.`); `""` with no picks |
| `personal_data_types` | each pick mapped: Contact and identity → `names, contact details and identification data`; Employee / HR → `employment and HR records`; Health or other special category → `health data and other special category data`; Financial → `financial and transaction data`; `"; ".join(...) + "."`; `""` with no picks |
| `special_category_data` | "Health or other special category" among the picks: `SPECIAL_YES`; other picks only: `SPECIAL_NO`; no picks: `""` |
| `processing_locations` | `transfer == "No"`: `India.`; `"Yes"` with countries: `sentence(and_join(["India", *countries]))` (`India, Singapore and Germany.`); else `""` |
| `authorised_subprocessors` | `subprocessors == "No"`: `None.`; `"Yes"` with rows: per row `{name} ({country}): {sentence(processing)}` joined `"\n\n"`; else `""` |

`record_facts(fv) -> dict` in each module gives the FR-49 keys (see Audit actions).

**The example readings (FR-33).** They share no name, deliverable, amount, country or sub-processor with any
acceptance-criteria request, so finding one in a real draft means the example leaked (AC-38). They read
consistently with the existing samples the same template uses (`services` and `fee_amount` are the SOW
example's portal-redesign scope and INR 1,200,000.00; the check's term is from 1 October 2026 until
terminated, the preview's until 30 September 2027).

- `consultancy.EXAMPLE_ANSWERS`: Consultancy, our paper, start 2026-10-01, INR 1200000, "Day rate" at 20000;
  deliverables "Portal discovery report" due 2026-12-15 and "Launch readiness assessment" due 2027-06-30;
  key individuals Lena Fischer and Omar Haddad; contact Kavita Joshi, Head of Digital Products.
  `EXAMPLE_MARKERS` = the two deliverable names, the three people, `Head of Digital Products`, `20,000.00`.
- `dpa.EXAMPLE_ANSWERS`: our paper, Controller; purpose "Running the occupational-health clinic booking
  system for staff and study volunteers."; people Employees and Patients or trial subjects; kinds Employee /
  HR and Health or other special category; transfer Yes with Ireland; sub-processor "Northwind Hosting Ltd",
  Ireland, "Hosting of the clinic booking database". `EXAMPLE_MARKERS` = the purpose sentence,
  `Northwind Hosting Ltd`, `Hosting of the clinic booking database`, `Ireland`.

### The new wording, place by place (Fill map)

Everything else in both files stays byte-identical. The new text contains no `[` or `]`, keeps
`{company}`/`{counterparty}`, passes `check_body`, keeps every playbook `required_language` and contains
neither `payable in advance` nor `unlimited liability` (`test_our_own_template_passes_the_literal_check`).
The sentences are the analyst's and planner's proposal for counsel's review (A-9); they become the shipped
wording.

`consultancy.txt` lines 191–199 become:

```
Services: {services}

Deliverables and timetable:

{consultancy_deliverables}

Fees: {consultancy_fee_basis} The Fees shall not exceed {fee_amount} except by a written amendment signed by both Parties. The Consultant shall invoice the Fees, and the Company shall pay them, in accordance with Clauses 6.2 to 6.4.

Consultant's key individual(s): {key_individuals} The Consultant shall not replace any key individual named here without the Company's prior written consent.

Company contact: {company_contact} The Company contact is the Consultant's point of contact at the Company for the Services and does not direct or supervise how, when or where the Consultant performs them.
```

| # | Place | Takes | Legal effect |
|---|---|---|---|
| 1 | Services | `{services}` | The Consultant's performance obligation is the requester's scope, verbatim (FR-3) |
| 2 | Deliverables and timetable | `{consultancy_deliverables}` | Each item of Work Product named and dated; or none specified and the output is the Clause 2.2 reports and advice, which Clause 8 vests in us |
| 3 | Fees | `{consultancy_fee_basis}` + standard cap + standard invoicing sentence | One basis, never alternatives; never more than the total value without a signed written amendment (for a rate, the ceiling; for a fixed fee, the fee itself); invoicing and payment by 6.2–6.4, so payment stays in arrears within `{payment_days}` days of a valid, undisputed invoice; nothing provides for advance payment (6.5's 20% cap is unchanged) (FR-4, FR-5) |
| 4 | Key individuals | `{key_individuals}` + standard consent sentence | No replacement without our prior written consent (FR-3 row 4, FR-5) |
| 5 | Company contact | `{company_contact}` + standard sentence | A liaison, not a supervisor: keeps the independent-contractor position of Clause 3 (FR-5) |

`dpa.txt` Annex 1 lines 168–176 become:

```
Nature and purpose of the Processing: The Processor shall process Personal Data only on the Controller's documented instructions and only for the purpose stated below. The Processing consists only of the operations described in Section 1.3 that are needed to provide the Services for that purpose, and nothing else.

{processing_purpose}

Categories of Data Subjects: {data_subject_categories} The Processor shall not process Personal Data of any other category of Data Subject.

Types of Personal Data: {personal_data_types} The Processor shall not process any other type of Personal Data.

Sensitive or special category Personal Data (if any): {special_category_data}

Processing locations: {processing_locations} Personal Data is processed and accessed only in the countries listed in this entry. Processing of, or access to, Personal Data in any country outside India is permitted only on the conditions of Section 9.
```

`dpa.txt` Annex 3 line 204 becomes:

```
The Controller authorises the following sub-processors at the Effective Date (name, location, and Processing performed):

{authorised_subprocessors}

No Sub-processor other than any listed above is authorised at the Effective Date. The Processor may engage any other Sub-processor only with the Controller's prior specific written authorisation under Section 6.1. A Sub-processor located outside India may process Personal Data only on the conditions of Section 9.
```

| # | Place | Takes | Legal effect |
|---|---|---|---|
| 1 | Nature and purpose | standard instruction and purpose limitation + `{processing_purpose}` | Processing only on documented instructions and only for the stated purpose, limited to the Section 1.3 operations needed (FR-8 row 1, FR-9) |
| 2 | Categories of Data Subjects | `{data_subject_categories}` + standard restriction | Only the ticked categories (FR-8 row 2) |
| 3 | Types of Personal Data | `{personal_data_types}` + standard restriction | Only the ticked kinds (FR-8 row 3) |
| 4 | Special category | `{special_category_data}` | Processed only for the purpose with Annex 2 measures, or none (FR-8 row 4; A-7: follows the kinds ticked only) |
| 5 | Processing locations | `{processing_locations}` + two standard sentences | India only, or India and the listed countries; outside India only on Section 9's conditions (FR-8 row 5, FR-9). The Section 9 sentence is visible and editable (AC-14) |
| 6 | Annex 3 | `{authorised_subprocessors}` + three standard sentences | Only the listed sub-processors; any other only with prior specific written authorisation under 6.1; one outside India only on Section 9's conditions (FR-8 row 6, FR-9). The authorisation sentences are visible and editable (AC-14) |

## Component design

### Backend
- **Models:** none. **Schemas:** none (`RequestCreate.field_values`, `RequestUpdate.field_values`,
  `DraftSave.values` are already `dict`). **Settings:** none. **Celery:** none new; the existing
  `resume_workflow_runs` beat continues held steps with no one present.
- **`app/intake/agreement_forms.py`** (T002):
  - `form_fields` adds `fills`, `older_may_lack`, `legacy_single`, `rules_when`, `max_length`,
    `max_decimals` via `.get()`; `_column` adds `open_end`, `outside`, `must_be`.
  - `shown(field, values)`: per rule, `hit = any(a in rule["in"] for a in chosen)`; the rule fails when
    `hit == bool(rule.get("not"))`.
  - `clean_rows`: M9b, M13, M14 as frozen. `_tidy` is exported as `tidy` (service.py uses it for
    `max_length`).
  - `answer_text`: a list whose every item is a `str` reads `", ".join(value)` (multi-tick answers in the
    triage, flow and advisory-read prompts, FR-23/FR-32). Rows and anything else as today (007's tests pin
    `str(ROWS)` without a form and `"[1]"`).
- **`app/intake/service.py`** (T002):
  - `_validate_field_values`, `_refuse_unsound_answer(f, value, raw, values)`, `_may_stay_unanswered(f,
    cleaned, stored)`, `_check_table` as frozen above.
  - `create_request`: the facts rule above.
  - `update_request`: after `_record_deliverables_change`, `_record_answer_changes(actor=, r=, stored=,
    request_id=)` (tracked keys = the form's questions with `fills`).
  - Scoping unchanged: `get_request` filters by org and access before any change.
- **`app/intake/consultancy.py`, `app/intake/dpa.py`** (T003, new): pure, stdlib + `app.core.config` +
  `app.intake.sow` only, so `drafting.py` and `drafting_templates/service.py` import them without a cycle.
  Exports: `FIELDS`, `<x>_values(fv, *, example=False)`, `record_facts(fv)`, `EXAMPLE_ANSWERS`,
  `EXAMPLE_VALUES`, `EXAMPLE_MARKERS`, the frozen strings, `held_message(labels: list[str]) -> str`
  (fills `{answers}`), and in consultancy.py `and_join`.
- **`app/intake/drafting.py`**:
  - T003: `template_values(..., example)` returns `{**today's keys, **sow.sow_values(f, example=example),
    **consultancy.consultancy_values(f, example=example), **dpa.dpa_values(f, example=example)}`. Other
    templates' text is byte-identical (`str.format` ignores unused keys, FR-47).
  - T005: `class DraftHeld(HTTPException)`: `__init__(self, detail: str = sow.DRAFT_HELD)` →
    `super().__init__(409, detail)` (`DraftHeld()` still means the SOW, as 007's tests raise it).
  - T005: `class TemplateSkip(NamedTuple): reason: str; title: str; note: str` and
    `template_skip(request) -> TemplateSkip | None`: `None` when `request.contract_id`; a DPA request
    (`resolve_doc_type == "dpa"`) whose `our_role == dpa.PROCESSOR_ROLE` gives
    `TemplateSkip("processor_role", dpa.PROCESSOR_TITLE, dpa.PROCESSOR_NOTE)`; else `None`. Recognised by
    "Our role" only (A-18). 006 Phase 2 adds its "form request whose kind has no active template" rule here.
  - T005: `draft_hold(db, *, org_id, request) -> str | None`, a pure read (never pins):
    1. `doc = resolve_doc_type(request)`; `"sow"`: 007's three lines, verbatim.
    2. `doc not in ("consultancy", "dpa")`, or `fv.get("paper") == "Their paper"` (A-3), or
       `template_skip(request)` (FR-56): `None`.
    3. `used` = field names of `field_uses(current_wording(db, org_id, get_type(db, org_id, doc)).body)`.
    4. No `request_form` (email/chat; only `dpa` resolves from free text): `dpa.FREE_TEXT_HELD` when
       `used & set(dpa.FIELDS)`, else `None`.
    5. `needed` = the form's questions with `fills` that are `required`, `shown(f, fv)`, lack an answer
       (`None`/`""`/`[]`, or a multiselect string not among its options, so "Several" lacks and an older
       "Customers" does not) and whose `fills` meet `used` (A-10). Empty → `None`; else
       `consultancy.held_message(labels)` / `dpa.held_message(labels)`.
  - T005: `held_step(message) -> tuple[str, dict]`: `sow.DRAFT_HELD` → `(sow.HELD_STEP_NOTE, {"held":
    "deliverable_rows"})`; `dpa.FREE_TEXT_HELD` → `(message, {"held": "form"})`; else `(message +
    ANSWERS_STEP_SUFFIX, {"held": "answers"})`.
  - T005: `draft_contract_for_request`, in this order: existing-contract return; `resolve_doc_type`; the
    no-template 422 (unchanged); then, when not `custom`: `skip = template_skip(request)` → `raise
    HTTPException(422, skip.note)`; then `held = draft_hold(...)` → `raise DraftHeld(held)`. Both are before
    `_template_at_this_moment`, so nothing is pinned, rendered, stored, recorded or audited (FR-43, FR-53).
    `custom=True` (a bespoke draft) and `ingest_attachment_as_contract` are untouched (FR-56). After
    `record` is built: the SOW keys (existing), `consultancy.record_facts(fv)` or `dpa.record_facts(fv)`.
    `_sow_facts` → `_fill_facts` (the union of the keys).
- **`app/drafting_templates/service.py`** (T003): the 10 PLACEHOLDERS and the two "about" texts. Nothing
  else, so save/reset/restore/preview/check/field uses work for the new fields as for any other (FR-35,
  FR-36). The 006 status turns Out of date by the shipped-body hash with no data step (FR-37).
- **`app/drafting_templates/checks.py`** (T003): `_CODED` += `consultancy.FIELDS`, `dpa.FIELDS`; the Apply
  guard in `_enrich` checks `(*sow.EXAMPLE_MARKERS, *consultancy.EXAMPLE_MARKERS, *dpa.EXAMPLE_MARKERS)`.
- **`app/workflows/service.py`** (T005):
  1. `clm_draft` branch, inside `if path not in ("custom", "attach"):` and **before** the
     `resolve_doc_type(req) is None → custom` fallback: `skip = template_skip(req)`; when set: `sr.status =
     "skipped"`, `sr.note = skip.note`, `sr.result = {"skipped": skip.reason}`, write the
     `workflow.draft_skipped` audit and `flow.draft_skipped` timeline (actor None), `return "advance"`. No
     `_draft_contract` call, so no contract and no AI bespoke draft (FR-54). The existing-contract branch
     above it still wins (FR-56). Custom and attach steps never reach it.
  2. `except DraftHeld as held:` → `sr.status = "waiting_human"; sr.note, sr.result = held_step(held.detail);
     _assign_step(...)`; `return "wait"`.
  3. `_is_held(sr)` = `isinstance(sr.result, dict) and bool(sr.result.get("held"))`; `_HELD_FOR_ROWS` kept.
     `_resolve_waiting_group` unchanged (it already re-arms on `draft_hold(...) is None`).
  4. `complete_human_step` raises `409` with `sow.HELD_STEP_NOT_MANUAL` when `result.held ==
     "deliverable_rows"`, else `ANSWERS_STEP_NOT_MANUAL`.
  Org scope: the run is already loaded through `_authorize_run`; the request through the run.
- **`backend/eval/exam/build.py`** (T003): `_FROZEN = {k: Path(__file__).with_name(f"{k}_2026-10-15.txt")
  for k in ("sow", "consultancy", "dpa")}`; `_template_body(kind)` returns the frozen text for those keys,
  else `None`. The manifest hashes and every exam document stay byte-identical (FR-48).

### Frontend (Next.js page/component tree)
- `src/app/(app)/intake/_agreement-forms.tsx` (T004):
  - Steps: new_agreement "Commercial terms" = `[..., "scope", "advice_only", "consultancy_deliverables",
    "buying", "hosting", "payment_terms", "fee_basis", "fee_rate", "key_individuals",
    "company_contact_name", "company_contact_role", "nonstandard"]` (FR-22); dpa "The data" = `[...,
    "transfer", "transfer_countries", "subprocessors", "subprocessor_rows"]` (FR-31).
  - `UI` entries (`uses`):
    `advice_only`: "Tick only when the consultant hands over no separate deliverables; the schedule then
    says its output is its reports and advice."; `consultancy_deliverables`: "Deliverables and timetable in
    the Engagement Schedule, D1, D2, … in this order."; `fee_basis`: "The Fees entry of the Engagement
    Schedule. The total value is the fixed fee, or the most the rate can add up to."; `fee_rate`: "The day
    or hourly rate in the Fees entry, in the currency of the total value."; `key_individuals`: "Named in
    the Engagement Schedule; the consultant may not replace them without our written consent.";
    `company_contact_name`, `company_contact_role`: "Company contact in the Engagement Schedule.";
    `transfer_countries`: "Processing locations in Annex 1 of the DPA, after India, in this order.";
    `subprocessor_rows`: "Annex 3 of the DPA, one entry per row. Any other sub-processor needs our prior
    written authorisation."
  - Corrected help, now true (spec Known issues): `our_role` "Controller: drafted from our DPA template.
    Processor: no draft is made from it; attach the other side's DPA (or the paper to use) before the
    review."; `processing_purpose` "Nature and purpose of the Processing in Annex 1 of the DPA.";
    `data_types` "Types of personal data and the special-category entry in Annex 1 of the DPA. Health or
    special category data flags a DPIA before signature."; `data_subjects` "Categories of Data Subjects in
    Annex 1 of the DPA."; `transfer` "Yes asks which countries; Annex 1 of the DPA lists them, and flags it
    for the Privacy review."; `subprocessors` "Yes asks for each one; Annex 3 of the DPA lists them."
  - `DRAFT_OF.Consultancy` = `"Consultancy Agreement template"` (FR-23).
  - `problemOf` passes `rulesFor(field, values)` to `answerProblem` (was `{no_blanks, min_exclusive}`; the
    same for the SOW, which has no `rules_when`).
  - A multiselect whose value is not an array counts as blank (a saved form's "Several" is not
    submittable, FR-45). `Question` gets `earlier` = the saved draft's string for a multiselect key and
    shows `<EarlierAnswer text={earlier} title="Your earlier answer (kept for reference, not filed)" />`
    beside the checkboxes (the draft prop is constant, so it stays while the person ticks).
  - Review step: `fee_rate` shows as `money(values.currency, v)`; tables (including one-column ones) show
    through `RowsTable` read-only, as today.
  - `export function Question` (unchanged behaviour).
- `src/app/(app)/intake/_deliverables.tsx` (T004): `RowsTable` reads `field.item` for "Add a {item[0]}" and
  "A table holds at most {max} {item[1]}." (defaults keep the SOW's text); read-only reference
  `${field.ref_prefix ?? "D"}${n}`; read-only with exactly one shown column renders `<ul
  aria-label={field.label}>` of the cell values (key individuals and countries as lists, FR-23/FR-32); an
  open-ended window hints "Due dates must be on or after {start} ({start label})." with no `max` on the
  date input. `RequestDeliverables`, `EarlierAnswer`, `useRequestForms` unchanged.
- `src/app/(app)/intake/_request-answers.tsx` (T006, new): `RequestAnswers({ request, canEdit, heldNote,
  heldRunId, onSaved })`, the same props as `RequestDeliverables`.
  - `request_form === "sow"` → renders `<RequestDeliverables {...props} />`.
  - `draftable_doc_type` not `consultancy`/`dpa` → `null`.
  - No form (email/chat DPA) → only `heldNote` as `MessageBar intent="warning"`, else `null`.
  - Otherwise: heading "Answers for the draft"; `heldNote` as a warning; each shown `fills` question
    read-only: selects and text as their value, `fee_rate` with the currency, multi-tick as `join(", ")`,
    an older single choice as `EarlierAnswer` titled "Earlier answer (kept as filed)", tables through
    read-only `RowsTable`, an unanswered one as "Not answered yet". Renders `null` when no `fills`
    question is shown and there is no `heldNote` (their paper Consultancy).
  - Editor when `canEdit && needsDraftAnswers(form, fv).length > 0`: every shown `fills` question through
    the wizard's `Question` (shown-ness evaluated on the edited values, so ticking "advice only" hides the
    table), prefilled with stored answers (an older string goes to `earlier`, the control starts empty),
    and a "Save answers" `Button`. Save checks each shown question (`rowsProblems`, `lacksAnswer` for
    required, `answerProblem` with `rulesFor`), lists problems in a `MessageBar intent="error"`, else calls
    `intakeApi.update(request.id, { field_values: { ...fv, ...edited } })`; then `refreshRun(heldRunId)`
    when held (failure → toast "Saved. The draft step carries on by itself shortly."), invalidates
    `["flow-run", request.id]`, `onSaved(updated)`. Entries are kept on failure; the server message (naming
    the row) goes to a toast.
- `src/app/(app)/intake/_request-overview.tsx` (T006): `held = current?.type === "clm_draft" &&
  current.status === "waiting_human" && !!(current.result as { held?: string } | null)?.held`;
  `atReviewGate` excludes it (existing); mounts `<RequestAnswers ... onSaved={(u) => { done(fv.request_form
  === "sow" ? "Deliverables saved" : "Answers saved")(u); qc.invalidateQueries({ queryKey:
  ["intake-request", id] }); }} />` in place of `RequestDeliverables`. The Draft button's 409/422 already
  reach the toast through `fail` (FR-43, FR-53). A skipped Processor step shows its note through the
  lifecycle view's `stepDetail` (no change).
- **New shared primitives in `src/components/ui.tsx`:** none. Imported and confirmed exported: `Table`,
  `THead`, `TH`, `TR`, `TD`, `Input`, `Button`, `MessageBar`, `LoadError`, `Card`, `CardBody`; icons from
  `lucide-react`. `slate-*` and semantic tokens only, never `bg-white`.
- **Demo-mode behaviour:** not applicable (no demo mode in this checkout).

## Test strategy

All backend tests run with `MOCK_*` forced on by `conftest.py`. No paid call. File abbreviations used in
the AC map: **A** `test_consultancy_dpa_answers.py`, **U** `test_consultancy_dpa_request_updates.py`,
**D** `test_consultancy_dpa_drafting.py`, **H** `test_consultancy_dpa_hold.py`, **W**
`test_workflow_engine_consistency.py`, **FC** `form-checks.test.ts`, **WZ** `_agreement-wizard.test.tsx`,
**RA** `_request-answers.test.tsx`, **RO** `_request-overview.test.tsx`.

- **`backend/tests/fixtures/consultancy_dpa_cases.json`** (T001), the parity file both suites read:
  - `{"_about": [...], "tables": [{"id", "form", "question", "values", "rows", "error", "stored"?}],
    "fields": [{"id", "form", "question", "values", "value", "error"}]}`. The question is looked up in
    `agreement_forms.json` by form and key; `values` are the other answers the rules read.
  - `tables`, at least one case per message on each new table: deliverables (1 row, 20, 21 → M4, an empty
    row ignored, numbering counts it, missing due M5, 201-character name M6, `[Insert name]` M7,
    "Process design report"/"process  design  report" M11, window ends included, 2026-10-31 and 2027-05-01
    M9, no end: 2030-01-01 accepted and 2026-10-31 M9b, bad date M8, a line break tidied); key individuals
    (none → M3, 10, 11 → M4, "Anita Rao"/"anita  rao" M11, 121 characters M6, `[Name]` M7, an empty entry
    ignored); countries (Singapore + Germany, "Singapore"/"singapore " M11, "India" and " india " M13, 21 →
    M4, `[Country]` M7, 101 characters M6); sub-processors (a row missing its country M5, "CloudHost Pte
    Ltd"/"cloudhost  pte ltd" M11, 20 and 21, transfer No + Singapore M14, transfer No + "india" accepted,
    transfer Yes + Singapore accepted, a 301-character "What it does" M6, `[Name]` M7).
  - `fields`: scope `Advise on [TBD] processes` → F1 for Consultancy/our paper, accepted for Services (MSA)
    and for Consultancy/their paper; `Advise on (phase 1) processes` accepted; value 0 and -5 → F2 for
    Consultancy/our paper, 1 accepted, 0 accepted for MSA; rate 0, -5 → F2, 25.001 and `1e3` → F4, 25000
    and 25000.50 accepted; contact name/role 121 characters → F3, `[Role]` → F1, `Head of Quality (acting)`
    accepted, a 121-character bracketed name → F3 (order); purpose `Hosting of [TBD] systems` → F1 for our
    paper, accepted for their paper; `Hosting of (phase 1) systems` accepted.
  - 007's `deliverable_rows_cases.json` is not edited.
- **A** (T002, no DB): every `tables` case through `clean_rows`; every `fields` case through
  `_validate_field_values([f], {**values, key: value})`; a filing pass of complete Consultancy and DPA
  answers through the real form fields; FR-12 (MSA, NDA, Software or SaaS, Buying from a vendor, Selling to
  a customer, Something else, and Consultancy on their paper file without any new answer and drop sent
  ones); advice only drops sent rows; fee basis Fixed fee drops a sent rate; a rate basis without a rate
  refused naming "Rate per day or hour"; `[]` for "Whose data?" refused; "Several" kept on an unchanged
  update and refused on a new filing; `older_may_lack` (an older Consultancy updated without the answers
  passes; with `fee_basis` set to "Day rate" and no rate refused; their paper → our template requires
  them); `shown` with `not`; `answer_text` for a multi-tick list; `triage_agent._prompt` and the flow
  agent's prompt show "Customers, Patients or trial subjects" and numbered sub-processor rows (closes 007's
  P3-3).
- **U** (T002, DB, rolled back): `intake.created` audit holds the new answers exactly as filed (AC-55);
  deliverables 2 → 1 rows: `intake.answers_changed` audit + timeline with both before/after (AC-55);
  "Day rate" → "Fixed fee" removes the rate and the audit shows it (AC-20); "Fixed fee" → "Hourly rate"
  without a rate 422; DPA Yes → No removes countries and rows, audited; No → Yes without entries 422
  (AC-33); an older DPA with "Several": priority update and another-answer update succeed, the stored
  value is unchanged (AC-45); the same invalid answers (a due date outside the window; a sub-processor row
  with no name) are refused with the same message through `create_request`, `update_request` and the
  assistant's `ToolRuntime()._create_intake_request` (AC-25, AC-34; closes 007's P3-4); a key individual or
  contact named like a user creates no Notification, User, grant or link and `get_request` still 404s for a
  same-org non-staff non-requester (AC-22, AC-57); another org's request → 404, nothing audited (AC-57).
- **D** (T003, no DB except where noted):
  - AC-1/AC-7: `find_blanks(default_body(k))` empty and no `[` or `]` for consultancy and dpa.
  - AC-2/AC-8: drafts of the AC example Consultancy request under Fixed fee, Day rate 25000, Hourly rate
    3500 and advice only, and of the example and plain DPA requests: no `find_blanks`, no `{`/`}`, no
    "delete as appropriate".
  - AC-3, AC-4, AC-9 to AC-12, AC-15: the exact place texts (Services, D1/D2 lines, "15 December 2026",
    "Anita Rao and Dev Menon.", "Ravi Shah, Head of Quality.", categories, types, special yes/no,
    "India, Singapore and Germany.", "India.", the two sub-processor entries in order, "None.").
  - AC-5: the three fee statements; "INR 1,800,000.00" in the cap; "INR 25,000.00 per day"; no basis offered
    as an alternative.
  - AC-6, AC-13: in the five schedule places (for each fee basis) none of `payable in advance`, `in
    advance`, `unlimited`; the only "direct or supervise" there is the Company contact's negated standard
    sentence; the fee cap names "a written amendment signed by both Parties"; the key-individuals place
    says "prior written consent"; every DPA place keeps its "only" limits and the Section 6.1 and Section 9
    sentences.
  - AC-14 (DB): the standard sentences are in `default_body`; a saved wording with "prior written consent"
    changed to "prior consent in writing" drafts the new sentence.
  - AC-37: `render_document(k, fields={}, example=True)` and `render_sample(default_body(k))` blank-free,
    brace-free, with the FR-33 contents and due dates inside both terms; samples equal `EXAMPLE_VALUES`;
    `checks.check_values(k)` equals them for the new fields.
  - AC-38: no `EXAMPLE_MARKERS` in any AC-fixture draft; the markers do find the example reading.
  - AC-39: the 10 names are listed with label, about and sample; labels unique; a saved wording using one
    passes `check_body`; `{deliverable_table}` refused.
  - AC-42 (text half): the text a check sends equals the example reading and is blank-free; a finding
    quoting "Northwind Hosting Ltd" or "Portal discovery report" has `suggestion_is_wording` false.
  - AC-52 / FR-47: for nda, msa, saas, vendor and sow, bare and full renders equal what they render with
    the 10 new keys removed; none of their default bodies uses a new field.
  - AC-53: the frozen copies' sha256 equal the committed `eval/exam/content/manifest.json` hashes
    (`9b8ea85d…bdad`, `4f2c4c57…9325`); `_template_body` maps all three keys; `PROCESSOR_ROLE` equals the
    JSON option.
  - Changes to 007 tests (T003; required by FR-1/FR-6 and by FR-35's reuse of `services`/`fee_amount`):
    `test_sow_drafting.py::test_the_blanks_the_other_templates_already_had_are_untouched` now expects 0 for
    consultancy and dpa (renamed `..._are_gone`, docstring updated);
    `test_no_other_template_changes_for_the_sow_fields` removes from `values` only the SOW fields a
    template does not use and asserts consultancy uses exactly `{"services", "fee_amount"}` of them and
    every other template none.
- **H** (T005, DB pattern and helpers of `test_template_drafting_record` / `test_sow_drafting`):
  - AC-46: an older Consultancy request + shipped wording → `DraftHeld` 409 naming the five answers, no
    Contract, no `DraftingTemplateType` pin, no audit row, no stored file; after entering valid answers the
    draft holds exactly them and no example content; the stored older answers are identical.
  - AC-47: an older DPA (Controller, Customers, Contact and identity, No, No) drafts at once; one with
    "Several", one with Yes to sub-processors and no rows: held; an org wording with none of the new fields:
    no hold, drafted with its brackets (AC-41).
  - AC-48: an email "please draft a DPA" → 409 `FREE_TEXT_HELD`, nothing created; a their-paper DPA and a
    their-paper Consultancy are not held.
  - AC-58, AC-61: a Processor request → 422 `PROCESSOR_NOTE`, no Contract, no file, no audit or timeline
    row; one that also holds "Several" gets the Processor message; `custom=True` still drafts; a Processor
    request with a contract returns that contract; an email DPA with no role is held, not skipped.
  - AC-54: the FR-49 keys (2, "Fixed fee", 2; 2, 2, true) in the timeline details, the audit `after` and
    `metadata_json.drafted_from_template`.
  - AC-41: a completed check whose `wording_sha256` is the old shipped hash → `template_status` Out of
    date, wording changed, with no `JobRun` or AI row created by reading; never checked stays never
    checked; reset gives the new wording as a new version.
  - AC-44: a contract drafted from the old wording keeps its snapshot text after the template is edited
    and reset.
- **W** (T005, fake DB as in the file, plus one real-DB case like 007's):
  - `_draft_contract` raising `DraftHeld(consultancy message)` → `waiting_human`, note = message +
    `ANSWERS_STEP_SUFFIX`, `result == {"held": "answers"}`, no contract; `complete_human_step` → 409
    `ANSWERS_STEP_NOT_MANUAL`; the resolver re-arms when `draft_hold` gives `None` (monkeypatched) (AC-49).
  - 007's SOW held-step assertions pass unchanged.
  - AC-59/AC-60: a Processor request at a fast-lane `clm_draft` step → `skipped`, `PROCESSOR_NOTE`,
    `result == {"skipped": "processor_role"}`, `_draft_contract` never called, the run advances to the next
    step, one `workflow.draft_skipped` audit and one `flow.draft_skipped` timeline row with actor None; the
    same with no person present (the beat's actor path); a step with `config.mode` "custom" or "attach"
    behaves as today; a run that already has a contract takes the existing skip.
  - Real DB (AC-49): an older Consultancy request's workflow waits at the draft step with no contract, the
    manual complete is refused, and after the answers are saved a `refresh_run` drafts it.
- **FC** (T004): every case of the new file (`rowsProblems(...)[0]?.message ?? null === error` and
  `tidiedRows` equals `stored`; `answerProblem(kind, value, undefined, rulesFor(f, values))` equals the
  server message with label and full stop stripped); `shown` with `not`; `dateWindow` open end;
  `lacksAnswer`; `needsDraftAnswers` (empty for their paper and for a Processor request; "Whose data?" for
  "Several"); 007's cases still pass.
- **agreement-forms.test.ts** (T004): every new question is placed on its step and says what it does; the
  Consultancy questions show only for Consultancy + Our template; the DPA tables only for Yes + Our
  template; `within` names two date questions of the same form; every `show`/`must_be.when` rule names a
  question of the same form whose options contain its values.
- **WZ** (T004): AC-16 (shown for Consultancy + Our template; none for their paper or any other kind);
  AC-17 (table rules beside the cell and in the banner; add disabled at 20 with the reason); AC-18 (window,
  evergreen start-only, end moved re-flags the row, the past-date note does not block); AC-19 (tick hides
  the table, rows return when unticked, the filed body has no rows); AC-20 (rate shown only for a rate
  basis, kept across switches, not filed for Fixed fee); AC-22, AC-23, AC-24 (lists and contact rules);
  AC-26 (keyboard-operable add/fill/remove, errors linked; LoadError with Retry; nothing while loading;
  entries kept and message shown on a failed filing); AC-27/AC-36 review step (rows, lists, rate with its
  currency, "Consultancy Agreement template", "DPA template"); AC-28 to AC-33 for the DPA (multi-tick, none
  ticked refused, countries and sub-processors appear for Yes and keep their entries when switched to No and
  back); AC-50 (a saved form with "Several" reopens empty with the earlier answer beside it and cannot be
  submitted; a saved Consultancy form shows the new questions empty and required).
- **_deliverables.test.tsx** (T004): the item noun and cap text; plain-number references with `ref_prefix:
  ""`; a one-column table read-only as a list; the open-end hint and no `max`; 007's cases unchanged.
- **RA** (T006): an older Consultancy request shows "Not answered yet" and the editor only to someone who
  may edit and only before drafting; ticking advice only hides the table; save sends `{ ...field_values,
  ...edited }` and refreshes the held run; a 422 keeps the entries; an older DPA shows "Several" as the
  earlier answer and requires a tick; a complete request shows read-only answers; a Processor request
  shows read-only answers and no editor; their paper Consultancy renders nothing; a SOW request renders
  `RequestDeliverables` (AC-27, AC-36, AC-46, AC-47, AC-49).
- **RO** (T006): a held answers step shows its note in the block and no "Approve & advance"; saving
  refreshes the run; a skipped Processor step shows as skipped with its note; 007's SOW cases unchanged.

Acceptance verification (T007) maps each AC to its evidence:

| AC | Evidence |
|---|---|
| 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 15, 37, 38, 39, 52, 53 | D (named cases) |
| 14, 41, 44, 46, 47, 48, 54, 58, 61 | H; AC-41 also a live `GET /drafting-templates` on the dev stack (Out of date for a template whose last check covered the old wording; the `drafting_template_check` count unchanged) |
| 16, 17, 18, 19, 20, 21, 22, 23, 24, 28, 29, 30, 31, 32, 33 | FC + A on the same cases file; WZ for the wizard half |
| 25, 34, 45, 55, 57 | U (+ A); AC-55's "no user can edit or delete" by the `audit_log_no_update`/`audit_log_no_delete` triggers; AC-57's wall clause rides on unchanged access code (007's P3-5 gap; one wall test if the owner wants it executed) |
| 26, 35 | WZ + `_deliverables.test.tsx`; browser walk for keyboard and screen reader |
| 27, 36 | WZ, RA, RO; A for the advisory-read prompt |
| 40, 43 | 006's Templates tests unchanged; `git diff` of the Templates page is empty; live walk of save/preview/check/reset/restore on both templates (a check is paid: owner's go-ahead) |
| 42 | D for the text read; live: one owner-approved check of each template after release, with its actual findings recorded (paid) |
| 49, 59, 60 | W (fake and real DB); RO |
| 50 | WZ |
| 51 | D (`resolve_doc_type` for the email "need a DPA for GDPR", a Consultancy form request, a Processor DPA request) |
| 56 | `git diff` shows no change to `app/core/rbac.py` or any `require_permission(...)`; existing 403 tests unchanged |

## Risks & decisions

- **No migration: answers live in `field_values` under new keys.** Rejected: tables for deliverables,
  people, countries and sub-processors (schema, models, scoping queries and a migration for small lists
  only ever read with their request).
- **Lists reuse the `rows` kind with one column** (key individuals, countries). Every rule FR-18 and FR-25
  ask for (tidy, ignore empty, 1–N, length, unique, no brackets) already exists there with frozen messages,
  in both languages. Rejected: a `list` kind (a second implementation of the same rules and a second cases
  section). Cost: messages say "row" ("…, rows 1 and 2 have the same "Name"…"), and the editor is a
  one-column table; read-only it is a list.
- **"Advice only" is a one-option `multiselect` plus a `not` show rule.** It reuses the existing checkbox
  control, option validation and string comparison in both `shown()`s. Rejected: a `boolean` kind (a new
  control, and `shown()` compares strings on one side and booleans on the other); a required "Are there
  deliverables?" select (it would force an answer the spec makes optional, and AC-19 expects FR-13's
  refusal when nothing is ticked and nothing is listed).
- **The Company contact is two text questions with `max_length`**, not a one-row table: a natural form
  ("Company contact: role" is required) with one small new rule (F3, mirrored).
- **`rules_when` scopes a shared question's rule to Consultancy on our paper** (A-4, FR-12). Rejected:
  separate `consultancy_scope`/`consultancy_value` keys, which every reader of `scope` and `value` (request
  facts, value conditions, Finance approval) would then have to know.
- **"Whose data?" and "What kind of personal data?" become multi-tick for every DPA request**, their paper
  included. One key has one kind; the spec requires multi-tick for our paper and is silent for theirs.
  Rejected: separate keys per paper (two questions for one fact). The follow-up tables are asked for our
  paper only (A-3).
- **Older answers are kept by two declared rules** (`older_may_lack`, `legacy_single`) rather than by
  rewriting anything (FR-41, D-9). The `older_may_lack` exemption ends the moment an answer it depends on
  changes, so "update to a rate basis needs a rate" and "update to Yes needs entries" hold (FR-16, FR-29).
- **The hold is generic for Consultancy and DPA and driven by `fills`**; the SOW keeps 007's three lines
  verbatim. Rejected for now: moving the SOW onto `fills` (one JSON attribute, but it would change 007's
  form JSON and two code paths that tests pin). Ceiling: two branches in `draft_hold`.
- **The request page knows what is missing by mirroring the hold's "lacks" rule in TypeScript**
  (`needsDraftAnswers`), not by a new response field. Rejected: `draft_needs` on `RequestResponse` (an API
  change and a template read per listed request). Ceiling: for an org wording that uses none of the new
  fields the page still offers the editor, although the draft would not be held; entering the answers is
  harmless.
- **The Processor rule is `template_skip`, one function both callers use**: a person gets 422 (as the
  "no draft template" refusal, D-10), a workflow step is skipped with the same note and an audit row, and
  it sits exactly where 006 Phase 2's "no active template" rule goes. Rejected: 409 (the request's state
  will not change to allow it); a new step type or config flag (the builder can't set one).
- **Skip before hold** (FR-56) and **both before anything is written** (FR-43, FR-53), so neither leaves a
  pin, file, contract or audit row.
- **Standard wording is in the template; only varying text is a field** (FR-10). Where a sentence depends
  on the answer (the fee basis, advice only, special category yes/no, "None."), the whole sentence is the
  field; where it holds either way (the fee cap, invoicing, the consent sentence, Section 9, 6.1
  authorisation), it is template wording a person can edit and check (AC-14).
- **Three standard sentences go beyond the Fill map's literal list** and need counsel's eye (A-9): the fee
  cap also on a fixed fee, the Company contact "does not direct or supervise", and the "shall not process
  any other category/type" restrictions. Each only restates or strengthens a position the playbooks prefer.
- **`[]` is no answer** in the validator's main loop (FR-24), which also aligns the Amendment form's
  multi-tick with what the wizard already refuses.
- **The exam keeps frozen copies** of today's consultancy and dpa wording (D-3), whose hashes equal the
  committed manifest. Rejected: rebuilding the exam (new offsets, the 005 baseline lost).
- **Risk:** stored answers are trusted at drafting; every write path validates them, and a malformed value
  written straight to the DB would draft oddly (as 007).
- **Risk:** an admin workflow condition on `data_types`/`data_subjects` with "Several" no longer matches a
  new request; `conditions_hold` already reads lists ("is" = any pick). No starter workflow has one.
- **Risk:** the bespoke paths (`_custom_shell`, the AI draft instructions) print list answers as Python
  lists. They are not template drafts (Out of scope) and were already like this for the SOW rows.
- **Risk:** `RequestFormField.kind` is unchanged, but the new optional attributes and `dateWindow`'s `end:
  string | null` need every caller updated; `tsc` catches them.

FR traceability (all 56):

| FR | Component |
|---|---|
| FR-1 | `consultancy.txt` new schedule (no bracket); D AC-1 |
| FR-2 | `consultancy.txt` + `consultancy_values`; F1/M7 on every Consultancy text that reaches it (scope via `rules_when`, names, contact) |
| FR-3 | Fill map table (5 places) + `consultancy_values` formats |
| FR-4 | `consultancy_fee_basis` statements + the Fees standard cap and invoicing sentences |
| FR-5 | standard consent, cap and contact sentences; no "advance" in any fill; 6.3–6.5 unchanged |
| FR-6 | `dpa.txt` new Annex 1 and 3 (no bracket); D AC-7 |
| FR-7 | `dpa.txt` + `dpa_values`; F1/M7 on purpose and every table cell |
| FR-8 | Fill map table (6 places) + `dpa_values` formats |
| FR-9 | Annex standard wording (instructions, purpose, 6.1, Section 9); Sections 3, 5, 10, 11 unchanged |
| FR-10 | sentences in the templates; fields only for varying text |
| FR-11 | `sow.day`, `sow.money`, stored order, D-numbers, uniqueness (M11), `and_join` |
| FR-12 | JSON `show` rules `[C, OT]`; `rules_when` keeps other kinds and their paper as today |
| FR-13 | `consultancy_deliverables` rows (1–20, 200, unique, no blanks, M5) |
| FR-14 | `within` + `open_end` (M9/M9b), re-checked on every update; `pastDueRows` note |
| FR-15 | `advice_only` + the `not` rule; wizard keeps hidden rows |
| FR-16 | `fee_basis`/`fee_rate` (F2, F4); `show` strips the rate; `older_may_lack` ends when the basis changes |
| FR-17 | `value` `min_exclusive` with `rules_when` |
| FR-18 | `key_individuals` rows (1–10, 120, unique); plain strings, nothing reads them but the fill |
| FR-19 | `company_contact_name`/`_role` (required, F3, F1) |
| FR-20 | `no_blanks` on scope (`rules_when`), names, contact |
| FR-21 | one validator for wizard/assistant/API/update + TS mirror + shared cases |
| FR-22 | wizard step placement; `RowsTable` (labels, add/remove, cap reason, errors); LoadError |
| FR-23 | review-step tables/lists, `DRAFT_OF`; `RequestAnswers`; `answer_text` |
| FR-24 | `data_types`/`data_subjects` multiselect, required, `[]` refused, no "Several" |
| FR-25 | `transfer_countries` (1–20, 100, unique, M13) |
| FR-26 | `subprocessor_rows` (three columns, 1–20, unique name) |
| FR-27 | `must_be` (M14) on the sub-processor country |
| FR-28 | `no_blanks` on purpose (`rules_when` our paper) and every DPA table cell |
| FR-29 | `show` rules (Yes/No), server pop, `older_may_lack`; wizard keeps hidden entries |
| FR-30 | same validator and mirror as FR-21 |
| FR-31 | wizard DPA step placement |
| FR-32 | review step, `RequestAnswers`, one-column lists, `answer_text` |
| FR-33 | `EXAMPLE_ANSWERS`/`EXAMPLE_VALUES` of both modules; samples = example readings |
| FR-34 | `example=True` only in checks; markers in the Apply guard; D AC-38 |
| FR-35 | 10 PLACEHOLDERS; `services` and `fee_amount` reused with new "about" |
| FR-36 | 006 service unchanged for both templates |
| FR-37 | hash-based status; no data step; no check triggered |
| FR-38 | D: the check's text blank-free; QA records one owner-approved check of each |
| FR-39 | 006 page text unchanged |
| FR-40 | no re-draft or migration; the record is written only for new drafts |
| FR-41 | `legacy_single`; `EarlierAnswer` on the request page |
| FR-42 | `older_may_lack`, `legacy_single`, unchanged-answer rule exemption (007) |
| FR-43 | `draft_hold` + `DraftHeld(message)` + held messages + `RequestAnswers` editor + `FREE_TEXT_HELD` |
| FR-44 | workflow held catch (`held_step`), 007 re-arm, `ANSWERS_STEP_NOT_MANUAL` |
| FR-45 | wizard: new questions start empty; multi-tick string is blank and shown as `earlier` |
| FR-46 | `resolve_doc_type` and `_draftable` untouched |
| FR-47 | other `defaults/*.txt` untouched; `str.format` ignores extra keys; D AC-52 |
| FR-48 | `_template_body` + two frozen copies; D AC-53 |
| FR-49 | `record_facts` keys via `_fill_facts` |
| FR-50 | `intake.created` facts; `intake.answers_changed` audit + timeline |
| FR-51 | no `require_permission` change |
| FR-52 | answers inside `field_values` under `get_request`'s scope |
| FR-53 | `template_skip` → 422 `PROCESSOR_NOTE` before the hold, nothing written |
| FR-54 | `clm_draft` skip branch before the custom fallback; custom/attach untouched |
| FR-55 | `workflow.draft_skipped` audit + `flow.draft_skipped` timeline, actor None |
| FR-56 | skip before hold; `custom`, ingest and existing-contract paths untouched; role-only recognition |

## Open questions

None blocks the design. Each has a default the plan already uses. Please confirm or change.

1. **A their-paper Consultancy or DPA request can still reach a template draft step** (asked by the spec,
   A-3). It does not on the normal path: the wizard requires their file and ingests it right after filing,
   so the contract exists and the draft step skips. It can when the ingest fails ("could not be read") or
   the background autostart wins the race, and then today's code drafts our template; after this feature
   that draft has the new places empty (no bracket, so the signature gate does not stop it).
   (a) *Default:* leave it, as the spec says (AC-48: their paper is not held).
   (b) Add the paper condition to the starter Consultancy and DPA draft steps (006 noted the same gap for
   MSA, Vendor and SaaS), a workflow-library change in a follow-up.
   (c) Hold their paper with no contract ("attach their draft first").
   *Recommend (a) now and (b) as a follow-up.*
2. **The exam's Consultancy and DPA documents** (`backend/eval/exam`, D-3).
   (a) *Default:* render them from frozen copies of today's wording; documents, hashes and baseline unchanged.
   (b) Rebuild the exam on the new wording (new offsets, baseline lost).
   *Recommend (a).* It touches files outside the usual ownership, so it needs your yes.
3. **The review step for a Processor request.** FR-32 says the review step keeps naming the DPA template,
   but no draft will be made.
   (a) *Default:* as the spec says ("DPA template").
   (b) Say "No draft from our template (we are the Processor): attach the other side's DPA" when "Our role"
   is Processor (one line in `draftFor`).
   *Recommend (b)*: it tells the requester before filing what they will be told after.
4. **Which Consultancy requests the zero-value and bracket rules apply to.**
   (a) *Default:* Consultancy on our paper only (FR-12 keeps their paper as today).
   (b) Every Consultancy request (FR-17/FR-20 read alone).
   *Recommend (a)*: their paper never reaches our template.
5. **Wording for counsel** (A-9): the three standard sentences beyond the Fill map's literal list (the fee
   cap also stated for a fixed fee; the Company contact "does not direct or supervise"; "shall not process
   any other category/type") and the four data-kind phrases. *Recommend* approving the plan with them and
   letting counsel adjust the template text before release; tests pin substance, not every word.
6. **Help text.** The spec lists the "Will data leave India?" help as a known issue not fixed here. The
   question now drives Annex 1, so the plan rewrites it (and the five other DPA help lines) to say what the
   answers really do. *Recommend* keeping that; say if the transfer line must stay as it is.
7. **Over-built candidates in the spec** (kept in this plan, not dropped):
   (i) FR-27's cross-check (M14): Annex 3 already allows a sub-processor outside India only on Section 9's
   conditions, so refusing the contradiction is belt and braces (one rule on both sides plus cases).
   (ii) FR-45's earlier "Several" shown in a saved form: matters only for forms saved before release.
   (iii) FR-49's counts and FR-50's per-answer audit: cheap, already the 007 pattern.
   (iv) FR-14's past-date note: already built in 007, no new code.
   *Recommend keeping all.* (i) is the only one with real cost; drop it if you prefer an annex that may name
   a foreign sub-processor while saying data stays in India.
