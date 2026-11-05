# Implementation Plan: Statement of Work Filled From the Request

Feature ID: 007-sow-filled-from-request
Spec: ./spec.md (must be APPROVED)
Created: 2026-10-15
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->

## Architecture overview

The feature changes three existing modules and adds one. **It needs no migration, no new
endpoint, no new permission, no new setting and no new dependency.** The rows live in the
request's existing `field_values` JSON. It mirrors the shapes already in `app/intake/`:

- the form-definition loader `app/intake/agreement_forms.py` (questions, `show` rules) and its
  JSON;
- the filing validator `_validate_field_values` in `app/intake/service.py`, which create and
  update already share;
- the drafting bridge `app/intake/drafting.py` (`template_values` / `render_document` /
  `draft_contract_for_request`);
- the 006 Templates domain `app/drafting_templates/` (PLACEHOLDERS, checks).

```
 wizard (_agreement-forms.tsx) ── rows control (_deliverables.tsx) ── rules mirror (lib/form-checks.ts)
        │  POST /intake/requests                     ▲ same rules, same messages (shared cases JSON)
        ▼                                            │
 intake/service._validate_field_values ──▶ agreement_forms.clean_rows  (new question kind "rows")
        │  stored in IntakeRequest.field_values["deliverable_rows"]
        │  PATCH /intake/requests/{id}: stored-aware (older requests), audit intake.deliverables_changed
        ▼
 intake/drafting.draft_contract_for_request ──▶ draft_hold() ──409 DraftHeld (older request, no rows)
        │                                   └──▶ template_values() ──▶ intake/sow.sow_values() (lists, pricing, money)
        ▼                                                         sow.txt: standard wording + 9 new fields
 workflows/service clm_draft step: catches DraftHeld → waiting_human "held"; _resolve_waiting_group re-arms it
 drafting_templates/checks: check reads render_document(..., example=True) → sow.EXAMPLE_VALUES
 drafting_templates/service: PLACEHOLDERS += 9 (samples = sow.EXAMPLE_VALUES) → Templates page field list, preview
```

What is new and what is reused:

- **New module `backend/app/intake/sow.py`.** Pure functions with no database: the nine SOW
  fill values built from the answers, the payment arithmetic, the example reading, and the hold
  message. It imports only the stdlib and `app.core.config`, so both `intake/drafting.py` and
  `drafting_templates/service.py` can import it without a cycle. Today `drafting.py` imports
  `drafting_templates.service`, so the samples cannot live in `drafting.py`.
- **New question kind `rows`**, declared in `agreement_forms.json` with its columns and rules.
  `agreement_forms.clean_rows` enforces it on the server. `lib/form-checks.ts` mirrors it in the
  wizard, the way `not_before` and the email rule are mirrored today. One shared cases file,
  read by both test suites, keeps the two from drifting (FR-16).
- **Reused unchanged:** `find_blanks` / `first_blank` (`app/contracts/blanks.py`, the definition
  of a blank for FR-1, FR-2 and FR-14), `shown()`, `_long_date`-style dates, `write_audit_log` /
  `write_timeline_event`, the 006 record (`_template_at_this_moment`, `on_created`),
  `current_wording` / `field_uses` / `check_body`, `template_status` (a changed shipped body turns
  a check Out of date by hash, FR-27), `_assign_step`, `refresh_run` and the
  `resume_workflow_runs` beat (FR-45 continue), and the `Table`, `Input`, `Button`,
  `MessageBar` and `LoadError` primitives.
- **Rows go under a new key, `deliverable_rows`.** The old free-text key `deliverables` is never
  written again. An older request keeps it untouched, and the server carries it over on every
  update (FR-31). Both can exist on one request. This is why no conversion, migration or
  rewrite is needed (D-5).

Discrepancies flagged:
- The constitution mentions `src/lib/demo.ts`, but there is no demo mode in this checkout, so
  there is nothing to keep working.
- `can()` in `lib/intake.ts` returns true for anyone, so the server's `intake:update` check is
  the real gate for entering rows.
- AC-1 says "35 in today's wording". That counts occurrences. `find_blanks` de-duplicates and
  returns 17 distinct blanks. AC-1's test counts occurrences, and the target is zero either way.

## Path mapping (ownership boundaries for THIS feature — exact files)

| Agent | Task | Files in this feature |
|---|---|---|
| db-engineer | — | **none** (no model, no migration; head stays `0074_template_sync`) |
| backend-dev | T001 | `backend/app/intake/agreement_forms.json` (form `sow` only: replace `deliverables` with `deliverable_rows`; `no_blanks` on `sow_title`, `scope`, `cp_signer_name`, `cp_signer_email`; `min_exclusive: 0` on `value`), `backend/tests/fixtures/deliverable_rows_cases.json` (new: the shared rule cases, exact messages) |
| backend-dev | T002 | `backend/app/intake/agreement_forms.py` (`form_fields` passes the new attributes; `clean_rows`; `answer_text`), `backend/app/intake/service.py` (`_validate_field_values(..., stored=)`, `no_blanks`, `min_exclusive`, rows post-pass, legacy rules; `create_request` audit facts; `update_request` passes `stored`, writes `intake.deliverables_changed`), `backend/app/intake/triage_agent.py` (`_prompt` uses `answer_text`), `backend/app/intake/flow_agent.py` (same one line). Tests: `backend/tests/test_sow_deliverable_rows.py` (new), `backend/tests/test_sow_request_updates.py` (new), `backend/tests/test_intake_form_values_make_sense.py` (`complete()` learns the `rows` kind) |
| backend-dev | T003 | `backend/app/intake/sow.py` (new), `backend/app/drafting_templates/defaults/sow.txt` (new wording), `backend/app/intake/drafting.py` (`example=` on `template_values`/`render_document`; `DraftHeld`; `draft_hold`; the hold in `draft_contract_for_request`; FR-37 record keys), `backend/app/drafting_templates/service.py` (9 `PLACEHOLDERS` entries; the `value_sentence` sample), `backend/app/drafting_templates/checks.py` (`example=True` in `check_values` and `_review`; `_CODED` += 9; the Apply guard in `_enrich`), `backend/eval/exam/build.py` (`_render` drafts `sow` from the frozen copy), `backend/eval/exam/sow_2026-10-15.txt` (new: a byte-for-byte copy of today's `defaults/sow.txt`). Tests: `backend/tests/test_sow_drafting.py` (new), `backend/tests/test_template_check.py` and `backend/tests/test_template_check_jobs.py` (`_bare_draft` passes `example=True`) |
| backend-dev | T005 | `backend/app/workflows/service.py` (clm_draft catches `DraftHeld`; `_resolve_waiting_group` re-arms a held step; `complete_human_step` refuses a held step). Tests: `backend/tests/test_workflow_engine_consistency.py` (extend) |
| frontend-dev | T004 | `frontend/src/lib/types.ts` (form types, `FormRow`, `FormValue`, `IntakeDraft.values`), `frontend/src/lib/form-checks.ts` (`findBlanks`, `tidyCell`, `rowsProblems`, `shareTotal`, `pastDueRows`, `longDate`, `answerProblem` rules), `frontend/src/lib/form-checks.test.ts`, `frontend/src/app/(app)/intake/_deliverables.tsx` (new: `RowsTable`, `RequestDeliverables`), `frontend/src/app/(app)/intake/_deliverables.test.tsx` (new), `frontend/src/app/(app)/intake/_agreement-forms.tsx`, `frontend/src/app/(app)/intake/agreement-forms.test.ts`, `frontend/src/app/(app)/intake/_agreement-wizard.test.tsx` |
| frontend-dev | T006 | `frontend/src/app/(app)/intake/_request-overview.tsx` (mount `RequestDeliverables`; held-step handling), `frontend/src/app/(app)/intake/_request-overview.test.tsx` |
| qa-engineer | T007 | `specs/007-sow-filled-from-request/verification.md` |

`backend/eval/exam/**` is outside every agent's default ownership. It is assigned to backend-dev
for T003 only, because T003's template change would otherwise break the exam content tests. See
Risks & decisions.

Shared-hotspot note:
- `frontend/src/lib/types.ts`: T004 only. `frontend/src/lib/endpoints.ts`: **no task**
  (`intakeApi.update` and `workflowsApi.refreshRun` already take what is needed).
  `frontend/src/components/ui.tsx`: no task. `backend/app/models.py`: no task.
  `backend/app/jobs/tasks.py`: no task.
- `backend/app/intake/agreement_forms.json`: T001 only. T002 and T004 read it and must start
  after T001.
- `backend/app/intake/drafting.py`: T003 only. `backend/app/intake/service.py`: T002 only.
  `backend/app/workflows/service.py`: T005 only.
- `frontend/src/app/(app)/intake/_deliverables.tsx`: T004 creates both exports. T006 only imports
  `RequestDeliverables`.
- `frontend/src/app/(app)/intake/_agreement-forms.tsx`: T004 only (T006 imports
  `useRequestForms`, which already exists).

Wave shape:
- **Wave 1:** T001 and T003 in parallel (no shared files).
- **Wave 2:** T002 (after T001), T004 (after T001) and T005 (after T003) in parallel.
- **Wave 3:** T006 (after T004).
- **Wave 4:** T007 (after all).

No migration task, so the Alembic rule is moot.

## Interface freeze (contract-first — fixed before implementation starts)

All paths are under `/api/v1` (client paths omit it). **No endpoint is added or removed and no
permission string changes (FR-39).** Every touched route keeps its gate and its org scope:
`get_request` filters `org_id == user.org_id` and lets only staff (`intake:read`) or the
requester read a request. Rows are part of `field_values`, so they inherit exactly the request's
visibility (FR-40). There is no new query. A request is not contract-adjacent until it is
drafted, and the drafted contract keeps `accessible_contract_filter` unchanged.

### API contract

| Method | Path | Permission (unchanged) | Request body change | Response change | New/changed errors |
|---|---|---|---|---|---|
| GET | /api/v1/intake/forms | `intake:create` | — | form `sow`: question `deliverable_rows` (kind `rows`) replaces `deliverables`; `no_blanks` / `min_exclusive` attributes | — |
| POST | /api/v1/intake/requests | `intake:create` + screen `intake` ADD | `field_values.deliverable_rows: FormRow[]` for `request_form: "sow"` | `field_values.deliverable_rows` as cleaned | 422: table messages M1–M12, field messages F1–F2 |
| PATCH | /api/v1/intake/requests/{id} | `intake:update` + screen `intake` EDIT | same | same | 422 as above; never for an older request's untouched free text (FR-32) |
| POST | /api/v1/intake/requests/{id}/draft-contract | `intake:read` + screen `intake` EDIT | — | — | **409** `DRAFT_HELD` (FR-33) |
| POST | /api/v1/workflows/runs/{id}/complete-step | `intake:read` | — | — | **409** `HELD_STEP_NOT_MANUAL` when the step is a held draft (FR-45) |
| POST | /api/v1/workflows/runs/{id}/refresh | `intake:read` | — | a held draft step whose rows now exist goes on and drafts | — |
| GET | /api/v1/drafting-templates/{key} | `playbook:read` | — | `placeholders` gains 9 entries; `sow` shipped `body`/`default_body` is the new wording | — |
| POST | /api/v1/drafting-templates/{key}/preview | `playbook:read` | — | the 9 new fields fill with the example reading | — |
| POST | /api/v1/drafting-templates/{key}/checks (and the job) | `playbook:run` | — | the check reads the example reading; a finding whose suggested wording quotes example content has `suggestion_is_wording: false` | — |

#### Full request/response JSON shapes

```json
// GET /intake/forms  — the SOW form's new question (it takes the old question's place, after "scope")
{
  "key": "deliverable_rows",
  "label": "Deliverables",
  "kind": "rows",
  "required": true,
  "min_rows": 1,
  "max_rows": 20,
  "legacy_key": "deliverables",
  "legacy_label": "Deliverables and who accepts them",
  "columns": [
    {"key": "name",        "label": "Deliverable name",     "kind": "text",    "max_length": 200, "unique": true, "no_blanks": true},
    {"key": "due",         "label": "Due date",             "kind": "date",    "within": ["services_start", "services_end"]},
    {"key": "their_owner", "label": "Their owner",          "kind": "text",    "max_length": 120, "no_blanks": true},
    {"key": "our_owner",   "label": "Our owner",            "kind": "text",    "max_length": 120, "no_blanks": true},
    {"key": "share",       "label": "Share of the fee (%)", "kind": "percent", "total": 100,
     "show": [{"field": "pricing", "in": ["Milestones"]}]}
  ]
}
// Other SOW-form attribute additions (same file, same form):
//   sow_title, scope, cp_signer_name, cp_signer_email:  "no_blanks": true
//   value:                                              "min_exclusive": 0
```

Rules of the `rows` kind (one definition, two implementations, checked in this order; the server
raises the first, and the wizard lists all of them in the same order):

1. The answer must be a JSON array, or M1.
2. Each element must be an object, or M2 for the first one that is not. Row numbers are 1-based
   positions in the array as sent, **counting rows that are ignored**, so the wizard and the
   server number rows alike.
3. Each cell of a **shown** column is tidied: `null`/missing becomes `""`, anything else becomes
   `str(value)`. Each run of `[ \t\r\n\f\v]` becomes one space, then those characters are stripped
   from both ends. ASCII whitespace only, in both languages, so they agree. A column is shown when
   every rule in its `show` matches the form's answers, using the same `shown()` as questions.
   Cells of hidden columns and unknown keys are dropped.
4. A row whose shown cells are all `""` is ignored (FR-8).
5. Non-empty row count `< min_rows` gives M3. `> max_rows` gives M4.
6. For each non-empty row in order, and each shown column in column order:
   - empty: M5;
   - `text`: length in code points (`[...s].length` in TS) `> max_length` gives M6; with
     `no_blanks`, `first_blank(cell)` gives M7;
   - `date`: not `^\d{4}-\d{2}-\d{2}$` or not a real day gives M8;
   - `within: [a, b]`, when both answers are ISO days: `cell < values[a]` or `cell > values[b]`
     gives M9 (both ends included);
   - `percent`: not `^\d{1,3}(\.\d{1,2})?$`, or the value is not `> 0` and `<= 100`, gives M10. It
     is stored as `str(Decimal(cell).quantize(Decimal("0.01")))`, e.g. `"40.00"`.
7. `unique` columns: compare `cell.lower()` (`toLowerCase()` in TS) across non-empty rows. The
   first later row that repeats an earlier one gives M11, naming both rows.
8. `total` columns: the exact decimal sum `!= total` gives M12.
9. The stored value is the list of non-empty rows, shown-column keys only, in order.

Exact messages (the cases file pins them). `{L}` is the question label ("Deliverables"), `{C}`
the column label, `{n}` the row number:

| Id | Message |
|---|---|
| M1 | `"{L}" must be a table of rows, not free text.` |
| M2 | `"{L}", row {n} is not a row of the table.` |
| M3 | `"{L}" needs at least {min_rows} row.` (`rows` when min_rows > 1) |
| M4 | `"{L}" can have at most {max_rows} rows (this has {count}).` |
| M5 | `"{L}", row {n}: "{C}" is required.` |
| M6 | `"{L}", row {n}: "{C}" is longer than {max_length} characters.` |
| M7 | `"{L}", row {n}: "{C}" contains {bracket}, which reads as a blank to fill in later. Reword it without square brackets.` |
| M8 | `"{L}", row {n}: "{C}" must be a date in YYYY-MM-DD form.` |
| M9 | `"{L}", row {n}: "{C}" must be between {start} and {end} ({start label} to {end label}).`, with dates written "1 November 2026" |
| M10 | `"{L}", row {n}: "{C}" must be a number above 0 and at most 100, with no more than two decimal places.` |
| M11 | `"{L}", rows {i} and {j} have the same "{C}" ("{value of row i}"). Give each row its own.` |
| M12 | Under: `"{L}": the "{C}" column adds up to {total}%, {diff} short of 100%.` Over: `"{L}": the "{C}" column adds up to {total}%, {diff} over 100%.` Numbers are two-decimal values with trailing zeros removed: 99 → `99`, 99.5 → `99.5` |
| F1 | `Field "{label}" contains {bracket}, which reads as a blank to fill in later. Reword it without square brackets.` (`no_blanks` on a text/textarea/email question) |
| F2 | `Field "{label}" must be more than {min_exclusive}` (no full stop, as the neighbouring `must be a number`) |

Filing and update semantics in `_validate_field_values(fields, values, *, stored=None)`:
`stored=None` means a new filing. `update_request` passes `r.field_values`.

- **Main loop (unchanged order).** A `rows` question is only checked against `shown()` there
  (popped when not shown). Its rules run in a post-pass **after** `_check_dates_in_order`, so an
  end-before-start error is reported first and the window is known.
- **`no_blanks` and `min_exclusive`** run after `_coerce_field`. On an update they are skipped
  when the coerced value equals `stored[key]`: an answer filed before this feature is not refused
  by a rule added with it (FR-32, and the edge case "a filed request with a total value of zero").
- **Rows post-pass, for each shown `rows` question `f`:**
  - new filing with `f.legacy_key` present in the values: M1 (FR-16);
  - update, and `stored` has `f.legacy_key`: `cleaned[f.legacy_key] = stored[f.legacy_key]`. The
    payload can neither change nor remove it (FR-31). Update where `stored` lacks it but the
    payload sends it: M1;
  - answer missing (`None` / `""`):
    - on an update where `stored` has `f.legacy_key` and no `f.key`, it stays absent (an older
      request, rows not entered yet: FR-32);
    - otherwise required gives M3;
  - otherwise `cleaned[f.key] = clean_rows(f, raw, cleaned, labels)`.

```json
// POST /intake/requests — body (the wizard's shape; other answers as today)
{
  "type_label": "Statement of Work Request", "priority": "Medium", "department": "Procurement",
  "description": "Statement of Work", "subject": "Statement of Work — Acme Ltd",
  "field_values": {
    "request_form": "sow", "parent_contract_id": "c-1", "cp_signer_name": "Maya Chen",
    "cp_signer_email": "maya.chen@example.com", "department": "Procurement",
    "sow_title": "Data platform migration, phase 2", "services_start": "2026-11-01", "services_end": "2027-03-31",
    "value": "4500000", "currency": "INR", "pricing": "Milestones",
    "scope": "Migrate the data warehouse to the new platform.",
    "deliverable_rows": [
      {"name": "Migration plan", "due": "2026-11-30", "their_owner": "Priya Nair", "our_owner": "Ravi Shah", "share": "40"},
      {"name": "", "due": "", "their_owner": "", "our_owner": "", "share": ""},
      {"name": "Migrated warehouse", "due": "2027-02-15", "their_owner": "Priya Nair", "our_owner": "Ravi Shah", "share": "35"},
      {"name": "Handover pack", "due": "2027-03-31", "their_owner": "Tom Reyes", "our_owner": "Ravi Shah", "share": "25"}
    ],
    "personal_data": "No", "gxp": "No"
  }
}
// 201 RequestResponse — field_values as stored (empty row gone, cells tidied, shares 2 dp; with
// pricing "Fixed fee" or "Time and materials" every row has only the four keys)
{ "...": "...", "field_values": { "...": "...", "value": 4500000,
  "deliverable_rows": [
    {"name": "Migration plan", "due": "2026-11-30", "their_owner": "Priya Nair", "our_owner": "Ravi Shah", "share": "40.00"},
    {"name": "Migrated warehouse", "due": "2027-02-15", "their_owner": "Priya Nair", "our_owner": "Ravi Shah", "share": "35.00"},
    {"name": "Handover pack", "due": "2027-03-31", "their_owner": "Tom Reyes", "our_owner": "Ravi Shah", "share": "25.00"}
  ] } }
// 422 (FastAPI shape), e.g.
{"detail": "\"Deliverables\", row 4: \"Due date\" must be between 1 November 2026 and 1 February 2027 (Services start to Services end)."}

// PATCH /intake/requests/{id} — entering rows on an older request: the page sends every stored
// answer back with the rows added (field_values is replaced whole, as today)
{"field_values": {"...every stored answer...": "...", "deliverables": "Plan by Nov, report by Mar, Ravi signs off",
  "deliverable_rows": [{"name": "Migration plan", "due": "2026-11-30", "their_owner": "Priya Nair", "our_owner": "Ravi Shah"}]}}
// 200 RequestResponse: "deliverables" unchanged (carried from storage whatever was sent), "deliverable_rows" cleaned

// POST /intake/requests/{id}/draft-contract — held (FR-33): no contract, no pin, no audit row
// 409
{"detail": "<DRAFT_HELD>"}

// POST /workflows/runs/{id}/complete-step on a held draft step
// 409
{"detail": "<HELD_STEP_NOT_MANUAL>"}
// GET /workflows/runs/by-request/{id} — the held step (existing fields; values frozen here)
{"idx": 0, "type": "clm_draft", "status": "waiting_human", "note": "<DRAFT_HELD> This step carries on by itself once the rows are saved.",
 "result": {"held": "deliverable_rows"}, "...": "..."}
```

Frozen strings (in `app/intake/sow.py`):

- `DRAFT_HELD` = `Enter the deliverables as rows first. This Statement of Work request has its
  deliverables only as free text, from before the form asked for a table, and the draft is made
  from the rows (name, due date, their owner and our owner, and a share of the fee when pricing is
  Milestones). The earlier text stays on the request to copy from.`
- `HELD_STEP_NOTE` = `DRAFT_HELD + " This step carries on by itself once the rows are saved."`
- `HELD_STEP_NOT_MANUAL` = `This step makes the Statement of Work draft from the request's
  deliverable rows, so it can't be marked done by hand. Enter the rows on the request and the step
  carries on by itself.`

Audit actions written (all in the request's own transaction, before its commit; never on a second
session, CLAUDE.md audit-lock):

| Where | Action | Change |
|---|---|---|
| `create_request` | `intake.created` (existing) | For `request_form == "sow"`, `after` also holds `deliverable_rows` (as stored), `pricing`, `services_start`, `services_end`, `value`, `currency` (FR-38, Audit trail) |
| `update_request` | **`intake.deliverables_changed`** (new) + timeline `intake.deliverables_changed` on the request | Written when `stored.get("deliverable_rows") != cleaned.get("deliverable_rows")`, in either branch of `update_request`. `before`/`after` = `{"deliverable_rows": <list or null>, "pricing": <str or null>}`. Timeline title: `Deliverables entered as rows` when `before` had no rows, else `Deliverables changed`; `details` = `{"before": ..., "after": ...}`. Covers shares removed or required by a pricing change (FR-43) |
| `draft_contract_for_request` | `intake.contract_drafted` (existing) + contract timeline `contract.drafted_from_template` (existing) + contract `metadata_json.drafted_from_template` | For `doc_type == "sow"` the 006 record gains `deliverable_rows: int` (rows on the request) and `pricing: str \| null` (FR-37) |
| Held draft, refused filing | none | They change nothing (spec, Audit trail) |

### Database schema

**No table, column or index changes. No migration.** `alembic heads` stays `0074_template_sync`.

The only new persisted shape is inside the existing JSON column `intake_request.field_values` (org
scoped by the row's `org_id`, as today) and, unvalidated, in `intake_draft.values`:

| Key in `field_values` | Type | When present |
|---|---|---|
| `deliverable_rows` | array (1–20) of objects `{"name": str ≤200, "due": "YYYY-MM-DD", "their_owner": str ≤120, "our_owner": str ≤120, "share"?: "NN.NN"}` | Every SOW request filed after this feature; an older request once rows are entered. `share` only while `pricing == "Milestones"` |
| `deliverables` | str | Older SOW requests only; never written by new code; carried unchanged by every update |

### Frontend TypeScript models (added to src/lib/types.ts)

```ts
// Mirrors agreement_forms.json exactly.
export interface RequestFormRule { field: string; in: string[] }  // unchanged
export interface RequestFormColumn {
  key: string;
  label: string;
  kind: "text" | "date" | "percent";
  max_length?: number;
  unique?: boolean;
  no_blanks?: boolean;
  /** Date columns: the two date questions of the same form the date must fall between, both included. */
  within?: [string, string];
  /** Percent columns: what the column must add up to across the rows. */
  total?: number;
  /** The column applies only when every rule matches the form's answers (same shape as a question's `show`). */
  show?: RequestFormRule[];
}
export interface RequestFormField {
  key: string; label: string;
  kind: "text" | "email" | "textarea" | "select" | "multiselect" | "date" | "number" | "rows";
  required: boolean; options?: string[]; show?: RequestFormRule[];
  /** A date that must not come before the date answered under this key (an end date against its start date). */
  not_before?: string;
  /** Text answers: refused when they hold a blank bracket such as "[TBD]". */
  no_blanks?: boolean;
  /** Number answers: must be more than this. */
  min_exclusive?: number;
  /** `rows` questions only. */
  columns?: RequestFormColumn[];
  min_rows?: number;
  max_rows?: number;
  /** `rows` questions: the key older requests kept a free-text answer under, and that question's label. */
  legacy_key?: string;
  legacy_label?: string;
}
export interface RequestFormDef { key: string; name: string; fields: RequestFormField[] }  // unchanged
/** One row of a table question, by column key. Every cell is text (a stored share reads "40.00"). */
export type FormRow = Record<string, string>;
/** Any answer the wizard holds. */
export type FormValue = string | string[] | FormRow[];
export interface IntakeDraft {
  id: ID; form_key: string; title: string | null; values: Record<string, FormValue>;  // was string | string[]
  parent_contract_id: ID | null; page_index: number; visited: number;
  created_at: string; updated_at: string;
}
// IntakeRequest.field_values stays Record<string, unknown>; WorkflowRunStep.result stays
// Record<string, unknown> | null — a held draft step is `result?.held === "deliverable_rows"`.
```

`lib/form-checks.ts` exports (frozen, since T006 and the tests use them):

```ts
export interface RowsProblem { row: number | null; column: string | null; message: string }  // message = server text
export function findBlanks(text: string): string[];            // port of app/contracts/blanks.find_blanks
export function tidyCell(raw: unknown): string;
export function longDate(iso: string): string;                 // "2026-11-01" → "1 November 2026"
export function rowsProblems(field: RequestFormField, raw: unknown,
  values: Record<string, unknown>, labels: Record<string, string>): RowsProblem[];
export function shareTotal(field: RequestFormField, rows: FormRow[]): string | null;  // "75" while a total column is shown, else null
export function pastDueRows(field: RequestFormField, rows: FormRow[], today: string): number[];  // FR-12, row numbers
export function answerProblem(kind: string | undefined, value: FormValue | undefined,
  notBefore?: { label: string; value: FormValue | undefined },
  rules?: { no_blanks?: boolean; min_exclusive?: number }): string | null;  // 4th param new, optional
```

`findBlanks` must match Python's regex semantics, so it uses the `u` flag and Unicode classes:
- Python `\w` becomes `[\p{L}\p{N}_]`, `\W` becomes `[^\p{L}\p{N}_]`, and `\d` becomes `\p{Nd}`;
- the article rule's `\b` becomes `(?![\p{L}\p{N}_])`;
- the 1–120 inside length counts code points.

The cases file includes the non-ASCII cases that would otherwise drift (`[é]` is a blank,
`[the Company]` is not).

### Endpoint client additions (src/lib/endpoints.ts)

None. `intakeApi.update(id, { field_values })` and `workflowsApi.refreshRun(runId)` already exist
and take what this feature sends.

### The new fill-in fields (`PLACEHOLDERS` in `drafting_templates/service.py`)

Appended after `survival_clause`, in this order. The `sample` of each is
`sow.EXAMPLE_VALUES[name]`, computed by the same code that fills a real request, so the preview,
the check and the field list show one consistent example (FR-23, FR-25). Labels are unique
against the 13 existing ones (`test_every_placeholder_has_its_own_plain_name`).

| name | label | about |
|---|---|---|
| `sow_title` | SoW title | Statement of Work: the SoW title from the request |
| `services` | Services | Statement of Work: the scope of services as the requester wrote it |
| `deliverables_list` | Deliverables list | Statement of Work: one entry per deliverable row (D1, D2, …) with its name, due date and both owners |
| `key_personnel_list` | Key personnel list | Statement of Work: each Provider owner once, with the deliverables they own |
| `acceptance_list` | Acceptance criteria list | Statement of Work: one entry per deliverable (D1, D2, …) the acceptance criteria apply to |
| `pricing_model` | Pricing model | Statement of Work: the pricing statement for the request's pricing answer |
| `fee_amount` | Fee amount | Statement of Work: the total value with its currency (the fixed fee, or the not-to-exceed amount) |
| `milestone_payments_list` | Milestone payments list | Statement of Work: one payment per deliverable, each payable on written acceptance; for time and materials, that none apply |
| `provider_representative` | Provider representative | Statement of Work: their signer's name and email, as authorised signatory |

The existing `value_sentence` sample changes to
` The total value of this Agreement shall not exceed INR 1,200,000.00.` so the preview's amount
matches the example fee. Only the example changes; no draft does.

### What each field fills (`app/intake/sow.py`, frozen formats)

`sow_values(fv: dict, *, example: bool = False) -> dict[str, str]` returns all nine keys.
`example=True` returns `EXAMPLE_VALUES`, ignoring `fv`. Otherwise every value comes from `fv`,
and a missing answer gives `""`. "A field fills with nothing" is how a non-SOW request or a
request without rows behaves. A real draft never gets example content (FR-24).

The building blocks:
- `rows = fv["deliverable_rows"]` (dicts only), numbered D1… in stored order.
- `cur = fv.currency or settings.default_currency`.
- `T = Decimal(str(fv.value).replace(",", "").strip()).quantize(0.01, ROUND_HALF_UP)`, or `None`
  when it doesn't parse.
- `money(x) = f"{cur} {x:,.2f}"`.
- `day(iso) = f"{d.day} {d:%B %Y}"`.
- `_s(text)` adds a full stop unless the text already ends with `.`, `!` or `?`.

Entries in a list are joined by `"\n\n"`, so a Phase 3 Word fill can split them into paragraphs.

| Field | Value |
|---|---|
| `sow_title` | `" ".join(str(fv.sow_title).split())` |
| `services` | `str(fv.scope).replace("\r\n", "\n").strip()`, line breaks kept |
| `deliverables_list` | per row: `D{i}. {_s(name)} Due date: {day(due)}. Provider owner: {_s(their_owner)} Customer owner: {_s(our_owner)}` |
| `key_personnel_list` | Group by `their_owner.lower()`, in order of first appearance, showing the first-seen spelling: `{owner} - owner of {Ds} - delivery of {that Deliverable \| those Deliverables}`. `{Ds}` reads `D1`, `D1 and D2`, or `D1, D2 and D3` |
| `acceptance_list` | per row: `D{i}: {_s(name)}` |
| `pricing_model` | See below; `""` for any other answer |
| `fee_amount` | `money(T)`, or `""` when `T is None` (a legacy `0` gives `INR 0.00`) |
| `milestone_payments_list` | "Fixed fee": `split(T, n)` equal; "Milestones": `split(T, shares)`; per row `M{i}: Acceptance of D{i} ({name}). Payment: {amount}, payable only after the Customer has accepted D{i} in writing.`, where `{amount}` = `money(p)` (Fixed fee) or `{share}% of the fixed fee: {money(p)}` (Milestones; share with trailing zeros removed: `40`, `33.33`, `12.5`). "Time and materials": `TM_NO_MILESTONES`. `""` when there are no rows or no `T` |
| `provider_representative` | `{name} ({email}), authorised signatory`; `{name}, authorised signatory` without an email; `""` without a name |

`pricing_model` values:
- "Fixed fee": `Fixed fee. The Fees for this SOW are the fixed fee stated in Clause 8.2.`
- "Time and materials": `Time and materials. The Fees for this SOW are charged on a
  time-and-materials basis under Clause 8.3 and may not exceed the not-to-exceed amount stated
  there.`
- "Milestones": `Fixed fee payable in instalments. The Fees for this SOW are the fixed fee stated
  in Clause 8.2, payable in the instalments set out in Clause 9.1, each only after the Customer
  has accepted in writing the Deliverable it is linked to.`

`TM_NO_MILESTONES` = `No fixed milestone payments apply, because this SOW is priced on a
time-and-materials basis. Payment follows Clause 9.2, including its hold-back until the Customer
has accepted the Deliverables in writing.`

**Money (FR-21, FR-44).** `split_payments(total: Decimal, n: int, shares: list[Decimal] | None) -> list[Decimal]`:
- Every amount is an exact `Decimal`. Floats are never used.
- `T` is the total rounded half-up to 2 dp. This is the only way payments can "add up exactly"
  when a total has more than 2 decimals.
- Payment `i < n` is `(T / n)` (equal) or `(T * share_i / 100)` (Milestones), each `.quantize(
  Decimal("0.01"), ROUND_HALF_UP)`.
- The last payment is `T - sum(first n-1)`, so the rounding difference is added to it or taken
  from it.

Examples, pinned by tests:

| Pricing | T | Shares | Payments |
|---|---|---|---|
| Fixed fee | 100.00 | — (3 rows) | 33.33, 33.33, 33.34 |
| Fixed fee | 4,500,000.00 | — (3 rows) | 1,500,000.00 ×3 (AC-46) |
| Fixed fee | any | — (1 row) | T |
| Fixed fee | 100.005 → 100.01 | — (3 rows) | 33.34, 33.34, 33.33 |
| Milestones | 4,500,000.00 | 40, 35, 25 | 1,800,000.00, 1,575,000.00, 1,125,000.00 (AC-47) |
| Milestones | 100.01 | 33.33, 33.33, 33.34 | 33.33, 33.33, 33.35 (AC-47) |
| Milestones | any | 100 (1 row) | T |

The currency is the request's `currency` answer: one of the form's four, `INR` by default.
Grouping is `,` with `.` as the decimal point, matching `value_sentence` and FR-6
("INR 4,500,000.00").

**The example reading (FR-23).** `EXAMPLE_ANSWERS` is a complete request, distinct from every AC
fixture (names, deliverables and amount), so AC-27's search is meaningful.
`EXAMPLE_VALUES = sow_values(EXAMPLE_ANSWERS)`.
- Title "Customer portal redesign".
- Scope "Redesign and rebuild the customer self-service portal, including user research, visual
  design, build, testing and launch support."
- Window 2026-10-01 to 2027-09-30. Both the preview's sample term and the check's "from 1 October
  2026, until terminated" contain it.
- INR 1,200,000, pricing "Milestones", signer Jordan Lee, jordan.lee@globex.example.
- Rows:
  - "Current-state assessment", due 2026-12-15, Anika Rao / Meera Iyer, 25;
  - "Pilot release", due 2027-03-31, Anika Rao / Meera Iyer, 50;
  - "Launch and handover pack", due 2027-06-30, Daniel Okafor / Meera Iyer, 25.

So the payments are 300,000.00 / 600,000.00 / 300,000.00, and one key person owns two
deliverables. `EXAMPLE_MARKERS` holds these strings: the three deliverable names, the four person
names, the email, the title, the scope sentence, `1,200,000.00`, `600,000.00` and `300,000.00`.

Other exports: `FIELDS` (the nine names), `ROW_FIELDS = ("deliverables_list",
"key_personnel_list", "acceptance_list", "milestone_payments_list")`, `rows_of(fv) -> list[dict]`,
and the three frozen strings above.

### The new `sow.txt` wording, clause by clause (Fill map)

The implementation task writes the sentences. They must:
- contain no `[...]` and no "delete as appropriate";
- not contain the SOW playbook's prohibited phrase "deemed accepted";
- keep `{company}` and `{counterparty}`;
- pass `check_body`.

Everything else in the file is unchanged: preamble, 1, 2, 3, 4.3 lead-in, 5.2–5.4, 6.2–6.4, 7,
8.4, 8.5 with `{value_sentence}`, 9.1 lead-in, 9.2, 9.3, 11, 12 and the signature block.

| # | Clause | Takes | Wording (in substance) and legal effect |
|---|---|---|---|
| 1 | 4.1 Background and objectives | standard + `{sow_title}` | "This SOW is issued under the Master Agreement for the work titled "{sow_title}". The Customer expects the Services and Deliverables in this SOW to be performed, delivered and accepted as this SOW provides." Identifies the work; adds no obligation beyond the SOW's own terms |
| 2 | 4.2 Services | `{services}` on its own paragraph after the existing lead-in | The Provider's performance obligation is the requester's scope, verbatim |
| 3 | 4.3 Deliverables | `{deliverables_list}` after the existing lead-in (replaces D1–D3) | Each Deliverable is named, dated and owned (playbook rule `scope_and_deliverables`) |
| 4 | 4.4 Out of scope | standard: keep the two existing sentences; replace the bracket sentence with "No further exclusions have been agreed at the SOW Effective Date." | Closed scope. Only the listed Services and Deliverables are in scope. Anything else needs a signed Change Order (7.1 defines it as signed by both) |
| 5 | 4.5 Customer dependencies | standard: "No Customer dependency is listed at the SOW Effective Date. The Provider may rely on a failure by the Customer to provide information, access, facilities or decisions to excuse a delay only where that dependency is recorded in a signed Change Order." Then keep the existing remedy sentence, worded "a dependency so recorded" | The Provider cannot excuse a delay by an unlisted dependency. Relief comes only through a signed Change Order |
| 6 | 4.6 Location and working hours | standard: "The Provider shall perform the Services at its own premises or remotely, during its normal business hours. It shall perform Services at the Customer's premises only where the Customer asks in writing, and shall then comply with the Customer's site rules." | Default off-site. On-site only at the Customer's written request, under its site rules |
| 7 | 5.1 Key personnel | `{key_personnel_list}` after the existing lead-in (replaces the two lines) | Binds each named Provider owner as Key Personnel under the unchanged 5.2 (no removal or replacement without the Customer's prior written consent) |
| 8 | 6.1 Acceptance | standard + `{acceptance_list}`: "The Customer shall test each Deliverable against its acceptance criteria within ten (10) working days of receiving it. The acceptance criteria of each Deliverable listed below are that it conforms in all material respects to its description in Clause 4.3 and to any specification the Parties agree in writing before its due date:" + list | The criteria are visible and editable here (FR-4, AC-7); the list only names the Deliverables they apply to. Acceptance stays written-only under the unchanged 6.2 |
| 9 | 8.1 Pricing model | `8.1 Pricing model: {pricing_model}` | One stated model, never alternatives (FR-19) |
| 10 | 8.2 Fixed fee | `{fee_amount}`; the opening becomes "Where this SOW is priced as a fixed fee (including a fixed fee payable in instalments), …" | Fixed price. Applies to Fixed fee and Milestones |
| 11 | 8.3 Time and materials | `{fee_amount}` as the not-to-exceed amount | A cap on T&M (A-8). Opens "Where this SOW is priced on a time-and-materials basis", as today (FR-20) |
| 12 | 9.1 Milestones | `{milestone_payments_list}` after the existing lead-in (replaces M1–M3) | Every payment is conditional on written acceptance, never on a date (FR-22) |
| 13 | 10.1 Reporting | standard: "… report progress … to the Customer owners named in Clause 4.3 weekly, …" | Weekly reporting (A-12). An edit to "fortnightly" flows to the next draft (AC-7) |
| 14 | 10.2 Representatives | standard + `{provider_representative}`: "Customer: the Customer owner named for each Deliverable in Clause 4.3 is the Customer's representative for that Deliverable, and the Customer may notify the Provider in writing of the name, role and email of an overall representative. Provider: {provider_representative}." | Names a representative on each side without a blank |

## Component design

### Backend
- **Models:** none.
- **Schemas:** none. `RequestCreate.field_values` / `RequestUpdate.field_values` / `DraftSave.values`
  are already `dict`.
- **`app/intake/agreement_forms.py`** (T002):
  - `form_fields` adds `no_blanks`, `min_exclusive`, `min_rows`, `max_rows`, `legacy_key`,
    `legacy_label` and `columns` (a list of `SimpleNamespace(key, label, kind, max_length=None,
    unique=False, no_blanks=False, within=None, total=None, show=None)`) via `.get()`.
  - `clean_rows(field, raw, values, labels) -> list[dict]` implements the rules above and raises
    `HTTPException(422, Mx)`.
  - `answer_text(form_key, key, value) -> str` renders a rows answer as
    `"\n    {n}. {Col label}: {cell}; …"` with the form's column labels, and anything else as
    `str(value)`. Pure, no DB.
- **`app/intake/service.py`** (T002):
  - `_validate_field_values(fields, values, *, stored=None)` as frozen above. It imports
    `first_blank` from `app.contracts.blanks`.
  - `create_request`: the `intake.created` `after` gains the SOW facts.
  - `update_request` passes `stored=r.field_values` (read **before** reassigning) and writes
    `intake.deliverables_changed` and its timeline event when the rows differ.
  - Scoping is unchanged: `get_request` filters by org and access.
- **`app/intake/triage_agent.py` / `flow_agent.py`** (T002): the `field_txt` comprehension calls
  `answer_text(fv.get("request_form"), k, v)`, so the advisory read and triage see rows, not a
  Python repr (FR-18).
- **`app/intake/sow.py`** (T003, new): as frozen. No DB, no audit.
- **`app/intake/drafting.py`** (T003):
  - `template_values(..., example: bool = False)` returns `{**today's 13 keys, **sow.sow_values(f,
    example=example)}` for every `doc_type`, so `test_the_placeholder_list_is_exactly_what_drafting_fills`
    keeps holding. `render_document(..., example: bool = False)` passes it through. Other
    templates' text is byte-identical, because `str.format` ignores unused keys (FR-36).
  - `class DraftHeld(HTTPException)`: `__init__` calls `super().__init__(409, sow.DRAFT_HELD)`.
  - `draft_hold(db, *, org_id, request) -> str | None`. It returns `None` unless all of these hold:
    - `resolve_doc_type(request) == "sow"`;
    - `sow.rows_of(request.field_values)` is empty;
    - `{f["name"] for f in field_uses(current_wording(db, org_id, get_type(db, org_id, "sow")).body)} &
      set(sow.ROW_FIELDS)` is not empty. This is a pure read: it never pins.

    When all three hold, it returns `sow.DRAFT_HELD` (A-18).
  - `draft_contract_for_request`, in this order:
    1. the existing-contract return;
    2. `resolve_doc_type`;
    3. **then, when not `custom`, `if draft_hold(...): raise DraftHeld()`**. This is before
       `_template_at_this_moment`, so nothing is pinned, rendered, stored, recorded or audited;
    4. after `record` is built and when `doc_type == "sow"`: `record["deliverable_rows"] =
       len(rows)` and `record["pricing"] = fv.get("pricing")`. `_record_drafted_from_template`
       copies both into the timeline `details`, and `template_audit` copies both into the audit
       `after` (FR-37).
- **`app/drafting_templates/service.py`** (T003): the 9 PLACEHOLDERS (samples from
  `sow.EXAMPLE_VALUES`) and the `value_sentence` sample. Nothing else changes, so save / reset /
  restore / preview / check / field uses work for the new fields as for any other (FR-25, FR-26).
- **`app/drafting_templates/checks.py`** (T003):
  - `check_values` and `_review` call with `example=True`. The check reads the example SOW
    (A-13). Every other template reads exactly as before, since they use no SOW field.
  - `_CODED` += the 9 names.
  - In `_enrich`, after `template_form_of`, a fix whose template form still contains any
    `sow.EXAMPLE_MARKERS` gets `suggestion_is_wording = False`, so Apply can never write example
    content into our paper (FR-24).
- **`app/workflows/service.py`** (T005):
  1. In `_execute_step`'s clm_draft template path:
     `try: contract = await _draft_contract(...)`, then
     `except DraftHeld: sr.status = "waiting_human"; sr.note = sow.HELD_STEP_NOTE;
     sr.result = {"held": "deliverable_rows"}; _assign_step(db, run=run, sr=sr, cfg=cfg);
     return "wait"`. The step is not skipped, not done, and no contract is made.
  2. In `_resolve_waiting_group`, a `clm_draft` step that is `waiting_human` with
     `result.held` and whose request now has `draft_hold(...) is None` is set back to
     `status="pending", note=None, result=None`. The group then returns `"rerun"`, and
     `refresh_run` drafts. The `resume_workflow_runs` beat does the same with no one present
     (FR-45).
  3. `complete_human_step` raises `HTTPException(409, sow.HELD_STEP_NOT_MANUAL)` when the chosen
     step has `result.held`.
  Org scope: the run is already loaded through `_authorize_run`.
- **`backend/eval/exam/build.py`** (T003): `_render.draft("sow")` passes
  `body=(Path(__file__).with_name("sow_2026-10-15.txt").read_text(encoding="utf-8"))`. The
  exam's frozen documents stay byte-identical.
- **Celery:** none new. The existing `resume_workflow_runs` beat continues held steps.
- **Settings:** none.

### Frontend (Next.js page/component tree)
- `src/app/(app)/intake/_agreement-forms.tsx` (T004):
  - SOW step "Scope and fees" lists `deliverable_rows` in place of `deliverables`. `UI.deliverables`
    is removed and `UI.deliverable_rows = { uses: "The Deliverables, Key personnel, Acceptance and
    Milestone payments clauses of the Statement of Work, one entry per row in this order (D1, D2, …)." }`
    is added.
  - `Value = FormValue`.
  - `Question` renders a `rows` field with `RowsTable`. When the form is loaded with no rows yet,
    it starts with one empty row.
  - `problemOf` for `rows` is `rowsProblems(...)[0]?.message`. The banner lists every rows message
    as-is, not prefixed by the label.
  - `answerProblem` gets `{no_blanks, min_exclusive}` from the server field.
  - The review step shows the rows as a read-only `RowsTable`, never through `v.join`.
  - `draftFor("sow")` reads `"Statement of Work template"` (FR-18).
  - The filed `body` drops `values[legacy_key]` (a reopened saved form must not send the old text,
    M1).
  - A saved form holding `values.deliverables` shows it read-only beside the table, labelled
    `"Your earlier free-text answer (kept for reference, not filed)"` (FR-34).
  - When `/intake/forms` fails, the wizard shows `<LoadError what="the form" onRetry={refetch}
    onBack={onBack} />` instead of "This form isn't available." Nothing else renders while loading
    (FR-17, AC-20).
- `src/app/(app)/intake/_deliverables.tsx` (T004, new):
  - `RowsTable({ field, rows, values, labels, onChange?, problems?, today? })`:
    - `Table`/`THead`/`TH scope="col"` headed by the shown columns. Cells are `Input`s with
      `aria-label="Row {n}, {Column label}"`, `aria-invalid` and `aria-describedby` pointing at
      their error.
    - A share cell uses `inputMode="decimal"`, a due cell is `type="date"` with `min`/`max` set
      to the window.
    - "Add a deliverable" is a `Button`, disabled at `max_rows` with visible text "A table holds
      at most 20 deliverables."
    - "Remove row {n}" is a `Button` per row, disabled when only one row is left.
    - Per-cell errors (`problems` with that row and column) appear under the cell. Table-level
      ones (`row === null`) appear under the table.
    - The past-date note "Row {n}: this due date has already passed." comes from `pastDueRows`,
      in `slate`/`warning` text, non-blocking (FR-12).
    - A running total under the Share column reads "Total {t}% of 100%" (`shareTotal`), only
      while the column is shown (FR-42).
    - Hidden columns' cells stay in `rows`, so a pricing change loses nothing (FR-43).
    - Without `onChange` it renders read-only, as text cells, with dates via `fmtDate` and shares
      with trailing zeros removed.
  - `RequestDeliverables({ request, canEdit, heldNote, heldRunId, onSaved })`:
    - Finds the form's `rows` question via `useRequestForms()`. Renders nothing when the request's
      form has none.
    - Stored rows render as a read-only `RowsTable`.
    - Legacy text renders under "Earlier free-text answer" (`whitespace-pre-line`, selectable).
    - `heldNote` renders as a `MessageBar intent="warning"`.
    - When `canEdit` and there are no stored rows, it shows an editable `RowsTable` beside the old
      text, with a "Save deliverables" `Button`. The button checks `rowsProblems` first, then calls
      `intakeApi.update(request.id, { field_values: { ...request.field_values, deliverable_rows: rows } })`.
      Rows are kept on failure, and the server message (which names the row) goes to a toast.
    - Then, when `heldRunId` is set, it calls `workflowsApi.refreshRun(heldRunId)`, then
      `onSaved(updated)`, and invalidates `["flow-run", request.id]`.
- `src/app/(app)/intake/_request-overview.tsx` (T006):
  - Defines `held = current?.type === "clm_draft" && current.status === "waiting_human" &&
    (current.result as {held?: string} | null)?.held === "deliverable_rows"`.
  - `atReviewGate` excludes `held`, so there is no "Approve & advance" for a held draft.
  - Mounts `<RequestDeliverables request={r} canEdit={canAct && !r.contract_id}
    heldNote={held ? current.note : null} heldRunId={held ? flowRun.id : null}
    onSaved={done("Deliverables saved")} />` in "The request" card, after the description.
  - The Draft button's 409 already reaches the toast through `fail`. Approvers open this same
    page from Approvals, so FR-18's approvers' view is this block.
- **New shared primitives in `src/components/ui.tsx`:** none. Imported and confirmed exported:
  `Table`, `THead`, `TH`, `TR`, `TD`, `Input`, `Button`, `MessageBar`, `LoadError`; icons from
  `lucide-react` (`Plus`, `Trash2`). Theming uses `slate-*` and semantic tokens only, never
  `bg-white`.
- **Demo-mode behaviour:** not applicable (there is no demo mode in this checkout).

## Test strategy

All backend tests run with `MOCK_*` forced on by `conftest.py`. No paid call.

- **`backend/tests/fixtures/deliverable_rows_cases.json`** (T001), the shared parity file.
  - `{"rows": [{"id", "values", "rows", "error": <exact message or null>, "stored": <list, when
    error is null>}], "blanks": [{"text", "blanks": [...]}]}`.
  - At least one case per message M1–M12, plus: the all-empty row ignored; numbering counting an
    ignored row; 20 accepted / 21 refused; the window ends included; 33.333 / 0 / -5 / 101 /
    "forty" refused; 12.5 + 37.5 + 50 accepted; a share of 100 on one row accepted and 50
    refused; "priya  nair" tidied; a cell with a line break; a hidden share dropped; non-ASCII
    blanks.
- **`backend/tests/test_sow_deliverable_rows.py`** (T002, no DB, via `_validate_field_values` and
  `clean_rows`):
  - Every parity case.
  - The order of checks.
  - FR-14 F1 on title, scope and signer, with "(phase 1)" accepted.
  - FR-15 F2 on 0 and -5, with 1 accepted.
  - M1 for a free-text `deliverables` on a new filing and for a string `deliverable_rows`.
  - FR-41/43 shares stripped for non-Milestones and required for Milestones.
  - FR-11 window re-checked when `services_end` moves (AC-14).
  - The stored-aware rules:
    - an older request with only `deliverables` passes an update without rows (FR-32);
    - its text is carried over even when the payload changes or omits it (FR-31);
    - an unchanged `value: 0` and an unchanged bracketed scope are not re-refused, and a changed
      one is;
    - setting rows on it runs every rule (AC-35).
  - `answer_text` renders rows readably.
- **`backend/tests/test_sow_request_updates.py`** (T002, DB, rolled back):
  - Create: the `intake.created` audit holds rows, pricing, dates, value and currency.
  - Update 3 rows → 2: `intake.deliverables_changed` audit + timeline with before/after (AC-41).
  - Milestones → Time and materials: shares removed and audited (AC-46). Fixed fee → Milestones
    without shares: 422 naming the row; with 40/35/25: accepted.
  - Older request: a priority-only update and an unrelated-answer update succeed (AC-35); rows
    entered → audited "Deliverables entered as rows"; the stored free text is identical afterwards
    (AC-34).
  - Owner equal to a user's name: no Notification, grant or user created (AC-16).
  - Another org's request → 404 (AC-43).
  - Failure paths: 422 bodies equal the parity messages through create and through update (AC-19).
- **`backend/tests/test_sow_drafting.py`** (T003, DB pattern and helpers from
  `test_template_drafting_record`):
  - AC-1: the bracket occurrences in `default_body("sow")` = 0, and `"delete as appropriate"`
    absent.
  - AC-2: drafts of the AC example request under each pricing have no `find_blanks`, no `{`/`}`,
    and no "delete as appropriate".
  - AC-3–AC-6 and AC-9: exact Services, Background, D1–D3 lines, key personnel (incl. "priya  nair"
    → one entry), acceptance entries, standard wording, representatives, "30 November 2026",
    "INR 4,500,000.00".
  - AC-22/23: pricing statements and the 8.2/8.3 amounts.
  - AC-24/25/46/47: payment amounts via `split_payments` and in the text, every payment "payable
    only after … in writing", sums equal to T.
  - AC-8: none of `"deemed accepted"`, `"by use"`, `"on the date"`, and so on, in the filled
    places.
  - AC-27: no `EXAMPLE_MARKERS` in a real draft; a one-row request has exactly D1.
  - AC-26/28: `render_document("sow", ..., fields={}, example=True)` has no blanks or braces, a
    payment per Deliverable summing to the example total, and all example dates inside the term.
    `render_sample(default_body("sow"))` likewise.
  - AC-7: saving SOW wording with "fortnightly" → the next draft says fortnightly.
  - AC-28: a saved wording using a new field passes `check_body`; `{deliverable_table}` is refused.
  - AC-30: a completed check whose `wording_sha256` is not the new shipped hash → `template_status`
    Out of date, wording changed, and no `JobRun` or AI row created by reading. Never checked stays
    never checked. An org wording with brackets and no new field drafts with its brackets and no
    hold; reset gives the new wording as a new version.
  - AC-36/AC-48 hold:
    - an older request + shipped wording → `DraftHeld` 409, and no Contract,
      `DraftingTemplateType` pin, audit row or stored file;
    - then rows entered → the draft holds exactly those rows;
    - an org wording without the row fields → drafts with no hold.
  - AC-40: the FR-37 keys in the timeline, the audit and the metadata.
  - AC-39: for each non-SOW key, bare and full renders equal what they render with the 9 SOW keys
    removed from `template_values`. `find_blanks` is empty for nda/msa/saas/vendor, and
    consultancy/dpa still have theirs (the known exceptions).
  - AC-38: a free-text "please draft a SOW" still resolves to `msa`.
  - Apply guard: a finding whose fix quotes "Pilot release" has `suggestion_is_wording` false.
- **`backend/tests/test_template_check.py`, `test_template_check_jobs.py`** (T003): `_bare_draft`
  passes `example=True`. The existing assertions keep proving the check sends exactly what is
  drafted.
- **`backend/tests/test_intake_form_values_make_sense.py`** (T002): `complete()` gives a `rows`
  question one valid row inside the window.
- **`backend/tests/test_workflow_engine_consistency.py`** (T005, fake DB as in the file):
  - `_draft_contract` raising `DraftHeld` → outcome `"wait"`, the step `waiting_human` with
    `HELD_STEP_NOTE` and `result.held`, and no contract on the run;
  - `complete_human_step` on it → 409 `HELD_STEP_NOT_MANUAL`;
  - `_resolve_waiting_group` with rows present → the step `pending` and `"rerun"`; without rows →
    `"wait"`. `drafting.draft_hold` is monkeypatched, because the fake DB has no template rows;
  - `refresh_run` then drafts (AC-48).
- **`frontend/src/lib/form-checks.test.ts`** (T004):
  - Every parity case: `rowsProblems(...)[0]?.message ?? null === error`, and the stored-shape
    emptiness agrees.
  - Every `blanks` case for `findBlanks`.
  - `shareTotal` ("99", "100", "12.5").
  - `pastDueRows`.
  - `answerProblem` with `no_blanks` / `min_exclusive`.
- **`frontend/src/app/(app)/intake/agreement-forms.test.ts`** (T004): `deliverable_rows` is placed
  and says what it does. The SOW table declares the five columns, the share column shows only for
  Milestones, and `within` names two date questions of the same form.
- **`frontend/src/app/(app)/intake/_agreement-wizard.test.tsx`** (T004):
  - Update `toStep2` and the bug-48 flow to fill row 1 instead of the old textarea.
  - New cases:
    - table, not textarea (AC-10);
    - add and remove; the last row can't be removed; 20 rows disables add with the reason
      (AC-11);
    - a missing cell is refused beside the cell and in the banner, naming row and column
      (AC-12, AC-20);
    - a past date shows the note and still continues (AC-15);
    - the share column appears for Milestones, hides for Fixed fee, and 40/35/25 reappear
      (AC-44, AC-46);
    - the running total updates; 99 is refused with "1 short" (AC-45);
    - the review step shows rows and "Statement of Work template" (AC-21);
    - the filed body has `deliverable_rows` and no `deliverables`;
    - filing failure keeps every entry and shows the message;
    - loading shows no table; a load failure shows LoadError with Retry (AC-20);
    - a saved form with old text shows it, the table is empty, and submit is refused until rows
      are valid (AC-37).
- **`frontend/src/app/(app)/intake/_deliverables.test.tsx`** (T004, new):
  - cell labels "Row 1, Deliverable name", keyboard-reachable buttons, and errors linked by
    `aria-describedby` (AC-20);
  - read-only rendering with shares (AC-21);
  - `RequestDeliverables`: an older request shows the earlier text and an editor only when
    `canEdit`; save sends `{ ...field_values, deliverable_rows }` and calls `refreshRun` when
    held; a 422 keeps the rows.
- **`frontend/src/app/(app)/intake/_request-overview.test.tsx`** (T006): a SOW request shows its
  rows; a held clm_draft step shows the note and the editor and no "Approve & advance"; saving
  refreshes the run (AC-36, AC-48).

Acceptance verification (T007) maps each AC to its evidence:

| AC | Evidence |
|---|---|
| AC-1, 2, 3, 4, 5, 6, 8, 9, 22, 23, 24, 25, 27, 28, 39, 40, 47 | `test_sow_drafting.py` (named cases) |
| AC-7, 29, 30 | `test_sow_drafting.py` + 006's Templates tests unchanged; a live GET on the dev stack (SOW shows Out of date: its 2026-10-15 check covered the old wording); no check started (the `drafting_template_check` count is unchanged) |
| AC-10, 11, 12, 15, 17, 20, 44, 45, 46 | wizard + `form-checks` vitest; server half in `test_sow_deliverable_rows.py`; browser walk for keyboard/screen-reader |
| AC-13, 14, 16, 18, 19, 35, 41, 43 | `test_sow_deliverable_rows.py`, `test_sow_request_updates.py`, parity file in both suites |
| AC-21 | wizard + `_deliverables` + overview vitest; `answer_text` test for the advisory read |
| AC-26 | example-reading test + a live preview POST |
| AC-31 | live: one owner-approved check of the SOW after release (paid, the owner's call); record its actual findings; assert the text read has no blank |
| AC-32 | 006 page text unchanged (diff) + vitest of the check panel caveat |
| AC-33 | test: a pre-existing contract's snapshot text is unchanged after a template edit/reset (006 FR-45 test re-run) + no migration/data step exists |
| AC-34, 36, 37, 48 | hold tests (backend), workflow tests, overview + wizard vitest, browser walk |
| AC-38 | `resolve_doc_type` test |
| AC-42 | `git diff` shows no change to `app/core/rbac.py` or any `require_permission(...)`; 403 tests of 006/intake unchanged |

## Risks & decisions

- 2026-10-15: M12 wording corrected ('short of'), orchestrator, during /implement
- **No migration: rows live in `field_values` under a new key.** Rejected:
  - a `deliverable` table (more schema, a model, a migration, org-scoping queries, for 1–20
    rows that are only ever read with their request);
  - reusing the `deliverables` key (FR-31 needs the old text kept beside the rows).
- **The table's rules are declared in the JSON and mirrored in TS, pinned by one shared cases
  file.** This follows the `not_before` + `form-checks.ts` pattern the wizard already uses.
  Rejected: a server "check answers" endpoint (one implementation, but a new endpoint, async
  Continue, and no live banner).
- **Rows are validated after the date-order check.** The window needs valid dates, and the
  existing test `test_a_statement_of_work_cannot_end_before_it_starts` keeps its first error.
- **Rules added by this feature never refuse an answer an update leaves unchanged** (`no_blanks`,
  `min_exclusive`), and an older request may stay without rows on unrelated updates (FR-32). The
  ceiling: an older request whose own title or scope already holds a bracket can carry it into its
  draft (see Open question 1). The signature gate (`find_blanks`) still stops it.
- **The example reading is an explicit `example=True` flag.** Only `checks.check_values` and
  `checks._review` pass it; the preview gets the same values through `SAMPLE_VALUES`. Rejected:
  "`fields == {}` means example". An email request with no answers would then draft example
  content into any template that used a SOW field (FR-24).
- **The example uses its own names, deliverables and amount (INR 1,200,000, Milestones).** It is
  distinct from every AC fixture, so AC-27 is meaningful. The `value_sentence` sample is changed to
  match, so the preview is one consistent SOW (FR-23).
- **Acceptance criteria wording sits once in clause 6.1. The list names the Deliverables it
  applies to**, so the standard wording is visible and editable (FR-4, AC-7). Rejected:
  generating the criterion sentence per row in code (it would not be in the template).
- **Hold before anything is written.** `draft_hold` runs before `_template_at_this_moment`, so a
  held draft pins no type, writes no audit row and stores no file (FR-33). 409 is used, not 422:
  the request is valid, but its state does not allow drafting yet.
- **Held workflow step = `waiting_human` + `result.held`, re-armed by the shared resolver.** It
  cannot be completed by hand. Rejected: a new step status (frontend and engine-wide churn), and
  re-arming inside `update_request` (that would couple intake to the engine; the beat and the
  page's `refreshRun` already cover it).
- **Signer name and email also refuse blank brackets on the SOW form.** This goes beyond FR-14's
  list, because they reach the SOW (provider representative) and FR-2 says "whatever the request's
  answers".
- **Key personnel responsibility is singular/plural**: "delivery of that Deliverable" for one,
  "delivery of those Deliverables" for several. The spec's literal phrase is used for several.
- **Apply guard reuses `suggestion_is_wording: false`.** There is no API change. The page then
  calls the suggestion advice, which is slightly inaccurate (see Open question 4).
- **The exam keeps its frozen SOW.** `eval/exam` builds `ae-sow-01..04` and the long documents'
  Schedule 1 from the shipped SOW, and committed exam items hold char offsets into that text
  (`review.json` planted changes at 1030–7702 and 33512–49733). Changing `sow.txt` would break
  `test_exam_content.py` and invalidate the 005 baseline. `build.py` renders `sow` from a verbatim
  copy of today's wording. Rejected: rebuilding the exam (new offsets, baseline lost).
- **Ceiling (`ponytail:` in `split_payments`):** with a total under about n × 0.01 the last
  payment can round to 0.00 or below. The form refuses ≤ 0, and real SOWs are far above this, so
  there is no guard.
- **Risk:** `RequestFormField.kind` gains `"rows"`. Any exhaustive `switch` on it must handle it
  (`tsc` catches it).
- **Risk:** stored rows are trusted at drafting. Every write path validates them, but a malformed
  share written straight to the DB would raise in `sow_values`. That is acceptable, because no
  route can store one.

FR traceability:

| FR | Component |
|---|---|
| FR-1 | `sow.txt` (no bracket); `test_sow_drafting` AC-1 |
| FR-2 | `sow.txt` + `sow.sow_values` + F1/M7 on every requester text that reaches the SOW |
| FR-3 | `sow.txt` clause table rows 1–14 + `sow.sow_values` formats |
| FR-4 | standard wording in `sow.txt`; only the 9 varying values are fields |
| FR-5 | `sow.txt` 4.4/4.5/4.6/6.1 + payment-entry wording; 6.2/7.1/5.2/2.2 kept |
| FR-6 | `sow.day`, `sow.money`, D-numbers from one row order in all four lists |
| FR-7 | `agreement_forms.json` `deliverable_rows`; wizard `RowsTable` |
| FR-8 | `clean_rows` steps 2–5, 9 / `rowsProblems`; `RowsTable` add and remove |
| FR-9 | M5 on every shown column |
| FR-10 | `tidyCell`/tidy, M6, M11 |
| FR-11 | `within` M9, re-run on every update |
| FR-12 | `pastDueRows` note (wizard only, non-blocking) |
| FR-13 | owners are plain strings; nothing reads them except `sow_values`; AC-16 test |
| FR-14 | `no_blanks` (F1, M7) via `first_blank` / `findBlanks` |
| FR-15 | `min_exclusive: 0` (F2) |
| FR-16 | one validator for create/update/assistant/API + TS mirror + shared cases; M1 |
| FR-17 | `RowsTable` (a11y, add/remove, errors, totals) + wizard LoadError |
| FR-18 | review step rows + "Statement of Work template"; `RequestDeliverables`; `answer_text` in triage/flow prompts |
| FR-19 | `pricing_model` statements |
| FR-20 | `{fee_amount}` in 8.2 and 8.3; their "Where…" openings |
| FR-21 | `split_payments` equal; `TM_NO_MILESTONES` |
| FR-22 | per-payment "payable only after … in writing"; 9.1 lead-in |
| FR-23 | `sow.EXAMPLE_*`; `example=True` in checks; samples in PLACEHOLDERS |
| FR-24 | flag only in checks; `EXAMPLE_MARKERS` Apply guard; AC-27 test |
| FR-25 | 9 PLACEHOLDERS entries (`check_body`, field list, insert) |
| FR-26 | 006 service unchanged for SOW |
| FR-27 | live shipped-wording hash (`current_wording`); no data step; no check triggered |
| FR-28 | example reading blank-free test |
| FR-29 | 006 page text unchanged (verify) |
| FR-30 | no re-draft or migration; `on_created` record only for new drafts |
| FR-31 | legacy key carried over on update; `RequestDeliverables` "Earlier free-text answer" |
| FR-32 | stored-aware rows requirement; unchanged-answer exemption |
| FR-33 | `draft_hold` + `DraftHeld` 409 + `RequestDeliverables` editor |
| FR-34 | wizard legacy panel; body drops legacy key; submit gated by `rowsProblems` |
| FR-35 | `resolve_doc_type` untouched |
| FR-36 | other `defaults/*.txt` untouched; `str.format` ignores extra keys; AC-39 test |
| FR-37 | `record["deliverable_rows"]`, `record["pricing"]` |
| FR-38 | `intake.created` facts; `intake.deliverables_changed` audit + timeline |
| FR-39 | no `require_permission` change |
| FR-40 | rows inside `field_values` under `get_request`'s scope |
| FR-41 | share column `show` rule |
| FR-42 | `percent` M10, `total` M12, `shareTotal` |
| FR-43 | hidden-column strip (server); wizard keeps hidden cells; audited on update |
| FR-44 | `split_payments` with shares; Milestones entry format |
| FR-45 | workflow clm_draft catch + resolver re-arm + manual-complete refusal |

## Open questions

None blocks the design. Each has a default the plan already uses. Please confirm or change.

1. **An older request whose own title, scope or signer already holds a blank bracket.**
   (a) *Default:* the new rules don't refuse an unchanged old answer, so rows can be entered, and
   that pre-existing bracket can reach its draft. The signature gate still stops it.
   (b) Apply the bracket rule on every update. Entering rows is then refused until the old answer
   is fixed, but the page has no editor for those answers.
   (c) Also hold the draft while an old answer holds a bracket.
   *Recommend (a)*: it is rare, and it is caught before signature.
2. **The exam's SOW documents** (`backend/eval/exam`, spec 005).
   (a) *Default:* the exam renders a frozen copy of today's SOW wording, so its documents and
   baseline are unchanged.
   (b) Rebuild the exam on the new wording (new offsets, the baseline comparison is lost).
   (c) Leave the exam on the live template (its content tests fail).
   *Recommend (a).* It touches a file outside the usual ownership, so it needs your yes.
3. **Changing rows on a request that already has them, from the page.**
   (a) *Default:* the page's editor appears only when a request has no rows (older requests, as
   FR-33 needs). The API accepts changes and audits them.
   (b) Also offer "Change deliverables" on any open SOW request before drafting.
   (c) No page editor (fails FR-33's "may enter the rows").
   *Recommend (a)*; (b) is a small later add.
4. **A check suggestion that quotes example content.**
   (a) *Default:* mark it not appliable (`suggestion_is_wording: false`). There is no API change,
   but the page calls it advice.
   (b) Add a `quotes_example` flag with an exact reason on the page (an API, type and
   `canApplyFinding` change).
   (c) No guard (Apply could write example deliverables into our paper, against FR-24).
   *Recommend (a).*
5. **Over-built candidates in the spec** (kept in this plan, not dropped):
   (i) FR-14 + FR-16 make the wizard port the blank detector to TypeScript (~25 lines with Unicode
   classes) only so that "the wizard never accepts what filing refuses" holds for brackets. Letting
   the server alone refuse brackets would save the port and the parity cases.
   (ii) FR-12's past-date note is a wizard-only nicety. The server accepts past dates anyway.
   (iii) FR-17's running share total duplicates what M12 already says on Continue.
   *Recommend keeping all three:* each is small, and (i) is what makes FR-2 hold for typed text.
   Say if you want (ii) or (iii) dropped.
