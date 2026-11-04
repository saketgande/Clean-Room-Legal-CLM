# Implementation Plan: Templates and playbooks aligned, so our own paper passes our own playbook

Feature ID: 014-template-playbook-alignment
Spec: ./spec.md (Status: APPROVED, 2026-10-27; 56 FRs incl. FR-1A, 46 ACs incl. AC-1A, Q1–Q19 binding)
Created: 2026-10-27
Status: APPROVED   <!-- DRAFT | APPROVED — only the user approves -->

## Architecture overview

This feature is mostly **content** (seven template texts and six-plus-one playbook rule sets),
changed as pairs, plus a small amount of plumbing so the content can be filled correctly and
reach existing organisations safely. No new domain, no new table.

```
request answers ──┐
register rows ────┼─> intake/drafting.template_values ──> defaults/<key>.txt ──> draft text
parent contract ──┘      ├─ sow.py / consultancy.py / dpa.py (existing per-type fillers, edited)
                         └─ intake/paper.py (NEW, pure): law, seat, dispute route, signatories,
                            addresses, currency amounts, DPA status, NDA/Vendor/SaaS variants

playbooks/library.py ── new standard rule sets (6 library playbooks + NDA_STANDARD)
        │
        ├─ tests (no AI): every form-answer combination → render → evaluate_rules_against_text → 0
        ├─ eval/template_alignment.py (deliberate, paid): AI review 3× per main combination
        └─ drafting_templates/standard.py (NEW): fingerprint, apply-to-existing-org, "older than
           standard" status, "use the standard rules" action
                 │
                 └─ alembic 0077_std_paper (data-only): runs standard.apply_standard_update on deploy,
                    so the new template text (read live from defaults/) and the new playbook versions
                    arrive in the same release (FR-4)
```

Existing modules reused, not paralleled:

- **Filling**: `intake/drafting.template_values` / `render_document` stay the single fill path
  (drafts, Templates-page check, preview). New fill logic goes in one new pure module
  `app/intake/paper.py`, shaped exactly like `intake/sow.py` (pure functions, stdlib + settings +
  `sow` helpers only, `EXAMPLE_*` for the check, no DB, no audit) so `drafting_templates.service`
  can import it without a cycle.
- **Save guard (FR-55)**: unchanged `check_body`; new placeholders are added to `PLACEHOLDERS`
  (with samples) and filled by `template_values`; `test_the_placeholder_list_is_exactly_what_
  drafting_fills` keeps them equal. **No placeholder is ever removed**: an org's saved wording
  may still use `{pricing_model}`, `{payment_days}`, `{agreement_term}`, `{value_sentence}`,
  `{governing_law}` etc., so every existing name keeps being filled even where the new defaults
  stop using it.
- **Holds (FR-25, FR-35)**: the existing `draft_hold` → `DraftHeld` (409) → `held_step` path, which
  the workflow already re-evaluates on its own (`workflows/service.py` ~L1858) once answers change.
- **Template check / out-of-date (FR-8, FR-9)**: unchanged `checks.template_status`. Because the
  shipped default is read and hashed live, a release that edits `defaults/<key>.txt` already turns
  every version-0 check *Out of date*; publishing a new playbook version does the same
  (`playbook_moved`). The stale 15 Oct SOW result is therefore replaced as soon as Check all runs.
- **Versioning (FR-53)**: playbooks get new versions through the same shape
  `clone_playbook_version` + publish uses (new `PlaybookVersion` + `PlaybookRule` rows,
  `playbook.published` audit); templates get a `drafting_template_version` row (`body` NULL = follow
  the shipped default) with a note. Nothing is edited in place.
- **Phrase check**: `playbooks/service.evaluate_rules_against_text` stays the deterministic engine;
  one small extension (one phrase per line, see Component design) so a rule can name the few
  risky wordings it must still catch on a counterparty's paper (FR-51).
- **AI proof**: `PlaybooksService.review_text` — the same skill, prompt, rules and payload the
  automatic review of a drafted contract uses — called from a deliberate script under `backend/eval/`
  (like `eval/exam`, never run by CI).

**Customer Agreement Playbook (FR-7)**: unchanged. Its only shared topic with the new positions is
`governing_law` ("Indian law with courts or arbitration seated in India"; fallback English law/SIAC
for foreign customers), which already agrees with Q2. A test pins its rules to the reviewed
baseline fingerprint (AC-6).

**Existing contracts (FR-10, FR-11)**: nothing in this feature writes to `contract*`,
`playbook_run`, `playbook_deviation`, `contract_edit`, `intake_request` or timelines of contracts.
Publishing a new playbook version never re-reviews a stored contract (only templates are checked
after a publish, `_check_templates_after_publish`). Old runs keep pointing at their old version ids.

### Key design principle for "zero findings"

The AI reviewer reports a clause sitting at a rule's *fallback* as a finding (both real NDA
redlines were exactly that), and `kept_deviations` only drops items it places at the *preferred*
position. So **every wording any form answer can produce is written into the rule's preferred
position**, and the fallback holds only positions our own paper never produces (a counterparty
asking for more). Example: MSA payment preferred = "45 or 60 days … never more than 45 days to a
registered micro or small enterprise"; fallback = "90 days for a non-MSE supplier with Finance
approval". Every rule below follows this.

## Path mapping (ownership boundaries for THIS feature — exact files)

| Agent | Files in this feature |
|---|---|
| db-engineer | `backend/alembic/versions/0077_std_paper.py` (new, data-only). No model change; `backend/app/models.py` untouched. |
| backend-dev | NEW: `backend/app/intake/paper.py`, `backend/app/drafting_templates/standard.py`, `backend/eval/template_matrix.py`, `backend/eval/template_alignment.py`. EDIT: `backend/app/drafting_templates/defaults/{nda,msa,sow,vendor,saas,consultancy,dpa}.txt`, `backend/app/intake/drafting.py`, `backend/app/intake/sow.py`, `backend/app/intake/consultancy.py`, `backend/app/intake/dpa.py`, `backend/app/drafting_templates/service.py`, `backend/app/drafting_templates/checks.py`, `backend/app/drafting_templates/routes.py`, `backend/app/drafting_templates/schemas.py`, `backend/app/playbooks/library.py`, `backend/app/playbooks/service.py` (only `evaluate_rules_against_text` + a `_phrases` helper), `backend/app/intake/agreement_forms.json`, `backend/app/intake/agreement_forms.py`, `backend/app/intake/service.py` (only the `select` branch of `_coerce_field` / its caller). TESTS NEW: `backend/tests/test_paper_fill.py`, `test_playbook_rules_014.py`, `test_payment_options_014.py`, `test_drafting_context_014.py`, `test_alignment_nda.py`, `test_alignment_msa.py`, `test_alignment_sow.py`, `test_alignment_vendor.py`, `test_alignment_saas.py`, `test_alignment_consultancy.py`, `test_alignment_dpa.py`, `test_standard_update_014.py`, `test_existing_contracts_untouched_014.py`. TESTS EDIT (only where they pin old wording/defaults): `test_drafting_templates.py`, `test_playbook_library.py`, `test_phase5_playbooks.py` (only if the phrase change touches it), `test_sow_drafting.py`, `test_sow_deliverable_rows.py`, `test_consultancy_dpa_drafting.py`, `test_consultancy_dpa_answers.py`, `test_nda_recital_purpose.py`, `test_template_check.py`, `test_template_status.py`, `test_intake_agreement_form_validation.py`. |
| frontend-dev | `frontend/src/lib/types.ts` (interfaces below), `frontend/src/lib/endpoints.ts` (`draftingTemplatesApi.adoptStandardPlaybook`), `frontend/src/lib/form-checks.ts` (`optionsFor`, not-offered problem), `frontend/src/app/(app)/intake/_agreement-forms.tsx` (`Question` uses `optionsFor`), `frontend/src/app/(app)/templates/_template-list.tsx` (badge), `frontend/src/app/(app)/templates/_template-detail.tsx` (two notices + button), `frontend/src/app/(app)/templates/_check-panel.tsx` (`TRIGGER_LABEL.sample`), tests: `frontend/src/lib/form-checks.test.ts` (or the existing test file for form-checks), `frontend/src/app/(app)/templates/page.test.tsx`, `frontend/src/app/(app)/templates/_check-panel.test.tsx`, `frontend/src/app/(app)/intake/_agreement-wizard.test.tsx`. |
| qa-engineer | `specs/014-template-playbook-alignment/verification.md` |

Shared-hotspot note (these serialize across tasks):
- `backend/app/intake/drafting.py`, `backend/app/drafting_templates/service.py` (PLACEHOLDERS),
  `backend/app/drafting_templates/checks.py`, `backend/tests/test_drafting_templates.py`: T005 then T013 (never together).
- `backend/tests/test_consultancy_dpa_drafting.py`: T011 then T012.
- `frontend/src/lib/types.ts`, `frontend/src/lib/endpoints.ts`: T004 only.
- `backend/app/models.py`, `backend/app/jobs/tasks.py`, `frontend/src/components/ui.tsx`: not touched.
- One migration (T014) only.

## Interface freeze (contract-first — fixed before implementation starts)

### API contract

| Method | Path | Permission | Request body | Response (200/201) | Errors |
|---|---|---|---|---|---|
| GET | /api/v1/drafting-templates | `playbook:read` (unchanged) | — | `DraftingTemplateSummary[]`, each with new `standard` | — |
| GET | /api/v1/drafting-templates/{key} | `playbook:read` (unchanged) | — | `DraftingTemplate` with new `standard` | 404 |
| POST | /api/v1/drafting-templates/{key}/playbook/standard | `playbook:publish` (existing permission) | `{ "note": string \| null }` | `TemplateMutationResult` (200) | 403, 404, 409, 422 |
| GET | /api/v1/drafting-templates/{key}/checks (+ `/{check_id}`) | `playbook:read` (unchanged) | — | `trigger` may now be `"sample"` | — |
| GET | /api/v1/intake/forms | existing intake create permission (unchanged) | — | form fields may carry `options_when` | — |
| POST/PATCH | intake filing / request update (unchanged routes) | unchanged | select answers validated against `options_when` | unchanged | 422 "Field \"Payment terms\" must be one of: 45 days, 60 days" |

No other endpoint changes. No permission string is added (`playbook:publish` already exists and is
what `POST /playbooks/{id}/publish` and `/playbooks/seed-library` require).

Full shapes:

```json
// DraftingTemplateSummary (GET /drafting-templates item; also inside DraftingTemplate) — ONE new key
{
  "...every existing field unchanged...": "",
  "standard": {                       // null for a type that was not shipped (org's own type)
    "revision": "014",                // the standard this release ships
    "updated_on": "2026-10-27",       // ISO date the standard wording and rules were set
    "wording_behind": true,           // the org's own saved wording (source "text") predates the standard
    "playbook_behind": false          // the playbook reviewing this template has rules that differ from
                                      // the standard AND its current published version predates it
  }
}

// POST /drafting-templates/{key}/playbook/standard  request
{ "note": "Adopting the 27 Oct standard after review" }      // optional, max 300 chars, trimmed

// response 200 — TemplateMutationResult (existing shape)
{
  "template": { "...DraftingTemplate...": "" },
  "changed": true,              // false = the playbook already had the standard rules: no version, no check
  "check": { "...TemplateCheckSummary...": "" }   // the template check started (trigger "playbook_published"), or null
}
// 403: no playbook:publish.  404: unknown key / not this org's.
// 409: "No playbook reviews this template, so there are no rules to update."  (no pin and no name match,
//      or the pinned playbook is deleted)
// 409: "This template has no standard rules." (a type the org created itself)
// 422: note longer than 300 characters.

// GET /intake/forms — a field MAY carry options_when (only payment_terms does in this feature)
{
  "key": "payment_terms", "label": "Payment terms", "kind": "select", "required": true,
  "options": ["30 days", "45 days", "60 days", "90 days"],          // the default list (Selling, Something else)
  "options_when": [
    { "when": [{ "field": "agreement_type", "in": ["Services (MSA)", "Consultancy"] }],
      "options": ["45 days", "60 days"] },
    { "when": [{ "field": "agreement_type", "in": ["Buying from a vendor", "Software or SaaS"] }],
      "options": ["45 days", "60 days", "90 days"] }
  ],
  "show": [ "...unchanged..." ]
}
// Resolution (server and browser, identical): the first options_when entry whose rules ALL match the
// form's answers gives the options; none matching gives "options". The same rule engine as "show".
```

Audit actions written by each mutation:

| Mutation | Audit action(s) (`write_audit_log`) | Actor |
|---|---|---|
| POST …/playbook/standard | `playbook.standard_adopted` (resource `playbook`, before `{version_id, version_number}`, after `{version_id, version_number, revision:"014", template_key, note, changes:[{clause_type, change, review_items}]}`) and `playbook.published` (after `{playbook_version_id, version_number}`), same transaction, committed BEFORE the template check is requested | the person |
| Migration 0077 (per org, per shipped key on the shipped wording) | `drafting_template.standard_updated` (resource `drafting_template`, resource_id = the new version row, after `{key, version, revision:"014", note, default:true}`) | system: `actor_user_id=None`, `metadata={"actor":"system","revision":"014"}` |
| Migration 0077 (per org playbook that still holds the reviewed baseline rules) | `playbook.standard_updated` + `playbook.published` (after as for adopt, plus `template_key`) | system |
| Migration 0077 (per org playbook the org had edited) | `playbook.standard_skipped` (after `{template_key, reason:"edited by the organisation"}`) | system |
| Migration 0077 downgrade | `playbook.standard_reverted` per reverted playbook | system |
| AI sample run (eval script), one per run | existing `drafting_template.check_finished` (after includes `trigger:"sample"`, `finding_count`) | the person named with `--as` |
| Template saves/resets/checks | unchanged existing actions | unchanged |

No timeline events (no contract or request changes). Viewing writes nothing.

### Database schema

**No schema change.** No new table, column, index or constraint.

Migration: revision ID `0077_std_paper` (14 chars) on current head `0076_request_summary`,
**data-only**:

```python
revision = "0077_std_paper"
down_revision = "0076_request_summary"

def upgrade() -> None:
    if context.is_offline_mode():
        return
    from sqlalchemy.orm import Session          # lazy, like 0045
    from app.drafting_templates.standard import apply_standard_update
    session = Session(bind=op.get_bind())
    apply_standard_update(session)              # flushes only; alembic's transaction commits
    session.flush()

def downgrade() -> None:
    if context.is_offline_mode():
        return
    ... revert_standard_update(session) ...
```

Why this is safe to replay: `apply_standard_update` first runs raw `SELECT id FROM organization`;
on a fresh database (CI, `make test-db`, any future rebuild) there are no orgs yet, so it returns
before touching any ORM model — a future model column can never break replaying 0077. On the dev
and production databases it runs once, with the code of this release.

Writes (all org-scoped, `org_id` set on every row):
- `drafting_template_version`: one row per org × shipped key **whose latest wording is the shipped
  default** (no row, or latest `body IS NULL`): `body=NULL`, `version=max+1`, `note` = the per-
  template "Standard updated" note (see each template section), `created_by_user_id=NULL`. An org
  with its own saved wording (`body` not NULL) gets no row: its text stays in effect (FR-54).
- `playbook_version` + `playbook_rule`: for the playbook that reviews each shipped template
  (`reviewing_playbook`: the pin, else today's name match), **only if** its latest published
  version's rules fingerprint equals the reviewed baseline (`BASELINE_FINGERPRINTS[key]`): a new
  version `version_number=max+1`, `status=published`, `summary` = plain reason,
  `source_metadata={"standard":"014","source_version_id":<prev>,"template_key":key,"changes":[…]}`;
  rules inserted in the standard order (so `rules_in_review_order` numbers them as listed here);
  `playbook.current_version_id` set to it. A playbook already equal to the standard is skipped
  silently; one the org edited is skipped with `playbook.standard_skipped`.
- `audit_log` rows as listed above.

Downgrade: for every `playbook_version` with `source_metadata.standard == "014"` and status
published: set `status=archived`; if it is the playbook's `current_version_id`, point that back at
`source_metadata.source_version_id`; audit `playbook.standard_reverted`. Template marker rows are
left (they hold no text and follow whatever default the code ships). Nothing is deleted.

### Frontend TypeScript models (added to src/lib/types.ts)

```ts
// Mirrors DraftingTemplateSummaryOut.standard exactly
export interface TemplateStandard {
  revision: string;          // "014"
  updated_on: string;        // "2026-10-27"
  wording_behind: boolean;
  playbook_behind: boolean;
}

// DraftingTemplateSummary gains one field (DraftingTemplate inherits it):
//   standard: TemplateStandard | null;

// TemplateCheckTrigger gains "sample":
export type TemplateCheckTrigger =
  | "saved" | "uploaded" | "restored" | "reset" | "type_created" | "type_reactivated"
  | "playbook_published" | "playbook_changed" | "manual" | "check_all" | "retry" | "sample";

export interface TemplateStandardAdopt { note?: string | null; }

// RequestFormField gains:
//   /** Options that replace `options` when every rule matches; first match wins. */
//   options_when?: { when: RequestFormRule[]; options: string[] }[];
```

### Endpoint client additions (src/lib/endpoints.ts)

- `draftingTemplatesApi.adoptStandardPlaybook(key: string, payload: TemplateStandardAdopt) → Promise<TemplateMutationResult>` — maps to `POST /drafting-templates/${key}/playbook/standard`.

### Backend schema additions (`app/drafting_templates/schemas.py`)

- `class TemplateStandardOut(BaseModel): revision: str; updated_on: date; wording_behind: bool; playbook_behind: bool`
- `DraftingTemplateSummaryOut.standard: TemplateStandardOut | None`
- `class TemplateStandardAdopt(BaseModel): note: str | None = Field(default=None, max_length=300)`
- `TemplateCheckTrigger` Literal gains `"sample"`. Model comment on `DraftingTemplateCheck.trigger` updated (no DDL: the column is `String(30)`).

## Component design

### Backend

- **Models**: none changed.
- **Settings**: none. No `MOCK_*` flag, no `validate_runtime_settings()` entry (the AI script uses the
  existing `MOCK_CLAUDE`/Claude key, and only refuses to run when mocked).
- **Celery**: none new. Checks keep using the existing `template_check` job.

#### `app/playbooks/service.py` — one-phrase-per-line (FR-51, FR-52)

```python
def _phrases(value: str | None) -> list[str]:
    """One phrase per line; blank lines ignored; each line whitespace-collapsed."""
```
- `prohibited_language`: flags the **first** line found (one deviation per rule, quote = that match).
- `required_language`: flags when **any** line is missing (one deviation; `issue` names the first
  missing line; citation `required_language` = that line).
- A single-line value behaves exactly as today, so every stored playbook reads as before.
- The AI review still receives the field text unchanged (`rule_payloads_for_review`).

#### `app/intake/paper.py` (NEW, pure) — the shared fill-ins

Frozen API (backend agents code against this):

```python
BLANK = "______________________________"          # 30 underscores: a line to fill by hand

@dataclass(frozen=True)
class DraftContext:
    entity_address: str | None = None             # LegalEntity.registered_address
    entity_signatory: str | None = None           # LegalEntity.authorised_signatory ("R. Iyer, Director")
    counterparty_jurisdiction: str | None = None  # Counterparty.jurisdiction
    counterparty_address: str | None = None       # Counterparty.address
    parent_title: str | None = None               # the request's parent contract (SOW, DPA)
    parent_effective: str | None = None           # its effective date, ISO

EXAMPLE_CONTEXT = DraftContext(
    entity_address="Level 5, One BKC, Bandra Kurla Complex, Mumbai 400051, India",
    entity_signatory="Priya Nair, Director",
    counterparty_jurisdiction="India",
    counterparty_address="12 MG Road, Bengaluru 560001, India",
    parent_title="Master Services Agreement", parent_effective="2026-04-01")
EXAMPLE_MARKERS = ("Priya Nair", "One BKC", "12 MG Road", "Bengaluru 560001")

FIELDS = ("dispute_seat", "dispute_resolution", "our_signatory_name", "our_signatory_title",
          "their_signatory_name", "their_signatory_title", "company_address", "counterparty_address",
          "fee_currency", "insurance_minimum", "data_breach_floor", "dpa_status", "nda_personal_data",
          "gxp_clause", "parent_agreement", "pricing_terms", "sow_payment", "supply_description",
          "vendor_supply_terms", "vendor_insurance", "hosting_clause", "renewal_notice_days",
          "consultancy_invoicing")

def paper_values(doc_type: str, fv: dict, *, company: str, counterparty: str,
                 context: DraftContext | None, example: bool = False) -> dict[str, str]: ...
def governing_law(doc_type: str, fv: dict) -> str: ...
def renewal_notice_days(fv: dict) -> int: ...                 # SaaS: max(notice_days, 60)
def number_words(n: int) -> str: ...                          # 30 -> "thirty (30)"; 45, 60, 90, 24, 6
def placeholder_scope(text: str | None) -> bool: ...          # FR-25
```
`example=True` uses `EXAMPLE_CONTEXT` when `context` is None (only the Templates-page check and
preview pass it). A real request with no context fills register-derived fields with `BLANK` /
the descriptive seat. `pricing_terms` and `sow_payment` delegate to `sow.py` (below); `paper`
imports `sow` only for its money helpers, `sow` never imports `paper`.

Fill rules (every value is plain text; a value that would be empty is `BLANK` or a stated default,
never an empty label):

| Field | Rule |
|---|---|
| `governing_law` (existing name, new logic, in `template_values`) | NDA: the form's answer mapped `{"India":"India","England and Wales":"England and Wales","Singapore":"Singapore","New York":"the State of New York","Delaware":"the State of Delaware"}`; anything else (blank, an Indian state or city such as "Maharashtra, India", any unrecognised text) → `"India"`. Every other template → always `"India"` (Q2; the form does not ask). The old entity-jurisdiction override in `draft_contract_for_request` (drafting.py ~L663–672) is **deleted**, and `"the State of Delaware"` disappears from the code (FR-12, FR-13). |
| `dispute_seat` | The first known Indian city found (word match, case-insensitive) in `entity_address`, normalised: Mumbai, New Delhi (also "Delhi"), Bengaluru (also "Bangalore"), Chennai, Hyderabad, Kolkata, Pune, Ahmedabad, Gurugram (also "Gurgaon"), Noida, Chandigarh, Kochi, Jaipur, Lucknow, Indore, Bhubaneswar, Coimbatore, Thiruvananthapuram, Nagpur, Vadodara. None found → `"the city in which {company}'s registered office is situated"` (FR-14: the seat IS our registered city; this states it without inventing one). For MSA/Vendor/SaaS with a **foreign** provider → `"Singapore"` (Consultancy always keeps our city: courts at the seat, decision 2). `ponytail:` a fixed city list; upgrade = a seat question on the per-type forms (next feature). |
| foreign provider | `counterparty_jurisdiction` non-blank and not Indian. Indian = contains "India" or one of the 28 state / 8 UT names (word match). Blank → Indian (domestic default). |
| `dispute_resolution` | **NDA**: "Each Party irrevocably submits to the exclusive jurisdiction of the courts at {seat} for any dispute arising out of or in connection with this Agreement and waives any objection to those courts, including that they are an inconvenient forum, provided that either Party may seek urgent interim or injunctive relief from any court of competent jurisdiction." **Consultancy (every consultant, Indian or foreign — decision 2)**: "Any dispute that is not resolved by negotiation within that period is subject to the exclusive jurisdiction of the courts at {seat}, provided that either Party may seek urgent interim or injunctive relief from any court of competent jurisdiction." **MSA/Vendor/SaaS, Indian provider**: "Any dispute that is not resolved by negotiation within that period shall be finally resolved by arbitration by a sole arbitrator under the Arbitration and Conciliation Act, 1996, administered by the Mumbai Centre for International Arbitration under its rules. The seat of arbitration is {seat}, the language is English, and the award is final and binding on the Parties. Subject to this, the courts at {seat} have exclusive supervisory jurisdiction, and nothing in this clause prevents either Party from seeking urgent interim or injunctive relief from any court of competent jurisdiction." **Same, foreign provider**: "Any dispute that is not resolved by negotiation within that period shall be finally resolved by arbitration by a sole arbitrator under the Arbitration Rules of the Singapore International Arbitration Centre in force when the arbitration starts. The seat of arbitration is Singapore, the language is English, and the award is final and binding on the Parties. Nothing in this clause prevents either Party from seeking urgent interim or injunctive relief from any court of competent jurisdiction, including under section 9 of the Arbitration and Conciliation Act, 1996." SOW, DPA: `""` (they follow the parent agreement). |
| `our_signatory_name`, `our_signatory_title` | `entity_signatory` split at the first comma ("R. Iyer, Director" → "R. Iyer" / "Director"); missing part → `BLANK`. |
| `their_signatory_name`, `their_signatory_title` | `cp_signer_name` split the same way ("Maya Chen, CFO"); missing → `BLANK`. |
| `company_address` / `counterparty_address` | the register address on one line (whitespace collapsed), else `BLANK`. Never a typed or invented address (FR-18). |
| `fee_currency` | `fv["currency"]` else `settings.default_currency` ("INR"). |
| `insurance_minimum` / goods floor / `data_breach_floor` | from one table keyed by `fee_currency` (DPA always uses `settings.default_currency`): INR → "INR 50,000,000 (five crore rupees)" / "INR 10,000,000 (one crore rupees)"; USD → "USD 600,000" / "USD 120,000"; EUR → "EUR 550,000" / "EUR 110,000"; GBP → "GBP 475,000" / "GBP 95,000". `data_breach_floor` = the five-crore row (decision 1). The table is ONE constant, `paper.MINIMUMS: dict[currency, {"five_crore": str, "one_crore": str}]`, and is the only place these amounts are written, so Finance can change them in one edit (decision 3). |
| `dpa_status` | roles `{"msa":("Customer","Provider","Personal Data"), "vendor":("Company","Vendor","personal data"), "saas":("Customer","Provider","personal data"), "consultancy":("Company","Consultant","personal data")}`. `personal_data == "No"` → "The Parties do not expect the {P} to process {pd} under this Agreement. Before any {pd} is processed, the Parties shall sign a data processing agreement on the {C}'s standard template, which will then form part of this Agreement and prevail over it on the processing of {pd}." Otherwise (Yes, or blank = the safe reading) → "The Parties expect the {P} to process {pd} under this Agreement. They shall sign a data processing agreement on the {C}'s standard template at the same time as this Agreement; it applies from the Effective Date, forms part of this Agreement and prevails over it on the processing of {pd}, and no {pd} is processed before it is signed." Other doc types → `""`. |
| `nda_personal_data` | Yes → "Where Confidential Information includes personal data, the Receiving Party shall process it only for the Purpose and in compliance with applicable data protection laws, including the Digital Personal Data Protection Act, 2023, with reasonable security safeguards, and shall notify the Disclosing Party of any personal data breach affecting it without undue delay." No/blank → "The Parties do not expect to share personal data under this Agreement. If any Confidential Information includes personal data, the Receiving Party shall process it only for the Purpose and in compliance with applicable data protection laws, including the Digital Personal Data Protection Act, 2023, with reasonable security safeguards." |
| `gxp_clause` | MSA and `gxp == "Yes"` → "\n\n3.4 Where the Services are GxP-regulated, the Parties shall sign a quality agreement before the Services start, and it prevails over this Agreement on quality matters."; else `""`. |
| `parent_agreement` | parent chosen: `'the agreement titled "{parent_title}" dated {long date}' between the Parties` (no date → without "dated …"); none: `"the master agreement between the Parties"` (DPA: `"the services agreement between the Parties"`). |
| `supply_description` | `buying`: Goods → "goods"; Services → "services"; Goods and services or blank → "goods and services". |
| `vendor_supply_terms` | goods: "\n\n3.3 Delivery. Goods shall be delivered DAP (Incoterms 2020) to the place stated in the Order, properly packed and labelled, with a delivery note identifying the Order. Title and risk in the goods pass to the Company on acceptance.\n\n3.4 Quantities. The Company may reject any delivery that is short of, or in excess of, the quantity ordered; excess goods are held at the Vendor's risk and returned at its cost.\n\n3.5 Recall. If any goods become subject to a recall or a safety notice, the Vendor shall notify the Company at once, carry out the recall, and replace or refund the affected goods, at its own cost." services (numbered 3.3 alone, 3.6 after the goods block): "\n\n3.N Services. The Vendor shall perform services with the reasonable skill, care and diligence of a competent supplier of similar services, by suitably qualified and experienced personnel, and shall meet or exceed any service levels set out in the applicable Order; any service credits are a price adjustment and are without prejudice to the Company's other rights. Services are accepted only on the Company's written confirmation that they meet the Specification, and until then the Vendor shall re-perform them at no charge." |
| `vendor_insurance` | goods: "The Vendor shall maintain with reputable insurers, during the term of this Agreement and for one (1) year after it ends, public and product liability insurance with a limit of not less than the greater of the total Fees under this Agreement and {goods floor} per claim, and employer's liability or workmen's compensation insurance as required by Applicable Law." services: same opening, "professional indemnity insurance with a limit of not less than {insurance_minimum} per claim and in the aggregate, and employer's liability …". both: both covers in one sentence. All end: " Where the Vendor processes the Company's personal data or has access to its systems, it shall also maintain cyber liability insurance of not less than {insurance_minimum} per claim. The Vendor shall provide evidence of cover on request." |
| `hosting_clause` (SaaS 9.1–9.2) | India: "9.1 The Provider shall host, store and process Customer Data only in data centres located in India." EU / US: "9.1 The Provider shall host, store and process Customer Data only in data centres located in the European Union" / "… in the United States of America" + ". That location is subject to the written approval of the Customer's privacy function, which shall be given before any Customer Data is stored there." Several regions: "9.1 The Provider shall host, store and process Customer Data only in the data centre regions named in the Order Form, each subject to the written approval of the Customer's privacy function before any Customer Data is stored there." Every variant then: "\n\n9.2 The Provider shall not move Customer Data to, or permit access to it from, any other location without the Customer's prior written approval given after review by the Customer's privacy function, never to a country or territory restricted by the Central Government under section 16 of the Digital Personal Data Protection Act, 2023, and, for personal data subject to the GDPR or the UK GDPR, only under the Standard Contractual Clauses or another safeguard recognised by that law." Blank → India. |
| `renewal_notice_days` | `number_words(max(int(notice_days digits or 60), 60))` → "sixty (60)" / "ninety (90)". |
| `consultancy_invoicing` | Fixed fee + rows: "The Consultant shall invoice the fixed fee in equal parts, each on the Company's written acceptance of a Deliverable listed in the Engagement Schedule." Fixed fee + advice only: "The Consultant shall invoice the fixed fee monthly in arrears, in proportion to the Services performed in that month." Day / Hourly rate: "The Consultant shall invoice the Fees monthly in arrears for time properly spent in that month." Blank: "The Consultant shall invoice the Company monthly in arrears." |
| `pricing_terms`, `sow_payment` | see SOW below (built in `sow.py`, exposed through `paper_values`). |

`placeholder_scope(text)` is true when the scope has fewer than 15 words, or matches (case-
insensitive) `\bthis is (a |an |just a |for a )?test\b`, `\blorem\b`, `\bipsum\b`, `\btbd\b`,
`\btba\b`, `\bto be (decided|confirmed)\b`, or is only "test"/"testing"/"sample"/"dummy".
`ponytail:` a word list; upgrade = the form refusing it at filing.

#### `app/intake/drafting.py` (T005)

- `template_values(doc_type, *, company, counterparty, effective, fields=None, example=False,
  context: DraftContext | None = None)` — new keyword, default None, so every existing caller
  (`check_values`, tests) still works. It returns everything it returns today **plus**
  `**paper.paper_values(...)`, and:
  - `governing_law` = `paper.governing_law(doc_type, f)`;
  - `term_clause` = `{"2 years": "continues for two (2) years", "3 years": "continues for three (3)
    years", "5 years": "continues for five (5) years"}.get(nda_term, "continues for three (3) years")`
    (FR-27);
  - `payment_days` default `"45"` for every doc type (consultancy no longer `"30"`, FR-3);
  - `agreement_term` for **saas** uses `renewal_notice_days` ("… unless either Party gives sixty
    (60) days' written notice of non-renewal"), so 6.1 and 6.2 always state the same period
    (FR-44); other doc types unchanged.
- `render_document(..., context=None)` passes it through.
- `DraftingService._draft_context(request) -> DraftContext`: register rows via `parties.get(...)`
  (already org-checked) and the parent via `db.get(Contract, fv["parent_contract_id"])` used only
  when `parent.org_id == request.org_id` (title + effective_date; same guard as
  `apply_change_request`). `draft_contract_for_request` passes it to `render_document`.
- Delete the entity-jurisdiction → `governing_law` block (~L663–672).
- `draft_hold` gains, before its current Consultancy/DPA logic and after the SOW rows check:
  - SOW whose wording uses `pricing_terms` or `sow_payment`, with no `pricing` answer or no total
    value → `SOW_PRICING_HELD` = "Choose how this Statement of Work is priced (fixed fee, milestones,
    or time and materials) and enter its total value on the request first. The draft prints only the
    pricing chosen, so it is not made without it." (FR-35)
  - SOW or Consultancy (on our paper) whose wording uses `services`, where
    `paper.placeholder_scope(fv["scope"])` → `SCOPE_HELD` = "Describe the work on the request first,
    in at least 15 words. The scope it holds now reads as placeholder text, and the draft would print
    it as the services the other side must perform." (FR-25)
  Both go through the existing `held_step` "answers" branch and resume on their own.
- Constants `SOW_PRICING_HELD`, `SCOPE_HELD` live in `paper.py` (pure) and are imported here.

#### `app/drafting_templates/service.py` + `checks.py` (T005, then T013)

- `PLACEHOLDERS` gains the 23 names of `paper.FIELDS`, each `{name, label, about, sample}` with
  `sample` = `paper.paper_values(<doc type that uses it>, {}, company=…, counterparty=…,
  context=None, example=True)[name]` (labels, all unique): Dispute seat · Dispute resolution ·
  Our signatory · Our signatory's title · Their signatory · Their signatory's title · Our registered
  office · Their address · Fee currency · Insurance minimum · Data breach floor · Personal data
  status · NDA personal data · Quality agreement (GxP) · Master or principal agreement · Pricing
  terms · SoW payment · What is supplied · Delivery and acceptance terms · Vendor insurance · Hosting
  location · Renewal notice · Consultancy invoicing.
- `checks._CODED` adds `*paper.FIELDS`; `checks._EXAMPLE_MARKERS` adds `*paper.EXAMPLE_MARKERS` (so a
  suggested fix quoting the sample signatory or address is advice, never applied).
- (T013) `checks._MANUAL` adds `"sample"` (a person's deliberate run); `_results_vary` ignores
  `trigger == "sample"` rows (different answers, not identical runs). `template_status` is otherwise
  unchanged: a sample run with findings is the latest finished check and shows *Has findings*,
  which is the truthful state.
- (T013) `_summary` adds `"standard": standard.status_for(db, org_id, ttype, wording)` (`None` for a
  non-shipped type).

#### `app/drafting_templates/standard.py` (NEW, T013)

```python
REVISION = "014"
UPDATED_ON = date(2026, 10, 27)
EFFECTIVE = datetime(2026, 10, 27, tzinfo=UTC)   # ponytail: a version saved before this instant is
                                                 # "older than the standard"; per-org apply time if it matters
FIELDS = ("clause_type", "rule_type", "risk_level", "preferred_position", "fallback_position",
          "prohibited_language", "required_language", "rationale")

def fingerprint(rules) -> str      # sha256 of json.dumps(sorted([[whitespace-collapsed str(getattr/[] f) or "" for f in FIELDS]]))
BASELINE_FINGERPRINTS: dict[str, frozenset[str]]  # key -> fingerprints of the rule sets the review read
CUSTOMER_BASELINE: str                            # Customer Agreement Playbook, for AC-6
STANDARD_RULES: dict[str, list[dict]]             # nda -> library.NDA_STANDARD["rules"]; others -> the library entry's rules
NOTES: dict[str, str]                             # the per-template "Standard updated" notes (below)
CHANGES: dict[str, list[dict]]                    # [{clause_type, change: changed|added|kept, review_items: [...]}]

def status_for(db, org_id, ttype, wording) -> dict | None
def publish_standard(db, *, org_id, playbook, key, actor_user_id: str | None, note: str | None,
                     action: str) -> PlaybookVersion | None    # flush only; None when already standard
def apply_standard_update(db: Session) -> None                  # the migration's body; flush only
def revert_standard_update(db: Session) -> None
def adopt(db, *, actor: User, key: str, note: str | None, request_id: str | None) -> dict   # the route
```
- `BASELINE_FINGERPRINTS` are computed **once** from `specs/014-template-playbook-alignment/review/
  playbooks.json` (the exact rule sets the review read from the dev DB; the 8 fields above) and
  pasted as hex constants with that comment; the specs folder is not in the container, so the code
  never reads it. A test asserts the current library code of the Customer Agreement still hashes to
  `CUSTOMER_BASELINE`.
- `status_for`: `wording_behind = wording.row is not None and wording.row.body is not None and
  wording.row.created_at < EFFECTIVE`; `playbook_behind = reviewing playbook exists and its latest
  published version's fingerprint != fingerprint(STANDARD_RULES[key]) and that version's
  created_at < EFFECTIVE`. Reads only.
- `adopt`: 404 via `get_type`; 409 when not shipped / no reviewing playbook / deleted; else
  `publish_standard(..., action="playbook.standard_adopted")` + `playbook.published`, `db.commit()`,
  **then** `checks.request_check(trigger="playbook_published", trigger_detail={...})` (commit before
  the check, per the audit-lock rule). `changed=False` and no check when already standard.
- Org scoping: every query filters `org_id`; `adopt` uses `actor.org_id` only.

#### `app/drafting_templates/routes.py` (T013)

```python
@router.post("/{key}/playbook/standard", response_model=TemplateMutationResultOut)
def adopt_standard_playbook(key: str, payload: TemplateStandardAdopt, request: Request,
                            user=Depends(require_permission("playbook:publish")), db=Depends(get_db)):
    return standard.adopt(db, actor=user, key=key, note=payload.note, request_id=_rid(request))
```
Registered after the static paths, beside `PUT /{key}/playbook`.

#### `app/intake/agreement_forms.json` / `agreement_forms.py` / `intake/service.py` (T003, FR-3)

- JSON: `payment_terms` gains the `options_when` shown in the interface freeze. `options` unchanged.
- `agreement_forms.py`: `form_fields` carries `options_when` on the SimpleNamespace; new
  `options_for(field, values) -> list[dict] | None` (first matching entry's options as
  `[{"value","label"}]`, else `field.options`).
- `intake/service._coerce_field` select branch validates against `options_for(f, cleaned)`; an
  **unchanged stored answer** (`stored is not None and raw == stored.get(f.key)`) is kept, as the
  module already does for rules added after filing — so a filed MSA request holding "30 days" can
  still be edited. `_validate_field_values` passes `cleaned`/`stored` through (signature
  `_coerce_field(f, raw, values=None, stored=None)`).
- Record of the FR-3 choice per answer: MSA 30 → **no longer offered** (Q16); MSA 45, 60 → pass
  (rule preferred); MSA 90 → **no longer offered**; Consultancy same as MSA; Vendor/SaaS 30 → **no
  longer offered**; 45/60/90 → pass; Selling to a customer / Something else → unchanged (no
  template, FR-7). NDA governing law (5 options) → all pass (NDA rule accepts all five). Every
  other offered answer → passes (rules below written so).

### Template + rule pairs (the frozen content)

Conventions for every template:
- Text shown in quotes is the **exact** new wording; `{x}` are placeholders from `paper.FIELDS` or
  existing ones. Clauses not mentioned stay byte-for-byte as they are.
- **Standard signature block** — replaces everything from the `{company}   {counterparty}` line to
  the end of the execution block in all seven templates (DPA: before the annexes; the NDA and DPA
  lose their old "Address for notices:" row):

```
Signed for and on behalf of {company}

By: ______________________________
Name: {our_signatory_name}
Title: {our_signatory_title}
Date: ______________________________
Registered office and address for notices: {company_address}
Company number (CIN): ______________________________
GSTIN: ______________________________

Signed for and on behalf of {counterparty}

By: ______________________________
Name: {their_signatory_name}
Title: {their_signatory_title}
Date: ______________________________
Address for notices: {counterparty_address}
Company number: ______________________________
Tax number (GSTIN or equivalent): ______________________________
```
- **MSME line** (`MSME(P, C)` below): "If the {P} is a micro or small enterprise registered under
  the Micro, Small and Medium Enterprises Development Act, 2006, it shall state its Udyam
  registration number on each invoice and promptly notify the {C} of any change in that status, and
  the {C} shall pay each valid, undisputed invoice within the period stated above or forty-five (45)
  days from the day of acceptance of the goods or services invoiced, whichever is shorter."
- **Breach window** (`BREACH`): "without undue delay, and in any event within twenty-four (24) hours
  (and within six (6) hours where it must be reported to the Indian Computer Emergency Response Team
  (CERT-In)), after becoming aware of" — the NDA keeps "without undue delay" only (Q1).
- **Rule block format**: `clause_type` — CHANGED / NEW / KEPT · risk · approval (escalation) ·
  review items. `P` preferred · `F` fallback (— = none) · `R` required_language lines · `X`
  prohibited_language lines · `why` rationale · `pass` = wording that must give no finding for this
  rule · `flag` = counterparty wording that must give a finding (both asserted by the phrase check in
  `test_playbook_rules_014.py`, FR-52). KEPT = copy verbatim from the current library code (or, for
  the NDA, from `review/playbooks.json`), including its guidance/approval/escalation. Rule order in
  each list = storage order = review order. Every new or changed rule is built with the existing
  `rule()` helper in `library.py`.

---

#### 1. NDA — `defaults/nda.txt` + `library.NDA_STANDARD`

Template edits:
1. 1.2 → "1.2 "Representatives" means a Party's directors, officers and employees, its Affiliates and their directors, officers and employees, its professional advisers, and its contractors and service providers, in each case who need to know the Confidential Information for the Purpose and are bound by written (or professional) obligations of confidentiality no less protective than this Agreement." (T6)
2. 3.1 → "3.1 The obligations of confidentiality do not apply to information that the Receiving Party can show, by its written records: (a) is or becomes publicly available other than through a breach of this Agreement by the Receiving Party or its Representatives; (b) was rightfully known to the Receiving Party before disclosure, free of any obligation of confidence; (c) is rightfully received from a third party without restriction and without breach of any obligation of confidentiality owed to the Disclosing Party; or (d) is independently developed by or for the Receiving Party without use of the Confidential Information." (T4)
3. 3.2 → "3.2 The Receiving Party bears the burden of showing, by its written records, that an exclusion applies. A combination of elements is not excluded merely because individual elements of it are publicly available or in the Receiving Party's possession." (T4)
4. 3.3 → "3.3 No right to use information retained in the unaided memory of any person is granted under this Agreement." (duplicate first sentence removed)
5. 4.2 → "4.2 The Receiving Party shall protect Confidential Information using at least the degree of care it uses for its own confidential information of similar sensitivity, and no less than reasonable care." (T5)
6. 4.3 → "4.3 The Receiving Party shall not use Confidential Information to train, fine-tune, test, or otherwise develop any artificial intelligence or machine learning model, algorithm, or system. This does not prevent the Receiving Party from handling Confidential Information for the Purpose with business software and services (including AI-enabled tools) whose provider is bound by confidentiality and does not use the Confidential Information to train or improve any model." (Q9)
7. 4.4 → "4.4 The Receiving Party may disclose Confidential Information only to its Representatives, and is responsible for any act or omission of a Representative that would be a breach of this Agreement if done by the Receiving Party." (T6; consent step removed)
8. 4.6 → "4.6 The Receiving Party shall notify the Disclosing Party in writing promptly and without undue delay after becoming aware of any actual or suspected unauthorised access to, or use or disclosure of, Confidential Information, and shall cooperate with the Disclosing Party to regain possession of it and prevent any further unauthorised use or disclosure." (Q1)
9. 5.2 → "5.2 Where applicable law prohibits notice before disclosure, the Receiving Party shall give notice as soon as legally permissible. No notice is needed for a routine examination by a regulator or a requirement of a stock exchange that is not targeted at the Disclosing Party or its information, provided the Receiving Party asks for confidential treatment of any Confidential Information disclosed." (P7)
10. 6.1–6.3 → two clauses: "6.1 Within ten (10) business days of expiration or termination of this Agreement, or of the Disclosing Party's written request, the Receiving Party shall return or destroy all Confidential Information (including all copies, extracts and derivatives) held by it or its Representatives, and certify in writing, signed by an authorised representative, that it has done so." / "6.2 The Receiving Party may keep copies (a) that it is required to keep by applicable law or regulation, or (b) held in automated backup systems that are not accessible in the ordinary course of business and are overwritten in the normal backup cycle. Copies kept under this Section 6.2 remain subject to this Agreement for as long as they are kept, notwithstanding any expiry of it." (T8)
11. 7.3 → "7.3 Information that is a trade secret remains protected for so long as it remains a trade secret, or otherwise remains protectable as confidential information, under applicable law." (T9)
12. 7.4 → "7.4 Subject to Sections 7.2 and 7.3, Sections 2 to 6 and 8 to 11, and any other provision which by its nature is intended to survive, survive expiration or termination of this Agreement." (T10)
13. 8.1–8.4 → "8.1 Each Party acknowledges that a breach or threatened breach of this Agreement may cause the Disclosing Party irreparable harm for which damages would not be an adequate remedy, and that the Disclosing Party may seek injunctive or other equitable relief, to the extent permitted by applicable law without the requirement to post a bond or other security, in addition to any other remedy available at law or in equity. The rights and remedies under this Agreement are cumulative." / "8.2" = old 8.3 verbatim. Old 8.2 and 8.4 deleted. (T12, Q9)
14. Section 9 → "9.1 This Agreement and any dispute arising out of or in connection with it are governed by the laws of {governing_law}, without regard to its conflict of laws principles." / "9.2 {dispute_resolution}". Old 9.2 and 9.3 (jury waiver) deleted. (T1, T2, Q2)
15. New 10.4: "10.4 Nothing in this Agreement restricts either Party from independently developing or acquiring products or services similar to those discussed, provided it does so without use of the other Party's Confidential Information." (T14a)
16. 11.1 → "11.1 Personal Data. {nda_personal_data}" (T13, FR-29)
17. 11.4 → "… to the address for notices of the receiving Party set out in the signature blocks below or such other address as a Party notifies in writing. A notice is effective on receipt." (rest unchanged)
18. 11.5 → "11.5 Assignment. Neither Party may assign, transfer or delegate this Agreement without the prior written consent of the other Party, except to an Affiliate or to a successor to all or substantially all of its business, on written notice to the other Party. Any purported assignment in breach of this Section is void. This Agreement binds and benefits the Parties and their permitted successors and assigns." (T14b)
19. 11.10 second sentence → "Signatures delivered electronically or by an electronic signature service, including electronic signatures under the Information Technology Act, 2000, are binding as originals." (T14d)
20. New 11.11: "11.11 Costs and Stamp Duty. Each Party bears its own costs in connection with this Agreement. Any stamp duty payable on this Agreement shall be borne by {company}." (Q6)
21. Standard signature block.
Unchanged by decision: Recital A `{purpose_phrase}` ("explore a potential business relationship", Q14); 7.2 `{survival_clause}` (5 years); 2.1–2.3.
Migration note: "Standard updated: Indian law by default and courts at our registered city; market exclusions, care and representatives; 10-day return with backups; trade secrets protected while secret; DPDP sentence; AI tools exception; assignment, e-signature and stamp duty."

Rules — `NDA_STANDARD = {"name": "Corporate NDA Negotiation Playbook v1.0", "rules": [...]}` (not seeded into new orgs; `test_ndas_are_left_to_the_orgs_own_nda_playbook` stays true):
- `definition_of_confidential_information` — KEPT (verbatim from review/playbooks.json).
- `exclusions_from_confidential_information` — CHANGED · medium · no · P2, T4
  P: "Standard four exclusions only: public through no fault of the receiving party, already known, received from a third party without restriction, or independently developed without use of the information; each shown by the receiving party's written records, with the burden on the receiving party; a combination is not excluded merely because its elements are." F: — R: "written records" X: —
  why: "Exclusions must be provable but not set at a standard that binds us unreasonably when we receive under a mutual NDA."
  pass: "…can show, by its written records: (a) is or becomes publicly available…" · flag: "Information is excluded if the Receiving Party believes it was already known to it."
- `permitted_use_and_standard_of_care` — CHANGED · high · no · P3, T5, T6, Q9
  P: "Use only for the Purpose; at least reasonable care and no less than the care used for the receiving party's own similar information; disclosure only to Representatives (directors, officers, employees, Affiliates, professional advisers, contractors and service providers) who need to know and are bound by written or professional confidentiality, the receiving party being liable for them; no use to train or develop AI or machine learning models, except ordinary business tools whose provider does not train on the information." F: — R: "train, fine-tune, test, or otherwise develop any artificial intelligence or machine learning model" X: "for any purpose whatsoever"
  why: "Information must be used only for the deal, protected to a market standard, and never fed into model training."
  pass: edit 6 text · flag: "The Recipient may use the Confidential Information for any purpose whatsoever."
- `compelled_disclosure_and_legal_process` — CHANGED · medium · no · P7
  P: kept text + " No notice is needed for a routine examination by a regulator or a requirement of a stock exchange that is not targeted at the disclosing party, provided confidential treatment is requested." F: "If notice before disclosure is legally prohibited, notice as soon as legally permitted after disclosure, keeping the cooperation and minimum-disclosure obligations." R: (KEPT: the 5.1 sentence) X: "deemed a waiver of all confidentiality obligations"
  why: "We must be able to protect our information when a court or authority demands it, without breaching on routine regulator visits."
  pass: 5.1 + edit 9 · flag: "Any compelled disclosure is deemed a waiver of all confidentiality obligations under this Agreement."
- `return_or_destruction_of_information` — CHANGED · medium · no · P4, T8
  P: "Within 10 business days of the end of the agreement or a request, return or destroy all confidential information with written certification by an authorised representative; copies required by law, or held in automated backups not accessible in the ordinary course and overwritten in the normal cycle, may be kept and stay confidential." F: "A 30-day return or destruction window, with certification." R: "ten (10) business days" X: "internal business purposes"
  why: "Our information must not outlive the purpose, but backup deletion on demand is not achievable."
  pass: edits 10 · flag: "The Recipient may retain copies for its internal business purposes."
- `term_and_duration_of_confidentiality` — CHANGED · high · no · P1, T9, T10, T11, both NDA redlines
  P: "An agreement term of 2 to 5 years, with confidentiality surviving at least 3 years after the term ends (5 years is best), and trade secrets protected for as long as they remain trade secrets. The term and the survival period are separate: a 2-year term with 5 years' survival meets this position." F: "A 1-year term with at least 3 years' survival and trade secrets protected while secret." R: "for so long as it remains a trade secret" X: "expire upon termination of this Agreement" / "expire upon expiration or termination of this Agreement" / "terminate upon termination of this Agreement"
  why: "Protection must last long enough after the deal ends; trade secrets must never lapse."
  pass: "This Agreement commences … and continues for two (2) years …; obligations … shall continue for five (5) years after …" · flag: "All obligations under this Agreement expire upon termination of this Agreement."
- `governing_law_and_jurisdiction` — CHANGED · medium · no · P5, T1, T2, T3, Q2
  P: "Indian law by default, or the law of England and Wales, Singapore, New York or Delaware where chosen for the transaction; the exclusive jurisdiction of the courts at a named city (our registered city, or the city of our registered office), with either party free to seek urgent interim relief from any competent court." F: "Arbitration (MCIA, or SIAC seated in Singapore for a foreign party) with a carve-out for urgent court relief, with Legal approval." R: "courts at" X: "exclusive jurisdiction of the courts of"
  why: "A dispute forum must be named precisely and allow urgent relief."
  pass: NDA `dispute_resolution` · flag: "Each party submits to the exclusive jurisdiction of the courts of New York."
- `remedies_and_injunctive_relief` — CHANGED · high · no · P6, T12, Q9
  P: "Mutual: a breach may cause irreparable harm; either party may seek injunctive or equitable relief, to the extent permitted by law without posting a bond, in addition to other remedies; no fee-shifting to the winning party." F: "Nominal security as the court may require, keeping mutual injunctive relief." R: "injunctive" X: "limited to monetary damages"
  why: "Only an injunction stops a leak in time."
  pass: edit 13 · flag: "Remedies for breach are limited to monetary damages."
- `non_compete_and_exclusivity` — NEW · high · yes (Legal Counsel) · P9, FR-31
  P: "No non-compete, exclusivity or standstill in an NDA." F: — R: — X: "shall not compete" / "non-compete" / "exclusivity"
  why: "An NDA must not restrict our business; such terms belong in a negotiated commercial agreement."
  pass: full NDA · flag: "During the term the Recipient shall not compete with the Discloser in India."
- `indemnity` — NEW · high · yes (Legal Counsel) · P9, FR-31
  P: "No indemnity in an NDA; remedies are damages and injunctive relief." F: — R: — X: "indemnify" / "hold harmless"
  why: "An indemnity turns a confidentiality promise into uncapped insurance."
  pass: full NDA · flag: "The Recipient shall indemnify and hold harmless the Discloser against all losses."
- `residuals` — NEW · high · no · P9, FR-31
  P: "No residuals clause: information kept in memory is still confidential." F: — R: — X: "residuals"
  why: "A residuals clause lets the other side use our information freely once remembered."
  pass: edit 4 · flag: "Either party may use Residuals, meaning information retained in unaided memory."
- `personal_data` — NEW · medium · no · T13, FR-29
  P: "Personal data in confidential information is handled under the DPDP Act 2023 with reasonable security safeguards; where the request says personal data is shared, breaches are notified without undue delay; where none is expected, compliance with data protection law is enough." F: — R: "Digital Personal Data Protection Act, 2023" X: —
  why: "Personal data has statutory protection that an NDA must respect."
  pass: either `nda_personal_data` · flag: "Personal data shall be handled in accordance with applicable law."
- `assignment` — NEW · low · no · T14b
  P: "No assignment without consent, except to an affiliate or a successor to the business on notice." F: — R: "successor" X: —
  why: "We must be able to reorganise without the counterparty's consent."
  pass: edit 18 · flag: "Neither party may assign this Agreement without consent."
- Notes: Q14 — the purpose stays generic, no rule change; P8 — the AI/ML definition stays (Q9).

#### 2. MSA — `defaults/msa.txt` + library "Master Services Agreement Playbook"

Template edits:
1. 2.3 → "2.3 If there is any conflict between this Agreement and a Statement of Work, this Agreement prevails, except that (a) the description of the Services, the Deliverables and the Fees in a Statement of Work prevails for that Statement of Work, and (b) a Statement of Work may vary another provision of this Agreement where it expressly identifies that provision and states that it does so for that Statement of Work only." (M11)
2. 3.3 ends "…without the Customer's prior written consent.{gxp_clause}" (M17)
3. New 5.5: "5.5 Where a Statement of Work sets Service Levels but no service credits, the service credit for each failure to meet a Service Level in a month is five percent (5%) of the monthly Fees for the affected Services. Total service credits in any month shall not exceed fifteen percent (15%) of the monthly Fees under that Statement of Work. Where a Statement of Work sets no Service Levels (for example, a Statement of Work for project Deliverables), Clauses 5.1 to 5.5 apply only to Service Levels later agreed by a signed change order, and the absence of Service Levels is not a breach." (M4, Q4)
4. New 6.3A: "6.3A " + MSME("Provider","Customer") with "the period stated above" → "the period in Clause 6.3". (M2, FR-19)
5. 6.6 → "6.6 The Fees exclude goods and services tax ("GST"). The Provider shall be registered for GST, state its GSTIN on each invoice, issue tax invoices that comply with the Central Goods and Services Tax Act, 2017, report each invoice correctly in its returns and pay the tax collected, so that the Customer can claim input tax credit. The Provider shall reimburse any input tax credit the Customer is denied or must reverse because of the Provider's default, with any interest and penalty, and the Customer may set that amount off against sums due to the Provider. Where the Services are imported, the Customer shall pay GST under reverse charge and the Fees shall not be increased. The Customer may deduct any tax it is required by law to withhold and shall provide the corresponding certificate. A reduced rate of withholding under a tax treaty applies only once the Provider has given a valid tax residency certificate, Form 10F and any other document the law requires. The Fees are payable in {fee_currency} unless a Statement of Work states another currency." (M3, M16, FR-20, FR-21)
6. 8.4 → "8.4 For up to ninety (90) days after termination or expiry, the Provider shall provide the reasonable assistance the Customer requests to transfer the Services to the Customer or a replacement provider, at the rates in the applicable Statement of Work or, if it sets none, at the Provider's then-current rates less any discount in that Statement of Work, and at no charge where the Customer terminates under Clause 8.2 or Clause 5.4." (M10)
7. 8.5 survival list → "Clauses 6, 8.3 to 8.6, 9, 10, 11, 12, 13, 14, 15.2, 16.2, 16.3, 17.2, 19, 20 and any other provisions …" (M18)
8. New 8.6: "8.6 The Customer may terminate this Agreement or any Statement of Work on written notice if the Provider undergoes a change of control in favour of a competitor of the Customer or of a person subject to sanctions." (M15)
9. 10.2 → "10.2 {dpa_status}" (FR-24)
10. 10.4 → "10.4 The Provider shall notify the Customer " + BREACH + " any personal data breach affecting Personal Data processed under this Agreement, and shall provide all information and assistance the Customer reasonably requires to investigate, mitigate and report the breach." (M7, Q1)
11. New 10.8: "10.8 The Provider shall maintain an information security programme consistent with ISO/IEC 27001 or an equivalent standard, provide its current certificate or SOC 2 report on request, remediate critical vulnerabilities promptly, and cooperate with the Customer in meeting any reporting obligation the Customer has to the Indian Computer Emergency Response Team (CERT-In)." (M8)
12. 11.1 append: " The assignment is worldwide and for the full term of the Intellectual Property Rights, including all renewals and extensions. To the extent permitted by law, sections 19(4) and 19A of the Copyright Act, 1957 do not apply to it, and it shall not lapse or revert to the Provider for non-exercise of any right within any period." (M6)
13. 12.1(a) → "(a) the Provider's breach of Clause 9 (Confidentiality) or of Applicable Law;" (M13, Q5 narrowed)
14. 13.1 → "13.1 Subject to Clauses 13.3 and 13.4, each Party's total aggregate liability arising out of or in connection with this Agreement, whether in contract, tort (including negligence), breach of statutory duty or otherwise, shall not exceed the greater of (a) the total Fees paid or payable under this Agreement in the twelve (12) months immediately preceding the event giving rise to the claim and (b) the total Fees payable under this Agreement for the first twelve (12) months after the Effective Date." (M12, Q5)
15. 13.3(b) → "(b) the Provider's indemnification obligations under Clause 12, including its indemnity for infringement of third-party Intellectual Property Rights under Clause 12.1(c);" (redline 2026-10-27)
16. 14.1 → "14.1 The Provider shall, at its own cost, maintain with reputable insurers during the term of this Agreement and for one (1) year after its termination or expiry: (a) commercial general liability insurance; (b) professional indemnity (errors and omissions) insurance with a limit of not less than {insurance_minimum} per claim and in the aggregate, or any higher amount specified in a Statement of Work; and (c) where it processes Personal Data or has access to the Customer's systems, cyber liability insurance with a limit of not less than {insurance_minimum} per claim, together with any other insurance required by Applicable Law." (M5, Q3)
17. New 16.3: "16.3 On reasonable notice, and not more than once a year except after a security incident or at a regulator's request, the Customer or its auditors may also audit the Provider's security and compliance controls relevant to the Services." (M9)
18. New 17.2: "17.2 The Provider shall maintain, test at least once a year and share on request a business continuity and disaster recovery plan for the Services." (M8)
19. 18.1 "…to the address of the receiving Party set out in this Agreement…" → "…to the address for notices of the receiving Party set out in its signature block below…" (M14)
20. 19.1 → "19.1 This Agreement and any dispute or claim arising out of or in connection with it (including non-contractual disputes) are governed by the laws of {governing_law}." 19.3 → "19.3 {dispute_resolution}" (M1, Q2)
21. Standard signature block.
Migration note: "Standard updated: Indian law with arbitration at our registered city; MSME and GST terms; default service credits; INR 5 crore insurance; 24-hour breach notice; security, continuity and audit; copyright for the full term; greater-of liability cap."

Rules (11):
- `limitation_of_liability` — CHANGED · high · yes · M12, M13, Q5, redline 2026-10-27 ×2
  P: "Each party's total liability is capped at the greater of the fees paid or payable in the 12 months before the claim and the fees payable for the first 12 months ("fees paid or payable in the preceding 12 months" meets this position). Indirect and consequential losses are excluded. The cap does not apply to breach of confidentiality or data protection, the provider's indemnities (including for IP infringement), gross negligence, wilful misconduct, fraud, or death or personal injury." F: "Up to 2x the annual fees; anything higher needs Legal Counsel approval." R: "limitation of liability" / "paid or payable" X: "Customer shall have unlimited liability" / "Customer's liability shall be unlimited"
  why: KEPT · pass: edit 14 · flag: "The Customer's liability shall be unlimited, and the Provider's shall not exceed the fees paid in the preceding month."
- `indemnification` — CHANGED · high · no · M13, Q5
  P: "The provider defends and indemnifies us against third-party claims arising from its breach of confidentiality, data protection or law, its negligence, wilful misconduct or fraud, or infringement of third-party IP." F/R/why: KEPT. X: —
  pass: 12.1 · flag: "The Customer shall defend the Provider against any claim." (lacks "indemnify")
- `intellectual_property` — CHANGED · high · no · M6
  P: "Deliverables created for us belong to us on payment, assigned worldwide for the full term of the rights with no lapse for non-use (sections 19(4) and 19A of the Copyright Act do not apply). The provider keeps its pre-existing IP and grants us a perpetual, royalty-free licence to use it within the deliverables." F: KEPT. R: "intellectual property" / "full term" X: —
  pass: edit 12 · flag: "The Provider assigns the copyright in the Deliverables to the Customer."
- `payment_terms` — CHANGED · medium · no · M2, redlines ×3, Q16
  P: "Payment within 45 or 60 days of receipt of a valid, undisputed invoice, with disputed amounts withheld in good faith. A supplier registered as a micro or small enterprise under the MSMED Act, 2006 is paid within 45 days at most whatever period is stated, and the contract says so. Any advance or mobilisation payment is no more than 20% of the fees and is credited against later invoices." F: "90 days for a supplier that is not a micro or small enterprise, with Finance approval." R: "undisputed invoice" / "Micro, Small and Medium Enterprises Development Act, 2006" X: —
  why: "Protects cash flow and keeps leverage until the work is delivered, within the MSMED Act's 45-day limit for small suppliers."
  pass: 6.3 + 6.3A · flag: "The Customer shall pay invoices within 90 days."
- `termination` — KEPT. `confidentiality` — KEPT.
- `insurance` — CHANGED · medium · no · M5, Q3, redline 2026-10-27
  P: "The provider holds professional indemnity insurance of at least INR 5 crore per claim (or the equivalent in the currency of the fees), cyber liability insurance of at least the same amount where it handles personal data or our systems, and general liability cover, for the term and 1 year after, with certificates on request." F: "Certificates on request instead of named-insured status." R: "per claim" X: "limits of cover appropriate to"
  why: "Insurance stands behind the provider's indemnities only if the amount is stated."
  pass: edit 16 · flag: "The Provider shall maintain insurance with limits of cover appropriate to the Services."
- `service_levels` — CHANGED · medium · no · M4, Q4, redlines ×4 + check
  P: "Service levels and credits are set in each SOW. Where an SOW sets service levels without credits, the MSA's default applies: 5% of the month's fees for the affected services per failure, capped at 15% of the month's fees under that SOW, with termination for repeated or critical failure. An SOW for project deliverables with no service levels needs none, and the MSA is not flagged for it." F: "Credits capped at 10% of monthly fees with a termination right for repeated failure." R: "fifteen percent (15%)" X: "sole and exclusive remedy"
  why: "Without measurable service levels there is no remedy short of termination."
  pass: edit 3 · flag: "Service credits are the Customer's sole and exclusive remedy for any failure to meet a Service Level."
- `data_protection` — CHANGED · high · yes (Compliance) · M7, Q1, redline 2026-10-23
  P: "Where personal data is processed: the provider complies with the DPDP Act 2023 (and the GDPR where it applies), a DPA on our template applies from the start, and the provider tells us of a personal data breach within 24 hours (6 hours where a CERT-In report is required). Where no personal data is expected, the contract says so and requires our DPA before any processing starts; that meets this position and is not a deviation." F: "The counterparty's DPA if it meets the same standards, including the 24-hour window; Privacy review required." R: "Digital Personal Data Protection Act, 2023" / "twenty-four (24) hours" X: "within seventy-two (72) hours" / "within 72 hours" / "within forty-eight (48) hours" / "within 48 hours"
  why: "We have our own short statutory notice deadlines (DPDP Rules, CERT-In)."
  pass: 10.1 + 10.4 · flag: "The Provider shall notify the Customer within 72 hours of a personal data breach."
- `governing_law` — NEW · high · yes (Legal Counsel) · M1, Q2
  P: "Indian law. Disputes go to arbitration under the Arbitration and Conciliation Act, 1996 by a sole arbitrator seated in a named Indian city (an Indian institution such as MCIA where the provider is Indian), or, for a provider outside India, under the SIAC Rules seated in Singapore; either party may seek urgent interim relief from any competent court. A seat stated as the city of our registered office is a named seat." F: "English law with SIAC arbitration seated in Singapore for a provider outside India, with Legal Counsel approval." R: "laws of India" X: "exclusive jurisdiction of the courts of" / "laws of the State of Delaware" / "laws of the State of New York"
  why: "An award is enforceable abroad under the New York Convention; a court judgment against a foreign provider often is not."
  pass: 19.1 + `dispute_resolution` · flag: "This Agreement is governed by the laws of the State of Delaware and the parties submit to the exclusive jurisdiction of the courts of Delaware."
- `tax_compliance` — NEW · medium · no · M3, FR-20
  P: "Fees exclude GST; the supplier issues compliant tax invoices with its GSTIN, reports them and pays the tax so we keep our input tax credit, and reimburses credit lost through its default; GST on imported services is paid by us under reverse charge without increasing the fees; tax is withheld as the law requires with certificates given; a reduced treaty rate applies only on a tax residency certificate and the forms the law requires." F: — R: "input tax credit" / "reverse charge" X: —
  why: "A supplier's GST default otherwise costs us the credit."
  pass: edit 5 · flag: "Fees are exclusive of taxes, which the Customer shall pay."

#### 3. SOW — `defaults/sow.txt` + `intake/sow.py` + library "Statement of Work Playbook"

`intake/sow.py` changes (all fields keep being produced; two new, built here and exposed via `paper`):
- `pricing_terms(fv)`:
  - Fixed fee: "8.1 Fixed fee. The total fee for the Services and Deliverables is {fee_amount}, inclusive of all Provider costs and exclusive only of applicable taxes, payable in accordance with Clause 9.1 after the Customer's written acceptance of the Deliverables.\n\n8.2 Rates and expenses. No rates apply to this SOW, and the fixed fee includes all expenses. Any change is priced in the Change Order that makes it."
  - Milestones: "8.1 Fixed fee payable in instalments. The total fee for the Services and Deliverables is {fee_amount}, inclusive of all Provider costs and exclusive only of applicable taxes, payable in the instalments set out in Clause 9.1, each only after the Customer has accepted in writing the Deliverable it is linked to.\n\n8.2 Rates and expenses." + same as fixed.
  - Time and materials: "8.1 Time and materials. The Provider shall charge for time actually and properly worked, supported by timesheets approved by the Customer. Total charges under this SOW shall not exceed the not-to-exceed amount of {fee_amount}. The Provider shall notify the Customer in writing when seventy-five percent (75%) of that amount has been reached, and the Customer is not liable for any charges above it unless it is increased by a signed Change Order.\n\n8.2 Rates and expenses. The Provider's rates are those set out in the Master Agreement and are fixed for the term of this SOW. Where the Master Agreement sets no rates, no time may be charged until rates are agreed in a signed Change Order. Expenses are payable only where approved by the Customer in writing in advance, at cost and in accordance with the Customer's expenses policy, and count towards the not-to-exceed amount." (Q19)
  - No pricing or no value: `""` (the draft is held).
- `sow_payment(fv)`: Fixed fee / Milestones with rows and total → "9.1 Payment under this SOW is tied to the Customer's written acceptance of Deliverables, not to calendar dates. The Provider may invoice each payment below only after the Customer has accepted in writing the Deliverable it is linked to under Clause 6:\n\n" + the existing milestone entries; T&M → "9.1 The Provider shall invoice monthly in arrears for time properly worked under Clause 8.1, and the Customer may hold back ten percent (10%) of each invoice until it has accepted all Deliverables in writing. No milestone payments apply."; else `""`. Add `"sow_payment"` to `ROW_FIELDS`.
- `provider_representative` with no signer → "the individual the Provider notifies to the Customer in writing" (FR-39); with signer unchanged.
- `EXAMPLE_VALUES` / markers regenerate from the same answers (no new example content).

Template edits:
1. Preamble "…, under the Master Agreement between the Parties (the "Master Agreement")." → "…, under {parent_agreement} (the "Master Agreement")." (S3, FR-37)
2. New 4.7: "4.7 Service Levels. No Service Levels apply to this SOW, which is for project Deliverables. A Service Level may be added only by a signed Change Order, and the Master Agreement's service-credit terms then apply to it." (S7, FR-38)
3. Section 8 body (old 8.1–8.5) → "{pricing_terms}" (S1, S2, S6, FR-35, FR-36)
4. Section 9 heading "9. PAYMENT"; body (old 9.1–9.3) → "{sow_payment}\n\n9.2 The Customer shall pay valid, undisputed invoices within the period set out in the Master Agreement, and may withhold any amount it disputes in good faith in accordance with the Master Agreement. Each invoice must reference this SOW and the Customer's purchase order number." (S5, FR-37)
5. 11.1 → "11.1 This SOW may be terminated in accordance with the Master Agreement. On termination, the Customer shall pay only for Deliverables accepted, and for Services charged under Clause 8.1 that were properly performed, up to the date of termination, and the Provider shall deliver all Deliverables, whether complete or in progress, to the Customer."
6. 12.1 → "12.1 All other terms of the Master Agreement, including those on confidentiality, data protection, intellectual property, indemnities, limitation of liability and insurance, apply to this SOW. This SOW is governed by the law, and disputes under it are resolved in the manner, set out in the Master Agreement." (S4, FR-15)
7. New 12.3: "12.3 Where the Provider processes personal data under this SOW, the data processing agreement under the Master Agreement applies, and no personal data is processed before it is signed." (S11)
8. Standard signature block.
Migration note: "Standard updated: names the master agreement; prints only the chosen pricing model; rates and payment period follow the master agreement; law follows the master agreement; service levels stated."

Rules (10):
- `scope_and_deliverables` — CHANGED · high · no · S9
  P: KEPT text + " The scope describes the real work in words, not placeholder text." F/R/X: — (scope placeholder text is refused before drafting)
  pass: example 4.2 · flag: (AI-only — no phrase; covered by the FR-25 hold test)
- `acceptance` — KEPT (prohibited "deemed accepted" stays; 6.2 does not contain it).
- `fees_and_pricing` — CHANGED · medium · yes (Finance) · S1, S2, redlines ×2
  P: "One pricing model, stated with its amount: a fixed fee (lump sum or instalments tied to accepted milestones) with no rates and changes priced by change order; or time and materials at rates in the SOW or the master agreement (or, where neither sets rates, agreed in a signed change order before any charge) under a not-to-exceed cap the provider may not pass without a signed change order." F: KEPT ("Rates up to 5% above the master rate card with Finance approval.") R: — X: "rates may be increased"
  pass: each `pricing_terms` · flag: "The Provider's rates may be increased by up to 10% each quarter."
- `milestone_payments` — CHANGED · medium · no · B2
  P: "For a fixed-fee or milestone SOW, payments are tied to accepted deliverables, not to dates. A time-and-materials SOW is invoiced monthly in arrears for time worked, with a hold-back until acceptance, and needs no milestones." F: — R: — X: "payable on signature of this SOW"
  pass: each `sow_payment` · flag: "50% of the fee is payable on signature of this SOW."
- `change_control`, `order_of_precedence`, `key_personnel` — KEPT.
- `service_levels` — NEW · medium · no · S7, FR-34
  P: "An SOW for ongoing services sets measurable service levels with credits (or the master agreement's default credits apply); an SOW for project deliverables states that no service levels apply, any being added only by signed change order." F: — R: "Service Level" X: —
  pass: edit 2 · flag: "The Provider will use reasonable efforts to keep the platform available."
- `payment_terms` — NEW · medium · no · S5
  P: "Payment follows the master agreement's payment period; the SOW does not state its own." F: — R: "period set out in the Master Agreement" X: —
  pass: edit 4 · flag: "The Customer shall pay invoices within 30 days."
- `governing_law` — NEW · medium · no · S4, FR-15
  P: "The SOW follows the law and dispute resolution of the master agreement and does not state its own." F: — R: "set out in the Master Agreement" X: "This SOW is governed by the laws of"
  pass: edit 6 · flag: "This SOW is governed by the laws of England."

#### 4. Vendor — `defaults/vendor.txt` + library "Vendor Agreement Playbook"

Template edits:
1. Background → "The Company wishes to purchase, and the Vendor wishes to supply, certain {supply_description} on the terms of this Agreement." (FR-41)
2. "Deliverables" definition → "means the {supply_description}, and any documents, materials or other output, supplied or to be supplied by the Vendor under an Order;" — "Specification" definition: delete ", including any service levels".
3. New 2.4: "2.4 If a Deliverable is not delivered or completed by its agreed date for a reason other than the Company's default or force majeure, the Vendor shall pay the Company, as liquidated damages and a genuine pre-estimate of loss, one half of one percent (0.5%) of the Fees for the late Deliverable for each week or part week of delay, up to ten percent (10%) of those Fees. If the delay exceeds thirty (30) days, the Company may also terminate the affected Order by written notice without liability and recover any Fees paid for the undelivered Deliverables." (Q10)
4. Section 3 → "3.1 The Company may inspect and test the Deliverables within thirty (30) days of delivery or completion. A Deliverable is accepted only when the Company confirms acceptance in writing; use, payment or the passing of time is not acceptance. The Company may reject any Deliverable that does not conform to the Specification or this Agreement, and the Vendor shall, at the Company's option and at the Vendor's cost, promptly repair, replace or re-perform it, or refund the Fees paid for it." / "3.2" = old 3.3 + "{vendor_supply_terms}" at its end. Old 3.1 (packing, title/risk) moves into the goods block. (FR-40, Q10)
5. 4.1 → "4.1 In consideration of the supply of the Deliverables, the Company shall pay the Fees set out in the applicable Order. The Fees are fixed for the term of each Order, include all of the Vendor's costs of supplying the Deliverables, and are stated and payable in {fee_currency}.{value_sentence}"
6. 4.5 append: " A reduced rate of withholding under a tax treaty applies only once the Vendor has given a valid tax residency certificate, Form 10F and any other document the law requires."
7. New 4.6 (GST): "4.6 The Fees exclude goods and services tax ("GST"), which the Vendor shall show separately on each tax invoice. The Vendor shall issue tax invoices (including e-invoices where required) that comply with GST law and state its GSTIN, report each invoice correctly in its GST returns and pay the tax collected, so that the Company can claim input tax credit. If the Company is denied input tax credit, or must reverse it, because the Vendor failed to do so, the Vendor shall reimburse it with any interest and penalty, and the Company may withhold or set off that amount. Where GST on any supply is payable by the Company under reverse charge, the Company shall pay it and the Fees shall not be increased."
8. New 4.7: "4.7 " + MSME("Vendor","Company") with "the period stated above" → "the period in Clause 4.3".
9. 5.1(d) and 5.2 deleted (now in the services block); old 5.3 → 5.2, 5.4 → 5.3; 5.1(c) ends "; and" → "." adjusted. Heading "5. WARRANTIES".
10. New 6.5: "6.5 The Vendor is solely responsible for the wages, benefits and statutory dues of its personnel and of its subcontractors' personnel, including provident fund, employees' state insurance, gratuity and bonus, shall comply with the Contract Labour (Regulation and Abolition) Act, 1970 or any labour code that replaces it, and shall give the Company evidence of payment of those dues on request."
11. 7.2 → "7.2 {dpa_status}"; 7.4 → "7.4 The Vendor shall notify the Company " + BREACH + " any personal data breach affecting the Company's personal data, and shall provide all information and assistance the Company reasonably requires to investigate, mitigate and report it."
12. 10.1: (c) ends "; ", (d) ends "; or", new "(e) any claim by the Vendor's personnel or subcontractors, or by any authority, for wages, benefits or statutory dues, or that the Company is their employer."
13. 11.1 → "…shall not exceed the greater of (a) the total Fees paid and payable under this Agreement in the twelve (12) months preceding the event giving rise to the claim and (b) the total Fees payable under all Orders in the first twelve (12) months of this Agreement." (Q5)
14. 12.1 → "12.1 {vendor_insurance}" (Q3)
15. 15.5 survival list adds "2.4" and "6.5": "…including clauses 2.4, 5, 6.5, 7, 8, 9, 10, 11, 14, 17 and 18."
16. 17.1 "…set out in the applicable Order…" → "…set out in its signature block below…".
17. 18.2 → "18.2 The Parties shall first try to resolve any dispute through good-faith negotiation between senior representatives within thirty (30) days of written notice of the dispute. {dispute_resolution}"
18. Standard signature block.
Migration note: "Standard updated: supply clauses follow goods or services; Indian law with arbitration; GST, MSME and statutory dues; delay damages; greater-of liability cap; insurance minimums; 24-hour breach notice."

Rules (12):
- `limitation_of_liability` — CHANGED · high · yes · A1.7, Q5
  P: "Each party's total liability is capped at the greater of the fees paid and payable in the 12 months before the claim and the fees payable under all orders in the first 12 months. Indirect losses are excluded. Uncapped: breach of confidentiality, the vendor's indemnities (defective goods, breach of law including data protection, negligence, wilful misconduct or fraud, IP infringement, claims by its personnel), gross negligence, wilful misconduct, fraud, and death or personal injury." F: "A super-cap of at least 3x annual fees for the vendor's indemnities for defective goods and negligence, with IP and data protection indemnities uncapped, with Legal Counsel approval." R: "limitation of liability" / "first twelve (12) months" X: "unlimited liability"
  pass: edit 13 + 11.3 · flag: "The Company shall have unlimited liability under this Agreement."
- `indemnification` — CHANGED · high · no · A1.4
  P: "The vendor defends and indemnifies us against third-party claims arising from defective goods, its breach of law, negligence or infringement of third-party IP, and claims by its personnel for wages or statutory dues." F/R: KEPT. X: —
  pass: edit 12 · flag: "Each party shall bear its own losses." (lacks "indemnify")
- `warranties` — CHANGED · medium · no · A2
  P: "The vendor warrants that what it supplies conforms to the specification and is free from defects for at least 12 months; services are performed with reasonable skill and care and meet any service levels an order sets." F: KEPT. R: "warrants" X: —
  pass: 5.1 · flag: "The goods are supplied as is."
- `compliance` — CHANGED · high · yes (Compliance) · A2
  P: KEPT text + " It is solely responsible for the wages and statutory dues of its personnel." R: "anti-bribery" X: —
  pass: 6.1(a) · flag: "The Vendor shall comply with law." (lacks the phrase)
- `payment_terms` — CHANGED · medium · no · A1.2, A2, Q16
  P: "Payment within 45, 60 or 90 days of receipt of a valid, undisputed invoice, with disputed amounts withheld in good faith. A supplier registered as a micro or small enterprise under the MSMED Act, 2006 is paid within 45 days at most whatever period is stated, and the contract says so. Any advance is no more than 20% of the order's fees." F: "30 days for a strategic supplier that is not a micro or small enterprise, with Finance approval." R: "undisputed invoice" / "Micro, Small and Medium Enterprises Development Act, 2006" X: —
  why: as MSA · pass: 4.3 + 4.7 · flag: "Payment is due within 120 days of invoice."
- `subcontracting`, `audit_rights` — KEPT.
- `data_protection` — CHANGED · as MSA `data_protection` (same P/F/R/X/why), `approval` yes (Compliance).
  pass: 7.1/7.4 · flag: "The Vendor shall notify the Company within forty-eight (48) hours of any breach."
- `delivery_and_acceptance` — NEW · medium · no · A2, Q10, FR-40
  P: "Goods are delivered DAP (Incoterms 2020) to the named place, with title and risk passing on our written acceptance; short or excess quantities may be rejected; the vendor runs recalls at its cost. Services are accepted only on our written confirmation. Late delivery costs 0.5% of the late item's fees per week, capped at 10%, and we may end the order after 30 days' delay. On termination for convenience we pay only for accepted deliverables." F: — R: "confirms acceptance in writing" X: "deemed accepted"
  pass: edit 4 · flag: "Goods are deemed accepted on delivery."
- `insurance` — NEW · medium · no · A1.10, Q3
  P: "A goods supplier holds product and public liability insurance of at least the greater of the contract value and INR 1 crore per claim; a services supplier holds professional indemnity insurance of at least INR 5 crore per claim; cyber liability of at least INR 5 crore where it handles personal data or our systems (or the equivalent in the currency of the fees); for the term and 1 year after, with evidence on request." F: — R: "per claim" X: "limits of cover appropriate to" / "insurance adequate to cover its liabilities"
  pass: any `vendor_insurance` · flag: "The Vendor shall maintain insurance adequate to cover its liabilities."
- `governing_law` — NEW · as MSA `governing_law`.
- `tax_compliance` — NEW · as MSA `tax_compliance`; pass: edit 7.

#### 5. SaaS — `defaults/saas.txt` + library "Software / SaaS Agreement Playbook"

Template edits:
1. 4.1 → "…which shall be shown separately on each valid tax invoice. The Fees are stated and payable in {fee_currency}.{value_sentence}"
2. 4.2 → "4.2 The Provider shall invoice the Fees quarterly in arrears unless the Order Form states another billing period. Each invoice must quote the Order Form reference." (Q13)
3. 4.4 append the treaty sentence (as Vendor edit 6, "Provider").
4. New 4.5: "4.5 The Provider may suspend the Service only (a) to the extent and for as long as strictly necessary to address an imminent threat to the security of the Service, with prompt notice to the Customer giving the reasons, or (b) where undisputed Fees are more than thirty (30) days overdue and remain unpaid after two written notices given at least fifteen (15) days apart, each stating that the Service will be suspended. The Provider shall restore the Service promptly once the reason for suspension ends."
5. New 4.6: "4.6 The Provider shall state its GSTIN (where it is registered in India) on each invoice, issue tax invoices that comply with GST law and report and pay the tax collected, so that the Customer can claim input tax credit, and shall reimburse any input tax credit the Customer loses through its default, with any interest and penalty. Where the Provider is outside India, the Customer shall pay GST on the Service under reverse charge, the Provider's invoice is sufficient for that purpose, and the Fees shall not be increased."
6. New 4.7: "4.7 " + MSME("Provider","Customer") with "the period in clause 4.3".
7. 6.1 → "6.1 This Agreement commences on the Effective Date and {agreement_term}. Each Order Form continues for its stated Subscription Term and renews in accordance with clauses 6.2 and 6.3, unless terminated earlier in accordance with this Agreement."
8. 6.2 "…at least sixty (60) days before the end…" → "…at least {renewal_notice_days} days before the end…" (FR-44)
9. 8.3 first sentence → "8.3 The Provider shall notify the Customer of any Security Incident " + BREACH + " it." (rest unchanged)
10. 8.4 → "8.4 Each Party shall comply with Data Protection Laws. Where the Provider processes personal data on behalf of the Customer, it shall do so only on the Customer's documented instructions. {dpa_status} The data protection obligations in this clause and in that data processing agreement apply in addition to the security obligations above."
11. New 8.6: "8.6 The Provider shall maintain, and test at least once a year, a business continuity and disaster recovery plan for the Service; back up Customer Data at least daily to a separate location in the same region as the hosting location in clause 9.1; and, unless the Order Form states otherwise, meet a recovery point objective of twenty-four (24) hours and a recovery time objective of eight (8) hours. It shall share the plan and its latest test results on request."
12. 9.1 + 9.2 → "{hosting_clause}" (FR-43, Q12)
13. 9.3 append: " If the Customer objects and the Parties do not resolve the objection within thirty (30) days, the Customer may terminate the affected Order Form on written notice without liability, and the Provider shall refund any prepaid Fees for the period after termination."
14. 15.1 → "15.1 Subject to clauses 15.2, 15.4 and 15.5, each Party's total aggregate liability arising out of or in connection with this Agreement, whether in contract, tort (including negligence) or otherwise, shall not exceed the greater of (a) the total Fees paid and payable under this Agreement in the twelve (12) months preceding the event giving rise to the claim and (b) the total Fees payable for the first twelve (12) months of the Subscription Term (the "General Cap")."
15. 15.2 → "15.2 Subject to clauses 15.4(c) to 15.4(f) and 15.5, the Provider's total aggregate liability for all Data Breach Claims shall not exceed the greater of (a) three (3) times the General Cap and (b) {data_breach_floor} (the "Data Breach Cap"). "Data Breach Claim" means any liability arising from a Security Incident or from a breach of clause 8 (Data Security and Data Protection), clause 9 (Data Location and Transfers) or Data Protection Laws, including where the same facts are also a breach of clause 11 (Confidentiality) or fall within the indemnity in clause 14.1(b). The Data Breach Cap replaces the General Cap for Data Breach Claims and is not in addition to it." (B1.1, FR-42, Q5)
16. 15.4 → "15.4 This limitation of liability does not apply to (a) breach of clause 11 (Confidentiality), other than a Data Breach Claim; (b) the indemnities in clauses 14.1(a) and 14.1(c); (c) gross negligence or wilful misconduct; (d) fraud or fraudulent misrepresentation; (e) death or personal injury caused by negligence; or (f) any other liability that cannot be limited or excluded under Applicable Law."
17. New 15.6: "15.6 The Provider shall maintain with reputable insurers, during the Subscription Term and for one (1) year after it ends, professional indemnity insurance and cyber liability insurance, each with a limit of not less than {insurance_minimum} per claim, and shall provide evidence of cover on request."
18. 19.1 "…set out in the applicable Order Form…" → "…set out in its signature block below…".
19. 20.2 → "20.2 The Parties shall first try to resolve any dispute through good-faith negotiation between senior representatives within thirty (30) days of written notice of the dispute. {dispute_resolution}"
20. Standard signature block.
Migration note: "Standard updated: hosting follows the request with privacy approval; one renewal notice of at least 60 days; one data-breach cap; continuity and backups; suspension limits; sub-processor exit; quarterly billing; GST, reverse charge and MSME; insurance; Indian law with arbitration; 24-hour breach notice."

Rules (15):
- `service_levels`, `exit_and_data_return`, `price_increases` — KEPT.
- `data_security` — CHANGED · high · yes (Compliance) · B2, Q1
  P: "The provider holds ISO 27001 or SOC 2 Type II, encrypts our data in transit and at rest, and notifies us of a Security Incident within 24 hours (6 hours where a CERT-In report is required)." F: KEPT. R: "Security Incident" / "twenty-four (24) hours" X: (the four breach-window lines)
  pass: 8.1–8.3 · flag: "The Provider will notify the Customer of a security incident within 72 hours."
- `data_location` — CHANGED · high · yes (Compliance) · B1.9, B2, Q12
  P: "Customer data stays in a named location: India is preferred; the EU, the US or other regions named in the order form only with our privacy function's written approval, and never in a country restricted under section 16 of the DPDP Act; data subject to the GDPR leaves the EEA or UK only under SCCs or an equivalent safeguard." F: — R: "privacy function" X: "any location chosen by the Provider" / "any country in which the Provider or its sub-processors operate"
  pass: every `hosting_clause` · flag: "Customer Data may be stored in any country in which the Provider or its sub-processors operate."
- `renewal` — CHANGED · medium · no · B3.1, FR-44
  P: "Renewal only with at least 60 days' notice to cancel, the same period wherever notice is stated, and a written reminder from the provider before the cancellation deadline." F: KEPT ("30 days' notice to cancel."). R: "renewal reminder" X: —
  pass: 6.1–6.3 · flag: "The subscription renews automatically each year."
- `limitation_of_liability` — CHANGED · high · yes · B1.1, B1.3, B2, Q5
  P: "General cap: the greater of the fees paid and payable in the 12 months before the claim and the fees payable for the first 12 months. Data-breach liability (a security incident, or breach of the data security, data location or data protection terms, including where it is also a breach of confidentiality or falls under the data indemnity) is under one higher cap: the greater of 3x the general cap and INR 5 crore (or the equivalent in the currency of the fees). Uncapped: the IP indemnity, other breach of confidentiality, gross negligence, wilful misconduct, fraud, and death or personal injury. Indirect losses excluded, with data restoration, breach notification costs and regulatory fines treated as direct." F: "A single cap of 2x annual fees covering data breaches, with Legal Counsel approval." R: "limitation of liability" / "Data Breach Cap" X: "unlimited liability"
  pass: edits 14–16 · flag: "The Customer shall have unlimited liability for any breach."
- `payment_terms` — CHANGED · medium · no · B2, Q13, Q16
  P: "Payment within 45, 60 or 90 days of receipt of a valid, undisputed invoice; billed quarterly in arrears unless the order form says otherwise (annual in advance only with a pro-rata refund on early termination, and with Finance approval above INR 10 lakh); a micro or small enterprise is paid within 45 days at most and the contract says so." F: "30 days for a strategic provider that is not a micro or small enterprise, with Finance approval." R: "undisputed invoice" / "Micro, Small and Medium Enterprises Development Act, 2006" X: —
  pass: 4.2–4.3 + 4.7 · flag: "Fees are payable annually in advance and are non-refundable."
- `suspension` — NEW · medium · no · B1.5
  P: "The provider may suspend only briefly for an imminent security threat with notice, or for undisputed fees more than 30 days overdue after two written notices; never while an amount is disputed in good faith." F: — R: "suspend" X: "suspend the Service at any time"
  pass: edit 4 · flag: "The Provider may suspend the Service at any time without notice."
- `business_continuity` — NEW · medium · no · B1.2
  P: "A tested (yearly) business continuity and disaster recovery plan, daily backups in-region, RPO 24 hours and RTO 8 hours unless the order form says otherwise." F: — R: "disaster recovery" X: —
  pass: edit 11 · flag: "The Provider maintains backups."
- `subprocessors` — NEW · high · yes (Compliance) · B1.6
  P: "A current sub-processor list, 30 days' notice of a new one with a right to object, and if the objection is not resolved in 30 days, a right to end the affected order with a refund of prepaid fees." F: — R: "object" X: —
  pass: 9.3 · flag: "The Provider may engage sub-processors at its discretion."
- `data_use` — NEW · high · yes (Compliance) · B2
  P: "Customer data is used only to provide the service; never to train or improve AI models, benchmark, or build data sets for others without our written consent." F: — R: "train" X: "may use Customer Data to improve"
  pass: 7.2 · flag: "The Provider may use Customer Data to improve its products and models."
- `governing_law` — NEW · as MSA. `tax_compliance` — NEW · as MSA; pass: edits 5. `insurance` — NEW · as MSA `insurance` (P: professional indemnity and cyber liability of at least INR 5 crore each per claim …); pass: edit 17.

#### 6. Consultancy — `defaults/consultancy.txt` + `intake/consultancy.py` + library "Consultancy Agreement Playbook"

`intake/consultancy.py`: `_fee_basis` appends for Day rate: " A day is eight (8) hours; part days are charged pro rata. Travel time is not chargeable unless agreed in writing."; for Hourly rate: " Part hours are charged pro rata. Travel time is not chargeable unless agreed in writing." (FR-47). `consultancy_invoicing` is built here (rules in `paper` table) and exposed via `paper_values`.

Template edits:
1. New 2.5: "2.5 If any Services or Deliverables do not comply with this Agreement, the Consultant shall, at the Company's option, re-perform them at no cost within ten (10) business days of notice, or refund the Fees paid for them. A Deliverable is accepted only when the Company confirms in writing that it meets its description in the Engagement Schedule." (C5)
2. 3.2 → "3.2 The Consultant is solely responsible for all income tax, social security, provident fund, employees' state insurance, gratuity and other statutory contributions in respect of itself and its Personnel, and shall indemnify the Company against any such liability, together with any related interest and penalties, that is assessed against the Company." (C1 — GST removed from here; one GST position in 6.6, FR-20/AC-16)
3. New 3.4: "3.4 The Consultant is free to provide services to others (subject to Clause 4), is not required to work fixed hours, and shall not hold itself out as an employee of the Company. Where its Personnel attend the Company's premises, the Consultant is their employer for all purposes and shall comply with all labour laws applicable to them, including any registration or licence required." (C8)
4. 6.1 → "6.1 In consideration of the Services, the Company shall pay the Consultant the Fees set out in the Engagement Schedule, in {fee_currency}." (`{value_sentence}` dropped: it repeated `{fee_amount}`)
5. 6.2 → "6.2 {consultancy_invoicing} Each invoice must itemise the Services performed, include any supporting timesheets the Company reasonably requests, and comply with Applicable Law on tax invoices." (C10)
6. New 6.3A: "6.3A " + MSME("Consultant","Company") with "the period in Clause 6.3".
7. 6.6 → "6.6 The Fees exclude goods and services tax ("GST"). Where GST is chargeable on the Fees, each invoice shall show the Consultant's GSTIN and comply with GST law, and the Company shall pay the GST shown against that valid tax invoice. If the Company loses input tax credit because the Consultant fails to deposit the tax or report the invoice, the Consultant shall reimburse that credit with any interest and penalty. Where GST is payable by the Company under reverse charge, the Company shall pay it and the Fees shall not be increased. The Company may deduct any tax it is required by law to withhold and shall provide the relevant certificate. A Consultant resident outside India shall provide its tax residency certificate, Form 10F and any other document the law requires before the first payment; until then tax is withheld at the rate that applies without them." (C1, C2)
8. 7.4 survival list adds "2.5" and "12.5": "Clauses 2.5, 3, 6, 7.3, 7.4, 8, 9, 10, 11, 12, 13, 15 and any other provisions…"
9. 8.1 → "8.1 All Intellectual Property Rights in the Work Product vest in the Company on creation. The Consultant hereby assigns to the Company, by way of present assignment of future rights, free of all encumbrances, all right, title and interest in and to the Work Product and all intellectual property in it, throughout the world and for the full term of those rights, and shall procure an equivalent assignment from each of its Personnel. The Fees are the full consideration for this assignment. To the extent permitted by law, section 19(4) of the Copyright Act, 1957 does not apply, and the assignment shall not lapse or revert for non-exercise of any right within any period." (C7)
10. 8.2 → "8.2 The Consultant irrevocably and unconditionally waives, and shall procure that its Personnel waive, all moral rights in the Work Product to the extent permitted by Applicable Law, and undertakes not to assert them, including the rights under section 57 of the Copyright Act, 1957." (Q11)
11. 8.4 append: " Any power of attorney given under this clause shall be stamped as required by law, at the Company's cost." (Q11)
12. 9.8 → "9.8 Where the Consultant processes personal data on behalf of the Company, it shall comply with the Digital Personal Data Protection Act, 2023 and any other applicable data protection law, act only on the Company's documented instructions, keep the data secure, and notify the Company " + BREACH + " any breach affecting it. {dpa_status}" (C9, Q1, FR-24)
13. 12.3(a) → "(a) a Party's breach of Clause 9 (Confidentiality), including any personal data breach under Clause 9.8;"
14. New 12.5: "12.5 The Consultant shall maintain, during the term and for two (2) years after it ends, professional indemnity insurance of not less than {insurance_minimum} per claim, public liability insurance, and, where it processes the Company's personal data or has access to its systems, cyber liability insurance of not less than {insurance_minimum} per claim, and shall provide evidence of cover on request." (C6, Q3)
15. 13.2 → "13.2 The Parties shall first attempt to resolve any dispute through good-faith negotiation for thirty (30) days after either Party notifies the other of the dispute. {dispute_resolution}" (C3, Q2)
16. 14.1 "…set out in this Agreement…" → "…set out in its signature block below…".
17. New 15.8: "15.8 Costs and stamp duty. Each Party bears its own costs in connection with this Agreement. The Company shall bear any stamp duty payable on this Agreement." (Q6)
18. Standard signature block (after the Engagement Schedule, as today).
Migration note: "Standard updated: one GST position; Indian law with the courts at our registered city; re-performance and acceptance; copyright under Indian law with moral rights not asserted; independent-contractor terms; day defined; invoicing by fee basis; insurance; 24-hour breach notice; stamp duty."

Rules (13):
- `intellectual_property` — CHANGED · high · no · C7, Q11
  P: "Everything the consultant creates for us is assigned to us on creation, the fees being the full consideration, worldwide and for the full term, with no lapse for non-use (section 19(4) of the Copyright Act does not apply); the consultant waives and does not assert moral rights where the law allows." F: "Assignment on payment of the related invoice, with a licence to use the work until then." R: "intellectual property" / "section 19(4)" X: —
  pass: edits 9–10 · flag: "The Consultant grants the Company a licence to use the reports."
- `independent_contractor` — CHANGED · high · yes · C8
  P: "An independent contractor: no employment relationship; the consultant pays its own taxes and statutory contributions and uses its own tools, is free to serve others, works no fixed hours, and is the employer of its own personnel, complying with labour law on our premises." F: — R: "independent contractor" X: —
  pass: 3.1, 3.4 · flag: "The Consultant shall work at the Company's offices from 9 to 6 under its manager." (lacks the phrase)
- `confidentiality`, `conflicts_of_interest`, `non_solicitation` — KEPT.
- `payment_terms` — CHANGED · medium · no · C12, Q16
  P: as MSA payment P. F: "90 days for a consultant that is not a micro or small enterprise, with Finance approval." R/X: as MSA. why: as MSA.
  pass: 6.3 + 6.3A · flag: "The Company shall pay within 120 days."
- `termination` — CHANGED · medium · no · C12, FR-48
  P: KEPT ("…15 days' notice…"). F: "Termination for convenience on 30 days' notice." R: "terminate" X: —
  pass: 7.1 · flag: "This Agreement may not be ended before its term."
- `limitation_of_liability` — CHANGED · high · yes · C12, FR-48
  P: "Each party's total liability is capped at the fees paid or payable under the agreement. Indirect and consequential losses are excluded. The cap does not apply to breach of confidentiality (including a personal data breach), the consultant's indemnities and its tax and statutory obligations, gross negligence, wilful misconduct, fraud, or death or personal injury." F: "Up to 2x the fees." R: "limitation of liability" / "paid or payable" X: "Company shall have unlimited liability" (old "unlimited liability" removed: it flagged our carve-outs)
  pass: 12.1–12.3 · flag: "The Company shall have unlimited liability for any claim."
- `governing_law` — NEW · high · yes · C3, C4, Q2
  P: "Indian law, with the exclusive jurisdiction of the courts at a named Indian city (our registered city, or the city of our registered office), for every consultant, individual or firm, Indian or foreign; either party may seek urgent interim relief from any competent court. Arbitration (MCIA, or SIAC seated in Singapore for a consultant outside India) also meets this position when a consultant's paper offers it." F: "English or Singapore law with SIAC arbitration for a consultant outside India, with Legal Counsel approval." R: "laws of India" / "courts at" X: "exclusive jurisdiction of the courts of" / "laws of the State of Delaware"
  pass: 13.1 + the Consultancy `dispute_resolution` (courts at the seat), for an Indian and a foreign consultant · flag: "Disputes shall be heard by the courts of competent jurisdiction under the laws of the State of Delaware."
- `insurance` — NEW · medium · no · C6, Q3
  P: "Professional indemnity insurance of at least INR 5 crore per claim (or the equivalent in the currency of the fees) and public liability cover during the engagement and 2 years after; cyber liability of the same amount where the consultant handles personal data or our systems." F: — R: "per claim" X: "limits of cover appropriate to"
  pass: edit 14 · flag: "The Consultant shall maintain appropriate insurance."
- `data_protection` — NEW · as MSA `data_protection` (same P/F/R/X/why).
- `tax_compliance` — NEW · as MSA `tax_compliance`; pass: edit 7.
- `non_compete` — NEW · high · yes (Legal Counsel) · C12, FR-48
  P: "No non-compete binding either party beyond the conflicts-of-interest restriction during the engagement; nothing restricts the Company from engaging others or doing similar work." F: — R: — X: "shall not compete" / "non-compete" / "not to compete"
  pass: clause 4 · flag: "The Company agrees not to compete with the Consultant for two years."

Advance payment (6.5 "Any advance payment shall not exceed twenty percent…") stays in the template; the rule no longer prohibits "payable in advance" (FR-48, AC-37).

#### 7. DPA — `defaults/dpa.txt` + `intake/dpa.py` + library "Data Processing Agreement Playbook"

`intake/dpa.py`: no change to its six fields. (`parent_agreement`, `data_breach_floor` come from `paper`.)

Template edits:
1. Recital A → "A. The Processor provides services to the Controller under {parent_agreement} (the "Principal Agreement"), in the course of which the Processor processes Personal Data on behalf of the Controller." (D1)
2. New 3.5: "3.5 The Processor shall not undertake tracking, behavioural monitoring or targeted advertising directed at children, and shall process children's Personal Data only as the Controller instructs in accordance with section 9 of the DPDP Act." (D8)
3. 8.1 → "8.1 The Processor shall notify the Controller of any personal data breach affecting Personal Data " + BREACH + " it." (D7, Q1, FR-50)
4. 8.2: "(d) …adverse effects; and (e) any other information…" → "(d) …adverse effects; (e) any other information the Controller reasonably requires to notify Supervisory Authorities and affected Data Subjects; (f) the date, time and location of the Personal Data Breach and when it was discovered; and (g) its cause and, where known, the person responsible." (D6)
5. 9.2 + 9.3 → "9.2 Any authorised transfer shall be made only as follows: (a) for Personal Data subject to the DPDP Act, never to a country or territory restricted by the Central Government under section 16 of the DPDP Act, and in compliance with any conditions prescribed under it, and no other safeguard is required for such data; and (b) for Personal Data subject to the GDPR or the UK GDPR, only under the Standard Contractual Clauses (Module Two, or Module Three for a transfer by the Processor to a Sub-processor), together with the UK International Data Transfer Addendum for UK data, or another safeguard recognised under Chapter V of the GDPR, with any supplementary measures needed." / "9.3 Where the Standard Contractual Clauses apply, they are incorporated by reference with the Controller as data exporter and the Processor as data importer: Clause 7 (docking clause) is included; under Clause 9 Option 1 (prior specific authorisation) applies; under Clause 13 the competent supervisory authority is that of the data exporter; Clauses 17 and 18 are governed by the law of Ireland and the courts of Ireland; and Annexes I to III are completed by Annexes 1 to 3 of this Agreement. The Standard Contractual Clauses prevail over this Agreement to the extent of any conflict." (D4, Q7)
6. New 10.6: "10.6 The Processor shall keep a record of its Processing as required by Applicable Data Protection Law, notify the Controller promptly (where lawful) of any request by a public authority for access to Personal Data, and erase specific Personal Data within ten (10) business days of the Controller's instruction." (D11)
7. Section 12 → "12.1 The Processor shall indemnify and hold harmless the Controller against all losses, damages, fines, penalties, costs and expenses (including reasonable legal fees) arising out of any breach of this Agreement or of Applicable Data Protection Law by the Processor or any Sub-processor, including any fine or penalty imposed on the Controller to the extent caused by the Processor, a Sub-processor or their personnel." / "12.2 The Processor's total aggregate liability under this Agreement and for breach of Applicable Data Protection Law shall not exceed the greater of (a) three (3) times the fees paid or payable under the Principal Agreement in the twelve (12) months preceding the event giving rise to the claim and (b) {data_breach_floor}, save that this limit does not apply to liability arising from the Processor's fraud or wilful misconduct." / "12.3 This Section 12 applies in place of any limitation of liability in the Principal Agreement for the liability it covers, and the limit in Section 12.2 is never lower than the general limit of liability in the Principal Agreement." (D10, Q5, Q8)
8. 13.1 → "13.1 This Agreement takes effect on the Effective Date, applies to all Processing under the Principal Agreement whether before or after that date, and continues until the later of the termination or expiry of the Principal Agreement and the return or deletion of all Personal Data under Section 11." (`{agreement_term}` no longer used; D2)
9. 14.1 → "14.1 Governing Law and Disputes. This Agreement is governed by the law that governs the Principal Agreement, and disputes under it are resolved under the dispute-resolution clause of the Principal Agreement, save that the Standard Contractual Clauses, where they apply, are governed by the law and forum they specify." (D3, FR-15)
10. 14.2 → "14.2 Notices. Notices under this Agreement shall be in writing and delivered by hand, by recognised courier, or by email with confirmation of transmission to the address for notices of the receiving Party set out in its signature block below. The Controller's privacy contact is ______________________________ (name, email and telephone) and the Processor's privacy contact is ______________________________ (name, email and telephone), each as updated by written notice. Notices of a Personal Data Breach shall also be given by telephone to the Controller's privacy contact." (D6, FR-49)
11. Standard signature block (before Annex 1).
Migration note: "Standard updated: names the principal agreement; term and law follow it; DPDP and EU/UK transfers treated separately; 24-hour breach notice with full details; children's data; records, authority requests and 10-day erasure; one 3x liability limit."

Rules (17):
- `processing_instructions`, `security_measures` — KEPT.
- `sub_processors` — CHANGED · high · no (Compliance) · B2, D12
  P: KEPT. F: "General authorisation with 30 days' notice and a right to object, and a right to end the affected services if the objection is not resolved in 30 days." R: "processor" X: —
  pass: 6.1 · flag: "The supplier may use other companies to help it." (lacks "processor")
- `breach_notification` — CHANGED · critical · yes (Compliance) · D7, Q1, FR-50
  P: "Personal data breaches are notified to us without undue delay and within 24 hours (6 hours where a CERT-In report is required), with their nature, the people and records affected, likely consequences, measures taken, the date, time and place, the cause and the person responsible, so we can notify the Data Protection Board and individuals." F: — R: "personal data breach" X: (the four breach-window lines)
  pass: edits 3–4 · flag: "The Processor shall notify the Controller of a personal data breach within 72 hours."
- `international_transfers` — CHANGED · high · yes (Compliance) · D5, Q7
  P: "No transfer outside India without our written authorisation, and never to a country restricted under section 16 of the DPDP Act; for EU or UK data, only under SCCs (with the UK Addendum) or another Chapter V safeguard." F: — R: "transfer" X: "transfer Personal Data to any country without"
  pass: 9.1–9.3 · flag: "The Processor may transfer Personal Data to any country without restriction."
- `audit` — CHANGED · medium · no · B2
  P: KEPT. F: "An annual independent report (ISO 27001 / SOC 2) instead of routine on-site audits, keeping on-site audits after a breach or at a regulator's demand." R: "audit" X: —
- `data_subject_rights` — CHANGED · medium · no · D12
  P: "The processor forwards a Data Principal's (data subject's) request to us within 2 business days and helps us answer it within 5 business days." F: — R: "request" X: —
  pass: 7.2–7.3 · flag: "The Processor will help as needed." (lacks "request")
- `return_or_deletion` — CHANGED · medium · no · D12
  P: "At the end of the services, personal data is returned or deleted (erased) at our choice, with deletion certified; specific data is erased within 10 business days of our instruction." F: — R: — (old "deletion" removed: it failed DPDP "erasure" wording) X: "retain Personal Data for its own"
  pass: 11.1 + 10.6 · flag: "The Processor may retain Personal Data for its own analytics."
- `liability` — NEW · high · yes (Legal Counsel) · D10, Q5, Q8
  P: "The processor's liability for data protection breaches is under one higher limit: the greater of 3x the fees paid or payable under the principal agreement in the 12 months before the claim and INR 5 crore, never below the principal agreement's general limit; fines caused by the processor or its staff are recoverable under the indemnity within that limit." F: — R: "three (3) times" X: —
  pass: edit 7 · flag: "The Processor's liability is limited as set out in the Principal Agreement."
- `term` — NEW · medium · no · D2
  P: "The DPA runs from its effective date until the later of the end of the principal agreement and the return or deletion of all personal data; no separate fixed term." F: — R: "return or deletion of all Personal Data" X: —
  pass: edit 8 · flag: "This Agreement continues for one year."
- `processing_description` — NEW · medium · no · FR-50
  P: "An annex describes the subject matter, duration, nature and purpose, data types and categories of people, and processing is limited to it." F: — R: "Annex 1" X: —
- `no_own_use` — NEW · high · no · FR-50
  P: "No processing for the processor's own purposes, no sale, advertising or combination with other data, and no AI training." F: — R: "own purposes" X: —
- `confidentiality_of_personnel` — NEW · medium · no · FR-50
  P: "Only personnel who need access, each bound by confidentiality and trained." F: — R: "confidentiality" X: —
- `assistance` — NEW · medium · no · FR-50
  P: "The processor assists with data protection impact assessments, prior consultations, records of processing and authority requests." F: — R: "impact assessment" X: —
- `children_data` — NEW · high · yes (Compliance) · D8
  P: "No tracking, behavioural monitoring or targeted advertising directed at children; children's data only on our instructions under section 9 of the DPDP Act." F: — R: "children" X: —
  pass: edit 2 · flag: (a DPA without the clause) "The Processor processes customer data."
- `governing_law` — NEW · medium · no · D3, FR-15
  P: "The DPA follows the principal agreement's law and dispute route; the SCCs follow their own." F: — R: "governs the Principal Agreement" X: "This Agreement shall be governed by and construed in accordance with the laws of"
  pass: edit 9 · flag: "This Agreement shall be governed by and construed in accordance with the laws of Delaware."

For `processing_description`, `no_own_use`, `confidentiality_of_personnel`, `assistance`: pass = the existing clause containing the phrase (2.1/Annex 1, 3.4, 4.1, 10.5); flag = the same clause without it.

### How new versions reach organisations (FR-53, FR-54)

| Org state | Template | Playbook |
|---|---|---|
| New org (no rows) | Shipped default (new text) is version 0; no marker needed | `seed-library` creates the six library playbooks from the new rules. The NDA has no library playbook (unchanged decision); "Use the standard rules" is offered only if a playbook reviews the NDA template. |
| Existing org on the shipped wording | New text applies on deploy (read live); 0077 adds a `body=NULL` version with the note and a system audit row | 0077 publishes a new version with the standard rules if the org's rules still equal the reviewed baseline |
| Existing org with its own saved wording | Kept and still used; `standard.wording_behind=true`; Templates page warns, shows the check result, offers the existing **Reset to shipped wording** | — |
| Existing org that edited the playbook | — | Kept; 0077 audits `playbook.standard_skipped`; `standard.playbook_behind=true`; Templates page warns and offers **Use the standard rules** (new endpoint, `playbook:publish`) |

Version 0 is, by spec 006's design, "the shipped wording" and is read live; the previous shipped
text stays in git history and the 0077 note records what changed. `ponytail:` no frozen copy of
old shipped texts; add `defaults/history/` if someone needs to read old shipped wording in-app.

### Proof (FR-1A, AC-1, AC-1A)

**(a) Deterministic matrix — runs with `pytest -q`, no AI.**
`backend/eval/template_matrix.py` (importable by tests as `eval.template_matrix`; pure):
- `CONTEXTS`: `"registered"` (EXAMPLE_CONTEXT-like: Mumbai address, signatory with title, Indian
  counterparty with address, parent chosen), `"bare"` (DraftContext() — no register data, no parent),
  `"foreign"` (counterparty jurisdiction "United States", no addresses).
- `combinations(key) -> Iterator[tuple[str, dict, DraftContext, str, str]]` (label, fields, context,
  company, counterparty) — dimensions read from `agreement_forms.json` through
  `form_fields` + `options_for` (so a later option change grows the matrix by itself):

| Template | Dimensions (full product) | Count |
|---|---|---|
| NDA | kind×direction {Mutual; One-way/We share; One-way/They share} × nda_term {2,3,5 years} × governing_law {5 options + blank + "Maharashtra, India"} × personal_data {Yes, No} × contexts {registered, bare, foreign} | 378 |
| MSA | payment_terms {45, 60} × term {Fixed end date; No end date; Renews automatically × renewal_term {1,2,3 years} × notice_days {30,60,90}} (11) × personal_data {Yes,No} × gxp {Yes,No} × currency {INR,USD,EUR,GBP} × value {given, blank} × contexts {registered, bare, foreign} | 2,112 |
| SOW | pricing {Fixed fee, Milestones, Time and materials} × personal_data × gxp × currency (4) × parent {chosen, none} × signer {"Jordan Lee, Delivery Lead", blank} with 3 deliverable rows (shares for Milestones) | 192 |
| Vendor | payment_terms {45,60,90} × buying {3} × term (11) × personal_data × currency (4) × contexts (3) | 2,376 |
| SaaS | payment_terms {45,60,90} × hosting {4} × term (11) × personal_data × currency (4) × contexts (3) | 3,168 |
| Consultancy | payment_terms {45,60} × fee_basis {Fixed fee, Day rate, Hourly rate} × deliverables {rows, advice only} × term (11) × personal_data × currency (4) × contexts {registered (Indian), foreign} | 2,112 |
| DPA | data_subjects {each single option, all} (5) × data_types {each single option, all} (5) × transfer {No; Yes + ["Ireland","United States"]} × subprocessors {No; Yes + 1 row} × parent {chosen, none} | 200 |

Each `tests/test_alignment_<key>.py` has **one** test that loops the matrix (not parametrized
per combination, to keep collection fast) and collects every failure into one assertion message:
1. `render_document(key, …, fields, context=…)` (shipped default);
2. no `{` or `}`; no line matching `^(Name|Title|Address for notices|Pricing model|Provider):\s*$`;
   none of `"Provider: ."`, `"is , "`, `" ,"`, `" ."`, `"Pricing model: "`;
3. `"Delaware"` / `"New York"` only when that law was picked on an NDA; never `"laws of Maharashtra"`;
4. currency ≠ INR → no `"INR "` in the text (all but DPA);
5. `evaluate_rules_against_text(rules=STANDARD_RULES[key] as SimpleNamespace, text=…) == []`;
6. the template's own invariants: SOW — exactly one of "8.1 Fixed fee." / "8.1 Fixed fee payable in
   instalments." / "8.1 Time and materials." and no "not-to-exceed" unless T&M; Vendor — "3.3
   Delivery." iff goods, "Services. The Vendor shall perform" iff services; SaaS — every
   `(\w+) \((\d+)\) days'? (written )?notice` in clause 6 names the same number, ≥ 60, and the hosting
   region named matches the answer; NDA — the term words match the answer and the
   `nda_personal_data` variant matches; MSA/Vendor/SaaS/Consultancy — `dpa_status` variant matches
   `personal_data`; DPA — no "{agreement_term}"-derived "continues until terminated" text.
Budget: the whole matrix under 20 s on the CI runner (≈ 12k renders); if it is slower, profile
`find_phrase` (compile once per phrase) before cutting combinations.

**(b) AI sample — deliberate, paid, never in CI.**
`backend/eval/template_alignment.py`, invoked as

```
docker compose exec -e MOCK_CLAUDE=false backend \
  python -m eval.template_alignment --as admin@example.com [--runs 3] [--only nda,sow] [--negatives] [--yes]
```

- Refuses unless: `settings.mock_claude` is false, a Claude key is set, `settings.environment` is
  local/development/dev, and `--as` names an active user of the org (the person accountable). Without
  `--yes` it only prints the plan: combinations × runs, an estimate at **$0.10 per review**, and exits.
- Main combinations (`template_matrix.MAIN`, 19, satisfying FR-1A's list):
  NDA ×3 (Mutual/3y/India/No · One-way We share/2y/England and Wales/Yes · One-way They share/5y/Delaware/No);
  MSA ×2 (45/Fixed end/INR/No/GxP No/registered · 60/Renews 2y notice 30/USD/Yes/GxP Yes/foreign);
  SOW ×3 (Fixed fee/INR/parent · Milestones/USD/PD Yes · Time and materials/no parent/no signer);
  Vendor ×3 (Goods/45/INR/No · Services/60/Renews notice 30/Yes · Goods and services/90/EUR/foreign);
  SaaS ×4 (India/45/Renews notice 30/INR · European Union/60/Fixed/Yes/EUR · United States/90/Renews notice 90/USD/foreign · Several regions/45/No end/GBP);
  Consultancy ×2 (Fixed fee/advice only/45/No/Indian · Day rate/rows/60/Yes/USD/foreign);
  DPA ×2 (no transfer, no sub-processors · transfer Ireland + United States, one sub-processor, special category, parent chosen).
  19 × 3 = **57 reviews**, plus `--negatives`: one "counterparty paper" per playbook (the default
  render with every `flag` wording of that playbook substituted for its `pass` clause) = 7 reviews.
  **64 reviews ≈ $6** (estimate printed before any spend).
- Each run: render the org's **current** wording (`current_wording`) with the combination; call
  `PlaybooksService.review_text(user, playbook=<pinned>, text, title, contract_type, counterparty_name,
  resource_type="drafting_template_check", resource_id=<new check id>)`; then insert a **completed**
  `DraftingTemplateCheck` (`trigger="sample"`, `trigger_detail={"batch": <uuid>, "combination":
  <label>, "run": n, "fields": {...}}`, template version and sha of the wording read, playbook ids,
  `findings=_enrich(...)`, timestamps) and the `drafting_template.check_finished` audit row as the
  `--as` person; commit per run (no audit write held across an AI call). Inserting only completed
  rows never collides with the one-running/one-queued indexes. Negative runs are recorded only in the
  report (they are not our template).
- Output: `backend/eval/results/template_alignment/<YYYY-MM-DD>-<batch8>.json`
  (`{date, batch, model, playbook versions, runs:[{template, combination, run, finding_count,
  findings}], negatives:[{playbook, expected_clause_types, found_clause_types}], totals}`) and a
  console summary; exit code 1 when any main run has a finding or any negative misses an expected
  clause type. The runs also appear in each template's check history on the Templates page, dated,
  labelled "Sample answers".
- When: before approval of the build (task T017) and after any later template/playbook change
  (FR-1A). CI never imports it (it lives outside `tests/`; nothing in `tests/` imports it).

**(c) Replace the stale results (FR-8, AC-7)**: after 0077 runs, every template is *Out of date*
(wording and playbook moved); T017 runs **Check all** (existing button / `POST /drafting-templates/
check-all`), which replaces the 15 Oct SOW result with a dated check of the current SOW wording.

### Frontend (Next.js page/component tree)

- `src/lib/form-checks.ts`: `export function optionsFor(field: RequestFormField, values: Record<string, FormValue>): string[]`
  — first `options_when` entry whose rules all match (reuse the existing rule matcher used by `shown`),
  else `field.options ?? []`. `answerProblem` returns `` `"${value}" is no longer offered for this agreement. Pick again.` ``
  for a `select` whose value is not in `optionsFor` (blocks Next, as a missing answer does) — this is
  the edge case "a half-filled saved draft that holds a removed option".
- `src/app/(app)/intake/_agreement-forms.tsx` `Question`: `const opts = optionsFor(field, values).map(...)`
  for `select` and `multiselect` (multiselect unchanged in practice: no options_when there).
- `src/app/(app)/templates/_template-list.tsx`: under the status badge, when
  `t.standard?.wording_behind || t.standard?.playbook_behind`: `<Badge tone="warning">Older than the standard</Badge>`.
- `src/app/(app)/templates/_template-detail.tsx`: above the editor, using the existing `MessageBar`
  primitive (confirm it is exported from `@/components/ui`; it is in the constitution's list):
  - `wording_behind`: "Your saved wording is older than the standard wording of {updated_on as
    "27 Oct 2026"}. The check below is the result on your wording." + the existing **Reset to
    shipped wording** action (same handler, `expected_version`).
  - `playbook_behind`: "{playbook.name} has rules older than the standard rules of 27 Oct 2026." +
    button **Use the standard rules** → confirm dialog (existing confirm pattern) →
    `draftingTemplatesApi.adoptStandardPlaybook(key, {})` → on success replace the template from
    `result.template` and toast "Standard rules published as version N"; on 403 toast the server
    message. Shown only to users who hold `playbook:publish` (same permission check the page already
    uses to show its edit actions).
- `_check-panel.tsx`: `TRIGGER_LABEL.sample = "Sample answers"`; show `trigger_detail.combination`
  under it when present.
- New shared primitives in `src/components/ui.tsx`: none (Badge, MessageBar, Button exist).
- Demo mode: `src/lib/demo.ts` does not exist in this repo; nothing to add.

## Test strategy

- Backend pytest (all in container, `legal_clm_test`):
  - `test_paper_fill.py`: law mapping (India/states/blank/each NDA option/"France" → India); seat from
    each address form, "Bangalore" → Bengaluru, no address → descriptive seat; foreign vs Indian
    detection; Consultancy gives the courts-at-the-seat text for an Indian and a foreign counterparty alike; signatory split; each `dpa_status`/`nda_personal_data`/hosting/
    vendor/insurance/currency variant; `renewal_notice_days(30)==60`, `(90)==90`; `placeholder_scope`
    true for "This is a test", "TBD", "lorem ipsum…", 14 words; false for a 15-word real scope
    containing "testing".
  - `test_playbook_rules_014.py`: `_phrases` (multi-line prohibited/required, single line unchanged);
    every rule in `STANDARD_RULES` complete (`preferred` + `rationale`, valid risk); **every CHANGED/NEW
    rule's `pass` gives no deviation and its `flag` gives at least one** (FR-52, AC-40); AC-5 table:
    MSA/SaaS/Vendor/Consultancy no "payable in advance", Consultancy no bare "unlimited liability",
    NDA exclusions no prohibition of market wording, DPA required no longer "deletion"/"data subject"/
    "sub-processor"/"international transfers", Vendor compliance not "compliance", Vendor warranties
    not "service levels", SaaS data_security not "data protection".
  - `test_payment_options_014.py`: MSA/Consultancy accept 45/60, refuse 30/90 (422 names the options);
    Vendor/SaaS accept 45/60/90, refuse 30; Selling still accepts 30; an update leaving a stored
    "30 days" unchanged is accepted; `options_for` without `options_when` = `options`.
  - `test_drafting_context_014.py`: `draft_contract_for_request` builds the context from the register
    (Mumbai address → "courts at Mumbai" in an NDA, AC-11), from a parent contract of the same org
    (named in SOW/DPA, AC-12/AC-29) and ignores a parent of another org; no Delaware anywhere when
    law is blank or "Maharashtra, India" for NDA/MSA/Vendor/SaaS/Consultancy (AC-10); FR-25 hold for
    "This is a test" on SOW and Consultancy and resume once the scope is real (AC-21); SOW with no
    pricing is held (AC-27); signature blocks with/without register data (AC-13, AC-14).
  - `test_alignment_<key>.py` ×7: the matrix above (AC-1, AC-1A deterministic half, AC-15–AC-20,
    AC-22, AC-23, AC-25–AC-36, AC-38).
  - `test_standard_update_014.py`: `apply_standard_update` on an org with (a) default wording + library
    playbooks at baseline → marker versions + new published versions + system audit rows, earlier
    versions unchanged (AC-41); (b) an org with its own NDA wording and an edited playbook → nothing
    overwritten, `playbook.standard_skipped`, `standard.wording_behind`/`playbook_behind` true, status
    shown (AC-42); idempotent second run writes nothing; revert archives and re-points; no orgs →
    no-op and no ORM access; the adopt endpoint: 200 with new version + audit (who/when/why), 200
    `changed:false` when already standard, 403 without `playbook:publish` (AC-44), 404 for a key of
    another org, 409 custom type; viewing (`GET /drafting-templates`) writes no audit row (AC-45);
    Customer Agreement library rules hash to `CUSTOMER_BASELINE` (AC-6).
  - `test_existing_contracts_untouched_014.py`: a drafted contract with a payment-terms deviation
    before `apply_standard_update` has byte-identical snapshot text, deviations, edits and timeline
    after it (AC-9).
  - Edited: `test_drafting_templates.py` (consultancy default now 45; the new route in the
    permission table; placeholder list), `test_playbook_library.py`, and any test pinning old wording.
- Frontend vitest: `optionsFor` + not-offered problem; wizard shows 45/60 for MSA and 45/60/90 for
  Vendor; Templates list badge; detail notices and the adopt button (calls the endpoint, hidden
  without permission); `TRIGGER_LABEL` covers every trigger (type-level `Record` already enforces).
- Gates: `ruff check .`, `alembic upgrade head` (one head: `0077_std_paper`), `pytest -q`;
  `npm run typecheck`, `npm run test`.

### Acceptance verification (AC → evidence)

| AC | Evidence |
|---|---|
| AC-1 | `test_alignment_*` (7 matrices, zero phrase findings) + AI sample JSON (0 findings) |
| AC-1A | matrix tests in `pytest -q`; AI JSON report dated, 57 runs × 0 findings; CI workflow unchanged and `eval/template_alignment.py` not imported by tests |
| AC-2 | `test_payment_options_014.py` + payment rules in matrices |
| AC-3 | pair tables in this plan; T006–T012 each own their template AND assert against the T002 rules; 0077 ships rules with the text in the same release |
| AC-4 | matrix check 2 + `test_drafting_context_014` (no pricing → held; blank signer/law → blank lines) |
| AC-5 | `test_playbook_rules_014.py` AC-5 table |
| AC-6 | `CUSTOMER_BASELINE` test; 0077 never touches the Customer Agreement Playbook |
| AC-7 | after T017 Check all: SOW check history shows a new dated check of the current wording; old one superseded by status (verification screenshot/API output) |
| AC-8 | existing `test_template_status` out_of_date cases + verification after 0077 (status "Out of date" before Check all) |
| AC-9 | `test_existing_contracts_untouched_014.py` |
| AC-10 | `test_drafting_context_014` |
| AC-11 | `test_drafting_context_014` (Mumbai) |
| AC-12 | `test_alignment_sow`/`_dpa` + context test |
| AC-13, AC-14 | `test_drafting_context_014` + `test_paper_fill` |
| AC-15 | MSA/Vendor/Consultancy matrices (MSME line + 0 findings for every offered period); 30 and 90 refused on MSA/Consultancy, 30 on Vendor (FR-3) |
| AC-16 | consultancy matrix: "goods and services tax" appears only in 6.6 |
| AC-17 | MSA/SOW matrices: USD → no "INR " |
| AC-18 | matrices (`per claim` present) + rule `flag` tests (no amount flagged) |
| AC-19 | matrices + `flag` tests (72h/48h flagged) |
| AC-20 | matrices `dpa_status` variant + 0 data_protection findings for "No" |
| AC-21 | `test_drafting_context_014` hold |
| AC-22 | NDA matrix + `term_and_duration` flag test |
| AC-23 | NDA matrix (PD variant; recital "explore a potential business relationship") |
| AC-24 | NDA rule flag tests (non-compete, indemnity) |
| AC-25 | MSA 5.5 + SOW 4.7 matrices; MSA `service_levels` 0 findings |
| AC-26 | MSA `limitation_of_liability` pass test |
| AC-27 | SOW matrix (one model) + hold test |
| AC-28 | SOW T&M matrix + `milestone_payments` 0 findings |
| AC-29 | SOW matrix (parent named, master payment period, 4.7, rep fallback) |
| AC-30 | Vendor matrix invariant 6 |
| AC-31 | SaaS matrix (Data Breach Claim definition + 15.4 wording) + AI sample |
| AC-32 | SaaS matrix hosting invariant + `data_location` flag test |
| AC-33 | SaaS matrix notice invariant |
| AC-34 | SaaS matrix (4.2, 4.5, 4.6, 8.6, 9.3 present) |
| AC-35 | `test_playbook_rules_014` (Vendor/SaaS rule lists contain the new clause types) |
| AC-36 | Consultancy matrix |
| AC-37 | Consultancy `non_compete` flag test; 6.5 advance wording passes |
| AC-38 | DPA matrix (parent named, no `agreement_term` text, 9.2 limbs) |
| AC-39 | DPA rule pass tests ("Data Principal", "erasure") + 72h flag test |
| AC-40 | `test_playbook_rules_014` pass/flag pairs + `--negatives` AI runs |
| AC-41 | `test_standard_update_014` (a) |
| AC-42 | `test_standard_update_014` (b) + frontend notice test |
| AC-43 | existing `test_a_template_drafting_could_not_fill_is_refused` (+ a case with a made-up name next to the new ones) |
| AC-44 | adopt 403 test; existing cross-org invisibility tests |
| AC-45 | audit assertions in `test_standard_update_014`; sample runs audited; GET writes nothing |

## Risks & decisions

- **Decision — preferred position covers every answer our form can give.** Rejected: leaving 60 days
  or a 2-year NDA at "fallback" — the AI reports fallbacks as findings (both NDA redlines), which is
  exactly the mismatch this feature removes.
- **Decision — content in shipped defaults and library code, delivered to existing orgs by a
  data-only migration (0077).** Rejected: a manual command (the template text changes on deploy
  but the rules would wait for someone to run it — FR-4 broken in between); an app-startup hook
  (races between workers, runs on every boot). The migration replays safely because it exits before
  any ORM access on a database with no organisation.
- **Decision — "unedited" = fingerprint equals the rule set the review read.** Rejected: comparing
  with version 1 or `source_metadata.library` (the NDA playbook is generated, not library; an org could
  have edited v1 in place via the rule PATCH endpoint on a draft and republished).
- **Decision — NDA standard rules live in `library.NDA_STANDARD`, not in the seeded library.**
  Keeps spec 006's "NDAs are left to the org's own NDA playbook" (pinned test) and avoids a second
  NDA playbook in this org on the next seed.
- **Decision — one phrase per line in `required_language`/`prohibited_language`.** Lets a rule name
  the 72h and 48h windows and both digit/word forms, so FR-51/52 are proven without AI. Rejected: a
  new column (migration, UI) for a list. Risk: a stored rule with a deliberate multi-line prose
  prohibited text would now match line by line — none exists in the reviewed playbooks (all single
  line); the AI still reads the full text.
- **Decision — new placeholders hold only text that varies with answers or registers; fixed legal
  text (MSME, GST, insurance clause frame, breach window) is in the template body**, where Legal can
  read and edit it on the Templates page (the existing principle in `sow.py`/`consultancy.py`).
- **Decision — never remove a placeholder.** An org's saved wording may still use them; removing one
  would KeyError its drafts.
- **Decision — dispute seat when the register has no address**: the clause names "the city in which
  {company}'s registered office is situated" (true, not invented; FR-14/FR-18). The dev org's entity
  has no address today, so drafts will say that until Admin fills the register; recommend filling it.
- **Decision — NDA with a foreign law picked on the form keeps the courts at our city** (FR-14/FR-16
  read literally: the NDA uses the courts at the seat, seat = our registered city). Counsel may
  prefer the chosen place's courts; it is one string in `paper.py`.
- **Decision — their signer's title**: `cp_signer_name` is split at the first comma like the register
  signatory ("Maya Chen, CFO"); otherwise a blank line (no question asks for it yet).
- **Decision — scope placeholder text is a draft hold, not a filing refusal**: the form layout is out
  of scope; the hold already resumes by itself once the request is updated.
- **Decision — AI sample results are stored as real (completed) checks with trigger `sample`**, so
  they are "recorded with the check result" on the Templates page; `results_vary` ignores them.
  Rejected: a JSON file only (not visible where Legal looks).
- **Risk — the AI still flags something on our paper.** Mitigation: T017 runs the sample; any finding
  is fixed as a pair (template + rule in the same task) and the sample re-run (budget: two re-runs,
  ≈ $12 more). The deterministic matrix cannot prove AI behaviour; the sample is the only proof, which
  is why the spec requires both.
- **Non-INR amounts are fixed round figures chosen by the user (decision 3)**, held in one constant
  (`paper.MINIMUMS`) so Finance can change them in a single edit; no exchange-rate lookup.
- **Risk — the "older than the standard" cut-off is a fixed instant** (`EFFECTIVE`); an edit made
  between 27 Oct and the deploy is not flagged. Single org today; `ponytail:` comment on the constant.
- **Risk — matrix runtime** (~12k renders): budget 20 s; one loop per template, no per-case
  collection; `find_phrase` regexes are cached by `re`.
- **Risk — wording-pinning tests elsewhere** break when texts change; each template task owns fixing
  tests that pin its own old wording (listed in the path mapping) and nothing else.

### FR traceability

| FR | Component |
|---|---|
| FR-1 | pairs §1–§7; `test_alignment_*`; AI sample |
| FR-1A | `eval/template_matrix.py` + `test_alignment_*` (no AI, in CI); `eval/template_alignment.py` (AI, 3×19 + negatives, outside CI, dated JSON + `sample` checks) |
| FR-2 | `template_matrix.combinations` dimensions from `agreement_forms.json` via `options_for` |
| FR-3 | `options_when` (T003); payment rules; FR-3 choice table |
| FR-4 | pairs edited in the same task (T006–T012 against T002 rules); 0077 in the same release as the defaults |
| FR-5 | `paper` (BLANK / descriptive values / variant blocks), SOW `pricing_terms`/`sow_payment`, holds; matrix check 2 |
| FR-6 | rule rewrites marked CHANGED; AC-5 table test |
| FR-7 | Customer Agreement untouched; `CUSTOMER_BASELINE` test |
| FR-8 | live default hash + new playbook versions → Out of date; T017 Check all; sample checks dated |
| FR-9 | existing `template_status` out_of_date + manual check (unchanged) |
| FR-10, FR-11 | no contract writes; `test_existing_contracts_untouched_014` |
| FR-12, FR-13 | `paper.governing_law`; deleted entity override and Delaware fallback |
| FR-14 | `dispute_seat` |
| FR-15 | SOW 12.1, DPA 14.1 static; SOW/DPA `governing_law` rules |
| FR-16 | `dispute_resolution` (NDA and Consultancy courts at the seat, MCIA, SIAC) |
| FR-17 | `our_/their_signatory_*` + standard signature block |
| FR-18 | `company_address`/`counterparty_address`; blank CIN/GSTIN lines |
| FR-19 | MSME line in MSA 6.3A, Vendor 4.7, SaaS 4.7, Consultancy 6.3A; payment rules require it |
| FR-20 | GST clauses (MSA 6.6, Vendor 4.6, SaaS 4.6, Consultancy 6.6) + `tax_compliance` rules; Consultancy 3.2 GST removed |
| FR-21 | `fee_currency`, currency-keyed amounts; matrix "no INR" check |
| FR-22 | insurance clauses + `insurance` rules (MSA, Vendor, SaaS, Consultancy) |
| FR-23 | BREACH wording; breach-window prohibited lines |
| FR-24 | `dpa_status`, `nda_personal_data`; data_protection rules accept "No" |
| FR-25 | `placeholder_scope` + `SCOPE_HELD` hold |
| FR-26 | NDA edits 1–13, 15, 18–20 |
| FR-27 | `term_clause` words |
| FR-28 | NDA `term_and_duration_of_confidentiality` |
| FR-29 | `nda_personal_data` + NDA `personal_data` rule |
| FR-30 | NDA edit 6 (Q9); `purpose_phrase` unchanged (Q14) |
| FR-31 | NDA `non_compete_and_exclusivity`, `indemnity`, `residuals`; `compelled_disclosure` routine-inspection position |
| FR-32 | MSA edits 1–8, 10–12, 16–20 |
| FR-33 | MSA edit 14 + `limitation_of_liability` |
| FR-34 | MSA 5.5 + `service_levels`; SOW 4.7 + SOW `service_levels` |
| FR-35 | `pricing_terms` + `SOW_PRICING_HELD` |
| FR-36 | `pricing_terms` rates sentences (Q19); SOW `fees_and_pricing`, `milestone_payments` |
| FR-37 | `parent_agreement`; SOW 9.2 |
| FR-38 | SOW 4.7 |
| FR-39 | `sow.py` representative fallback |
| FR-40 | Vendor edits 3–4, 6–8, 10–14, 17; `vendor_supply_terms`; Q10 |
| FR-41 | Vendor 7.2 `dpa_status`; `supply_description` |
| FR-42 | Vendor 11.1; SaaS 15.1/15.2/15.4 (one Data Breach Cap, precedence stated) |
| FR-43 | `hosting_clause`; SaaS `data_location` |
| FR-44 | `renewal_notice_days` + SaaS `agreement_term`; `renewal` rule |
| FR-45 | SaaS 8.6, 4.5, 9.3, 4.6, 4.2 |
| FR-46 | Vendor/SaaS new rules |
| FR-47 | Consultancy edits 1–17; `_fee_basis`, `consultancy_invoicing` |
| FR-48 | Consultancy rule set |
| FR-49 | DPA edits 1–10 |
| FR-50 | DPA rule set |
| FR-51 | `flag` wordings on every loosened rule; `_phrases`; `--negatives` |
| FR-52 | pass/flag per CHANGED/NEW rule; `test_playbook_rules_014` |
| FR-53 | 0077 marker versions and playbook versions (reason in note/summary/source_metadata, review items in `changes`); adopt endpoint |
| FR-54 | `standard` status + Templates notices; existing reset; adopt endpoint; nothing overwritten |
| FR-55 | `PLACEHOLDERS` + existing `check_body`; AC-43 test |

## Tasks and waves (for `/tasks`)

| ID | Agent | Wave | dependsOn | Files | Done when |
|---|---|---|---|---|---|
| T001 [P] | backend-dev | 1 | — | `app/intake/paper.py`, `tests/test_paper_fill.py` | unit tests pass; module imports only stdlib, settings, `intake.sow` |
| T002 [P] | backend-dev | 1 | — | `app/playbooks/library.py`, `app/playbooks/service.py` (`_phrases`, `evaluate_rules_against_text`), `tests/test_playbook_rules_014.py`, `tests/test_playbook_library.py` | every rule set exactly as frozen; pass/flag tests pass |
| T003 [P] | backend-dev | 1 | — | `app/intake/agreement_forms.json`, `app/intake/agreement_forms.py`, `app/intake/service.py`, `tests/test_payment_options_014.py`, `tests/test_intake_agreement_form_validation.py` | options resolved server-side; stored answers kept |
| T004 [P] | frontend-dev | 1 | — | all frontend files in the path mapping | typecheck + vitest green |
| T005 | backend-dev | 2 | T001, T003 | `app/intake/drafting.py`, `app/drafting_templates/service.py` (PLACEHOLDERS), `app/drafting_templates/checks.py` (`_CODED`, markers), `eval/template_matrix.py`, `tests/test_drafting_context_014.py`, `tests/test_drafting_templates.py` | context wired, holds, placeholders; existing suite green |
| T006 [P] | backend-dev | 3 | T002, T005 | `defaults/nda.txt`, `tests/test_alignment_nda.py`, `tests/test_nda_recital_purpose.py` (if needed) | NDA matrix 0 findings |
| T007 [P] | backend-dev | 3 | T002, T005 | `defaults/msa.txt`, `tests/test_alignment_msa.py` | MSA matrix 0 |
| T008 [P] | backend-dev | 3 | T002, T005 | `defaults/sow.txt`, `app/intake/sow.py`, `tests/test_alignment_sow.py`, `tests/test_sow_drafting.py`, `tests/test_sow_deliverable_rows.py` | SOW matrix 0 |
| T009 [P] | backend-dev | 3 | T002, T005 | `defaults/vendor.txt`, `tests/test_alignment_vendor.py` | Vendor matrix 0 |
| T010 [P] | backend-dev | 3 | T002, T005 | `defaults/saas.txt`, `tests/test_alignment_saas.py` | SaaS matrix 0 |
| T011 [P] | backend-dev | 3 | T002, T005 | `defaults/consultancy.txt`, `app/intake/consultancy.py`, `tests/test_alignment_consultancy.py`, `tests/test_consultancy_dpa_drafting.py`, `tests/test_consultancy_dpa_answers.py` | Consultancy matrix 0 |
| T012 | backend-dev | 3 (after T011) | T002, T005, T011 | `defaults/dpa.txt`, `app/intake/dpa.py`, `tests/test_alignment_dpa.py`, `tests/test_consultancy_dpa_drafting.py` (DPA parts) | DPA matrix 0 |
| T013 [P] | backend-dev | 3 | T002, T005 | `app/drafting_templates/standard.py`, `routes.py`, `schemas.py`, `service.py` (`_summary`), `checks.py` (`_MANUAL`, `_results_vary`), `tests/test_standard_update_014.py`, `tests/test_drafting_templates.py` (route table), `tests/test_template_status.py`, `tests/test_template_check.py` (if needed) | adopt endpoint + status + apply/revert tested |
| T014 | db-engineer | 4 | T013 | `backend/alembic/versions/0077_std_paper.py` | `alembic heads` = one; upgrade on dev applies; `make test-db` re-run clean; downgrade works |
| T015 | backend-dev | 4 | T005, T006–T013 | `eval/template_alignment.py` | `--help`, dry plan without `--yes`, refusal under MOCK_CLAUDE verified (no paid run in this task) |
| T016 | backend-dev | 4 | T006–T014 | `tests/test_existing_contracts_untouched_014.py`; full `ruff check .` + `pytest -q` green | CI-equivalent green |
| T017 | orchestrator + user | 5 | T004, T014–T016 | none (runs): `make migrate`, Check all, then — **only with the user's go-ahead (≈ $6)** — the AI sample; any finding → a pair fix by backend-dev in the owning template task's files, re-run | AI JSON: 0 findings on 57 main runs; negatives flagged |
| T018 | qa-engineer | 6 | T017 | `specs/014-template-playbook-alignment/verification.md` | every AC evidenced |

Parallel-safety: wave 1 tasks share no file; wave 3 template tasks each own their default file and
filler (T011 → T012 serialized on the shared consultancy/DPA test file); T013 runs in wave 3 because
it touches only files T005 has finished with and no template task touches. Only T014 writes a
migration.

## Decisions (answered by the user, 2026-10-27)

1. **Data-breach floor (SaaS and DPA): INR 5 crore**, the same as the cyber insurance minimum, so the
   floor is always insured. Non-INR contracts use the five-crore equivalents from decision 3
   (`data_breach_floor` reads the "five_crore" entry of `paper.MINIMUMS`). Used in SaaS 15.2, DPA 12.2,
   and the SaaS/DPA liability rules.
2. **Consultancy disputes: every Consultancy uses the courts at the seat (our registered city)**, for
   individuals and firms, Indian and foreign, until the next feature adds the individual-or-firm
   question. No name-based guess anywhere (design, matrix, tests). The Consultancy `governing_law`
   rule's preferred position names courts at the seat, so the template gives zero findings; the seat
   is never Singapore for Consultancy.
3. **Non-INR minimums: USD 600,000 / 120,000; EUR 550,000 / 110,000; GBP 475,000 / 95,000** (five-crore /
   one-crore equivalents). They live in one constant, `paper.MINIMUMS`, the only place the amounts are
   written, so Finance can change them later in one edit.

## Pair fixes after the first AI sample (user-approved, 2026-10-27)

An accidental 8-review AI sample on the NDA (`drafting_template_check` rows with `trigger='sample'`, 13:32–13:35 UTC)
found four template–rule disagreements the literal phrase check cannot see. Each is fixed as a pair:

| # | Finding (rule) | Template change (`defaults/nda.txt`) | Rule change (`NDA_STANDARD` in `app/playbooks/library.py`) |
|---|---|---|---|
| PF-1 | `permitted_use_and_standard_of_care`: the AI reads 4.1's "for no other purpose whatsoever" as the prohibited "for any purpose whatsoever" | 4.1 → "The Receiving Party shall use Confidential Information solely for the Purpose." | Preferred position adds: "Wording that limits use to 'solely for the Purpose' meets this position." Prohibited phrase unchanged. |
| PF-2 | `personal_data`: wants a breach-notice sentence even when the request says no personal data | 11.1 always ends with: "Each Party shall notify the other without undue delay of any personal data breach affecting the other Party's Confidential Information." (no personal-data variant) | Preferred: "…handled under the DPDP Act 2023 with reasonable security safeguards, and any personal data breach is notified without undue delay." (drop the "where the request says…" condition); required adds the line "without undue delay". |
| PF-3 | `governing_law_and_jurisdiction`: prohibited "exclusive jurisdiction of the courts of" fuzzy-matched our "courts at Mumbai" | none | Prohibited → "exclusive jurisdiction of the courts of the State of" (US-style foreign courts only); preferred adds: "Wording naming 'the exclusive jurisdiction of the courts at <city>' meets this position." |
| PF-4 | `remedies_and_injunctive_relief`: 8.1 gives relief only to the Disclosing Party, so as Receiving Party in a one-way NDA we have none | 8.1 → "…a breach or threatened breach of this Agreement may cause the other Party irreparable harm…, and that either Party may seek injunctive or other equitable relief…" | none (preferred already says "either party") |

Scope: NDA only (the user approved these four). The same prohibited phrase "exclusive jurisdiction of the courts of"
also sits in the MSA and Vendor rules; it is left as is and the paid AI sample (T017) will show whether it misfires there.
Rollout on dev: the org's NDA playbook already carries the first standard version (T014); the changed `NDA_STANDARD`
reaches it through "Use the standard rules" during T017 — adopting queues a template check, which is a paid AI call, so
it happens only with the user's go-ahead.

## Pair fixes after Check all (user-approved, 2026-10-27)

"Check all" with the real AI review (after PF-1..PF-4) gave NDA 0, Consultancy 0, and six findings on five
templates. Each is fixed as a pair (most are rule clarifications: the reviewer misread compliant wording):

| # | Finding | Template change | Rule change (`app/playbooks/library.py`) |
|---|---|---|---|
| PF-5 | DPA `international_transfers` (high): the prohibited phrase "transfer Personal Data to any country without" is read onto our protective 9.1 ("shall not transfer … without the Controller's prior written authorisation") | none | Prohibited → "may transfer Personal Data to any country" (permissive wording only); preferred adds "Wording that forbids any transfer without our prior written authorisation meets this position." Must-flag: "The Processor may transfer Personal Data to any country without notice." |
| PF-6 | MSA and Vendor `insurance` (medium): "per claim and in the aggregate" read as an unwanted aggregate sublimit | none | Every playbook's `insurance` rule (MSA, Vendor, SaaS, Consultancy, and any other 014 rule set with one) adds to preferred: "Cover of the stated amount 'per claim and in the aggregate' meets this position." |
| PF-7 | Vendor `payment_terms` (medium): the MSME clause's "within the period in Clause 4.3 or forty-five (45) days … whichever is shorter" read as unclear | none | Preferred adds: "For a registered micro or small supplier, payment within the agreed period or 45 days from acceptance, whichever is shorter, meets this position." |
| PF-8 | SaaS `renewal` (low): reminder 30 days before the cancellation deadline read as possibly late | none | Preferred adds: "A written reminder at least 30 days before the cancellation deadline meets this position." |
| PF-9 | SOW `scope_and_deliverables` (medium): the template check fills the example answers, and the example scope is one high-level sentence | `app/intake/sow.py` `EXAMPLE_ANSWERS["scope"]` → a detailed example (named activities, the deliverables they produce, what is out of scope), still clearly example text | none |

Changed rule sets reach the dev organisation through "Use the standard rules" (MSA, Vendor, SaaS, Consultancy, DPA),
which queues each template's check; the SOW (no rule change) is re-checked by "Check all". That is the ~$0.70
re-check. The 64-review sample runs only if every template is then at zero findings.

## Review prompt change and Vendor clarification (user-approved, 2026-10-27)

The re-check after PF-5..PF-9 cleared every earlier finding but surfaced two new ones on unchanged clauses (MSA
`payment_terms` "whatever period is stated"; Vendor `limitation_of_liability` personnel indemnity), and four checks
were interrupted. The AI review raises different misreadings run to run, so the user chose to fix the source:

| # | Change | Where |
|---|---|---|
| PF-10 | The playbook review prompt gains a section telling the reviewer that (a) wording a rule's preferred position names as meeting it ("… meets this position") meets the rule; (b) a provision is judged with the provisions it refers to (e.g. "the Vendor's indemnities under the Indemnification clause" covers every indemnity in that clause); (c) a prohibited phrase used in a negated or protective sense ("shall not transfer … without our authorisation") is not prohibited language; (d) asking for more detail, or for protection beyond the preferred position, is not a deviation. It does NOT relax real gaps: wording worse than the preferred position, missing required protection, and permissive/one-sided counterparty wording are still reported. Prompt version bumped per `app/ai/prompt_versions.py` conventions. | `_PLAYBOOK_REVIEW` in `app/ai/prompt_versions.py` |
| PF-11 | Vendor 11.3(b) made explicit: "the Vendor's indemnities under the Indemnification clause (including clause 10.1(a) to (e))". The existing wording already covered 10.1(e); this removes the misreading. | `defaults/vendor.txt` |

| PF-12 | The review output parser accepts a list field (`citations` on `PlaybookReviewOutput` and on each deviation, and `deviations`) sent by the model as a JSON-encoded string: it is parsed into the list before validation; anything that is not valid JSON for a list still fails validation as today. Found when Consultancy's check returned 0 deviations but `citations` as a string, so the whole reply was rejected ("ai_unavailable"). Applies to every playbook review. | `PlaybookReviewOutput` / `PlaybookDeviationOutput` in `app/ai/schemas.py` |

| PF-13 | PF-6's sentence was read as required wording: Consultancy's "per claim" cover was flagged for lacking "and in the aggregate". Every `insurance` rule's added sentence becomes: "Cover of the stated amount per claim, with or without the words 'and in the aggregate', meets this position." | the `insurance` rules in `app/playbooks/library.py` (MSA, Vendor, SaaS, Consultancy) |

| PF-14 | Vendor `data_protection` (high): with personal data "Yes", clause 7.2's "shall sign a data processing agreement … at the same time as this Agreement; it applies from the Effective Date … and no personal data is processed before it is signed" was read as a future promise. Every `data_protection` rule whose preferred position says "a DPA on our template applies from the start" (MSA, Vendor, SaaS, and any other with the same wording) adds: "A clause requiring our DPA to be signed with this Agreement, to apply from the Effective Date, and barring any processing of personal data until it is signed, meets this position." | those `data_protection` rules in `app/playbooks/library.py` |

Because PF-10 changes how every contract is reviewed (counterparty paper too), the deterministic negative fixtures in
`test_playbook_rules_014.py` keep passing, and the 7 risky counterparty-paper runs in the AI sample (T017) must still
come back with findings.

## 64-review AI sample result (2026-10-27)

User accepted Vendor's one low insurance finding and ran the sample. 35 of 57 main runs had findings; 6 of 7 risky counterparty runs were flagged (NDA risky found no clauses). Check all passed because it uses the default INR/registered answers; the sample's foreign currencies and foreign counterparties expose these repeatable gaps:

1. Foreign-currency minimums in `paper.MINIMUMS` are not named in the rules, so the reviewer cannot confirm they equal INR 5 crore / 1 crore (msa, saas, vendor, consultancy).
2. Foreign paper picks SIAC Singapore, but the rule allows it only for a provider outside India and the paper never states the party is outside India (msa, saas, vendor).
3. NDA Delaware fill writes 'the State of Delaware', a phrase its own rule prohibits.
4. Consultancy fixed-fee combination renders the Fees blank.
5. SOW example scope still read as too high level on two combinations.
6. SaaS EU hosting: no Order Form approval referenced.

No fixes made yet; waiting for the user.

## Pair fixes after the 64-review sample (PF-15..PF-21)

- **PF-15 Foreign-currency floors.** library.py builds the INR 5 crore / 1 crore floors from `paper.MINIMUMS`, naming USD 600,000 / EUR 550,000 / GBP 475,000 and USD 120,000 / EUR 110,000 / GBP 95,000 (MSA, Vendor, SaaS and Consultancy insurance; SaaS data-breach cap). DPA stays INR-only.
- **PF-16 Singapore seat.** Foreign paper's dispute clause opens "<Counterparty> is incorporated or established in <jurisdiction>, outside India."; the shared governing-law rule (MSA, Vendor, SaaS) says a Singapore SIAC seat meets the position where the contract states that.
- **PF-17 NDA US-state law.** The NDA governing-law rule accepts "the laws of the State of Delaware / New York"; the prohibited phrase concerns submitting to a US state's courts, not choosing its law.
- **PF-18 Consultancy fee.** The matrix now gives a value; a Consultancy request with no contract value is held (`paper.FEE_CAP_HELD`) instead of drafting a blank fee.
- **PF-19 SOW.** The matrix uses the detailed example scope; fixed-fee SOW tax reads GST or equivalent indirect tax.
- **PF-20 SaaS hosting.** EU/US hosting is stated in the Order Form subject to the privacy function's prior written approval; the data_location rule accepts that without naming India.
- **PF-21 Risky sample.** `_risky_text` writes one full operative sentence per rule under "SPECIAL CONDITIONS" that prevail over the agreement, not bare fragments.

Proof (free): tests/test_pair_fixes_pf15_21.py (36), all alignment tests 0 phrase findings, ruff clean, full suite 8210 passed / 3 failed (known gateway test + 2 order-dependent tests that pass alone). Rollout to the dev org ("Use the standard rules" for nda, msa, vendor, saas, consultancy) and any AI re-check await the user's go-ahead.

## Second 64-review sample (2026-10-28, batch 847a2aea)

21 of 57 main runs had findings (first sample: 35); 7 of 7 risky runs flagged; 1 run and 2 adoption checks failed on malformed AI output (lists sent as JSON text with unescaped quotes). Runs with findings: nda 1/9, msa 4/6, sow 1/9, vendor 6/9, saas 10/12, consultancy 1/6, dpa 0/6. Repeatable gaps left: MSA cap wording; Vendor data protection on no-personal-data paper and professional indemnity on goods paper; SaaS non-India hosting and IP indemnity carve-out. Waiting for the user.

## Pair fixes after the second sample (PF-22..PF-26)

- **PF-22 MSA cap.** The MSA limitation_of_liability rule now quotes the template's own cap ("fees paid or payable under this Agreement in the twelve (12) months immediately preceding the event giving rise to the claim") as meeting the position; a total contract value elsewhere does not narrow it. Template unchanged.
- **PF-23 DPA on no-personal-data paper.** `paper._dpa_status` (No) now signs our DPA with the agreement, applying from the Effective Date, with no processing before signature (Vendor 7.2 and MSA/Consultancy equivalents). The shared data_protection rule says the same and accepts the no-personal-data case.
- **PF-24 Vendor professional indemnity.** PI applies to a supplier of services under an Order; installing, repairing, replacing or re-performing delivered goods under the warranty does not make a goods supplier a services supplier. vendor.txt 4.7 says "Deliverables invoiced" and 5.2 "re-perform them".
- **PF-25 SaaS.** The data_location rule: an Order Form location with the Customer's privacy function's prior written approval IS the preferred position; paper 9.2 uses "the prior written approval of the Customer's privacy function". saas.txt 15.4(b): IP indemnity (14.1(a)) and 14.1(c) are unlimited and outside every cap; the rule says the same. MSMED low nit left alone.
- **PF-26 Review output recovery.** `_list_from_json_text` retries once after escaping stray quotes inside JSON text; the three failed real reviews now parse (17 citations; 3 and 2 deviations). Ceiling marked `ponytail:`.

Proof (free): tests/test_pair_fixes_pf22_26.py (15) plus alignment, PF-15..21 and output tests: 3122 passed; ruff clean; full suite 8234 passed / 1 failed (known gateway test). Adoption and an AI re-check await the user's go-ahead.

## Third sample (2026-10-28, partial)

Killed after 31 of 64 reviews by a backend container restart (outside this feature). Completed: nda 0/9, sow 1/9, msa 5/6, vendor 2/7. MSA still flags its own total-contract-value clause (6.1) as possibly narrowing the 13.1 cap despite PF-22's sentence. Remaining (vendor, saas, consultancy, dpa, risky) not run.

## Remaining sample (2026-10-28, batch f1fc2f46)

Vendor 3/9, SaaS 5/12, Consultancy 2/6, DPA 2/6 runs with findings; risky 4/4 flagged. With the partial third run (NDA 0/9, SOW 1/9, MSA 5/6): 18 of 57 main runs had findings (first sample 35, second 21). Repeatable gaps left: (1) the Mumbai-seat wording 'exclusive supervisory jurisdiction' reads as the prohibited 'exclusive jurisdiction of the courts of' (Vendor, SaaS); (2) Vendor 10.2 does not say the Vendor controls the defence; (3) Consultancy public liability has no amount; (4) MSA 6.1 total contract value seen as narrowing the 13.1 cap. The rest are single-run findings that differ every run.

## Pair fixes after the third sample (PF-27..PF-30) and the new pass rule

- **PF-27 Seat wording.** `paper._dispute` (Vendor 18.2, SaaS 20.2, MSA) now says "the courts at {seat} alone have supervisory jurisdiction over the arbitration"; the shared governing_law rule says seat-court supervision is not the prohibited phrase, which concerns submitting the dispute itself to a foreign court. The prohibited list is unchanged.
- **PF-28 Defence control.** Vendor 10.2 and SaaS 14.2: the provider controls the defence and settlement, may not settle in a way that admits fault for or binds us without written consent; we may join at our own cost. Vendor and MSA indemnification rules carry the same sentence.
- **PF-29 Consultancy public liability.** consultancy.txt 12.5: public liability of not less than `{public_liability_minimum}` per claim (paper.MINIMUMS one-crore tier); the rule names INR 1 crore / USD 120,000 / EUR 110,000 / GBP 95,000.
- **PF-30 Contract value.** `drafting.value_sentence` (MSA 6.1, Vendor 4.1, SaaS 4.1) adds that the figure is a spending limit only and never limits either party's liability; the MSA cap rule says the same. Frozen exam renders strip the new sentence (eval/exam/build.py).
- **Pass rule (user, 2026-10-28).** eval/template_alignment.py fails a combination only when a clause type repeats in 2 or more of its 3 runs (or a run errors, or a risky paper is not flagged); spec FR-1A and AC-1A updated.

## Fixes after /verify (PF-31..PF-34)

- **PF-31 Indian city read as foreign.** `paper.is_foreign`: India, a state/UT or a listed Indian city is Indian; a listed foreign country (`_FOREIGN_COUNTRIES`) wins ("Punjab, Pakistan" is foreign); blank is Indian; unknown places stay foreign. Ceiling marked `ponytail:`.
- **PF-32 Seat from the address.** `paper._seat` takes the last listed city ("Delhi Public School Road, Pune 411001" gives Pune).
- **PF-33 Standard update sees all edits.** `standard.FIELDS` adds approval_required, escalation_role, negotiation_guidance, sample_clause; library baselines re-hashed from the committed library on all 12 fields; NDA (org's own playbook) stays compared on the 8 core fields (`ponytail:`). A baseline playbook edited only in those fields is skipped (audited `playbook.standard_skipped`) and shows as behind.
- **PF-34 SaaS several regions.** 9.1: only the regions listed in the Order Form signed with this Agreement, each with the privacy function's prior written approval, no other region; the data_location rule accepts that and still rejects a provider-chosen location.

Proof (free): tests/test_pair_fixes_pf31_34.py (22) and related files 2482 passed on re-run; ruff clean; full suite 8402 passed / 1 failed (known gateway test). Adoption and the paid sample await the user's go-ahead.

## Open questions

None.
